"""Python that ix runs but did not write, each run in a Python process of its own.

Two kinds, on two sides of one trust boundary:

* **Code an agent wrote** (``function-test``, and a ``test-feedback`` loop): untrusted. It
  is written to a fresh temporary directory and run there by a child process with ``-I``
  (no user site, no ``PYTHON*`` variables, not the current directory on ``sys.path``), an
  environment holding only ``PATH`` and the locale (so no credential of this process is
  in reach through the environment), and a time limit, after which its process group is
  ended. That bounds a hang and keeps a crash out of the harness. It is **not a sandbox**:
  the code can still read and write files, open sockets and start programs as the user ix
  runs as. Run code from a subject you would run yourself, or run ix in a container.
* **Code the experiment owns** (``outcome`` graders): trusted as far as the experiment's
  author is. It runs in a child process for the same two reasons (a hang or a crash costs
  one reading, not the run) and sees the response only as data. Validating or planning an
  experiment never imports it; only reading a trial does.

The child reports one JSON line per result as it goes, so what finished before a time limit
is kept.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ix.adapters._out.processes import Launcher

#: How long a check may take when the experiment does not say.
TIMEOUT_S = 30.0

_PYTHON_BLOCK = re.compile(r"```(?:python|py)[^\n]*\n(.*?)```", re.DOTALL)
_ANY_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


def code_in(text: str) -> str:
    """The code in a response: its last python block, else its last fenced block, else the
    whole text (a reply that is only code). The last block is the final version."""
    for pattern in (_PYTHON_BLOCK, _ANY_BLOCK):
        blocks = pattern.findall(text)
        if blocks:
            return str(blocks[-1]).strip()
    return text.strip()


# The child that runs test cases. It reads {function_name, cases} on stdin and prints one
# line per case: {"case": i, "passed": bool, "message": str}; or one {"load": message} when
# the solution cannot be loaded or has no such function.
_CASES = r"""
import importlib.util, json, sys, traceback

def plain(value):
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return value

def say(record):
    print(json.dumps(record), flush=True)

job = json.load(sys.stdin)
try:
    spec = importlib.util.spec_from_file_location("solution", "solution.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = getattr(module, job["function_name"])
    if not callable(function):
        raise TypeError(f"{job['function_name']!r} is not callable")
except BaseException as error:
    say({"load": f"{type(error).__name__}: {error}"})
    sys.exit(0)

for index, case in enumerate(job["cases"]):
    given = case.get("input")
    try:
        if isinstance(given, dict) and case.get("kwargs"):
            result = function(**given)
        elif isinstance(given, list):
            result = function(*given)
        else:
            result = function(given)
    except BaseException as error:
        raised = [kind.__name__ for kind in type(error).__mro__]
        wanted = case.get("raises")
        passed = wanted is not None and (wanted in ("any", "Exception") or wanted in raised)
        say({"case": index, "passed": passed,
             "message": "" if passed else f"raised {type(error).__name__}: {error}"})
        continue
    if "raises" in case:
        say({"case": index, "passed": False,
             "message": f"returned {result!r} where {case['raises']} was to be raised"})
        continue
    expected = case.get("expected")
    passed = result == expected or plain(result) == expected
    say({"case": index, "passed": passed,
         "message": "" if passed else f"got {result!r}, expected {expected!r}"})
"""

# The child that runs one grader. It reads {module, grader, text, trial} on stdin and prints
# {"score": number} or {"error": message}.
_GRADER = r"""
import importlib.util, inspect, json, sys

def say(record):
    print(json.dumps(record), flush=True)

job = json.load(sys.stdin)
try:
    spec = importlib.util.spec_from_file_location("graders", job["module"])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    graders = getattr(module, "GRADERS", None)
    if not isinstance(graders, dict):
        raise TypeError("the module defines no GRADERS mapping of probe ids to functions")
    grader = graders.get(job["grader"])
    if grader is None:
        say({"absent": job["grader"]})
        sys.exit(0)
    takes = len(inspect.signature(grader).parameters)
    score = grader(job["text"], job["trial"]) if takes >= 2 else grader(job["text"])
except BaseException as error:
    say({"error": f"{type(error).__name__}: {error}"})
    sys.exit(0)
if isinstance(score, bool):
    score = 1.0 if score else 0.0
if not isinstance(score, (int, float)) or not 0 <= score <= 1:
    say({"error": f"the grader returned {score!r}, not a score from 0 to 1"})
else:
    say({"score": float(score)})
"""


@dataclass(frozen=True)
class CaseResult:
    passed: bool
    message: str = ""


@dataclass(frozen=True)
class CaseRun:
    """What running the cases found: one result per case, in order. ``load`` says why the
    code could not be loaded, when it could not (every case then failed)."""

    results: tuple[CaseResult, ...]
    load: str = ""


def _environment() -> dict[str, str]:
    """What a child sees of this process's environment: enough to run, no credentials."""
    kept = {name: os.environ[name] for name in ("PATH", "LANG", "LC_ALL") if name in os.environ}
    return {**kept, "PYTHONDONTWRITEBYTECODE": "1"}


async def _child(
    launcher: Launcher, script: str, job: Mapping[str, Any], cwd: str, timeout_s: float
) -> tuple[list[dict[str, Any]], bool]:
    """Run ``script`` in ``cwd`` with ``job`` on stdin. Returns every JSON line it printed,
    and whether it was ended for overrunning ``timeout_s``."""
    Path(cwd, "check.py").write_text(script, encoding="utf-8")
    process = await launcher.start(
        [sys.executable, "-I", "check.py"],
        cwd=cwd,
        env=_environment(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert process.stdin is not None and process.stdout is not None  # both piped above
    lines: list[dict[str, Any]] = []

    async def converse() -> None:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(job).encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()
        async for raw in process.stdout:
            try:
                record = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                lines.append(record)
        await process.wait()

    overran = False
    try:
        await asyncio.wait_for(converse(), timeout_s)
    except TimeoutError:
        overran = True
    except (BrokenPipeError, ConnectionResetError):
        pass  # the child ended before it read its job: what it printed says why
    finally:
        await launcher.stop(process, grace_s=1.0)
    return lines, overran


async def run_cases(
    launcher: Launcher,
    code: str,
    function_name: str,
    cases: Sequence[Mapping[str, Any]],
    *,
    timeout_s: float = TIMEOUT_S,
) -> CaseRun:
    """Run ``cases`` against ``function_name`` in ``code`` (untrusted: see the module
    docstring). A case is ``{input, expected}`` or ``{input, raises}``; a list input is
    spread as positional arguments, a mapping one as keywords when ``kwargs`` is true."""
    with tempfile.TemporaryDirectory(prefix="ix-check-") as scratch:
        Path(scratch, "solution.py").write_text(code, encoding="utf-8")
        job = {"function_name": function_name, "cases": [dict(case) for case in cases]}
        lines, overran = await _child(launcher, _CASES, job, scratch, timeout_s)
    for line in lines:
        if "load" in line:
            failed = CaseResult(passed=False, message=f"the code did not load: {line['load']}")
            return CaseRun(results=tuple(failed for _ in cases), load=str(line["load"]))
    found = {
        int(line["case"]): CaseResult(bool(line.get("passed")), str(line.get("message", "")))
        for line in lines
        if isinstance(line.get("case"), int)
    }
    missing = (
        f"not run: the tests overran their {timeout_s:g}s"
        if overran
        else "not run: the test process ended early"
    )
    return CaseRun(
        results=tuple(found.get(index, CaseResult(False, missing)) for index in range(len(cases)))
    )


class GraderError(Exception):
    """A grader that could not grade: it raised, returned no score, or overran."""


async def run_grader(
    launcher: Launcher,
    module: str,
    grader: str,
    text: str,
    trial: Mapping[str, Any],
    *,
    timeout_s: float = TIMEOUT_S,
) -> float | None:
    """The score ``GRADERS[grader]`` in ``module`` gives ``text``; None when the module has no
    such grader. Raises :class:`GraderError` when it could not grade."""
    with tempfile.TemporaryDirectory(prefix="ix-grade-") as scratch:
        job = {"module": module, "grader": grader, "text": text, "trial": dict(trial)}
        lines, overran = await _child(launcher, _GRADER, job, scratch, timeout_s)
    if overran:
        raise GraderError(f"the grader {grader!r} overran its {timeout_s:g}s")
    for line in lines:
        if "absent" in line:
            return None
        if "error" in line:
            raise GraderError(f"the grader {grader!r} could not grade: {line['error']}")
        if "score" in line:
            return float(line["score"])
    raise GraderError(f"the grader {grader!r} ended without a score")

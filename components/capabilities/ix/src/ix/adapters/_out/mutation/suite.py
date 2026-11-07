"""The ``test-suite`` subject, the ``mutant`` probe and the ``mutant-kill`` sensor.

**A trial is one mutant applied once to one suite**, in a fresh copy of the tree the subject
names (:mod:`.runner`). The probe splices the mutant into the copy, checks that the text it
replaces is there (``original``; a tree that differs makes the trial the harness's,
``mutant_inapplicable``), and runs the suite in **stages**:

* ``selected``: the tests the selection map says ran a line of the statement the mutant lies in
  (:mod:`.selection`); a mutant whose statement only imports ran runs every test;
* ``confirmed``: every test, for a survivor the subject's ``confirm`` names (``uncovered``,
  the default: one no test ran; ``all``: every survivor; ``none``: never).

**A kill counts only when it reproduces and is attributable.** A test that failed on the
mutant is a killer only if it fails again when run alone on the mutant, and passes when run
alone on the same copy with the original text restored; a collection error kills
(``<collection>``) only if it recurs and collection then succeeds without the mutant. A
failure that does not recur is a flake (``unreproduced``: the test passed on the mutant when it
ran again), never a kill: a test that fails under load in a whole-suite run, and passes alone,
was credited with mutants on lines it never ran in the first acceptance run (design record
§32). Failures that also fail without the mutant are ``unattributed``, and a mutant whose only
reproduced failures are unattributed is the harness's failure (``unattributable``). This is an
invariant: no option turns it off.

**Every run has a temporary directory of its own** (``TMPDIR``), as it has a copy of its own,
so trials in flight share no temporary files.

**Every run is checked to have imported the copy.** A run that imported a scope module from
anywhere but the copy is the harness's failure (``not_the_mutant``): never ``survived``.

**Time.** Each run may take ``timeout_factor`` times what its tests and the suite's start
took in the baseline, never less than ``timeout_floor``, and the trial no more than the
probe's ``budget`` in all. A run past its limit is the harness's failure (``timeout``): it
showed nothing, and is read neither as killed nor as survived.

The outcome facet (``outcome``) says how the trial ended: ``status`` (``killed``, ``survived``,
or ``uncovered`` when nothing ran it), ``stage``, ``covered``, ``selected``, ``ran``, ``killers``,
``unattributed``, ``unreproduced``, ``covered_by`` (for a survivor, the tests
that ran its statement and passed), ``lines`` (the statement's, which selected them),
``imported_from``, ``drifted`` (its file changed since it was sampled), ``timeout_s`` and
``duration_s``. The sensor reads ``killed`` from it and keeps it whole as its detail.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ix.adapters._out.mutation.population import PROBE_KIND, digest, span_text, splice
from ix.adapters._out.mutation.runner import OverranError, Ran, Suite, copy_tree, run, suite_of
from ix.adapters._out.mutation.selection import (
    MapUnreadableError,
    Selected,
    baseline_time,
    overhead,
    read_map,
    select,
    statement_lines,
)
from ix.extension import HarnessError, Requirement, Sensed, Target, duration_of, problem

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

    from ix.extension import (
        Interaction,
        Launcher,
        MutantSources,
        Problem,
        SubjectConfig,
        Surroundings,
    )

#: The type URLs of the three kinds.
SUBJECT_KIND = "ix.v1.subject.test-suite"
MUTANT_KIND = "ix.v1.probe.mutant"
SENSOR_KIND = "ix.v1.sensor.mutant-kill"

#: The killer a collection error names.
COLLECTION = "<collection>"

#: How long the check that the suite's Python has coverage.py may take.
CHECK_TIMEOUT_S = 60.0

#: Where ix-cosmic-ray, the mutant source this repository ships, is installed from: its
#: release's tag. The skill's mutation reference names the same (``MUTANT_SOURCE``), and a
#: test holds both to ix-cosmic-ray's version.
COSMIC_RAY_SOURCE = (
    "git+https://github.com/mox-labs/gnx@ix-cosmic-ray-v0.1.0"
    "#subdirectory=components/capabilities/ix-cosmic-ray"
)

#: What to install when no mutant source is. It is on no package index, where another project
#: could take the name (SECURITY.md, I-8), so where it comes from is said with it.
SOURCE_INSTALL = (
    "install ix-cosmic-ray beside ix, from this repository at its release's tag: "
    f'`uv tool install <ix\'s git URL> --with "{COSMIC_RAY_SOURCE}"`, or in a project '
    f'`uv add "ix-cosmic-ray @ {COSMIC_RAY_SOURCE}"`; it is on no package index'
)


def _workspace(suite: Suite, copy: Path) -> dict[str, Any]:
    return {
        "copy": str(copy),
        "root": str(suite.root),
        "argv": list(suite.argv),
        "scope": list(suite.scope),
        "exclude": list(suite.exclude),
        "deselect": list(suite.deselect),
        "map": str(suite.map),
        "confirm": suite.confirm,
        "timeout_factor": suite.timeout_factor,
        "timeout_floor_s": suite.timeout_floor_s,
    }


def _suite(workspace: Mapping[str, Any]) -> tuple[Suite, Path]:
    suite = Suite(
        root=Path(workspace["root"]),
        argv=tuple(workspace["argv"]),
        scope=tuple(workspace["scope"]),
        exclude=tuple(workspace["exclude"]),
        deselect=tuple(workspace["deselect"]),
        map=Path(workspace["map"]),
        confirm=str(workspace["confirm"]),
        timeout_factor=float(workspace["timeout_factor"]),
        timeout_floor_s=float(workspace["timeout_floor_s"]),
    )
    return suite, Path(workspace["copy"])


def coverage_installed(python: str, root: Path) -> str | None:
    """Why coverage.py and pytest-cov cannot be imported by the suite's Python; None when
    both can."""
    try:
        found = subprocess.run(
            [python, "-c", "import coverage, pytest_cov"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"{python} could not be asked: {error}"
    if found.returncode != 0:
        return (found.stderr.strip().splitlines() or ["it could not import them"])[-1]
    return None


def coverage_fix(python: str) -> str:
    return (
        f"install pytest-cov, which brings coverage.py, into the suite's environment "
        f"(`uv pip install --python {python} pytest-cov` for a uv environment, which has no "
        f"pip; `{python} -m pip install pytest-cov` otherwise; or the suite's own dev "
        "dependencies): ix runs the baseline under its per-test contexts; ix itself does not "
        "depend on it"
    )


def _startable(program: str) -> bool:
    if os.sep in program or "/" in program:
        return Path(program).is_file() and os.access(program, os.X_OK)
    return shutil.which(program) is not None


def _is_mutant(kind: str) -> bool:
    return kind in (PROBE_KIND, MUTANT_KIND)


class TestSuite:
    """The ``test-suite`` subject: a fresh copy of the tree for each trial, released on exit.
    A :class:`~ix.extension.Preflight` (the program, the tree, the scope and a map that
    matches it) and a :class:`~ix.extension.Review` (every mutant inside the scope, and a
    population to run)."""

    def __init__(self, mutant_sources: MutantSources) -> None:
        self._sources = mutant_sources

    @asynccontextmanager
    async def reach(self, name: str, subject: SubjectConfig, *, base: str) -> AsyncIterator[Target]:
        try:
            suite = suite_of(subject.options, base)
        except ValueError as error:
            raise HarnessError("option", str(error)) from error
        scratch = Path(tempfile.mkdtemp(prefix="ix-mutant-"))
        try:
            try:
                copy = await asyncio.to_thread(copy_tree, suite, scratch)
            except OSError as error:
                raise HarnessError("copy", f"the tree could not be copied: {error}") from error
            yield Target(subject=name, facets={"workspace": _workspace(suite, copy)})
        finally:
            await asyncio.to_thread(shutil.rmtree, scratch, True)

    def requirements(self, options: Mapping[str, Any], *, base: str) -> list[Requirement]:
        try:
            suite = suite_of(options, base)
        except ValueError as error:
            return [
                Requirement(
                    need="the subject's root, scope and command describe a suite",
                    holds=False,
                    why=str(error),
                    fix="name scope files inside root, and the suite's Python first in command",
                )
            ]
        found = [
            Requirement(
                need=f"the tree {suite.root} is there",
                holds=suite.root.is_dir(),
                why="no such folder",
                fix="name the tree the suite runs in as root, relative to the experiment",
            ),
            Requirement(
                need=f"the suite's Python {suite.python} can be started",
                holds=_startable(suite.python),
                why="not there, or not executable",
                fix="name the suite's own interpreter first in command (.venv/bin/python, "
                "relative to root)",
            ),
        ]
        missing = [file for file in suite.scope if not (suite.root / file).is_file()]
        found.append(
            Requirement(
                need="every scope file is there",
                holds=not missing,
                why=f"not there: {', '.join(missing)}",
                fix="name source files that are in the tree",
            )
        )
        found.append(self._mapped(suite, base))
        return found

    def _mapped(self, suite: Suite, base: str) -> Requirement:
        """That the selection map is there and was made of the scope as it is now."""
        need = f"the selection map {suite.map} matches the scope"
        why = ""
        try:
            recorded = read_map(suite.map).get("scope", {})
        except MapUnreadableError as error:
            why = str(error)
        else:
            changed = [
                file
                for file in suite.scope
                if (suite.root / file).is_file()
                and recorded.get(file) != digest((suite.root / file).read_bytes().decode("utf-8"))
            ]
            if changed or set(recorded) != set(suite.scope):
                why = (
                    "the scope changed since the map was made"
                    + (f": {', '.join(changed)}" if changed else "")
                    + "; its mutants and its selection would not be this code's"
                )
        if not why:
            return Requirement(need=need, holds=True)
        probes = Path(base) / "probes"
        if probes.is_dir() and any(probes.iterdir()):
            fix = f"run `ix mutants map {base}`, which makes the map for the probes there"
        else:
            fix = f"run `ix mutants sample {base}`, which draws the mutants and makes the map"
        missing = coverage_installed(suite.python, suite.root) if _startable(suite.python) else None
        if missing is not None:
            why += f"; and coverage.py and pytest-cov are not installed for it ({missing})"
            fix += f"; first {coverage_fix(suite.python)}"
        return Requirement(need=need, holds=False, why=why, fix=fix)

    def review(self, options: Mapping[str, Any], around: Surroundings) -> list[Problem]:
        found: list[Problem] = []
        root = os.path.normpath(os.path.join(around.base, str(options.get("root", "."))))
        scope = set()
        for index, written in enumerate(options.get("scope") or ()):
            path = os.path.normpath(os.path.join(around.base, str(written)))
            relative = os.path.relpath(path, root)
            if relative.startswith(".."):
                found.append(
                    problem(
                        f"{around.path}.scope.{index}",
                        kind="option_invalid",
                        message=f"{written!r} is not inside root {options.get('root')!r}",
                        fix="name source files inside the tree root names",
                    )
                )
            scope.add(relative.replace(os.sep, "/"))
        mutants = [(where, probe) for where, probe in around.probes if _is_mutant(probe.kind)]
        for where, probe in mutants:
            file = probe.options.get("file")
            if isinstance(file, str) and scope and os.path.normpath(file) not in scope:
                found.append(
                    problem(
                        f"{where}.file",
                        kind="option_invalid",
                        message=f"the mutant changes {file}, which {around.path} does not "
                        f"name in its scope: its tests were not judged against it",
                        fix="sample the mutants of this scope again (`ix mutants sample`), or "
                        "add the file to scope",
                    )
                )
        if not mutants:
            sources = self._sources.names()
            fix = f"run `ix mutants sample {around.base}`, which draws them and pins them as files"
            if not sources:
                fix += (
                    f"; it needs a mutant source, and none is installed: {SOURCE_INSTALL}. "
                    "Probe files of kind mutant that another tool wrote run without one, "
                    f"once `ix mutants map {around.base}` has made the map"
                )
            found.append(
                problem(
                    around.path,
                    kind="probes_empty",
                    message="there are no mutants to apply to this suite",
                    fix=fix,
                )
            )
        return found


class Mutant:
    """The ``mutant`` probe: see the module docstring."""

    def __init__(self, launcher: Launcher) -> None:
        self._launcher = launcher

    def budget_s(self, options: Mapping[str, Any]) -> float | None:
        try:
            return duration_of(options, "budget")
        except ValueError as error:
            raise HarnessError("option", str(error)) from error

    async def apply(
        self, options: Mapping[str, Any], target: Target, *, seed: int
    ) -> Mapping[str, Mapping[str, Any]]:
        workspace = target.facets.get("workspace")
        if not isinstance(workspace, dict):
            raise HarnessError("option", "a mutant is applied to a test-suite subject")
        if options.get("equivalent"):
            raise HarnessError("equivalent", f"marked equivalent: {options['equivalent']}")
        suite, copy = _suite(workspace)
        trial = _Trial(self._launcher, suite, copy, options, self.budget_s(options))
        return {"outcome": await trial.carried_out()}


def _position(value: Any) -> tuple[int, int]:
    return int(value[0]), int(value[1])


class _Trial:
    """One mutant, applied to one copy and run in stages."""

    def __init__(
        self,
        launcher: Launcher,
        suite: Suite,
        copy: Path,
        options: Mapping[str, Any],
        budget_s: float | None,
    ) -> None:
        self._launcher, self._suite, self._copy, self._options = launcher, suite, copy, options
        self._ends = None if budget_s is None else time.monotonic() + budget_s
        self._limits: list[float] = []
        self._imported: dict[str, str] = {}
        self._unreproduced: list[str] = []
        self._ran = 0

    async def carried_out(self) -> dict[str, Any]:
        started = time.monotonic()
        options = self._options
        file = self._copy / str(options["file"])
        try:
            text = file.read_bytes().decode("utf-8")
            start, end = _position(options["start"]), _position(options["end"])
        except (OSError, ValueError, TypeError, IndexError) as error:
            raise HarnessError("mutant_inapplicable", f"{options['file']}: {error}") from error
        if span_text(text, start, end) != options["original"]:
            raise HarnessError(
                "mutant_inapplicable",
                f"{options['file']} does not hold {options['original']!r} from {list(start)} "
                f"to {list(end)}: the tree is not the one the mutant was made of",
            )
        mutated = splice(text, start, end, str(options["replacement"]))
        assert mutated is not None  # the span was read above
        try:
            found = read_map(self._suite.map)
        except MapUnreadableError as error:
            raise HarnessError("map", str(error)) from error
        first, last = statement_lines(text, start[0], end[0])
        chosen = select(found, str(options["file"]), first, last, self._suite.deselect)
        file.write_bytes(mutated.encode("utf-8"))
        result = await self._stages(found, chosen, file, text)
        return {
            **result,
            "covered": chosen.covered,
            "lines": [first, last],
            "selected": "all" if chosen.tests is None else len(chosen.tests),
            "ran": self._ran,
            "imported_from": self._imported,
            "unreproduced": self._unreproduced,
            "drifted": options.get("file_digest", digest(text)) != digest(text),
            "timeout_s": self._limits,
            "duration_s": round(time.monotonic() - started, 3),
        }

    async def _stages(
        self, found: dict[str, Any], chosen: Selected, file: Path, text: str
    ) -> dict[str, Any]:
        confirm = self._suite.confirm
        survived: dict[str, Any] | None = None
        if chosen.covered:
            killed = await self._stage(found, chosen.tests, file, text, "selected")
            if killed is not None:
                return killed
            survived = {
                "status": "survived",
                "stage": "selected",
                "killers": [],
                "unattributed": [],
                "covered_by": list(chosen.tests or ()),
            }
            if chosen.tests is None or confirm != "all":
                return survived
        elif confirm == "none":
            return {"status": "uncovered", "stage": "none", "killers": [], "unattributed": [],
                    "covered_by": []}  # fmt: skip
        killed = await self._stage(found, None, file, text, "confirmed")
        if killed is not None:
            return killed
        return {
            "status": "survived",
            "stage": "confirmed",
            "killers": [],
            "unattributed": [],
            "covered_by": [] if survived is None else survived["covered_by"],
        }

    def _limit(self, found: dict[str, Any], tests: Sequence[str] | None) -> float:
        took = baseline_time(found, None if tests is None else tuple(tests), self._suite.deselect)
        return self._within_budget(
            max(self._suite.timeout_floor_s, self._suite.timeout_factor * (took + overhead(found)))
        )

    def _within_budget(self, limit: float) -> float:
        if self._ends is not None:
            left = self._ends - time.monotonic()
            if left <= 0:
                raise HarnessError("timeout", "the trial used all of its budget")
            limit = min(limit, left)
        return limit

    async def _run(
        self, found: dict[str, Any], tests: Sequence[str] | None, *, limit: float | None = None
    ) -> Ran:
        """Run ``tests`` in the copy within ``limit`` (by default, what their baseline time
        allows), the trial's budget left the most it may be."""
        limit = self._limit(found, tests) if limit is None else self._within_budget(limit)
        self._limits.append(round(limit, 3))
        try:
            ran = await run(self._launcher, self._suite, self._copy, select=tests, timeout_s=limit)
        except OverranError as overran:
            raise HarnessError(
                "timeout",
                f"the suite {overran}: {self._suite.timeout_factor:g} times its tests' and its "
                f"start's time in the baseline, and never less than "
                f"{self._suite.timeout_floor_s:g}s",
            ) from None
        elsewhere = ran.elsewhere()
        if elsewhere:
            name, where = next(iter(elsewhere.items()))
            raise HarnessError(
                "not_the_mutant",
                f"{name} was imported from {where}, not from the trial's copy: the suite "
                "did not run the mutant (an editable install, or a path a test sets)",
            )
        for name, where in ran.modules.items():
            if self._imported.get(name) != "copy":
                self._imported[name] = where
        return ran

    async def _stage(
        self,
        found: dict[str, Any],
        tests: tuple[str, ...] | None,
        file: Path,
        text: str,
        stage: str,
    ) -> dict[str, Any] | None:
        """Run ``tests`` on the mutant: a kill, attributed, or None when nothing failed that
        fails again. A failure is a kill only when the test fails again run alone on the
        mutant, and passes run alone on the original: one that does not fail again is a flake
        (``unreproduced``), and the test passed on the mutant when it ran again."""
        ran = await self._run(found, tests)
        self._ran += len(ran.tests)
        failed = _failed(ran)
        if not failed:
            return None
        # On the mutant, a failing test may take far longer than its baseline (a loop the
        # mutant broke, run out by the suite's own per-test timeout), so running it again
        # there may take what the stage was allowed, never only what its baseline allows.
        again = await self._run(found, _rerun(failed, tests), limit=self._limits[-1])
        reproduced = [node for node in failed if _fails(again, node)]
        self._unreproduced += [node for node in failed if node not in reproduced]
        if not reproduced:
            return None
        mutated = file.read_bytes()
        file.write_bytes(text.encode("utf-8"))
        try:
            without = await self._run(found, _rerun(reproduced, tests))
        finally:
            file.write_bytes(mutated)
        killers = [node for node in reproduced if _passes(without, node)]
        unattributed = [node for node in reproduced if node not in killers]
        if not killers:
            raise HarnessError(
                "unattributable",
                f"{len(unattributed)} test(s) failed on the mutant and fail without it too: "
                + ", ".join(unattributed[:5]),
            )
        return {
            "status": "killed",
            "stage": stage,
            "killers": killers,
            "unattributed": unattributed,
            "covered_by": [],
        }


def _rerun(
    nodes: Sequence[str], tests: tuple[str, ...] | None
) -> list[str] | tuple[str, ...] | None:
    """What to run again to see ``nodes`` again: the tests themselves, or the stage's whole
    selection for a collection error."""
    return tests if COLLECTION in nodes else [node for node in nodes if node != COLLECTION]


def _fails(ran: Ran, node: str) -> bool:
    if node == COLLECTION:
        return bool(ran.collection_errors)
    return ran.tests.get(node, {}).get("outcome") in ("failed", "error")


def _passes(ran: Ran, node: str) -> bool:
    if node == COLLECTION:
        return not ran.collection_errors and ran.exit in (0, 1)
    return ran.tests.get(node, {}).get("outcome") == "passed"


def _failed(ran: Ran) -> list[str]:
    """What failed in a run on the mutant: its failing tests, and ``<collection>`` for a
    collection error. Raises :class:`HarnessError` (``runner``) for an exit that is pytest's
    own trouble (an internal error, a usage error, no test collected, a signal)."""
    if ran.exit == 0:
        return []
    if ran.exit == 1 and ran.failed():
        return ran.failed()
    if ran.exit == 2 and ran.collection_errors:
        return [COLLECTION, *ran.failed()]
    raise HarnessError(
        "runner",
        f"pytest exited {ran.exit}, which is not a test failing: "
        f"{ran.stderr.strip()[-600:] or 'nothing on stderr'}",
    )


class MutantKill:
    """The ``mutant-kill`` sensor: ``killed`` from the mutant probe's outcome, which it keeps
    whole as its detail."""

    def watch(self, target: Target, options: Mapping[str, Any]) -> None:
        return None

    async def sense(
        self, interaction: Interaction, *, truth: Mapping[str, Any], options: Mapping[str, Any]
    ) -> Sensed:
        outcome = dict(interaction.observations["outcome"])
        return Sensed(values={"killed": outcome["status"] == "killed"}, detail=outcome)

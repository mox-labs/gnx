"""A test suite, copied and run: what a trial and the baseline share.

**The tree is read, never written.** Every run is in a fresh copy of ``root``, without the
names ``exclude`` lists, and the suite's command runs in that copy.

**The copy is what is imported.** The command runs with the copy's import roots first on
``PYTHONPATH``: the parent of each scope file's top package (the first folder above it with
no ``__init__.py``). An editable install of the tree puts its own folder on the path through
a ``.pth`` file, which comes after ``PYTHONPATH``; without this, the suite imports the
original, and every mutant would survive. ix's pytest plugin (``plugin/ix_mutation_trial.py``)
reports where each scope module was imported from, and a run that imported one from
anywhere but the copy is refused by its caller. ``PYTHONDONTWRITEBYTECODE`` is set, so no
compiled file outlives the text it was compiled from when a mutant is restored.

**The interpreter is the suite's own.** ``command`` is the suite's Python and pytest's
arguments (``[.venv/bin/python, -m, pytest, tests]``); a relative program with a folder in it
is resolved against ``root``, since the copy leaves environments out.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ix.extension import HarnessError, duration_of

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ix.extension import Launcher

#: The folder put first on the suite's ``PYTHONPATH`` for ix's plugin, and the plugin's name.
PLUGIN_DIR = Path(__file__).resolve().parent / "plugin"
PLUGIN = "ix_mutation_trial"

#: The most of a run's stderr kept to say why it failed, in characters: its end.
STDERR_LIMIT = 4000

#: How long a run that has finished, or overrun, has to end with its process group.
GRACE_S = 1.0


@dataclass(frozen=True)
class Suite:
    """A ``test-suite`` subject's options, resolved against the experiment's directory."""

    root: Path
    argv: tuple[str, ...]
    scope: tuple[str, ...]
    exclude: tuple[str, ...]
    deselect: tuple[str, ...]
    map: Path
    confirm: str
    timeout_factor: float
    timeout_floor_s: float

    @property
    def python(self) -> str:
        return self.argv[0]

    def modules(self) -> dict[str, str]:
        """Each scope file's module, by name, with its path relative to ``root``."""
        return {module_name(self.root, file)[1]: file for file in self.scope}

    def import_roots(self) -> tuple[str, ...]:
        """The folders, relative to ``root``, a scope file's module is imported from."""
        found: list[str] = []
        for file in self.scope:
            folder = module_name(self.root, file)[0]
            if folder not in found:
                found.append(folder)
        return tuple(found)


def module_name(root: Path, file: str) -> tuple[str, str]:
    """The import root (relative to ``root``) and the dotted name of the module at ``file``."""
    path = Path(file)
    parts = [path.stem] if path.name != "__init__.py" else []
    folder = path.parent
    while (root / folder / "__init__.py").is_file() and folder != Path("."):
        parts.insert(0, folder.name)
        folder = folder.parent
    return folder.as_posix(), ".".join(parts)


def suite_of(options: Mapping[str, Any], base: str) -> Suite:
    """The suite an entry's options describe. Raises :class:`ValueError` naming the option
    that does not say what it must."""
    root = (Path(base) / str(options["root"])).resolve()
    command = [str(part) for part in options["command"]]
    if not command:
        raise ValueError("command is empty: write the suite's Python and pytest's arguments")
    program = command[0]
    if not Path(program).is_absolute() and ("/" in program or os.sep in program):
        program = str(root / program)
    scope: list[str] = []
    for written in options["scope"]:
        path = (Path(base) / str(written)).resolve()
        try:
            scope.append(path.relative_to(root).as_posix())
        except ValueError:
            raise ValueError(f"scope {written!r} is not inside root {str(root)!r}") from None
    return Suite(
        root=root,
        argv=(program, *command[1:]),
        scope=tuple(scope),
        exclude=tuple(str(options["exclude"]).split()),
        deselect=tuple(str(node) for node in options.get("deselect") or ()),
        map=(Path(base) / str(options["map"])).resolve(),
        confirm=str(options["confirm"]),
        timeout_factor=float(options["timeout_factor"]),
        timeout_floor_s=duration_of(options, "timeout_floor"),
    )


def copy_tree(suite: Suite, into: Path) -> Path:
    """A fresh copy of the suite's tree under ``into``, without what ``exclude`` names; links
    are copied as links."""
    target = into / "tree"
    shutil.copytree(
        suite.root, target, symlinks=True, ignore=shutil.ignore_patterns(*suite.exclude)
    )
    return target


class OverranError(Exception):
    """A run went past its time limit."""

    def __init__(self, limit_s: float) -> None:
        super().__init__(f"ran past {limit_s:.4g}s")
        self.limit_s = limit_s


@dataclass(frozen=True)
class Ran:
    """One run of the suite in a copy, as ix's plugin reported it."""

    exit: int
    tests: dict[str, dict[str, Any]]
    collection_errors: tuple[str, ...]
    modules: dict[str, str]
    rootdir: str
    duration_s: float
    stderr: str

    def failed(self) -> list[str]:
        return sorted(
            node for node, test in self.tests.items() if test["outcome"] in ("failed", "error")
        )

    def elsewhere(self) -> dict[str, str]:
        """Each scope module imported from anywhere but the copy, with where it came from."""
        return {
            name: where for name, where in self.modules.items() if where not in ("copy", "absent")
        }


async def _tail(stream: asyncio.StreamReader | None) -> str:
    if stream is None:
        return ""
    kept = bytearray()
    while chunk := await stream.read(65536):
        kept += chunk
        del kept[: max(0, len(kept) - STDERR_LIMIT)]
    return kept.decode("utf-8", errors="replace")


async def _drain(stream: asyncio.StreamReader | None) -> None:
    if stream is not None:
        while await stream.read(65536):
            pass


async def run(
    launcher: Launcher,
    suite: Suite,
    copy: Path,
    *,
    select: Sequence[str] | None,
    timeout_s: float,
    arguments: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
) -> Ran:
    """Run the suite's command in ``copy`` with ix's plugin: the tests in ``select`` (every
    test when None), less the suite's ``deselect``. Raises :class:`OverranError` past ``timeout_s``
    and :class:`HarnessError` (``runner``) when pytest could not be started or wrote no
    report."""
    with tempfile.TemporaryDirectory(prefix="ixm-") as scratch:
        report = Path(scratch, "report.json")
        private = Path(scratch, "tmp")
        private.mkdir()
        env = {
            **os.environ,
            **(environment or {}),
            "PYTHONPATH": os.pathsep.join(
                [
                    str(PLUGIN_DIR),
                    *(str(copy / folder) for folder in suite.import_roots()),
                    *filter(None, [os.environ.get("PYTHONPATH", "")]),
                ]
            ),
            "PYTHONDONTWRITEBYTECODE": "1",
            # A temporary directory of its own: two trials in flight share none (a test that
            # holds the system's temporary directory to what it held before, as one of
            # recon's does, fails under another trial's files otherwise).
            "TMPDIR": str(private),
            "IX_MUTATION_REPORT": str(report),
            "IX_MUTATION_MODULES": str(
                _written(
                    Path(scratch, "modules.json"),
                    {name: str(copy / file) for name, file in suite.modules().items()},
                )
            ),
            "IX_MUTATION_DESELECT": str(
                _written(Path(scratch, "deselect.json"), list(suite.deselect))
            ),
        }
        if select is not None:
            env["IX_MUTATION_SELECT"] = str(_written(Path(scratch, "select.json"), list(select)))
        argv = [*suite.argv, "-p", PLUGIN, *arguments]
        started = time.monotonic()
        try:
            process = await launcher.start(
                argv,
                cwd=str(copy),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise HarnessError("runner", f"{argv[0]!r} could not be started: {error}") from error
        try:
            async with asyncio.timeout(timeout_s):
                _, stderr, code = await asyncio.gather(
                    _drain(process.stdout), _tail(process.stderr), process.wait()
                )
        except TimeoutError:
            raise OverranError(timeout_s) from None
        finally:
            await launcher.stop(process, grace_s=GRACE_S)
        took = time.monotonic() - started
        if not report.is_file():
            raise HarnessError(
                "runner",
                f"pytest exited {code} and ix's plugin wrote no report: "
                f"{stderr.strip()[-600:] or 'nothing on stderr'}",
            )
        found = json.loads(report.read_text(encoding="utf-8"))
    return Ran(
        exit=int(found["exit"]),
        tests=dict(found["tests"]),
        collection_errors=tuple(found["collection_errors"]),
        modules=dict(found["modules"]),
        rootdir=str(found["rootdir"]),
        duration_s=took,
        stderr=stderr,
    )


def _written(path: Path, value: Any) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path

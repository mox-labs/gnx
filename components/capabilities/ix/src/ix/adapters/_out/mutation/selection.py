"""The selection map, and which tests a mutant is run against.

The map (``ix.v1.mutation-map``) is what the suite's baseline run recorded under coverage.py's
per-test contexts (``ix mutants sample`` writes it), one per suite, its ``subjects`` the ones it
serves: for every test, its outcome and duration,
the lines it ran in each scope file and the other files it ran; and the lines of each scope
file that ran outside every test (imports, collection). It is a file beside the experiment,
named by the subject's ``map`` option, so it is digested and frozen with the registration.

**Selection.** A mutant is run against the tests that ran a line of the **statement** it lies
in (:func:`statement_lines`), less the suite's ``deselect``: the whole of a simple statement,
or the header of a compound one (an ``if``'s test, a ``def``'s signature), whose body runs only
through it. The mutated line alone is not enough: a test that short-circuits a multi-line
condition before its last line never runs that line, and a mutant of it (``and`` for ``or``)
changes the test's path all the same (found in the acceptance run on ``items.py``, design
record §32). A mutant whose statement ran only outside every test (a module-level statement,
a default argument) runs every test. One whose statement nothing ran is **uncovered**: no test
could have seen it, and only a whole-suite confirmation can say otherwise (a line run only in
a child process is not recorded against a test).

Readers ignore fields they do not know and refuse a map of a schema they do not.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

#: The map's schema; a reader refuses any other.
MAP_SCHEMA = "ix.v1.mutation-map"


class MapUnreadableError(Exception):
    """The map is not there, or is not one this ix reads."""


@dataclass(frozen=True)
class Selected:
    """The tests a mutant is run against: ``tests`` (None: every test), and whether a test
    ran its span (``covered``)."""

    tests: tuple[str, ...] | None
    covered: bool


def read_map(path: Path) -> dict[str, Any]:
    """The map at ``path``. Raises :class:`MapUnreadableError` for one that is not there or not an
    ``ix.v1.mutation-map``."""
    try:
        stat = path.stat()
    except OSError as error:
        raise MapUnreadableError(f"no selection map at {path}: {error.strerror}") from None
    return _read(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=8)
def _read(path: str, mtime_ns: int, size: int) -> dict[str, Any]:
    """The map, read once for each state of the file: a run's trials share one."""
    try:
        found = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise MapUnreadableError(f"the selection map {path} cannot be read: {error}") from None
    if not isinstance(found, dict) or found.get("schema") != MAP_SCHEMA:
        raise MapUnreadableError(f"{path} is not an {MAP_SCHEMA} document")
    return found


def statement_lines(text: str, start_line: int, end_line: int) -> tuple[int, int]:
    """The lines a mutant spanning ``start_line`` to ``end_line`` of ``text`` acts through:
    those of the innermost statement that holds it, or only its header when that statement is
    compound (``if``, ``while``, ``def``, ``try``, ...) and the mutant lies before its body.
    The span's own lines when the text does not parse or no statement holds it (a module-level
    decorator, which runs at import)."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return start_line, end_line
    holding = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.stmt)
        and node.lineno <= start_line
        and end_line <= (node.end_lineno or node.lineno)
    ]
    if not holding:
        return start_line, end_line
    # The innermost: it starts last, and of those, ends first.
    inner = max(holding, key=lambda node: (node.lineno, -(node.end_lineno or node.lineno)))
    inside = [
        child.lineno
        for field in ("body", "handlers", "orelse", "finalbody")
        for child in getattr(inner, field, None) or ()
    ]
    if inside and end_line < min(inside):
        return inner.lineno, min(inside) - 1
    return inner.lineno, inner.end_lineno or inner.lineno


def select(
    found: dict[str, Any], file: str, start_line: int, end_line: int, deselect: tuple[str, ...]
) -> Selected:
    """The tests a mutant spanning ``start_line`` to ``end_line`` of ``file`` runs against."""
    span = set(range(start_line, end_line + 1))
    left_out = set(deselect)
    tests = found.get("tests", {})
    ran = tuple(
        sorted(
            node
            for node, test in tests.items()
            if node not in left_out
            and test.get("outcome") == "passed"
            and span & set(test.get("lines", {}).get(file, ()))
        )
    )
    if ran:
        return Selected(tests=ran, covered=True)
    if span & set(found.get("import_lines", {}).get(file, ())):
        return Selected(tests=None, covered=True)
    return Selected(tests=(), covered=False)


def baseline_time(
    found: dict[str, Any], tests: tuple[str, ...] | None, deselect: tuple[str, ...]
) -> float:
    """What ``tests`` took in the baseline, in seconds (every test's, less ``deselect``, for
    None)."""
    recorded = found.get("tests", {})
    named = (
        [node for node in recorded if node not in set(deselect)] if tests is None else list(tests)
    )
    return sum(float(recorded.get(node, {}).get("duration_s", 0.0)) for node in named)


def overhead(found: dict[str, Any]) -> float:
    """What one run of the suite takes besides its tests (starting, collecting), in seconds,
    as the baseline measured it."""
    return float(found.get("baseline", {}).get("overhead_s", 0.0))

"""The population of mutants, and the probe files that pin a sample of it.

A mutant source (:class:`~ix.extension.MutantSource`) says what it would change; this module
decides what counts. Each mutation is applied to the file's text, and it is a **candidate**
when the text it makes parses and differs, as Python's syntax tree, from the original. The
rest are left out and counted, never dropped silently:

* ``unparsable``: the mutated text is not Python (cosmic-ray's binary-operator replacers
  mistake the ``*`` and ``/`` markers of a signature for operators);
* ``unchanged``: its syntax tree is the original's (a change of layout only);
* ``outside_text``: its span is not inside the file;
* ``annotation``: it changes only a type annotation, which a running program does not read
  (a function's, a variable's, a return's);
* ``type_checking``: it lies in an ``if TYPE_CHECKING:`` block, the imports only a type
  checker runs;
* ``marked``: a line it spans carries ``# ix: no mutate``.

A mutant's **id** is ``<file>:<key>``, the file relative to the tree's root and the key the
source gave it, and it is pinned to the file's digest as it was sampled (``file_digest``), so a
file that changed since is seen. The probe file's name is ``m-`` and 12 hex digits of the
sha256 of both.

**The sample** is a simple random sample without replacement, drawn with ``random.Random``
from the seed over the population in id order: the same population and seed give the same
sample on every machine. A population no larger than the sample is taken whole (a census).
"""

from __future__ import annotations

import ast
import hashlib
import json
import random
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from ix.extension import Mutation, Mutations

#: The kind a probe file of a mutant names.
PROBE_KIND = "mutant"

#: The comment that keeps a line from being mutated.
NO_MUTATE = "# ix: no mutate"

#: Why a mutation a source made is not a candidate, each counted.
DROPPED = ("unparsable", "unchanged", "outside_text", "annotation", "type_checking", "marked")


def digest(text: str) -> str:
    """The digest a mutant is pinned to: of the file's bytes, as UTF-8."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Mutant:
    """A candidate: one mutation of one file, applied and checked."""

    file: str
    key: str
    operator: str
    start: tuple[int, int]
    end: tuple[int, int]
    original: str
    replacement: str
    file_digest: str

    @property
    def id(self) -> str:
        return f"{self.file}:{self.key}"

    @property
    def probe_id(self) -> str:
        pinned = f"{self.id}@{self.file_digest}".encode()
        return "m-" + hashlib.sha256(pinned).hexdigest()[:12]

    def options(self) -> dict[str, object]:
        """The ``mutant`` probe's options for this mutant."""
        return {
            "file": self.file,
            "start": list(self.start),
            "end": list(self.end),
            "original": self.original,
            "replacement": self.replacement,
            "operator": self.operator,
            "mutant": self.id,
            "file_digest": self.file_digest,
        }


def offset(lines: Sequence[str], position: tuple[int, int]) -> int | None:
    """The character offset of ``(line, column)`` in the text split into ``lines`` (each with
    its line ending); None for a position outside it. A position just past the last line's
    end is the end of the text."""
    line, column = position
    if line < 1 or column < 0:
        return None
    if line == len(lines) + 1 and column == 0:
        return sum(len(text) for text in lines)
    if line > len(lines) or column > len(lines[line - 1]):
        return None
    return sum(len(text) for text in lines[: line - 1]) + column


def splice(text: str, start: tuple[int, int], end: tuple[int, int], replacement: str) -> str | None:
    """``text`` with ``replacement`` from ``start`` to ``end``; None when the span is not in
    it."""
    lines = text.splitlines(keepends=True)
    first, last = offset(lines, start), offset(lines, end)
    if first is None or last is None or last < first:
        return None
    return text[:first] + replacement + text[last:]


def span_text(text: str, start: tuple[int, int], end: tuple[int, int]) -> str | None:
    lines = text.splitlines(keepends=True)
    first, last = offset(lines, start), offset(lines, end)
    if first is None or last is None or last < first:
        return None
    return text[first:last]


def _characters(lines: Sequence[str], line: int, byte_column: int) -> int:
    """A syntax tree's column, in UTF-8 bytes, as characters."""
    return len(lines[line - 1].encode("utf-8")[:byte_column].decode("utf-8", errors="replace"))


@dataclass(frozen=True)
class _Exclusions:
    """The spans of a file no candidate may lie in, and the lines marked."""

    annotations: tuple[tuple[tuple[int, int], tuple[int, int]], ...]
    type_checking: tuple[tuple[tuple[int, int], tuple[int, int]], ...]
    marked: frozenset[int]

    def why(self, start: tuple[int, int], end: tuple[int, int]) -> str | None:
        if any(start[0] <= line <= end[0] for line in self.marked):
            return "marked"
        for reason, spans in (
            ("type_checking", self.type_checking),
            ("annotation", self.annotations),
        ):
            if any(low <= start and end <= high for low, high in spans):
                return reason
        return None


def _exclusions(text: str, tree: ast.Module) -> _Exclusions:
    lines = text.splitlines(keepends=True)

    def span(node: ast.expr | ast.stmt) -> tuple[tuple[int, int], tuple[int, int]]:
        end_line = node.end_lineno or node.lineno
        end_column = node.end_col_offset if node.end_col_offset is not None else node.col_offset
        return (
            (node.lineno, _characters(lines, node.lineno, node.col_offset)),
            (end_line, _characters(lines, end_line, end_column)),
        )

    annotations: list[tuple[tuple[int, int], tuple[int, int]]] = []
    type_checking: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            arguments = node.args
            every = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
            every += [argument for argument in (arguments.vararg, arguments.kwarg) if argument]
            annotations += [span(a.annotation) for a in every if a.annotation is not None]
            if node.returns is not None:
                annotations.append(span(node.returns))
        elif isinstance(node, ast.AnnAssign):
            annotations.append(span(node.annotation))
        elif isinstance(node, ast.If) and _is_type_checking(node.test):
            type_checking.append(span(node))
    marked = frozenset(number for number, line in enumerate(lines, start=1) if NO_MUTATE in line)
    return _Exclusions(tuple(annotations), tuple(type_checking), marked)


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


@dataclass(frozen=True)
class Made:
    """What one file gave: its candidates, and each mutation left out, by why."""

    candidates: tuple[Mutant, ...]
    dropped: Counter[str]
    #: What the source meant to make and could not, by operator: (count, reason).
    unmade: dict[str, tuple[int, str]]


def candidates(file: str, text: str, mutations: Mutations) -> Made:
    """The candidates among ``mutations`` of ``text`` (the file ``file``, relative to the
    tree's root). Raises :class:`SyntaxError` when ``text`` itself does not parse."""
    tree = ast.parse(text)
    original = ast.dump(tree)
    excluded = _exclusions(text, tree)
    pinned = digest(text)
    kept: list[Mutant] = []
    dropped: Counter[str] = Counter()
    for mutation in mutations.made:
        reason, mutant = _checked(file, text, original, excluded, pinned, mutation)
        if mutant is None:
            dropped[reason] += 1
        else:
            kept.append(mutant)
    unmade = {item.operator: (item.count, item.reason) for item in mutations.unmade}
    return Made(candidates=tuple(kept), dropped=dropped, unmade=unmade)


def _checked(
    file: str,
    text: str,
    original: str,
    excluded: _Exclusions,
    pinned: str,
    mutation: Mutation,
) -> tuple[str, Mutant | None]:
    try:
        start_at = (int(mutation.start[0]), int(mutation.start[1]))
        end_at = (int(mutation.end[0]), int(mutation.end[1]))
    except (TypeError, ValueError, IndexError):
        return "outside_text", None
    replaced = span_text(text, start_at, end_at)
    mutated = splice(text, start_at, end_at, mutation.replacement)
    if replaced is None or mutated is None:
        return "outside_text", None
    try:
        tree = ast.parse(mutated)
    except (SyntaxError, ValueError):
        return "unparsable", None
    if ast.dump(tree) == original:
        return "unchanged", None
    why = excluded.why(start_at, end_at)
    if why is not None:
        return why, None
    return "", Mutant(
        file=file,
        key=mutation.key,
        operator=mutation.operator,
        start=start_at,
        end=end_at,
        original=replaced,
        replacement=mutation.replacement,
        file_digest=pinned,
    )


def sample(
    population: Sequence[Mutant], n: int, seed: int, *, exclude: Iterable[str] = ()
) -> list[Mutant]:
    """``n`` mutants of ``population`` drawn without replacement from ``seed``, none whose id
    is in ``exclude``, in id order; the whole of what is left when it holds no more than
    ``n``."""
    excluded = set(exclude)
    left = sorted((m for m in population if m.id not in excluded), key=lambda m: m.id)
    if len(left) <= n:
        return left
    return sorted(random.Random(seed).sample(left, n), key=lambda m: m.id)


def probe_file(mutant: Mutant, *, source: str, seed: int) -> str:
    """The probe file of ``mutant``, as YAML: each value is written as JSON, which YAML reads
    as the same value, so no YAML library is needed to write it."""
    header = (
        f"# A mutant `ix mutants sample` drew (seed {seed}) from {source}.\n"
        "# A survivor that behaves as the original may be marked, under options:\n"
        '#   equivalent: "<why it cannot be told from the original>"\n'
        "# which moves the probe set's digest: amend the registration with the reason.\n"
    )
    lines = [f"kind: {PROBE_KIND}", "options:"]
    lines += [f"  {key}: {json.dumps(value)}" for key, value in mutant.options().items()]
    return header + "\n".join(lines) + "\n"

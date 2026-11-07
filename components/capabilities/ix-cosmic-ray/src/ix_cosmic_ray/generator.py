"""cosmic-ray's operators as an ix mutant source: the one module that imports cosmic-ray.

cosmic-ray is used as a generator library and nothing else: its operators enumerate where they
can mutate a file (``Operator.mutation_positions`` over ``cosmic_ray.ast.ast_nodes``), and
``cosmic_ray.mutating.mutate_code(text, operator, occurrence)`` makes one mutant as text. Its
``init``, its work database, its runner and its distributors are never used: ix runs the
trials.

Every argument-free operator is asked over the **whole file**, so a mutant's occurrence counts
from the top of the file, as ``mutate_code`` counts it, and its key, ``<operator>:<occurrence>``,
is the same for the same text and the same cosmic-ray. An operator that takes arguments
(``core/VariableReplacer``, ``core/VariableInserter``) needs a configuration this source does
not have, and is not asked. An operator that raises on an occurrence makes nothing there; each
is counted, with what it raised, as :class:`~ix.extension.Unmade`. ix applies every mutant it
is given and keeps those that parse and change the code (cosmic-ray's binary-operator
replacers mistake a signature's ``*`` and ``/`` markers, and ``**``, for operators).

**The span.** cosmic-ray positions are the mutated node's, ``(line, column)`` with the line from
1 and the column in characters. ``mutate_code`` gives back the whole file; the mutant handed to
ix is the node's span and the text now in it. Where the new text differs outside that span
(an operator that rewrites more than its node), the span widens to cover every character that
changed, so applying ``replacement`` to the span always gives the text cosmic-ray made.

``core/ExceptionReplacer`` names ``CosmicRayTestingException`` in place of the caught type, as
cosmic-ray itself does: nothing defines that name in the code under test, so the handler
raises ``NameError`` when an exception reaches it. That is cosmic-ray's mutant, kept as made.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cosmic_ray.ast import ast_nodes, get_ast
from cosmic_ray.mutating import mutate_code
from cosmic_ray.plugins import get_operator, operator_names
from ix.extension import Mutation, Mutations, Unmade

if TYPE_CHECKING:
    from collections.abc import Sequence


def _offset(lines: Sequence[str], position: tuple[int, int]) -> int:
    line, column = position
    return sum(len(text) for text in lines[: line - 1]) + column


def _position(lines: Sequence[str], offset: int) -> tuple[int, int]:
    """The ``(line, column)`` of a character offset into the text split into ``lines``."""
    for number, text in enumerate(lines, start=1):
        if offset <= len(text) and (offset < len(text) or number == len(lines)):
            return number, offset
        offset -= len(text)
    return len(lines) + 1, 0


type Span = tuple[tuple[int, int], tuple[int, int], str]


def _span(text: str, mutated: str, start: tuple[int, int], end: tuple[int, int]) -> Span | None:
    """The node's span (start and end) and the text now in it; widened to cover every change
    when the new text differs outside it. None when the two texts are the same."""
    if mutated == text:
        return None
    lines = text.splitlines(keepends=True)
    first, last = _offset(lines, start), _offset(lines, end)
    tail = len(text) - last
    if not (
        0 <= first <= last <= len(text)
        and len(mutated) - tail >= first
        and mutated[:first] == text[:first]
        and mutated[len(mutated) - tail :] == text[last:]
    ):
        prefix = 0
        while prefix < min(len(text), len(mutated)) and text[prefix] == mutated[prefix]:
            prefix += 1
        suffix = 0
        while (
            suffix < min(len(text), len(mutated)) - prefix
            and text[len(text) - 1 - suffix] == mutated[len(mutated) - 1 - suffix]
        ):
            suffix += 1
        first = min(max(0, first), prefix)
        last = max(min(len(text), last), len(text) - suffix)
        tail = len(text) - last
    return _position(lines, first), _position(lines, last), mutated[first : len(mutated) - tail]


def _operators() -> list[tuple[str, Any]]:
    """Every operator cosmic-ray has that takes no arguments, by name, in its own order."""
    found = []
    for name in operator_names():
        operator = get_operator(name)
        if operator.arguments():
            continue  # it needs a configuration this source does not have
        found.append((name, operator()))
    return found


class CosmicRay:
    """A :class:`~ix.extension.MutantSource` over cosmic-ray's argument-free operators."""

    def mutations(self, text: str) -> Mutations:
        tree = get_ast(text)
        made: list[Mutation] = []
        unmade: list[Unmade] = []
        for name, operator in _operators():
            positions = [
                position
                for node in ast_nodes(tree)
                for position in operator.mutation_positions(node)
            ]
            failed, reason = 0, ""
            for occurrence, (start, end) in enumerate(positions):
                try:
                    mutated = mutate_code(text, operator, occurrence)
                except Exception as error:  # cosmic-ray's operator, on this occurrence
                    failed += 1
                    reason = reason or f"{type(error).__name__}: {error}"[:200]
                    continue
                span = None if mutated is None else _span(text, mutated, start, end)
                if span is None:
                    failed += 1
                    reason = reason or "it made no change at this occurrence"
                    continue
                made.append(
                    Mutation(
                        key=f"{name}:{occurrence}",
                        operator=name,
                        start=span[0],
                        end=span[1],
                        replacement=span[2],
                    )
                )
            if failed:
                unmade.append(Unmade(operator=name, count=failed, reason=reason))
        return Mutations(made=tuple(made), unmade=tuple(unmade))

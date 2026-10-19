"""matrix's errors and problems as ix's: one translation, used wherever ix calls matrix.

matrix and ix share the error envelope and the problem record
(``components/capabilities/error-envelope.json``); what differs is the set of kinds each
knows. A problem kind ix does not know becomes ``invalid_value``, with matrix's word kept in
the message; an error kind ix has no class for becomes ``unknown``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ix.domain.errors import ConfigError, IxError, NotFoundError
from ix.domain.problem import CARRIED_KINDS, PROBLEM_KINDS, Problem, problem

if TYPE_CHECKING:
    from collections.abc import Iterable

    import matrix

#: Said beside a key ix does not know, in an experiment or a probe file.
_KEYS_HINT = "`ix --skill -r experiment` lists the keys"


def problems_of(error: matrix.MatrixError) -> tuple[Problem, ...]:
    """The problems a matrix error carries, as ix's."""
    return converted(error.problems)


def converted(problems: Iterable[matrix.Problem]) -> tuple[Problem, ...]:
    """matrix's problems (an error's, or a document's warnings) as ix's. A kind ix does not
    know is ``invalid_value``, with matrix's own word kept in the message."""
    found = []
    for item in problems:
        kind = item.kind if item.kind in PROBLEM_KINDS | CARRIED_KINDS else "invalid_value"
        message = item.message if kind == item.kind else f"{item.message} ({item.kind})"
        fix = item.fix or None
        if kind == "unknown_key" and fix:
            fix = f"{fix}; {_KEYS_HINT}"
        found.append(
            problem(
                item.path,
                kind=kind,
                message=message,
                fix=fix,
                allowed=item.allowed,
                got=item.got,
                source=item.source,
            )
        )
    return tuple(found)


def carried_over(message: str, error: matrix.MatrixError, fix: str | None = None) -> IxError:
    """``error`` as the ix error of its kind (``config``, ``not_found``; any other is
    ``unknown``), its details, problems and completeness kept."""
    kinds: dict[str, type[IxError]] = {"config": ConfigError, "not_found": NotFoundError}
    carried = kinds.get(error.kind, IxError)(
        f"{message}: {error.message}", fix=fix or error.fix, details=error.details
    )
    carried.problems = problems_of(error)
    carried.complete = error.complete
    return carried

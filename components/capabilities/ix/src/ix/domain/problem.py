"""A problem: one thing wrong with an experiment, a genre file or a kind file, in a form an
agent can act on without parsing prose.

``path`` is the offending key, ``kind`` comes from the vocabulary below, ``allowed`` lists
the legal values where there is a closed set, ``got`` is what was written, and ``fix`` says
what to write instead (built from ``allowed`` when nothing more specific is known).

:func:`error_document` is the one envelope every failing command prints.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, field_validator

from ix.domain import schema
from ix.domain.errors import EXIT_CODES, ConfigError, IxError
from ix.domain.schema import Record

if TYPE_CHECKING:
    from pydantic import ValidationError

#: Every kind a problem or a warning can have. A consumer should tolerate a kind it does not
#: know: the set grows. The first eight are the kinds every capability shares
#: (``components/capabilities/error-envelope.json``): ``name_unknown`` is a genre, kind,
#: subject or parent genre named and not defined, ``name_taken`` a genre, kind or probe id
#: defined twice, or a sensor kind listed twice; the rest are ix's.
PROBLEM_KINDS: frozenset[str] = frozenset(
    {
        # shared: the file as a document, and names
        "yaml_syntax",
        "schema_unsupported",
        "wrong_shape",
        "unknown_key",
        "missing_key",
        "invalid_value",
        "name_unknown",
        "name_taken",
        # the experiment against its genre
        "genre_roles",
        "genre_factors",
        "must_set",
        "repeats_below_min",
        "control_not_applicable",
        "comparison_not_allowed",
        "margin_required",
        "guardrail_form",
        # kinds and their options
        "kind_unfit",
        "option_unknown",
        "option_missing",
        "option_invalid",
        "option_inapplicable",
        "unobservable",
        # metrics and the decision
        "metric_unprovided",
        "metric_ambiguous",
        "metric_mismatch",
        "metric_direction",
        "statistic_illegal",
        "estimator_illegal",
        "judged_gate",
        "unit_mismatch",
        # probes and placeholders
        "probes_empty",
        "truth_unclaimed",
        "unbound_placeholder",
        # genre and kind files
        "genre_invalid",
        "genre_depth",
        "genre_narrowing",
        "kind_invalid",
        # warnings
        "subjects_differ_in",
        "kind_unimplemented",
        "analysis_unimplemented",
        "genre_rejected",
        "kind_rejected",
    }
)


#: matrix's own kinds, which a problem carried over from matrix (a run that could not be
#: wired, a telemetry warning) may have. ``validate`` reports none of them.
CARRIED_KINDS: frozenset[str] = frozenset(
    {"unprovided", "cycle", "dependency_missing", "permissive"}
)


class Problem(Record):
    """One problem. ``path`` is dotted from the document root (``subjects.buffered.kind``).

    ``source`` names the file when it is not the experiment itself (a genre or kind file).
    """

    path: str
    kind: str
    message: str
    fix: str
    allowed: tuple[str, ...] = ()
    got: Any = None
    source: str = ""

    @field_validator("kind")
    @classmethod
    def _known(cls, kind: str) -> str:
        if kind not in PROBLEM_KINDS | CARRIED_KINDS:
            raise ValueError(f"{kind!r} is not a problem kind")
        return kind


def problem(
    path: str,
    *,
    kind: str,
    message: str,
    fix: str | None = None,
    allowed: tuple[str, ...] | list[str] = (),
    got: Any = None,
    source: str = "",
) -> Problem:
    """A problem whose ``fix`` is built from ``allowed`` when none is given."""
    allowed = tuple(str(item) for item in allowed)
    if fix is None:
        key = path.rsplit(".", 1)[-1] or "the value"
        fix = (
            f"{key}: one of {', '.join(allowed)}"
            if allowed
            else f"correct the value at {path or 'the top of the file'}"
        )
    return Problem(
        path=path, kind=kind, message=message, fix=fix, allowed=allowed, got=got, source=source
    )


class Validation(Record):
    """What ``validate`` found: every problem and every warning, in one document.

    ``complete`` is false when the file did not load as an experiment, so the checks against
    the genre, the kinds and the probes never ran and more problems may follow a fix.
    """

    schema_: Literal["ix.v1.validation"] = Field(default=schema.VALIDATION, alias="schema")
    experiment: str = ""
    source: str = ""
    #: The genre's short name as the author writes it, and its type URL.
    genre: str = ""
    genre_url: str = ""
    valid: bool
    complete: bool = True
    problems: tuple[Problem, ...] = ()
    warnings: tuple[Problem, ...] = ()

    @property
    def exit_code(self) -> int:
        """What a command that printed this document exits with."""
        return EXIT_CODES["ok" if self.valid else "config"]


class InvalidExperimentError(ConfigError):
    """An experiment that cannot be planned or registered. ``problems`` says why, in full."""

    def __init__(
        self,
        message: str,
        problems: tuple[Problem, ...],
        *,
        fix: str | None = None,
        complete: bool = True,
    ):
        super().__init__(message, fix=fix)
        self.problems = problems
        self.complete = complete


def error_document(error: IxError) -> dict[str, Any]:
    """The ``ix.v1.error`` envelope: the one shape a failing command prints, as
    ``components/capabilities/error-envelope.json`` agrees it.

    ``complete``, ``details``, ``problems`` and ``deviations`` are always present;
    ``complete`` is false when checking stopped before every problem was found.
    """
    return {
        "schema": schema.ERROR,
        "error": {
            "kind": error.kind,
            "message": str(error),
            "fix": error.fix,
            "complete": error.complete,
            "details": error.details,
            "problems": [item.document() for item in error.problems],
            "deviations": [item.document() for item in error.deviations],
        },
    }


# --- pydantic errors as problems ---------------------------------------------------------

_QUOTED = re.compile(r"'([^']*)'")
#: pydantic error types that say a value has the wrong type or structure: the same reading
#: matrix's loader gives them, so a file and an object built in memory report alike.
_SHAPE = re.compile(
    r"^(dict|list|tuple|set|frozenset|model|model_attributes|string|int|float|bool|"
    r"none_required|is_instance|dataclass|arguments)_type$|^(int|float|bool)_parsing$"
)


def _path(loc: tuple[Any, ...], prefix: str) -> str:
    return ".".join(([prefix] if prefix else []) + [str(part) for part in loc])


def _scalar(value: Any) -> Any:
    return value if isinstance(value, str | int | float | bool) or value is None else None


def from_validation_error(
    error: ValidationError, *, prefix: str = "", source: str = ""
) -> list[Problem]:
    """Each pydantic error as a :class:`Problem` with a path, the legal set and a fix."""
    problems: list[Problem] = []
    for item in error.errors():
        path = _path(item["loc"], prefix)
        key = str(item["loc"][-1]) if item["loc"] else ""
        got = _scalar(item.get("input"))
        context = item.get("ctx") or {}
        if item["type"] == "extra_forbidden":
            found = problem(
                path,
                kind="unknown_key",
                message=f"{key!r} is not a key ix knows here",
                fix=f"remove {key!r} or correct its spelling; `ix --skill -r experiment` lists "
                "the keys",
                source=source,
            )
        elif item["type"] == "missing":
            found = problem(
                path,
                kind="missing_key",
                message=f"{key!r} is required",
                fix=f"add {key!r}",
                source=source,
            )
        elif item["type"] == "literal_error" and key == "schema":
            allowed = _QUOTED.findall(str(context.get("expected", "")))
            found = problem(
                path,
                kind="schema_unsupported",
                message=f"this ix does not read schema {got!r} here",
                fix=f"schema: {allowed[0]}, or leave 'schema' out" if allowed else None,
                allowed=allowed,
                got=got,
                source=source,
            )
        elif item["type"] == "literal_error":
            allowed = _QUOTED.findall(str(context.get("expected", "")))
            found = problem(
                path,
                kind="invalid_value",
                message=f"{key!r} is one of {', '.join(allowed)}; got {got!r}",
                allowed=allowed,
                got=got,
                source=source,
            )
        else:
            message = item["msg"].removeprefix("Value error, ")
            found = problem(
                path,
                kind="wrong_shape" if _SHAPE.match(str(item["type"])) else "invalid_value",
                message=message,
                fix=f"give {path or 'the file'} a value that fits: {message}",
                got=got,
                source=source,
            )
        problems.append(found)
    return problems

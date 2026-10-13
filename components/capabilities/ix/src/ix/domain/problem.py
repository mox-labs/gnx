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
from ix.domain.errors import EXIT_BY_KIND, ConfigError, IxError
from ix.domain.schema import Record

if TYPE_CHECKING:
    from pydantic import ValidationError

#: Every kind a problem or a warning can have. A consumer should tolerate a kind it does not
#: know: the set grows.
PROBLEM_KINDS: frozenset[str] = frozenset(
    {
        # the file as a document
        "yaml_syntax",
        "schema_unsupported",
        "wrong_shape",
        "unknown_key",
        "missing_key",
        "invalid_value",
        # the experiment against its genre
        "genre_unknown",
        "genre_roles",
        "genre_factors",
        "must_set",
        "repeats_below_min",
        "control_unknown",
        "control_not_applicable",
        "comparison_not_allowed",
        "margin_required",
        "guardrail_form",
        # kinds and their options
        "kind_unknown",
        "kind_unfit",
        "option_unknown",
        "option_missing",
        "option_invalid",
        # metrics and the decision
        "metric_unprovided",
        "metric_ambiguous",
        "metric_mismatch",
        "metric_direction",
        "statistic_illegal",
        "estimator_illegal",
        "judged_gate",
        "unit_mismatch",
        # probes, placeholders and spend
        "probes_empty",
        "probe_source",
        "probe_duplicate_id",
        "truth_unclaimed",
        "unbound_placeholder",
        "spend_over_ceiling",
        "spend_unbounded",
        # genre and kind files
        "genre_invalid",
        "genre_duplicate",
        "genre_extends_unknown",
        "genre_depth",
        "genre_narrowing",
        "kind_invalid",
        "kind_duplicate",
        # warnings
        "subjects_differ_in",
        "genre_rejected",
        "kind_rejected",
    }
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
        if kind not in PROBLEM_KINDS:
            raise ValueError(f"{kind!r} is not a problem kind")
        return kind


def problem(
    path: str,
    kind: str,
    message: str,
    *,
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
        return EXIT_BY_KIND["ok" if self.valid else "config"]


class InvalidExperimentError(ConfigError):
    """An experiment that cannot be planned or registered. ``problems`` says why, in full."""

    def __init__(self, message: str, problems: tuple[Problem, ...], *, fix: str | None = None):
        super().__init__(message, fix=fix)
        self.problems = problems


def error_document(error: IxError) -> dict[str, Any]:
    """The ``ix.v1.error`` envelope: the one shape a failing command prints.

    ``problems`` and ``deviations`` are always present and empty when the error has none.
    """
    return {
        "schema": schema.ERROR,
        "error": {
            "kind": error.kind,
            "message": str(error),
            "fix": error.fix,
            "problems": [item.document() for item in error.problems],
            "deviations": [item.document() for item in error.deviations],
        },
    }


# --- pydantic errors as problems ---------------------------------------------------------

#: Tags of the ``probes`` union, which pydantic puts in an error's location and no author
#: wrote. Angle brackets keep them apart from any key a file can hold.
UNION_TAGS = frozenset({"<inline>", "<from-source>"})

_QUOTED = re.compile(r"'([^']*)'")
_NAME_PATTERN = "^[A-Za-z0-9][A-Za-z0-9_.-]*$"


def _path(loc: tuple[Any, ...], prefix: str) -> str:
    parts = [str(part) for part in loc if part not in UNION_TAGS]
    return ".".join(([prefix] if prefix else []) + parts)


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
                "unknown_key",
                f"{key!r} is not a key ix knows here",
                fix=f"remove {key!r} or correct its spelling; `ix --skill` lists the keys",
                source=source,
            )
        elif item["type"] == "missing":
            found = problem(
                path, "missing_key", f"{key!r} is required", fix=f"add {key!r}", source=source
            )
        elif item["type"] == "literal_error" and key == "schema":
            allowed = _QUOTED.findall(str(context.get("expected", "")))
            found = problem(
                path,
                "schema_unsupported",
                f"this ix does not read schema {got!r} here",
                fix=f"schema: {allowed[0]}, or leave 'schema' out" if allowed else None,
                allowed=allowed,
                got=got,
                source=source,
            )
        elif item["type"] == "literal_error":
            allowed = _QUOTED.findall(str(context.get("expected", "")))
            found = problem(
                path,
                "invalid_value",
                f"{key!r} is one of {', '.join(allowed)}; got {got!r}",
                allowed=allowed,
                got=got,
                source=source,
            )
        elif item["type"] == "string_pattern_mismatch" and context.get("pattern") == _NAME_PATTERN:
            found = problem(
                path,
                "invalid_value",
                f"{got!r} is not a name: a name is one token of letters, digits, '_', '.' "
                "and '-', starting with a letter or digit",
                fix=f"rename it to one token, for example {_token(got)!r}",
                got=got,
                source=source,
            )
        else:
            message = item["msg"].removeprefix("Value error, ")
            found = problem(
                path,
                "invalid_value",
                message,
                fix=f"give {path or 'the file'} a value that fits: {message}",
                got=got,
                source=source,
            )
        problems.append(found)
    return problems


def _token(value: Any) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip("-.")
    return token or "name"

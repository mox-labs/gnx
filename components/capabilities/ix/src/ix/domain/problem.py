"""A problem: one thing wrong with an experiment or a genre file, in a form an agent can fix.

``validate`` never stops at the first problem. It returns every one it can find, each with
the ``path`` of the offending key, a ``kind`` from the vocabulary below, a ``message`` that
names what is wrong and the legal values, and a ``fix`` that says what to write instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from ix.domain import schema
from ix.domain.errors import ConfigError
from ix.domain.schema import Record

if TYPE_CHECKING:
    from pydantic import ValidationError

#: Every kind a problem or a warning can have. A new kind is added here first.
PROBLEM_KINDS: frozenset[str] = frozenset(
    {
        # the file as a document
        "yaml_syntax",
        "earlier_format",
        "unknown_key",
        "missing_key",
        "invalid_value",
        # the experiment against its genre
        "genre_unknown",
        "genre_subjects",
        "genre_requires",
        "genre_factors",
        "genre_min_repeats",
        "control_unknown",
        "control_not_applicable",
        "rule_not_allowed",
        "rule_requires_margin",
        # metrics and the decision
        "metric_unknown",
        "metric_direction",
        "statistic_illegal",
        "judged_gate",
        "unit_mismatch",
        # stimulus, bindings and spend
        "probes_empty",
        "probe_source",
        "probe_duplicate_id",
        "unbound_binding",
        "spend_over_ceiling",
        # genre files
        "genre_invalid",
        "genre_duplicate",
        "genre_extends_unknown",
        "genre_depth",
        "genre_narrowing",
        # warnings
        "subjects_differ_in",
        "genre_rejected",
    }
)


class Problem(Record):
    """One problem. ``path`` is dotted from the document root (``subjects.buffered.kind``).

    ``source`` names the file when it is not the experiment itself (a genre file).
    """

    path: str
    kind: str
    message: str
    fix: str
    source: str = ""


class Validation(Record):
    """What ``validate`` found: every problem and every warning, in one document."""

    schema_: Literal["ix.v2.validation"] = Field(default=schema.VALIDATION, alias="schema")
    experiment: str = ""
    source: str = ""
    genre: str = ""
    valid: bool
    problems: tuple[Problem, ...] = ()
    warnings: tuple[Problem, ...] = ()


class InvalidExperimentError(ConfigError):
    """An experiment that cannot be planned or registered. ``problems`` says why, in full."""

    def __init__(self, message: str, problems: tuple[Problem, ...], *, fix: str | None = None):
        super().__init__(message, fix=fix)
        self.problems = problems


#: Tags of the ``probes`` union, which pydantic puts in an error's location and a file's
#: author never wrote.
_UNION_TAGS = frozenset({"inline", "source"})


def _path(loc: tuple[Any, ...], prefix: str) -> str:
    parts = [str(part) for part in loc if part not in _UNION_TAGS]
    return ".".join(([prefix] if prefix else []) + parts)


def from_validation_error(
    error: ValidationError, *, prefix: str = "", source: str = ""
) -> list[Problem]:
    """Each pydantic error as a :class:`Problem` with a path and a fix."""
    problems: list[Problem] = []
    for item in error.errors():
        path = _path(item["loc"], prefix)
        key = str(item["loc"][-1]) if item["loc"] else ""
        if item["type"] == "extra_forbidden":
            problem = Problem(
                path=path,
                kind="unknown_key",
                message=f"{key!r} is not a key ix knows here",
                fix=f"remove {key!r} or correct its spelling; `ix --skill` lists the keys",
                source=source,
            )
        elif item["type"] == "missing":
            problem = Problem(
                path=path,
                kind="missing_key",
                message=f"{key!r} is required",
                fix=f"add {key!r}",
                source=source,
            )
        else:
            problem = Problem(
                path=path,
                kind="invalid_value",
                message=item["msg"].removeprefix("Value error, "),
                fix=f"correct the value at {path!r}",
                source=source,
            )
        problems.append(problem)
    return problems

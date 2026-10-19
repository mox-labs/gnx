"""A problem: one thing wrong with a document, in a form an agent can act on without parsing
prose.

The record and its kinds are agreed across the capability tools in
``components/capabilities/error-envelope.json``. ``path`` is the offending key, dotted from
the document's root; ``kind`` comes from :data:`PROBLEM_KINDS` or is one of matrix's own;
``allowed`` lists the legal values where the set is closed; ``got`` is what was written, when
it is a scalar; ``source`` is the file the problem is in. Errors carry problems
(:attr:`~matrix.domain.errors.MatrixError.problems`), and the documents the ``matrix``
command prints carry warnings in the same shape.

The kinds are an open set: a consumer tolerates a kind it does not know.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pydantic import ValidationError

#: The shared kinds, and what each means.
PROBLEM_KINDS: dict[str, str] = {
    "yaml_syntax": "the file is not valid YAML or frontmatter",
    "schema_unsupported": "the document names a schema this tool does not read",
    "wrong_shape": "a value has the wrong type or structure",
    "unknown_key": "a key the schema does not have",
    "missing_key": "a required key is absent",
    "invalid_value": "a value of the right type that the schema refuses",
    "name_unknown": "a name or type URL that is not registered or defined",
    "name_taken": "a name defined twice",
}

#: matrix's own kinds.
MATRIX_PROBLEM_KINDS: dict[str, str] = {
    "unprovided": "a component requires a kind that nothing provides and no input supplies",
    "cycle": "components wait on each other in a cycle",
    "extra_missing": "something selected needs an optional dependency that is not installed",
    "permissive": "a runtime grants an agent tools without per-call confirmation",
}


class Problem(BaseModel):
    """One problem, as ``error.problems[]`` and ``warnings[]`` hold it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    kind: str
    message: str
    fix: str = ""
    allowed: tuple[str, ...] = ()
    got: Any = None
    source: str = ""

    def document(self) -> dict[str, Any]:
        """The record as JSON data: every key always present."""
        data: dict[str, Any] = self.model_dump(mode="json")
        return data

    def at(self, prefix: str, *, source: str | None = None) -> Problem:
        """This problem with ``prefix`` put before its path (and ``source`` set, when given):
        how a section's problem becomes a problem of the document that holds it."""
        path = f"{prefix}.{self.path}" if prefix and self.path else prefix or self.path
        update: dict[str, Any] = {"path": path}
        if source is not None and not self.source:
            update["source"] = source
        return self.model_copy(update=update)


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
    legal = tuple(str(item) for item in allowed)
    if fix is None:
        key = path.rsplit(".", 1)[-1] or "the value"
        fix = (
            f"{key}: one of {', '.join(legal)}"
            if legal
            else f"correct the value at {path or 'the top of the document'}"
        )
    return Problem(
        path=path,
        kind=kind,
        message=message,
        fix=fix,
        allowed=legal,
        got=_scalar(got),
        source=source,
    )


def sourced(problems: Sequence[Problem], origins: Mapping[str, str]) -> list[Problem]:
    """``problems``, each without a ``source`` given the file that set the longest prefix of
    its path in ``origins`` (dotted key path -> file), as a merged config records them."""
    found: list[Problem] = []
    for item in problems:
        source = item.source
        if not source and origins:
            parts = item.path.split(".")
            for end in range(len(parts), 0, -1):
                source = origins.get(".".join(parts[:end]), "")
                if source:
                    break
        found.append(item if source == item.source else item.model_copy(update={"source": source}))
    return found


def summary(problems: Sequence[Problem], *, limit: int = 8) -> str:
    """The problems as indented lines for a human, ``path: message`` each."""
    lines = [f"  {p.path or '(document)'}: {p.message}" for p in problems[:limit]]
    if len(problems) > limit:
        lines.append(f"  ... and {len(problems) - limit} more")
    return "\n".join(lines)


def counted(problems: Sequence[Problem]) -> str:
    """``1 problem`` or ``N problems``."""
    return f"{len(problems)} problem{'' if len(problems) == 1 else 's'}"


# --- pydantic errors as problems ---------------------------------------------------------

_QUOTED = re.compile(r"'([^']*)'")
#: pydantic error types that say the value is the wrong type or structure.
_SHAPE = re.compile(
    r"^(dict|list|tuple|set|frozenset|model|model_attributes|string|int|float|bool|"
    r"none_required|is_instance|dataclass|arguments)_type$|^(int|float|bool)_parsing$"
)


def _scalar(value: Any) -> Any:
    return value if isinstance(value, str | int | float | bool) or value is None else None


def _path(loc: tuple[Any, ...], prefix: str) -> str:
    return ".".join([prefix, *map(str, loc)] if prefix else map(str, loc))


def from_validation_error(
    error: ValidationError,
    *,
    prefix: str = "",
    source: str | None = None,
    sources: Callable[[tuple[str, ...]], str] | None = None,
) -> list[Problem]:
    """Each pydantic error in ``error`` as a :class:`Problem` with a path, a kind, the legal
    set where there is one, and a fix.

    ``prefix`` goes before every path (the section the model was validated from). The source
    of each problem is ``source``, or ``sources(keys)`` when a callable is given: the file
    that set the value at that key path, for a document merged from several.
    """
    found: list[Problem] = []
    for item in error.errors():
        loc = tuple(item["loc"])
        path = _path(loc, prefix)
        key = str(loc[-1]) if loc else ""
        got = item.get("input")
        context = item.get("ctx") or {}
        kind, fix, allowed = "invalid_value", None, ()
        message = str(item["msg"]).removeprefix("Value error, ")
        if item["type"] == "extra_forbidden":
            kind, message = "unknown_key", f"{key!r} is not a key here"
            fix, got = f"remove {key!r} or correct its spelling", None
        elif item["type"] == "missing":
            kind, message, fix, got = "missing_key", f"{key!r} is required", f"add {key!r}", None
        elif item["type"] == "literal_error":
            allowed = tuple(_QUOTED.findall(str(context.get("expected", ""))))
            kind = "schema_unsupported" if key == "schema" else "invalid_value"
            message = f"{key!r} is one of {', '.join(allowed)}; got {got!r}"
        elif _SHAPE.match(str(item["type"])):
            kind = "wrong_shape"
            fix = f"give {path or 'the document'} a value that fits: {message}"
        else:
            fix = f"give {path or 'the document'} a value that fits: {message}"
        keys = (*([prefix] if prefix else []), *map(str, loc))
        where = sources(keys) if sources is not None else source
        found.append(
            problem(path, kind, message, fix=fix, allowed=allowed, got=got, source=where or "")
        )
    return found

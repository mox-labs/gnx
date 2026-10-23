"""A step's text template: ``{placeholder}``s that its ``inputs`` fill, checked when the step
is built.

An agent step's ``task`` and a model step's ``prompt`` are both one of these. Each placeholder
must name an input, and each input must have a placeholder, so a typo fails at composition
with every other problem rather than when a run reaches the step. ``{{`` and ``}}`` are
literal braces; a placeholder may index or take an attribute of its input (``{diff[0]}``),
as :meth:`str.format` allows.

:func:`render` fills one from the rows a step was handed, and :func:`json_document` reads a
reply that must be one JSON document, which both steps' ``output`` asks for.
"""

from __future__ import annotations

import json
import re
import string
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from matrix.domain.errors import ConfigError, ContractError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

_FIX = "use {placeholder} for each input, by its name under `inputs`, and {{ }} for literal braces"


def check_template(template: str, inputs: Iterable[str], *, where: str) -> None:
    """Raise ConfigError unless every placeholder of ``template`` names one of ``inputs`` and
    every input has one. ``where`` says whose template it is, for the message."""
    try:
        fields = [
            field for _, field, _, _ in string.Formatter().parse(template) if field is not None
        ]
    except ValueError as e:
        raise ConfigError(f"{where}: the template is invalid: {e}", fix=_FIX) from e
    names: set[str] = set()
    for field in fields:
        name = field.split(".", 1)[0].split("[", 1)[0]
        if not name or name.isdigit():
            raise ConfigError(
                f"{where}: the template has a positional placeholder {{{field}}}, which no "
                "input fills",
                fix=_FIX,
            )
        names.add(name)
    given = set(inputs)
    unknown, unused = sorted(names - given), sorted(given - names)
    if unknown:
        raise ConfigError(
            f"{where}: the template uses {', '.join(map(repr, unknown))}, which "
            f"{'is not an input' if len(unknown) == 1 else 'are not inputs'}. Inputs: "
            f"{', '.join(sorted(given)) or '(none)'}",
            fix=_FIX,
        )
    if unused:
        raise ConfigError(
            f"{where}: input{'s' if len(unused) > 1 else ''} {', '.join(map(repr, unused))} "
            f"{'are' if len(unused) > 1 else 'is'} never used by the template",
            fix="add a {placeholder} for each input, or drop the inputs the template does not use",
        )


def render(
    template: str | None,
    inputs: Mapping[str, str],
    rows: Mapping[str, tuple[Any, ...]],
    *,
    where: str,
) -> str:
    """The text for these ``rows``: each placeholder of ``template`` filled with the rows of
    the kind ``inputs`` maps it to, one row as itself, several as a JSON list. Without a
    template, the single input's text is the whole text.

    Raises ContractError when a provider handed over no rows, and ConfigError when an index
    or attribute the template asks of a row is not there."""
    values: dict[str, str] = {}
    for placeholder, kind in inputs.items():
        found = rows[kind]
        if not found:
            raise ContractError(
                f"{where} requires {kind!r}, but its provider handed over no rows", table=kind
            )
        values[placeholder] = as_text(found[0] if len(found) == 1 else list(found))
    if template is None:
        return next(iter(values.values()))
    try:
        return template.format_map(values)
    except (KeyError, IndexError, AttributeError, ValueError) as e:  # what an index asked
        raise ConfigError(
            f"{where}: the template cannot be filled: {e}",
            fix="index or take an attribute of an input only where its rows have one",
        ) from e


def as_text(value: Any) -> str:
    """A row as a template fills it in: text as it stands, anything else as JSON."""
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump_json(indent=2)
    if isinstance(value, list):
        value = [v.model_dump(mode="json") if isinstance(v, BaseModel) else v for v in value]
    return json.dumps(value, indent=2, default=str)


_FENCED = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)


def json_document(content: str) -> Any:
    """The one JSON document ``content`` holds, alone or in a fenced ```` ```json ```` block.
    Raises LookupError when it holds none."""
    for candidate in (content.strip(), *(m.group(1) for m in _FENCED.finditer(content))):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
    raise LookupError("no JSON document")

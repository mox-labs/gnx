"""A step's text template: ``{placeholder}``s that its ``inputs`` fill, checked when the step
is built.

An agent step's ``task`` and a model step's ``prompt`` are both one of these. Each placeholder
must name an input, and each input must have a placeholder, so a typo fails at composition
with every other problem rather than when a run reaches the step. ``{{`` and ``}}`` are
literal braces; a placeholder may index or take an attribute of its input (``{diff[0]}``),
as :meth:`str.format` allows.
"""

from __future__ import annotations

import string
from typing import TYPE_CHECKING

from matrix.domain.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Iterable

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

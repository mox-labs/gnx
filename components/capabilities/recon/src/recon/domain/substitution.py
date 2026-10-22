"""``{placeholder}`` substitution — filling config templates from variables. Pure.

A placeholder is ``{name}``, the name a letter or ``_`` and then letters, digits or ``_``:
``{3}`` and ``{2,5}``, a regular expression's counts, are not placeholders. ``{{`` and ``}}``
are a literal ``{`` and ``}``, as in Python's format strings, so a literal ``{word}`` can be
written (a compact GraphQL query, ``{viewer{{login}}}``). A shell command is the exception:
its braces are its own (``--format '{{.Name}}'``), so it is filled without escapes.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

_PLACEHOLDER = re.compile(r"\{([A-Za-z_]\w*)\}")

#: A placeholder, or an escaped brace (no group): scanned left to right, so ``{{x}}`` is two
#: escapes around ``x``, never a placeholder.
_WITH_ESCAPES = re.compile(r"\{\{|\}\}|\{([A-Za-z_]\w*)\}")


def substitute(value: Any, variables: Mapping[str, str], *, escapes: bool = True) -> Any:
    """Apply `{key}` substitution to string values, recursing into dicts and
    lists. Non-string scalars (ints, bools, None) pass through unchanged.

    A key missing from ``variables`` is left as the literal ``{key}``; ``find_unresolved``
    reports what is left, from the template. With ``escapes``, ``{{`` and ``}}`` become one
    brace; without, every brace but a placeholder's is passed as written.
    """
    if isinstance(value, str):

        def replacer(match: re.Match[str]) -> str:
            name = match.group(1)
            if name is None:
                return match.group(0)[0]
            return str(variables.get(name, match.group(0)))

        return (_WITH_ESCAPES if escapes else _PLACEHOLDER).sub(replacer, value)
    if isinstance(value, dict):
        return {k: substitute(v, variables, escapes=escapes) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, variables, escapes=escapes) for v in value]
    return value


def find_unresolved(value: Any, variables: Mapping[str, str] | None = None) -> list[str]:
    """Every `{placeholder}` in the string values of the template ``value`` that
    ``variables`` does not fill. An escaped brace is none. A variable whose value holds a
    placeholder itself (a template's ``owner: "{owner}"``, left to edit) fills nothing: the
    placeholder in its value is reported. Empty means all resolved."""
    filled = variables or {}
    unresolved: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, str):
            for match in _WITH_ESCAPES.finditer(v):
                name = match.group(1)
                if name is None:
                    continue
                if name in filled:
                    unresolved.extend(_PLACEHOLDER.findall(str(filled[name])))
                else:
                    unresolved.append(name)
        elif isinstance(v, dict):
            for item in v.values():
                walk(item)
        elif isinstance(v, list):
            for item in v:
                walk(item)

    walk(value)
    return unresolved

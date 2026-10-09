"""``{placeholder}`` substitution — filling config templates from variables. Pure."""

from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def substitute(value: Any, variables: dict[str, str]) -> Any:
    """Apply `{key}` substitution to string values, recursing into dicts and
    lists. Non-string scalars (ints, bools, None) pass through unchanged.

    A key missing from ``variables`` is left as the literal ``{key}``; ``find_unresolved``
    reports what is left.
    """
    if isinstance(value, str):

        def replacer(match: re.Match[str]) -> str:
            return str(variables.get(match.group(1), match.group(0)))

        return _PLACEHOLDER.sub(replacer, value)
    if isinstance(value, dict):
        return {k: substitute(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, variables) for v in value]
    return value


def find_unresolved(value: Any) -> list[str]:
    """Every `{placeholder}` still present in string values. Empty means all resolved."""
    unresolved: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, str):
            unresolved.extend(_PLACEHOLDER.findall(v))
        elif isinstance(v, dict):
            for item in v.values():
                walk(item)
        elif isinstance(v, list):
            for item in v:
                walk(item)

    walk(value)
    return unresolved

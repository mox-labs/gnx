"""Pulling a JSON object out of model text. Pure string work, no I/O.

Models wrap JSON in markdown fences, lead with a sentence, or trail an explanation, and the
prose may hold brackets of its own ("see [1]", "the {name} field"). So every balanced object
or array that parses as JSON is a candidate, in the order it appears, and the runtime tries
each against the schema. Nothing here repairs JSON. Repair belongs to the retry, where the
model is shown what failed.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n?```", re.DOTALL)


def _template(schema: dict[str, Any]) -> str | None:
    """A fill-in-the-values skeleton for a top-level object schema, or None.

    Small models shown a raw JSON Schema tend to echo the schema back (observed live with
    Llama-3.2-3B: two attempts, both returned a schema-shaped object). A skeleton of the
    fields with their types reads as "fill this in", which they follow.
    """
    props = schema.get("properties")
    if schema.get("type") != "object" or not isinstance(props, dict) or not props:
        return None
    fields = []
    for name, prop in props.items():
        kind = prop.get("type") if isinstance(prop, dict) else None
        if isinstance(kind, list):
            kind = " | ".join(str(k) for k in kind)
        fields.append(f'"{name}": <{kind or "value"}>')
    return "{" + ", ".join(fields) + "}"


def schema_instruction(schema: dict[str, Any]) -> str:
    template = _template(schema)
    lines = ["Answer with only a JSON value — no prose, no markdown fences."]
    if template:
        lines.append(f"Fill in this shape with your answer: {template}")
    lines.append(
        "It must validate against this JSON Schema. The schema describes the shape; "
        "do not repeat it in your answer:\n" + json.dumps(schema, sort_keys=True)
    )
    return "\n\n".join(lines)


def retry_instruction(validation: str) -> str:
    return (
        "That response did not validate against the schema:\n"
        f"{validation}\n\nRespond again with only the corrected JSON value."
    )


def json_candidates(text: str) -> list[str]:
    """Every balanced object or array in ``text`` that parses as JSON, in order of opening.

    A fenced block, when there is one, is the only place looked. A candidate nested inside
    another candidate is not listed again; one nested inside bracketed prose is.
    """
    stripped = text.strip()
    fenced = _FENCE.search(stripped)
    if fenced:
        stripped = fenced.group(1).strip()
    found: list[str] = []
    covered = 0  # the end of the last candidate found; openers before it are inside it
    for start, ch in enumerate(stripped):
        if start < covered or ch not in _CLOSERS:
            continue
        end = _balanced_end(stripped, start)
        if end is None:
            continue
        candidate = stripped[start : end + 1]
        try:
            json.loads(candidate)
        except ValueError:
            continue
        found.append(candidate)
        covered = end + 1
    return found


def extract_json(text: str) -> str:
    """The first JSON value in ``text`` (see ``json_candidates``). ValueError when there is none."""
    candidates = json_candidates(text)
    if not candidates:
        raise ValueError("no JSON object or array found in the response")
    return candidates[0]


_CLOSERS = {"{": "}", "[": "]"}


def _balanced_end(text: str, start: int) -> int | None:
    """The index closing the bracket opened at ``start``, or None when it never closes."""
    opener, closer = text[start], _CLOSERS[text[start]]
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i
    return None

"""Pulling a JSON object out of model text. Pure string work, no I/O.

Models wrap JSON in markdown fences, lead with a sentence, or trail an explanation. The
extractor accepts the first balanced top-level object or array and nothing else; it does
not repair JSON. Repair belongs to the retry, where the model is shown what failed.
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


def extract_json(text: str) -> str:
    """Return the JSON substring of ``text``. Raises ValueError when there is none."""
    stripped = text.strip()
    fenced = _FENCE.search(stripped)
    if fenced:
        stripped = fenced.group(1).strip()
    # Whichever bracket opens first is the value: '[{"a": 1}]' is an array, not an object.
    candidates = [(stripped.find(o), o, c) for o, c in (("{", "}"), ("[", "]"))]
    for start, opener, closer in sorted(c for c in candidates if c[0] != -1):
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(stripped)):
            ch = stripped[i]
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
                    return stripped[start : i + 1]
    raise ValueError("no JSON object or array found in the response")

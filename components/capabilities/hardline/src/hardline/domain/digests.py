"""Content digests for the record a call carries. Pure: hashlib and json, no I/O.

A digest is ``sha256:`` and the hex SHA-256 of a canonical JSON encoding (UTF-8, no
whitespace, keys sorted), so a reader can recompute one from what it names without hardline.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hardline.domain.types import Message


def digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def turns_digest(turns: Sequence[Message]) -> str:
    """The digest of ``[[role, content], ...]``, in the order the turns were sent.

    A turn with tool parts has a third element, an object holding those it has:
    ``tool_calls`` as ``[[id, name, arguments], ...]``, ``tool_call_id``, ``is_error``. A turn
    without them is ``[role, content]`` as before, so their digests are unchanged.
    """
    return digest([_turn(turn) for turn in turns])


def _turn(turn: Message) -> list[Any]:
    tools: dict[str, Any] = {}
    if turn.tool_calls:
        tools["tool_calls"] = [[call.id, call.name, call.arguments] for call in turn.tool_calls]
    if turn.tool_call_id is not None:
        tools["tool_call_id"] = turn.tool_call_id
    if turn.is_error:
        tools["is_error"] = True
    return [turn.role, turn.content, tools] if tools else [turn.role, turn.content]

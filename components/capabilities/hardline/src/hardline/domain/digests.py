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
    """The digest of ``[[role, content], ...]``, in the order the turns were sent."""
    return digest([[turn.role, turn.content] for turn in turns])

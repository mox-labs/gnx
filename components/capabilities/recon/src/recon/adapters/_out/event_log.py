"""The event log: a survey's events as ``recon.v1.event`` lines in its archive.

Each event is appended and closed as it happens, so a survey that is killed leaves every
event up to the moment it stopped, a start with no end marking where.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from recon.domain.telemetry import Event


class EventLog:
    def __init__(self, path: Path) -> None:
        self._path = path

    def on_event(self, event: Event) -> None:
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.document(), default=str) + "\n")

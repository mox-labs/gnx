"""The event log: a survey's events as ``recon.v1.event`` lines in its archive.

The file is opened once, at the first event, and each event is appended to it in one
``write`` as it happens, so a reader sees each event, whole, as soon as it is emitted, and a
survey that is killed leaves every event up to the moment it stopped, a start with no end
marking where. ``close`` closes the file at the operation's end.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from recon.domain.telemetry import Event


class EventLog:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._descriptor: int | None = None

    def on_event(self, event: Event) -> None:
        line = (json.dumps(event.document(), default=str) + "\n").encode()
        if self._descriptor is None:
            self._descriptor = os.open(self._path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        written = os.write(self._descriptor, line)
        if written != len(line):
            raise OSError(f"wrote {written} of {len(line)} bytes of an event")

    def close(self) -> None:
        if self._descriptor is not None:
            descriptor, self._descriptor = self._descriptor, None
            os.close(descriptor)

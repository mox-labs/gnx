"""One JSON line per event, to a file or to stderr: the stream an agent tails.

Each line is an event document (``schema: matrix.v1.event``; see
:meth:`matrix.domain.observer.Event.document`). A run's first line is its
``matrix.run.start`` and its last its ``matrix.run.end``; a failure shows as a
``matrix.component.end`` with ``status: failed`` before that. Lines are flushed as they are
written, so the file can be followed while the run is going.

The stream is telemetry: under load events may be missing, and a ``matrix.events.dropped``
line says how many. Results are read from the run, never from here.

Config: ``path`` (a file, appended to; omitted or ``-`` for stderr).

Type URL: ``matrix.v1.observer.jsonl``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from matrix.domain.observer import Event


class JsonlObserverConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Where the lines go. ``None`` or ``-``: stderr.
    path: str | None = None


class JsonlObserver:
    def __init__(self, path: Path | str | None = None, *, stream: TextIO | None = None) -> None:
        self._owned = False
        if stream is not None:
            self._stream = stream
        elif path is None or str(path) == "-":
            self._stream = sys.stderr
        else:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._stream = target.open("a", encoding="utf-8")
            self._owned = True

    def on_event(self, event: Event) -> None:
        self._stream.write(json.dumps(event.document(), default=str) + "\n")
        self._stream.flush()

    def close(self) -> None:
        """Close the file this observer opened. A stream it was given is left alone."""
        if self._owned:
            self._stream.close()
            self._owned = False

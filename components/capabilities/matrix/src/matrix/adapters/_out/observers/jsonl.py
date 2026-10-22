"""One JSON line per event, to a file or to stderr: the stream an agent tails.

Each line is an event document (``schema: matrix.v1.event``; see
:meth:`matrix.domain.observer.Event.document`). A run's first line is its
``matrix.run.start`` and its last its ``matrix.run.end``; a failure shows as a
``matrix.component.end`` with ``status: failed`` before that. Lines are flushed as they are
written, so the file can be followed while the run is going.

The stream is telemetry: under load events may be missing, and a ``matrix.events.dropped``
line says how many. Results are read from the run, never from here.

A file is opened, its folders made, when the first event arrives, never when the observer is
built (so ``matrix check`` and ``matrix plan``, which call nothing, touch nothing), and closed
when a run's ``matrix.run.end`` has been written; the next event opens it again, for
appending.

Config: ``path`` (a file, appended to; omitted or ``-`` for stderr).

Type URL: ``matrix.v1.observer.jsonl``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from pydantic import BaseModel, ConfigDict

from matrix.domain.observer import RUN_END

if TYPE_CHECKING:
    from matrix.domain.observer import Event


class JsonlObserverConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Where the lines go. ``None`` or ``-``: stderr.
    path: str | None = None


class JsonlObserver:
    def __init__(self, path: Path | str | None = None, *, stream: TextIO | None = None) -> None:
        #: The file to append to; None when writing to a stream it was given, or stderr.
        self._path = None if stream is not None or path is None or str(path) == "-" else Path(path)
        self._given = stream
        #: The file this observer opened, while it is open.
        self._file: TextIO | None = None

    @property
    def closed(self) -> bool:
        """Whether this observer holds no file open."""
        return self._file is None

    def _stream(self) -> TextIO:
        if self._path is None:
            return self._given if self._given is not None else sys.stderr
        if self._file is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self._path.open("a", encoding="utf-8")
        return self._file

    def on_event(self, event: Event) -> None:
        stream = self._stream()
        stream.write(json.dumps(event.document(), default=str) + "\n")
        stream.flush()
        if event.name == RUN_END:
            self.close()

    def close(self) -> None:
        """Close the file this observer opened; the next event opens it again. A stream it
        was given is left alone."""
        if self._file is not None:
            self._file.close()
            self._file = None

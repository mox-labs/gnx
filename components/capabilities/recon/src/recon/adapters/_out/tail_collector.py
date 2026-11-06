"""TailCollector: a file followed as it grows, each line a record, for as long as a stream runs.

A ``tail`` collector reads the file a ``file`` source names (its ``url``, relative to the
project root), from its start (``start: beginning``) or from its end (``start: end``, the
default, as ``tail -f`` does), and then each line written to it, waiting ``interval``
seconds on the clock port between two reads that found nothing new. A line that is a JSON
object is that record; any other line is ``{"line": <its text>}``, and so is a JSON line that
holds ``_position`` itself, so a record's position is always recon's.

Each record carries its position (``Record.position``, kept as ``_position``): ``{"inode",
"offset"}``, the file's inode and the byte offset just after the line. A stream resumed after a
position goes on from that offset of that file, so each line is collected once. When the file
at the path is a different one (rotated: renamed away, and a new one made in its place), the
old one is read to its end and the new one from its start, so nothing is lost. When the file is
shorter than what was read (truncated in place, as ``copytruncate`` does), or a resumed
position is not in the file now there, it is read from its start, and the stretch whose lines
may be lost is given as a :class:`~recon.core.domain.stream.Gap`. A line longer than the
source's ``max_bytes`` fails the stream, rather than being held in memory.

The built-in collector type ``tail``, with the source kind ``file``: its records are
``unbounded_recon_paced`` (they do not end, and it reads the next line when recon is ready, so
a full buffer holds it back and loses nothing).
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Literal

from pydantic import BaseModel, ConfigDict, Field

from recon.core.domain.clock import LONGEST_WAIT
from recon.core.domain.collector import PlainKind, Record, source_needed
from recon.core.domain.errors import CollectionError
from recon.core.domain.stream import POSITION_FIELD, Gap

if TYPE_CHECKING:
    from collections.abc import Iterator

    from recon.core.domain.clock import Clock
    from recon.core.domain.collector import Effect, Intent, Item, Position, RecordsKind, Run
    from recon.core.domain.errors import Problem

_READ = 65536


class TailSettings(BaseModel):
    """A ``tail`` collector's own keys: ``start``, where a stream begins the file when it has
    no position to go on from (``end``, the default, or ``beginning``), and ``interval``, the
    seconds between two reads once the file has nothing new (1, as ``recon watch``'s)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: Literal["end", "beginning"] = "end"
    interval: float = Field(default=1.0, gt=0, le=LONGEST_WAIT, allow_inf_nan=False)


class TailCollector:
    """Follows a file. ``close()``, from any thread, ends the following at its next wait."""

    effects: frozenset[Effect] | None = frozenset({"filesystem"})
    records: RecordsKind = "unbounded_recon_paced"
    accepts: frozenset[str] | None = frozenset({"file"})

    def __init__(self, *, clock: Clock) -> None:
        self._clock = clock
        self._closed = threading.Event()

    def describe(self, run: Run) -> Intent:
        settings = run.settings_as(TailSettings)
        return {
            "path": run.source.url if run.source is not None else None,
            "start": settings.start,
            "interval": settings.interval,
        }

    def check(self, run: Run) -> list[Problem]:
        return source_needed(run, "tail")

    def close(self) -> None:
        self._closed.set()

    def collect(self, run: Run, resume: Position | None) -> Iterator[Item]:
        source = run.source
        assert source is not None  # the plan holds it: check needs a source
        settings = run.settings_as(TailSettings)
        follow = _Follow(Path(source.url), source.max_bytes, self._clock)
        self._closed.clear()
        try:
            follow.begin(None if resume is None else resume.value, settings.start)
            yield from follow.taken()
            while not self._closed.is_set():
                read = follow.lines()
                yield from follow.taken()
                if read:
                    yield from read
                elif follow.moved():
                    yield from follow.taken()
                    continue
                else:
                    self._clock.sleep(settings.interval)
        finally:
            follow.close()


class _Follow:
    """The file being followed: its handle, its inode, the offset read to, and the part of a
    line read so far."""

    def __init__(self, path: Path, max_bytes: int, clock: Clock) -> None:
        self._path = path
        self._max_bytes = max_bytes
        self._clock = clock
        #: The gaps found since the collector last took them.
        self._found: list[Gap] = []
        self._file: BinaryIO | None = None
        self._inode = 0
        self._offset = 0
        self._partial = b""
        self._read_at = clock.now()

    def begin(self, after: Any, start: str) -> None:
        """Open the file where a stream begins it: after the position ``after`` when the file
        there still holds it, else (a gap) at its start; with no position, at its end or its
        start as ``start`` says."""
        if not self._open():
            return  # not there yet: read from its start once it is
        if isinstance(after, dict) and {"inode", "offset"} <= set(after):
            size = os.fstat(self._handle().fileno()).st_size
            if (
                after["inode"] == self._inode
                and isinstance(after["offset"], int)
                and (0 <= after["offset"] <= size)
            ):
                self._seek(after["offset"])
            else:
                self._gap(None)
        elif start == "end":
            self._seek(os.fstat(self._handle().fileno()).st_size)

    def taken(self) -> list[Gap]:
        """The gaps found since last asked: a stretch whose lines may be lost."""
        found, self._found = self._found, []
        return found

    def lines(self) -> list[Record]:
        """The records of the whole lines written since the last read; none at its end."""
        if self._file is None and not self._open():
            return []
        data = self._handle().read(_READ)
        if data:
            self._read_at = self._clock.now()
            self._partial += data
        elif b"\n" not in self._partial:  # a rotated file's last line is ended by moved()
            return []
        *whole, self._partial = self._partial.split(b"\n")
        if len(self._partial) > self._max_bytes:
            raise CollectionError(
                f"{self._path} has a line longer than its source's max_bytes "
                f"({self._max_bytes:,} bytes); it was not kept",
                kind="run_failed",
                fix="raise max_bytes on the file source, if the lines are meant to be that long",
            )
        records = []
        for raw in whole:
            self._offset += len(raw) + 1
            record = _record(raw)
            if record is not None:
                records.append(Record(record, {"inode": self._inode, "offset": self._offset}))
        return records

    def moved(self) -> bool:
        """At the end of what was written: whether the file at the path changed under it (a
        new file, or this one cut short), in which case it is read again from the start."""
        try:
            now = os.stat(self._path)
        except FileNotFoundError:
            return False  # gone, and not yet made again: keep the one open
        if self._file is None:
            return self._open()
        if now.st_ino != self._inode:  # rotated: this one is read to its end
            if self._partial:  # its last line had no newline: it is whole now
                self._partial += b"\n"
                return True
            self.close()
            return self._open()
        if now.st_size < self._offset + len(self._partial):  # cut short in place
            self._gap(self._read_at)
            self._seek(0)
            return True
        return False

    def close(self) -> None:
        if self._file is not None:
            with contextlib.suppress(OSError):
                self._file.close()
            self._file = None

    def _open(self) -> bool:
        try:
            self._file = open(self._path, "rb")  # noqa: SIM115 - held while followed
        except FileNotFoundError:
            return False
        self._inode = os.fstat(self._file.fileno()).st_ino
        self._offset = 0
        self._partial = b""
        return True

    def _handle(self) -> BinaryIO:
        assert self._file is not None
        return self._file

    def _seek(self, offset: int) -> None:
        self._handle().seek(offset)
        self._offset = offset
        self._partial = b""

    def _gap(self, start: Any) -> None:
        self._found.append(Gap(start, self._clock.now(), resumed=False))


def _record(raw: bytes) -> dict[str, Any] | None:
    """A line's record: its JSON object, or its text; None for a blank line."""
    text = raw.removesuffix(b"\r").decode("utf-8", errors="replace")
    if not text.strip():
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict) and POSITION_FIELD not in parsed:
        return parsed
    return {"line": text}


class FileSource(PlainKind):
    """The ``file`` source kind: a file of this host (its ``url``, relative to the project
    root), followed as it grows; ``max_bytes`` bounds one line."""


def register(registry: Any) -> None:
    """The ``tail`` collector type and the ``file`` source kind."""
    registry.register(
        "recon.collector",
        "recon.v1.collector.tail",
        TailCollector,
        config=TailSettings,
        needs={"clock"},
        summary="a file followed as it grows → each line a record, positioned by inode and offset",
    )
    registry.register(
        "recon.source",
        "recon.v1.source.file",
        FileSource,
        summary="a file of this host, followed as it grows",
    )

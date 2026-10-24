"""TailCollector: a file followed as it grows, each line a record, for as long as a stream runs.

A ``tail`` collector reads the file a ``file`` source names (its ``url``, relative to the
project root), from its start (``start: beginning``) or from its end (``start: end``, the
default, as ``tail -f`` does), and then each line written to it, waiting ``interval``
seconds on the clock port between two reads that found nothing new. A line that is a JSON
object is that record; any other line is ``{"line": <its text>}``, and so is a JSON line that
holds ``_position`` itself, so a record's position is always recon's.

Each record carries its position as ``_position``: ``{"inode", "offset"}``, the file's inode
and the byte offset just after the line. A stream resumed after a position goes on from that
offset of that file, so each line is collected once. When the file at the path is a
different one (rotated: renamed away, and a new one made in its place), the old one is read
to its end and the new one from its start, so nothing is lost. When the file is shorter than
what was read (truncated in place, as ``copytruncate`` does), or a resumed position is not in
the file now there, it is read from its start, and the stretch whose lines may be lost is
reported as a gap (``CollectorContext.gaps``). A line longer than the source's ``max_bytes``
fails the stream, rather than being held in memory.

Registered as the built-in collector type ``tail`` (entry-point group ``recon.collectors``):
``unbounded`` (its records do not end) and ``paced_by: recon`` (it reads the next line when
recon is ready, so a full buffer holds it back and loses nothing).
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Literal

from pydantic import BaseModel, ConfigDict, Field

from recon.domain.clock import LONGEST_WAIT
from recon.domain.collector import CollectorType
from recon.domain.errors import CollectionError
from recon.domain.stream import POSITION_FIELD, Gap

if TYPE_CHECKING:
    from collections.abc import Iterator

    from recon.domain.capture import CaptureLog
    from recon.domain.clock import Clock
    from recon.domain.models import CollectorEntry, SourceEntry
    from recon.domain.stream import GapLog

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

    def __init__(self, clock: Clock | None, gaps: GapLog) -> None:
        if clock is None:
            raise CollectionError(
                "a tail collector waits on the survey's clock, and none was given",
                kind="config",
                fix="build it through recon's root, which hands every type the clock",
            )
        self._clock = clock
        self._gaps = gaps
        self._closed = threading.Event()

    def close(self) -> None:
        self._closed.set()

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
        after: Any = None,
    ) -> Iterator[dict[str, Any]]:
        assert source is not None  # the plan holds it: the type requires a source
        settings = TailSettings.model_validate(entry.settings)
        follow = _Follow(Path(source.url), source.max_bytes, self._clock, self._gaps)
        self._closed.clear()
        try:
            follow.begin(after, settings.start)
            while not self._closed.is_set():
                read = follow.lines()
                if read:
                    yield from read
                elif follow.moved():
                    continue
                else:
                    self._clock.sleep(settings.interval)
        finally:
            follow.close()


class _Follow:
    """The file being followed: its handle, its inode, the offset read to, and the part of a
    line read so far."""

    def __init__(self, path: Path, max_bytes: int, clock: Clock, gaps: GapLog) -> None:
        self._path = path
        self._max_bytes = max_bytes
        self._clock = clock
        self._gaps = gaps
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

    def lines(self) -> list[dict[str, Any]]:
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
                record[POSITION_FIELD] = {"inode": self._inode, "offset": self._offset}
                records.append(record)
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
        self._gaps.gap(Gap(start, self._clock.now(), resumed=False))


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


def _describe(entry: CollectorEntry, source: SourceEntry | None) -> dict[str, Any]:
    settings = TailSettings.model_validate(entry.settings)
    return {
        "path": source.url if source is not None else None,
        "start": settings.start,
        "interval": settings.interval,
    }


collector_type = CollectorType(
    create=lambda ctx: TailCollector(ctx.clock, ctx.gaps),
    effects=frozenset({"filesystem"}),
    summary="a file followed as it grows → each line a record, positioned by inode and offset",
    settings=TailSettings,
    source_kinds=frozenset({"file"}),
    accepts_kinds=frozenset({"file"}),
    requires_source=True,
    describe=_describe,
    unbounded=True,
    paced_by="recon",
)

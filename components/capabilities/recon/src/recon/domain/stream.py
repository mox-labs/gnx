"""Streams: a collector entry with ``window:`` is a stream, collected into a chain of windows.

A source that does not end (a file followed as it grows, a server that sends events) is
collected window by window. Each window is an ordinary archive (``meta.yaml``, its writer's
lock, its state, its one table, its query), sealed when the window ends, and chained to the one
before it through ``meta.yaml``'s ``stream``. A window ends when the first of its bounds is
reached (:class:`WindowSpec`): ``seconds`` from its open, half-open, ``[open, open + seconds)``;
or ``records`` records; or ``bytes`` bytes of them.

Which window an arrival falls in is a pure function of its order of arrival, its stamp on the
clock port and the window spec (:class:`Windowing`), so a window replays the same from the
arrivals it recorded, whatever clock replays it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from recon.domain.archives import ARCHIVE_ID_FORMAT
from recon.domain.clock import LONGEST_WAIT

#: Why a window ended, when it ended well: the bound of ``window:`` it reached, named by its
#: key (``seconds``, ``records``, ``bytes``), its source ended, the stream was stopped by a
#: signal, or it reached the stream's ``until``.
SealReason = Literal["seconds", "records", "bytes", "eof", "signal", "until"]

#: How a window was closed, as ``meta.yaml``'s ``stream.closed_by`` says it: a
#: :data:`SealReason`, or ``error`` for a window its source's fatal error ended, which keeps
#: the records it got.
ClosedBy = Literal["seconds", "records", "bytes", "eof", "signal", "until", "error"]

#: What a full buffer does with one more record (:class:`BufferSpec`). There is no ``fail``:
#: a burst would cost the whole stream.
Overflow = Literal["block", "drop_newest", "drop_oldest"]

#: Who sets the pace a type's records arrive at (``CollectorType.paced_by``): ``recon``, which
#: reads the next when it is ready for it (a file, a command, a polled API), or ``source``,
#: which sends whether recon is ready or not (server-sent events, a socket).
PacedBy = Literal["recon", "source"]

#: By default, the seconds a window of a type that does not end spans, when its entry sets no
#: ``window:``: windows of five minutes or more keep a query across them cheap.
DEFAULT_WINDOW_S: Final = 300.0

#: A window's id is its index, this many digits wide, then when it opened.
WINDOW_INDEX_DIGITS: Final = 8

_WINDOW_ID = re.compile(rf"^(\d{{{WINDOW_INDEX_DIGITS},}})-(.+)$")


class WindowSpec(BaseModel):
    """``window:`` on a collector entry: what ends each window, the first of these reached.
    ``seconds`` (a number of seconds, as ``deadline:`` is, at least 1: each window's end is an
    event in the buffer outside its bound, so the floor bounds those) from the window's open;
    ``records`` records; ``bytes`` bytes of records. At least one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seconds: float | None = Field(default=None, ge=1, allow_inf_nan=False)
    records: int | None = Field(default=None, ge=1)
    bytes: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _one_bound_the_clock_can_time(self) -> WindowSpec:
        if self.seconds is None and self.records is None and self.bytes is None:
            raise ValueError("a window needs at least one of seconds, records, bytes")
        if self.seconds is not None and self.seconds > LONGEST_WAIT:
            raise ValueError(
                f"{self.seconds:g}s is longer than this platform's timers take ({LONGEST_WAIT:g}s)"
            )
        return self


class BufferSpec(BaseModel):
    """``buffer:`` on a stream's entry: what may wait between the source and the window being
    written. At most ``records`` records and ``bytes`` bytes of them wait; one more is an
    overflow, which ``overflow`` settles: ``block`` waits for room (the source is read no
    faster than the windows are written), ``drop_newest`` drops the one arriving,
    ``drop_oldest`` the one waiting longest. A record of more than ``max_record_bytes`` bytes
    is dropped as it arrives. Every drop is counted in its window. ``overflow`` left out is
    :func:`default_overflow` of the collector type's ``paced_by``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    records: int = Field(default=10_000, ge=1)
    bytes: int = Field(default=16 * 1024 * 1024, ge=1)
    max_record_bytes: int = Field(default=1024 * 1024, ge=1)
    overflow: Overflow | None = None


class RetainSpec(BaseModel):
    """``retain:`` on a stream's entry, when it is not ``all``: which sealed windows to keep.
    ``windows``: the newest this many; ``seconds`` (a number of seconds): those closed within
    this long of now; ``bytes``: the newest whose sizes add up to no more than this. With more
    than one, a window is kept only while every rule keeps it. At least one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    windows: int | None = Field(default=None, ge=1)
    seconds: float | None = Field(default=None, ge=1, allow_inf_nan=False)
    bytes: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _one_rule(self) -> RetainSpec:
        if self.windows is None and self.seconds is None and self.bytes is None:
            raise ValueError("retain needs at least one of windows, seconds, bytes (or: all)")
        return self


#: ``retain:``: ``all`` keeps every window, which is the default, so nothing is removed unless
#: the mission says so; a :class:`RetainSpec` keeps the windows its rules keep.
Retain = Literal["all"] | RetainSpec


def default_overflow(paced_by: PacedBy) -> Overflow:
    """A full buffer's default: ``block`` for a source recon paces, which waits without loss;
    ``drop_newest`` for a source that paces itself, whose sender does not wait, so a block
    would only move the loss to it, out of sight."""
    return "block" if paced_by == "recon" else "drop_newest"


#: The field of a stream's record that holds where the record is in its source (a file's
#: inode and offset, a server-sent event's id): the record's own, set by the collector, kept
#: through ``normalize:``, and written with the record in one line, so a stream resumes from
#: the last whole line it wrote. A collector type whose records have a position sets it;
#: ``normalize:`` may not name a column so.
POSITION_FIELD: Final = "_position"


@dataclass(frozen=True)
class Gap:
    """A stretch when a stream's source was not read, so records it sent then may be missing:
    from ``start`` (None when not known, as after a crash) to ``end``. ``resumed``: the
    source was asked to go on from the last position kept, so nothing is missing if it did."""

    start: datetime | None
    end: datetime
    resumed: bool

    def document(self) -> dict[str, object]:
        return {
            "from": None if self.start is None else self.start.isoformat(),
            "to": self.end.isoformat(),
            "resumed": self.resumed,
        }


class GapLog(Protocol):
    """Where a collector of a stream says what of its source it could not give: a stretch it
    was not read (a dropped connection, a file truncated under it: :meth:`gap`), or a record
    it dropped before handing it on (one larger than the stream's ``max_record_bytes``:
    :meth:`drop`). ``CollectorContext.gaps``; each is counted in the window being filled when
    it is said."""

    def gap(self, gap: Gap) -> None: ...

    def drop(self) -> None: ...


class NoGaps:
    """The :class:`GapLog` of a survey's snapshot tables, which keep no gaps."""

    def gap(self, gap: Gap) -> None:
        return None

    def drop(self) -> None:
        return None


class Stop(Protocol):
    """What a running stream reads of the requests to stop it (``recon.StreamStop`` is recon's
    own, asked from any thread or a signal handler). The stream's own thread waits on
    :meth:`next_ask`, acts on each ask (the first drains, a later one stops at once), says so
    with :meth:`acted`, and calls :meth:`end` when its run is over."""

    def next_ask(self) -> bool:
        """Wait for an ask the stream has not acted on yet; False once its run is over."""
        ...

    def acted(self) -> None: ...

    def end(self) -> None: ...


@dataclass(frozen=True)
class WindowEnd:
    """A window that ended: its index, why, and its span ``[opened, closed)`` on the clock's
    monotonic time. A window ended by its records or bytes closes at its last arrival's stamp,
    and the next opens there."""

    index: int
    reason: SealReason
    opened: float
    closed: float


class Windowing:
    """Which window each arrival falls in, from the stream's open at ``opened`` (the clock's
    monotonic time) and the window spec; ``until`` ends the last window, at that time. Pure: it
    reads no clock, and the same arrivals give the same windows.

    A window ended by time closes at ``open + seconds`` and the next opens there, so windows of
    a quiet stretch fall on one grid; a window ended by ``records`` or ``bytes`` closes at the
    stamp of the arrival that reached the bound, and the next opens at that stamp."""

    def __init__(
        self,
        spec: WindowSpec,
        opened: float,
        *,
        until: float | None = None,
        index: int = 0,
        last: int | None = None,
    ) -> None:
        """``index`` is the first window's; ``last``, when given, is the index of the window
        the stream ends with, as ``until`` ends it at a time."""
        self._spec = spec
        self._until = until
        self._last = last
        self.index = index
        self.opened = opened
        self.records = 0
        self.bytes = 0
        #: True once ``until`` has passed or the ``last`` window has ended: no arrival falls in
        #: any window after it.
        self.ended = False

    @property
    def close(self) -> float | None:
        """When the window open now ends by time, ``until`` included; None when it has no time
        bound."""
        by_time = self.opened + self._spec.seconds if self._spec.seconds is not None else None
        if self._until is None:
            return by_time
        return self._until if by_time is None else min(by_time, self._until)

    def advance(self, now: float) -> list[WindowEnd]:
        """The windows that have ended by ``now``, in order, each by time or by ``until``."""
        ends: list[WindowEnd] = []
        while not self.ended and (close := self.close) is not None and now >= close:
            reason: SealReason = "until" if close == self._until else "seconds"
            ends.append(self._end(reason, close))
            self.ended = self.ended or reason == "until"
        return ends

    def arrive(
        self, stamp: float, size: int
    ) -> tuple[list[WindowEnd], int | None, list[WindowEnd]]:
        """An arrival at ``stamp`` of ``size`` bytes: the windows that ended before it, the
        window it falls in (None once ``until`` has passed), and the window it ends by filling
        it (its own, ended by ``records`` or ``bytes``), if it does."""
        before = self.advance(stamp)
        if self.ended:
            return before, None, []
        window = self.index
        self.records += 1
        self.bytes += size
        after: list[WindowEnd] = []
        if self._spec.records is not None and self.records >= self._spec.records:
            after.append(self._end("records", stamp))
        elif self._spec.bytes is not None and self.bytes >= self._spec.bytes:
            after.append(self._end("bytes", stamp))
        return before, window, after

    def _end(self, reason: SealReason, closed: float) -> WindowEnd:
        end = WindowEnd(self.index, reason, self.opened, closed)
        self.ended = self.index == self._last
        self.index += 1
        self.opened = closed
        self.records = 0
        self.bytes = 0
        return end


def window_id(index: int, opened: datetime) -> str:
    """A window's id: its index, then when it opened (``00000003-2026-10-24-010203-456789``).
    Unique by construction in its stream, which one writer at a time extends."""
    return f"{index:0{WINDOW_INDEX_DIGITS}d}-{opened.strftime(ARCHIVE_ID_FORMAT)}"


def window_opened(name: str) -> datetime | None:
    """When a window opened, as its id says (UTC); None for a name that is not a window's
    id."""
    found = _WINDOW_ID.match(name)
    if found is None:
        return None
    try:
        return datetime.strptime(found.group(2), ARCHIVE_ID_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def window_index(name: str) -> int | None:
    """The index a window's id holds; None for a name that is not a window's id."""
    found = _WINDOW_ID.match(name)
    if found is None:
        return None
    try:
        datetime.strptime(found.group(2), ARCHIVE_ID_FORMAT)
    except ValueError:
        return None
    return int(found.group(1))

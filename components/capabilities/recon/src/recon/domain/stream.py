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
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from recon.domain.archives import ARCHIVE_ID_FORMAT
from recon.domain.clock import LONGEST_WAIT

if TYPE_CHECKING:
    from datetime import datetime

#: Why a window ended, when it ended well: its time, records or bytes bound was reached, its
#: source ended, the stream was stopped by a signal, or it reached the stream's ``until``.
SealReason = Literal["window", "records", "bytes", "eof", "signal", "until"]

#: By default, the seconds a window of a type that does not end spans, when its entry sets no
#: ``window:``: windows of five minutes or more keep a query across them cheap.
DEFAULT_WINDOW_S: Final = 300.0

#: A window's id is its index, this many digits wide, then when it opened.
WINDOW_INDEX_DIGITS: Final = 8

_WINDOW_ID = re.compile(rf"^(\d{{{WINDOW_INDEX_DIGITS},}})-(.+)$")


class WindowSpec(BaseModel):
    """``window:`` on a collector entry: what ends each window, the first of these reached.
    ``seconds`` (a number of seconds, as ``deadline:`` is) from the window's open; ``records``
    records; ``bytes`` bytes of records. At least one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
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
        self, spec: WindowSpec, opened: float, *, until: float | None = None, index: int = 0
    ) -> None:
        self._spec = spec
        self._until = until
        self.index = index
        self.opened = opened
        self.records = 0
        self.bytes = 0
        #: True once ``until`` has passed: no arrival falls in any window after it.
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
            reason: SealReason = "until" if close == self._until else "window"
            ends.append(self._end(reason, close))
            if reason == "until":
                self.ended = True
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
        self.index += 1
        self.opened = closed
        self.records = 0
        self.bytes = 0
        return end


def window_id(index: int, opened: datetime) -> str:
    """A window's id: its index, then when it opened (``00000003-2026-10-24-010203-456789``).
    Unique by construction in its stream, which one writer at a time extends."""
    return f"{index:0{WINDOW_INDEX_DIGITS}d}-{opened.strftime(ARCHIVE_ID_FORMAT)}"


def window_index(name: str) -> int | None:
    """The index a window's id holds; None for a name that is not a window's id."""
    from datetime import datetime

    found = _WINDOW_ID.match(name)
    if found is None:
        return None
    try:
        datetime.strptime(found.group(2), ARCHIVE_ID_FORMAT)
    except ValueError:
        return None
    return int(found.group(1))

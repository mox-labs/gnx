"""A stream's run: its source read on a thread of its own into a bounded buffer, and its windows
written from that buffer one after another, each sealed as an archive (rulings B1 to B7).

Three threads take part, and only the first writes anything:

- **the writer**, the caller's thread, takes what arrived from the buffer in order and writes
  each window's one table through the archive store (built once: :class:`Stream`);
- **the pump** reads the collector's records, which may never end, and hands each to the
  buffer, so a source that sends while the writer is busy is read meanwhile;
- **the ticker** sleeps on the clock port until the open window's time is up and tells the
  buffer, so a quiet source's window still ends on time.

**Windows.** Each record is stamped by the clock port as the buffer admits it, under the
buffer's lock, and assigned to its window there (:class:`~recon.domain.stream.Windowing`), so
which window a record falls in is a pure function of its order of arrival, its stamp and the
window spec, whatever thread delivered it. A window ends at its time (half-open), on its
``records`` or ``bytes``, at the stream's ``until``, when its source ends (``eof``) or when the
stream is stopped on request (``stopped``); it is then sealed ``ok`` with that reason. A
source that fails seals its window ``error`` and **keeps its records**: what a source sent
cannot be fetched again.

**Flow.** At most ``buffer.records`` records and ``buffer.bytes`` bytes wait (one record more
may be admitted while fewer bytes wait, so a stream holds at most ``bytes`` +
``max_record_bytes`` of them); one more is an overflow, which ``overflow`` settles: ``block``
holds the pump until there is room, ``drop_newest`` drops the arriving record, ``drop_oldest``
the one waiting longest. A record over ``max_record_bytes`` is dropped as it arrives. Every
drop is counted in the window it fell in, so for each window the records admitted are those
written and those dropped, and nothing of a window is left waiting when it is sealed.

**Determinism, per window.** A window's records are written in order of arrival, and with
``preserve_raw`` its capture holds each as its collector gave it, with its order and its
offset from the window's open, from which the window replays to the same bytes whatever clock
replays it. A window that dropped records cannot be replayed from its capture, and says so
(``reproducible: false``), as does a window kept without captures.

**Crash and resume.** A writer killed mid-window leaves that window unsealed, read
``incomplete``, and never changed: its torn last line, if any, is moved aside. The next run of
the stream chains its first window to it (``previous``), and resumes its source after the
position of the last whole record kept (a record's ``_position``): exactly once for a source
that has positions. Every restart is recorded as a gap, ``resumed`` when it went on from one.

**Crawls** (ruling P4). An entry that reads its source page by page (``paginate:``) crawls it
across its windows. Its collector marks each page before it asks for it (the stream's page
log, :class:`StreamPages`), and the mark goes through the buffer in order with the records, so
the writer adds it to the window's page log (``<output>.pages.jsonl``) after the records before
it are written, and a window of ``pages`` pages ends at the mark of the page after them. A
mark the stream will not keep (it has ended) is refused, and the page is not asked for. A run
goes on from where the last left off by :func:`crawl_resume`: the last page the page logs name
and the last record kept from it, so a page none of whose records was kept is asked for first,
and one whose records were is asked for again and read past them. The crawl's end (a page that
names no next one, or a crawl cut short) ends the stream (``eof``), and a run after it crawls
again from the first page. A crawl that began again, and a page that moved under it, are each
recorded and warned (``paginate_restarted``, ``paginate_shifted``).
"""

from __future__ import annotations

import json
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from recon.application.changes import changes_failed, failed, join_unclosed
from recon.application.survey import PROGRESS_S, crawl_truncated
from recon.domain.archives import META_FORMAT_VERSION
from recon.domain.documents import TableError, TableOutcome, WindowOutcome
from recon.domain.errors import ErrorKind, Problem, ReconError
from recon.domain.paginate import Resume, content
from recon.domain.stream import (
    POSITION_FIELD,
    Gap,
    WindowEnd,
    Windowing,
    window_id,
    window_index,
    window_opened,
)
from recon.domain.telemetry import COLLECT

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator, Mapping
    from contextlib import AbstractContextManager
    from pathlib import Path

    from recon.application.changes import ChangeDetector
    from recon.domain.archives import ArchiveStore
    from recon.domain.capture import CaptureLog
    from recon.domain.clock import Clock
    from recon.domain.models import CollectorEntry, SourceEntry
    from recon.domain.paginate import TruncatedBy
    from recon.domain.stream import (
        BufferSpec,
        ClosedBy,
        Overflow,
        Retain,
        RetainSpec,
        SealReason,
        Stop,
        WindowSpec,
    )
    from recon.domain.telemetry import EventCallback, Span, Telemetry

#: By default, the seconds the end of a stream waits for its source's thread to stop, once
#: its collector is closed.
STOP_S = 5.0

#: The longest the ticker sleeps at once before it looks whether the stream has ended: a
#: window is a second at least, so it wakes no more than once a window to no purpose.
TICK_S = 1.0

#: Opens one window's telemetry over the clock given, its event log among the sinks: the
#: telemetry and the callback the window's outcome events go to, both ended when the block
#: ends.
WindowTelemetry = Callable[
    ["Path", "Clock"], "AbstractContextManager[tuple[Telemetry, EventCallback]]"
]


@dataclass(frozen=True)
class StreamPlan:
    """One stream, as its plan resolved it: its name (the collector's), where its entry is in
    the config (``collectors.<i>``), the entry its window table is written for (named for its
    output) and its source, and the effective window, buffer and overflow. ``package`` is
    the package that registered the entry's collector type, when it is known (``recon`` for
    its own), so a warning about the type says whose it is."""

    name: str
    at: str
    entry: CollectorEntry
    source: SourceEntry | None
    window: WindowSpec
    buffer: BufferSpec
    overflow: Overflow
    columns: tuple[str, ...] = ()
    retain: Retain = "all"
    package: str | None = None
    #: The windows retention must keep whatever ``retain`` says: those holding change rows a
    #: subscription has not acked (asked after each window is sealed).
    held: Callable[[], set[str]] | None = None


@dataclass(frozen=True)
class StreamRun:
    """What one run of a stream did: the windows it sealed, how many, and what they held.
    ``last`` is the last window sealed (None when none was), whose ``closed_by`` says why the
    run stopped."""

    windows: int
    records: int
    dropped: int
    first: str | None
    last: WindowOutcome | None


class ConservationError(RuntimeError):
    """A defect in recon: a window's table holds more records than were admitted to it, so
    the identity every count rests on (admitted = written + dropped) broke. Raised, never
    clamped: a drop count that hid it would be a false record of the stream."""


def _drops(stream: str, index: int, ledger: _Ledger, written: int) -> int:
    """A window's drops: the records admitted to it that its table does not hold, and those
    refused before admission. ConservationError when the table holds more than was admitted."""
    if written > ledger.admitted:
        raise ConservationError(
            f"stream {stream} window {index}: {ledger.admitted} admitted, but its sealed table "
            f"holds {written}; a defect in recon, please report it"
        )
    return ledger.admitted - written + ledger.refused


# --- the buffer ------------------------------------------------------------------------------


@dataclass
class _Ledger:
    """What a window's arrivals came to besides its records: the drops counted so far (for its
    progress), those of records never admitted (``refused``: too large, or overflow's newest,
    and what later windows that will never be written held), the most records that waited,
    the gaps its source reported, and the order of its first and last admitted. A window's
    drops are, once it is sealed, the records admitted to it that its table does not hold,
    and those refused. Of them, ``oversize`` were larger than ``max_record_bytes`` and
    ``overflow`` met a full buffer; the rest waited when the stream stopped at once."""

    dropped: int = 0
    refused: int = 0
    oversize: int = 0
    overflow: int = 0
    high_water: int = 0
    gaps: list[Gap] = field(default_factory=list)
    first: int | None = None
    last: int | None = None
    #: The page marks that fell in it, and what its crawl said moved: ``("restarted", page,
    #: None)``, or ``("shifted", page, by)``.
    pages: int = 0
    moved: list[tuple[str, int, int | None]] = field(default_factory=list)

    @property
    def admitted(self) -> int:
        return 0 if self.first is None or self.last is None else self.last - self.first + 1


@dataclass(frozen=True)
class _Arrival:
    """One record admitted: its order of arrival, its stamp, its window, and the record as its
    collector gave it, as a JSON line's bytes (which is what the buffer's bounds count)."""

    ordinal: int
    stamp: float
    window: int
    line: bytes


@dataclass(frozen=True)
class _Page:
    """A crawl's mark of a page it is about to ask for: the window it fell in, the page's
    ordinal in its crawl and the token that asks for it, as the page log keeps them."""

    window: int
    index: int
    token: Any


@dataclass(frozen=True)
class _End:
    """The stream's last event: its source ended (``eof``), it was stopped on request
    (``stopped``), or its source failed (``error``). A crawl that ended cut short says what
    cut it (``truncated_by``)."""

    reason: SealReason | None
    stamp: float
    error: BaseException | None = None
    truncated_by: TruncatedBy | None = None


type _Event = _Arrival | _Page | WindowEnd | _End


class Arrivals:
    """The bounded buffer between a stream's source and its windows, and the windowing of what
    it admits. Thread-safe: the pump puts, the ticker advances, the writer takes, each under
    one lock, and every stamp is read from the clock under it, so the order the writer takes
    events in is the order of their stamps."""

    def __init__(
        self,
        clock: Clock,
        windowing: Windowing,
        buffer: BufferSpec,
        overflow: Overflow,
        *,
        again: int | None = None,
    ) -> None:
        """``again``: the page a resumed crawl asks for again to read past what was kept of
        it, whose first mark is the earlier window's page, not counted in this one's."""
        self._clock = clock
        self._windowing = windowing
        self._buffer = buffer
        self._overflow = overflow
        self._cond = threading.Condition()
        self._events: deque[_Event] = deque()
        self._records = 0
        self._bytes = 0
        self._ordinal = 0
        self._ledgers: dict[int, _Ledger] = {}
        #: False once the stream has ended: no record is admitted, or counted, after that.
        self._admitting = True
        #: The window the writer is writing, or opens next: a second stop counts in it.
        self._writing = windowing.index
        #: The stops asked so far (:meth:`interrupt`).
        self._stops = 0
        self._again = again
        self._truncated_by: TruncatedBy | None = None
        #: The pages the crawl has marked so far, in its run: its last mark's ordinal + 1.
        self.crawled = 0

    # --- the pump's side ---

    def put(self, line: bytes) -> bool:
        """Admit a record, or drop it; False once the stream has ended, when the pump stops."""
        with self._cond:
            if not self._advance():
                return False
            if len(line) > self._buffer.max_record_bytes:
                self._refuse(self._windowing.index, oversize=True)
                return True
            if self._overflow == "block":
                while self._admitting and self._full():
                    self._cond.wait()
                if not self._advance():
                    return False
            elif self._full():
                if self._overflow == "drop_newest":
                    self._refuse(self._windowing.index, oversize=False)
                    return True
                self._evict_oldest()
            stamp = self._clock.monotonic()
            before, window, after = self._windowing.arrive(stamp, len(line))
            self._events.extend(before)
            if window is None:
                self._admitting = False
                self._cond.notify_all()
                return False
            self._events.append(_Arrival(self._ordinal, stamp, window, line))
            self._events.extend(after)
            ledger = self._ledger(window)
            ledger.first = self._ordinal if ledger.first is None else ledger.first
            ledger.last = self._ordinal
            self._ordinal += 1
            self._records += 1
            self._bytes += len(line)
            ledger.high_water = max(ledger.high_water, self._records)
            self._admitting = not self._windowing.ended
            self._cond.notify_all()
            return self._admitting

    def gap(self, gap: Gap) -> None:
        """Record a gap the source reported, in the window open now."""
        with self._cond:
            if self._advance():
                self._ledger(self._windowing.index).gaps.append(gap)

    def page(self, index: int, token: Any) -> bool:
        """A crawl's mark of page ``index``, about to be asked for by ``token``: in the window
        open now, which ends first when it holds its ``pages`` already. False once the stream
        has ended, when the page is not asked for."""
        with self._cond:
            if not self._advance():
                return False
            counted, self._again = index != self._again, None
            before, window = self._windowing.page(self._clock.monotonic(), counted=counted)
            self._events.extend(before)
            if window is None:
                self._admitting = False
                self._cond.notify_all()
                return False
            self._events.append(_Page(window, index, token))
            self._ledger(window).pages += 1
            self.crawled = index + 1
            self._cond.notify_all()
            return True

    def truncated(self, by: TruncatedBy) -> None:
        """The crawl stopped short, cut by ``by``: said by the stream's end."""
        with self._cond:
            self._truncated_by = by

    def moved(self, what: str, index: int, by: int | None) -> None:
        """The crawl said its resumed page ``index`` moved under it (``what``: ``restarted``,
        or ``shifted`` by ``by``), in the window open now; a crawl that could not go on from
        where it was (restarted, or its boundary not found) is a gap there that did not
        resume."""
        with self._cond:
            if self._advance():
                ledger = self._ledger(self._windowing.index)
                ledger.moved.append((what, index, by))
                if what == "restarted" or by is None:
                    ledger.gaps.append(Gap(None, self._clock.now(), resumed=False))

    def dropped_early(self) -> None:
        """Count a record its collector dropped before handing it on (one too large), in the
        window open now, as one the buffer refused."""
        with self._cond:
            if self._advance():
                self._refuse(self._windowing.index, oversize=True)

    def end(self, error: BaseException | None = None) -> None:
        """The source ended, or failed with ``error``: the stream ends after what waits. Said
        after the stream has ended (a source its closing broke off), it is not heard."""
        with self._cond:
            if self._admitting:
                self._admitting = False
                reason: SealReason | None = "eof" if error is None else None
                cut = self._truncated_by if error is None else None
                self._events.append(_End(reason, self._clock.monotonic(), error, cut))
                self._cond.notify_all()

    # --- the ticker's side ---

    def close(self) -> float | None:
        """When the window open now ends by time; None once the stream has ended."""
        with self._cond:
            return self._windowing.close if self._admitting else None

    def advance(self) -> None:
        """End each window whose time is up by now."""
        with self._cond:
            self._advance()

    # --- the writer's side ---

    def peek(self) -> _Event:
        """The next event, left in place; waits for one."""
        with self._cond:
            while not self._events:
                self._cond.wait()
            return self._events[0]

    def pop(self) -> None:
        """Take out the event :meth:`peek` gave. The writer peeks (and may be interrupted
        there, losing nothing), then pops what it holds."""
        with self._cond:
            event = self._events.popleft()
            if isinstance(event, _Arrival):
                self._records -= 1
                self._bytes -= len(event.line)
                self._cond.notify_all()

    def stop(self) -> None:
        """Stop the stream, as a signal does: nothing more is admitted, and the stream ends
        once what waits is written."""
        with self._cond:
            if self._admitting:
                self._admitting = False
                self._events.append(_End("stopped", self._clock.monotonic()))
            self._cond.notify_all()

    def interrupt(self) -> bool:
        """A stop asked of the stream (a signal, a :class:`StreamStop`'s ask, or the writer's
        thread interrupted): the first stops it once what waits is written; a later one stops
        it now, what waits counted dropped in the window the writer is writing or opens next.
        True when this one stopped it now."""
        with self._cond:
            self._stops += 1
            if self._stops == 1:
                self.stop()
                return False
            self.abandon(into=self._writing)
            return True

    def abandon(self, into: int | None = None) -> int:
        """Stop the stream at once, as a second signal or a failure does: nothing more is
        admitted or written, and what waits is dropped, its stream's end the one event left.
        With ``into``, the window being written (or about to be), what later windows that will
        never be written held (the records admitted to them, their refusals, their gaps) is
        counted there, so nothing admitted goes uncounted; that window's own records that
        waited are admitted to it, so its seal counts them dropped. Without it, what every
        window not sealed held is discarded, and how many records that was is returned for
        the caller to count (with ``into``, none is)."""
        with self._cond:
            self._admitting = False
            discarded = 0
            if into is not None:
                ledger = self._ledger(into)
                for later in sorted(w for w in self._ledgers if w > into):
                    gone = self._ledgers.pop(later)
                    ledger.refused += gone.admitted + gone.refused
                    ledger.dropped += gone.admitted + gone.refused
                    ledger.oversize += gone.oversize
                    ledger.overflow += gone.overflow
                    ledger.gaps += gone.gaps
                    ledger.moved += gone.moved
            else:
                discarded = sum(gone.admitted + gone.refused for gone in self._ledgers.values())
                self._ledgers.clear()
            self._events.clear()
            self._events.append(_End("stopped", self._clock.monotonic()))
            self._records = self._bytes = 0
            self._cond.notify_all()
            return discarded

    def ledger(self, window: int) -> _Ledger:
        """What window ``window``'s arrivals came to, taken out: called once it is sealed, so
        the writer is on to the next."""
        with self._cond:
            self._writing = window + 1
            return self._ledgers.pop(window, _Ledger())

    def dropped(self, window: int) -> int:
        with self._cond:
            return self._ledgers[window].dropped if window in self._ledgers else 0

    def noted(self, window: int) -> bool:
        """Whether anything was counted in window ``window``: a record admitted, a drop, a
        gap."""
        with self._cond:
            ledger = self._ledgers.get(window)
            return ledger is not None and bool(
                ledger.admitted or ledger.refused or ledger.dropped or ledger.gaps or ledger.pages
            )

    # --- under the lock ---

    def _advance(self) -> bool:
        """End each window whose time is up by now; whether the stream still admits."""
        if not self._admitting:
            return False
        ended = self._windowing.advance(self._clock.monotonic())
        self._events.extend(ended)
        self._admitting = not self._windowing.ended
        if ended:  # the writer waits on events: each window's end wakes it
            self._cond.notify_all()
        return self._admitting

    def _full(self) -> bool:
        return self._records >= self._buffer.records or self._bytes >= self._buffer.bytes

    def _evict_oldest(self) -> None:
        for at, event in enumerate(self._events):
            if isinstance(event, _Arrival):
                del self._events[at]
                self._records -= 1
                self._bytes -= len(event.line)
                ledger = self._ledger(event.window)
                ledger.dropped += 1
                ledger.overflow += 1
                return

    def _ledger(self, window: int) -> _Ledger:
        return self._ledgers.setdefault(window, _Ledger())

    def _refuse(self, window: int, *, oversize: bool) -> None:
        """A record dropped before it was admitted, counted in ``window``: too large
        (``oversize``), or the buffer full."""
        ledger = self._ledger(window)
        ledger.dropped += 1
        ledger.refused += 1
        if oversize:
            ledger.oversize += 1
        else:
            ledger.overflow += 1


class StreamGaps:
    """The :class:`~recon.domain.stream.GapLog` a stream's collector is handed
    (``CollectorContext.gaps``): what it says goes to the window being filled."""

    def __init__(self) -> None:
        self._to: Arrivals | None = None

    def gap(self, gap: Gap) -> None:
        to = self._to
        if to is not None:
            to.gap(gap)

    def drop(self) -> None:
        to = self._to
        if to is not None:
            to.dropped_early()


class StreamPages:
    """The :class:`~recon.domain.paginate.PageLog` a stream's collector is handed
    (``CollectorContext.pages``): each page it marks goes to the buffer, in order with its
    records, and into the window being filled; a mark the stream will not keep is refused, so
    the page is not asked for."""

    def __init__(self) -> None:
        self._to: Arrivals | None = None

    def page(self, index: int, token: Any) -> bool:
        to = self._to
        return True if to is None else to.page(index, token)

    def truncated(self, by: TruncatedBy) -> None:
        to = self._to
        if to is not None:
            to.truncated(by)

    def restarted(self, index: int) -> None:
        to = self._to
        if to is not None:
            to.moved("restarted", index, None)

    def shifted(self, index: int, by: int | None) -> None:
        to = self._to
        if to is not None:
            to.moved("shifted", index, by)


class StreamStop:
    """Asks a running stream to stop, from any thread or a signal handler (``recon stream``
    asks from its SIGINT and SIGTERM handlers). The first ask seals the window being written
    once what waits in the buffer is written, however busy the writer is when it comes; a
    second seals it at once, what waits counted dropped. An ask made before the stream starts
    stops it as it starts. One stop serves one run of one stream.

    An ask touches nothing of the stream: a thread of the stream's own hears it and stops the
    stream's buffer, so a signal handler that runs on the writer's thread, inside whatever it
    is doing, leaves that undisturbed. It is the domain's :class:`~recon.domain.stream.Stop`."""

    def __init__(self) -> None:
        # Reentrant: a second signal's handler may run inside the first's ask.
        self._cond = threading.Condition(threading.RLock())
        self._asked = 0
        self._heard = 0
        self._over = False

    def ask(self) -> None:
        """Ask the stream to stop: safe from a signal handler."""
        with self._cond:
            self._asked += 1
            self._cond.notify_all()

    @property
    def heard(self) -> int:
        """How many asks the stream has acted on."""
        with self._cond:
            return self._heard

    # --- the stream's side ---

    def next_ask(self) -> bool:
        """Wait for an ask the stream has not acted on yet; False once its run is over."""
        with self._cond:
            while self._asked == self._heard and not self._over:
                self._cond.wait()
            return not self._over

    def acted(self) -> None:
        """The stream acted on an ask."""
        with self._cond:
            self._heard += 1
            self._cond.notify_all()

    def end(self) -> None:
        """The stream's run is over: no ask is waited for after this."""
        with self._cond:
            self._over = True
            self._cond.notify_all()


def _listen(stop: Stop, arrivals: Arrivals) -> None:
    """The listener's thread: each ask of ``stop`` to the buffer, outside the stop's lock,
    until the run is over."""
    while stop.next_ask():
        arrivals.interrupt()
        stop.acted()


# --- the writer ----------------------------------------------------------------------------


@dataclass
class _Window:
    """The window being written: what the writer has learned of it so far."""

    index: int
    id: str
    archive: Path
    opened: float
    previous: str | None
    records: int = 0
    first: Any = None
    last: Any = None
    end: WindowEnd | _End | None = None
    error: BaseException | None = None
    frames: bool = True


@dataclass(frozen=True)
class _Start:
    """Where a run of a stream begins: the index of its first window, the window it follows,
    what its source is asked to resume after (``after``: a position, or a crawl's
    :class:`~recon.domain.paginate.Resume`), the position its first window carries until it
    holds a record of its own, the gap since that window, and the page a resumed crawl asks
    for again (``again``)."""

    index: int
    previous: str | None
    after: Any
    gap: Gap | None
    carried: Any = None
    again: int | None = None


class Stream:
    """A stream's run, over the archive store and the clock. Built once and run once per
    stream: the window writer every stream goes through, a survey's one window included.

    ``telemetry`` opens each window's telemetry and the callback its outcome event goes to
    (``{"kind": "window", ...}``, as each window is sealed); ``progress_s`` is the least time
    between two progress events of a window; ``stop_s`` how long the end of the stream waits
    for its source's thread once its collector is closed. ``warnings`` collects what the run
    found worth saying, at most one of each kind, so a stream that runs for a month holds no
    more than one that runs a minute."""

    def __init__(
        self,
        archives: ArchiveStore,
        clock: Clock,
        recon_version: str,
        *,
        telemetry: WindowTelemetry,
        progress_s: float = PROGRESS_S,
        stop_s: float = STOP_S,
        changes: ChangeDetector | None = None,
    ) -> None:
        self._archives = archives
        self._changes = changes
        self._clock = clock
        self._recon_version = recon_version
        self._telemetry = telemetry
        self._progress_s = progress_s
        self._stop_s = stop_s
        self.warnings: list[Problem] = []
        #: By cause, the windows that dropped records and how many they dropped.
        self._dropped: dict[str, tuple[int, int]] = {}

    def run(
        self,
        mission_dir: Path,
        plan: StreamPlan,
        collect: Callable[[Any], Iterator[dict[str, Any]]],
        normalize: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        close: Callable[[], object],
        gaps: StreamGaps | None = None,
        pages: StreamPages | None = None,
        windows: int | None = None,
        until: datetime | None = None,
        preserve_raw: bool = False,
        config_sha256: str | None = None,
        resolved: Mapping[str, Any] | None = None,
        on_window: Callable[[WindowOutcome], None] | None = None,
        stop: Stop | None = None,
    ) -> StreamRun:
        """Run the stream until it ends: after ``windows`` windows, at ``until``, when its
        source ends or fails, or when it is asked to stop (``stop``) or the caller's thread is
        interrupted (``KeyboardInterrupt``): the window being written is then sealed
        ``stopped`` once what waits is written; a second stop seals it at once, what waits
        dropped. An interrupt that lands in the store's write seals the window at once.
        ``collect(after)`` starts the source after the position ``after`` (None: from its
        start); ``normalize`` shapes each record; ``close`` ends the collector, which ends a
        source that would not; ``gaps`` and ``pages`` are the gap log and the page log its
        collector was built with. Raises ReconError (``busy``) when another writer holds the
        stream."""
        self._archives.hold_stream(mission_dir, plan.name)
        try:
            return self._run(
                mission_dir, plan, collect, normalize, close, gaps, pages, windows, until,
                preserve_raw, config_sha256, dict(resolved or {}), on_window, stop,
            )  # fmt: skip
        finally:
            self._archives.release_stream(mission_dir, plan.name)

    def _run(
        self,
        mission_dir: Path,
        plan: StreamPlan,
        collect: Callable[[Any], Iterator[dict[str, Any]]],
        normalize: Callable[[dict[str, Any]], dict[str, Any]],
        close: Callable[[], object],
        gaps: StreamGaps | None,
        pages: StreamPages | None,
        windows: int | None,
        until: datetime | None,
        preserve_raw: bool,
        config_sha256: str | None,
        resolved: dict[str, Any],
        on_window: Callable[[WindowOutcome], None] | None,
        stop: Stop | None,
    ) -> StreamRun:
        start = self._resume(mission_dir, plan)
        opened_wall, opened = self._clock.now(), self._clock.monotonic()

        def wall(stamp: float) -> datetime:
            return opened_wall + timedelta(seconds=stamp - opened)

        last = None if windows is None else start.index + windows - 1
        until_at = None if until is None else opened + (until - opened_wall).total_seconds()
        windowing = Windowing(plan.window, opened, until=until_at, index=start.index, last=last)
        arrivals = Arrivals(self._clock, windowing, plan.buffer, plan.overflow,
                            again=start.again)  # fmt: skip
        if start.gap is not None:
            arrivals.gap(start.gap)
        if gaps is not None:
            gaps._to = arrivals
        if pages is not None:
            pages._to = arrivals
        pump = threading.Thread(
            target=_pump, args=(collect, start.after, arrivals), name="recon-stream-pump",
            daemon=True,
        )  # fmt: skip
        pump.start()
        listener = None
        if stop is not None:
            listener = threading.Thread(
                target=_listen, args=(stop, arrivals), name="recon-stream-stop", daemon=True
            )
            listener.start()
        if plan.window.seconds is not None or until_at is not None:
            threading.Thread(
                target=_tick, args=(self._clock, arrivals), name="recon-stream-ticker", daemon=True
            ).start()
        meta = {"config_sha256": config_sha256, "preserve_raw": preserve_raw, "resolved": resolved}
        sealed, records, dropped = 0, 0, 0
        sizes: dict[str, int] = {}  # each window's size, read once, for retain: {bytes}
        first: str | None = None
        outcome: WindowOutcome | None = None
        previous, carried = start.previous, start.carried
        index, window_opened = start.index, opened
        try:
            while True:
                event = self._next(arrivals)
                ends_bare = isinstance(event, _End) and event.error is None
                if ends_bare and sealed and not arrivals.noted(index):
                    break  # the source ended as a window opened: it has nothing to keep
                window = _Window(
                    index=index,
                    id=window_id(index, wall(window_opened)),
                    archive=mission_dir,  # set once made
                    opened=window_opened,
                    previous=previous,
                    last=carried,
                    frames=preserve_raw,
                )
                window.archive = self._archives.create(mission_dir, window.id, stream=plan.name)
                try:
                    outcome = self._window(plan, window, arrivals, normalize, wall, meta)
                finally:
                    self._archives.release(window.archive)
                sealed += 1
                records += outcome.table.records
                dropped += outcome.dropped
                first = first or outcome.id
                if on_window is not None:
                    on_window(outcome)
                self._retain(mission_dir, plan, outcome, sizes)
                if not isinstance(window.end, WindowEnd) or window.end.reason == "until":
                    break
                if window.end.index == last:
                    break
                previous, carried = window.id, window.last
                index, window_opened = window.end.index + 1, window.end.closed
        finally:
            if stop is not None and listener is not None:
                stop.end()
                listener.join(self._stop_s)
            # Every way the loop returns stops admitting first, so this is none then; what
            # it is counts dropped, so nothing that arrived goes uncounted.
            dropped += arrivals.abandon()
            close()
            pump.join(self._stop_s)
            if pump.is_alive():
                self._warn_stuck(plan)
        return StreamRun(sealed, records, dropped, first, outcome)

    def _next(self, arrivals: Arrivals) -> _Event:
        """The next event, waiting for it, left in the buffer: an interrupt is a stop asked
        (:meth:`Arrivals.interrupt`), and the wait goes on for what it leaves."""
        while True:
            try:
                return arrivals.peek()
            except KeyboardInterrupt:
                arrivals.interrupt()

    def _halt(self, window: _Window, arrivals: Arrivals) -> None:
        """Stop the stream now, in ``window``: what waits, and what the writer held, are
        admitted to it and not in its table, so its seal counts them dropped."""
        arrivals.abandon(into=window.index)

    def _window(
        self,
        plan: StreamPlan,
        window: _Window,
        arrivals: Arrivals,
        normalize: Callable[[dict[str, Any]], dict[str, Any]],
        wall: Callable[[float], datetime],
        meta: dict[str, Any],
    ) -> WindowOutcome:
        """Write one window from ``arrivals`` until its end, seal it, and write its meta."""
        captures = self._archives.capture_log(window.archive) if window.frames else None
        output = plan.entry.name
        table = TableOutcome(
            name=output,
            file=f"{output}.jsonl",
            collector=plan.name,
            type=plan.entry.type,
            source=plan.source.name if plan.source is not None else None,
            type_url=plan.entry.type_url,
        )
        attributes = {
            "recon.table": output,
            "recon.collector": plan.name,
            "recon.collector.type": plan.entry.type,
            "recon.source": table.source,
            "recon.stream": plan.name,
            "recon.window": window.index,
        }
        time = _WindowTime(wall, window.opened)
        with self._telemetry(window.archive, time) as (telemetry, emit):
            with telemetry.span(COLLECT, attributes) as span:
                records = self._records(
                    window, arrivals, normalize, captures, telemetry, span, output, time
                )
                try:
                    self._archives.write_table(
                        window.archive, output, records, columns=plan.columns, keep_on_error=True
                    )
                except KeyboardInterrupt:
                    # Interrupted away from a wait (writing, shaping): what was written is
                    # sealed, and the stream ends here, what it held or still waits dropped.
                    self._halt(window, arrivals)
                    window.end = window.end or _End("stopped", self._clock.monotonic())
                except Exception as exc:  # the store failed: what was written is sealed
                    window.error = window.error or exc
                    self._halt(window, arrivals)
                    window.end = window.end or _End(None, self._clock.monotonic(), exc)
                ledger = arrivals.ledger(window.index)
                # What the window holds is what its table holds, read from it: a record the
                # writer took and the store never kept is no record of it, and a dropped one.
                ends = self._archives.table_ends(window.archive, output)
                window.records = ends.records
                if ends.first is not None:
                    window.first = ends.first.get(POSITION_FIELD)
                if ends.last is not None and POSITION_FIELD in ends.last:
                    window.last = ends.last[POSITION_FIELD]
                window_dropped = _drops(plan.name, window.index, ledger, ends.records)
                cut = window.end.truncated_by if isinstance(window.end, _End) else None
                if cut is not None:
                    table = table.model_copy(update={"truncated": True, "truncated_by": cut})
                span.set("recon.records", window.records)
                span.set("recon.dropped", window_dropped)
                error = window.error or (window.end.error if isinstance(window.end, _End) else None)
                kind: ErrorKind | None = None
                if error is not None:
                    kind = error.kind if isinstance(error, ReconError) else "run_failed"
                    span.fail(kind, "the window's source failed; its error is in its meta.yaml")
                time.pin(_closed(window))
            outcome = self._seal(
                plan, window, table, ledger, window_dropped, error, kind, wall, meta, captures
            )
            emit(
                {
                    "kind": "window",
                    "name": plan.name,
                    "index": window.index,
                    "id": window.id,
                    "records": outcome.table.records,
                    "dropped": outcome.dropped,
                    "closed_by": outcome.closed_by,
                    "status": outcome.table.status,
                    "reason": None if outcome.table.error is None else outcome.table.error.message,
                    "fix": None if outcome.table.error is None else outcome.table.error.fix,
                }
            )
            if cut is not None:
                pages = arrivals.crawled
                warning = crawl_truncated(plan.at, plan.entry, plan.entry.name, cut, pages)
                self._warn(plan, warning, emit)
            for what, page, by in ledger.moved:
                self._warn(plan, _moved(plan, what, page, by), emit)
            if window_dropped:
                halted = window_dropped - ledger.oversize - ledger.overflow
                causes = {"overflow": ledger.overflow, "oversize": ledger.oversize,
                          "halted": halted}  # fmt: skip
                self._warn_dropped(plan, causes, emit)
        return outcome

    def _records(
        self,
        window: _Window,
        arrivals: Arrivals,
        normalize: Callable[[dict[str, Any]], dict[str, Any]],
        captures: CaptureLog | None,
        telemetry: Telemetry,
        span: Span,
        output: str,
        time: _WindowTime,
    ) -> Generator[dict[str, Any], None, None]:
        """The window's records, shaped, each with its source position, in order of arrival,
        until the window's end; tees each as it arrived into the window's capture. Ends
        quietly on the source's failure (the error kept for the window), so the store seals
        what was written. Progress is reported by the records' stamps, at most once a
        ``progress_s``."""
        reported = window.opened
        while True:
            try:
                event = arrivals.peek()
            except KeyboardInterrupt:
                if arrivals.interrupt():  # a second: seal now
                    window.end = _End("stopped", self._clock.monotonic())
                    return
                continue
            if isinstance(event, _Page):
                arrivals.pop()
                line = {"page": event.index, "token": event.token}
                self._archives.append_sidecar(window.archive, output, "pages", line)
                continue
            if not isinstance(event, _Arrival):
                arrivals.pop()
                window.end = event
                return
            arrivals.pop()
            raw = json.loads(event.line)
            if captures is not None:
                at = json.dumps(event.stamp - window.opened)
                frame = b'{"n":%d,"at":%s,"record":%s}\n' % (event.ordinal, at.encode(), event.line)
                captures.tee(output, frame)
            try:
                record = normalize(raw)
            except Exception as exc:  # a transform that failed: the stream ends here
                window.error = exc
                self._halt(window, arrivals)
                window.end = _End(None, self._clock.monotonic(), exc)
                return
            if POSITION_FIELD in raw:
                record = {**record, POSITION_FIELD: raw[POSITION_FIELD]}
            window.records += 1  # handed to the store; its seal counts what it kept
            yield record
            if event.stamp - reported >= self._progress_s:
                time.pin(event.stamp)
                dropped = arrivals.dropped(window.index)
                telemetry.progress(span, {"recon.records": window.records,
                                          "recon.dropped": dropped})  # fmt: skip
                reported = event.stamp

    def _seal(
        self,
        plan: StreamPlan,
        window: _Window,
        table: TableOutcome,
        ledger: _Ledger,
        dropped: int,
        error: BaseException | None,
        kind: ErrorKind | None,
        wall: Callable[[float], datetime],
        meta: dict[str, Any],
        captures: CaptureLog | None,
    ) -> WindowOutcome:
        """The window's ``meta.yaml``, written; marked complete unless its source failed."""
        closed = _closed(window)
        closed_by: ClosedBy = "error" if error is not None else _reason(window.end)
        opened_at, closed_at = wall(window.opened).isoformat(), wall(closed).isoformat()
        if error is not None:
            fix = error.fix if isinstance(error, ReconError) else None
            assert kind is not None
            table = table.model_copy(
                update={"status": "error", "error": TableError(kind=kind, message=str(error),
                                                               fix=fix)}
            )  # fmt: skip
        table = table.model_copy(
            update={"records": window.records, "seconds": round(closed - window.opened, 2)}
        )
        reproducible = captures is not None and dropped == 0
        if captures is not None:
            captures.finalize_stream(
                table.name,
                source=table.source,
                kind="stream",
                request={"stream": plan.name, "window": window.index},
                status=None,
                content_type="application/x-ndjson",
            )
        source = {
            "source": table.source,
            "output": table.name,
            "first": window.first,
            "last": window.last,
            "records": window.records,
            "dropped": dropped,
            "gaps": [gap.document() for gap in ledger.gaps],
            "arrivals": None
            if ledger.first is None
            else {"first": ledger.first, "last": ledger.last},
        }

        def document(table: TableOutcome, ended: Literal["finished", "stopped"] | None) -> Any:
            return {
                "format_version": META_FORMAT_VERSION,
                "recon_version": self._recon_version,
                "config_sha256": meta["config_sha256"],
                "started_at": opened_at,
                "timestamp": closed_at,
                "preserve_raw": meta["preserve_raw"],
                "resolved": meta["resolved"],
                # How the window's writer ended it: stopped on request, as a survey says;
                # null while it compares, read as crashed if it is never written again.
                "ended": ended,
                "tables": [table.document()],
                "stream": {
                    "name": plan.name,
                    "index": window.index,
                    "previous": window.previous,
                    "opened_at": opened_at,
                    "closed_at": closed_at,
                    "closed_by": closed_by,
                    "window": plan.window.model_dump(exclude_none=True),
                    "sources": [source],
                    "reproducible": reproducible,
                },
            }

        spec = plan.entry.changes
        if spec is not None and self._changes is not None:
            # Against what the chain has told, never a delete (ruling C7); a window whose
            # source failed holds records too, and tells them (Dijkstra F1). Its summary is
            # in meta.yaml before its first row can be read (F3).
            try:
                table = self._changes.window(
                    window.archive, plan.name, window.previous, table, spec,
                    plan.entry.normalize,
                    pending=lambda told: self._archives.write_meta(
                        window.archive, document(told, None)),
                )  # fmt: skip
            except Exception as exc:  # the window's changes fail, never the window (F6)
                warning = changes_failed(plan.at, table.name, exc)
                self.warnings = [w for w in self.warnings if w.kind != warning.kind] + [warning]
                table = failed(table, exc)
            for output, unclosed in self._changes.unclosed():
                warning = join_unclosed(plan.at, output, unclosed)
                self.warnings = [w for w in self.warnings if w.kind != warning.kind] + [warning]
        self._archives.write_meta(
            window.archive, document(table, "stopped" if closed_by == "stopped" else "finished")
        )
        if error is None:
            self._archives.mark_complete(window.archive)
        return WindowOutcome(
            stream=plan.name,
            index=window.index,
            id=window.id,
            path=str(window.archive),
            opened_at=opened_at,
            closed_at=closed_at,
            closed_by=closed_by,
            dropped=dropped,
            high_water=ledger.high_water,
            table=table,
        )

    def _resume(self, mission_dir: Path, plan: StreamPlan) -> _Start:
        """Where this run begins: after the stream's last window, resuming its source after
        the last position kept (a crawl's from :func:`crawl_resume`); a fresh stream at
        window 0."""
        windows = self._archives.archives(mission_dir, stream=plan.name)
        if not windows:
            return _Start(0, None, None, None)
        last = windows[-1]
        index = (window_index(last.name) or 0) + 1
        stream = _stream_meta(self._archives.read_meta(last))
        if stream is not None:  # sealed: its meta carries the position to go on from
            after, since = _carried(stream), _time(stream.get("closed_at"))
        else:  # its writer died: the last whole record it wrote says
            record = self._archives.last_record(last, plan.entry.name)
            after = record.get(POSITION_FIELD) if record is not None else None
            if after is None and len(windows) > 1:
                before = _stream_meta(self._archives.read_meta(windows[-2]))
                after = _carried(before) if before is not None else None
            since = None
        carried, again = after, None
        if plan.entry.paginate is not None:
            resume = crawl_resume(self._archives, windows, plan.entry.name)
            after = None if resume is None else resume.document()
            again = None if resume is None or resume.i < 0 else resume.page
        # Every restart is a stretch no process read, recorded in the chain: resumed from a
        # position when there is one (and a source that does not honour it shows where).
        gap = Gap(since, self._clock.now(), resumed=after is not None)
        return _Start(index, last.name, after, gap, carried, again)

    def _retain(
        self, mission_dir: Path, plan: StreamPlan, sealed: WindowOutcome, sizes: dict[str, int]
    ) -> None:
        """Retire the sealed windows ``plan.retain`` does not keep, oldest first, once
        ``sealed`` is: each that ``windows`` leaves out, that closed more than ``seconds``
        before it, or that the newest windows' ``bytes`` leave no room for. The newest is
        always kept, and a window not sealed is never retired. Each costs the windows the
        rules look at; the sizes are read once a run."""
        rule = plan.retain
        if isinstance(rule, str):
            return
        windows = self._archives.archives(mission_dir, stream=plan.name)
        now = datetime.fromisoformat(sealed.closed_at)
        going = windows_going(self._archives, windows, rule, now, sizes)
        held = plan.held() if plan.held is not None and going else set()
        for window in windows[:-1]:
            if window.name not in going or window.name in held:
                continue
            meta = self._archives.read_meta(window)
            stream = _stream_meta(meta)
            if stream is None or self._archives.state(window) == "writing":
                continue  # never sealed: kept, as what a dead writer left
            [source] = stream.get("sources") or [{}]
            tombstone = {
                "id": window.name,
                "index": stream.get("index"),
                "opened_at": stream.get("opened_at"),
                "closed_at": stream.get("closed_at"),
                "closed_by": stream.get("closed_by"),
                "records": source.get("records"),
                "dropped": source.get("dropped"),
                "retired_at": now.isoformat(),
                "rule": rule.model_dump(exclude_none=True),
            }
            self._archives.retire(window, tombstone)
            sizes.pop(window.name, None)

    def _warn_dropped(self, plan: StreamPlan, causes: dict[str, int], emit: EventCallback) -> None:
        """One ``records_dropped`` warning for the run for each cause a window's drops had,
        at the path of the key that governs it, its counts kept current: ``overflow`` (a full
        buffer, ``buffer``), ``oversize`` (``buffer.max_record_bytes``) and ``halted`` (what
        waited when the stream stopped at once: the entry itself, no key to raise)."""
        for cause, dropped in causes.items():
            if not dropped:
                continue
            windows, total = self._dropped.get(cause, (0, 0))
            windows, total = windows + 1, total + dropped
            self._dropped[cause] = (windows, total)
            path, why, fix = _DROP_CAUSES[cause]
            warning = Problem(
                path.format(at=plan.at),
                f"stream {plan.name}: {total} record(s) dropped in {windows} window(s): "
                f"{why.format(buffer=plan.buffer, overflow=plan.overflow)}; each window's "
                "meta.yaml counts its own",
                kind="records_dropped",
                fix=fix,
                got=total,
            )
            self.warnings = [
                w for w in self.warnings if (w.kind, w.path) != (warning.kind, warning.path)
            ] + [warning]
            emit({"kind": "table_warning", "name": plan.entry.name, "reason": str(warning),
                  "fix": warning.fix})  # fmt: skip

    def _warn(self, plan: StreamPlan, warning: Problem, emit: EventCallback) -> None:
        """A warning of the run about its crawl, one of each kind, the latest kept."""
        self.warnings = [w for w in self.warnings if w.kind != warning.kind] + [warning]
        emit({"kind": "table_warning", "name": plan.entry.name, "reason": str(warning),
              "fix": warning.fix})  # fmt: skip

    def _warn_stuck(self, plan: StreamPlan) -> None:
        """One ``source_stuck`` warning, whose fix says whose the type is: recon's own is a
        bug in recon; another's is its package's, to report there or upgrade."""
        kind = plan.entry.type
        if plan.package == "recon":
            whose = (f"collector type {kind} is recon's own: report this as a bug in recon, "
                     "with the stream's entry")  # fmt: skip
        else:
            package = plan.package or f"the package that registers collector type {kind}"
            whose = (f"report it to {package}, or upgrade it: its type's close() should end a "
                     "read in progress, or the read should time out")  # fmt: skip
        self.warnings.append(
            Problem(
                "",
                f"stream {plan.name}: its source had not returned its next record "
                f"{self._stop_s:g}s after the stream stopped and its collector was closed; its "
                "thread is left until it does, and what it returns is not kept",
                kind="source_stuck",
                fix=f"the windows are whole; {whose}",
                got=kind,
            )
        )


#: Each cause of a stream's drops: the path its ``records_dropped`` warning is at (``at`` is
#: the entry's), why the records went, and the fix, which names the key to change.
_DROP_CAUSES: dict[str, tuple[str, str, str]] = {
    "overflow": (
        "{at}.buffer",
        "they arrived faster than the windows were written, and the buffer was full "
        "({buffer.records} records or {buffer.bytes} bytes; overflow: {overflow})",
        "raise buffer.records and buffer.bytes; for a source recon reads at its own pace, "
        "overflow: block waits instead of dropping",
    ),
    "oversize": (
        "{at}.buffer.max_record_bytes",
        "each was larger than buffer.max_record_bytes ({buffer.max_record_bytes})",
        "raise buffer.max_record_bytes above the largest record (no higher than buffer.bytes)",
    ),
    "halted": (
        "{at}",
        "they waited when the stream stopped at once: a second stop, or its source or store failed",
        "stop a stream once: a single Ctrl-C or SIGTERM seals the window once what waits is "
        "written; if a window failed, its meta.yaml has the error",
    ),
}


def crawl_resume(archives: ArchiveStore, windows: list[Path], output: str) -> Resume | None:
    """Where a stream's crawl goes on (ruling P4): the last page its windows' page logs name,
    newest window first, asked for again by its token, and the last record kept from that
    page, if one was. None when the crawl ended (its last window sealed ``eof``: the next run
    crawls again from its first page) or no page log is left (``retain:`` removed them), so
    the crawl begins again; a window sealed ``eof`` ends the search, as what is before it is
    a crawl that ended."""
    mark: dict[str, Any] | None = None
    record: dict[str, Any] | None = None
    for window in reversed(windows):
        stream = _stream_meta(archives.read_meta(window))
        if stream is not None and stream.get("closed_by") == "eof":
            break
        if record is None:
            record = archives.last_record(window, output)
        if mark is None:
            for line in archives.read_sidecar(window, output, "pages") or ():
                mark = line
        if mark is not None and record is not None:
            break
    if mark is None or not isinstance(mark.get("page"), int):
        return None
    page, token = mark["page"], mark.get("token")
    at = record.get(POSITION_FIELD) if record is not None else None
    if record is not None and isinstance(at, dict) and at.get("page") == page:
        i = at.get("i")
        if isinstance(i, int) and i >= 0:
            return Resume(page, token, i, content(record))
    return Resume(page, token)


def _moved(plan: StreamPlan, what: str, page: int, by: int | None) -> Problem:
    """The warning for a resumed crawl whose page moved under it: its token refused, so the
    crawl began again (``restarted``), or its last record kept found ``by`` records off, or
    not found (``shifted``)."""
    at = f"{plan.at}.paginate"
    if what == "restarted":
        return Problem(
            at,
            f"stream {plan.name}: the source refused the token of page {page}, where the crawl "
            "went on from (an expired cursor, or a page gone), so the crawl began again from "
            "its first page: what it reads again is beside what earlier windows hold, and the "
            "window's gaps say where",
            kind="paginate_restarted",
            fix="nothing kept is lost; set changes: {key: [...]} to be told only what changed, "
            "or crawl in one run (recon stream --windows N or --forever) so no token waits "
            "between runs to expire",
            got=page,
        )
    if by is None:
        return Problem(
            at,
            f"stream {plan.name}: the last record kept from page {page} was not found on it or "
            "the next page when the crawl went on (it changed, or moved further), so the "
            "crawl went on by its index: records about it may be missing or read twice, and "
            "the window's gaps say where",
            kind="paginate_shifted",
            fix="page by a cursor or a link where the API has one: a page number or an offset "
            "moves when records are added or removed ahead of it",
            got=None,
        )
    moved = "inserted" if by > 0 else "removed"
    return Problem(
        at,
        f"stream {plan.name}: page {page} moved since the crawl left off: its last record "
        f"kept was {abs(by)} record(s) {'later' if by > 0 else 'earlier'}, {moved} ahead of it, "
        "found by its content, so the crawl went on after it and kept nothing twice"
        + (
            ": those inserted are not in this crawl, and the next crawl reads them"
            if by > 0
            else ""
        ),
        kind="paginate_shifted",
        fix="page by a cursor or a link where the API has one: a page number or an offset "
        "moves when records are added or removed ahead of it",
        got=by,
    )


def _pump(
    collect: Callable[[Any], Iterator[dict[str, Any]]], after: Any, arrivals: Arrivals
) -> None:
    """The pump's thread: the source's records into the buffer, until it ends, fails, or the
    stream stops taking them."""
    try:
        records = collect(after)
        try:
            for record in records:
                if not arrivals.put(json.dumps(record, default=str).encode()):
                    return
        finally:
            close = getattr(records, "close", None)
            if close is not None:
                close()
    except BaseException as exc:  # whatever ends the source's thread ends the stream
        arrivals.end(exc)
        return
    arrivals.end()


def _tick(clock: Clock, arrivals: Arrivals) -> None:
    """The ticker's thread: wake the buffer whenever the clock reaches the open window's
    time, until the stream ends. It waits on the clock port, so a test's clock decides it,
    and in slices of at most :data:`TICK_S`, looking between, so a stream that ends leaves
    it asleep no longer than that. When it wakes changes no window: each closes at its
    spec's time, whatever the time the buffer is told."""
    while (close := arrivals.close()) is not None:
        left = close - clock.monotonic()
        if left > 0:
            clock.sleep(min(left, TICK_S))
        else:
            arrivals.advance()


class _WindowTime:
    """The clock a window's telemetry reads: the window's own times (its open, the arrival of
    the record a progress event follows, its close), pinned by the writer, so its event log is
    a function of what arrived, not of when the writer's thread got to it."""

    def __init__(self, wall: Callable[[float], datetime], at: float) -> None:
        self._wall = wall
        self._at = at

    def pin(self, at: float) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._wall(self._at)

    def monotonic(self) -> float:
        return self._at

    def sleep(self, seconds: float) -> None:
        self._at += seconds


def _closed(window: _Window) -> float:
    """When the window closed, on the clock's monotonic time: its end's stamp."""
    end = window.end
    if isinstance(end, WindowEnd):
        return end.closed
    return end.stamp if end is not None else window.opened


def _reason(end: WindowEnd | _End | None) -> SealReason:
    if isinstance(end, WindowEnd):
        return end.reason
    return end.reason if end is not None and end.reason is not None else "eof"


def _stream_meta(meta: Mapping[str, Any]) -> dict[str, Any] | None:
    stream = meta.get("stream")
    return stream if isinstance(stream, dict) else None


def _carried(stream: Mapping[str, Any]) -> Any:
    """The position a window's source went on from: its last record's, or the one carried
    through it when it had none."""
    sources = stream.get("sources")
    if isinstance(sources, list) and sources and isinstance(sources[0], dict):
        return sources[0].get("last")
    return None


def _time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def windows_going(
    archives: ArchiveStore,
    windows: list[Path],
    rule: RetainSpec,
    now: datetime,
    sizes: dict[str, int],
) -> set[str]:
    """The windows of a stream (oldest first) ``rule`` lets go at ``now``: each that
    ``archives`` leaves out, that closed more than ``seconds`` before it, or that the newest
    windows' ``bytes`` leave no room for. Each window's size is read once into ``sizes``."""
    going: set[str] = set()
    if rule.archives is not None:
        going.update(window.name for window in windows[: -rule.archives])
    if rule.seconds is not None:
        cutoff = now - timedelta(seconds=rule.seconds)
        for window, after in zip(windows, windows[1:], strict=False):
            closed = window_opened(after.name)  # a window closes as the next opens
            if closed is not None and closed < cutoff:
                going.add(window.name)
    if rule.bytes is not None:
        held = 0
        for at, window in enumerate(reversed(windows)):
            if window.name not in sizes:
                sizes[window.name] = archives.size(window)
            held += sizes[window.name]
            if held > rule.bytes and at > 0:
                going.update(w.name for w in windows[: len(windows) - at])
                break
    return going

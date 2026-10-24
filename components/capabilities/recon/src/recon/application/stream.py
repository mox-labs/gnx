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
``records`` or ``bytes``, at the stream's ``until``, when its source ends (``eof``) or when a
signal stops the stream; it is then sealed ``ok`` with that reason. A source that fails seals
its window ``error`` and **keeps its records**: what a source sent cannot be fetched again.

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
that has positions. For a source that has none, the stretch between is recorded as a gap.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from recon.application.survey import PROGRESS_S
from recon.domain.archives import META_FORMAT_VERSION
from recon.domain.documents import TableError, TableOutcome, WindowOutcome
from recon.domain.errors import ErrorKind, Problem, ReconError
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

    from recon.domain.archives import ArchiveStore
    from recon.domain.capture import CaptureLog
    from recon.domain.clock import Clock
    from recon.domain.models import CollectorEntry, SourceEntry
    from recon.domain.stream import (
        BufferSpec,
        ClosedBy,
        Overflow,
        Retain,
        SealReason,
        WindowSpec,
    )
    from recon.domain.telemetry import EventCallback, Span, Telemetry

#: By default, the seconds the end of a stream waits for its source's thread to stop, once
#: its collector is closed.
STOP_S = 5.0

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
    output) and its source, and the effective window, buffer and overflow."""

    name: str
    at: str
    entry: CollectorEntry
    source: SourceEntry | None
    window: WindowSpec
    buffer: BufferSpec
    overflow: Overflow
    columns: tuple[str, ...] = ()
    retain: Retain = "all"


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


# --- the buffer ------------------------------------------------------------------------------


@dataclass
class _Ledger:
    """What a window's arrivals came to besides its records: the drops, the most records that
    waited, the gaps its source reported, and the order of its first and last admitted."""

    dropped: int = 0
    high_water: int = 0
    gaps: list[Gap] = field(default_factory=list)
    first: int | None = None
    last: int | None = None


@dataclass(frozen=True)
class _Arrival:
    """One record admitted: its order of arrival, its stamp, its window, and the record as its
    collector gave it, as a JSON line's bytes (which is what the buffer's bounds count)."""

    ordinal: int
    stamp: float
    window: int
    line: bytes


@dataclass(frozen=True)
class _End:
    """The stream's last event: its source ended (``eof``), a signal stopped it (``signal``),
    or its source failed (``error``)."""

    reason: SealReason | None
    stamp: float
    error: BaseException | None = None


type _Event = _Arrival | WindowEnd | _End


class Arrivals:
    """The bounded buffer between a stream's source and its windows, and the windowing of what
    it admits. Thread-safe: the pump puts, the ticker advances, the writer takes, each under
    one lock, and every stamp is read from the clock under it, so the order the writer takes
    events in is the order of their stamps."""

    def __init__(
        self, clock: Clock, windowing: Windowing, buffer: BufferSpec, overflow: Overflow
    ) -> None:
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

    # --- the pump's side ---

    def put(self, line: bytes) -> bool:
        """Admit a record, or drop it; False once the stream has ended, when the pump stops."""
        with self._cond:
            if not self._advance():
                return False
            if len(line) > self._buffer.max_record_bytes:
                self._ledger(self._windowing.index).dropped += 1
                return True
            if self._overflow == "block":
                while self._admitting and self._full():
                    self._cond.wait()
                if not self._advance():
                    return False
            elif self._full():
                if self._overflow == "drop_newest":
                    self._ledger(self._windowing.index).dropped += 1
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

    def end(self, error: BaseException | None = None) -> None:
        """The source ended, or failed with ``error``: the stream ends after what waits. Said
        after the stream has ended (a source its closing broke off), it is not heard."""
        with self._cond:
            if self._admitting:
                self._admitting = False
                reason: SealReason | None = "eof" if error is None else None
                self._events.append(_End(reason, self._clock.monotonic(), error))
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

    def holds(self, event: _Event) -> bool:
        """Whether ``event`` still waits: an interrupt may have come before or after it was
        popped, and only the buffer can say which."""
        with self._cond:
            return any(waiting is event for waiting in self._events)

    def stop(self) -> None:
        """Stop the stream, as a signal does: nothing more is admitted, and the stream ends
        once what waits is written."""
        with self._cond:
            if self._admitting:
                self._admitting = False
                self._events.append(_End("signal", self._clock.monotonic()))
            self._cond.notify_all()

    def abandon(self, into: int | None = None) -> None:
        """Stop the stream at once, as a second signal or a failure does: nothing more is
        admitted or written, and what waits is dropped, its stream's end the one event left.
        With ``into``, the window being written (or about to be), every record that waited
        is counted dropped there, and so is what later windows that will never be written
        counted (their drops, their gaps), so nothing admitted goes uncounted."""
        with self._cond:
            self._admitting = False
            if into is not None:
                ledger = self._ledger(into)
                ledger.dropped += sum(1 for e in self._events if isinstance(e, _Arrival))
                for later in sorted(w for w in self._ledgers if w > into):
                    gone = self._ledgers.pop(later)
                    ledger.dropped += gone.dropped
                    ledger.gaps += gone.gaps
            self._events.clear()
            self._events.append(_End("signal", self._clock.monotonic()))
            self._records = self._bytes = 0
            self._cond.notify_all()

    def ledger(self, window: int) -> _Ledger:
        """What window ``window``'s arrivals came to, taken out: called once it is sealed."""
        with self._cond:
            return self._ledgers.pop(window, _Ledger())

    def dropped(self, window: int) -> int:
        with self._cond:
            return self._ledgers[window].dropped if window in self._ledgers else 0

    def noted(self, window: int) -> bool:
        """Whether a drop or a gap was counted in window ``window``."""
        with self._cond:
            ledger = self._ledgers.get(window)
            return ledger is not None and bool(ledger.dropped or ledger.gaps)

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
                self._ledger(event.window).dropped += 1
                return

    def _ledger(self, window: int) -> _Ledger:
        return self._ledgers.setdefault(window, _Ledger())


class StreamGaps:
    """The :class:`~recon.domain.stream.GapLog` a stream's collector is handed
    (``CollectorContext.gaps``): what it says goes to the window being filled."""

    def __init__(self) -> None:
        self._to: Arrivals | None = None

    def gap(self, gap: Gap) -> None:
        to = self._to
        if to is not None:
            to.gap(gap)


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
    #: The arrival the writer holds between taking it from the buffer and handing it to the
    #: store: dropped, if the window ends before it is handed on.
    pending: _Arrival | None = None
    dropped_late: int = 0
    frames: bool = True


@dataclass(frozen=True)
class _Start:
    """Where a run of a stream begins: the index of its first window, the window it follows,
    the position its source resumes after, and the gap since that window."""

    index: int
    previous: str | None
    after: Any
    gap: Gap | None


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
    ) -> None:
        self._archives = archives
        self._clock = clock
        self._recon_version = recon_version
        self._telemetry = telemetry
        self._progress_s = progress_s
        self._stop_s = stop_s
        self.warnings: list[Problem] = []
        self._overflowed = (0, 0)  # windows that dropped records, and how many they dropped
        #: A first interrupt was had in this run: a second one stops it at once.
        self._signalled = False

    def run(
        self,
        mission_dir: Path,
        plan: StreamPlan,
        collect: Callable[[Any], Iterator[dict[str, Any]]],
        normalize: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        close: Callable[[], object],
        gaps: StreamGaps | None = None,
        windows: int | None = None,
        until: datetime | None = None,
        preserve_raw: bool = False,
        config_sha256: str | None = None,
        resolved: Mapping[str, Any] | None = None,
        on_window: Callable[[WindowOutcome], None] | None = None,
    ) -> StreamRun:
        """Run the stream until it ends: after ``windows`` windows, at ``until``, when its
        source ends or fails, or when the caller's thread is interrupted (``KeyboardInterrupt``:
        the window being written is sealed ``signal`` once what waits is written; a second
        interrupt seals it at once, what waits dropped). ``collect(after)`` starts the source
        after the position ``after`` (None: from its start); ``normalize`` shapes each record;
        ``close`` ends the collector, which ends a source that would not. Raises
        ConflictError when another writer holds the stream."""
        self._archives.hold_stream(mission_dir, plan.name)
        try:
            return self._run(
                mission_dir, plan, collect, normalize, close, gaps, windows, until,
                preserve_raw, config_sha256, dict(resolved or {}), on_window,
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
        windows: int | None,
        until: datetime | None,
        preserve_raw: bool,
        config_sha256: str | None,
        resolved: dict[str, Any],
        on_window: Callable[[WindowOutcome], None] | None,
    ) -> StreamRun:
        start = self._resume(mission_dir, plan)
        opened_wall, opened = self._clock.now(), self._clock.monotonic()

        def wall(stamp: float) -> datetime:
            return opened_wall + timedelta(seconds=stamp - opened)

        last = None if windows is None else start.index + windows - 1
        until_at = None if until is None else opened + (until - opened_wall).total_seconds()
        windowing = Windowing(plan.window, opened, until=until_at, index=start.index, last=last)
        arrivals = Arrivals(self._clock, windowing, plan.buffer, plan.overflow)
        if start.gap is not None:
            arrivals.gap(start.gap)
        if gaps is not None:
            gaps._to = arrivals
        pump = threading.Thread(
            target=_pump, args=(collect, start.after, arrivals), name="recon-stream-pump",
            daemon=True,
        )  # fmt: skip
        pump.start()
        if plan.window.seconds is not None or until_at is not None:
            threading.Thread(
                target=_tick, args=(self._clock, arrivals), name="recon-stream-ticker", daemon=True
            ).start()
        meta = {"config_sha256": config_sha256, "preserve_raw": preserve_raw, "resolved": resolved}
        sealed, records, dropped = 0, 0, 0
        sizes: dict[str, int] = {}  # each window's size, read once, for retain: {bytes}
        first: str | None = None
        outcome: WindowOutcome | None = None
        previous, carried = start.previous, start.after
        index, window_opened = start.index, opened
        self._signalled = False
        try:
            while True:
                event = self._next(arrivals, index)
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
            arrivals.abandon()
            close()
            pump.join(self._stop_s)
            if pump.is_alive():
                self._warn_stuck(plan)
        return StreamRun(sealed, records, dropped, first, outcome)

    def _next(self, arrivals: Arrivals, index: int) -> _Event:
        """The next event, waiting for it, left in the buffer: a first interrupt stops the
        stream, and the wait goes on for what was admitted before it; a second stops it at
        once, what waits counted dropped in window ``index``, the one about to open."""
        while True:
            try:
                return arrivals.peek()
            except KeyboardInterrupt:
                self._interrupted(arrivals, index)

    def _interrupted(self, arrivals: Arrivals, index: int) -> bool:
        """Answer an interrupt the writer had while waiting: the first stops the stream
        once what waits is written; a second stops it now (True), what waits counted dropped
        in window ``index``."""
        if self._signalled:
            arrivals.abandon(into=index)
            return True
        self._signalled = True
        arrivals.stop()
        return False

    def _halt(self, window: _Window, arrivals: Arrivals) -> None:
        """Stop the stream now, in ``window``: the arrival the writer held, if the buffer no
        longer has it, and what waits are counted dropped in it."""
        held = window.pending
        if held is not None and not arrivals.holds(held):
            window.dropped_late += 1
        window.pending = None
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
                    window.end = window.end or _End("signal", self._clock.monotonic())
                except Exception as exc:  # the store failed: what was written is sealed
                    window.error = window.error or exc
                    self._halt(window, arrivals)
                    window.end = window.end or _End(None, self._clock.monotonic(), exc)
                ledger = arrivals.ledger(window.index)
                window_dropped = ledger.dropped + window.dropped_late
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
            if window_dropped:
                self._warn_overflow(plan, window_dropped, emit)
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
                if self._interrupted(arrivals, window.index):  # a second: seal now
                    window.end = _End("signal", self._clock.monotonic())
                    return
                continue
            if not isinstance(event, _Arrival):
                arrivals.pop()
                window.end = event
                return
            window.pending = event  # before it is popped: an interrupt may come between
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
                window.first = raw[POSITION_FIELD] if window.records == 0 else window.first
                window.last = raw[POSITION_FIELD]
            window.records += 1
            window.pending = None  # handed to the store: written, from here on
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
        document = {
            "format_version": META_FORMAT_VERSION,
            "recon_version": self._recon_version,
            "config_sha256": meta["config_sha256"],
            "started_at": opened_at,
            "timestamp": closed_at,
            "preserve_raw": meta["preserve_raw"],
            "resolved": meta["resolved"],
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
        self._archives.write_meta(window.archive, document)
        if error is None:
            self._archives.mark_complete(window.archive)
        return WindowOutcome(
            stream=plan.name,
            index=window.index,
            id=window.id,
            archive=str(window.archive),
            opened_at=opened_at,
            closed_at=closed_at,
            closed_by=closed_by,
            dropped=dropped,
            high_water=ledger.high_water,
            table=table,
        )

    def _resume(self, mission_dir: Path, plan: StreamPlan) -> _Start:
        """Where this run begins: after the stream's last window, resuming its source after
        the last position kept; a fresh stream at window 0."""
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
        gap = None if after is not None else Gap(since, self._clock.now(), resumed=False)
        return _Start(index, last.name, after, gap)

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
        going: set[str] = set()
        if rule.windows is not None:
            going.update(window.name for window in windows[: -rule.windows])
        now = datetime.fromisoformat(sealed.closed_at)
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
                    sizes[window.name] = self._archives.size(window)
                held += sizes[window.name]
                if held > rule.bytes and at > 0:
                    going.update(w.name for w in windows[: len(windows) - at])
                    break
        for window in windows[:-1]:
            if window.name not in going:
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

    def _warn_overflow(self, plan: StreamPlan, dropped: int, emit: EventCallback) -> None:
        """One ``stream_overflow`` warning for the run, its counts kept current."""
        windows, total = self._overflowed
        self._overflowed = (windows + 1, total + dropped)
        warning = Problem(
            f"{plan.at}.buffer",
            f"stream {plan.name}: {total + dropped} record(s) dropped in {windows + 1} "
            "window(s): they arrived faster than the windows were written, or were larger "
            "than buffer.max_record_bytes; each window's meta.yaml counts its own",
            kind="stream_overflow",
            fix="raise buffer: records and bytes (or max_record_bytes), or set overflow: block "
            "for a source recon reads at its own pace",
            got=total + dropped,
        )
        self.warnings = [w for w in self.warnings if w.kind != "stream_overflow"] + [warning]
        emit({"kind": "table_warning", "name": plan.entry.name, "reason": str(warning),
              "fix": warning.fix})  # fmt: skip

    def _warn_stuck(self, plan: StreamPlan) -> None:
        self.warnings.append(
            Problem(
                "",
                f"stream {plan.name}: its source had not returned its next record "
                f"{self._stop_s:g}s after the stream stopped and its collector was closed; its "
                "thread is left until it does, and what it returns is not kept",
                kind="source_stuck",
                fix="the windows are whole; give the collector type a close() that ends a "
                "read in progress, or a timeout on it",
            )
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
    time, until the stream ends. It waits on the clock port, so a test's clock decides it."""
    while (close := arrivals.close()) is not None:
        left = close - clock.monotonic()
        if left > 0:
            clock.sleep(left)
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

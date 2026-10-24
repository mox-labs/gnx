"""A stream's run (rulings B1 to B7, validated as B10 asks): windows that end on time on a
quiet source, conservation of what arrives, overflow by policy, sealed windows that never
change, exact replay from captures, and the same archive from the same script twice. Every
test runs on a virtual clock its script moves, so nothing sleeps for the time it stands for."""

from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from recon.adapters._out.archive_files import ArchiveFiles
from recon.application.stream import Arrivals, Stream, StreamGaps, StreamPlan, StreamRun
from recon.composition import root
from recon.domain.models import CollectorEntry
from recon.domain.stream import (
    POSITION_FIELD,
    BufferSpec,
    Overflow,
    StreamStop,
    Windowing,
    WindowSpec,
)
from recon.domain.telemetry import Telemetry
from tests.recon_fakes import StalledArchives, VirtualClock

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator, Sequence

    from recon.domain.clock import Clock
    from recon.domain.documents import WindowOutcome
    from recon.domain.telemetry import EventCallback


class Script:
    """A source that gives each record at its time on the virtual clock, in order, then ends
    (``then="eof"``), fails (``"fail"``), or goes quiet (``"quiet"``) with the clock moved to
    ``quiet_to``, until it is closed."""

    def __init__(
        self,
        clock: VirtualClock,
        timeline: list[tuple[float, dict[str, Any]]],
        *,
        then: str = "eof",
        quiet_to: float = 0.0,
    ) -> None:
        self._clock = clock
        self._timeline = timeline
        self._then = then
        self._quiet_to = quiet_to
        self.closed = threading.Event()
        #: Set once the last record of the timeline has been taken from the source.
        self.done = threading.Event()
        self.after: Any = None
        #: How many records the source has handed out: the pump has put each before it took
        #: the next, so with ``given`` at n + 1 the first n are in the buffer or dropped.
        self.given = 0

    def records(self, after: Any) -> Iterator[dict[str, Any]]:
        self.after = after
        for at, record in self._timeline:
            self._clock.set(at)
            self.given += 1
            yield record
        self.done.set()
        if self._then == "fail":
            raise RuntimeError("the source broke off")
        if self._then == "quiet":
            self._clock.set(self._quiet_to)
            self.closed.wait(10)

    def close(self) -> None:
        self.closed.set()


def _plan(
    window: WindowSpec,
    *,
    buffer: BufferSpec | None = None,
    overflow: Overflow = "block",
    name: str = "s",
) -> StreamPlan:
    return StreamPlan(
        name=name,
        at="collectors.0",
        entry=CollectorEntry(name=name, type="fake"),
        source=None,
        window=window,
        buffer=buffer or BufferSpec(),
        overflow=overflow,
    )


def _telemetry(store: ArchiveFiles, events: list[dict[str, Any]]) -> Any:
    def open_window(archive: Path, clock: Clock) -> Any:
        @contextmanager
        def window() -> Iterator[tuple[Telemetry, EventCallback]]:
            telemetry = Telemetry([store.event_log(archive)], clock)
            try:
                yield telemetry, events.append
            finally:
                telemetry.close()

        return window()

    return open_window


def _run(
    project: Path,
    clock: VirtualClock,
    plan: StreamPlan,
    source: Script,
    *,
    store: ArchiveFiles | None = None,
    windows: int | None = None,
    preserve_raw: bool = False,
    normalize: Callable[[dict[str, Any]], dict[str, Any]] = dict,
    stop: StreamStop | None = None,
    on_window: Callable[[WindowOutcome], None] | None = None,
    gaps: StreamGaps | None = None,
) -> tuple[StreamRun, list[WindowOutcome], list[dict[str, Any]]]:
    store = store or ArchiveFiles(clock)
    events: list[dict[str, Any]] = []
    outcomes: list[WindowOutcome] = []

    def sealed(outcome: WindowOutcome) -> None:
        outcomes.append(outcome)
        if on_window is not None:
            on_window(outcome)

    stream = Stream(store, clock, "test", telemetry=_telemetry(store, events))
    run = stream.run(
        project,
        plan,
        source.records,
        normalize,
        close=source.close,
        windows=windows,
        preserve_raw=preserve_raw,
        on_window=sealed,
        stop=stop,
        gaps=gaps,
    )
    return run, outcomes, events


def _rows(outcome: WindowOutcome) -> list[dict[str, Any]]:
    table = Path(outcome.archive) / outcome.table.file
    rows = [json.loads(line) for line in table.read_text().splitlines()]
    return [] if rows == [{"_empty": True}] else rows


def _meta(outcome: WindowOutcome) -> dict[str, Any]:
    meta = yaml.safe_load((Path(outcome.archive) / "meta.yaml").read_text())
    assert isinstance(meta, dict)
    return meta


def _seq(n: int, *, every: float = 1.0, start: float = 0.0) -> list[tuple[float, dict[str, Any]]]:
    return [(start + i * every, {"seq": i}) for i in range(n)]


class TestWindowsEndOnTheClock:
    def test_the_boundaries_are_half_open_through_the_writer(self, tmp_path: Path) -> None:
        """B10 #1: 0 / 59.999 / 60.0 / 60.001 against a 60 s window."""
        clock = VirtualClock()
        stamps = [0.0, 59.999, 60.0, 60.001]
        source = Script(clock, [(t, {"at": t}) for t in stamps])

        run, outcomes, _ = _run(tmp_path, clock, _plan(WindowSpec(seconds=60)), source)

        assert [[r["at"] for r in _rows(o)] for o in outcomes] == [[0.0, 59.999], [60.0, 60.001]]
        assert [o.closed_by for o in outcomes] == ["window", "eof"]
        assert run.windows == 2

    @pytest.mark.parametrize(("n", "k"), [(10, 3), (9, 3)])
    def test_count_windows_give_the_ceiling_of_n_over_k(
        self, tmp_path: Path, n: int, k: int
    ) -> None:
        clock = VirtualClock()

        _, outcomes, _ = _run(tmp_path, clock, _plan(WindowSpec(records=k)), Script(clock, _seq(n)))

        assert len(outcomes) == -(-n // k)
        assert [len(_rows(o)) for o in outcomes] == [
            min(k, n - i * k) for i in range(len(outcomes))
        ]

    def test_a_quiet_source_seals_on_time(self, tmp_path: Path) -> None:
        """B10 #10: one record, then nothing; the window still ends at 60 s, by its time."""
        clock = VirtualClock()
        source = Script(clock, [(1.0, {"seq": 0})], then="quiet", quiet_to=61.0)

        _, [window], _ = _run(tmp_path, clock, _plan(WindowSpec(seconds=60)), source, windows=1)

        assert window.closed_by == "window"
        assert window.closed_at == "2026-01-02T03:05:05+00:00", "opened 03:04:05, plus 60 s"
        assert _rows(window) == [{"seq": 0}]
        assert source.closed.is_set(), "the source was closed once the stream had its window"

    def test_each_quiet_window_is_sealed_when_its_time_comes(self, tmp_path: Path) -> None:
        """The writer is woken at each window's close, not only at the stream's last: a
        quiet stretch seals its windows one by one, as the clock reaches each."""
        clock = VirtualClock()
        store = ArchiveFiles(clock)
        source = Script(clock, [(1.0, {"seq": 0})], then="quiet", quiet_to=15.0)
        result: list[Any] = []
        writer = threading.Thread(
            target=lambda: result.append(
                _run(
                    tmp_path, clock, _plan(WindowSpec(seconds=10)), source, store=store, windows=3
                )  # fmt: skip
            )
        )
        writer.start()

        def sealed(count: int) -> bool:
            windows = store.archives(tmp_path, stream="s")
            return sum(store.state(w) == "complete" for w in windows) >= count

        _wait_for(lambda: sealed(1))  # at 15 s: window 0 is sealed, the clock no further
        clock.set(25.0)
        _wait_for(lambda: sealed(2))
        clock.set(35.0)
        writer.join(10)
        _, outcomes, _ = result[0]
        assert [(o.index, o.closed_by, o.table.records) for o in outcomes] == [
            (0, "window", 1),
            (1, "window", 0),
            (2, "window", 0),
        ]

    def test_a_window_that_ends_is_ok_not_transient(self, tmp_path: Path) -> None:
        """B10 #7: reaching a window's end is success; a deadline cut would be ``transient``."""
        clock = VirtualClock()

        _, outcomes, _ = _run(
            tmp_path, clock, _plan(WindowSpec(seconds=10)), Script(clock, _seq(25)), windows=2
        )

        assert [(o.table.status, o.closed_by, o.table.error) for o in outcomes] == [
            ("ok", "window", None),
            ("ok", "window", None),
        ]
        store = ArchiveFiles(clock)
        assert [store.state(Path(o.archive)) for o in outcomes] == ["complete", "complete"]
        assert [_meta(o)["tables"][0]["status"] for o in outcomes] == ["ok", "ok"]


class TestTheChain:
    def test_windows_chain_through_meta_stream(self, tmp_path: Path) -> None:
        clock = VirtualClock()
        records = [(float(i), {"seq": i, POSITION_FIELD: i * 10}) for i in range(7)]

        _, outcomes, _ = _run(tmp_path, clock, _plan(WindowSpec(records=3)), Script(clock, records))

        streams = [_meta(o)["stream"] for o in outcomes]
        assert [s["index"] for s in streams] == [0, 1, 2]
        assert [s["previous"] for s in streams] == [None, outcomes[0].id, outcomes[1].id]
        sources = [s["sources"][0] for s in streams]
        assert [(s["first"], s["last"]) for s in sources] == [(0, 20), (30, 50), (60, 60)]
        arrivals = [s["arrivals"] for s in sources]
        for before, after in zip(arrivals, arrivals[1:], strict=False):
            assert before["last"] + 1 == after["first"], "the chain has no hole"
        assert all(row[POSITION_FIELD] == row["seq"] * 10 for o in outcomes for row in _rows(o))

    def test_a_source_that_fails_seals_its_window_error_and_keeps_its_records(
        self, tmp_path: Path
    ) -> None:
        clock = VirtualClock()
        source = Script(clock, _seq(3), then="fail")

        run, [window], _ = _run(tmp_path, clock, _plan(WindowSpec(seconds=60)), source)

        assert (window.closed_by, window.table.status) == ("error", "error")
        assert window.table.error is not None
        assert window.table.error.kind == "run_failed"
        assert "broke off" in window.table.error.message
        assert _rows(window) == [{"seq": 0}, {"seq": 1}, {"seq": 2}], "push records are kept"
        assert ArchiveFiles(clock).state(Path(window.archive)) == "incomplete"
        assert run.last is not None and run.last.closed_by == "error"


class TestFlow:
    """B5 and B10 #2, #5: what arrives is written or dropped and counted, never lost."""

    def _burst(
        self, tmp_path: Path, overflow: Overflow, bound: int, extra: int
    ) -> tuple[list[int], WindowOutcome]:
        clock = VirtualClock()
        store = StalledArchives(clock)
        timeline = [(0.0, {"seq": i}) for i in range(bound + extra)]
        source = Script(clock, timeline)
        plan = _plan(WindowSpec(seconds=60), buffer=BufferSpec(records=bound), overflow=overflow)
        result: list[Any] = []
        writer = threading.Thread(
            target=lambda: result.append(_run(tmp_path, clock, plan, source, store=store))
        )
        writer.start()
        if overflow != "block":  # a blocked source cannot finish before the store goes
            assert source.done.wait(10)
        else:  # the pump holds the record after the bound's: the buffer is full
            _wait_for(lambda: source.given > bound)
        store.go.set()
        writer.join(10)
        _, [window], _ = result[0]
        return [row["seq"] for row in _rows(window)], window

    @pytest.mark.parametrize(
        ("overflow", "kept"),
        [
            ("drop_newest", list(range(8))),
            ("drop_oldest", list(range(5, 13))),
            ("block", list(range(13))),
        ],
    )
    def test_a_burst_past_the_bound_drops_by_the_policy(
        self, tmp_path: Path, overflow: Overflow, kept: list[int]
    ) -> None:
        """B10 #5: bound 8, a burst of 13 into a store that is not writing yet."""
        written, window = self._burst(tmp_path, overflow, 8, 5)

        assert written == kept
        assert window.dropped == 13 - len(kept)
        assert _meta(window)["stream"]["sources"][0]["dropped"] == window.dropped
        assert window.high_water == 8, "the buffer filled to its bound, and no further"

    def test_what_arrives_is_written_or_dropped_once_by_sequence_id(self, tmp_path: Path) -> None:
        """B10 #2: across windows and a burst, emitted = written + dropped; no id twice."""
        clock = VirtualClock()
        store = StalledArchives(clock)
        timeline = [(i * 0.5, {"seq": i}) for i in range(200)]
        source = Script(clock, timeline)
        plan = _plan(WindowSpec(seconds=7), buffer=BufferSpec(records=16), overflow="drop_newest")
        result: list[Any] = []
        writer = threading.Thread(
            target=lambda: result.append(_run(tmp_path, clock, plan, source, store=store))
        )
        writer.start()
        assert source.done.wait(10)
        store.go.set()
        writer.join(10)
        run, outcomes, _ = result[0]

        written = [row["seq"] for o in outcomes for row in _rows(o)]
        assert len(written) == len(set(written)), "no record is written twice"
        assert written == sorted(written), "in order of arrival"
        dropped = sum(o.dropped for o in outcomes)
        assert dropped > 0, "the burst overflowed: the check has something to check"
        assert len(written) + dropped == 200
        assert run.records + run.dropped == 200
        spans = [_meta(o)["stream"]["sources"][0]["arrivals"] for o in outcomes]
        admitted = [span for span in spans if span is not None]
        for o, span in zip(outcomes, spans, strict=True):
            if span is not None:  # drop_newest drops before admitting: admitted = written
                assert span["last"] - span["first"] + 1 == o.table.records
        for before, after in zip(admitted, admitted[1:], strict=False):
            assert before["last"] + 1 == after["first"]

    @pytest.mark.parametrize("overflow", ["drop_newest", "drop_oldest"])
    def test_each_window_writes_or_drops_exactly_what_was_stamped_in_it(
        self, tmp_path: Path, overflow: Overflow
    ) -> None:
        """Review item 13 (Ixian): conservation holds window by window, not only in total:
        what a window wrote and dropped is the records whose stamps fall in it, and each it
        wrote is one of those, whichever record the policy drops."""
        clock = VirtualClock()
        store = StalledArchives(clock)
        timeline = [(i * 0.5, {"seq": i}) for i in range(200)]
        source = Script(clock, timeline)
        plan = _plan(WindowSpec(seconds=7), buffer=BufferSpec(records=16), overflow=overflow)
        result: list[Any] = []
        writer = threading.Thread(
            target=lambda: result.append(_run(tmp_path, clock, plan, source, store=store))
        )
        writer.start()
        assert source.done.wait(10)
        store.go.set()
        writer.join(10)
        run, outcomes, _ = result[0]

        stamped: dict[int, list[int]] = {}
        for at, record in timeline:
            stamped.setdefault(int(at // 7), []).append(record["seq"])
        assert sum(o.dropped for o in outcomes) > 0, "the burst overflowed"
        assert [o.index for o in outcomes] == sorted(stamped)
        for o in outcomes:
            written = [row["seq"] for row in _rows(o)]
            assert o.table.records + o.dropped == len(stamped[o.index]), o.index
            assert set(written) <= set(stamped[o.index]), o.index
        assert run.records + run.dropped == 200

    def test_a_record_over_max_record_bytes_is_dropped_and_counted(self, tmp_path: Path) -> None:
        clock = VirtualClock()
        timeline: list[tuple[float, dict[str, Any]]] = [
            (0.0, {"seq": 0}),
            (1.0, {"seq": 1, "big": "x" * 500}),
            (2.0, {"seq": 2}),
        ]

        _, [window], _ = _run(
            tmp_path,
            clock,
            _plan(WindowSpec(seconds=60), buffer=BufferSpec(max_record_bytes=100)),
            Script(clock, timeline),
        )

        assert [r["seq"] for r in _rows(window)] == [0, 2]
        assert window.dropped == 1


class TestEveryRecordIsCountedWhenTheStreamBreaks:
    """Review (Dijkstra) of the window writer: when the stream ends early, each record it
    took is still written or counted dropped, once, and the drops later windows held are
    not lost with them."""

    def test_a_record_a_transform_fails_on_is_counted_dropped(self, tmp_path: Path) -> None:
        clock = VirtualClock()

        def shaping(record: dict[str, Any]) -> dict[str, Any]:
            if record["seq"] == 3:
                raise ValueError("cannot shape it")
            return record

        run, [window], _ = _run(
            tmp_path, clock, _plan(WindowSpec(seconds=60)), Script(clock, _seq(6)),
            normalize=shaping,
        )  # fmt: skip

        assert (window.closed_by, window.table.records) == ("error", 3)
        assert window.dropped == 3, "the one it failed on, and the two waiting behind it"
        assert run.records + run.dropped == 6

    def test_what_later_windows_held_is_counted_when_the_stream_breaks(
        self, tmp_path: Path
    ) -> None:
        clock = VirtualClock()
        store = StalledArchives(clock)
        timeline = [(i * 0.5, {"seq": i}) for i in range(40)]
        source = Script(clock, timeline)

        def shaping(record: dict[str, Any]) -> dict[str, Any]:
            if record["seq"] == 1:
                raise ValueError("cannot shape it")
            return record

        plan = _plan(WindowSpec(seconds=5), buffer=BufferSpec(records=8), overflow="drop_newest")
        result: list[Any] = []
        writer = threading.Thread(
            target=lambda: result.append(
                _run(tmp_path, clock, plan, source, store=store, normalize=shaping)
            )
        )
        writer.start()
        assert source.done.wait(10)
        store.go.set()
        writer.join(10)
        run, outcomes, _ = result[0]

        assert [o.closed_by for o in outcomes] == ["error"]
        assert run.records + run.dropped == 40, "the queued and the overflowed, all counted"

    def test_a_record_its_collector_drops_is_counted_in_its_window(self, tmp_path: Path) -> None:
        """Review item 7: a collector that drops a record rather than hold it (an ``sse``
        event over ``max_record_bytes``) says so, and the window counts it."""
        clock = VirtualClock()
        gaps = StreamGaps()

        class Drops(Script):
            def records(self, after: Any) -> Iterator[dict[str, Any]]:
                yield {"seq": 0}
                gaps.drop()
                yield {"seq": 2}

        run, [window], _ = _run(
            tmp_path, clock, _plan(WindowSpec(seconds=60)), Drops(clock, []), gaps=gaps,
            preserve_raw=True,
        )  # fmt: skip

        assert (window.table.records, window.dropped) == (2, 1)
        assert _meta(window)["stream"]["reproducible"] is False
        assert run.records + run.dropped == 3

    def test_a_source_that_dies_of_a_base_exception_ends_the_stream(self, tmp_path: Path) -> None:
        clock = VirtualClock()

        class Dies(Script):
            def records(self, after: Any) -> Iterator[dict[str, Any]]:
                yield {"seq": 0}
                raise SystemExit("the source's thread was told to exit")

        run, [window], _ = _run(tmp_path, clock, _plan(WindowSpec(records=10)), Dies(clock, []))

        assert window.closed_by == "error", "no ticker, no until: still it ends"
        assert run.records == 1


class TestAStopAsked:
    """Review item 6 (Erlang #3): a stop is asked of the stream (what ``recon stream``'s
    SIGINT and SIGTERM handlers do), never raised in the writer, so the first one drains
    however busy the writer is when it comes, and a second seals at once: before the stop
    object, the first Ctrl-C usually landed inside the store's write and halted at once."""

    def _stalled(
        self, tmp_path: Path, plan: StreamPlan, stop: StreamStop, **given: Any
    ) -> tuple[Script, StalledArchives, threading.Thread, list[Any]]:
        clock = VirtualClock()
        store = StalledArchives(clock)
        source = Script(clock, _seq(10), then="quiet", quiet_to=10.0)
        result: list[Any] = []
        writer = threading.Thread(
            target=lambda: result.append(
                _run(tmp_path, clock, plan, source, store=store, stop=stop, **given)
            )
        )
        writer.start()
        assert source.done.wait(10)
        return source, store, writer, result

    def test_the_first_stop_seals_the_window_once_what_waits_is_written(
        self, tmp_path: Path
    ) -> None:
        stop = StreamStop()
        plan = _plan(WindowSpec(seconds=60))
        _, store, writer, result = self._stalled(tmp_path, plan, stop)

        stop.ask()  # the writer is in the store's write, holding none of the ten
        _wait_for(lambda: stop.heard == 1)
        store.go.set()
        writer.join(10)
        run, [window], _ = result[0]

        assert (window.closed_by, window.table.records, window.dropped) == ("signal", 10, 0)
        assert run.records == 10

    def test_a_second_stop_seals_it_at_once_what_waits_counted_dropped(
        self, tmp_path: Path
    ) -> None:
        stop = StreamStop()
        plan = _plan(WindowSpec(seconds=60))
        _, store, writer, result = self._stalled(tmp_path, plan, stop)

        stop.ask()
        stop.ask()
        _wait_for(lambda: stop.heard == 2)
        store.go.set()
        writer.join(10)
        run, [window], _ = result[0]

        assert (window.closed_by, window.table.records, window.dropped) == ("signal", 0, 10)
        assert run.records + run.dropped == 10

    def test_a_second_stop_between_windows_counts_the_next_ones_records_in_it(
        self, tmp_path: Path
    ) -> None:
        """The first window sealed, the second's records waiting, and both stops asked before
        the writer opens it: the second window is sealed ``signal``, its records dropped."""
        clock = VirtualClock()
        stop = StreamStop()
        source = Script(clock, _seq(8), then="quiet", quiet_to=8.0)

        def between(outcome: WindowOutcome) -> None:
            if outcome.index == 0:
                assert source.done.wait(10), "the second window's three wait"
                stop.ask()
                stop.ask()
                _wait_for(lambda: stop.heard == 2)

        run, outcomes, _ = _run(
            tmp_path, clock, _plan(WindowSpec(records=5)), source, stop=stop, on_window=between
        )

        assert [(o.closed_by, o.table.records, o.dropped) for o in outcomes] == [
            ("records", 5, 0),
            ("signal", 0, 3),
        ]
        assert run.records + run.dropped == 8

    def test_a_stop_asked_before_the_stream_starts_stops_it_as_it_starts(
        self, tmp_path: Path
    ) -> None:
        clock = VirtualClock()
        stop = StreamStop()
        stop.ask()
        source = Script(clock, [], then="quiet", quiet_to=1.0)

        run, outcomes, _ = _run(tmp_path, clock, _plan(WindowSpec(seconds=60)), source, stop=stop)

        assert [o.closed_by for o in outcomes] == ["signal"]
        assert run.records == 0


class TestWhatTheEndOfARunDiscardsIsCounted:
    """Review item 20 (Erlang): the end of a run abandons the buffer without a window to count
    what it held in, so it says how much that was, and the run counts it dropped. Every way
    a run returns stops admitting first, so it is none there; it is the buffer's answer that
    is held to."""

    def test_abandon_says_what_it_discarded(self) -> None:
        clock = VirtualClock()
        arrivals = Arrivals(clock, Windowing(WindowSpec(records=2), 0.0), BufferSpec(), "block")
        for n in range(3):  # two in window 0, one in window 1
            arrivals.put(json.dumps({"seq": n}).encode())

        assert arrivals.abandon() == 3

    def test_abandon_into_a_window_discards_nothing_uncounted(self) -> None:
        clock = VirtualClock()
        arrivals = Arrivals(clock, Windowing(WindowSpec(records=2), 0.0), BufferSpec(), "block")
        for n in range(3):
            arrivals.put(json.dumps({"seq": n}).encode())

        assert arrivals.abandon(into=0) == 0
        assert arrivals.ledger(0).refused == 1, "window 1's record, counted in window 0"


class TestTheMetaIsTheSealedFile:
    """Review item 1 (Dijkstra F1, Erlang #2): a window's ``meta.yaml`` says what its sealed
    table holds, read from the table, so a store that fails mid-window leaves no claim of a
    record the file lacks, and a resume does not skip it."""

    def test_a_store_that_fails_mid_window_leaves_meta_equal_to_the_file(
        self, tmp_path: Path
    ) -> None:
        clock = VirtualClock()

        class Failing(ArchiveFiles):
            def write_table(
                self,
                archive: Path,
                output: str,
                records: Generator[dict[str, Any], None, None],
                *,
                columns: Sequence[str] = (),
                keep_on_error: bool = False,
            ) -> int:
                def failing() -> Generator[dict[str, Any], None, None]:
                    for n, record in enumerate(records):
                        if n == 3:  # the store takes the fourth, and fails on it
                            raise OSError("no space left on device")
                        yield record

                return super().write_table(
                    archive, output, failing(), columns=columns, keep_on_error=keep_on_error
                )

        timeline = [(float(i), {"seq": i, POSITION_FIELD: i}) for i in range(6)]
        run, [window], _ = _run(
            tmp_path, clock, _plan(WindowSpec(seconds=60)), Script(clock, timeline),
            store=Failing(clock),
        )  # fmt: skip

        rows = _rows(window)
        source = _meta(window)["stream"]["sources"][0]
        assert window.closed_by == "error"
        assert [r["seq"] for r in rows] == [0, 1, 2]
        assert (window.table.records, source["records"]) == (3, 3)
        assert _meta(window)["tables"][0]["records"] == 3
        assert (source["first"], source["last"]) == (0, 2), "a resume goes on after 2"
        assert window.dropped == 3 and run.records + run.dropped == 6


class TestSealedIsSealed:
    def test_a_sealed_window_never_changes_and_reads_sealed_only_after_its_end(
        self, tmp_path: Path
    ) -> None:
        """B10 #3: the digest of a sealed window holds while later windows are written; a
        reader is told it sealed only once its table ended."""
        clock = VirtualClock()
        store = ArchiveFiles(clock)
        seen: dict[str, Any] = {}
        digests: list[str] = []
        gate = threading.Event()

        def look() -> None:
            [first] = store.archives(tmp_path, stream="s")[:1]
            seen["while"] = store.read_since(first, None, limit=100, max_bytes=1 << 20)
            gate.set()

        class Watched(Script):
            def records(self, after: Any) -> Iterator[dict[str, Any]]:
                for at, record in self._timeline:
                    self._clock.set(at)
                    if at == 2.0:
                        _wait_for(lambda: bool(store.archives(tmp_path, stream="s")))
                        look()
                    if at == 30.0:
                        digests.append(_digest(_sealed_first()))
                    yield record

        def _sealed_first() -> Path:
            first = store.archives(tmp_path, stream="s")[0]
            _wait_for(lambda: store.state(first) == "complete")
            return first

        source = Watched(clock, _seq(40, every=1.0))
        _, outcomes, _ = _run(tmp_path, clock, _plan(WindowSpec(seconds=10)), source, store=store)

        assert gate.is_set()
        assert seen["while"].sealed == [], "a table being written is not sealed"
        assert digests == [_digest(Path(outcomes[0].archive))]
        after = store.read_since(Path(outcomes[0].archive), seen["while"].cursor, limit=100,
                                 max_bytes=1 << 20)  # fmt: skip
        assert after.sealed == ["s"]


class TestDeterminism:
    """B7, B10 #6 and #8."""

    def test_the_same_script_under_the_same_clock_writes_the_same_archive(
        self, tmp_path: Path
    ) -> None:
        timeline = [(i * 0.7, {"seq": i, POSITION_FIELD: i}) for i in range(60)]
        trees = []
        for run in ("a", "b"):
            clock = VirtualClock()
            project = tmp_path / run
            _run(project, clock, _plan(WindowSpec(seconds=9, records=11)), Script(clock, timeline))
            trees.append(_tree(project / "streams" / "s"))

        assert trees[0] == trees[1]
        assert len(trees[0]) > 10, "several windows were compared"

    def test_a_window_that_dropped_records_is_not_reproducible(self, tmp_path: Path) -> None:
        """Review item 12 (Ixian): what the buffer dropped depends on the writer's speed,
        and its capture holds only what was written, so the window says it cannot be
        replayed, though it keeps a capture (one that dropped nothing can: the replay test
        below)."""
        clock = VirtualClock()
        store = StalledArchives(clock)
        source = Script(clock, [(0.0, {"seq": i}) for i in range(12)])
        plan = _plan(WindowSpec(seconds=20), buffer=BufferSpec(records=8),
                     overflow="drop_newest")  # fmt: skip
        result: list[Any] = []
        writer = threading.Thread(
            target=lambda: result.append(
                _run(tmp_path, clock, plan, source, store=store, preserve_raw=True)
            )
        )
        writer.start()
        assert source.done.wait(10)
        store.go.set()
        writer.join(10)
        _, [burst], _ = result[0]

        assert burst.dropped == 4
        assert _meta(burst)["stream"]["reproducible"] is False
        assert (Path(burst.archive) / "captures.jsonl").exists(), "captured, yet not replayable"

    def test_a_window_kept_without_captures_is_not_reproducible(self, tmp_path: Path) -> None:
        clock = VirtualClock()

        _, [window], _ = _run(
            tmp_path, clock, _plan(WindowSpec(seconds=60)), Script(clock, _seq(3)),
            preserve_raw=False,
        )  # fmt: skip

        assert window.dropped == 0
        assert _meta(window)["stream"]["reproducible"] is False
        assert not (Path(window.archive) / "captures.jsonl").exists()

    def test_a_sealed_window_replays_exactly_from_its_capture_under_another_clock(
        self, tmp_path: Path
    ) -> None:
        """Review item 15 (Ixian): the replay is recon's own: a mission whose ``capture``
        collector reads the window's capture, surveyed on a clock at another epoch, writes the
        window's table byte for byte. Each frame's offset also puts it in its window by the
        stream's own windowing, which a frame moved across the boundary fails."""
        clock = VirtualClock()
        timeline = [(i * 1.3, {"seq": i, POSITION_FIELD: i}) for i in range(30)]
        spec = WindowSpec(seconds=10)

        _, outcomes, _ = _run(
            tmp_path / "streamed",
            clock,
            _plan(spec),
            Script(clock, timeline),
            preserve_raw=True,
            normalize=lambda r: {"n": r["seq"]},
        )

        sealed = [o for o in outcomes if o.closed_by == "window"]
        assert len(sealed) >= 2
        for window in sealed:
            archive = Path(window.archive)
            table = (archive / window.table.file).read_bytes()
            assert _meta(window)["stream"]["reproducible"] is True
            assert table.count(b"\n") >= 7, "the window holds records to replay"
            assert _replayed(tmp_path / f"replay-{window.index}", archive) == table
            assert _members(_frames(archive), spec, window.index)

        # Check the checker: one arrival moved across the boundary leaves the window.
        frames = _frames(Path(sealed[0].archive))
        frames[-1] = {**frames[-1], "at": spec.seconds}
        assert not _members(frames, spec, sealed[0].index)


# --- helpers ---------------------------------------------------------------------------------


def _wait_for(done: Callable[[], bool]) -> None:
    import time

    started = time.monotonic()
    while not done():
        assert time.monotonic() - started < 10, "timed out"
        time.sleep(0.005)


def _digest(archive: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(archive.rglob("*")):
        if path.is_file() and path.name != "writer.lock":
            digest.update(path.relative_to(archive).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _frames(archive: Path) -> list[dict[str, Any]]:
    """A window's capture: each arrival with its order, its offset and its record."""
    [line] = (archive / "captures.jsonl").read_text().splitlines()
    capture = json.loads(line)
    assert capture["kind"] == "stream"
    body = (archive / capture["body"]).read_text()
    return [json.loads(frame) for frame in body.splitlines()]


def _members(frames: list[dict[str, Any]], spec: WindowSpec, index: int) -> bool:
    """Whether every frame's offset puts it in window ``index``, by the stream's windowing."""
    windowing = Windowing(spec, 0.0, index=index)
    for frame in frames:
        size = len(json.dumps(frame["record"], default=str).encode())
        _, window, _ = windowing.arrive(frame["at"], size)
        if window != index:
            return False
    return True


def _replayed(project: Path, window: Path) -> bytes:
    """The window's table, collected again by recon's own ``capture`` collector from the
    window's capture alone, shaped as the stream shaped it, on a clock at another epoch."""
    mission = project / ".recon" / "replay"
    mission.mkdir(parents=True)
    (project / ".git").mkdir()
    shaped = {"n": "record.seq", POSITION_FIELD: f"record.{POSITION_FIELD}"}
    collector = {"name": "s", "type": "capture", "path": str(window / "captures.jsonl"),
                 "normalize": shaped}  # fmt: skip
    config = yaml.safe_dump({"collectors": [collector]}, sort_keys=False)
    (mission / "config.yaml").write_text(config)
    later = VirtualClock(datetime(2031, 6, 7, 8, 9, 10, tzinfo=UTC))

    document = root({}, project=project, clock=later).survey("replay")

    assert document.status == "complete", document.warnings
    [table] = document.tables
    return (Path(document.archive) / table.file).read_bytes()

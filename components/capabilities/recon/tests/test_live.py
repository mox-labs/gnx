"""Live observation of a survey (rulings A1-A7): what a reader sees of an archive while a
survey writes it, and that reading it changes nothing the survey writes."""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

import recon
from recon.adapters._out.archive_files import ArchiveFiles
from recon.adapters._out.system_clock import SystemClock
from recon.composition import root
from recon.domain.models import ReconConfig
from tests.recon_fakes import FixedClock, plugins_with, run_survey

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from recon.domain.archives import NewRecords
    from recon.domain.capture import CaptureLog
    from recon.domain.models import CollectorEntry, SourceEntry


def _until(read: Callable[[], recon.RecordsSince], records: int) -> recon.RecordsSince:
    """Read until at least ``records`` records came back, or two seconds passed: what the
    survey wrote reaches the table's file within 250 ms."""
    started = time.monotonic()
    while True:
        got = read()
        if got.count >= records or time.monotonic() - started > 2:
            return got
        time.sleep(0.02)


class _Steps:
    """A collector that yields records in steps, and between steps runs what the test asks:
    ``between[i]`` runs after the records of step ``i``. It raises at the end when told."""

    def __init__(
        self, steps: list[list[dict[str, Any]]], between: list[Callable[[], None]], fail: bool
    ) -> None:
        self._steps = steps
        self._between = between
        self._fail = fail

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
    ) -> Iterator[dict[str, Any]]:
        for step, then in zip(self._steps, self._between, strict=True):
            yield from step
            then()
        if self._fail:
            raise RuntimeError("the source broke off")


class TestReadSince:
    """A4: ``read_since`` returns the whole records written since its cursor, from sealed and
    live tables alike, and says which tables were sealed or retracted since."""

    def _survey(
        self, project: Path, steps: list[list[dict[str, Any]]], fail: bool = False
    ) -> list[recon.RecordsSince]:
        reads: list[recon.RecordsSince] = []

        def read(records: int) -> Callable[[], None]:
            def then() -> None:
                cursor = reads[-1].cursor if reads else None
                reads.append(_until(lambda: recon.read_since("m", cursor, root=project), records))

            return then

        between = [read(len(step)) for step in steps]
        config = ReconConfig.model_validate({"collectors": [{"name": "t", "type": "x"}]})
        run_survey(config, project, plugins=plugins_with(x=_Steps(steps, between, fail)))
        reads.append(recon.read_since("m", reads[-1].cursor, root=project))
        return reads

    def test_a_reader_follows_a_table_as_it_is_written_and_sealed(self, tmp_path: Path) -> None:
        reads = self._survey(tmp_path, [[{"n": 0}, {"n": 1}], [{"n": 2}]])

        assert [[r["record"] for r in read.records] for read in reads] == [
            [{"n": 0}, {"n": 1}],
            [{"n": 2}],
            [],
        ]
        assert {r["table"] for read in reads for r in read.records} == {"t"}
        assert [read.state for read in reads] == ["writing", "writing", "ok"]
        assert [read.sealed for read in reads] == [[], [], ["t"]]
        assert reads[-1].retracted == []
        again = recon.read_since("m", reads[-1].cursor, root=tmp_path)
        assert (again.records, again.sealed) == ([], []), "a seal is told once"

    def test_a_table_that_fails_is_retracted_and_the_reader_told(self, tmp_path: Path) -> None:
        reads = self._survey(tmp_path, [[{"n": 0}, {"n": 1}]], fail=True)

        assert [r["record"] for r in reads[0].records] == [{"n": 0}, {"n": 1}]
        assert (reads[-1].retracted, reads[-1].sealed) == (["t"], [])
        assert reads[-1].state == "incomplete"

    def test_a_reader_with_no_cursor_reads_a_finished_archive_whole(self, tmp_path: Path) -> None:
        config = {
            "collectors": [
                {"name": "a", "type": "cli", "run": 'printf \'{"n": 1}\\n{"n": 2}\\n\''},
                {"name": "b", "type": "cli", "run": "true"},
            ]
        }
        run_survey(config, tmp_path)
        read = recon.read_since("m", root=tmp_path)
        assert read.records == [
            {"table": "a", "record": {"n": 1}},
            {"table": "a", "record": {"n": 2}},
        ], "an empty table's sentinel line is not a record"
        assert (read.sealed, read.state, read.count) == (["a", "b"], "ok", 2)

    def test_limit_bounds_a_read_and_the_cursor_goes_on_from_there(self, tmp_path: Path) -> None:
        lines = "".join(f'{{"n": {n}}}\\n' for n in range(5))
        run_survey({"collectors": [{"name": "t", "type": "cli", "run": f"printf '{lines}'"}]},
                   tmp_path)  # fmt: skip
        seen: list[list[int]] = []
        sealed: list[list[str]] = []
        cursor = None
        for _ in range(3):
            read = recon.read_since("m", cursor, limit=2, root=tmp_path)
            seen.append([r["record"]["n"] for r in read.records])
            sealed.append(read.sealed)
            cursor = read.cursor
        assert seen == [[0, 1], [2, 3], [4]]
        assert sealed == [[], [], ["t"]], "sealed once its last line is read, not before"


class TestWholeLinesOnly:
    """A line the writer has not finished is not read, and a cursor resumes after the last
    whole line it gave."""

    def test_a_cut_short_last_line_waits_for_its_end(self, tmp_path: Path) -> None:
        store = ArchiveFiles(SystemClock())
        archive = store.create(tmp_path, "2026-01-02-030405-000001")
        live = archive / "t.jsonl.tmp"
        live.write_bytes(b'{"n": 0}\n{"n": ')
        first = store.read_since(archive, None, limit=100)
        with live.open("ab") as f:
            f.write(b'1}\n{"n": 2}\n')
        second = store.read_since(archive, first.cursor, limit=100)
        store.release(archive)

        assert [r.record for r in first.records] == [{"n": 0}]
        assert [r.record for r in second.records] == [{"n": 1}, {"n": 2}]
        assert first.sealed == second.sealed == []

    def test_a_short_write_taken_back_under_a_reader_is_a_retraction_not_its_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression (review, Dijkstra F2): a write the disk cut short is taken back to the
        last whole line written before it; a reader that had read a whole line of the cut
        write was then past the table's end, and was told its cursor was not this archive's.
        It now waits, and is told the table retracted when it is."""
        import types

        store = ArchiveFiles(SystemClock())
        archive = store.create(tmp_path, "2026-01-02-030405-000001")
        reads: list[NewRecords] = []
        stand_in = types.ModuleType("os")
        stand_in.__dict__.update(os.__dict__)

        def short(descriptor: int, data: bytes) -> int:
            cut = data[: data.index(b"\n") + 3]  # a whole line, and part of the next
            written: int = os.write(descriptor, cut)
            reads.append(store.read_since(archive, None, limit=100))
            return written

        def truncate(descriptor: int, size: int) -> None:
            os.ftruncate(descriptor, size)
            reads.append(store.read_since(archive, reads[0].cursor, limit=100))

        stand_in.__dict__.update(write=short, ftruncate=truncate)
        monkeypatch.setattr("recon.adapters._out.archive_files.os", stand_in)
        with pytest.raises(OSError, match="wrote"):
            store.write_table(archive, "t", (r for r in [{"n": 0}, {"n": 1}]))
        monkeypatch.undo()
        after = store.read_since(archive, reads[1].cursor, limit=100)
        store.release(archive)

        assert [r.record for r in reads[0].records] == [{"n": 0}]
        assert (reads[1].records, reads[1].retracted) == ([], [])
        assert (after.records, after.retracted) == ([], ["t"])


class TestACursorIsOpaque:
    """A cursor is recon's to make and read: one that is not a cursor, or is another
    archive's, is a usage error that says what to do instead."""

    @pytest.fixture
    def surveyed(self, tmp_path: Path) -> Path:
        run_survey({"collectors": [{"name": "t", "type": "cli", "run": "echo '{\"n\": 1}'"}]},
                   tmp_path, clock=FixedClock())  # fmt: skip
        return tmp_path

    @pytest.mark.parametrize(
        "cursor",
        [
            "not a cursor",
            "",
            base64.urlsafe_b64encode(b'{"tables": 7}').decode(),
            base64.urlsafe_b64encode(b"[1, 2]").decode(),
        ],
    )
    def test_a_cursor_recon_did_not_make_is_refused(self, surveyed: Path, cursor: str) -> None:
        with pytest.raises(recon.UsageError) as raised:
            recon.read_since("m", cursor, root=surveyed)
        assert raised.value.kind == "usage"
        assert "without a cursor" in (raised.value.fix or "")

    def test_another_archive_s_cursor_is_refused_naming_it(self, surveyed: Path) -> None:
        cursor = recon.read_since("m", root=surveyed).cursor
        later = FixedClock()
        later.advance(60)
        run_survey({"collectors": [{"name": "t", "type": "cli", "run": "true"}]}, surveyed,
                   clock=later)  # fmt: skip
        first = recon.status("m", root=surveyed).missions[0].history
        assert first is not None
        with pytest.raises(recon.UsageError) as raised:
            recon.read_since("m", cursor, root=surveyed)
        assert f"--archive {first[0].id}" in (raised.value.fix or "")
        again = recon.read_since("m", cursor, archive=first[0].id, root=surveyed)
        assert again.records == [] and again.archive_id == first[0].id

    def test_a_limit_below_one_is_refused(self, surveyed: Path) -> None:
        with pytest.raises(recon.UsageError):
            recon.read_since("m", limit=0, root=surveyed)

    def test_the_cursor_is_a_string_and_its_contents_are_not_published(
        self, surveyed: Path
    ) -> None:
        read = recon.read_since("m", root=surveyed)
        assert isinstance(read.cursor, str)
        assert "t.jsonl" not in json.dumps(read.document()), "no file of the store's is named"


def test_a_reader_attached_changes_no_byte_of_the_archive(tmp_path: Path) -> None:
    """A7: the archive is byte-identical with and without a reader attached."""

    def survey(project: Path, reading: bool) -> dict[str, bytes]:
        stop = threading.Event()
        reads: list[int] = []
        cursors: list[str | None] = [None]

        def reader() -> None:
            while not stop.is_set():
                if (project / ".recon" / "m" / "archive").exists():
                    read = recon.read_since("m", cursors[-1], root=project)
                    recon.status("m", root=project)
                    cursors.append(read.cursor)
                    reads.append(read.count)

        steps = [[{"n": n} for n in range(k * 50, k * 50 + 50)] for k in range(4)]
        pause = [lambda: time.sleep(0.3)] * 4
        config = ReconConfig.model_validate({"collectors": [{"name": "t", "type": "x"}]})
        thread = threading.Thread(target=reader)
        if reading:
            thread.start()
        try:
            archive, _ = run_survey(
                config, project, plugins=plugins_with(x=_Steps(steps, pause, False)),
                clock=FixedClock(),
            )  # fmt: skip
        finally:
            stop.set()
            if reading:
                thread.join()
        if reading:
            reads.append(recon.read_since("m", cursors[-1], root=project).count)
            assert sum(reads) == 200, "the reader saw every record, once"
            assert len(reads) > 4, "the reader read while the survey wrote"
        return {
            p.relative_to(archive).as_posix(): p.read_bytes()
            for p in sorted(archive.rglob("*"))
            if p.is_file()
        }

    assert survey(tmp_path / "read", True) == survey(tmp_path / "alone", False)


class TestNoReceiverSlowsTheWrite:
    """A6: OpenTelemetry and ``on_event`` receive through a bounded queue a thread drains,
    so a slow receiver neither slows the archive's writing nor changes its bytes; what the
    queue could not hold, or the survey's end could not wait for, is counted."""

    @staticmethod
    def _survey(
        project: Path, on_event: Callable[[dict[str, Any]], None] | None
    ) -> tuple[recon.SurveyDocument, dict[str, bytes], float]:
        from recon.adapters._out.event_channel import ThreadChannel

        written: list[float] = []

        class Timed(ArchiveFiles):
            def create(self, mission_dir: Path, archive_id: str) -> Path:
                written.append(time.monotonic())
                return super().create(mission_dir, archive_id)

            def write_meta(self, archive: Path, meta: dict[str, Any]) -> None:
                super().write_meta(archive, meta)
                written.append(time.monotonic())

        class Slow:
            def collect(
                self,
                entry: CollectorEntry,
                source: SourceEntry | None,
                *,
                captures: CaptureLog | None = None,
            ) -> Iterator[dict[str, Any]]:
                time.sleep(0.2)
                yield {"table": entry.name}

        clock = FixedClock()
        config = {"collectors": [{"name": f"t{n}", "type": "x"} for n in range(5)]}
        mission = project / ".recon" / "m"
        mission.mkdir(parents=True)
        (mission / "config.yaml").write_text(yaml.safe_dump(config))
        surveyed = root(
            {},
            project=project,
            clock=clock,
            plugins=plugins_with(x=Slow()),
            archives=Timed(clock),
            channel=lambda: ThreadChannel(queue=2, drain_s=0.2),
        ).survey("m", on_event=on_event)
        archive = Path(surveyed.archive)
        files = {p.name: p.read_bytes() for p in sorted(archive.iterdir()) if p.is_file()}
        return surveyed, files, written[1] - written[0]

    def test_a_slow_receiver_changes_no_byte_and_no_time_and_its_losses_are_counted(
        self, tmp_path: Path
    ) -> None:
        def slow(event: dict[str, Any]) -> None:
            time.sleep(1.0)

        alone, alone_files, alone_s = self._survey(tmp_path / "alone", None)
        slowed, slowed_files, slowed_s = self._survey(tmp_path / "slowed", slow)

        assert slowed_files == alone_files, "the archive is the same, byte for byte"
        assert slowed_s <= alone_s * 1.05, (slowed_s, alone_s)
        [dropped] = [w for w in slowed.warnings if w["kind"] == "events_dropped"]
        assert dropped["got"] > 0 and "dropped" in dropped["message"]
        assert dropped["fix"]
        assert not [w for w in alone.warnings if w["kind"] == "events_dropped"]

    def test_a_sink_that_never_returns_cannot_hold_the_survey(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression (review, Erlang): closing a sink waited for the delivery it was stuck
        in, so an exporter that hung made the survey hang forever. The survey returns within
        the drain bound, and says what was lost and what is stuck."""
        released = threading.Event()

        class Hangs:
            def on_event(self, event: Any) -> None:
                if event.name.endswith(".end"):
                    released.wait()  # an exporter that never answers

            def close(self) -> None:
                pass

        monkeypatch.setattr("recon.composition.OtelEvents", lambda version: Hangs())
        config = {"collectors": [{"name": "t", "type": "cli", "run": "echo '{\"n\": 1}'"}]}
        mission = tmp_path / ".recon" / "m"
        mission.mkdir(parents=True)
        (mission / "config.yaml").write_text(yaml.safe_dump(config))
        from recon.adapters._out.event_channel import ThreadChannel

        done: list[recon.SurveyDocument] = []
        surveying = threading.Thread(
            target=lambda: done.append(
                root({}, project=tmp_path, channel=lambda: ThreadChannel(drain_s=0.3)).survey(
                    "m"
                )  # fmt: skip
            ),
            daemon=True,
        )
        try:
            surveying.start()
            surveying.join(timeout=5)
            assert done, "the survey returned within the drain bound and a margin"
        finally:
            released.set()
        kinds = {w["kind"]: w for w in done[0].warnings}
        assert done[0].status == "ok"
        assert "events_dropped" in kinds
        assert "Hangs" in kinds["telemetry_stuck"]["message"] and kinds["telemetry_stuck"]["fix"]

    def test_a_receiver_that_raises_is_reported_and_the_survey_goes_on(
        self, tmp_path: Path
    ) -> None:
        def broken(event: dict[str, Any]) -> None:
            raise RuntimeError("the terminal went away")

        surveyed, _, _ = self._survey(tmp_path, broken)
        assert surveyed.status == "ok"
        [failed] = [w for w in surveyed.warnings if w["kind"] == "telemetry_failed"]
        assert "the terminal went away" in failed["message"]


def test_opentelemetry_spans_nest_while_the_queue_is_behind() -> None:
    """``around`` and ``trace_headers`` are answered at once, not through the queue, so a
    span is current for the work inside it however far behind the queue's thread is."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from recon.adapters._out.event_channel import ThreadChannel
    from recon.adapters._out.otel_telemetry import OtelEvents
    from recon.domain.telemetry import Telemetry

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    channel = ThreadChannel()
    busy = channel.callback(lambda event: time.sleep(0.3))
    busy({})  # the queue's thread is held while the spans begin
    telemetry = Telemetry([channel.sink(OtelEvents("9.9", tracer_provider=provider))],
                          FixedClock())  # fmt: skip
    library = provider.get_tracer("some.library")
    with telemetry.span("recon.collect") as span:
        headers = telemetry.trace_headers(span)
        with library.start_as_current_span("library.call"):
            pass
    channel.close()
    telemetry.close()

    spans = {s.name: s for s in exporter.get_finished_spans()}
    parent = spans["library.call"].parent
    assert parent is not None and parent.span_id == spans["recon.collect"].context.span_id
    assert headers.get("traceparent", "").count("-") == 3
    assert channel.dropped == 0

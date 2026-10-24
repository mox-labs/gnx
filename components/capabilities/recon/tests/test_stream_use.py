"""Streams through recon's operations and its command line (ruling B8): ``recon stream`` rolls
windows and returns after one unless told otherwise, ``recon survey`` runs one window of a
windowed entry, ``watch --stream`` follows across windows, ``query --stream`` reads across
them, and ``status`` shows each stream's state."""

from __future__ import annotations

import dataclasses
import json
import threading
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from click.testing import CliRunner

import recon
from recon.adapters._in.cli import main
from recon.composition import root
from tests.recon_fakes import VirtualClock, plugins_with

if TYPE_CHECKING:
    from collections.abc import Iterator

    from recon.composition import Recon
    from recon.domain.capture import CaptureLog
    from recon.domain.models import CollectorEntry, SourceEntry


class Feed:
    """A source of records, each at its time on the virtual clock, then quiet (or ended, with
    ``ends``) until it is closed; started again, it goes on after the position it is given."""

    def __init__(
        self,
        clock: VirtualClock,
        every: float,
        count: int,
        *,
        ends: bool = False,
        pause_at: int | None = None,
    ) -> None:
        self._clock = clock
        self._every = every
        self._count = count
        self._ends = ends
        self._pause_at = pause_at
        #: Set by the test to let a feed paused at ``pause_at`` go on.
        self.go = threading.Event()
        self.closed = threading.Event()
        self.started: list[Any] = []

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
        after: Any = None,
    ) -> Iterator[dict[str, Any]]:
        self.started.append(after)
        self.closed.clear()
        start = 0 if after is None else int(after) + 1
        base = self._clock.monotonic()
        for seq in range(start, self._count):
            if seq == self._pause_at:
                assert self.go.wait(10), "the test never let the feed go on"
            self._clock.set(base + (seq - start + 1) * self._every)
            yield {"seq": seq, "_position": seq}
        if not self._ends:
            self._clock.advance(10_000)
            self.closed.wait(10)

    def close(self) -> None:
        self.closed.set()


def _mission(
    project: Path, entry: dict[str, Any], *, snapshot: bool = False, name: str = "m"
) -> None:
    collectors = [{"name": "events", "type": "feed", **entry}]
    if snapshot:
        collectors.insert(0, {"name": "now", "type": "cli", "run": "echo '{\"a\": 1}'"})
    config = {"collectors": collectors}
    mission = project / ".recon" / name
    mission.mkdir(parents=True, exist_ok=True)
    (mission / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))


def _root(project: Path, clock: VirtualClock, feed: Feed) -> Recon:
    return root({}, project=project, clock=clock, plugins=plugins_with(feed=feed))


class TestReconStream:
    def test_a_stream_returns_after_one_window_by_default(self, tmp_path: Path) -> None:
        clock = VirtualClock()
        feed = Feed(clock, 1.0, 100)
        _mission(tmp_path, {"window": {"seconds": 10}, "retain": "all"})

        document = _root(tmp_path, clock, feed).stream("m", "events")

        assert document.windows == 1
        assert document.last is not None
        assert (document.last.closed_by, document.last.table.records) == ("seconds", 9)
        assert document.state == "complete"
        assert feed.closed.is_set()

    def test_windows_n_rolls_n_and_a_second_run_resumes_after_the_last_position(
        self, tmp_path: Path
    ) -> None:
        clock = VirtualClock()
        feed = Feed(clock, 1.0, 30)
        _mission(tmp_path, {"window": {"records": 5}, "retain": "all"})
        recon_ = _root(tmp_path, clock, feed)

        first = recon_.stream("m", "events", windows=2)
        second = recon_.stream("m", "events", windows=1)

        assert (first.windows, first.records) == (2, 10)
        assert feed.started == [None, 9], "the second run went on after the last kept"
        assert second.last is not None and second.last.index == 2
        meta = yaml.safe_load((Path(second.last.path) / "meta.yaml").read_text())
        assert first.last is not None and meta["stream"]["previous"] == first.last.id
        # Every restart is in the chain (review item 3): the stretch no process read, here
        # resumed from a position, so a source that does not honour it shows where.
        [gap] = meta["stream"]["sources"][0]["gaps"]
        assert (gap["from"], gap["resumed"]) == (first.last.closed_at, True)
        rows = [
            json.loads(line)["seq"]
            for line in (Path(second.last.path) / "events.jsonl").read_text().splitlines()
        ]
        assert rows == [10, 11, 12, 13, 14], "exactly once across the two runs"

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"windows": 0}, "windows"),
            ({"forever": True, "windows": 2}, "forever"),
            ({"until": "past"}, "until"),
        ],
    )
    def test_a_stream_is_refused_a_bound_it_cannot_keep(
        self, tmp_path: Path, kwargs: dict[str, Any], match: str
    ) -> None:
        clock = VirtualClock()
        _mission(tmp_path, {"window": {"seconds": 10}})
        if kwargs.get("until") == "past":
            kwargs["until"] = clock.now() - timedelta(seconds=1)

        with pytest.raises(recon.UsageError, match=match) as raised:
            _root(tmp_path, clock, Feed(clock, 1.0, 3)).stream("m", "events", **kwargs)
        assert raised.value.fix

    def test_a_collector_that_is_no_stream_is_named(self, tmp_path: Path) -> None:
        clock = VirtualClock()
        _mission(tmp_path, {"window": {"seconds": 10}}, snapshot=True)

        with pytest.raises(recon.NotFoundError, match="no stream 'now'") as raised:
            _root(tmp_path, clock, Feed(clock, 1.0, 3)).stream("m", "now")
        assert raised.value.details["allowed"] == ["events"]

    def test_until_ends_the_stream_at_that_time(self, tmp_path: Path) -> None:
        clock = VirtualClock()
        _mission(tmp_path, {"window": {"seconds": 10}, "retain": "all"})

        document = _root(tmp_path, clock, Feed(clock, 1.0, 100)).stream(
            "m", "events", until=clock.now() + timedelta(seconds=25)
        )

        assert document.windows == 3
        assert document.last is not None
        assert (document.last.closed_by, document.last.table.records) == ("until", 5)


class TestASurveyRunsOneWindow:
    def test_a_windowed_entry_is_one_window_sealed_ok_beside_the_snapshot_tables(
        self, tmp_path: Path
    ) -> None:
        clock = VirtualClock()
        _mission(tmp_path, {"window": {"seconds": 10}, "retain": "all"}, snapshot=True)

        document = _root(tmp_path, clock, Feed(clock, 1.0, 100)).survey("m")

        assert document.status == "complete"
        assert [t.name for t in document.tables] == ["now"]
        [window] = document.stream_windows
        assert (window.stream, window.closed_by, window.table.status) == ("events", "seconds", "ok")
        assert window.table.records == 9

    @pytest.mark.parametrize(
        ("snapshot", "deadline", "returns"),
        [(False, None, 30.0), (True, 5, 35.0), (True, None, None)],
    )
    def test_survey_dry_run_says_how_long_its_windows_hold_it(
        self, tmp_path: Path, snapshot: bool, deadline: float | None, returns: float | None
    ) -> None:
        """Polish R3 (Ace 1-2): a survey runs one window of each stream, one after another,
        so two streams of 10 s and 20 s hold it 30 s past its snapshot tables; the plan says
        so as ``survey.returns``, adding the snapshot tables' ``deadline:``, and null when
        nothing bounds them."""
        clock = VirtualClock()
        collectors: list[dict[str, Any]] = [
            {"name": "events", "type": "feed", "window": {"seconds": 10}, "retain": "all"},
            {"name": "log", "type": "feed", "window": {"seconds": 20}, "retain": "all"},
        ]
        if snapshot:
            collectors.insert(0, {"name": "now", "type": "cli", "run": "echo '{\"a\": 1}'"})
        config: dict[str, Any] = {"collectors": collectors}
        if deadline is not None:
            config["deadline"] = deadline
        mission = tmp_path / ".recon" / "m"
        mission.mkdir(parents=True)
        (mission / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))

        result = CliRunner().invoke(main, ["survey", "m", "--dry-run", "--json"],
                                    obj=_root(tmp_path, clock, Feed(clock, 1.0, 3)))  # fmt: skip

        assert result.exit_code == 0, result.output
        said = {v["path"]: v for v in json.loads(result.stdout)["provenance"]}
        assert said["survey.returns"]["value"] == returns
        assert "in turn" in said["survey.returns"]["note"]
        if returns is None:
            assert "deadline:" in said["survey.returns"]["note"], "it says what would bound it"


class TestWatchAcrossWindows:
    def test_watch_follows_a_stream_from_window_to_window(self, tmp_path: Path) -> None:
        clock = VirtualClock()
        feed = Feed(clock, 1.0, 25, ends=True)
        _mission(tmp_path, {"window": {"seconds": 10}, "retain": "all"})
        recon_ = _root(tmp_path, clock, feed)
        recon_.stream("m", "events", forever=True)
        [mission] = recon_.status("m").missions
        first = mission.streams[0].first

        reads = list(recon_.watch("m", stream="events", archive=first, max_records=4))

        seqs = [r["record"]["seq"] for read in reads for r in read.table_records]
        assert seqs == list(range(25))
        assert len({read.archive for read in reads}) == 3
        assert all(read.stream == "events" for read in reads)

    def test_a_cursor_into_a_retired_window_goes_on_from_the_next_one(self, tmp_path: Path) -> None:
        clock = VirtualClock()
        _mission(tmp_path, {"window": {"seconds": 10}, "retain": "all"})
        recon_ = _root(tmp_path, clock, Feed(clock, 1.0, 100))
        recon_.stream("m", "events", windows=1)
        read = recon_.read_since("m", stream="events")
        _mission(tmp_path, {"window": {"seconds": 10}, "retain": {"windows": 1}})
        recon_.stream("m", "events", windows=2)  # the window the cursor is in is retired

        after = recon_.read_since("m", read.cursor, stream="events")

        assert after.archive != read.archive
        assert [r["record"]["seq"] for r in after.table_records][:1] == [18], "window 2's first"
        stones = [stone["id"] for stone in _retired(tmp_path)]
        assert after.skipped is not None, "a read that jumps windows says which it jumped"
        assert (after.skipped.first, after.skipped.count) == (read.archive, 2)
        assert after.skipped.last == stones[-1]
        assert recon_.read_since("m", after.cursor, stream="events").skipped is None

    def test_a_watcher_attached_changes_no_byte_of_the_stream(self, tmp_path: Path) -> None:
        """B10 #9: the same stream, with and without a reader following it as it is written."""
        trees = []
        states: list[str] = []
        for watched in (False, True):
            project = tmp_path / str(watched)
            clock = VirtualClock()
            feed = Feed(clock, 0.5, 60, ends=True, pause_at=30 if watched else None)
            _mission(project, {"window": {"seconds": 7}, "retain": "all"})
            recon_ = _root(project, clock, feed)
            reader: threading.Thread | None = None
            if watched:
                follow = (project, feed.go, states)
                reader = threading.Thread(target=_follow, args=follow, daemon=True)
                reader.start()
            recon_.stream("m", "events", forever=True)
            if reader is not None:
                reader.join(10)
            trees.append(_tree(project / ".recon" / "m" / "streams" / "events"))

        assert "writing" in states, "the reader read a window while it was written"
        assert trees[0] == trees[1]


class TestQueryAcrossWindows:
    def test_a_query_reads_every_window_of_a_stream_or_those_in_a_range(
        self, tmp_path: Path
    ) -> None:
        clock = VirtualClock()
        _mission(tmp_path, {"window": {"seconds": 10}, "retain": "all"})
        recon_ = _root(tmp_path, clock, Feed(clock, 1.0, 35, ends=True))
        recon_.stream("m", "events", forever=True)
        opened = clock.now() - timedelta(seconds=clock.monotonic())

        every = recon_.query("m", "SELECT count(*) AS n FROM events", stream="events")
        later = recon_.query(
            "m",
            "SELECT min(seq) AS lo, count(*) AS n FROM events",
            stream="events",
            since=opened + timedelta(seconds=20),
        )

        assert every.rows == [{"n": 35}]
        status = recon_.status("m").missions[0].streams[0]
        assert every.window_range is not None and status.latest is not None
        assert (every.window_range.first, every.window_range.last, every.window_range.count) == (
            status.first,
            status.latest.id,
            4,
        ), "the range read, not every id (review item 11)"
        assert later.rows == [{"lo": 19, "n": 16}]
        assert later.window_range is not None
        assert later.window_range.count == 2, "windows that ended before since are not read"
        assert not [w for w in every.warnings if w["kind"] == "windows_many"]

    def test_a_query_of_many_windows_warns_how_to_narrow_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from recon.application import use_cases

        monkeypatch.setattr(use_cases, "MANY_WINDOWS", 3)
        clock = VirtualClock()
        _mission(tmp_path, {"window": {"seconds": 10}, "retain": "all"})
        recon_ = _root(tmp_path, clock, Feed(clock, 1.0, 35, ends=True))
        recon_.stream("m", "events", forever=True)

        rows = recon_.query("m", "SELECT count(*) AS n FROM events", stream="events")

        [warning] = [w for w in rows.warnings if w["kind"] == "windows_many"]
        assert warning["got"] == 4 and "--since" in warning["fix"]

    def test_windows_of_two_configs_are_refused_with_a_fix(self, tmp_path: Path) -> None:
        clock = VirtualClock()
        _mission(tmp_path, {"window": {"records": 3}, "retain": "all"})
        recon_ = _root(tmp_path, clock, Feed(clock, 1.0, 100))
        recon_.stream("m", "events")
        _mission(tmp_path, {"window": {"records": 4}, "retain": "all"})
        recon_.stream("m", "events")

        with pytest.raises(recon.QueryError, match="configs") as raised:
            recon_.query("m", "SELECT count(*) FROM events", stream="events")
        assert raised.value.fix and "--since" in raised.value.fix


class TestStatus:
    def test_status_shows_each_stream_and_its_state(self, tmp_path: Path) -> None:
        clock = VirtualClock()
        _mission(tmp_path, {"window": {"seconds": 10}, "retain": "all"})
        recon_ = _root(tmp_path, clock, Feed(clock, 1.0, 100))
        recon_.stream("m", "events", windows=2)

        [mission] = recon_.status("m").missions
        [stream] = mission.streams

        assert (stream.name, stream.state, stream.windows) == ("events", "complete", 2)
        assert stream.latest is not None and stream.latest.state == "complete"


class TestTheCommandLine:
    def test_recon_stream_json_prints_one_document_and_exits_0(self, tmp_path: Path) -> None:
        clock = VirtualClock()
        _mission(tmp_path, {"window": {"seconds": 10}, "retain": "all"})
        recon_ = _root(tmp_path, clock, Feed(clock, 1.0, 100))

        result = CliRunner().invoke(
            main, ["stream", "m", "--stream", "events", "--json"], obj=recon_
        )

        assert result.exit_code == 0, result.output
        document = json.loads(result.stdout)
        assert document["schema"] == "recon.v1.stream"
        assert document["windows"] == 1

    def test_recon_stream_dry_run_says_each_default(self, tmp_path: Path) -> None:
        clock = VirtualClock()
        _mission(tmp_path, {})
        plugins = plugins_with(feed=Feed(clock, 1.0, 3))
        unbounded = dataclasses.replace(plugins.collector_types["feed"], unbounded=True)
        plugins.collector_types["feed"] = unbounded
        recon_ = root({}, project=tmp_path, clock=clock, plugins=plugins)

        result = CliRunner().invoke(
            main, ["stream", "m", "--stream", "events", "--dry-run", "--json"], obj=recon_
        )

        assert result.exit_code == 0, result.output
        document = json.loads(result.stdout)
        assert document["schema"] == "recon.v1.plan"
        said = {v["path"]: (v["value"], v["source"]) for v in document["provenance"]}
        assert said["collectors.0.window.seconds"] == (300.0, "recon")
        assert said["collectors.0.retain"] == ("all", "recon")
        assert said["collectors.0.buffer.overflow"] == ("block", "type")
        assert "retain_unset" in {w["kind"] for w in document["warnings"]}

    @pytest.mark.parametrize(
        ("flags", "said"),
        [
            ([], ("stream.windows", 1, "recon")),
            (["--windows", "4"], ("stream.windows", 4, "command")),
            (["--forever"], ("stream.forever", True, "command")),
        ],
    )
    def test_recon_stream_dry_run_says_how_long_the_run_is_and_why(
        self, tmp_path: Path, flags: list[str], said: tuple[str, Any, str]
    ) -> None:
        clock = VirtualClock()
        _mission(tmp_path, {"window": {"seconds": 10}, "retain": "all"})
        recon_ = _root(tmp_path, clock, Feed(clock, 1.0, 3))

        result = CliRunner().invoke(
            main, ["stream", "m", "--stream", "events", "--dry-run", "--json", *flags],
            obj=recon_,
        )  # fmt: skip

        assert result.exit_code == 0, result.output
        provenance = json.loads(result.stdout)["provenance"]
        path, value, source = said
        assert {(v["path"], v["value"], v["source"]) for v in provenance} >= {said}
        assert path.startswith("stream.")

    @pytest.mark.parametrize(
        ("flags", "returns"), [([], 10.0), (["--windows", "4"], 40.0), (["--forever"], None)]
    )
    def test_recon_stream_dry_run_says_when_the_run_returns(
        self, tmp_path: Path, flags: list[str], returns: float | None
    ) -> None:
        """Review item 21 (Erlang): an agent learns before it runs a stream how long the
        command holds it: at most, in seconds, or null for one only a stop ends."""
        clock = VirtualClock()
        _mission(tmp_path, {"window": {"seconds": 10}, "retain": "all"})
        recon_ = _root(tmp_path, clock, Feed(clock, 1.0, 3))

        result = CliRunner().invoke(
            main, ["stream", "m", "--stream", "events", "--dry-run", "--json", *flags],
            obj=recon_,
        )  # fmt: skip

        assert result.exit_code == 0, result.output
        said = {v["path"]: v for v in json.loads(result.stdout)["provenance"]}
        assert said["stream.returns"]["value"] == returns
        assert said["stream.returns"]["note"]

    def test_recon_stream_says_when_it_will_return_as_it_starts(self, tmp_path: Path) -> None:
        clock = VirtualClock()
        _mission(tmp_path, {"window": {"seconds": 10}, "retain": "all"})
        events: list[dict[str, Any]] = []
        _root(tmp_path, clock, Feed(clock, 1.0, 30)).stream("m", "events", on_event=events.append)
        again = VirtualClock()

        result = CliRunner().invoke(
            main, ["stream", "m", "--stream", "events"],
            obj=_root(tmp_path, again, Feed(again, 1.0, 30)),
        )  # fmt: skip

        assert events[0]["kind"] == "stream"
        assert (events[0]["name"], events[0]["returns_s"]) == ("events", 10.0)
        assert result.exit_code == 0, result.output
        assert "returns within 10s" in result.stderr.splitlines()[0]

    def test_under_json_the_run_s_document_says_when_it_returns(self, tmp_path: Path) -> None:
        """Polish R3 (Ace 1-2): the notice of when the run returns went to stderr for a
        person and to ``on_event`` for a program, and to a ``--json`` caller nowhere. It is
        in the run's document; stderr stays the error envelope's alone."""
        clock = VirtualClock()
        _mission(tmp_path, {"window": {"seconds": 10}, "retain": "all"})

        result = CliRunner().invoke(main, ["stream", "m", "--stream", "events", "--json"],
                                    obj=_root(tmp_path, clock, Feed(clock, 1.0, 30)))  # fmt: skip

        assert result.exit_code == 0, result.output
        document = json.loads(result.stdout)
        assert document["returns_s"] == 10.0
        assert "returns within 10s" in document["returns"]
        assert result.stderr == ""

    def test_the_help_says_how_long_a_stream_holds_the_command_and_to_plan_first(self) -> None:
        """Polish R3 (Ace 1-2): one window of the default 300 s can outlast an agent's tool
        call, so the help says so, and that ``--dry-run`` says when the run returns."""
        streaming = " ".join(CliRunner().invoke(main, ["stream", "--help"]).output.split())
        surveying = " ".join(CliRunner().invoke(main, ["survey", "--help"]).output.split())

        for said in ("300 s", "tool call", "--dry-run", "stream.returns"):
            assert said in streaming, said
        for said in ("one window of each stream", "in turn", "survey.returns"):
            assert said in surveying, said

    def test_recon_stream_names_its_stream_with_stream_as_watch_and_query_do(
        self, tmp_path: Path
    ) -> None:
        """Review item 22 (Knuth 3): one thing, one word: ``--stream`` names the stream on
        ``recon stream`` as it does on ``watch`` and ``query``. Polish R11 (Karman 9): the
        ``--collector`` alias is gone, so the stream has one name on the command line."""
        clock = VirtualClock()
        _mission(tmp_path, {"window": {"seconds": 10}, "retain": "all"})
        recon_ = _root(tmp_path, clock, Feed(clock, 1.0, 30))

        result = CliRunner().invoke(main, ["stream", "m", "--stream", "events", "--json"],
                                    obj=recon_)  # fmt: skip
        alias = CliRunner().invoke(main, ["stream", "m", "--collector", "events", "--json"],
                                   obj=recon_)  # fmt: skip

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["stream"] == "events"
        assert alias.exit_code == recon.EXIT_CODES["usage"], "no alias: --collector is unknown"
        assert "--collector" not in CliRunner().invoke(main, ["stream", "--help"]).output

    def test_recon_stream_dry_run_refuses_what_the_run_would(self, tmp_path: Path) -> None:
        clock = VirtualClock()
        _mission(tmp_path, {"window": {"seconds": 10}})
        recon_ = _root(tmp_path, clock, Feed(clock, 1.0, 3))

        result = CliRunner().invoke(
            main, ["stream", "m", "--stream", "nope", "--dry-run", "--json"], obj=recon_
        )

        assert result.exit_code == 4, "no stream nope: not_found, before anything runs"

    def test_recon_stream_refuses_two_bounds_with_usage(self, tmp_path: Path) -> None:
        clock = VirtualClock()
        _mission(tmp_path, {"window": {"seconds": 10}})
        recon_ = _root(tmp_path, clock, Feed(clock, 1.0, 3))

        result = CliRunner().invoke(
            main,
            ["stream", "m", "--stream", "events", "--forever", "--windows", "2", "--json"],
            obj=recon_,
        )

        assert result.exit_code == 2
        assert json.loads(result.stderr)["error"]["kind"] == "usage"


def _follow(project: Path, go: threading.Event, states: list[str]) -> None:
    """Follow the stream with the library's watch, as an agent would; once it has read a
    record, let the paused feed go on, so the rest is read as it is written."""
    import time

    started = time.monotonic()
    while time.monotonic() - started < 10:
        try:
            for read in recon.watch("m", stream="events", interval_s=0.01, root=project):
                states.append(read.state)
                if read.count:
                    go.set()
            return
        except recon.NotFoundError:
            time.sleep(0.01)


def _retired(project: Path) -> list[dict[str, Any]]:
    """The tombstones of the mission's stream, in the order its windows were retired."""
    lines = (project / ".recon" / "m" / "streams" / "events" / "retired.jsonl").read_text()
    return [json.loads(line) for line in lines.splitlines()]


def _tree(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }

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
        assert (document.last.closed_by, document.last.table.records) == ("window", 9)
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
        meta = yaml.safe_load((Path(second.last.archive) / "meta.yaml").read_text())
        assert first.last is not None and meta["stream"]["previous"] == first.last.id
        # Every restart is in the chain (review item 3): the stretch no process read, here
        # resumed from a position, so a source that does not honour it shows where.
        [gap] = meta["stream"]["sources"][0]["gaps"]
        assert (gap["from"], gap["resumed"]) == (first.last.closed_at, True)
        rows = [
            json.loads(line)["seq"]
            for line in (Path(second.last.archive) / "events.jsonl").read_text().splitlines()
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
        [window] = document.windows
        assert (window.stream, window.closed_by, window.table.status) == ("events", "window", "ok")
        assert window.table.records == 9


class TestWatchAcrossWindows:
    def test_watch_follows_a_stream_from_window_to_window(self, tmp_path: Path) -> None:
        clock = VirtualClock()
        feed = Feed(clock, 1.0, 25, ends=True)
        _mission(tmp_path, {"window": {"seconds": 10}, "retain": "all"})
        recon_ = _root(tmp_path, clock, feed)
        recon_.stream("m", "events", forever=True)
        [mission] = recon_.status("m").missions
        first = mission.streams[0].first

        reads = list(recon_.watch("m", stream="events", archive=first, limit=4))

        seqs = [r["record"]["seq"] for read in reads for r in read.records]
        assert seqs == list(range(25))
        assert len({read.archive_id for read in reads}) == 3
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

        assert after.archive_id != read.archive_id
        assert [r["record"]["seq"] for r in after.records][:1] == [18], "window 2's first"

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
        assert len(every.windows) == 4
        assert later.rows == [{"lo": 19, "n": 16}]
        assert len(later.windows) == 2, "windows that ended before since are not read"

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
            main, ["stream", "m", "--collector", "events", "--json"], obj=recon_
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
            main, ["stream", "m", "--collector", "events", "--dry-run", "--json"], obj=recon_
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
            main, ["stream", "m", "--collector", "events", "--dry-run", "--json", *flags],
            obj=recon_,
        )  # fmt: skip

        assert result.exit_code == 0, result.output
        provenance = json.loads(result.stdout)["provenance"]
        path, value, source = said
        assert {(v["path"], v["value"], v["source"]) for v in provenance} >= {said}
        assert path.startswith("stream.")

    def test_recon_stream_dry_run_refuses_what_the_run_would(self, tmp_path: Path) -> None:
        clock = VirtualClock()
        _mission(tmp_path, {"window": {"seconds": 10}})
        recon_ = _root(tmp_path, clock, Feed(clock, 1.0, 3))

        result = CliRunner().invoke(
            main, ["stream", "m", "--collector", "nope", "--dry-run", "--json"], obj=recon_
        )

        assert result.exit_code == 4, "no stream nope: not_found, before anything runs"

    def test_recon_stream_refuses_two_bounds_with_usage(self, tmp_path: Path) -> None:
        clock = VirtualClock()
        _mission(tmp_path, {"window": {"seconds": 10}})
        recon_ = _root(tmp_path, clock, Feed(clock, 1.0, 3))

        result = CliRunner().invoke(
            main,
            ["stream", "m", "--collector", "events", "--forever", "--windows", "2", "--json"],
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


def _tree(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }

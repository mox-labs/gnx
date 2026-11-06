"""The ``tail`` collector type (ruling B9): a file followed as it grows, each line a record
positioned by the file's inode and the offset after the line, through rotation and truncation,
and resumed after a position exactly once. The file is a fake the test writes, rotates and
truncates; the waits between reads are on a virtual clock."""

from __future__ import annotations

import json
import os
import threading
import time
from typing import TYPE_CHECKING, Any

import pytest

from recon.adapters._out.tail_collector import TailCollector
from recon.composition import load_plugins
from recon.core.domain.collector import Dropped, Position, Record
from recon.core.domain.stream import POSITION_FIELD, Gap
from tests.recon_fakes import VirtualClock, run_for

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class Gaps:
    def __init__(self) -> None:
        self.said: list[Gap] = []


class Following:
    """A tail collector run on a thread of its own, as a stream's pump runs it, its records
    gathered as they come (each with its position as ``_position``, as the stream keeps it),
    and the gaps it gave."""

    def __init__(self, path: Path, *, after: Any = None, start: str = "beginning") -> None:
        self.clock = VirtualClock()
        self.gaps = Gaps()
        self.collector = TailCollector(clock=self.clock)
        self.records: list[dict[str, Any]] = []
        self.error: BaseException | None = None
        run = run_for({"name": "log", "type": "tail", "start": start},
                      {"name": "app", "type": "file", "url": str(path)})  # fmt: skip
        items = self.collector.collect(run, None if after is None else Position(after))

        def run_() -> None:
            try:
                for item in items:
                    if isinstance(item, Record):
                        self.records.append({**item.data, POSITION_FIELD: item.position})
                    elif isinstance(item, Gap):
                        self.gaps.said.append(item)
                    elif isinstance(item, Dropped):
                        raise AssertionError("a tail collector drops nothing")
            except BaseException as exc:
                self.error = exc

        self.thread = threading.Thread(target=run_, daemon=True)
        self.thread.start()

    def until(self, done: Callable[[list[dict[str, Any]]], bool]) -> list[dict[str, Any]]:
        started = time.monotonic()
        while not done(self.records):
            assert self.error is None, self.error
            assert time.monotonic() - started < 10, f"timed out with {self.records}"
            self.clock.advance(1.0)
            time.sleep(0.005)
        return self.records

    def stop(self) -> None:
        self.collector.close()
        self.thread.join(5)
        assert not self.thread.is_alive(), "close() ended the read"


def _append(path: Path, *lines: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


def _lines(records: list[dict[str, Any]]) -> list[Any]:
    return [r.get("n", r.get("line")) for r in records]


class TestFollowing:
    def test_each_line_is_a_record_positioned_after_it(self, tmp_path: Path) -> None:
        log = tmp_path / "app.log"
        _append(log, '{"n": 1}', "plain text")
        following = Following(log)

        records = following.until(lambda got: len(got) == 2)
        _append(log, '{"n": 3}')
        records = following.until(lambda got: len(got) == 3)
        following.stop()

        assert _lines(records) == [1, "plain text", 3]
        inode = os.stat(log).st_ino
        assert [r["_position"] for r in records] == [
            {"inode": inode, "offset": 9},
            {"inode": inode, "offset": 20},
            {"inode": inode, "offset": 29},
        ]

    def test_a_line_not_yet_whole_waits_for_its_end(self, tmp_path: Path) -> None:
        log = tmp_path / "app.log"
        log.write_text('{"n": 1}\n{"n": ')
        following = Following(log)
        following.until(lambda got: len(got) == 1)
        with open(log, "a") as f:
            f.write("2}\n")

        records = following.until(lambda got: len(got) == 2)
        following.stop()
        assert _lines(records) == [1, 2]

    def test_by_default_it_starts_at_the_end(self, tmp_path: Path) -> None:
        log = tmp_path / "app.log"
        _append(log, '{"n": 1}')
        following = Following(log, start="end")
        # Its first wait comes once it has opened the file at its end and found nothing new:
        # a sleep in its place lost the race under load, the line appended before the open.
        assert following.clock.waited.wait(10), "the follower never waited for more"
        _append(log, '{"n": 2}')

        records = following.until(lambda got: len(got) == 1)
        following.stop()
        assert _lines(records) == [2]

    def test_a_line_holding_the_position_field_is_kept_as_its_text(self, tmp_path: Path) -> None:
        log = tmp_path / "app.log"
        _append(log, '{"_position": "mine", "n": 1}')
        following = Following(log)

        [record] = following.until(lambda got: len(got) == 1)
        following.stop()
        assert record["line"] == '{"_position": "mine", "n": 1}'
        assert record["_position"]["offset"] == 30


class TestRotationAndTruncation:
    def test_a_rotated_file_is_read_to_its_end_then_the_new_one_from_its_start(
        self, tmp_path: Path
    ) -> None:
        log = tmp_path / "app.log"
        _append(log, '{"n": 1}')
        following = Following(log)
        following.until(lambda got: len(got) == 1)
        _append(log, '{"n": 2}')
        log.rename(tmp_path / "app.log.1")
        _append(log, '{"n": 3}')

        records = following.until(lambda got: len(got) == 3)
        following.stop()
        assert _lines(records) == [1, 2, 3]
        assert following.gaps.said == [], "nothing was missed"
        assert records[2]["_position"]["inode"] == os.stat(log).st_ino

    def test_a_truncated_file_is_read_from_its_start_and_the_gap_said(self, tmp_path: Path) -> None:
        log = tmp_path / "app.log"
        _append(log, '{"n": 1}', '{"n": 2}')
        following = Following(log)
        following.until(lambda got: len(got) == 2)
        with open(log, "w") as f:  # copytruncate: the same file, cut to nothing
            f.write('{"n": 3}\n')

        records = following.until(lambda got: len(got) == 3)
        following.stop()
        assert _lines(records) == [1, 2, 3]
        [gap] = following.gaps.said
        assert gap.resumed is False


class TestResume:
    def test_it_goes_on_after_the_position_given_exactly(self, tmp_path: Path) -> None:
        log = tmp_path / "app.log"
        _append(log, '{"n": 1}', '{"n": 2}', '{"n": 3}')
        first = Following(log)
        kept = first.until(lambda got: len(got) >= 2)[1]["_position"]
        first.stop()

        again = Following(log, after=kept)
        records = again.until(lambda got: len(got) == 1)
        again.stop()
        assert _lines(records) == [3]
        assert again.gaps.said == []

    @pytest.mark.parametrize("change", ["replaced", "shorter"])
    def test_a_position_the_file_no_longer_has_is_a_gap_and_a_fresh_start(
        self, tmp_path: Path, change: str
    ) -> None:
        log = tmp_path / "app.log"
        _append(log, '{"n": 1}', '{"n": 2}')
        position = {"inode": os.stat(log).st_ino, "offset": 18}
        if change == "replaced":  # a new file in its place, while the old one still holds its inode
            fresh = tmp_path / "fresh.log"
            fresh.write_text('{"n": 9}\n')
            os.replace(fresh, log)
        else:
            log.write_text('{"n": 9}\n')

        following = Following(log, after=position)
        records = following.until(lambda got: len(got) == 1)
        following.stop()
        assert _lines(records) == [9]
        [gap] = following.gaps.said
        assert gap.resumed is False


class TestTheType:
    def test_it_declares_a_stream_read_at_recon_s_pace(self) -> None:
        plugins = load_plugins()
        declared = plugins.collector_types["tail"].declared
        assert declared.records == "unbounded_recon_paced"
        assert declared.accepts == frozenset({"file"}) and "file" in plugins.source_kinds
        assert declared.effects == frozenset({"filesystem"})

    def test_a_record_of_a_text_line_is_json(self, tmp_path: Path) -> None:
        log = tmp_path / "app.log"
        _append(log, "a line, é")
        following = Following(log)
        [record] = following.until(lambda got: len(got) == 1)
        following.stop()
        assert json.loads(json.dumps(record))["line"] == "a line, é"

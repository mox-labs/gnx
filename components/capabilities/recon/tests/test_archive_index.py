"""Archives in order of their making, not of the clock (the coordinator's ruling on the R slice).
Each archive of a mission gets an ``index``, one more than the last, given under the mission
lock and recorded in its ``meta.yaml``, as a stream's window has its ``index``; the archives
are listed, compared and read in its order. An archive made before indexes has none: those are
ordered by when they began, before every indexed one (the legacy rule)."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING

import yaml

from recon.adapters._out.archive_files import ArchiveFiles
from tests.recon_fakes import FixedClock
from tests.test_changes import Surveys, _mission, _rows

if TYPE_CHECKING:
    from pathlib import Path

A, B, C = ({"id": k, "subject": k} for k in "abc")


def _meta(archive: Path) -> dict[str, object]:
    loaded = yaml.safe_load((archive / "meta.yaml").read_text())
    assert isinstance(loaded, dict)
    return loaded


class TestAClockThatStepsBack:
    def test_the_cursor_still_delivers_the_later_archive_s_rows(self, tmp_path: Path) -> None:
        _mission(tmp_path, {"key": ["id"]})
        surveys = Surveys(tmp_path)
        first, _ = surveys.survey(A, B)
        read = surveys.recon.changes_since("m", "inbox")
        assert [row.key for row in read.rows] == [{"id": "a"}, {"id": "b"}]
        surveys.clock.advance(-3600)  # the host's clock set back an hour
        later, _ = surveys.survey(A, B, C)
        assert later.name < first.name, "its id says it began before the first"
        on = surveys.recon.changes_since("m", "inbox", read.cursor)
        assert [(row.archive, row.key) for row in on.rows] == [(later.name, {"id": "c"})]
        assert _rows(later) == [{"op": "insert", "key": {"id": "c"}, "record": C}], (
            "compared with the archive made before it, not the one its id sorts after"
        )

    def test_each_archive_has_its_index_and_is_listed_by_it(self, tmp_path: Path) -> None:
        _mission(tmp_path, {"key": ["id"]})
        surveys = Surveys(tmp_path)
        first, _ = surveys.survey(A)
        surveys.clock.advance(-3600)
        second, _ = surveys.survey(A, B)
        assert (_meta(first)["index"], _meta(second)["index"]) == (1, 2)
        listed = ArchiveFiles(FixedClock()).archives(tmp_path / ".recon" / "m")
        assert [a.name for a in listed] == [first.name, second.name]
        history = surveys.recon.status("m").missions[0].history
        assert history is not None and [a.id for a in history] == [first.name, second.name]
        assert surveys.recon.query("m", "SELECT 1 AS one").archive == second.name, "the latest"

    def test_an_ack_after_the_step_back_is_later_not_older(self, tmp_path: Path) -> None:
        mission = {"catalog": [{"name": "mail", "url": "https://mail.test"}],
                   "collectors": [{"name": "inbox", "type": "api", "source": "mail",
                                   "endpoint": "/m", "extract": "items",
                                   "changes": {"key": ["id"]}}],
                   "subscriptions": [{"name": "all", "output": "inbox"}]}  # fmt: skip
        (tmp_path / ".recon" / "m").mkdir(parents=True)
        (tmp_path / ".recon" / "m" / "config.yaml").write_text(yaml.safe_dump(mission))
        surveys = Surveys(tmp_path)
        surveys.survey(A)
        surveys.recon.ack("m", "all", surveys.recon.subscribe("m", "all").cursor)
        surveys.clock.advance(-3600)
        surveys.survey(A, B)
        read = surveys.recon.subscribe("m", "all")
        assert [row.key for row in read.rows] == [{"id": "b"}]
        assert surveys.recon.ack("m", "all", read.cursor).outcome == "acked"

    def test_a_retired_archive_s_tombstone_keeps_its_index(self, tmp_path: Path) -> None:
        _mission(tmp_path, {"key": ["id"]}, retain={"archives": 1})
        surveys = Surveys(tmp_path)
        surveys.survey(A)
        surveys.survey(A, B)
        surveys.survey(A, B, C)
        retired = tmp_path / ".recon" / "m" / "archive" / "retired.jsonl"
        stones = [json.loads(line) for line in retired.read_text().splitlines()]
        assert [stone["index"] for stone in stones] == [1, 2]


class TestTheLegacyRule:
    def test_archives_with_no_index_go_first_by_time_and_the_count_goes_on(
        self, tmp_path: Path
    ) -> None:
        _mission(tmp_path, {"key": ["id"]})
        surveys = Surveys(tmp_path)
        old, _ = surveys.survey(A)
        older_still = old.with_name("2000-01-01-000000-000000")
        old.rename(older_still)
        (
            tmp_path / ".recon" / "m" / "archive-index.jsonl"
        ).unlink()  # made before indexes: none for either
        meta = _meta(older_still)
        meta.pop("index")
        (older_still / "meta.yaml").write_text(yaml.safe_dump(meta))
        surveys.clock.advance(-7200)
        new, _ = surveys.survey(A, B)
        listed = ArchiveFiles(FixedClock()).archives(tmp_path / ".recon" / "m")
        assert [a.name for a in listed] == [older_still.name, new.name]
        assert _meta(new)["index"] == 1
        assert [r["key"] for r in _rows(new)] == [{"id": "b"}], "compared with the legacy one"

    def test_a_lost_index_file_is_made_again_from_the_archives_meta(self, tmp_path: Path) -> None:
        _mission(tmp_path, {"key": ["id"]})
        surveys = Surveys(tmp_path)
        surveys.survey(A)
        surveys.survey(A, B)
        (tmp_path / ".recon" / "m" / "archive-index.jsonl").unlink()
        surveys.clock.advance(-3600)
        third, _ = surveys.survey(A, B, C)
        assert _meta(third)["index"] == 3
        listed = ArchiveFiles(FixedClock()).archives(tmp_path / ".recon" / "m")
        assert listed[-1].name == third.name


class TestALostRecordIsReadFromTheArchives:
    """Dijkstra F1: with ``archive-index.jsonl`` gone, the order falls back to what each
    archive's ``meta.yaml`` says, on every read, before any survey makes the record again: the
    clock step, a subscriber's unread archives and retention all stand as they did."""

    def _stepped(self, tmp_path: Path) -> tuple[Surveys, Path, Path]:
        mission = {"catalog": [{"name": "mail", "url": "https://mail.test"}],
                   "collectors": [{"name": "inbox", "type": "api", "source": "mail",
                                   "endpoint": "/m", "extract": "items",
                                   "changes": {"key": ["id"]}, "retain": {"archives": 1}}],
                   "subscriptions": [{"name": "all", "output": "inbox"}]}  # fmt: skip
        (tmp_path / ".recon" / "m").mkdir(parents=True)
        (tmp_path / ".recon" / "m" / "config.yaml").write_text(yaml.safe_dump(mission))
        surveys = Surveys(tmp_path)
        first, _ = surveys.survey(A)
        surveys.clock.advance(-3600)
        second, _ = surveys.survey(A, B)
        (tmp_path / ".recon" / "m" / "archive-index.jsonl").unlink()
        return surveys, first, second

    def test_the_order_is_the_order_made_while_the_record_is_gone(self, tmp_path: Path) -> None:
        surveys, first, second = self._stepped(tmp_path)
        listed = ArchiveFiles(FixedClock()).archives(tmp_path / ".recon" / "m")
        assert [a.name for a in listed] == [first.name, second.name]
        assert ArchiveFiles(FixedClock()).indexes(tmp_path / ".recon" / "m") == {
            first.name: 1, second.name: 2,
        }  # fmt: skip
        assert not (tmp_path / ".recon" / "m" / "archive-index.jsonl").exists(), "it wrote nothing"

    def test_a_subscriber_s_unread_archives_are_still_held(self, tmp_path: Path) -> None:
        surveys, first, second = self._stepped(tmp_path)
        read = surveys.recon.subscribe("m", "all")
        assert [row.key for row in read.rows] == [{"id": "a"}, {"id": "b"}]
        warnings = [w["kind"] for w in surveys.recon.status("m").warnings]
        assert "subscription_behind" in warnings
        assert first.exists() and second.exists()

    def test_the_previous_table_is_the_one_made_before(self, tmp_path: Path) -> None:
        surveys, first, second = self._stepped(tmp_path)
        third, document = surveys.survey(A, B, C)  # its clock is still the hour back
        assert document.tables[0].changes is not None
        assert document.tables[0].changes.previous == second.name
        assert _rows(third) == [{"op": "insert", "key": {"id": "c"}, "record": C}]


def test_a_clock_step_is_one_hour_back(tmp_path: Path) -> None:
    """The fake clock moves back as the tests above need it to."""
    clock = FixedClock()
    before = clock.now()
    clock.advance(-3600)
    assert before - clock.now() == timedelta(hours=1)

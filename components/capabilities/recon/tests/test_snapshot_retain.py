"""Snapshot retention (Knuth, round-4 review S1): ``retain:`` on a snapshot entry, the shape a
stream's has, so a scheduled mission does not grow by a whole table and its told index every
tick for ever. The default is ``all``: nothing is removed unless the mission says so, and a
scheduled mission that says nothing is warned ``retain_unset``. Retention removes whole sealed
archives, oldest first, never the newest, never one being written, never the last one a
table's changes were told in, each with every file it holds (its sidecars too), each leaving
a tombstone. ``archives`` counts archives, a survey's or a stream's window alike."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from recon.application.plan import build_plan
from recon.composition import load_plugins, root
from recon.domain.errors import CollectionError
from recon.domain.http import HttpResponse
from tests.recon_fakes import FixedClock

if TYPE_CHECKING:
    from pathlib import Path

    from recon.domain.collector import Plugins
    from recon.domain.models import SourceEntry


@pytest.fixture(scope="module")
def plugins() -> Plugins:
    return load_plugins()


class Answers:
    """The Requester port answering with ``items``, or failing when ``fails``."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = [{"id": "a"}]
        self.fails = False

    def request(self, source: SourceEntry, method: str, url: str, **kw: Any) -> HttpResponse:
        if self.fails:
            raise CollectionError("the source is down", kind="transient")
        text = json.dumps({"items": self.items})
        return HttpResponse(status_code=200, headers={}, body=text.encode(), text=text, url=url,
                            content_type="application/json")  # fmt: skip

    def get(self, source: SourceEntry, url: str, **kw: Any) -> HttpResponse:
        return self.request(source, "GET", url)


def _entry(name: str = "inbox", **keys: Any) -> dict[str, Any]:
    return {"name": name, "type": "api", "source": "mail", "endpoint": "/m", "extract": "items",
            **keys}  # fmt: skip


def _mission(project: Path, *entries: dict[str, Any], **top: Any) -> None:
    mission = {"catalog": [{"name": "mail", "url": "https://mail.test"}],
               "collectors": list(entries or [_entry()]), **top}  # fmt: skip
    (project / ".recon" / "m").mkdir(parents=True, exist_ok=True)
    (project / ".recon" / "m" / "config.yaml").write_text(yaml.safe_dump(mission))


class Surveys:
    def __init__(self, project: Path) -> None:
        self.project = project
        self.answers = Answers()
        self.clock = FixedClock()
        self.recon = root({}, project=project, clock=self.clock,
                          requester=lambda t, d, r: self.answers)  # fmt: skip

    def survey(self, times: int = 1) -> list[str]:
        for _ in range(times):
            self.clock.advance(60)
            self.recon.survey("m")
        return self.kept()

    def kept(self) -> list[str]:
        home = self.project / ".recon" / "m" / "archive"
        return sorted(p.name for p in home.iterdir() if p.is_dir() and not p.name.startswith("."))

    def tombstones(self) -> list[dict[str, Any]]:
        path = self.project / ".recon" / "m" / "archive" / "retired.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class TestThePlan:
    def test_retain_on_a_snapshot_is_reported_with_its_origin(self, plugins: Plugins) -> None:
        mission = {"catalog": [{"name": "mail", "url": "https://mail.test"}],
                   "collectors": [_entry(retain={"archives": 5}), _entry("other")]}  # fmt: skip
        plan = build_plan(mission, plugins, {})

        assert plan.valid, plan.problems
        values = {v.path: (v.value, v.origin) for v in plan.provenance}
        assert values["collectors.0.retain"] == ({"archives": 5}, "mission")
        assert values["collectors.1.retain"] == ("all", "recon")
        assert not [w for w in plan.warnings if w.kind == "retain_unset"], "unscheduled"

    def test_a_scheduled_mission_that_keeps_every_archive_is_warned(self, plugins: Plugins) -> None:
        mission = {"catalog": [{"name": "mail", "url": "https://mail.test"}],
                   "collectors": [_entry()], "schedule": {"every": 600}}  # fmt: skip
        plan = build_plan(mission, plugins, {})

        [warning] = [w for w in plan.warnings if w.kind == "retain_unset"]
        assert warning.path == "collectors.0.retain"
        assert "retain: {archives: 144}" in warning.fix, "a day of ticks, one each 600s"
        assert "retain: all" in warning.fix

    def test_a_scheduled_mission_that_says_all_is_not_warned(self, plugins: Plugins) -> None:
        mission = {"catalog": [{"name": "mail", "url": "https://mail.test"}],
                   "collectors": [_entry(retain="all")], "schedule": {"every": 600}}  # fmt: skip
        plan = build_plan(mission, plugins, {})

        assert not [w for w in plan.warnings if w.kind == "retain_unset"]


class TestRetention:
    def test_the_newest_windows_archives_are_kept_with_a_tombstone_each(
        self, tmp_path: Path
    ) -> None:
        _mission(tmp_path, _entry(retain={"archives": 2}, changes={"key": ["id"]}))
        surveys = Surveys(tmp_path)
        made = []
        for _ in range(4):
            surveys.survey()
            made.append(surveys.kept()[-1])

        assert surveys.kept() == made[-2:]
        assert [t["id"] for t in surveys.tombstones()] == made[:2]
        assert surveys.tombstones()[0]["rule"] == [{"archives": 2}]
        home = tmp_path / ".recon" / "m" / "archive"
        assert not list(home.glob(".*")), "nothing left half removed"
        assert (home / made[-1] / "inbox.keys.jsonl").exists(), "the kept keep their sidecars"

    def test_nothing_is_removed_by_default(self, tmp_path: Path) -> None:
        _mission(tmp_path)
        assert len(Surveys(tmp_path).survey(4)) == 4

    def test_an_entry_that_keeps_all_keeps_every_archive_it_shares(self, tmp_path: Path) -> None:
        """An archive holds every entry's tables: it goes only when every entry lets it go."""
        _mission(tmp_path, _entry(retain={"archives": 1}), _entry("other"))
        assert len(Surveys(tmp_path).survey(3)) == 3

    def test_the_newest_is_kept_whatever_the_rule(self, tmp_path: Path) -> None:
        _mission(tmp_path, _entry(retain={"bytes": 1}))
        surveys = Surveys(tmp_path)
        newest = surveys.survey(3)[-1]
        assert surveys.kept() == [newest]

    def test_seconds_keeps_the_archives_that_ended_within_them(self, tmp_path: Path) -> None:
        _mission(tmp_path, _entry(retain={"seconds": 150}))
        surveys = Surveys(tmp_path)
        assert len(surveys.survey(5)) == 3, "a survey a minute: 150 s keeps the last three"

    def test_the_last_archive_a_table_was_told_in_is_kept(self, tmp_path: Path) -> None:
        """The newest archive's table failed: the told index the next survey compares with is
        the one before, which the rule would remove. It is kept, so nothing is told again."""
        _mission(tmp_path, _entry(retain={"archives": 1}, changes={"key": ["id"]}))
        surveys = Surveys(tmp_path)
        told = surveys.survey()[-1]
        surveys.answers.fails = True
        surveys.survey()
        assert told in surveys.kept()
        surveys.answers.fails = False
        surveys.survey()
        newest = tmp_path / ".recon" / "m" / "archive" / surveys.kept()[-1]
        rows = (newest / "inbox.changes.jsonl").read_text().splitlines()
        assert [json.loads(r).get("_empty") for r in rows] == [True], "nothing told again"

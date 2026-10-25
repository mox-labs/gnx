"""Retention and subscriptions (the coordinator's ruling on the R slice): a notification lost in
silence is worse than disk used, so ``retain:`` never removes an archive (or a stream's window)
holding change rows a subscription the config declares has not acked. While one holds archives
back, the plan and ``status`` warn ``subscription_behind``, naming it and how many archives it
holds, with what to do."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from recon.composition import root
from recon.domain.subscriptions import Delivered
from tests.recon_fakes import VirtualClock, plugins_with
from tests.test_changes import Surveys, _Feed, _stream_mission, _windows

if TYPE_CHECKING:
    from pathlib import Path

A, B, C, D = ({"id": k, "subject": k} for k in "abcd")


def _mission(project: Path, subscriptions: list[dict[str, Any]]) -> None:
    mission = {
        "catalog": [{"name": "mail", "url": "https://mail.test"}],
        "collectors": [{"name": "inbox", "type": "api", "source": "mail", "endpoint": "/m",
                        "extract": "items", "changes": {"key": ["id"]},
                        "retain": {"archives": 1}}],
        "subscriptions": subscriptions,
    }  # fmt: skip
    (project / ".recon" / "m").mkdir(parents=True, exist_ok=True)
    (project / ".recon" / "m" / "config.yaml").write_text(yaml.safe_dump(mission))


def _archives(project: Path) -> list[str]:
    home = project / ".recon" / "m" / "archive"
    return sorted(p.name for p in home.iterdir() if p.is_dir())


def _behind(document: Any) -> list[dict[str, Any]]:
    warnings = document.warnings if isinstance(document.warnings, list) else []
    return [w for w in warnings if w["kind"] == "subscription_behind"]


class TestASubscriptionHoldsItsArchives:
    def test_unacked_rows_keep_their_archives_past_retain_and_say_so(self, tmp_path: Path) -> None:
        _mission(tmp_path, [{"name": "all", "output": "inbox"}])
        surveys = Surveys(tmp_path)
        for messages in ((A,), (A, B), (A, B, C)):
            surveys.survey(*messages)
        assert len(_archives(tmp_path)) == 3, "retain: {archives: 1} held back by the subscription"
        for document in (surveys.recon.status("m"), surveys.recon.plan("m")):
            [warning] = _behind(document)
            assert warning["path"] == "subscriptions.0" and warning["got"] == 2
            assert "all" in warning["message"] and warning["fix"]
        read = surveys.recon.subscribe("m", "all")
        assert [row.key for row in read.rows] == [{"id": "a"}, {"id": "b"}, {"id": "c"}]

    def test_acking_lets_them_go(self, tmp_path: Path) -> None:
        _mission(tmp_path, [{"name": "all", "output": "inbox"}])
        surveys = Surveys(tmp_path)
        for messages in ((A,), (A, B), (A, B, C)):
            surveys.survey(*messages)
        surveys.recon.ack("m", "all", surveys.recon.subscribe("m", "all").cursor)
        assert _behind(surveys.recon.status("m")) == []
        surveys.survey(A, B, C, D)
        assert len(_archives(tmp_path)) == 1, "the newest, which holds D's row, alone"

    def test_a_subscription_part_read_holds_the_archive_it_is_in_and_those_after(
        self, tmp_path: Path
    ) -> None:
        _mission(tmp_path, [{"name": "all", "output": "inbox"}])
        surveys = Surveys(tmp_path)
        surveys.survey(A, B)
        first = _archives(tmp_path)[0]
        surveys.recon.ack("m", "all", surveys.recon.subscribe("m", "all", max_records=1).cursor)
        surveys.survey(A, B)  # no change: an empty change table holds nothing
        surveys.survey(A, B, C)
        assert first in _archives(tmp_path), "b is in it, unacked"
        assert len(_archives(tmp_path)) == 2

    def test_removing_the_subscription_from_the_config_lets_them_go(self, tmp_path: Path) -> None:
        _mission(tmp_path, [{"name": "all", "output": "inbox"}])
        surveys = Surveys(tmp_path)
        for messages in ((A,), (A, B), (A, B, C)):
            surveys.survey(*messages)
        _mission(tmp_path, [])
        assert _behind(surveys.recon.status("m")) == []
        surveys.survey(A, B, C)
        assert len(_archives(tmp_path)) == 1

    def test_a_stream_s_windows_are_held_as_a_snapshot_s_archives(self, tmp_path: Path) -> None:
        _stream_mission(tmp_path, retain={"archives": 1})
        config = tmp_path / ".recon" / "m" / "config.yaml"
        mission = yaml.safe_load(config.read_text())
        mission["subscriptions"] = [{"name": "log", "output": "s"}]
        config.write_text(yaml.safe_dump(mission))
        records = [{"id": k} for k in "abcdef"]
        recon = root({}, project=tmp_path, clock=VirtualClock(),
                     plugins=plugins_with(feed=_Feed(records)))  # fmt: skip
        recon.stream("m", "s", windows=3)
        assert len(_windows(tmp_path)) == 3
        [warning] = _behind(recon.status("m"))
        assert warning["got"] == 2
        recon.ack("m", "log", recon.subscribe("m", "log").cursor)
        recon.stream("m", "s", windows=1)
        assert len(_windows(tmp_path)) == 1


class Recording:
    """The Deliverer port, recording each payload and running nothing."""

    def __init__(self) -> None:
        self.given: list[bytes] = []

    def deliver(self, argv: Any, payload: bytes, **kw: Any) -> Delivered:
        self.given.append(payload)
        return Delivered(True, 0)


class TestTheHoldIsBounded:
    """Erlang F2: a subscription nobody reads must not grow a mission for ever. It holds an
    archive for ``max_lag`` seconds (30 days by default, which the plan reports); past that,
    retention goes ahead and the subscriber is told the archives went, ``changes_retired``."""

    def _surveyed(self, tmp_path: Path, max_lag: int) -> Surveys:
        _mission(tmp_path, [{"name": "all", "output": "inbox", "max_lag": max_lag,
                             "deliver": {"command": "/usr/bin/true"}}])  # fmt: skip
        surveys = Surveys(tmp_path)
        surveys.survey(A)
        surveys.clock.advance(100)
        surveys.survey(A, B)
        surveys.survey(A, B, C)
        return surveys

    def test_past_max_lag_the_archives_retire_and_the_subscriber_is_told(
        self, tmp_path: Path
    ) -> None:
        surveys = self._surveyed(tmp_path, max_lag=50)
        assert len(_archives(tmp_path)) == 2, "the first, 100 s old, went; the second is held"
        recording = Recording()
        recon = root({}, project=tmp_path, clock=surveys.clock, deliverer=recording,
                     requester=lambda t, d, r: surveys.inbox)  # fmt: skip
        document = recon.deliver("m")
        assert [w["kind"] for w in document.warnings] == ["changes_retired"]
        assert document.deliveries[0].retired_range is not None
        assert len(recording.given) == 2, "b and c, what was kept"

    def test_within_max_lag_they_are_held(self, tmp_path: Path) -> None:
        self._surveyed(tmp_path, max_lag=1000)
        assert len(_archives(tmp_path)) == 3

    def test_the_default_is_30_days_and_the_plan_says_so(self, tmp_path: Path) -> None:
        _mission(tmp_path, [{"name": "all", "output": "inbox"}])
        plan = Surveys(tmp_path).recon.plan("m")
        said = {value.path: (value.value, value.origin) for value in plan.provenance}
        assert said["subscriptions.0.max_lag"] == (2592000.0, "recon")

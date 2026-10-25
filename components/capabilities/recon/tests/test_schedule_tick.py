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

import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest
import yaml
from click.testing import CliRunner

from recon.adapters._in.cli import main
from recon.adapters._out.archive_files import ArchiveFiles
from recon.adapters._out.mission_files import MissionFiles
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
    from collections.abc import Callable

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


def _root(
    project: Path,
    host: Host,
    clock: Clock,
    slow: Slow | None = None,
    local: Callable[[datetime], datetime] = lambda at: at,
) -> Recon:
    return root(
        {"PATH": "/usr/bin:/bin"}, project=project, clock=clock,
        plugins=plugins_with(slow=slow or _going()), schedulers=host.scheduler,
        schedule_host=ScheduleHost("/opt/recon/bin/recon", ("/usr/bin", "/bin")),
        local=local,
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
        # Changed requirement (review F2): the line also names its tick's warning kinds.
        assert set(line) == {"scheduled", "started", "ended", "exit", "kind", "archive",
                             "skipped_by", "late_s", "warning_kinds"}  # fmt: skip
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
        # The changed command reaches no network and nothing outside tmp_path, so a guard
        # that regressed runs nothing that harms (Vector's re-check).
        pwned = tmp_path / "pwned"
        changed = f"cli\n  run: touch '{pwned}'"
        config.write_text(config.read_text().replace("slow", changed))

        with pytest.raises(ReconError) as raised:
            _root(tmp_path, host, clock).schedule_tick("m", config_sha256=digest)

        assert raised.value.kind == "stale" and raised.value.exit_code == 12
        assert "recon schedule install m" in (raised.value.fix or "")
        [line] = _log(mission)
        assert (line["skipped_by"], line["kind"], line["exit"]) == ("config_changed", "stale", 12)
        assert not (mission / "archive").exists() and not pwned.exists()
        config.write_text(config.read_text().replace(changed, "slow"))
        assert _root(tmp_path, host, clock).schedule_tick("m", config_sha256=digest).survey

    def test_a_config_swapped_after_the_check_is_never_run(
        self, tmp_path: Path, host: Host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dijkstra and Vector F4: the digest a tick checks is of the bytes it plans and runs,
        read once under the mission lock. A config swapped between a first read and the one
        the survey makes is refused ``stale``, and nothing of it runs."""
        mission = _mission(tmp_path)
        clock = FixedClock(SLOT)
        digest = _installed(tmp_path, host, clock)
        config = mission / "config.yaml"
        pwned = tmp_path / "pwned"
        evil = config.read_text().replace("slow", f"cli\n  run: touch '{pwned}'")
        read_config = MissionFiles.read_config
        reads: list[Path] = []

        def swapped_first(self: MissionFiles, path: Path) -> Any:
            if not reads:  # the swap lands as the config is read to be run
                config.write_text(evil)
            reads.append(path)
            return read_config(self, path)

        monkeypatch.setattr(MissionFiles, "read_config", swapped_first)
        with pytest.raises(ReconError) as raised:
            _root(tmp_path, host, clock).schedule_tick("m", config_sha256=digest)

        assert raised.value.kind == "stale"
        assert not (mission / "archive").exists() and not pwned.exists()
        [line] = _log(mission)
        assert (line["skipped_by"], line["kind"]) == ("config_changed", "stale")


class TestStaleIsItsOwnKind:
    def test_stale_has_its_own_code_meaning_and_next_step_and_conflict_keeps_its_one(
        self,
    ) -> None:
        """One kind, one next step (rule 18): a tick's changed config is acted on by
        installing the schedule again, which is not conflict's ``--replace``."""
        from recon.domain.errors import ERROR_KINDS, EXIT_CODES, EXIT_MEANINGS, EXIT_NEXT

        assert "stale" in ERROR_KINDS and EXIT_CODES["stale"] == 12
        assert "recon schedule install" in EXIT_NEXT["stale"]
        assert "schedule" in EXIT_MEANINGS["stale"]
        assert "tick" not in EXIT_MEANINGS["conflict"] + EXIT_NEXT["conflict"]
        assert EXIT_MEANINGS["conflict"] == (
            "init, or survey -c, would overwrite a different mission config"
        )


NEW_YORK = ZoneInfo("America/New_York")


def _new_york(at: datetime) -> datetime:
    return at.astimezone(NEW_YORK)


class TestASpringForward:
    """Round-4 review M2, the choice made: a tick whose grid time the clocks skipped (New
    York's 02:22 on 2026-03-08, when 02:00 EST became 03:00 EDT) is due when the clocks
    reached past it, 03:00 EDT, which is when a scheduler can start it: it is on time, and
    ``missed: skip`` runs it."""

    EVERY = 3600
    #: 03:00:30 EDT, thirty seconds after the jump.
    NOW = datetime(2026, 3, 8, 7, 0, 30, tzinfo=UTC)

    def test_a_skipped_grid_time_is_due_at_the_jump_and_on_time(
        self, tmp_path: Path, host: Host
    ) -> None:
        _mission(tmp_path, missed="skip")
        config = tmp_path / ".recon" / "m" / "config.yaml"
        config.write_text(config.read_text().replace("every: 600", f"every: {self.EVERY}"))
        assert offset_s("m", self.EVERY) == 22 * 60, "its ticks fall at :22, 02:22 among them"
        install = FixedClock(datetime(2026, 3, 7, 20, 0, tzinfo=UTC))
        digest = (
            _root(tmp_path, host, install, local=_new_york)
            .schedule_install("m", platform="launchd")
            .config_sha256
            or ""
        )

        tick = _root(tmp_path, host, FixedClock(self.NOW), local=_new_york).schedule_tick(
            "m", config_sha256=digest
        )

        assert tick.skipped_by is None and tick.survey is not None
        assert tick.scheduled_at == "2026-03-08T03:00:00-04:00"
        assert tick.late_s == 30.0


class TestADaylightSavingChange:
    """Lateness, and so the missed-tick grace, is the seconds that passed: UTC epoch time,
    never the difference of two wall-clock readings. On 2026-11-01 New York's clocks fall
    back from 02:00 EDT to 01:00 EST: a tick due at 00:MM EDT and started at 01:30 EST
    started 2.5 h, not 1.5 h, after it was due."""

    EVERY = 10800
    #: When the clocks are back at 01:30, local time, a second time: 06:30 UTC.
    NOW = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)

    def _mission(self, project: Path, missed: str) -> tuple[Path, int]:
        """A mission with ``every: 10800``, and its offset, which this test needs under an
        hour: the tick due at 00:MM EDT (04:MM UTC) is then the latest one at 01:30."""
        _mission(project, missed=missed)
        config = project / ".recon" / "m" / "config.yaml"
        config.write_text(config.read_text().replace("every: 600", f"every: {self.EVERY}"))
        offset = offset_s("m", self.EVERY)
        assert offset < 3600, "a mission name whose ticks fall in the first hour"
        return project / ".recon" / "m", offset

    def _install(self, project: Path, host: Host) -> str:
        clock = FixedClock(datetime(2026, 10, 31, 20, 0, tzinfo=UTC))
        installed = _root(project, host, clock, local=_new_york).schedule_install(
            "m", platform="launchd"
        )
        return installed.config_sha256 or ""

    def test_a_tick_across_the_change_is_as_late_as_the_seconds_that_passed(
        self, tmp_path: Path, host: Host
    ) -> None:
        _, offset = self._mission(tmp_path, "once")
        digest = self._install(tmp_path, host)
        clock = FixedClock(self.NOW)

        tick = _root(tmp_path, host, clock, local=_new_york).schedule_tick(
            "m", config_sha256=digest
        )

        due = datetime(2026, 11, 1, 4, 0, tzinfo=UTC) + timedelta(seconds=offset)
        assert tick.late_s == (self.NOW - due).total_seconds() == 9000 - offset
        assert tick.scheduled_at == due.astimezone(NEW_YORK).isoformat()
        assert tick.scheduled_at is not None and tick.scheduled_at.endswith("-04:00")

    def test_status_is_as_late_as_the_seconds_that_passed_and_the_next_tick_is_est(
        self, tmp_path: Path, host: Host
    ) -> None:
        _, offset = self._mission(tmp_path, "once")
        self._install(tmp_path, host)

        status = _root(tmp_path, host, FixedClock(self.NOW), local=_new_york).schedule_status(
            "m", platform="launchd"
        )

        [listed] = status.schedules
        assert listed.late_s == 9000 - offset
        after = datetime(2026, 11, 1, 8, 0, tzinfo=UTC) + timedelta(seconds=offset)
        assert listed.next_at == after.astimezone(NEW_YORK).isoformat()
        assert listed.next_at is not None and listed.next_at.endswith("-05:00")

    def test_skip_judges_the_grace_by_the_seconds_that_passed(
        self, tmp_path: Path, host: Host
    ) -> None:
        mission, offset = self._mission(tmp_path, "skip")
        digest = self._install(tmp_path, host)

        tick = _root(tmp_path, host, FixedClock(self.NOW), local=_new_york).schedule_tick(
            "m", config_sha256=digest
        )

        assert tick.skipped_by == "missed" and tick.late_s == 9000 - offset
        [line] = _log(mission)
        assert line["late_s"] == 9000 - offset

    def test_a_tick_on_time_after_the_change_is_on_time(self, tmp_path: Path, host: Host) -> None:
        """The first tick after the change, 03:MM EST, started 20 s after it: on time, run."""
        _, offset = self._mission(tmp_path, "skip")
        digest = self._install(tmp_path, host)
        due = datetime(2026, 11, 1, 8, 0, tzinfo=UTC) + timedelta(seconds=offset)
        clock = FixedClock(due + timedelta(seconds=20))

        tick = _root(tmp_path, host, clock, local=_new_york).schedule_tick(
            "m", config_sha256=digest
        )

        assert tick.survey is not None and tick.late_s == 20.0


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

    def test_a_line_longer_than_the_cap_is_written_alone_never_rotated_for_ever(
        self, tmp_path: Path
    ) -> None:
        """Round-4 review M1: a line past the cap rotated an empty log to make room, and again,
        for ever. A log is rotated only when it holds something; a line too long for any log
        is written alone into a fresh one."""
        store = ScheduleFiles(max_bytes=64)
        store.append(tmp_path, {"n": 1})
        done = threading.Event()

        def append() -> None:
            store.append(tmp_path, {**self.LINE, "n": 2})
            done.set()

        threading.Thread(target=append, daemon=True).start()
        assert done.wait(5), "the append returned"
        assert [line.get("n") for line in store.lines(tmp_path)] == [1, 2]
        assert (tmp_path / SCHEDULE_LOG).stat().st_size > 64, "the long line, alone"

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

    def test_tick_surveys_the_project_it_is_given_not_the_one_around_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``--project`` is the project, as installed: a mission of a directory inside a
        repository (a library caller's ``root=``) is not looked for at the repository's root."""
        (tmp_path / ".git").mkdir()
        project = tmp_path / "sub"
        mission = _mission(project, type_="cli")
        config = yaml.safe_load((mission / "config.yaml").read_text())
        config["collectors"][0]["run"] = "echo '{\"a\": 1}'"
        (mission / "config.yaml").write_text(yaml.safe_dump(config))
        digest = hashlib.sha256((mission / "config.yaml").read_bytes()).hexdigest()
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(
            main, ["schedule", "tick", "m", "--project", str(project), "--config-sha256", digest,
                   "--json"], catch_exceptions=False,
        )  # fmt: skip

        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout)["survey"]["status"] == "complete"
        assert len(_log(mission)) == 1

    def test_tick_with_another_digest_exits_12_stale(
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

        assert result.exit_code == 12
        assert json.loads(result.stderr)["error"]["kind"] == "stale"

"""Rulings S3 and S4: a schedule projected onto the operating system's scheduler is a pure
function, ``project(run, platform) -> files``, and what it writes is checked here without
installing anything (ixian round 4, C: S1-S3).

- **Golden files**: each platform's files, byte for byte; they change only when regenerated
  on purpose (``RECON_UPDATE_GOLDEN=1``).
- **The platforms' own validators**: ``plutil -lint`` and ``systemd-analyze verify`` read the
  files and install nothing; each runs on its own platform.
- **Cadence**: the next five times each projected file fires, read back from the file by a
  parser of its own here, are the next five the config says; and the check fails a projection
  of the wrong ``every`` (check the checker).
- **What the files hold**: an absolute recon in argv form, the mission and the project, and a
  PATH of absolute directories; output to ``/dev/null``; never a shell for launchd and systemd.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from recon.domain.errors import UsageError
from recon.domain.schedule import (
    PLATFORMS,
    Platform,
    ProjectedFile,
    ScheduledRun,
    fire_times,
    project,
)

GOLDEN = Path(__file__).parent / "fixtures" / "schedules"
UPDATE = os.environ.get("RECON_UPDATE_GOLDEN") == "1"

SHA = "0" * 63 + "1"


def _run(every: int = 600, offset_s: int = 420, **changes: object) -> ScheduledRun:
    fields: dict[str, object] = {
        "label": "recon.1a2b3c4d.inbox",
        "mission": "inbox",
        "project": "/home/someone/mail project",
        "recon": "/home/someone/.local/bin/recon",
        "path": ("/home/someone/.local/bin", "/usr/bin", "/bin"),
        "config_sha256": SHA,
        "every": every,
        "offset_s": offset_s,
        "missed": "once",
    }
    return ScheduledRun(**{**fields, **changes})  # type: ignore[arg-type]


def _texts(files: tuple[ProjectedFile, ...]) -> dict[str, str]:
    return {f.name: f.text for f in files}


# --- golden files ------------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["launchd", "systemd", "cron"])
@pytest.mark.parametrize(("every", "offset_s"), [(600, 420), (5400, 1380)])
def test_each_platform_s_files_are_the_golden_ones(
    platform: Platform, every: int, offset_s: int
) -> None:
    files = project(_run(every, offset_s), platform)

    for name, text in _texts(files).items():
        golden = GOLDEN / f"{every}" / name
        if UPDATE:
            golden.parent.mkdir(parents=True, exist_ok=True)
            golden.write_text(text, encoding="utf-8")
        assert golden.is_file(), f"no golden {golden}: regenerate with RECON_UPDATE_GOLDEN=1"
        assert text == golden.read_text(encoding="utf-8"), name


def test_each_platform_projects_its_own_files() -> None:
    assert sorted(_texts(project(_run(), "launchd"))) == ["recon.1a2b3c4d.inbox.plist"]
    assert sorted(_texts(project(_run(), "systemd"))) == [
        "recon.1a2b3c4d.inbox.service",
        "recon.1a2b3c4d.inbox.timer",
    ]
    assert sorted(_texts(project(_run(), "cron"))) == ["recon.1a2b3c4d.inbox.cron"]


def test_the_projection_is_a_pure_function() -> None:
    for platform in PLATFORMS:
        assert project(_run(), platform) == project(_run(), platform)


# --- the platforms' own validators -------------------------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="plutil -lint runs on macOS")
def test_plutil_lints_the_launchd_agent(tmp_path: Path) -> None:
    if shutil.which("plutil") is None:
        pytest.skip("plutil is not on this machine")
    for every, offset_s in ((600, 420), (5400, 1380), (60, 0)):
        [plist] = project(_run(every, offset_s), "launchd")
        path = tmp_path / plist.name
        path.write_text(plist.text, encoding="utf-8")
        checked = subprocess.run(["plutil", "-lint", str(path)], capture_output=True, text=True)
        assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.skipif(sys.platform != "linux", reason="systemd-analyze verify runs on Linux")
def test_systemd_analyze_verifies_the_timer_and_its_service(tmp_path: Path) -> None:
    """``verify`` checks the command is executable and the directory is there, so the run
    names this Python and a real directory; it loads the files and installs nothing."""
    if shutil.which("systemd-analyze") is None:
        pytest.skip("systemd-analyze is not on this machine")
    run = _run(recon=sys.executable, project=str(tmp_path), path=("/usr/bin", "/bin"))
    units = tmp_path / "units"
    units.mkdir()
    for unit in project(run, "systemd"):
        (units / unit.name).write_text(unit.text, encoding="utf-8")
    checked = subprocess.run(
        ["systemd-analyze", "verify", *sorted(str(p) for p in units.iterdir())],
        capture_output=True, text=True,
    )  # fmt: skip
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "recon" not in checked.stderr, checked.stderr


# --- cadence -----------------------------------------------------------------------------


def _launchd_times(text: str) -> set[tuple[int, int]]:
    """(hour, minute) of each time of day the agent fires, read with plistlib."""
    agent = plistlib.loads(text.encode())
    found = set()
    for entry in agent["StartCalendarInterval"]:
        assert set(entry) <= {"Hour", "Minute"}, entry
        hours = [entry["Hour"]] if "Hour" in entry else range(24)
        found |= {(hour, entry["Minute"]) for hour in hours}
    return found


def _field(text: str, top: int, width: int = 1) -> list[int]:
    return list(range(top)) if text == "*" else [int(part) for part in text.split(",")]


def _systemd_times(text: str) -> set[tuple[int, int]]:
    found = set()
    for line in text.splitlines():
        if not line.startswith("OnCalendar="):
            continue
        date, clock = line.removeprefix("OnCalendar=").split(" ")
        assert date == "*-*-*", line
        hours, minutes, seconds = clock.split(":")
        assert seconds == "00", line
        found |= {(h, m) for h in _field(hours, 24) for m in _field(minutes, 60)}
    return found


def _cron_times(text: str) -> set[tuple[int, int]]:
    found = set()
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        minutes, hours, days, months, weekdays, *_ = line.split(" ")
        assert (days, months, weekdays) == ("*", "*", "*"), line
        found |= {(h, m) for h in _field(hours, 24) for m in _field(minutes, 60)}
    return found


def _projected_times(platform: Platform, files: tuple[ProjectedFile, ...]) -> set[tuple[int, int]]:
    texts = _texts(files)
    if platform == "launchd":
        return _launchd_times(next(iter(texts.values())))
    if platform == "systemd":
        return _systemd_times(next(t for n, t in texts.items() if n.endswith(".timer")))
    return _cron_times(next(iter(texts.values())))


def _next(times: set[tuple[int, int]], after: datetime, count: int) -> list[datetime]:
    """The next ``count`` wall-clock times after ``after`` at one of ``times`` of the day."""
    found: list[datetime] = []
    day = after.replace(hour=0, minute=0, second=0, microsecond=0)
    while len(found) < count:
        for hour, minute in sorted(times):
            at = day.replace(hour=hour, minute=minute)
            if at > after and len(found) < count:
                found.append(at)
        day += timedelta(days=1)
    return found


def _from_config(every: int, offset_s: int, after: datetime, count: int) -> list[datetime]:
    """The next ``count`` ticks the config says: the times of day ``offset_s`` + k × ``every``."""
    day = after.replace(hour=0, minute=0, second=0, microsecond=0)
    k = 0
    found: list[datetime] = []
    while len(found) < count:
        at = day + timedelta(seconds=offset_s + k * every)
        if at > after:
            found.append(at)
        k += 1
    return found


def _assert_cadence(
    platform: Platform, files: tuple[ProjectedFile, ...], every: int, offset_s: int
) -> None:
    times = _projected_times(platform, files)
    for after in (datetime(2026, 10, 24, 0, 0), datetime(2026, 10, 24, 23, 58, 30),
                  datetime(2026, 12, 31, 13, 7)):  # fmt: skip
        assert _next(times, after, 5) == _from_config(every, offset_s, after, 5), after


@pytest.mark.parametrize("platform", ["launchd", "systemd", "cron"])
@pytest.mark.parametrize(("every", "offset_s"),
                         [(60, 0), (600, 420), (3600, 1980), (5400, 1380), (7200, 3660),
                          (86400, 3600 * 5 + 60 * 17)])  # fmt: skip
def test_the_next_five_ticks_of_each_file_are_the_config_s(
    platform: Platform, every: int, offset_s: int
) -> None:
    files = project(_run(every, offset_s), platform)

    _assert_cadence(platform, files, every, offset_s)


@pytest.mark.parametrize("platform", ["launchd", "systemd", "cron"])
def test_the_cadence_check_fails_a_projection_of_the_wrong_every(platform: Platform) -> None:
    """Check the checker: ``every: 600`` projected as if it were 60, the slip a duration in
    the wrong unit makes, is caught."""
    wrong = project(_run(60, 0), platform)

    with pytest.raises(AssertionError):
        _assert_cadence(platform, wrong, 600, 0)


def test_the_ticks_are_the_daily_grid_of_offset_plus_every() -> None:
    assert fire_times(600, 420)[:3] == (420, 1020, 1620)
    assert len(fire_times(600, 420)) == 144
    assert fire_times(86400, 3600) == (3600,)


# --- what the files hold -----------------------------------------------------------------


def test_launchd_runs_recon_by_argv_from_the_project_with_its_path_and_no_output() -> None:
    [plist] = project(_run(), "launchd")
    agent = plistlib.loads(plist.text.encode())

    assert agent["Label"] == "recon.1a2b3c4d.inbox"
    assert agent["ProgramArguments"] == [
        "/home/someone/.local/bin/recon", "schedule", "tick", "inbox",
        "--project", "/home/someone/mail project", "--config-sha256", SHA,
    ]  # fmt: skip
    assert agent["WorkingDirectory"] == "/home/someone/mail project"
    assert agent["EnvironmentVariables"] == {"PATH": "/home/someone/.local/bin:/usr/bin:/bin"}
    assert agent["StandardOutPath"] == agent["StandardErrorPath"] == "/dev/null"
    assert "sh" not in agent["ProgramArguments"][0]


def test_systemd_runs_one_shot_from_the_project_and_keeps_a_missed_tick_as_ruled() -> None:
    texts = _texts(project(_run(), "systemd"))
    service, timer = texts["recon.1a2b3c4d.inbox.service"], texts["recon.1a2b3c4d.inbox.timer"]

    assert "Type=oneshot" in service
    assert "WorkingDirectory=/home/someone/mail project" in service
    assert "Environment=PATH=/home/someone/.local/bin:/usr/bin:/bin" in service
    assert ('ExecStart=/home/someone/.local/bin/recon schedule tick inbox --project '
            f'"/home/someone/mail project" --config-sha256 {SHA}') in service  # fmt: skip
    assert "sh -c" not in service
    assert "Persistent=true" in timer
    assert (
        "Persistent=false"
        in _texts(project(_run(missed="skip"), "systemd"))["recon.1a2b3c4d.inbox.timer"]
    )
    assert "AccuracySec=1s" in timer, "a tick fires on time, so skip's grace is not spent"


def test_cron_quotes_each_argument_and_sends_its_output_nowhere() -> None:
    [entry] = project(_run(), "cron")
    [line] = [line for line in entry.text.splitlines() if not line.startswith("#")]

    assert line.endswith(">/dev/null 2>&1")
    assert ("/usr/bin/env 'PATH=/home/someone/.local/bin:/usr/bin:/bin' "
            "'/home/someone/.local/bin/recon' 'schedule' 'tick' 'inbox' '--project' "
            f"'/home/someone/mail project' '--config-sha256' '{SHA}'") in line  # fmt: skip


@pytest.mark.parametrize(
    ("changes", "says"),
    [
        ({"mission": "../etc"}, "mission name"),
        ({"project": "relative/dir"}, "absolute"),
        ({"recon": "recon"}, "absolute"),
        ({"path": ("/usr/bin", ".")}, "absolute"),
        ({"path": ("/usr/bin", "")}, "absolute"),
        ({"project": "/a\nb"}, "line"),
        ({"offset_s": 30}, "offset"),
        ({"offset_s": 600}, "offset"),
    ],
)
def test_what_no_scheduler_file_may_hold_is_refused(changes: dict[str, Any], says: str) -> None:
    for platform in PLATFORMS:
        with pytest.raises(UsageError, match=says):
            project(_run(**changes), platform)


def test_cron_refuses_a_percent_sign_which_cron_reads_as_a_newline() -> None:
    with pytest.raises(UsageError, match="%"):
        project(_run(project="/home/someone/100%"), "cron")
    assert project(_run(project="/home/someone/100%"), "systemd"), "systemd escapes it: %%"
    [service] = [f for f in project(_run(project="/x/100%"), "systemd") if f.name.endswith("ice")]
    assert "/x/100%%" in service.text and re.search(r"[^%]%[^%]", service.text) is None

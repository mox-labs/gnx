"""Schedules: a mission that runs itself, by the operating system's own scheduler.

A mission declares ``schedule: {every, missed}`` and recon projects it onto the scheduler the
host has (launchd, systemd's user timers, or cron): there is no recon daemon. Each tick the
scheduler starts is one survey of the mission (``recon schedule tick``).

``every`` is bare seconds, as every duration in a recon config is. It is a whole number of
minutes that divides a day, so the ticks fall on one daily grid of wall-clock times that every
scheduler keeps alike: launchd and cron count in minutes, and a systemd timer that runs a
missed tick (``Persistent=true``) is a calendar, which repeats daily. ``missed`` says what a
tick missed while the host was asleep or off becomes: ``once``, the missed ticks coalesced
into one run when the host is back (the default), or ``skip``, none. Missed ticks are never
caught up one by one: that would be a burst of surveys against each source's rate limit.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from recon.domain.errors import UsageError
from recon.domain.missions import MISSION_NAME

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

#: What a tick the host missed (asleep, off) becomes: one run once it is back, or none.
Missed = Literal["once", "skip"]

#: The shortest ``every``, in seconds.
MIN_EVERY_S = 60

#: The seconds in a day: the grid every schedule's ticks fall on repeats daily.
DAY_S = 86400

#: Every ``every`` a schedule may have, in seconds: the whole minutes that divide a day.
EVERY_ALLOWED: tuple[int, ...] = tuple(m * 60 for m in range(1, 1441) if 1440 % m == 0)

#: A scheduled mission's ``deadline:``, unless it sets one, as a share of ``every``: a tick
#: ends before the next one is due, so two ticks do not overlap (ruling S2).
DEADLINE_SHARE = 0.8

#: How late a tick may start and still be the one that was due, in seconds: the grid's own
#: grain. Under ``missed: skip`` a later one is the scheduler catching up after sleep, and is
#: skipped.
MISSED_GRACE_S = 60.0

#: The skipped ticks in a row, each refused ``busy``, that make an overrun: the mission's
#: survey takes longer than ``every``.
OVERRUN_SKIPS = 2

#: Why a tick ran no survey: another held the mission (``busy``), it was a missed tick under
#: ``missed: skip``, or the config is not the one installed.
Skipped = Literal["busy", "missed", "config_changed"]


def nearest_every(every: int) -> tuple[int, ...]:
    """The allowed ``every`` nearest ``every``: the one below it and the one above, or the one
    at the end of the range it is past."""
    below = [e for e in EVERY_ALLOWED if e < every]
    above = [e for e in EVERY_ALLOWED if e > every]
    return tuple(([below[-1]] if below else []) + ([above[0]] if above else []))


def every_fix(every: int) -> str:
    """The fix for an ``every`` no scheduler keeps: the nearest that one does."""
    nearest = " or ".join(f"every: {e}" for e in nearest_every(every))
    return f"set {nearest}: whole minutes that divide a day (86400 s), in seconds"


class ScheduleSpec(BaseModel):
    """``schedule:``: run the mission every ``every`` seconds, by the operating system's
    scheduler, a tick missed while the host was away run ``once`` when it is back or
    ``skip``-ped. ``every`` has no default: how often is the one thing only the mission's
    author knows, and no ``schedule:`` at all is the default."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    every: int = Field(ge=MIN_EVERY_S)
    missed: Missed = "once"

    @field_validator("every")
    @classmethod
    def _on_a_daily_grid(cls, value: int) -> int:
        if value not in EVERY_ALLOWED:
            raise ValueError(
                f"{value} s is not whole minutes that divide a day: launchd and cron count in "
                "minutes, and a schedule's ticks repeat daily"
            )
        return value

    @property
    def deadline(self) -> float:
        """The ``deadline:`` a mission scheduled so has unless it sets one."""
        return DEADLINE_SHARE * self.every


# --- projection ---------------------------------------------------------------------------

#: The schedulers a schedule is projected onto: launchd's user agents (macOS), systemd's user
#: timers (Linux), and the user's crontab where neither is.
Platform = Literal["launchd", "systemd", "cron"]

PLATFORMS: tuple[Platform, ...] = ("launchd", "systemd", "cron")


@dataclass(frozen=True)
class ScheduledRun:
    """Everything a projection writes, decided before it: the schedule's ``label`` (its name in
    the scheduler, one per mission of a project), the ``mission`` and its absolute ``project``,
    the absolute ``recon`` command a tick runs, the ``path`` it runs with, the digest of the
    config it was installed for (a tick refuses another), ``every`` and ``missed``, and
    ``offset_s``, where in each ``every`` its ticks fall (whole minutes, less than ``every``),
    which spreads missions due at once."""

    label: str
    mission: str
    project: str
    recon: str
    path: tuple[str, ...]
    config_sha256: str
    every: int
    offset_s: int
    missed: Missed

    @property
    def argv(self) -> tuple[str, ...]:
        """What a tick runs: recon, by its absolute path, never through a shell."""
        return (self.recon, "schedule", "tick", self.mission, "--project", self.project,
                "--config-sha256", self.config_sha256)  # fmt: skip


@dataclass(frozen=True)
class ProjectedFile:
    """One file a scheduler is given: its ``name`` in the scheduler's directory (for cron, the
    name of the entry in recon's block of the crontab), and its whole ``text``."""

    name: str
    text: str


def fire_times(every: int, offset_s: int) -> tuple[int, ...]:
    """The times of day, in seconds from midnight, a schedule's ticks fall on: ``offset_s``,
    then every ``every`` after it, through the day."""
    return tuple(range(offset_s, DAY_S, every))


def project(run: ScheduledRun, platform: Platform) -> tuple[ProjectedFile, ...]:
    """The files that make ``platform``'s scheduler tick ``run``, as text: pure, so the same
    run projects the same bytes. Raises UsageError for a run no scheduler file can hold."""
    _check(run, platform)
    if platform == "launchd":
        return (ProjectedFile(f"{run.label}.plist", _plist(run)),)
    if platform == "systemd":
        return (
            ProjectedFile(f"{run.label}.service", _service(run)),
            ProjectedFile(f"{run.label}.timer", _timer(run)),
        )
    return (ProjectedFile(f"{run.label}.cron", _cron(run)),)


def _check(run: ScheduledRun, platform: Platform) -> None:
    if not MISSION_NAME.match(run.mission):
        raise UsageError(
            f"{run.mission!r} is not a mission name",
            fix="name the mission as `recon init` does: letters, digits, '.', '_' and '-'",
        )
    named = [("the project", run.project), ("recon", run.recon)]
    named += [("a PATH directory", directory) for directory in run.path]
    for what, value in named:
        if not value.startswith("/"):
            raise UsageError(
                f"{what} {value!r} is not an absolute path; a scheduled tick has no working "
                "directory of its own to resolve it in",
                fix="install the schedule from the project, with recon installed",
            )
        if any(c in value for c in "\n\r\x00"):
            raise UsageError(
                f"{what} {value!r} holds a line break, which no scheduler file can hold",
                fix="move it to a path without one",
            )
        if platform == "cron" and "%" in value:
            raise UsageError(
                f"{what} {value!r} holds a %, which cron reads as a new line",
                fix="move it to a path without one, or install with --platform launchd or systemd",
            )
    if run.every not in EVERY_ALLOWED or run.offset_s % 60 or not 0 <= run.offset_s < run.every:
        raise UsageError(
            f"offset {run.offset_s} s is not whole minutes within every ({run.every} s)",
            fix="report it as a bug in recon",
        )


def _by_minute(run: ScheduledRun) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:
    """The ticks as (hours, minutes) pairs, each the hours a set of minutes falls in: the
    smallest calendar every scheduler takes. All 24 hours means every hour."""
    hours_of: dict[int, set[int]] = {}
    for at in fire_times(run.every, run.offset_s):
        hours_of.setdefault(at // 60 % 60, set()).add(at // 3600)
    minutes_of: dict[tuple[int, ...], list[int]] = {}
    for minute, hours in sorted(hours_of.items()):
        minutes_of.setdefault(tuple(sorted(hours)), []).append(minute)
    pairs = [(hours, tuple(minutes)) for hours, minutes in minutes_of.items()]
    return sorted(pairs, key=lambda pair: (pair[1], pair[0]))


def _every_hour(hours: tuple[int, ...]) -> bool:
    return len(hours) == 24


def _what(run: ScheduledRun) -> str:
    return f"mission {run.mission} of {run.project}, every {run.every} s"


def _made_by(run: ScheduledRun) -> str:
    return (
        f"# Made by `recon schedule install {run.mission}`; `recon schedule remove "
        f"{run.mission}` removes it.\n"
    )


# launchd: a property list, written here rather than by plistlib so the domain imports nothing
# it may not; each string is XML-escaped.


def _plist_string(value: str, indent: str) -> str:
    return f"{indent}<string>{html.escape(value, quote=False)}</string>\n"


def _plist(run: ScheduledRun) -> str:
    times: list[tuple[int, int]] = []  # (hour, minute); hour -1 is every hour
    for hours, minutes in _by_minute(run):
        for minute in minutes:
            times += [(-1, minute)] if _every_hour(hours) else [(h, minute) for h in hours]
    calendar = ""
    for hour, minute in sorted(times):
        calendar += "\t\t<dict>\n"
        if hour >= 0:
            calendar += f"\t\t\t<key>Hour</key>\n\t\t\t<integer>{hour}</integer>\n"
        calendar += f"\t\t\t<key>Minute</key>\n\t\t\t<integer>{minute}</integer>\n\t\t</dict>\n"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        "\t<key>Label</key>\n" + _plist_string(run.label, "\t")
        + "\t<key>ProgramArguments</key>\n\t<array>\n"
        + "".join(_plist_string(arg, "\t\t") for arg in run.argv)
        + "\t</array>\n"
        + "\t<key>WorkingDirectory</key>\n" + _plist_string(run.project, "\t")
        + "\t<key>EnvironmentVariables</key>\n\t<dict>\n\t\t<key>PATH</key>\n"
        + _plist_string(":".join(run.path), "\t\t")
        + "\t</dict>\n"
        + "\t<key>StartCalendarInterval</key>\n\t<array>\n" + calendar + "\t</array>\n"
        + "\t<key>StandardOutPath</key>\n\t<string>/dev/null</string>\n"
        + "\t<key>StandardErrorPath</key>\n\t<string>/dev/null</string>\n"
        + "</dict>\n</plist>\n"
    )  # fmt: skip


# systemd: a oneshot service and the timer that starts it. A % in a value is a specifier there,
# so it is written %%; an argument is quoted when it holds what would split or expand it.

_SYSTEMD_BARE = re.compile(r"^[A-Za-z0-9_@%+=:,./-]+$")


def _systemd(value: str) -> str:
    return value.replace("%", "%%")


def _systemd_arg(value: str) -> str:
    if _SYSTEMD_BARE.match(value):
        return _systemd(value)
    quoted = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$")
    return f'"{_systemd(quoted)}"'


def _service(run: ScheduledRun) -> str:
    return (
        _made_by(run)
        + "[Unit]\n"
        + f"Description=recon: {_systemd(_what(run))}\n\n"
        + "[Service]\n"
        + "Type=oneshot\n"
        + f"WorkingDirectory={_systemd(run.project)}\n"
        + f"Environment={_systemd_arg('PATH=' + ':'.join(run.path))}\n"
        + f"ExecStart={' '.join(_systemd_arg(arg) for arg in run.argv)}\n"
        + "StandardOutput=null\n"
    )


def _timer(run: ScheduledRun) -> str:
    calendars = ""
    for hours, minutes in _by_minute(run):
        hour = "*" if _every_hour(hours) else ",".join(f"{h:02d}" for h in hours)
        calendars += f"OnCalendar=*-*-* {hour}:{','.join(f'{m:02d}' for m in minutes)}:00\n"
    return (
        _made_by(run)
        + "[Unit]\n"
        + f"Description=recon: {_systemd(_what(run))}\n\n"
        + "[Timer]\n"
        + calendars
        + f"Persistent={'true' if run.missed == 'once' else 'false'}\n"
        + "AccuracySec=1s\n\n"
        + "[Install]\n"
        + "WantedBy=timers.target\n"
    )


# cron: one entry of recon's block in the user's crontab. cron hands the line to /bin/sh, so
# each argument is quoted whole; PATH is set by env(1), and the output goes nowhere.


def _sh(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _cron(run: ScheduledRun) -> str:
    words = ["/usr/bin/env", _sh("PATH=" + ":".join(run.path)), *(_sh(a) for a in run.argv)]
    command = " ".join(words) + " >/dev/null 2>&1"
    lines = f"# {run.label}: {_what(run)}\n"
    for hours, minutes in _by_minute(run):
        hour = "*" if _every_hour(hours) else ",".join(str(h) for h in hours)
        lines += f"{','.join(str(m) for m in minutes)} {hour} * * * {command}\n"
    return lines


def due(every: int, offset_s: int, at: datetime) -> datetime:
    """The latest tick at or before the wall-clock time ``at`` (naive, local): the grid of
    :func:`fire_times`, which a tick just after midnight finds on the day before."""
    midnight = at.replace(hour=0, minute=0, second=0, microsecond=0)
    k = int(((at - midnight).total_seconds() - offset_s) // every)
    return midnight + timedelta(seconds=offset_s + k * every)


# --- ports --------------------------------------------------------------------------------


class Scheduler(Protocol):
    """The operating system's scheduler, which starts each tick: where a schedule's files go,
    and how it is made to load them, is the adapter's. A label names one schedule."""

    @property
    def platform(self) -> Platform: ...

    def install(self, label: str, files: Sequence[ProjectedFile]) -> None:
        """Put ``files`` in place of any the scheduler has under ``label``, each readable by
        its owner alone, and have the scheduler load them. Raises ReconError (``run_failed``)
        when the scheduler refuses."""
        ...

    def locations(self, label: str) -> list[str]:
        """Where the schedule ``label``'s files are, or go: absolute paths, or ``crontab`` for
        an entry of the user's crontab."""
        ...

    def remove(self, label: str) -> bool:
        """Unload and remove the schedule ``label``; False when there was none."""
        ...

    def installed(self, prefix: str) -> list[str]:
        """The labels of the schedules installed whose label starts with ``prefix``, sorted."""
        ...

    def files(self, label: str) -> tuple[ProjectedFile, ...]:
        """The schedule's files as they are installed now; none when it is not."""
        ...


class ScheduleStore(Protocol):
    """What recon keeps of a mission's schedule beside the mission: what ``install`` put in
    place, so ``status`` can tell an installed schedule from one changed or gone since, and
    the log of its ticks."""

    def record(self, mission_dir: Path, installed: dict[str, Any] | None) -> None:
        """Keep ``installed`` as the mission's install record; None forgets it."""
        ...

    def recorded(self, mission_dir: Path) -> dict[str, Any] | None:
        """The mission's install record; None when it has none."""
        ...

    def append(self, mission_dir: Path, tick: dict[str, Any]) -> None:
        """Log one tick, whole, after those before it; the log is bounded (the oldest go)."""
        ...

    def lines(self, mission_dir: Path) -> list[dict[str, Any]]:
        """The ticks the log still holds, oldest first."""
        ...


@dataclass(frozen=True)
class ScheduleHost:
    """What the host gives every tick: the absolute ``recon`` command it runs (None when this
    recon is not installed as one), and the ``path`` it runs with: absolute directories no
    other user can write."""

    recon: str | None
    path: tuple[str, ...]

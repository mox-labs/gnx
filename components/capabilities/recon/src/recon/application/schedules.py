"""Schedules: ``recon schedule install | list | remove | status``, over the scheduler port.

A mission's schedule is decided here and projected by the domain (:func:`~recon.domain.
schedule.project`): its label, ``recon.<project digest>.<mission>``, one per mission of a
project; its offset, ``hash(mission) mod every`` in whole minutes, which spreads missions due
at the same moment; and the digest of the config it is installed for, which a tick holds the
config to. What ``install`` put in place is recorded beside the mission, so ``status`` tells
a schedule as installed from one changed or gone since.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from recon.domain.documents import ScheduleDocument, SchedulesDocument, ScheduleStatus
from recon.domain.errors import ConfigError, NotFoundError, Problem, UsageError
from recon.domain.schedule import ScheduledRun, due, project

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from pathlib import Path

    from recon.application.plan import Plan
    from recon.domain.clock import Clock
    from recon.domain.documents import ScheduleOutcome
    from recon.domain.missions import ConfigFile, MissionStore
    from recon.domain.schedule import (
        Platform,
        ScheduleHost,
        Scheduler,
        ScheduleSpec,
        ScheduleStore,
    )


def label_prefix(project: Path) -> str:
    """The start of every label of ``project``'s schedules: two projects' missions of one name
    are two schedules."""
    return f"recon.{hashlib.sha256(str(project).encode()).hexdigest()[:8]}."


def offset_s(mission: str, every: int) -> int:
    """Where in each ``every`` the mission's ticks fall: ``hash(mission) mod every``, in whole
    minutes, the same on every host."""
    digest = int(hashlib.sha256(mission.encode()).hexdigest(), 16)
    return digest % (every // 60) * 60


def config_digest(config: ConfigFile) -> str:
    return hashlib.sha256(config.text).hexdigest()


def _file_digests(files: Any) -> dict[str, str]:
    return {f.name: hashlib.sha256(f.text.encode()).hexdigest() for f in files}


class Schedules:
    """``recon schedule``'s operations. ``prepare`` plans a mission as a survey would
    (raising what a survey's plan raises); ``local`` turns the clock's time into the host's
    local time, which every scheduler keeps its calendar in."""

    def __init__(
        self,
        *,
        missions: MissionStore,
        store: ScheduleStore,
        schedulers: Callable[[Platform | None], Scheduler],
        host: ScheduleHost,
        clock: Clock,
        local: Callable[[datetime], datetime],
        prepare: Callable[[str], tuple[ConfigFile, Plan]],
    ) -> None:
        self._missions = missions
        self._store = store
        self._schedulers = schedulers
        self._host = host
        self._clock = clock
        self._local = local
        self._prepare = prepare

    def _label(self, mission: str) -> str:
        return label_prefix(self._missions.project) + mission

    def _scheduled(self, name: str) -> tuple[ConfigFile, ScheduleSpec]:
        """The mission's config and its schedule; raises ConfigError, with the fix, when the
        mission has no ``schedule:``."""
        if self._missions.config_text(name) is None:
            raise NotFoundError(
                f"no mission {name!r} under {self._missions.root}",
                fix=f"recon init {name} -c <file>, with a schedule: in it",
                details={"value": name},
            )
        config, plan = self._prepare(name)
        assert plan.config is not None  # prepare raises on an invalid plan
        if plan.config.schedule is None:
            problem = Problem(
                "schedule",
                "the mission has no schedule: to install",
                kind="missing_key",
                fix="add schedule: {every: 600} (seconds) to the mission, then: recon "
                f"schedule install {name}",
            )
            raise ConfigError(f"mission {name} has no schedule", [problem], fix=problem.fix)
        return config, plan.config.schedule

    def install(self, name: str, platform: Platform | None = None) -> ScheduleDocument:
        config, schedule = self._scheduled(name)
        if self._host.recon is None:
            raise UsageError(
                "this recon is not installed as a command a scheduler can start",
                fix="install recon as a command (`uv tool install` from its repository) and "
                "run `recon schedule install` with it",
            )
        scheduler = self._schedulers(platform)
        label = self._label(name)
        run = ScheduledRun(
            label=label,
            mission=name,
            project=str(self._missions.project),
            recon=self._host.recon,
            path=self._host.path,
            config_sha256=config_digest(config),
            every=schedule.every,
            offset_s=offset_s(name, schedule.every),
            missed=schedule.missed,
        )
        files = project(run, scheduler.platform)
        there = scheduler.files(label)
        outcome: ScheduleOutcome = "unchanged"
        if there != files:
            scheduler.install(label, files)
            outcome = "replaced" if there else "installed"
        self._store.record(
            self._missions.mission_dir(name),
            {
                "label": label,
                "platform": scheduler.platform,
                "config_sha256": run.config_sha256,
                "files": _file_digests(files),
                "installed_at": self._clock.now().isoformat(),
                "every": run.every,
                "missed": run.missed,
                "offset_s": run.offset_s,
            },
        )
        return ScheduleDocument(
            mission=name,
            platform=scheduler.platform,
            label=label,
            outcome=outcome,
            every=run.every,
            missed=run.missed,
            offset_s=run.offset_s,
            config_sha256=run.config_sha256,
            argv=list(run.argv),
            files=scheduler.locations(label),
            warnings=[w.document() for w in _platform_warnings(scheduler.platform, schedule)],
        )

    def remove(self, name: str, platform: Platform | None = None) -> ScheduleDocument:
        mission = self._missions.mission_dir(name)
        scheduler = self._schedulers(platform)
        label = self._label(name)
        removed = scheduler.remove(label)
        self._store.record(mission, None)
        return ScheduleDocument(
            mission=name,
            platform=scheduler.platform,
            label=label,
            outcome="removed" if removed else "absent",
            every=None,
            missed=None,
            offset_s=None,
            config_sha256=None,
            argv=[],
            files=[],
            warnings=[],
        )

    def list(self, platform: Platform | None = None) -> SchedulesDocument:
        scheduler = self._schedulers(platform)
        prefix = label_prefix(self._missions.project)
        schedules = [
            self._status(scheduler, label.removeprefix(prefix), label)
            for label in scheduler.installed(prefix)
        ]
        return SchedulesDocument(
            root=str(self._missions.root), platform=scheduler.platform, schedules=schedules
        )

    def status(
        self, mission: str | None = None, platform: Platform | None = None
    ) -> SchedulesDocument:
        scheduler = self._schedulers(platform)
        prefix = label_prefix(self._missions.project)
        if mission is not None:
            if self._missions.config_text(mission) is None:
                raise NotFoundError(
                    f"no mission {mission!r} under {self._missions.root}",
                    fix="recon schedule list",
                    details={"value": mission},
                )
            names = [mission]
        else:
            installed = {label.removeprefix(prefix) for label in scheduler.installed(prefix)}
            names = sorted(installed | {m for m in self._missions.missions() if self._declares(m)})
        schedules = [self._status(scheduler, name, prefix + name) for name in names]
        warnings = [_config_changed(s.mission).document() for s in schedules if s.config_changed]
        return SchedulesDocument(
            root=str(self._missions.root),
            platform=scheduler.platform,
            schedules=schedules,
            warnings=warnings,
        )

    def _declares(self, name: str) -> bool:
        """Whether the mission's config declares a schedule, as far as it can be read."""
        try:
            _, plan = self._prepare(name)
        except (ConfigError, NotFoundError):
            return False
        return plan.config is not None and plan.config.schedule is not None

    def _status(self, scheduler: Scheduler, name: str, label: str) -> ScheduleStatus:
        record = self._store.recorded(self._missions.mission_dir(name))
        files = scheduler.files(label)
        installed = bool(
            files
            and record is not None
            and record.get("platform") == scheduler.platform
            and record.get("files") == _file_digests(files)
        )
        text = self._missions.config_text(name)
        changed = None
        if record is not None and text is not None:
            changed = hashlib.sha256(text).hexdigest() != record.get("config_sha256")
        schedule = self._schedule_of(name)
        next_at = None
        if schedule is not None:
            now = self._local(self._clock.now())
            wall = now.replace(tzinfo=None)
            last = due(schedule.every, offset_s(name, schedule.every), wall)
            next_at = (last + timedelta(seconds=schedule.every)).replace(tzinfo=now.tzinfo)
        return ScheduleStatus(
            mission=name,
            label=label,
            platform=scheduler.platform,
            installed=installed,
            config_changed=changed,
            every=schedule.every if schedule is not None else None,
            missed=schedule.missed if schedule is not None else None,
            next_at=next_at.isoformat() if next_at is not None else None,
        )

    def _schedule_of(self, name: str) -> ScheduleSpec | None:
        try:
            _, plan = self._prepare(name)
        except (ConfigError, NotFoundError):
            return None
        return plan.config.schedule if plan.config is not None else None


def _platform_warnings(platform: Platform, schedule: ScheduleSpec) -> list[Problem]:
    if platform == "cron" and schedule.missed == "once":
        return [
            Problem(
                "schedule.missed",
                "cron runs no tick it missed: a tick due while the host was off or asleep is "
                "skipped, whatever missed: once asks",
                kind="missed_ignored",
                fix="install with --platform launchd or systemd where the host has one, or "
                "write missed: skip to say what cron does",
            )
        ]
    return []


def _config_changed(name: str) -> Problem:
    return Problem(
        "",
        f"mission {name}'s config changed since its schedule was installed: each tick is "
        "refused until it is installed again",
        kind="config_changed",
        fix=f"read what changed in the config (it runs unattended), then: recon schedule "
        f"install {name}",
    )

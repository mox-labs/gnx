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
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from recon.domain.documents import (
    ScheduleDocument,
    SchedulesDocument,
    ScheduleStatus,
    TickDocument,
    TickOutcome,
    survey_failure,
)
from recon.domain.errors import (
    EXIT_CODES,
    ConfigError,
    ConflictError,
    NotFoundError,
    Problem,
    ReconError,
    UsageError,
)
from recon.domain.schedule import MISSED_GRACE_S, OVERRUN_SKIPS, ScheduledRun, due, project

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from recon.application.plan import Plan
    from recon.domain.clock import Clock
    from recon.domain.documents import ScheduleOutcome, SurveyDocument
    from recon.domain.missions import ConfigFile, MissionStore
    from recon.domain.schedule import (
        Platform,
        ScheduleHost,
        Scheduler,
        ScheduleSpec,
        ScheduleStore,
        Skipped,
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
        for listed in schedules:
            busy = _in_a_row(self._store.lines(self._missions.mission_dir(listed.mission)), "busy")
            if busy >= OVERRUN_SKIPS:
                warnings.append(_overrun(listed.mission, busy).document())
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
        lines = self._store.lines(self._missions.mission_dir(name))
        last = _outcome(lines[-1]) if lines else None
        next_at, late_s = None, None
        if schedule is not None:
            now = self._local(self._clock.now())
            slot = _due(schedule.every, offset_s(name, schedule.every), now)
            next_at = slot + timedelta(seconds=schedule.every)
            if installed and record is not None:
                late_s = _late(slot, now, last, record.get("installed_at"))
        return ScheduleStatus(
            mission=name,
            label=label,
            platform=scheduler.platform,
            installed=installed,
            config_changed=changed,
            every=schedule.every if schedule is not None else None,
            missed=schedule.missed if schedule is not None else None,
            next_at=next_at.isoformat() if next_at is not None else None,
            last_tick=last,
            late_s=late_s,
            skips=_in_a_row(lines),
        )

    # --- the tick ------------------------------------------------------------------------

    def tick(
        self,
        name: str,
        config_sha256: str,
        survey: Callable[[dict[str, Any]], SurveyDocument],
    ) -> TickDocument:
        """One tick of the mission's schedule, logged: refused (``conflict``) when the config
        is not the one installed; skipped when ``missed: skip`` and it started more than
        :data:`MISSED_GRACE_S` late; else ``survey`` run, given when the tick was due and how
        late it started, for its archive. A survey refused ``busy`` is logged as a skip, and
        the second skip in a row says the schedule overruns."""
        mission = self._missions.mission_dir(name)
        text = self._missions.config_text(name)
        if text is None:
            raise NotFoundError(
                f"no mission {name!r} under {self._missions.root}",
                fix=f"recon schedule remove {name}: its mission is gone",
                details={"value": name},
            )
        started = self._local(self._clock.now())
        record = self._store.recorded(mission) or {}
        every, offset = record.get("every"), record.get("offset_s")
        slot = None
        if isinstance(every, int) and isinstance(offset, int):
            slot = _due(every, offset, started)
        if hashlib.sha256(text).hexdigest() != config_sha256:
            self._log(mission, slot, started, "conflict", None, "config_changed")
            raise ConflictError(
                f"mission {name}'s config is not the one its schedule was installed for: a "
                "tick runs only the config installed",
                fix=f"read what changed in the config (it runs unattended), then: recon "
                f"schedule install {name}",
                details={"mission": name, "config_sha256": config_sha256},
            )
        _, plan = self._prepare(name)
        schedule = plan.config.schedule if plan.config is not None else None
        if schedule is not None:
            slot = _due(schedule.every, offset_s(name, schedule.every), started)
        late_s = (started - slot).total_seconds() if slot is not None else None
        if schedule is not None and schedule.missed == "skip" and (late_s or 0) > MISSED_GRACE_S:
            self._log(mission, slot, started, "ok", None, "missed")
            return TickDocument(
                mission=name, scheduled_at=_iso(slot), late_s=late_s, skipped_by="missed",
                survey=None,
            )  # fmt: skip
        try:
            document = survey({"scheduled_at": _iso(slot), "late_s": late_s})
        except ReconError as error:
            skipped: Skipped | None = "busy" if error.kind == "busy" else None
            self._log(mission, slot, started, error.kind, None, skipped)
            if skipped is not None:
                raise _skipped(
                    name, error, _in_a_row(self._store.lines(mission), "busy")
                ) from error
            raise
        except KeyboardInterrupt:
            self._log(mission, slot, started, "interrupted", None, None)
            raise
        failure = survey_failure(document)
        kind = failure.kind if failure is not None else "ok"
        self._log(mission, slot, started, kind, document.archive, None)
        return TickDocument(
            mission=name, scheduled_at=_iso(slot), late_s=late_s, skipped_by=None, survey=document
        )

    def _log(
        self,
        mission: Path,
        slot: datetime | None,
        started: datetime,
        kind: str,
        archive: str | None,
        skipped: Skipped | None,
    ) -> None:
        """Log one tick: when it was due, started and ended, how it ended, and why it ran no
        survey, if it ran none."""
        line = TickOutcome(
            scheduled=_iso(slot),
            started=started.isoformat(),
            ended=self._local(self._clock.now()).isoformat(),
            exit=EXIT_CODES[kind],
            kind=kind,
            archive=archive,
            skipped_by=skipped,
            late_s=(started - slot).total_seconds() if slot is not None else None,
        )
        self._store.append(mission, line.document())

    def _schedule_of(self, name: str) -> ScheduleSpec | None:
        try:
            _, plan = self._prepare(name)
        except (ConfigError, NotFoundError):
            return None
        return plan.config.schedule if plan.config is not None else None


def _due(every: int, offset: int, now: datetime) -> datetime:
    """The tick due last at ``now`` (local, with its zone): the grid is wall-clock time."""
    return due(every, offset, now.replace(tzinfo=None)).replace(tzinfo=now.tzinfo)


def _iso(at: datetime | None) -> str | None:
    return at.isoformat() if at is not None else None


def _outcome(line: dict[str, Any]) -> TickOutcome | None:
    """A line of the log as a tick, or None for one this recon cannot read as one."""
    try:
        return TickOutcome.model_validate(line)
    except ValidationError:
        return None


def _in_a_row(lines: list[dict[str, Any]], why: str | None = None) -> int:
    """The ticks skipped in a row at the log's end, each for ``why`` (None: any reason)."""
    count = 0
    for line in reversed(lines):
        if line.get("skipped_by") is None or (why is not None and line["skipped_by"] != why):
            break
        count += 1
    return count


def _late(slot: datetime, now: datetime, last: TickOutcome | None, installed_at: Any) -> float:
    """How late the schedule is at ``now``: the seconds since ``slot``, the tick due last,
    when no tick of it is logged and it fell after the schedule was installed; else 0."""
    scheduled = last.scheduled if last is not None else None
    if scheduled is not None and datetime.fromisoformat(scheduled) >= slot:
        return 0.0
    if isinstance(installed_at, str) and datetime.fromisoformat(installed_at) > slot:
        return 0.0
    return (now - slot).total_seconds()


def _skipped(name: str, busy: ReconError, skips: int) -> ReconError:
    """A tick's ``busy``, counted in ``details``: the second in a row is an overrun."""
    details = {**busy.details, "skips": skips}
    if skips < OVERRUN_SKIPS:
        return ReconError(f"tick skipped: {busy.message}", kind="busy", fix=busy.fix,
                          details=details)  # fmt: skip
    overrun = _overrun(name, skips)
    return ReconError(f"tick skipped: {overrun.message}", kind="busy", fix=overrun.fix,
                      details=details)  # fmt: skip


def _overrun(name: str, skips: int) -> Problem:
    return Problem(
        "schedule.every",
        f"the last {skips} ticks of mission {name} were skipped, each while a survey of it "
        "still ran: its survey takes longer than its schedule's every",
        kind="schedule_overrun",
        fix="raise schedule.every above how long a survey of it takes, or lower its "
        "deadline: (0.8 × every by default) so each ends before the next is due",
    )


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

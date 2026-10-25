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
from datetime import UTC, datetime, timedelta
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
    from recon.domain.documents import DeliverDocument, ScheduleOutcome, SurveyDocument
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
            now = self._clock.now()
            slot = self._slot(schedule.every, offset_s(name, schedule.every), now)
            next_at = self._instant(
                slot.replace(tzinfo=None) + timedelta(seconds=schedule.every),
                slot + timedelta(seconds=schedule.every),
            )
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
        survey: Callable[[dict[str, Any]], tuple[SurveyDocument, DeliverDocument | None]],
    ) -> TickDocument:
        """One tick of the mission's schedule, logged: refused (``stale``) when the config
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
            slot = self._slot(every, offset, started)
        if hashlib.sha256(text).hexdigest() != config_sha256:
            self._log(mission, slot, started, "stale", None, "config_changed")
            raise stale(name, config_sha256)
        _, plan = self._prepare(name)
        schedule = plan.config.schedule if plan.config is not None else None
        if schedule is not None:
            slot = self._slot(schedule.every, offset_s(name, schedule.every), started)
        late_s = _seconds(started, slot) if slot is not None else None
        if schedule is not None and schedule.missed == "skip" and (late_s or 0) > MISSED_GRACE_S:
            self._log(mission, slot, started, "ok", None, "missed")
            return TickDocument(
                mission=name, scheduled_at=_iso(slot), late_s=late_s, skipped_by="missed",
                survey=None,
            )  # fmt: skip
        try:
            # The survey checks the digest again, of the bytes it plans and runs, read once
            # under the mission lock: a config swapped since the check above is refused.
            document, delivered = survey({"scheduled_at": _iso(slot), "late_s": late_s})
        except ReconError as error:
            skipped: Skipped | None = _SKIPPED_BY.get(error.kind)
            self._log(mission, slot, started, error.kind, None, skipped)
            if skipped == "busy":
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
            mission=name, scheduled_at=_iso(slot), late_s=late_s, skipped_by=None, survey=document,
            deliveries=delivered,
        )  # fmt: skip

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
            late_s=_seconds(started, slot) if slot is not None else None,
        )
        self._store.append(mission, line.document())

    def _slot(self, every: int, offset: int, now: datetime) -> datetime:
        """The tick due last at ``now``: the latest time of the schedule's grid at or before
        it. The grid is the scheduler's, local wall-clock times; the slot is the instant that
        time names, in local time with the offset it had then, so a daylight-saving change
        between it and ``now`` counts as the hour that passed or did not."""
        wall_now = self._local(now).replace(tzinfo=None)
        wall = due(every, offset, wall_now)
        return self._instant(wall, now - (wall_now - wall))

    def _instant(self, wall: datetime, guess: datetime) -> datetime:
        """The instant the local wall-clock time ``wall`` names, found from ``guess`` (within
        an hour of it) by moving it until the local clock reads ``wall``. A time the clocks
        skipped (02:30 on a spring-forward night) names none: it is due when the clocks
        reached past it, the first instant whose local time is later, which is when a
        scheduler can start it (round-4 review M2). The guess is moved in UTC: in a local
        zone, a skipped time reads as itself and would pass for one that exists."""
        at = guess.astimezone(UTC)
        for _ in range(3):
            drift = self._local(at).replace(tzinfo=None) - wall
            if not drift:
                return self._local(at)
            at -= drift
        # Skipped: the jump is within a few hours of the guess, and local time only rises
        # there, so the first second whose local time is later is found by halving.
        lo = int((at - _EPOCH).total_seconds()) - 3 * 3600
        hi = lo + 6 * 3600
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self._local(_EPOCH + timedelta(seconds=mid)).replace(tzinfo=None) > wall:
                hi = mid
            else:
                lo = mid
        return self._local(_EPOCH + timedelta(seconds=hi))

    def _schedule_of(self, name: str) -> ScheduleSpec | None:
        try:
            _, plan = self._prepare(name)
        except (ConfigError, NotFoundError):
            return None
        return plan.config.schedule if plan.config is not None else None


def _seconds(later: datetime, earlier: datetime) -> float:
    """The seconds from ``earlier`` to ``later``, in UTC epoch time: never the difference of
    two wall-clock readings, which a daylight-saving change between them would skew."""
    return (later.astimezone(UTC) - earlier.astimezone(UTC)).total_seconds()


#: The epoch, for counting seconds without reading the clock.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


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
    if scheduled is not None and _seconds(datetime.fromisoformat(scheduled), slot) >= 0:
        return 0.0
    if isinstance(installed_at, str) and _seconds(datetime.fromisoformat(installed_at), slot) > 0:
        return 0.0
    return _seconds(now, slot)


#: The ticks a survey's refusal skips, by its kind: another holds the mission, or the config
#: it read under the lock is not the one installed.
_SKIPPED_BY: dict[str, Skipped] = {"busy": "busy", "stale": "config_changed"}


def stale(name: str, config_sha256: str) -> ReconError:
    """The refusal of a tick whose mission's config is not the one installed (its digest is
    not ``config_sha256``)."""
    return ReconError(
        f"mission {name}'s config is not the one its schedule was installed for: a tick runs "
        "only the config installed",
        kind="stale",
        fix=f"read what changed in the config (it runs unattended), then: recon schedule "
        f"install {name}",
        details={"mission": name, "config_sha256": config_sha256},
    )


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

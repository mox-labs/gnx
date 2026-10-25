"""What recon does, over its ports: scaffold a mission, plan it, survey it, stream it, report
on the missions, query an archive or a stream, list the templates, append a capture.

Each operation returns one versioned document (``recon.domain.documents``), whose
``document()`` is what ``--json`` prints, or raises a
:class:`~recon.domain.errors.ReconError` that says what to do next.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any, Protocol

from recon.application.captures import record_hook_payload
from recon.application.changes import MEMORY_KEYS, ChangeDetector
from recon.application.collectors import confined_transforms, declared_env
from recon.application.plan import Plan, PlannedStream, build_plan, fan_out, plugin_warning
from recon.application.schedules import Schedules, stale
from recon.application.stream import Stream, StreamGaps, StreamPlan, StreamRun, windows_going
from recon.application.subscriptions import ChangeReader, Subscriptions
from recon.application.survey import CollectorError, Survey, TablePages
from recon.domain.archives import (
    ARCHIVE_ID_FORMAT,
    READ_MAX_BYTES,
    READ_MAX_RECORDS,
    WATCH_INTERVAL_S,
)
from recon.domain.deadline import Deadline
from recon.domain.documents import (
    ArchiveStatus,
    BrokenPluginInfo,
    ChangesSince,
    CollectorTypeInfo,
    DeliverDocument,
    InstalledPlugins,
    MissionDocument,
    MissionStatus,
    PlanDocument,
    PlanWrites,
    QueryRows,
    RecordsSince,
    Required,
    SourcedValue,
    StatusDocument,
    StreamDocument,
    StreamStatus,
    SubscriptionDocument,
    SubscriptionStatus,
    SurveyDocument,
    TableError,
    TableStatus,
    TemplateInfo,
    TemplatesDocument,
    WindowOutcome,
    WindowRange,
    failure_kind,
)
from recon.domain.errors import (
    ConfigError,
    ConflictError,
    NoRecordsError,
    NotFoundError,
    Problem,
    QueryError,
    ReconError,
    UsageError,
)
from recon.domain.normalize import apply_normalize
from recon.domain.stream import window_index, window_opened
from recon.domain.telemetry import QUERY, SURVEY

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator, Mapping
    from pathlib import Path

    from recon.application.collectors import SurveyCollectors
    from recon.domain.archives import ArchiveQuery, ArchiveState, ArchiveStore, Ended
    from recon.domain.changes import KeyJoin
    from recon.domain.clock import Clock
    from recon.domain.collector import Collector, Plugins
    from recon.domain.documents import (
        Capture,
        ScheduleDocument,
        SchedulesDocument,
        TickDocument,
    )
    from recon.domain.missions import ConfigFile, MissionStore
    from recon.domain.models import CollectorEntry, ReconConfig, SourceEntry
    from recon.domain.paginate import PageLog
    from recon.domain.schedule import Platform, ScheduleHost, Scheduler, ScheduleStore
    from recon.domain.stream import GapLog, RetainSpec, Stop, WindowSpec
    from recon.domain.subscriptions import Deliverer, SubscriptionStore
    from recon.domain.telemetry import Channel, EventCallback, Telemetry


class CollectorFactory(Protocol):
    """Builds a collector for each installed type a mission uses, reporting through the
    survey's telemetry, with the types whose factory failed (which a survey reports and whose
    tables fail with a config error), and what to close when the survey ends; ``gaps`` is where
    a stream's collector says when its source was not read, and ``pages`` where a paginated
    table's collector says its pages."""

    def __call__(
        self,
        mission_dir: Path,
        config: ReconConfig,
        telemetry: Telemetry,
        deadline: Deadline,
        /,
        *,
        gaps: GapLog | None = None,
        pages: PageLog | None = None,
    ) -> SurveyCollectors: ...


class TelemetryFactory(Protocol):
    """The telemetry for one operation: a survey passes the archive it writes, whose event
    log is one of the sinks, and its channel, through which every other sink receives; an
    operation that writes no archive passes None for both, and its sinks receive at once. A
    stream's window passes the clock its telemetry reads (the window's own times); None is
    recon's clock."""

    def __call__(
        self, archive: Path | None, channel: Channel | None, clock: Clock | None = None, /
    ) -> Telemetry: ...


#: Makes a survey's channel (``recon.domain.telemetry.Channel``).
ChannelFactory = Callable[[], "Channel"]

DEFAULT_TEMPLATE = "code-forensics"

#: Past this many windows, a query of a stream warns (``windows_many``) how to narrow it:
#: each window is a file DuckDB opens.
MANY_WINDOWS = 300


class Recon:
    """recon's operations."""

    def __init__(
        self,
        *,
        missions: MissionStore,
        archives: ArchiveStore,
        query: ArchiveQuery,
        clock: Clock,
        plugins: Plugins,
        build_collectors: CollectorFactory,
        telemetry: TelemetryFactory,
        env: Mapping[str, str],
        version: str,
        root_found_by: str = "argument",
        channel: ChannelFactory | None = None,
        schedulers: Callable[[Platform | None], Scheduler] | None = None,
        schedule_store: ScheduleStore | None = None,
        schedule_host: ScheduleHost | None = None,
        local: Callable[[datetime], datetime] | None = None,
        key_join: KeyJoin | None = None,
        memory_keys: int = MEMORY_KEYS,
        subscriptions: SubscriptionStore | None = None,
        deliverer: Deliverer | None = None,
    ) -> None:
        """``root_found_by`` is how the composition root found the project root:
        ``argument`` (the caller named it), ``repository`` (the nearest ancestor holding
        ``.git``) or ``working_directory``. Each survey records it. ``channel`` makes each
        survey's channel, through which OpenTelemetry and ``on_event`` receive; without
        one they receive at once, on the survey's thread. ``schedulers`` makes the operating
        system's scheduler of a platform (None: the host's own), which ``schedule_*`` run
        through, with the ``schedule_store`` beside each mission, the ``schedule_host``'s
        recon command and PATH, and ``local``, the clock's time as the host's local time;
        without them, the ``schedule_*`` operations are refused. ``key_join`` joins a change
        detection's comparisons of more than ``memory_keys`` keys (None: all in memory).
        ``subscriptions`` keeps what each subscription acked (None: the ``subscribe``, ``ack``,
        ``unsubscribe`` and ``deliver`` operations are refused); ``deliverer`` gives a
        subscription's deliveries to its ``command:`` (None: ``deliver`` is refused)."""
        self._missions = missions
        self._archives = archives
        self._query = query
        self._clock = clock
        self._plugins = plugins
        self._build_collectors = build_collectors
        self._telemetry = telemetry
        self._env = env
        self._version = version
        self._root_found_by = root_found_by
        self._channel = channel
        self._schedules = (
            None
            if schedulers is None or schedule_store is None or schedule_host is None
            else Schedules(
                missions=missions,
                store=schedule_store,
                schedulers=schedulers,
                host=schedule_host,
                clock=clock,
                local=local or (lambda at: at),
                prepare=lambda name: self._prepare(name, None, False)[:2],
            )
        )
        self._key_join = key_join
        self._memory_keys = memory_keys
        self._subscriptions = (
            None
            if subscriptions is None or deliverer is None
            else Subscriptions(
                store=subscriptions,
                clock=clock,
                mission_dir=missions.mission_dir,
                prepare=lambda name: self._prepare(name, None, False)[1],
                reader=self._change_reader,
                deliverer=deliverer,
                project=missions.project,
            )
        )

    # --- init ---------------------------------------------------------------------------

    def init(
        self,
        name: str,
        *,
        template: str | None = None,
        config: Path | None = None,
        replace: bool = False,
    ) -> MissionDocument:
        """Create a mission from a built-in template (default ``code-forensics``) or from the
        config file ``config`` (``-``: standard input). Idempotent: a mission that already has
        this config, byte for byte, is left as it is (``outcome: unchanged``). Raises
        ConflictError when it has a different one, unless ``replace`` (``outcome:
        replaced``)."""
        if template is not None and config is not None:
            raise UsageError(
                "init takes a template or a config file, not both",
                fix=f"recon init {name} -t <template>, or recon init {name} -c <file>",
            )
        if config is not None:
            text, origin, flag = self._missions.read_config(config).text, str(config), "-c"
        else:
            chosen = template or DEFAULT_TEMPLATE
            text, origin, flag = self._template(chosen).encode(), f"template:{chosen}", "-t"
        path = self._missions.config_path(name)
        current = self._missions.config_text(name)
        if current == text:
            return MissionDocument(
                mission=name, config=str(path), from_=origin, outcome="unchanged"
            )
        if current is not None and not replace:
            given = str(config) if config is not None else origin.removeprefix("template:")
            raise ConflictError(
                f"mission {name!r} already has a different config: {path}",
                fix=f"recon init {name} {flag} {given} --replace to overwrite it, or edit "
                f"{path} and run `recon survey {name} --dry-run`",
                details={"config": str(path)},
            )
        written = self._missions.write_config(name, text)
        return MissionDocument(
            mission=name,
            config=str(written),
            from_=origin,
            outcome="created" if current is None else "replaced",
        )

    def _template(self, name: str) -> str:
        templates = {t.name: t.text for t in self._missions.templates()}
        if name not in templates:
            available = ", ".join(sorted(templates)) or "(none)"
            raise NotFoundError(
                f"template {name!r} not found; built-in templates: {available}",
                fix="recon templates",
                details={"value": name, "allowed": sorted(templates)},
            )
        return templates[name]

    # --- plan and survey ----------------------------------------------------------------

    def plan(
        self,
        name: str,
        *,
        config: Path | None = None,
        replace: bool = False,
        stream: str | None = None,
        windows: int | None = None,
        until: datetime | None = None,
        forever: bool = False,
    ) -> PlanDocument:
        """What a survey would do: every run with its effects, the files it would write, and
        what the runs need of this host. Raises ConfigError listing every problem.

        Writes nothing. Runs and fetches nothing either, unless a run's collector type
        declares a ``preflight``: that type's collector is then built, asked what each of its
        runs needs (an ``mcp`` type starts each stdio server a run names, and connects to each
        remote one, to list its tools), and closed; ``preflight`` says what was done, and
        ``requirements`` what was found.

        With ``stream``, what :meth:`stream` would do with ``windows``, ``until`` and
        ``forever``: it refuses what that refuses, and ``provenance`` also says how long the
        run is and why (``stream.windows``, one by default; ``stream.until``;
        ``stream.forever``), and when it returns at the latest (``stream.returns``: seconds
        from its start, or null when only a stop or its source ends it). Without it, a
        mission with streams has ``survey.returns``: a survey runs one window of each, in
        turn, after its snapshot tables."""
        bound = None if stream is None else _stream_bound(
            windows, until, forever, self._clock.now(), name, stream
        )  # fmt: skip
        _, plan, copy_to = self._prepare(name, config, replace)
        mission = self._missions.mission_dir(name)
        assert plan.config is not None  # _prepare raises on an invalid plan
        provenance = list(plan.provenance)
        if stream is not None:
            planned = _planned_stream(plan, name, stream)
            provenance += _run_bound(bound, windows, until, forever)
            returns_s, returns = _returns(planned.window, bound, until, forever, self._clock.now())
            provenance.append(
                SourcedValue(path="stream.returns", value=returns_s, origin="recon", note=returns)
            )
        elif plan.streams:
            provenance.append(_survey_returns(plan.config, plan.streams))
        requirements, said, broken = self._preflight(mission, plan.config)
        broken += self._behind(name, plan)
        return PlanDocument(
            mission=name,
            valid=plan.valid,
            runs=[run.document() for run in plan.runs],
            effects=plan.effects(),
            writes=PlanWrites(
                config=str(copy_to) if copy_to else None,
                archive_path=str(mission / "archive" / "<archive id>"),
                preserve_raw=bool(plan.config and plan.config.preserve_raw),
                stream_paths=[str(mission / "streams" / s.name) for s in plan.streams],
            ),
            problems=[p.document() for p in plan.problems],
            warnings=[w.document() for w in plan.warnings + broken],
            skipped=[skipped.document() for skipped in plan.skipped],
            requirements=requirements,
            runnable=all(required.holds for required in requirements),
            preflight=said,
            provenance=provenance,
        )

    def _preflight(
        self, mission: Path, config: ReconConfig
    ) -> tuple[list[Required], list[str], list[Problem]]:
        """Ask the collector of each type that declares a preflight what each of its runs
        needs: the requirements, each said once with the outputs that need it; what was done,
        a sentence per type; and a warning for each type that could not be built or closed."""
        types = {n: ct for n, ct in self._plugins.collector_types.items() if ct.preflight}
        entries = [entry for entry in config.collectors if entry.type in types]
        if not entries:
            return [], [], []
        said = [
            f"{name}: {types[name].preflight}" for name in dict.fromkeys(e.type for e in entries)
        ]
        subset = config.model_copy(update={"collectors": entries})
        built = self._build_collectors(
            mission, subset, self._telemetry(None, None), Deadline(self._clock)
        )
        warnings = [plugin_warning(broken) for broken in built.failed]
        merged: dict[tuple[str, bool, str, str], list[str]] = {}
        catalog = {source.name: source for source in config.catalog}
        try:
            for entry in entries:
                ask = built.requirements.get(entry.type)
                if ask is None:
                    continue
                for source, output in fan_out(entry, catalog, types[entry.type].accepts_kinds):
                    target = (
                        entry if output == entry.name else entry.model_copy(update={"name": output})
                    )
                    for found in ask(target, source):
                        key = (found.need, found.holds, found.why, found.fix)
                        needed_by = merged.setdefault(key, [])
                        if output not in needed_by:
                            needed_by.append(output)
        finally:
            warnings += [plugin_warning(broken) for broken in built.close()]
        requirements = [
            Required(need=need, holds=holds, why=why, fix=fix, needed_by=needed_by)
            for (need, holds, why, fix), needed_by in merged.items()
        ]
        return requirements, said, warnings

    def survey(
        self,
        name: str,
        *,
        config: Path | None = None,
        replace: bool = False,
        on_event: EventCallback | None = None,
    ) -> SurveyDocument:
        """Plan, then run every table and write one archive. A survey whose tables failed,
        or whose archive could not be finished, still returns its document (``status:
        incomplete``); :func:`~recon.domain.documents.survey_failure` says what that adds up
        to.
        Each table written that SQL cannot read is a ``table_unreadable`` warning, so it is
        found here rather than by the first query. An entry that is a stream runs one window,
        sealed when it ends (``windows``), after the snapshot tables, in config order: the
        dirt road of a stream, a survey run from cron. The mission is held while it runs, so
        a second survey of it is refused ``busy`` before it writes anything."""
        source, plan, copy_to = self._prepare(name, config, replace)
        assert plan.config is not None  # _prepare raises on an invalid plan
        mission = self._missions.mission_dir(name)
        # One survey a mission at a time, from before it writes anything until it returns.
        self._archives.hold_mission(mission)
        try:
            return self._survey(name, source, plan, copy_to, on_event)
        finally:
            self._archives.release_mission(mission)

    def _scheduled_survey(
        self, name: str, scheduled: dict[str, Any], config_sha256: str
    ) -> tuple[SurveyDocument, DeliverDocument | None, list[dict[str, Any]]]:
        """:meth:`survey` as a tick runs it, its archive's ``meta.yaml`` saying when the tick
        was due and how late it started (``scheduled``). The mission is held first; then its
        config is read once, and those bytes are what is checked against ``config_sha256``,
        planned and run, so no config swapped after a check runs (Dijkstra, Vector F4). Then,
        the archive sealed and the mission let go, the delivery of its subscriptions that have
        ``deliver:`` (ruling R4), by the same config's plan; None when it has none."""
        mission = self._missions.mission_dir(name)
        self._archives.hold_mission(mission)
        try:
            source, plan, _ = self._prepare(name, None, False, expect_sha256=config_sha256)
            document = self._survey(name, source, plan, None, None, scheduled=scheduled)
        finally:
            self._archives.release_mission(mission)
        assert plan.config is not None
        delivering = any(s.deliver is not None for s in plan.config.subscriptions)
        delivered = None
        if self._subscriptions is not None and delivering:
            delivered = self._subscriptions.deliver(name, plan=plan)
        return document, delivered, [w.document() for w in self._behind(name, plan)]

    def _survey(
        self,
        name: str,
        source: ConfigFile,
        plan: Plan,
        copy_to: Path | None,
        on_event: EventCallback | None,
        *,
        scheduled: dict[str, Any] | None = None,
    ) -> SurveyDocument:
        """:meth:`survey`, the mission held and its plan valid."""
        assert plan.config is not None
        caller = on_event
        emit = on_event or (lambda event: None)
        streams = {stream.index for stream in plan.streams}
        snapshot = plan.config.model_copy(
            update={
                "collectors": [e for i, e in enumerate(plan.config.collectors) if i not in streams]
            }  # fmt: skip
        )
        sha256 = hashlib.sha256(source.text).hexdigest()
        warnings = list(plan.warnings)
        for warning in warnings:
            emit({"kind": "warning", "reason": str(warning), "fix": warning.fix})
        if copy_to is not None:
            self._missions.write_config(name, source.text)
        mission = self._missions.mission_dir(name)
        # Its key read before anything is written: a key file that does not hold is refused
        # with no archive begun.
        changes = self._changes(name, snapshot)
        started = self._clock.now()
        archive = self._archives.create(mission, started.strftime(ARCHIVE_ID_FORMAT))
        # Held from here until released, so a reader of the archive sees it is being
        # written, however the survey ends.
        try:
            channel = self._channel() if self._channel is not None else None
            telemetry = self._telemetry(archive, channel)
            if channel is not None and on_event is not None:
                on_event = emit = channel.callback(on_event)
            about = {"recon.mission": name, "recon.archive": archive.name}
            deadline = Deadline(self._clock)
            pages = TablePages()
            with _ending(telemetry, channel), telemetry.span(SURVEY, about) as span:
                built = self._build_collectors(mission, snapshot, telemetry, deadline, pages=pages)
                try:
                    for broken in built.failed:
                        warnings.append(plugin_warning(broken))
                        emit({"kind": "warning", "reason": str(warnings[-1]),
                              "fix": warnings[-1].fix})  # fmt: skip
                    emit(
                        {
                            "kind": "start",
                            "runs": len(plan.runs),
                            "collectors": len(plan.config.collectors),
                            "sources": len(plan.config.catalog),
                        }
                    )
                    types = self._plugins.collector_types
                    extracts = {n for n, ct in types.items() if ct.extracts}
                    kinds = {
                        n: ct.accepts_kinds
                        for n, ct in types.items()
                        if ct.accepts_kinds is not None
                    }
                    survey = Survey(
                        self._archives, self._clock, self._version, telemetry, on_event,
                        extracts=extracts, kinds=kinds, deadline=deadline, pages=pages,
                        changes=changes,
                    )  # fmt: skip
                    tables = survey.run(
                        plan.config,
                        built.collectors,
                        archive,
                        started=started,
                        config_sha256=sha256,
                        resolved=self._resolved(plan.config),
                        streams=streams,
                        scheduled=scheduled,
                        index=self._archives.indexes(mission).get(archive.name),
                    )
                finally:
                    # Whatever the survey opened (a collector's session, the requester's
                    # connections) ends with it, however it ends.
                    for broken in built.close():
                        warnings.append(plugin_warning(broken))
                        emit({"kind": "warning", "reason": str(warnings[-1]),
                              "fix": warnings[-1].fix})  # fmt: skip
                failed_tables = [t for t in tables if t.status == "error"]
                span.set("recon.tables", len(tables))
                span.set("recon.tables.failed", len(failed_tables))
                span.set("recon.records", sum(t.records for t in tables))
                if not survey.finished:
                    span.fail("store", "the archive could not be finished")
                elif (kind := failure_kind(tables)) is not None:
                    span.fail(kind, f"{len(failed_tables)} of {len(tables)} table(s) failed")
                warnings += survey.warnings
                for unreadable in self._query.unreadable(archive):
                    warnings.append(unreadable)
                    emit({"kind": "table_warning", "name": unreadable.got,
                          "reason": str(unreadable), "fix": unreadable.fix})  # fmt: skip
            warnings += _dropped(telemetry) + _undelivered(channel)
        finally:
            self._archives.release(archive)
        self._retain_snapshots(name, mission, plan, streams)
        stream_windows: list[WindowOutcome] = []
        for stream in plan.streams:
            run, found = self._run_stream(
                mission, plan.config, stream, sha256, windows=1, until=None, on_event=caller,
                name=name, plan=plan,
            )  # fmt: skip
            warnings += found
            stream_windows += [run.last] if run.last is not None else []
        failed = failed_tables or any(w.table.status == "error" for w in stream_windows)
        return SurveyDocument(
            mission=name,
            archive=archive.name,
            path=str(archive),
            status="incomplete" if failed or not survey.finished else "complete",
            tables=tables,
            warnings=[w.document() for w in warnings],
            stream_windows=stream_windows,
        )

    def _retain_snapshots(self, name: str, mission: Path, plan: Plan, streams: set[int]) -> None:
        """Retire the archives every snapshot entry's ``retain:`` lets go (Knuth, round-4
        review S1), oldest first, once the survey's archive is sealed, the mission still held:
        never the newest, never one being written, never the last one a table with
        ``changes:`` was told in (the told index the next comparison reads). An archive holds
        every entry's tables, so it goes only when every snapshot entry lets it go; one that
        keeps ``all`` (the default) keeps every archive. Nor one holding change rows a
        subscription the config declares has not acked: a notification lost in silence is
        worse than the disk it takes. Each leaves a tombstone in ``archive/retired.jsonl``; a
        retirement that fails is tried again by the next."""
        assert plan.config is not None
        config = plan.config
        entries = [entry for i, entry in enumerate(config.collectors) if i not in streams]
        rules = [entry.retain for entry in entries]
        going = self._snapshots_letting_go(mission, config, streams)
        if not going:
            return
        held = self._subscriptions.held(name, plan, None) if (
            self._subscriptions is not None
        ) else {}  # fmt: skip
        kept = set().union(*(archives for _, archives in held.values()))
        archives = self._archives.archives(mission)
        now = self._clock.now()
        for archive in archives[:-1]:
            if archive.name not in going or archive.name in kept:
                continue
            if self._archives.state(archive) == "writing":
                continue
            meta = self._archives.read_meta(archive)
            tombstone = {
                "id": archive.name,
                "index": meta.get("index"),
                "started_at": meta.get("started_at"),
                "timestamp": meta.get("timestamp"),
                "ended": meta.get("ended"),
                "retired_at": now.isoformat(),
                "rule": [rule.model_dump(exclude_none=True) for rule in rules
                         if rule is not None and not isinstance(rule, str)],
            }  # fmt: skip
            with contextlib.suppress(OSError):  # left listed: the next survey retires it
                self._archives.retire(archive, tombstone)

    def _snapshots_letting_go(
        self, mission: Path, config: ReconConfig, streams: set[int]
    ) -> set[str]:
        """The archives the snapshot entries' ``retain:`` lets go now: those every entry's
        rules let go, but the newest and the last one each table with ``changes:`` was told
        in; none when an entry keeps ``all``."""
        entries = [entry for i, entry in enumerate(config.collectors) if i not in streams]
        rules = [entry.retain for entry in entries]
        if not entries or any(rule is None or isinstance(rule, str) for rule in rules):
            return set()
        archives = self._archives.archives(mission)
        if len(archives) < 2:
            return set()
        now = self._clock.now()
        sizes: dict[str, int] = {}
        going: set[str] | None = None
        for rule in rules:
            assert rule is not None and not isinstance(rule, str)
            lets_go = self._snapshots_going(archives, rule, now, sizes)
            going = lets_go if going is None else going & lets_go
        kept = {archives[-1].name} | self._told_in(archives, entries)
        return (going or set()) - kept

    def _behind(self, name: str, plan: Plan | None = None) -> list[Problem]:
        """A ``subscription_behind`` warning for each declared subscription holding back
        archives (or a stream's windows) ``retain:`` would let go: its change rows there are
        not acked. None when the config cannot be planned."""
        if self._subscriptions is None:
            return []
        if plan is None:
            try:
                plan = self._prepare(name, None, False)[1]
            except ReconError:
                return []
        if plan.config is None or not plan.config.subscriptions:
            return []
        mission = self._missions.mission_dir(name)
        streams = {stream.index for stream in plan.streams}
        letting: dict[str | None, set[str]] = {
            None: self._snapshots_letting_go(mission, plan.config, streams)
        }
        for stream in plan.streams:
            windows = self._archives.archives(mission, stream=stream.name)
            if isinstance(stream.retain, str) or len(windows) < 2:
                continue
            going = windows_going(self._archives, windows, stream.retain, self._clock.now(), {})
            letting[stream.name] = going - {windows[-1].name}
        problems = []
        for location, going in letting.items():
            if not going:
                continue
            for index, (subscription, archives) in self._subscriptions.held(
                name, plan, location
            ).items():
                count = len(archives & going)
                if not count:
                    continue
                problems.append(
                    Problem(
                        f"subscriptions.{index}",
                        f"subscription {subscription} has not acked change rows in {count} "
                        f"archive(s) retain: would remove, so they are kept until it does",
                        kind="subscription_behind",
                        fix=f"read and ack them (recon subscribe {name} {subscription}, then "
                        f"recon ack), deliver them (recon deliver {name}), or remove the "
                        "subscription from subscriptions: if nobody reads it",
                        got=count,
                    )
                )
        return problems

    def _snapshots_going(
        self, archives: list[Path], rule: RetainSpec, now: datetime, sizes: dict[str, int]
    ) -> set[str]:
        """The archives one entry's ``retain:`` lets go: beyond its newest ``archives``, ended
        more than ``seconds`` before now, or past the newest whose sizes add up to ``bytes``.
        Any one of its rules lets an archive go, as a stream's do."""
        going: set[str] = set()
        if rule.archives is not None:
            going.update(archive.name for archive in archives[: -rule.archives])
        if rule.seconds is not None:
            cutoff = now - timedelta(seconds=rule.seconds)
            for archive in archives:
                ended = _when(self._archives.read_meta(archive).get("timestamp"), archive.name)
                if ended is not None and ended < cutoff:
                    going.add(archive.name)
        if rule.bytes is not None:
            held = 0
            for at, archive in enumerate(reversed(archives)):
                if archive.name not in sizes:
                    sizes[archive.name] = self._archives.size(archive)
                held += sizes[archive.name]
                if held > rule.bytes and at > 0:
                    going.update(a.name for a in archives[: len(archives) - at])
                    break
        return going

    def _told_in(self, archives: list[Path], entries: list[CollectorEntry]) -> set[str]:
        """The last archive each table of an entry with ``changes:`` was told in: what its next
        comparison reads, kept whatever a rule says."""
        changing = {entry.name for entry in entries if entry.changes is not None}
        found: dict[tuple[Any, Any], str] = {}
        for archive in reversed(archives):
            for table in self._archives.read_meta(archive).get("tables") or []:
                told = table.get("changes")
                if table.get("collector") not in changing or not isinstance(told, dict):
                    continue
                if told.get("error"):
                    continue
                found.setdefault((table.get("name"), table.get("source")), archive.name)
        return set(found.values())

    def stream(
        self,
        name: str,
        stream: str,
        *,
        windows: int | None = None,
        until: datetime | None = None,
        forever: bool = False,
        config: Path | None = None,
        replace: bool = False,
        on_event: EventCallback | None = None,
        stop: Stop | None = None,
    ) -> StreamDocument:
        """Plan, then run the mission's stream ``stream`` (its collector's name), window after
        window, until ``windows`` are sealed, ``until`` comes, its source ends or fails, or it
        is stopped: ``stop`` asked (as the command line's SIGINT and SIGTERM ask it), or the
        caller's thread interrupted. The window being written is then sealed ``stopped`` once
        what waits is written (a second stop seals it at once), and this returns. With none
        of ``windows``, ``until`` and ``forever``, it runs one window, so it always returns
        unless told otherwise. Raises UsageError for a bound it cannot keep, NotFoundError
        for a name that is no stream of the mission, ReconError (``busy``) when another
        writer holds the stream; a window whose source failed is in the document, and
        :func:`~recon.domain.documents.stream_failure` says what it adds up to."""
        now = self._clock.now()
        windows = _stream_bound(windows, until, forever, now, name, stream)
        source, plan, copy_to = self._prepare(name, config, replace)
        assert plan.config is not None  # _prepare raises on an invalid plan
        planned = _planned_stream(plan, name, stream)
        if copy_to is not None:
            self._missions.write_config(name, source.text)
        mission = self._missions.mission_dir(name)
        sha256 = hashlib.sha256(source.text).hexdigest()
        started: list[Problem] = []
        returns_s, returns = _returns(planned.window, windows, until, forever, now)
        if on_event is not None:
            try:
                on_event({"kind": "stream", "name": stream, "returns_s": returns_s,
                          "returns": returns})  # fmt: skip
            except Exception as exc:  # a receiver that fails is said, never the stream's end
                started.append(
                    Problem(
                        "",
                        f"telemetry stopped going to a receiver that failed: {exc!r}",
                        kind="telemetry_failed",
                        fix="the collected data is unaffected; fix the on_event callback",
                    )
                )
                on_event = None
        run, found = self._run_stream(
            mission, plan.config, planned, sha256, windows=windows, until=until,
            on_event=on_event, stop=stop, name=name, plan=plan,
        )  # fmt: skip
        found = started + found
        return StreamDocument(
            mission=name,
            stream=stream,
            path=str(mission / "streams" / stream),
            returns_s=returns_s,
            returns=returns,
            state=self._archives.stream_state(mission, stream),
            windows=run.windows,
            records=run.records,
            dropped=run.dropped,
            first=run.first,
            last_window=run.last,
            warnings=[w.document() for w in plan.warnings + found],
        )

    def _run_stream(
        self,
        mission: Path,
        config: ReconConfig,
        planned: PlannedStream,
        config_sha256: str,
        *,
        windows: int | None,
        until: datetime | None,
        on_event: EventCallback | None,
        stop: Stop | None = None,
        name: str,
        plan: Plan,
    ) -> tuple[StreamRun, list[Problem]]:
        """Run one stream of ``config`` through the window writer, over a collector built for
        it alone (its ``close()`` is what stops a source that would not); the run, and what it
        found worth saying, one problem of each kind. ``name`` is the mission's."""
        entry = config.collectors[planned.index]
        ctype = self._plugins.collector_types[entry.type]
        catalog = {source.name: source for source in config.catalog}
        [(source, output)] = fan_out(entry, catalog, ctype.accepts_kinds)
        target = entry if output == entry.name else entry.model_copy(update={"name": output})
        subset = config.model_copy(update={"collectors": [entry]})
        warnings: list[Problem] = []
        gaps = StreamGaps()
        # The collector's own telemetry, used on the stream's pump thread alone.
        telemetry = self._telemetry(None, None)
        built = self._build_collectors(mission, subset, telemetry, Deadline(self._clock),
                                       gaps=gaps)  # fmt: skip
        warnings += [plugin_warning(broken) for broken in built.failed]

        def close() -> None:
            warnings.extend(plugin_warning(broken) for broken in built.close())

        try:
            collector = built.raw.get(entry.type)
            if collector is None:
                raise ReconError(
                    f"collector type {entry.type!r} could not be built, so stream "
                    f"{planned.name} cannot run",
                    kind="config",
                    fix="see the plugin_broken warning for why; fix or reinstall its package",
                )
            normalize = _shaping(entry, ctype.normalizes, confined_transforms(
                self._plugins.transforms, subset, mission))  # fmt: skip
            engine = Stream(
                self._archives, self._clock, self._version,
                telemetry=partial(self._window_telemetry, on_event=on_event, warnings=warnings),
                changes=self._changes(name, subset),
            )  # fmt: skip
            held = None if self._subscriptions is None else partial(
                _held, self._subscriptions, name, plan, planned.name
            )  # fmt: skip
            plan_ = StreamPlan(
                name=planned.name,
                at=f"collectors.{planned.index}",
                entry=target,
                source=source,
                window=planned.window,
                buffer=planned.buffer,
                overflow=planned.overflow,
                columns=tuple(entry.normalize or ()),
                retain=planned.retain,
                package=self._plugins.collector_packages.get(entry.type),
                held=held,
            )
            run = engine.run(
                mission,
                plan_,
                partial(_collect, collector, target, source),
                normalize,
                close=close,
                gaps=gaps,
                windows=windows,
                until=until,
                preserve_raw=config.preserve_raw,
                config_sha256=config_sha256,
                resolved=self._resolved(subset),
                stop=stop,
            )
        finally:
            close()
            telemetry.close()
        warnings += engine.warnings
        return run, _once_each(warnings)

    def _changes(self, name: str, config: ReconConfig) -> ChangeDetector | None:
        """The detector of what changed in the mission's tables, under its key, made on first
        use; None when no entry of ``config`` asks for it, so such a mission has no key."""
        if not any(entry.changes is not None for entry in config.collectors):
            return None
        mission = self._missions.mission_dir(name)
        return ChangeDetector(self._archives, mission, self._missions.change_key(name),
                              large=self._key_join, memory_keys=self._memory_keys)  # fmt: skip

    @contextlib.contextmanager
    def _window_telemetry(
        self,
        archive: Path,
        clock: Clock,
        *,
        on_event: EventCallback | None,
        warnings: list[Problem],
    ) -> Iterator[tuple[Telemetry, EventCallback]]:
        """One window's telemetry, as a survey's is: its event log inline, every other
        receiver through a channel of its own, closed when the window is sealed."""
        channel = self._channel() if self._channel is not None else None
        telemetry = self._telemetry(archive, channel, clock)
        emit: EventCallback = on_event or _ignore
        if channel is not None and on_event is not None:
            emit = channel.callback(on_event)
        try:
            yield telemetry, emit
        finally:
            with _ending(telemetry, channel):
                pass
            warnings += _dropped(telemetry) + _undelivered(channel)

    def _prepare(
        self, name: str, config: Path | None, replace: bool, *, expect_sha256: str | None = None
    ) -> tuple[ConfigFile, Plan, Path | None]:
        """The config read once, and its plan. ``expect_sha256``: the digest its bytes must
        have (a tick's), else it is refused ``stale`` before it is planned."""
        mission_config = self._missions.config_path(name)
        path = config or mission_config
        try:
            source = self._missions.read_config(path)
        except NotFoundError as exc:
            raise NotFoundError(
                f"no config for mission {name!r} at {path}",
                fix=f"recon init {name} --template <name> (see `recon templates`), "
                f"or recon survey {name} -c <config.yaml>",
            ) from exc
        if expect_sha256 is not None and hashlib.sha256(source.text).hexdigest() != expect_sha256:
            raise stale(name, expect_sha256)
        plan = build_plan(
            source.data,
            self._plugins,
            self._env,
            base=self._missions.project,
            is_directory=self._missions.is_directory,
        )
        if not plan.valid:
            raise ConfigError(
                f"{path}: {len(plan.problems)} problem(s)",
                plan.problems,
                fix=f"fix each problem, then: recon survey {name} --dry-run",
                complete=plan.complete,
            )
        current = self._missions.config_text(name)
        if config is None or current == source.text:
            return source, plan, None
        if current is not None and not replace:
            raise ConflictError(
                f"{mission_config} exists and differs from {config}; -c would overwrite it",
                fix=f"recon survey {name} -c {config} --replace",
            )
        return source, plan, mission_config

    # --- schedules ---------------------------------------------------------------------

    def _scheduling(self) -> Schedules:
        if self._schedules is None:
            raise UsageError(
                "this recon was built with no scheduler",
                fix="build it with recon.composition.root(), which gives it the host's",
            )
        return self._schedules

    def schedule_install(self, name: str, *, platform: Platform | None = None) -> ScheduleDocument:
        """Project the mission's ``schedule:`` onto the scheduler (the host's, or
        ``platform``'s) and have it load the files; again with the same config it changes
        nothing (``unchanged``). Raises ConfigError for a mission with no ``schedule:``."""
        return self._scheduling().install(name, platform)

    def schedule_remove(self, name: str, *, platform: Platform | None = None) -> ScheduleDocument:
        """Unload and remove the mission's schedule (``removed``), or say there was none
        (``absent``)."""
        return self._scheduling().remove(name, platform)

    def schedule_list(self, *, platform: Platform | None = None) -> SchedulesDocument:
        """The schedules installed for this project's missions."""
        return self._scheduling().list(platform)

    def schedule_status(
        self, mission: str | None = None, *, platform: Platform | None = None
    ) -> SchedulesDocument:
        """Each schedule of the project, or the mission's: installed or not, its config
        changed since or not, when the next tick is due, the last tick, how late the schedule
        is and the ticks skipped in a row."""
        return self._scheduling().status(mission, platform)

    def schedule_tick(self, name: str, *, config_sha256: str) -> TickDocument:
        """One tick of the mission's schedule, what the scheduler starts: a survey under the
        mission lock, logged to ``<mission>/schedule.log``, unless the config is not the one
        whose digest the schedule holds (``stale``), another survey holds the mission
        (``busy``, logged skipped), or it is a missed tick under ``missed: skip``."""
        return self._scheduling().tick(
            name,
            config_sha256,
            lambda scheduled: self._scheduled_survey(name, scheduled, config_sha256),
        )

    # --- subscriptions ----------------------------------------------------------------

    def _subscribing(self) -> Subscriptions:
        if self._subscriptions is None:
            raise UsageError(
                "this recon was built with no subscription store",
                fix="build it with recon.composition.root(), which gives it the mission's files",
            )
        return self._subscriptions

    def subscribe(
        self,
        name: str,
        subscription: str,
        cursor: str | None = None,
        *,
        max_records: int = READ_MAX_RECORDS,
        max_bytes: int = READ_MAX_BYTES,
    ) -> ChangesSince:
        """One read of the subscription the mission declares: the rows of its output its
        ``match:`` keeps, from the cursor it acked last (None acked: from the first archive),
        or from ``cursor`` for a subscriber that keeps its own. It acks nothing: :meth:`ack`
        does, with the read's ``cursor``, once the rows are acted on. Raises NotFoundError for a
        subscription the config does not declare, ReconError ``busy`` while another consumer
        holds it."""
        return self._subscribing().read(
            name, subscription, cursor, max_records=max_records, max_bytes=max_bytes
        )

    def ack(self, name: str, subscription: str, cursor: str) -> SubscriptionDocument:
        """Keep ``cursor`` as what the subscription has acked, so its next read goes on from
        it. Monotone: a cursor before the one acked is refused (UsageError), the same one
        changes nothing (``unchanged``)."""
        return self._subscribing().ack(name, subscription, cursor)

    def unsubscribe(self, name: str, subscription: str) -> SubscriptionDocument:
        """Remove what is kept of the subscription, its acked cursor and dead letters
        (``removed``; ``absent`` when nothing was). A subscription the config still declares
        then reads from the first archive again."""
        return self._subscribing().remove(name, subscription)

    def deliver(self, name: str, *, subscription: str | None = None) -> DeliverDocument:
        """Give each subscription with ``deliver:`` (or the one named) the rows it has not
        acked, to its command: at most ``max_per_tick`` one at a time, the rest as one row
        counting them, all within its ``deadline``. A row a command fails is given again next
        time and set aside after ``attempts``, each said as a warning, never raised. Raises
        NotFoundError for a subscription the config does not declare, UsageError for one with
        no ``deliver:``."""
        return self._subscribing().deliver(name, subscription)

    # --- status, query, templates, capture ----------------------------------------------

    def status(self, mission: str | None = None) -> StatusDocument:
        """Every mission with its latest archive, and the installed plugins. Given a mission,
        that mission alone, still in ``missions`` (one document shape for both), with every
        one of its archives as its ``history``."""
        if mission is None:
            names = self._missions.missions()
        elif self._missions.config_text(mission) is None:
            known = self._missions.missions()
            raise NotFoundError(
                f"no mission {mission!r}; have: {', '.join(known) or '(none)'}",
                fix="recon status",
                details={"value": mission, "allowed": known},
            )
        else:
            names = [mission]
        return StatusDocument(
            root=str(self._missions.root),
            missions=[self._mission_status(name, history=mission is not None) for name in names],
            plugins=_installed(self._plugins),
            warnings=[
                w.document()
                for name in names
                for w in self._abandoned(name) + self._exposed(name) + self._behind(name)
            ],
        )

    def _exposed(self, name: str) -> list[Problem]:
        """A ``mission_exposed`` warning when a path of the mission (one an older recon made,
        or one changed since) is open to other users; recon changes no mode it did not set."""
        path = self._missions.exposed(name)
        if path is None:
            return []
        mission = self._missions.mission_dir(name)
        return [
            Problem(
                "",
                f"mission {name}: {path} can be reached by users other than its owner, and "
                "what a mission collects is its owner's alone",
                kind="mission_exposed",
                fix=f"chmod -R go-rwx '{mission}'",
                got=str(path),
            )
        ]

    def _abandoned(self, name: str) -> list[Problem]:
        return [
            Problem(
                "",
                f"mission {name}: a survey or stream that died while making an archive left "
                f"{left}, which no listing shows and nothing reads",
                kind="archive_abandoned",
                fix=f"nothing was collected into it; remove it: rm -r '{left}'",
                got=str(left),
            )
            for left in self._archives.abandoned(self._missions.mission_dir(name))
        ]

    def _mission_status(self, name: str, *, history: bool = False) -> MissionStatus:
        mission = self._missions.mission_dir(name)
        archives = self._archives.archives(mission)
        return MissionStatus(
            name=name,
            config=str(self._missions.config_path(name)),
            archives=len(archives),
            latest=self._archive_status(archives[-1]) if archives else None,
            captures=self._missions.capture_count(name),
            history=[self._archive_status(a) for a in archives] if history else None,
            streams=[self._stream_status(mission, s) for s in self._archives.streams(mission)],
            subscriptions=self._subscription_statuses(name),
        )

    def _subscription_statuses(self, name: str) -> list[SubscriptionStatus]:
        """The mission's subscriptions, as far as its config can be planned: one that cannot
        lists only those something is kept of."""
        if self._subscriptions is None:
            return []
        try:
            plan: Plan | None = self._prepare(name, None, False)[1]
        except ReconError:
            plan = None
        return self._subscriptions.statuses(name, plan)

    def _stream_status(self, mission: Path, stream: str) -> StreamStatus:
        windows = self._archives.archives(mission, stream=stream)
        return StreamStatus(
            name=stream,
            path=str(mission / "streams" / stream),
            state=self._archives.stream_state(mission, stream),
            windows=len(windows),
            first=windows[0].name if windows else None,
            latest=self._archive_status(windows[-1]) if windows else None,
            retired=self._archives.retired_count(mission, stream),
        )

    def _archive_status(self, archive: Path) -> ArchiveStatus:
        meta = self._archives.read_meta(archive)
        state = self._archives.state(archive)
        return ArchiveStatus(
            id=archive.name,
            path=str(archive),
            state=state,
            ended=_ended(state, meta),
            started_at=_text(meta.get("started_at")),
            finished_at=_text(meta.get("timestamp")),
            format_version=meta.get("format_version"),
            recon_version=meta.get("recon_version"),
            config_sha256=meta.get("config_sha256"),
            tables=[
                TableStatus(
                    name=t.get("name"),
                    type_url=t.get("type_url"),
                    status=t.get("status"),
                    records=t.get("records"),
                    error=_table_error(t.get("error")),
                )
                for t in meta["tables"]
            ],
        )

    def _resolved(self, config: ReconConfig) -> dict[str, Any]:
        """Where a survey's inputs came from: how the project root was found, and each
        variable the mission declares, by name, ``set`` or ``unset``. Never a value."""
        names = sorted(set().union(*declared_env(config, self._plugins.collector_types).values()))
        return {
            "root": self._root_found_by,
            "env": {name: "set" if self._env.get(name) else "unset" for name in names},
        }

    def query(
        self,
        name: str,
        sql: str,
        *,
        archive: str | None = None,
        stream: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> QueryRows:
        """Run ``sql`` against the latest archive, or the one named. ``state`` says whether
        the archive is incomplete, so its rows may be partial. A row is an object keyed by
        column, so a name the SQL gives twice is made unique, in ``columns`` and every row,
        with the suffix DuckDB gives it in a subquery (``a``, ``a_1``, then ``a_1_1`` on a
        further clash).

        With ``stream``, against that stream's windows instead, each table the union of its
        windows': every window, the one ``archive`` names, or those whose span meets
        ``[since, until)`` (found from the windows' ids, so no window outside it is opened).
        Windows collected under different configs are refused, with the ranges to narrow
        to."""
        if stream is not None:
            return self._query_stream(name, sql, stream, archive, since, until)
        if since is not None or until is not None:
            raise UsageError(
                "since and until choose a stream's windows, and no stream is named",
                fix=f"recon query {name} <sql> --stream <name> --since <time>",
            )
        path = self._archive(name, archive)
        telemetry = self._telemetry(None, None)
        with telemetry.span(QUERY, {"recon.mission": name, "recon.archive": path.name}) as span:
            result = self._query.execute(path, sql)
            span.set("recon.rows", len(result.rows))
        columns = _unique(result.columns)
        rows = [dict(zip(columns, row, strict=True)) for row in result.rows]
        return QueryRows(
            mission=name,
            archive=path.name,
            path=str(path),
            state=(state := self._archives.state(path)),
            ended=self._ended(path, state),
            columns=columns,
            rows=rows,
            count=len(rows),
            warnings=[w.document() for w in result.warnings + _dropped(telemetry)],
        )

    def _query_stream(
        self,
        name: str,
        sql: str,
        stream: str,
        archive: str | None,
        since: datetime | None,
        until: datetime | None,
    ) -> QueryRows:
        mission = self._missions.mission_dir(name)
        windows = self._windows(name, stream)
        if archive is not None:
            windows = [self._window(name, stream, windows, archive)]
        for bound in (since, until):
            if bound is not None and bound.tzinfo is None:
                raise UsageError(
                    "since and until must name their time zone: ISO 8601 with an offset",
                    fix=f"recon query {name} <sql> --stream {stream} --since 2026-10-24T18:00Z",
                )
        selected = _meeting(windows, since, until)
        if not selected:
            raise NotFoundError(
                f"no window of stream {stream} meets the range asked; it has "
                f"{windows[0].name} to {windows[-1].name}",
                fix=f"widen --since/--until, or `recon status {name}` for the stream's windows",
            )
        configs: dict[str, list[str]] = {}
        for window in selected:
            sha = self._archives.read_meta(window).get("config_sha256")
            if isinstance(sha, str):
                configs.setdefault(sha, []).append(window.name)
        if len(configs) > 1:
            ranges = "; ".join(
                f"{ids[0]} to {ids[-1]} ({sha[:12]})" for sha, ids in configs.items()
            )
            raise QueryError(
                f"the windows asked for were collected under {len(configs)} configs, whose "
                f"tables may not mean the same: {ranges}",
                fix="query the windows of one config: narrow --since and --until to one of "
                f"these ranges ({ranges}), or name one window with --archive",
                details={"configs": configs},
            )
        telemetry = self._telemetry(None, None)
        about = {"recon.mission": name, "recon.stream": stream, "recon.archive": selected[-1].name}
        with telemetry.span(QUERY, about) as span:
            result = self._query.execute_across(selected, sql)
            span.set("recon.rows", len(result.rows))
        columns = _unique(result.columns)
        rows = [dict(zip(columns, row, strict=True)) for row in result.rows]
        last = selected[-1]
        state = self._archives.stream_state(mission, stream) or self._archives.state(last)
        wide = []
        if len(selected) > MANY_WINDOWS:
            wide.append(
                Problem(
                    "",
                    f"the query read {len(selected)} windows of stream {stream}, each a file "
                    "DuckDB opens: it costs each window it reads",
                    kind="windows_many",
                    fix=f"narrow it to the windows it needs: --since and --until (`recon status "
                    f"{name}` gives the stream's first and latest window)",
                    got=len(selected),
                )
            )
        return QueryRows(
            mission=name,
            archive=last.name,
            path=str(mission / "streams" / stream),
            state=state,
            ended=self._ended(last, state),
            columns=columns,
            rows=rows,
            count=len(rows),
            warnings=[w.document() for w in result.warnings + _dropped(telemetry) + wide],
            stream=stream,
            window_range=WindowRange(first=selected[0].name, last=last.name, count=len(selected)),
        )

    def _windows(self, name: str, stream: str) -> list[Path]:
        """The stream's windows, oldest first; NotFoundError when it has none."""
        mission = self._missions.mission_dir(name)
        windows = self._archives.archives(mission, stream=stream)
        if not windows:
            known = self._archives.streams(mission)
            raise NotFoundError(
                f"stream {stream!r} of mission {name!r} has no window"
                + ("" if stream in known else f"; its streams: {', '.join(known) or '(none)'}"),
                fix=f"recon stream {name} --stream {stream} seals one; `recon status {name}` "
                "lists the streams",
                details={"value": stream, "allowed": known},
            )
        return windows

    def _window(self, name: str, stream: str, windows: list[Path], window_id: str) -> Path:
        for window in windows:
            if window.name == window_id:
                return window
        raise NotFoundError(
            f"window {window_id!r} not found in stream {stream} of {name!r}; it has "
            f"{windows[0].name} to {windows[-1].name}",
            fix=f"recon status {name} names each stream's first and latest window",
            details={"value": window_id},
        )

    def read_since(
        self,
        name: str,
        cursor: str | None = None,
        *,
        archive: str | None = None,
        stream: str | None = None,
        max_records: int = READ_MAX_RECORDS,
        max_bytes: int = READ_MAX_BYTES,
    ) -> RecordsSince:
        """The records of the mission's latest archive, or the one named, written since
        ``cursor`` (None: from its first), at most ``max_records`` and ``max_bytes`` of them. The
        archive's state is read before its records, so a read that finds it no longer
        ``writing`` and has no ``more`` holds its last records.

        With ``stream``, of that stream: from its latest window, or the one ``archive``
        names, or where ``cursor`` left off; a read that ends a window that is sealed, with a
        later one there, gives the cursor to that one and ``more``."""
        if stream is not None:
            return self._read_stream(name, stream, cursor, archive, max_records, max_bytes)[0]
        return self._read(name, self._archive(name, archive), cursor, max_records, max_bytes)

    def _read_stream(
        self,
        name: str,
        stream: str,
        cursor: str | None,
        archive: str | None,
        max_records: int,
        max_bytes: int,
    ) -> tuple[RecordsSince, bool]:
        """One read of a stream, and whether it is the stream's last: no writer extends it,
        and its last window is read to its end. The stream's state is read first."""
        mission = self._missions.mission_dir(name)
        writing = self._archives.stream_state(mission, stream) == "writing"
        windows = self._windows(name, stream)
        inner: str | None = None
        skipped: WindowRange | None = None
        if cursor is not None:
            window_id, inner = _stream_cursor_read(cursor, stream)
            index = window_index(window_id) or 0
            later = [w for w in windows if (window_index(w.name) or 0) >= index]
            if later and later[0].name == window_id:
                path = later[0]
            elif later:  # its window was retired since: go on from the next one kept
                path, inner = later[0], None
                skipped = self._skipped(mission, stream, window_id, path.name)
            else:
                path = self._window(name, stream, windows, window_id)
        elif archive is not None:
            path = self._window(name, stream, windows, archive)
        else:
            path = windows[-1]
        read = self._read(name, path, inner, max_records, max_bytes)
        later = [w for w in windows if (window_index(w.name) or 0) > (window_index(path.name) or 0)]
        done = read.state != "writing" and not read.more
        if done and later:
            on = _stream_cursor(stream, later[0].name, None)
            update = {"cursor": on, "more": True, "stream": stream, "retired_range": skipped}
            return read.model_copy(update=update), False
        on = _stream_cursor(stream, path.name, read.cursor)
        update = {"cursor": on, "stream": stream, "retired_range": skipped}
        return read.model_copy(update=update), done and not writing

    def _skipped(self, mission: Path, stream: str, since: str, kept: str) -> WindowRange:
        """The windows a read jumps, from ``since`` (its cursor's, retired) to the one before
        ``kept``: counted from their indexes, the last named by its tombstone."""
        start, end = window_index(since) or 0, window_index(kept) or 0
        names = {
            window_index(str(stone.get("id"))): stone.get("id")
            for stone in self._archives.retired(mission, stream)
        }
        last = names.get(end - 1)
        return WindowRange(first=since, last=str(last) if last else None, count=end - start)

    def _read(
        self, name: str, path: Path, cursor: str | None, max_records: int, max_bytes: int
    ) -> RecordsSince:
        """One read of the archive at ``path``, its state read first."""
        state = self._archives.state(path)
        read = self._archives.read_since(path, cursor, max_records=max_records, max_bytes=max_bytes)
        return RecordsSince(
            mission=name,
            archive=path.name,
            path=str(path),
            state=state,
            ended=self._ended(path, state),
            table_records=[{"table": r.table, "record": r.record} for r in read.records],
            count=len(read.records),
            cursor=read.cursor,
            exhausted=read.exhausted,
            retracted=read.retracted,
            more=read.more,
        )

    def watch(
        self,
        name: str,
        cursor: str | None = None,
        *,
        archive: str | None = None,
        stream: str | None = None,
        interval_s: float = WATCH_INTERVAL_S,
        max_records: int = READ_MAX_RECORDS,
        max_bytes: int = READ_MAX_BYTES,
    ) -> Iterator[RecordsSince]:
        """Follow the mission's latest archive, or the one named, from ``cursor``: each
        :meth:`read_since` in turn, waiting ``interval_s`` on the clock whenever a read
        caught up while a survey still writes the archive, and reading again at once while
        a bound cuts the reads (``more``). Ends after the read that finds it no longer
        ``writing`` with no ``more``: at once for a finished archive, read whole. Raises
        UsageError for an ``interval_s`` not above 0.

        With ``stream``, follow that stream from its latest window (or the one ``archive``
        names, or ``cursor``'s) into each window after it, exactly as an archive is followed;
        ends once no writer extends the stream and its last window is read to its end."""
        if not interval_s > 0:
            raise UsageError(
                f"the watch's interval is the seconds between two reads, above 0; got {interval_s}",
                fix="leave the interval at its default (1 second), or give a number above 0",
            )
        if stream is not None:
            read, end = self._read_stream(name, stream, cursor, archive, max_records, max_bytes)
            yield read
            while not end:
                if not read.more:
                    self._clock.sleep(interval_s)
                read, end = self._read_stream(
                    name, stream, read.cursor, None, max_records, max_bytes
                )
                yield read
            return
        path = self._archive(name, archive)  # once: the archive followed does not change
        read = self._read(name, path, cursor, max_records, max_bytes)
        yield read
        while read.state == "writing" or read.more:
            if not read.more:
                self._clock.sleep(interval_s)
            read = self._read(name, path, read.cursor, max_records, max_bytes)
            yield read

    def changes_since(
        self,
        name: str,
        output: str,
        cursor: str | None = None,
        *,
        max_records: int = READ_MAX_RECORDS,
        max_bytes: int = READ_MAX_BYTES,
    ) -> ChangesSince:
        """The change rows of ``output`` told since ``cursor`` (None: from the mission's first
        archive), across its archives (a stream's windows), at most ``max_records`` and about
        ``max_bytes`` of them: each row of a change table sealed since, once, with an ``id``
        that names it. Stateless: the caller holds the cursor, and reading again from one gives
        the same rows. Raises NotFoundError for an output no entry with ``changes:`` makes, and
        UsageError for a cursor recon did not give for this output."""
        reader = self._change_reader(name, output)
        position = None if cursor is None else reader.decode(cursor)
        read = reader.read(position, max_records=max_records, max_bytes=max_bytes)
        return ChangesSince(
            mission=name,
            output=output,
            rows=read.rows,
            count=len(read.rows),
            cursor=reader.encode(read.position),
            more=read.more,
            state=read.state,
            retired_range=read.retired,
        )

    def _change_reader(self, name: str, output: str, plan: Plan | None = None) -> ChangeReader:
        """The reader of ``output``'s change rows in the mission ``name``, found by its plan:
        across its archives, or its stream's windows. Raises NotFoundError for an output no
        entry with ``changes:`` makes."""
        if plan is None:
            _, plan, _ = self._prepare(name, None, False)
        if output not in plan.changes:
            known = sorted(plan.changes)
            raise NotFoundError(
                f"no output {output!r} with changes: in mission {name!r}; its outputs with "
                f"changes: {', '.join(known) or '(none)'}",
                fix="name one of them, or give its collector changes: {key: [<field>]} "
                "(recon --skill -r changes)",
                details={"value": output, "allowed": known},
            )
        mission = self._missions.mission_dir(name)
        return ChangeReader(self._archives, mission, name, output, plan.changes[output])

    def _ended(self, archive: Path, state: ArchiveState) -> Ended | None:
        return None if state == "writing" else _ended(state, self._archives.read_meta(archive))

    def _archive(self, name: str, archive_id: str | None) -> Path:
        archives = self._archives.archives(self._missions.mission_dir(name))
        if not archives:
            raise NotFoundError(f"no archives for mission {name!r}", fix=f"recon survey {name}")
        if archive_id is None:
            return archives[-1]
        for archive in archives:
            if archive.name == archive_id:
                return archive
        have = [a.name for a in archives]
        raise NotFoundError(
            f"archive {archive_id!r} not found for {name!r}; have: {', '.join(have)}",
            fix=f"recon status {name} lists its archives",
            details={"value": archive_id, "allowed": have},
        )

    def templates(self) -> TemplatesDocument:
        return TemplatesDocument(
            templates=[
                TemplateInfo(name=t.name, description=t.description)
                for t in self._missions.templates()
            ]
        )

    def capture(self, name: str, payload: Any) -> Capture:
        """Append one ``PostToolUse`` hook payload to the mission's capture log; return the
        capture line. Raises ConfigError for a payload that is not one."""
        if self._missions.config_text(name) is None:
            raise NotFoundError(
                f"no mission {name!r} at {self._missions.mission_dir(name)}",
                fix=f"recon init {name} --template mcp-captures",
            )
        try:
            return record_hook_payload(self._missions.capture_log(name), payload)
        except ConfigError as exc:
            raise ConfigError(str(exc), fix="run recon capture from a PostToolUse hook") from exc


def _held(subscriptions: Subscriptions, name: str, plan: Plan, stream: str) -> set[str]:
    """The windows of ``stream`` holding change rows a declared subscription has not acked."""
    held = subscriptions.held(name, plan, stream)
    return set().union(*(windows for _, windows in held.values()))


def _stream_bound(
    windows: int | None,
    until: datetime | None,
    forever: bool,
    now: datetime,
    mission: str,
    collector: str,
) -> int | None:
    """The windows a stream's run seals at most (None: no bound by count), refusing a bound it
    cannot keep. With no bound given, one window: a stream returns unless told otherwise."""
    run = f"recon stream {mission} --stream {collector}"
    if forever and (windows is not None or until is not None):
        raise UsageError(
            "--forever runs the stream until it is stopped, so it takes no --windows or --until",
            fix=f"{run} --forever, or bound it with --windows N or --until TIME",
        )
    if windows is not None and windows < 1:
        raise UsageError(
            f"--windows is how many windows to seal, 1 or more; got {windows}",
            fix=f"{run} --windows 1 (the default), or more",
        )
    if until is not None and until.tzinfo is None:
        raise UsageError(
            "until must name its time zone: an ISO 8601 time with its offset",
            fix=f"{run} --until 2026-10-24T18:00:00Z",
        )
    if until is not None and until <= now:
        raise UsageError(
            f"until {until.isoformat()} has passed (it is {now.isoformat()})",
            fix=f"{run} --until <a time still to come>",
        )
    return 1 if windows is None and until is None and not forever else windows


def _planned_stream(plan: Plan, mission: str, collector: str) -> PlannedStream:
    """The mission's stream ``collector``; NotFoundError naming its streams when it has no
    such stream."""
    planned = {stream.name: stream for stream in plan.streams}
    if collector in planned:
        return planned[collector]
    known = sorted(planned)
    raise NotFoundError(
        f"no stream {collector!r} in mission {mission!r}; its streams: "
        f"{', '.join(known) or '(none)'}",
        fix=f"recon stream {mission} --stream <one of: {', '.join(known)}>"
        if known
        else "give a collector window: to make it a stream (recon --skill -r streaming)",
        details={"value": collector, "allowed": known},
    )


def _run_bound(
    bound: int | None, windows: int | None, until: datetime | None, forever: bool
) -> list[SourcedValue]:
    """How long a stream's run is, and why: the plan's say of ``--windows``, ``--until`` and
    ``--forever``."""
    said: list[SourcedValue] = []
    if bound is not None:
        given = windows is not None
        said.append(
            SourcedValue(
                path="stream.windows",
                value=bound,
                origin="command" if given else "recon",
                note=""
                if given
                else "no --windows, --until or --forever: one window, so the run returns",
            )
        )
    if until is not None:
        said.append(SourcedValue(path="stream.until", value=until.isoformat(), origin="command"))
    if forever:
        said.append(
            SourcedValue(
                path="stream.forever",
                value=True,
                origin="command",
                note="runs until stopped: Ctrl-C or SIGTERM seals the window being written",
            )
        )
    return said


def _returns(
    window: WindowSpec, bound: int | None, until: datetime | None, forever: bool, now: datetime
) -> tuple[float | None, str]:
    """When a stream's run returns at the latest, in seconds from its start (None when no time
    bounds it), and that said for a person or an agent deciding whether to wait for it."""
    if forever:
        return None, "only when stopped (Ctrl-C or SIGTERM), or when its source ends"
    limits = [] if bound is None or window.seconds is None else [bound * window.seconds]
    if until is not None:
        limits.append(max(0.0, (until - now).total_seconds()))
    if not limits:
        return None, (
            f"after {bound} window(s), each ended by its records or bytes: when the source has "
            "sent them, or it ends, or the stream is stopped"
        )
    most = min(limits)
    return most, (
        f"returns within {most:g}s; sooner when its source ends or a window's records or bytes "
        "end it first. To not wait: a shorter window: seconds, or run it in the background"
    )


def _survey_returns(config: ReconConfig, streams: list[PlannedStream]) -> SourcedValue:
    """When a survey of ``config`` returns at the latest, in seconds from its start: its
    snapshot tables (as long as the mission's ``deadline:`` lets them), then one window of
    each stream, in turn. Null when one of them has no time bound."""
    snapshot = len(streams) < len(config.collectors)
    spans = [stream.window.seconds for stream in streams]
    each = ", ".join(
        f"{stream.name} {stream.window.seconds:g}s"
        if stream.window.seconds is not None
        else f"{stream.name} no time bound"
        for stream in streams
    )
    note = f"one window of each of {len(streams)} stream(s), in turn ({each})"
    first: float | None = 0.0
    if snapshot:
        first = config.deadline
        note += ", after the snapshot tables, " + (
            f"which deadline: bounds at {first:g}s"
            if first is not None
            else "which no deadline: bounds: set deadline: to bound them"
        )
    if None in spans:
        note += "; a window with no seconds: ends on its records or bytes, or its source's end"
    timed = [span for span in spans if span is not None]
    value = None if first is None or len(timed) < len(spans) else first + sum(timed)
    return SourcedValue(path="survey.returns", value=value, origin="recon", note=note)


def _collect(
    collector: Collector, entry: CollectorEntry, source: SourceEntry | None, after: Any
) -> Generator[dict[str, Any], None, None]:
    """The stream's records from its collector, after the position ``after`` when there is
    one; an exception it raises comes out as a CollectorError naming the entry and source."""
    resume: dict[str, Any] = {} if after is None else {"after": after}
    try:
        yield from collector.collect(entry, source, captures=None, **resume)
    except (CollectorError, NoRecordsError):
        raise
    except Exception as exc:
        raise CollectorError(entry.name, source.name if source else None, exc) from exc


def _shaping(
    entry: CollectorEntry, normalizes: bool, transforms: Mapping[str, Any]
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """What a stream applies to each record: the entry's ``normalize:``, unless its type
    applies it itself."""
    spec = entry.normalize
    if normalizes or not spec:
        return dict
    return lambda record: apply_normalize(record, spec, transforms)


def _once_each(problems: list[Problem]) -> list[Problem]:
    """``problems``, one of each kind at each path: the last said."""
    kept: dict[tuple[str, str, Any], Problem] = {}
    for problem in problems:
        kept.pop((problem.kind, problem.path, problem.got), None)
        kept[(problem.kind, problem.path, problem.got)] = problem
    return list(kept.values())


def _ignore(event: dict[str, Any]) -> None:
    return None


#: A stream's cursor names its format first: one this recon does not know is refused.
_STREAM_CURSOR = "s1"


def _stream_cursor(stream: str, window: str, at: str | None) -> str:
    """The cursor of a read of ``stream``: the window, and the store's own cursor in it
    (None: from its start). Opaque: only :func:`_stream_cursor_read` reads it."""
    body = json.dumps({"stream": stream, "window": window, "at": at}, separators=(",", ":"))
    return _STREAM_CURSOR + body.encode().hex()


def _stream_cursor_read(cursor: str, stream: str) -> tuple[str, str | None]:
    """The window and the store's cursor in it that ``cursor`` holds. Raises UsageError for a
    cursor recon did not give for a stream, or gave for another stream."""
    body: Any = None
    if cursor.startswith(_STREAM_CURSOR):
        try:
            body = json.loads(bytes.fromhex(cursor.removeprefix(_STREAM_CURSOR)))
        except ValueError:
            body = None
    fresh = "read without --cursor to start from the stream's latest window"
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("stream"), str)
        or not isinstance(body.get("window"), str)
        or window_index(body["window"]) is None
        or not isinstance(body.get("at"), str | None)
    ):
        raise UsageError(
            "the cursor is not one recon gave for a stream: pass on the cursor the last read "
            "of the stream returned, unchanged",
            fix=fresh,
        )
    if body["stream"] != stream:
        raise UsageError(
            f"the cursor is for stream {body['stream']}, not {stream}",
            fix=f"read stream {body['stream']} with it (--stream {body['stream']}), or {fresh}",
            details={"stream": stream, "cursor_stream": body["stream"]},
        )
    return body["window"], body["at"]


def _meeting(windows: list[Path], since: datetime | None, until: datetime | None) -> list[Path]:
    """The windows whose span meets ``[since, until)``: a window spans from its open to the
    next one's (the last, onward), and its open is in its id, so no window is opened to
    choose."""
    opened = [window_opened(window.name) for window in windows]
    kept = []
    for at, window in enumerate(windows):
        start, end = opened[at], opened[at + 1] if at + 1 < len(windows) else None
        if until is not None and start is not None and start >= until:
            continue
        if since is not None and end is not None and end <= since:
            continue
        kept.append(window)
    return kept


def _when(stamp: Any, archive_id: str) -> datetime | None:
    """When an archive ended, as its ``meta.yaml`` says, else when it started, as its id
    says; None when neither can be read."""
    if isinstance(stamp, str):
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(stamp)
    with contextlib.suppress(ValueError):
        return datetime.strptime(archive_id, ARCHIVE_ID_FORMAT).replace(tzinfo=UTC)
    return None


def _ended(state: ArchiveState, meta: Mapping[str, Any]) -> Ended | None:
    """How the writer of an archive in ``state``, whose ``meta.yaml`` reads ``meta``, ended:
    as its ``meta.yaml`` says (``finished`` when one an older recon wrote does not say), or
    ``crashed`` when it wrote none, or wrote one while it compared its tables (``ended``
    null) and never wrote how it ended. None while it is being written."""
    if state == "writing":
        return None
    if meta.get("format_version") is None or ("ended" in meta and meta["ended"] is None):
        return "crashed"
    return "stopped" if meta.get("ended") == "stopped" else "finished"


def _unique(columns: list[str]) -> list[str]:
    """``columns``, each name already taken given ``_1`` until it is not."""
    taken: set[str] = set()
    unique = []
    for column in columns:
        while column in taken:
            column = f"{column}_1"
        taken.add(column)
        unique.append(column)
    return unique


def _table_error(error: Any) -> TableError | None:
    """A table's error as ``meta.yaml`` holds it, read for the fields this recon knows: one
    a newer recon wrote may carry more, and is not refused for them."""
    if not isinstance(error, dict):
        return None
    fix = error.get("fix")
    return TableError(
        kind=str(error.get("kind", "unknown")),
        message=str(error.get("message", "")),
        fix=fix if isinstance(fix, str) else None,
    )


def _text(value: Any) -> str | None:
    """A time read back from ``meta.yaml``, where YAML may have parsed it into a date."""
    if value is None:
        return None
    return value.isoformat() if isinstance(value, date) else str(value)


@contextlib.contextmanager
def _ending(telemetry: Telemetry, channel: Channel | None) -> Iterator[None]:
    """End the operation's telemetry when the block ends: the channel first, so its receivers
    get what is queued (or it is counted as dropped), then each sink is closed."""
    try:
        yield
    finally:
        if channel is not None:
            channel.close()
        telemetry.close()


def _undelivered(channel: Channel | None) -> list[Problem]:
    """What the survey's channel did not deliver, each callback that raised, and each
    receiver stuck in a delivery."""
    if channel is None:
        return []
    problems = [
        Problem(
            "",
            f"telemetry stopped going to a receiver that failed: {failure}",
            kind="telemetry_failed",
            fix="the collected data is unaffected; fix the on_event callback",
        )
        for failure in channel.failed
    ]
    problems += [
        Problem(
            "",
            f"{receiver} had not returned from an event when the survey ended, so it was not "
            "closed and the thread delivering to it is left running until it returns",
            kind="telemetry_stuck",
            fix="the collected data is unaffected; give the receiver a timeout (an "
            "OpenTelemetry exporter's), or replace it, so it cannot hang",
        )
        for receiver in channel.stuck
    ]
    if channel.dropped:
        problems.append(
            Problem(
                "",
                f"{channel.dropped} event(s) for OpenTelemetry or on_event were dropped: a "
                "receiver was slower than the survey, which never waits for one",
                kind="events_dropped",
                fix="the archive and its telemetry.jsonl are whole; make the on_event callback "
                "or the OpenTelemetry exporter faster (a batching span processor) to lose none",
                got=channel.dropped,
            )
        )
    return problems


def _dropped(telemetry: Telemetry) -> list[Problem]:
    return [
        Problem(
            "",
            f"telemetry stopped going to a sink that failed: {failure}",
            kind="telemetry_failed",
            fix="the collected data is unaffected; fix the sink (a full disk, an unwritable "
            "archive) for the next survey",
        )
        for failure in telemetry.failed
    ]


def _installed(plugins: Plugins) -> InstalledPlugins:
    return InstalledPlugins(
        collectors=[
            CollectorTypeInfo(
                type=name,
                summary=ct.summary,
                effects=sorted(ct.effects) if ct.effects is not None else "unknown",
            )
            for name, ct in sorted(plugins.collector_types.items())
        ],
        transforms=sorted(f"${name}" for name in plugins.transforms),
        broken=[
            BrokenPluginInfo(group=b.group, name=b.name, value=b.value, error=b.error)
            for b in plugins.broken
        ],
    )

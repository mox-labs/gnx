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
from datetime import date, datetime
from functools import partial
from typing import TYPE_CHECKING, Any, Protocol

from recon.application.captures import record_hook_payload
from recon.application.collectors import confined_transforms, declared_env
from recon.application.plan import Plan, PlannedStream, build_plan, fan_out, plugin_warning
from recon.application.stream import Stream, StreamGaps, StreamPlan, StreamRun
from recon.application.survey import CollectorError, Survey
from recon.domain.archives import (
    ARCHIVE_ID_FORMAT,
    READ_LIMIT,
    READ_MAX_BYTES,
    WATCH_INTERVAL_S,
)
from recon.domain.deadline import Deadline
from recon.domain.documents import (
    ArchiveStatus,
    BrokenPluginInfo,
    CollectorTypeInfo,
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
from recon.domain.stream import StreamStop, window_index, window_opened
from recon.domain.telemetry import QUERY, SURVEY

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator, Mapping
    from pathlib import Path

    from recon.application.collectors import SurveyCollectors
    from recon.domain.archives import ArchiveQuery, ArchiveState, ArchiveStore, Ended
    from recon.domain.clock import Clock
    from recon.domain.collector import Collector, Plugins
    from recon.domain.documents import Capture
    from recon.domain.missions import ConfigFile, MissionStore
    from recon.domain.models import CollectorEntry, ReconConfig, SourceEntry
    from recon.domain.stream import GapLog, WindowSpec
    from recon.domain.telemetry import Channel, EventCallback, Telemetry


class CollectorFactory(Protocol):
    """Builds a collector for each installed type a mission uses, reporting through the
    survey's telemetry, with the types whose factory failed (which a survey reports and whose
    tables fail with a config error), and what to close when the survey ends; ``gaps`` is where
    a stream's collector says when its source was not read."""

    def __call__(
        self,
        mission_dir: Path,
        config: ReconConfig,
        telemetry: Telemetry,
        deadline: Deadline,
        /,
        *,
        gaps: GapLog | None = None,
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

#: Past this many windows, a query of a stream warns (``many_windows``) how to narrow it:
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
    ) -> None:
        """``root_found_by`` is how the composition root found the project root:
        ``argument`` (the caller named it), ``repository`` (the nearest ancestor holding
        ``.git``) or ``working_directory``. Each survey records it. ``channel`` makes each
        survey's channel, through which OpenTelemetry and ``on_event`` receive; without
        one they receive at once, on the survey's thread."""
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
        from its start, or null when only a stop or its source ends it)."""
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
                SourcedValue(path="stream.returns", value=returns_s, source="recon", note=returns)
            )
        requirements, said, broken = self._preflight(mission, plan.config)
        return PlanDocument(
            mission=name,
            valid=plan.valid,
            runs=[run.document() for run in plan.runs],
            effects=plan.effects(),
            writes=PlanWrites(
                config=str(copy_to) if copy_to else None,
                archive=str(mission / "archive" / "<archive id>"),
                captures=bool(plan.config and plan.config.preserve_raw),
                streams=[str(mission / "streams" / stream.name) for stream in plan.streams],
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
        dirt road of a stream, a survey run from cron."""
        source, plan, copy_to = self._prepare(name, config, replace)
        assert plan.config is not None  # _prepare raises on an invalid plan
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
            with _ending(telemetry, channel), telemetry.span(SURVEY, about) as span:
                built = self._build_collectors(mission, snapshot, telemetry, deadline)
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
                        extracts=extracts, kinds=kinds, deadline=deadline,
                    )  # fmt: skip
                    tables = survey.run(
                        plan.config,
                        built.collectors,
                        archive,
                        started=started,
                        config_sha256=sha256,
                        resolved=self._resolved(plan.config),
                        streams=streams,
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
        windows: list[WindowOutcome] = []
        for stream in plan.streams:
            run, found = self._run_stream(
                mission, plan.config, stream, sha256, windows=1, until=None, on_event=caller
            )
            warnings += found
            windows += [run.last] if run.last is not None else []
        failed = failed_tables or any(w.table.status == "error" for w in windows)
        return SurveyDocument(
            mission=name,
            archive=str(archive),
            status="incomplete" if failed or not survey.finished else "complete",
            tables=tables,
            warnings=[w.document() for w in warnings],
            windows=windows,
        )

    def stream(
        self,
        name: str,
        collector: str,
        *,
        windows: int | None = None,
        until: datetime | None = None,
        forever: bool = False,
        config: Path | None = None,
        replace: bool = False,
        on_event: EventCallback | None = None,
        stop: StreamStop | None = None,
    ) -> StreamDocument:
        """Plan, then run the stream of the mission's collector ``collector``, window after
        window, until ``windows`` are sealed, ``until`` comes, its source ends or fails, or it
        is stopped: ``stop`` asked (as the command line's SIGINT and SIGTERM ask it), or the
        caller's thread interrupted. The window being written is then sealed ``signal`` once
        what waits is written (a second stop seals it at once), and this returns. With none
        of ``windows``, ``until`` and ``forever``, it runs one window, so it always returns
        unless told otherwise. Raises UsageError for a bound it cannot keep, NotFoundError
        for a collector that is no stream of the mission, ReconError (``busy``) when another
        writer holds the stream; a window whose source failed is in the document, and
        :func:`~recon.domain.documents.stream_failure` says what it adds up to."""
        now = self._clock.now()
        windows = _stream_bound(windows, until, forever, now, name, collector)
        source, plan, copy_to = self._prepare(name, config, replace)
        assert plan.config is not None  # _prepare raises on an invalid plan
        planned = _planned_stream(plan, name, collector)
        if copy_to is not None:
            self._missions.write_config(name, source.text)
        mission = self._missions.mission_dir(name)
        sha256 = hashlib.sha256(source.text).hexdigest()
        started: list[Problem] = []
        if on_event is not None:
            returns_s, returns = _returns(planned.window, windows, until, forever, now)
            try:
                on_event({"kind": "stream", "name": collector, "returns_s": returns_s,
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
            on_event=on_event, stop=stop,
        )  # fmt: skip
        found = started + found
        return StreamDocument(
            mission=name,
            stream=collector,
            path=str(mission / "streams" / collector),
            state=self._archives.stream_state(mission, collector),
            windows=run.windows,
            records=run.records,
            dropped=run.dropped,
            first=run.first,
            last=run.last,
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
        stop: StreamStop | None = None,
    ) -> tuple[StreamRun, list[Problem]]:
        """Run one stream of ``config`` through the window writer, over a collector built for
        it alone (its ``close()`` is what stops a source that would not); the run, and what it
        found worth saying, one problem of each kind."""
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
            )  # fmt: skip
            plan = StreamPlan(
                name=planned.name,
                at=f"collectors.{planned.index}",
                entry=target,
                source=source,
                window=planned.window,
                buffer=planned.buffer,
                overflow=planned.overflow,
                columns=tuple(entry.normalize or ()),
                retain=planned.retain,
            )
            run = engine.run(
                mission,
                plan,
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
        self, name: str, config: Path | None, replace: bool
    ) -> tuple[ConfigFile, Plan, Path | None]:
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
            warnings=[w.document() for name in names for w in self._abandoned(name)],
        )

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
        )

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
            archive=str(path),
            archive_id=path.name,
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
                    kind="many_windows",
                    fix=f"narrow it to the windows it needs: --since and --until (`recon status "
                    f"{name}` gives the stream's first and latest window)",
                    got=len(selected),
                )
            )
        return QueryRows(
            mission=name,
            archive=str(mission / "streams" / stream),
            archive_id=last.name,
            state=state,
            ended=self._ended(last, state),
            columns=columns,
            rows=rows,
            count=len(rows),
            warnings=[w.document() for w in result.warnings + _dropped(telemetry) + wide],
            stream=stream,
            windows=WindowRange(first=selected[0].name, last=last.name, count=len(selected)),
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
        limit: int = READ_LIMIT,
        max_bytes: int = READ_MAX_BYTES,
    ) -> RecordsSince:
        """The records of the mission's latest archive, or the one named, written since
        ``cursor`` (None: from its first), at most ``limit`` and ``max_bytes`` of them. The
        archive's state is read before its records, so a read that finds it no longer
        ``writing`` and has no ``more`` holds its last records.

        With ``stream``, of that stream: from its latest window, or the one ``archive``
        names, or where ``cursor`` left off; a read that ends a window that is sealed, with a
        later one there, gives the cursor to that one and ``more``."""
        if stream is not None:
            return self._read_stream(name, stream, cursor, archive, limit, max_bytes)[0]
        return self._read(name, self._archive(name, archive), cursor, limit, max_bytes)

    def _read_stream(
        self,
        name: str,
        stream: str,
        cursor: str | None,
        archive: str | None,
        limit: int,
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
        read = self._read(name, path, inner, limit, max_bytes)
        later = [w for w in windows if (window_index(w.name) or 0) > (window_index(path.name) or 0)]
        done = read.state != "writing" and not read.more
        if done and later:
            on = _stream_cursor(stream, later[0].name, None)
            update = {"cursor": on, "more": True, "stream": stream, "skipped": skipped}
            return read.model_copy(update=update), False
        on = _stream_cursor(stream, path.name, read.cursor)
        update = {"cursor": on, "stream": stream, "skipped": skipped}
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
        self, name: str, path: Path, cursor: str | None, limit: int, max_bytes: int
    ) -> RecordsSince:
        """One read of the archive at ``path``, its state read first."""
        state = self._archives.state(path)
        read = self._archives.read_since(path, cursor, limit=limit, max_bytes=max_bytes)
        return RecordsSince(
            mission=name,
            archive=str(path),
            archive_id=path.name,
            state=state,
            ended=self._ended(path, state),
            records=[{"table": r.table, "record": r.record} for r in read.records],
            count=len(read.records),
            cursor=read.cursor,
            sealed=read.sealed,
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
        limit: int = READ_LIMIT,
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
            read, end = self._read_stream(name, stream, cursor, archive, limit, max_bytes)
            yield read
            while not end:
                if not read.more:
                    self._clock.sleep(interval_s)
                read, end = self._read_stream(name, stream, read.cursor, None, limit, max_bytes)
                yield read
            return
        path = self._archive(name, archive)  # once: the archive followed does not change
        read = self._read(name, path, cursor, limit, max_bytes)
        yield read
        while read.state == "writing" or read.more:
            if not read.more:
                self._clock.sleep(interval_s)
            read = self._read(name, path, read.cursor, limit, max_bytes)
            yield read

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
                source="command" if given else "recon",
                note=""
                if given
                else "no --windows, --until or --forever: one window, so the run returns",
            )
        )
    if until is not None:
        said.append(SourcedValue(path="stream.until", value=until.isoformat(), source="command"))
    if forever:
        said.append(
            SourcedValue(
                path="stream.forever",
                value=True,
                source="command",
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


def _ended(state: ArchiveState, meta: Mapping[str, Any]) -> Ended | None:
    """Whether the survey of an archive in ``state``, whose ``meta.yaml`` reads ``meta``, ran
    to its end: it did when it wrote ``meta.yaml``. None while it is being written."""
    if state == "writing":
        return None
    return "finished" if meta.get("format_version") is not None else "stopped"


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

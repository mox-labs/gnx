"""The collect core's driving port: one act at a time, and what an act needs around it.

:class:`Collection` is what the observe layer and any outside driver (a harness, a flow) use to
collect: a mission's config read once and planned (:meth:`Collection.prepare`), the hold one
writer takes (:meth:`Collection.hold`), a survey's snapshot tables run into one archive
(:meth:`Collection.snapshot`), a stream read into windows (:meth:`Collection.stream`), and SQL
over what was landed (:meth:`Collection.query`). It knows no act but the one it runs: which
spans to ask for, what changed since, what to retire and when to run are the observe layer's.

Each operation returns a versioned document (``recon.core.domain.documents``) or a plain
result, or raises a :class:`~recon.core.domain.errors.ReconError` that says what to do next.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from recon.core.application.captures import record_hook_payload
from recon.core.application.collectors import declared_env
from recon.core.application.items import Unpacking, run_events
from recon.core.application.plan import Plan, build_plan, fan_out, plugin_warning
from recon.core.application.stream import Stream, StreamPlan
from recon.core.application.survey import CollectorError, Survey, TablePages, run_of
from recon.core.domain.archives import ARCHIVE_ID_FORMAT, ArchiveRef
from recon.core.domain.collector import Position
from recon.core.domain.deadline import Deadline
from recon.core.domain.documents import (
    Act,
    BrokenPluginInfo,
    CollectorTypeInfo,
    ConverterInfo,
    InstalledPlugins,
    MissionDocument,
    QueryRows,
    Required,
    SchemaDocument,
    SourcedValue,
    SourceKindInfo,
    TemplateInfo,
    TemplatesDocument,
    TypeInfo,
    TypesDocument,
    WindowRange,
    failure_kind,
)
from recon.core.domain.errors import (
    ConfigError,
    ConflictError,
    NoRecordsError,
    NotFoundError,
    Problem,
    QueryError,
    ReconError,
    UsageError,
)
from recon.core.domain.http import reported_url
from recon.core.domain.missions import ConfigFile, ConfigGiven
from recon.core.domain.models import ReconConfig
from recon.core.domain.paginate import Resume
from recon.core.domain.ranges import Interval, RangeRun, fill
from recon.core.domain.stream import window_opened
from recon.core.domain.telemetry import QUERY, SURVEY

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator, Sequence
    from contextlib import AbstractContextManager

    from recon.core.application.collectors import ActCollectors
    from recon.core.application.items import Event
    from recon.core.application.plan import PlannedRun, PlannedStream
    from recon.core.application.stream import StreamRun
    from recon.core.domain.archives import (
        ArchiveQuery,
        Archives,
        ArchiveState,
        Ended,
        Hold,
        Landing,
    )
    from recon.core.domain.clock import Clock
    from recon.core.domain.collector import Collector, Plugins, Run
    from recon.core.domain.documents import (
        Capture,
        Span,
        TableOutcome,
        WindowOutcome,
    )
    from recon.core.domain.missions import MissionStore
    from recon.core.domain.stream import Stop
    from recon.core.domain.telemetry import Channel, EventCallback, EventSink, Telemetry


class CollectorFactory(Protocol):
    """Builds a collector for each installed type the mission ``mission`` uses, reporting
    through the act's telemetry, with the types whose build failed (which an act reports and
    whose tables fail with a config error), and what to close when the act ends."""

    def __call__(
        self, mission: str, config: ReconConfig, telemetry: Telemetry, deadline: Deadline, /
    ) -> ActCollectors: ...


class TelemetryFactory(Protocol):
    """The telemetry for one operation: an act passes the event log of the archive it writes,
    which is then one of the sinks, and its channel, through which every other sink receives;
    an operation that writes no archive passes None for both, and its sinks receive at once.
    A stream's window passes the clock its telemetry reads (the window's own times); None is
    recon's clock."""

    def __call__(
        self, events: EventSink | None, channel: Channel | None, clock: Clock | None = None, /
    ) -> Telemetry: ...


#: Makes an act's channel (``recon.core.domain.telemetry.Channel``).
ChannelFactory = Callable[[], "Channel"]

#: What a stream's run is told after each window is sealed and let go, under the stream's hold:
#: the hold the window was written under, and the window.
Sealed = Callable[["Hold", "WindowOutcome"], None]

DEFAULT_TEMPLATE = "code-forensics"

#: Where a config given as a value was read from, as its ``ConfigFile`` says.
VALUE = "value"

#: Past this many windows, a query of a stream warns (``windows_many``) how to narrow it:
#: each window is a file DuckDB opens.
MANY_WINDOWS = 300


@dataclass(frozen=True)
class Prepared:
    """A mission's config read once (``source``), its plan (valid), and where ``-c`` copies it
    (None: the mission's own is planned)."""

    source: ConfigFile
    plan: Plan
    copy_to: str | None

    @property
    def config(self) -> ReconConfig:
        assert self.plan.config is not None  # a prepared plan is valid
        return self.plan.config

    @property
    def sha256(self) -> str:
        """The digest of the config's bytes: what each archive it lands records."""
        return hashlib.sha256(self.source.text).hexdigest()

    @property
    def recorded(self) -> dict[str, Any] | None:
        """What each archive it lands keeps of a config given as a value, which has no file to
        read again: its data, every URL in it as recon reports one (user information and
        signature parameters redacted, ``reported_url``). None for a config file: the archive's
        digest names its bytes."""
        if self.source.origin != VALUE or not isinstance(self.source.data, dict):
            return None
        return {key: _reported(value) for key, value in self.source.data.items()}


@dataclass(frozen=True)
class Snapshot:
    """What one snapshot act did: the archive it sealed, its tables as their outcomes were
    recorded, what it found worth saying, and whether the archive was finished (its meta
    recorded, and marked complete when every table succeeded)."""

    archive: ArchiveRef
    tables: list[TableOutcome]
    warnings: list[Problem]
    finished: bool


class Collection:
    """The collect core's operations."""

    def __init__(
        self,
        *,
        missions: MissionStore,
        landing: Landing,
        archives: Archives,
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
        """``landing`` is where acts land their archives, ``archives`` how they are read back,
        and ``query`` the SQL over them. ``root_found_by`` is how the composition root found the
        project root: ``argument`` (the caller named it), ``repository`` (the nearest ancestor
        holding ``.git``) or ``working_directory``; each act records it. ``channel`` makes each
        act's channel, through which OpenTelemetry and ``on_event`` receive; without one they
        receive at once, on the act's thread."""
        self._missions = missions
        self._landing = landing
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

    def accepts(self, type_name: str) -> frozenset[str] | None:
        """The source kinds the installed type ``type_name`` accepts; None for any."""
        return self._plugins.accepts(type_name)

    # --- missions ------------------------------------------------------------------------

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
        where = self._missions.where(name)
        current = self._missions.config_text(name)
        if current == text:
            return MissionDocument(mission=name, config=where, from_=origin, outcome="unchanged")
        if current is not None and not replace:
            given = str(config) if config is not None else origin.removeprefix("template:")
            raise ConflictError(
                f"mission {name!r} already has a different config: {where}",
                fix=f"recon init {name} {flag} {given} --replace to overwrite it, or edit "
                f"{where} and run `recon survey {name} --dry-run`",
                details={"config": where},
            )
        self._missions.write_config(name, text)
        return MissionDocument(
            mission=name,
            config=where,
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
                f"no mission {name!r} in project {self._missions.project}",
                fix=f"recon init {name} --template mcp-captures",
            )
        try:
            return record_hook_payload(self._missions.capture_log(name), payload)
        except ConfigError as exc:
            raise ConfigError(str(exc), fix="run recon capture from a PostToolUse hook") from exc

    # --- planning ------------------------------------------------------------------------

    def read(self, name: str, config: ConfigGiven = None) -> ConfigFile:
        """The config of the mission ``name``, or the file ``config`` (``-``: standard input),
        or ``config`` itself, a value (a ``ReconConfig`` or a mapping), read once: the bytes an
        act plans and runs, and records in each archive it lands. A value's bytes are its
        canonical JSON, so the same value is the same config. Raises NotFoundError when there
        is none."""
        if isinstance(config, ReconConfig | Mapping):
            return _value(config)
        try:
            return (
                self._missions.config(name) if config is None
                else self._missions.read_config(config)
            )  # fmt: skip
        except NotFoundError as exc:
            where = config if config is not None else self._missions.where(name)
            raise NotFoundError(
                f"no config for mission {name!r} at {where}",
                fix=f"recon init {name} --template <name> (see `recon templates`), "
                f"or recon survey {name} -c <config.yaml>",
            ) from exc

    def prepare(
        self,
        name: str,
        config: ConfigGiven = None,
        replace: bool = False,
        *,
        source: ConfigFile | None = None,
    ) -> Prepared:
        """The mission's config read once (or ``source``, read already by :meth:`read`), its
        plan, and where ``-c`` copies it (None: the mission's own is planned, or a value,
        which is never kept as the mission's). Raises ConfigError listing every problem, and
        ConflictError for a config file that differs from the mission's own, unless
        ``replace``."""
        where = self._missions.where(name)
        source = source if source is not None else self.read(name, config)
        plan = build_plan(
            source.data,
            self._plugins,
            self._env,
            base=self._missions.project,
            is_directory=self._missions.is_directory,
        )
        if not plan.valid:
            raise ConfigError(
                f"{source.origin}: {len(plan.problems)} problem(s)",
                plan.problems,
                fix=f"fix each problem, then: recon survey {name} --dry-run",
                complete=plan.complete,
            )
        if not isinstance(config, Path):  # the mission's own, or a value: nothing to keep
            return Prepared(source, plan, None)
        current = self._missions.config_text(name)
        if current == source.text:
            return Prepared(source, plan, None)
        if current is not None and not replace:
            raise ConflictError(
                f"{where} exists and differs from {config}; -c would overwrite it",
                fix=f"recon survey {name} -c {config} --replace",
            )
        return Prepared(source, plan, where)

    def keep_config(self, name: str, prepared: Prepared) -> None:
        """Keep a config given with ``-c`` as the mission's own, as ``prepare`` said it would
        (``copy_to``); nothing when the mission's own was planned."""
        if prepared.copy_to is not None:
            self._missions.write_config(name, prepared.source.text)

    def describe(
        self, run: PlannedRun, config: ReconConfig, span: RangeRun | None = None
    ) -> dict[str, Any]:
        """A planned run as a plan shows it; a ranged one (``span``) with its span, and its
        request described with its first step's bounds filled."""
        document = run.document()
        if span is None:
            return document
        entry = next(e for e in config.collectors if e.name == run.collector)
        target = entry.model_copy(update={"name": run.output})
        source = next((s for s in config.catalog if s.name == run.source), None)
        if span.steps:
            asked = target.model_copy(update=fill(target.settings, span.steps[0]))
            declared = self._plugins.collector_types[run.type].declared
            document |= dict(declared.describe(run_of(self._plugins, asked, source)))
        return {**document, "range": span.document()}

    def preflight(
        self, name: str, config: ReconConfig
    ) -> tuple[list[Required], list[str], list[Problem]]:
        """Ask the collector of each type that declares a preflight what each of its runs
        needs: the requirements, each said once with the outputs that need it; what was done,
        a sentence per type; and a warning for each type that could not be built or closed."""
        types = {n: it for n, it in self._plugins.collector_types.items() if it.preflight}
        entries = [entry for entry in config.collectors if entry.type in types]
        if not entries:
            return [], [], []
        said = [
            f"{name}: {types[name].preflight}" for name in dict.fromkeys(e.type for e in entries)
        ]
        subset = config.model_copy(update={"collectors": entries})
        built = self._build_collectors(
            name, subset, self._telemetry(None, None), Deadline(self._clock)
        )
        warnings = [plugin_warning(broken) for broken in built.failed]
        merged: dict[tuple[str, bool, str, str], list[str]] = {}
        catalog = {source.name: source for source in config.catalog}
        try:
            for entry in entries:
                ask = built.requirements.get(entry.type)
                if ask is None:
                    continue
                for source, output in fan_out(entry, catalog, self._plugins.accepts(entry.type)):
                    target = (
                        entry if output == entry.name else entry.model_copy(update={"name": output})
                    )
                    for found in ask(run_of(self._plugins, target, source)):
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

    def preflight_said(self, config: ReconConfig, check: bool) -> list[SourcedValue]:
        """Whether a plan checked what its runs need, said when a run's type declares a
        preflight (an ``mcp`` run's): off by default, so a plan starts and connects to
        nothing."""
        types = self._plugins.collector_types
        declared = sorted({e.type for e in config.collectors if e.type in types
                           and types[e.type].preflight})  # fmt: skip
        if not declared:
            return []
        if check:
            return [SourcedValue(path="preflight", value=True, origin="command",
                                 note=f"checked: {', '.join(declared)}")]  # fmt: skip
        return [SourcedValue(
            path="preflight",
            value=False,
            origin="recon",
            note=f"off: nothing was started or reached; --check runs the preflight of "
            f"{', '.join(declared)} (an mcp server is started, or connected to, to list its "
            "tools)",
        )]  # fmt: skip

    def types(self) -> TypesDocument:
        """Every collector type a mission may name, installed or given, with its config's JSON
        Schema; every source kind and whose it is; every transform; every converter and the
        media types it converts; and what failed to load. ``recon.v1.types``."""
        plugins = self._plugins
        collectors = []
        for name, it in sorted(plugins.collector_types.items()):
            declared = it.declared
            capabilities = [flag for flag, has in (("paginate", it.paged),
                            ("preflight", bool(it.preflight))) if has]  # fmt: skip
            collectors.append(TypeInfo(
                type=name,
                origin=it.package,
                summary=it.summary,
                effects=sorted(declared.effects) if declared.effects is not None else "unknown",
                records_kind=declared.records,
                capabilities=capabilities,
                config=it.settings.model_json_schema() if it.settings is not None else None,
                accepts=sorted(declared.accepts) if declared.accepts is not None else None,
            ))  # fmt: skip
        sources = [
            SourceKindInfo(kind=name, origin=kind.package, summary=kind.summary,
                           config=kind.settings.model_json_schema() if kind.settings else None)
            for name, kind in sorted(plugins.source_kinds.items())
        ]  # fmt: skip
        converters = [
            ConverterInfo(converter=name, origin=found.package, summary=found.summary,
                          media_types=sorted(found.converter.media_types))
            for name, found in sorted(plugins.converters.items())
        ]  # fmt: skip
        installed = self.installed()
        return TypesDocument(collectors=collectors, sources=sources,
                             transforms=installed.transforms, converters=converters,
                             broken=installed.broken)  # fmt: skip

    def schema(self) -> SchemaDocument:
        """The JSON Schema of a mission's config. ``recon.v1.mission-config``."""
        return SchemaDocument(json_schema=ReconConfig.model_json_schema())

    def installed(self) -> InstalledPlugins:
        """The installed collector types and transforms, and what failed to load."""
        plugins = self._plugins
        return InstalledPlugins(
            collectors=[
                CollectorTypeInfo(
                    type=name,
                    summary=it.summary,
                    effects=sorted(it.declared.effects)
                    if it.declared.effects is not None
                    else "unknown",
                )
                for name, it in sorted(plugins.collector_types.items())
            ],
            transforms=sorted(f"${name}" for name in plugins.transforms),
            broken=[
                BrokenPluginInfo(group=b.group, name=b.name, value=b.value, error=b.error)
                for b in plugins.broken
            ],
        )

    # --- acts ----------------------------------------------------------------------------

    def hold(self, mission: str, *, stream: str | None = None) -> AbstractContextManager[Hold]:
        """Hold the mission's surveys (or its stream ``stream``) for this writer alone until
        the block ends: :meth:`Landing.hold`. Raises ReconError (``busy``) while another holds
        it."""
        return self._landing.hold(mission, stream=stream)

    def snapshot(
        self,
        hold: Hold,
        name: str,
        prepared: Prepared,
        *,
        started: datetime,
        ranges: Mapping[str, RangeRun] | None = None,
        on_event: EventCallback | None = None,
        scheduled: dict[str, Any] | None = None,
    ) -> Snapshot:
        """Run the prepared config's snapshot tables (every entry that is no stream) into one
        new archive of the mission held, begun at ``started``, and seal it. ``ranges`` is the
        span each ranged table asks for, by output. A survey a scheduled tick runs is given
        ``scheduled`` (``scheduled_at``, ``late_s``), recorded in the archive's meta. Each
        table's outcome goes to ``on_event`` as it lands. A table that fails does not raise:
        its outcome says so. The archive is let go when this returns."""
        streams = {stream.index for stream in prepared.plan.streams}
        full = prepared.config
        config = full.model_copy(
            update={"collectors": [e for i, e in enumerate(full.collectors) if i not in streams]}
        )
        emit = on_event or _ignore
        warnings: list[Problem] = []
        # Held from here until the block ends, so a reader of the archive sees it is being
        # written, however the act ends.
        with hold.begin(started.strftime(ARCHIVE_ID_FORMAT)) as landed:
            archive = landed.ref
            channel = self._channel() if self._channel is not None else None
            telemetry = self._telemetry(landed.events(), channel)
            if channel is not None and on_event is not None:
                on_event = emit = channel.callback(on_event)
            about = {"recon.mission": name, "recon.archive": archive.id}
            deadline = Deadline(self._clock)
            pages = TablePages()
            with _ending(telemetry, channel), telemetry.span(SURVEY, about) as span:
                built = self._build_collectors(name, config, telemetry, deadline)
                try:
                    for broken in built.failed:
                        warnings.append(plugin_warning(broken))
                        emit({"kind": "warning", "reason": str(warnings[-1]),
                              "fix": warnings[-1].fix})  # fmt: skip
                    emit({"kind": "start", "runs": len(prepared.plan.runs),
                          "collectors": len(full.collectors),
                          "sources": len(full.catalog)})  # fmt: skip
                    survey = Survey(
                        self._clock, self._version, telemetry, on_event,
                        plugins=self._plugins, deadline=deadline, pages=pages, ranges=ranges,
                    )  # fmt: skip
                    tables = survey.run(
                        full, built, landed, started=started,
                        config_sha256=prepared.sha256, config_value=prepared.recorded,
                        resolved=self.resolved(full),
                        streams=streams, scheduled=scheduled,
                    )  # fmt: skip
                finally:
                    # Whatever the act opened (a collector's session, the requester's
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
        return Snapshot(archive, tables, warnings, survey.finished)

    def stream(
        self,
        name: str,
        prepared: Prepared,
        planned: PlannedStream,
        *,
        windows: int | None,
        until: datetime | None,
        on_event: EventCallback | None,
        stop: Stop | None = None,
        sealed: Sealed | None = None,
    ) -> tuple[StreamRun, list[Problem]]:
        """Run one stream of the mission through the window writer, over a collector built for
        it alone (its ``close()`` is what stops a source that would not), until ``windows`` are
        sealed, ``until`` comes, its source ends or fails, or it is stopped; each window, once
        sealed and let go, is told to ``sealed`` with the stream's hold. Returns the run and
        what it found worth saying, one problem of each kind."""
        config = prepared.config
        entry = config.collectors[planned.index]
        catalog = {source.name: source for source in config.catalog}
        [(source, output)] = fan_out(entry, catalog, self._plugins.accepts(entry.type))
        target = entry if output == entry.name else entry.model_copy(update={"name": output})
        subset = config.model_copy(update={"collectors": [entry]})
        warnings: list[Problem] = []
        # The collector's own telemetry, used on the stream's pump thread alone.
        telemetry = self._telemetry(None, None)
        built = self._build_collectors(name, subset, telemetry, Deadline(self._clock))
        warnings += [plugin_warning(broken) for broken in built.failed]

        def close() -> None:
            warnings.extend(plugin_warning(broken) for broken in built.close())

        try:
            collector = built.collectors.get(entry.type)
            if collector is None:
                raise ReconError(
                    f"collector type {entry.type!r} could not be built, so stream "
                    f"{planned.name} cannot run",
                    kind="config",
                    fix="see the plugin_broken warning for why; fix or reinstall its package",
                )
            run_ = run_of(self._plugins, target, source)
            shaping = built.shaping(run_)
            engine = Stream(
                self._landing, self._archives, self._clock, self._version,
                telemetry=partial(self._window_telemetry, on_event=on_event, warnings=warnings),
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
                package=self._plugins.collector_types[entry.type].package,
            )
            run = engine.run(
                name,
                plan_,
                partial(_collect, collector, run_, built),
                shaping,
                close=close,
                windows=windows,
                until=until,
                preserve_raw=config.preserve_raw,
                config_sha256=prepared.sha256,
                config_value=prepared.recorded,
                resolved=self.resolved(subset),
                stop=stop,
                sealed=sealed,
            )
        finally:
            close()
            telemetry.close()
        warnings += engine.warnings
        return run, once_each(warnings)

    def collect(
        self,
        name: str,
        spans: Sequence[Span],
        *,
        config: ConfigGiven = None,
        sink: Callable[[Act], object] | None = None,
        stop: Stop | None = None,
    ) -> list[Act]:
        """The acts ``spans`` ask for, each told to ``sink`` as it seals: the mission's snapshot
        tables, each ranged one asking for its span's source time, in one archive, when a span
        names a table that is no stream's; then one window of each stream a ``window`` span
        names, until ``stop`` is asked. The core computes no span of its own: every ranged
        table of the mission must have one (UsageError naming those that do not), and a span
        of another mission, or of an output the mission does not make, is refused. Returns the
        acts, in the order they sealed."""
        prepared = self.prepare(name, config)
        plan = prepared.plan
        made = {run.output: run for run in plan.runs}
        wrong = sorted({s.output for s in spans if s.mission != name or s.output not in made})
        if wrong:
            raise UsageError(
                f"spans name outputs mission {name!r} does not make: {', '.join(wrong)}",
                fix=f"give spans of mission {name}'s outputs (recon.v1.span), as its plan names "
                "them",
                details={"value": wrong, "allowed": sorted(made)},
            )
        ranges = {s.output: _range_run(s) for s in spans if s.kind not in ("snapshot", "window")}
        entries = {entry.name: entry for entry in prepared.config.collectors}
        ranged = sorted(
            run.output
            for run in plan.runs
            if run.stream is None and entries[run.collector].range is not None
        )
        missing = [output for output in ranged if output not in ranges]
        snapshot = [s for s in spans if s.kind != "window"]
        if snapshot and missing:
            raise UsageError(
                f"ranged tables of mission {name!r} were given no span: {', '.join(missing)}",
                fix="give each its span (recon.v1.span): the observe layer's planner gives them",
                details={"value": missing},
            )
        acts: list[Act] = []

        def told(act: Act) -> None:
            acts.append(act)
            if sink is not None:
                sink(act)

        if snapshot:
            with self.hold(name) as hold:
                run = self.snapshot(hold, name, prepared, started=self._clock.now(),
                                    ranges=ranges)  # fmt: skip
            told(Act(mission=name, archive=run.archive.id, tables=run.tables,
                     finished=run.finished))  # fmt: skip
        streams = {stream.name: stream for stream in plan.streams}
        for stream in dict.fromkeys(s.stream for s in spans if s.kind == "window"):
            planned = streams.get(stream or "")
            if planned is None:
                raise UsageError(
                    f"a window span names {stream!r}, which is no stream of mission {name!r}",
                    fix="name one of its streams: " + (", ".join(sorted(streams)) or "(none)"),
                )
            self.stream(
                name, prepared, planned, windows=1, until=None, on_event=None, stop=stop,
                sealed=lambda hold, window: told(Act(
                    mission=name, archive=window.id, stream=window.stream, tables=[window.table],
                )),
            )  # fmt: skip
        return acts

    def records(self, act: Act, output: str) -> Iterator[dict[str, Any]]:
        """The records of the table ``output`` an act sealed, in the order written; none when it
        has no such sealed table."""
        found = self._archives.records(ArchiveRef(act.mission, act.archive, act.stream), output)
        return iter(()) if found is None else found

    @contextlib.contextmanager
    def _window_telemetry(
        self,
        events: EventSink,
        clock: Clock,
        *,
        on_event: EventCallback | None,
        warnings: list[Problem],
    ) -> Iterator[tuple[Telemetry, EventCallback]]:
        """One window's telemetry, as an act's is: its event log inline, every other receiver
        through a channel of its own, closed when the window is sealed."""
        channel = self._channel() if self._channel is not None else None
        telemetry = self._telemetry(events, channel, clock)
        emit: EventCallback = on_event or _ignore
        if channel is not None and on_event is not None:
            emit = channel.callback(on_event)
        try:
            yield telemetry, emit
        finally:
            with _ending(telemetry, channel):
                pass
            warnings += _dropped(telemetry) + _undelivered(channel)

    def resolved(self, config: ReconConfig) -> dict[str, Any]:
        """Where an act's inputs came from: how the project root was found, and each variable
        the mission declares, by name, ``set`` or ``unset``. Never a value."""
        names = sorted(set().union(*declared_env(config, self._plugins).values()))
        return {
            "root": self._root_found_by,
            "env": {name: "set" if self._env.get(name) else "unset" for name in names},
        }

    # --- reading back --------------------------------------------------------------------

    def archive(self, name: str, archive_id: str | None) -> ArchiveRef:
        """The mission's archive ``archive_id``, or its latest; NotFoundError when it has none,
        or none of that id."""
        archives = self._archives.archives(name)
        if not archives:
            raise NotFoundError(f"no archives for mission {name!r}", fix=f"recon survey {name}")
        if archive_id is None:
            return archives[-1]
        for archive in archives:
            if archive.id == archive_id:
                return archive
        have = [a.id for a in archives]
        raise NotFoundError(
            f"archive {archive_id!r} not found for {name!r}; have: {', '.join(have)}",
            fix=f"recon status {name} lists its archives",
            details={"value": archive_id, "allowed": have},
        )

    def windows(self, name: str, stream: str) -> list[ArchiveRef]:
        """The stream's windows, oldest first; NotFoundError when it has none."""
        windows = self._archives.archives(name, stream=stream)
        if not windows:
            known = self._archives.streams(name)
            raise NotFoundError(
                f"stream {stream!r} of mission {name!r} has no window"
                + ("" if stream in known else f"; its streams: {', '.join(known) or '(none)'}"),
                fix=f"recon stream {name} --stream {stream} seals one; `recon status {name}` "
                "lists the streams",
                details={"value": stream, "allowed": known},
            )
        return windows

    def window(
        self, name: str, stream: str, windows: list[ArchiveRef], window_id: str
    ) -> ArchiveRef:
        """The window ``window_id`` among ``windows`` (the stream's); NotFoundError naming the
        stream's first and latest when it is not one of them."""
        for window in windows:
            if window.id == window_id:
                return window
        raise NotFoundError(
            f"window {window_id!r} not found in stream {stream} of {name!r}; it has "
            f"{windows[0].id} to {windows[-1].id}",
            fix=f"recon status {name} names each stream's first and latest window",
            details={"value": window_id},
        )

    def ended(self, archive: ArchiveRef, state: ArchiveState) -> Ended | None:
        """How ``archive``'s writer ended, read from its meta; None while it is written."""
        return None if state == "writing" else ended_of(state, self._archives.meta(archive))

    def query(
        self,
        name: str,
        sql: str,
        *,
        archive: str | None = None,
        stream: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        baseline: str | None = None,
    ) -> QueryRows:
        """Run ``sql`` against the latest archive, or the one named. ``state`` says whether
        the archive is incomplete, so its rows may be partial. A row is an object keyed by
        column, so a name the SQL gives twice is made unique, in ``columns`` and every row,
        with the suffix DuckDB gives it in a subquery (``a``, ``a_1``, then ``a_1_1`` on a
        further clash). Each table is also ``json.<table>`` (each record's JSON text) and
        ``lines.<table>`` (each record with its ``line``, what a row is cited by). With
        ``baseline``, the earlier archive it names is read too, its tables as
        ``baseline.<table>``: what is new since it is ``... EXCEPT SELECT ... FROM
        baseline.<table>``.

        With ``stream``, against that stream's windows instead, each table the union of its
        windows': every window, the one ``archive`` names, or those whose span meets
        ``[since, until)`` (found from the windows' ids, so no window outside it is opened).
        Windows collected under different configs are refused, with the ranges to narrow
        to."""
        if stream is not None:
            if baseline is not None:
                raise UsageError(
                    "a baseline is an earlier archive of the mission, and a stream's windows "
                    "are read by time",
                    fix=f"recon query {name} <sql> --stream {stream} --since <time>, or query "
                    f"the mission's archives with --baseline",
                )
            return self._query_stream(name, sql, stream, archive, since, until)
        if since is not None or until is not None:
            raise UsageError(
                "since and until choose a stream's windows, and no stream is named",
                fix=f"recon query {name} <sql> --stream <name> --since <time>",
            )
        ref = self.archive(name, archive)
        before = None if baseline is None else self.archive(name, baseline)
        telemetry = self._telemetry(None, None)
        with telemetry.span(QUERY, {"recon.mission": name, "recon.archive": ref.id}) as span:
            result = self._query.run([ref], sql, baseline=before)
            span.set("recon.rows", len(result.rows))
        columns = _unique(result.columns)
        rows = [dict(zip(columns, row, strict=True)) for row in result.rows]
        return QueryRows(
            mission=name,
            archive=ref.id,
            baseline=before.id if before is not None else None,
            state=(state := self._archives.state(ref)),
            ended=self.ended(ref, state),
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
        windows = self.windows(name, stream)
        if archive is not None:
            windows = [self.window(name, stream, windows, archive)]
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
                f"{windows[0].id} to {windows[-1].id}",
                fix=f"widen --since/--until, or `recon status {name}` for the stream's windows",
            )
        configs: dict[str, list[str]] = {}
        for window in selected:
            sha = self._archives.meta(window).get("config_sha256")
            if isinstance(sha, str):
                configs.setdefault(sha, []).append(window.id)
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
        about = {"recon.mission": name, "recon.stream": stream, "recon.archive": selected[-1].id}
        with telemetry.span(QUERY, about) as span:
            result = self._query.run(selected, sql)
            span.set("recon.rows", len(result.rows))
        columns = _unique(result.columns)
        rows = [dict(zip(columns, row, strict=True)) for row in result.rows]
        last = selected[-1]
        state = self._archives.stream_state(name, stream) or self._archives.state(last)
        wide = []
        if len(selected) > MANY_WINDOWS:
            wide.append(
                Problem(
                    "",
                    f"the query read {len(selected)} windows of stream {stream}, and it costs "
                    "each window it reads",
                    kind="windows_many",
                    fix=f"narrow it to the windows it needs: --since and --until (`recon status "
                    f"{name}` gives the stream's first and latest window)",
                    got=len(selected),
                )
            )
        return QueryRows(
            mission=name,
            archive=last.id,
            state=state,
            ended=self.ended(last, state),
            columns=columns,
            rows=rows,
            count=len(rows),
            warnings=[w.document() for w in result.warnings + _dropped(telemetry) + wide],
            stream=stream,
            window_range=WindowRange(first=selected[0].id, last=last.id, count=len(selected)),
        )


def _range_run(span: Span) -> RangeRun:
    """The run of a ranged table a span asks for, as the core executes it."""
    if span.kind not in ("continuing", "fixed", "rolling") or not span.start or not span.end:
        raise UsageError(
            f"the span of {span.output} is a {span.kind} range with no start or end",
            fix="give a ranged span its start and end, and its steps (recon.v1.span)",
        )
    steps = tuple(Interval(_instant(step["start"]), _instant(step["end"])) for step in span.steps)
    return RangeRun(
        kind=span.kind,
        interval=Interval(_instant(span.start), _instant(span.end)),
        steps=steps,
        began=span.from_ or "default",
        request=span.request or "",
        moves=span.moves,
    )


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _value(config: ReconConfig | Mapping[str, Any]) -> ConfigFile:
    """A config given as a value, as if read: its data, and its bytes, its canonical JSON (keys
    sorted, no spaces), a date or time as ISO 8601."""
    data: Any = (
        config.model_dump(mode="json", exclude_unset=True)
        if isinstance(config, ReconConfig) else dict(config)
    )  # fmt: skip
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=_iso)  # fmt: skip
    return ConfigFile(origin=VALUE, text=text.encode(), data=json.loads(text))


def _reported(value: Any) -> Any:
    """``value`` with each string that is a URL as recon reports one."""
    if isinstance(value, dict):
        return {key: _reported(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_reported(inner) for inner in value]
    return reported_url(value) if isinstance(value, str) and "://" in value else value


def _iso(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"a config value holds {type(value).__name__}, which no config file can")


def ended_of(state: ArchiveState, meta: Mapping[str, Any]) -> Ended | None:
    """How the writer of an archive in ``state``, whose meta reads ``meta``, ended: as its meta
    says (``finished`` when one an older recon wrote does not say), or ``crashed`` when it
    recorded none, or recorded one while it compared its tables (``ended`` null) and never
    recorded how it ended. None while it is being written."""
    if state == "writing":
        return None
    if meta.get("format_version") is None or ("ended" in meta and meta["ended"] is None):
        return "crashed"
    return "stopped" if meta.get("ended") == "stopped" else "finished"


def once_each(problems: list[Problem]) -> list[Problem]:
    """``problems``, one of each kind at each path: the last said."""
    kept: dict[tuple[str, str, Any], Problem] = {}
    for problem in problems:
        kept.pop((problem.kind, problem.path, problem.got), None)
        kept[(problem.kind, problem.path, problem.got)] = problem
    return list(kept.values())


def _collect(
    collector: Collector, run: Run, built: ActCollectors, after: Any
) -> Generator[Event, None, None]:
    """The stream's events: a crawl recon drives going on from ``after`` (a
    :class:`~recon.core.domain.paginate.Resume`'s document), or the collector's items after the
    position ``after``, when there is one, found by recon (captured in the window's capture, not
    as bodies). An exception it raises comes out as a CollectorError naming the entry and
    source."""
    paged = run.entry.paginate is not None
    resume = Resume.of(after) if paged else None
    position = Position(after) if after is not None and not paged else None
    unpacking = Unpacking(built.converters, None)
    events = run_events(collector, run, unpacking, built.shaping(run), position=position,
                        resume=resume)  # fmt: skip
    try:
        yield from events
        unpacking.end()
    except (CollectorError, NoRecordsError):
        raise
    except Exception as exc:
        source = run.source.name if run.source else None
        raise CollectorError(run.entry.name, source, exc) from exc


def _ignore(event: dict[str, Any]) -> None:
    return None


def _meeting(
    windows: list[ArchiveRef], since: datetime | None, until: datetime | None
) -> list[ArchiveRef]:
    """The windows whose span meets ``[since, until)``: a window spans from its open to the
    next one's (the last, onward), and its open is in its id, so no window is opened to
    choose."""
    opened = [window_opened(window.id) for window in windows]
    kept = []
    for at, window in enumerate(windows):
        start, end = opened[at], opened[at + 1] if at + 1 < len(windows) else None
        if until is not None and start is not None and start >= until:
            continue
        if since is not None and end is not None and end <= since:
            continue
        kept.append(window)
    return kept


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
    """What the act's channel did not deliver, each callback that raised, and each receiver
    stuck in a delivery."""
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
                fix="the archive and its event log are whole; make the on_event callback "
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

"""What recon does, over its ports: scaffold a mission, plan it, survey it, report on the
missions, query an archive, list the templates, append a capture.

Each operation returns one versioned document (``recon.domain.documents``), whose
``document()`` is what ``--json`` prints, or raises a
:class:`~recon.domain.errors.ReconError` that says what to do next.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING, Any

from recon.application.captures import record_hook_payload
from recon.application.collectors import declared_env
from recon.application.plan import Plan, build_plan, fan_out, plugin_warning
from recon.application.survey import EventCallback, Survey
from recon.domain.archives import ARCHIVE_ID_FORMAT
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
    Required,
    StatusDocument,
    SurveyDocument,
    TableError,
    TableStatus,
    TemplateInfo,
    TemplatesDocument,
    failure_kind,
)
from recon.domain.errors import ConfigError, ConflictError, NotFoundError, Problem, UsageError
from recon.domain.telemetry import QUERY, SURVEY

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from recon.application.collectors import SurveyCollectors
    from recon.domain.archives import ArchiveQuery, ArchiveStore, WriteState
    from recon.domain.clock import Clock
    from recon.domain.collector import Plugins
    from recon.domain.documents import ArchiveState, Capture
    from recon.domain.missions import ConfigFile, MissionStore
    from recon.domain.models import ReconConfig
    from recon.domain.telemetry import Telemetry

#: Builds a collector for each installed type a mission uses, reporting through the survey's
#: telemetry, with the types whose factory failed (which a survey reports and whose tables
#: fail with a config error), and what to close when the survey ends.
CollectorFactory = Callable[["Path", "ReconConfig", "Telemetry", "Deadline"], "SurveyCollectors"]

#: The telemetry for one operation: a survey passes the archive it writes, whose event log
#: is one of the sinks; an operation that writes no archive passes None.
TelemetryFactory = Callable[["Path | None"], "Telemetry"]

DEFAULT_TEMPLATE = "code-forensics"


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
    ) -> None:
        """``root_found_by`` is how the composition root found the project root:
        ``argument`` (the caller named it), ``repository`` (the nearest ancestor holding
        ``.git``) or ``working_directory``. Each survey records it."""
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

    def plan(self, name: str, *, config: Path | None = None, replace: bool = False) -> PlanDocument:
        """What a survey would do: every run with its effects, the files it would write, and
        what the runs need of this host. Raises ConfigError listing every problem.

        Writes nothing. Runs and fetches nothing either, unless a run's collector type
        declares a ``preflight``: that type's collector is then built, asked what each of its
        runs needs (an ``mcp`` type starts each stdio server a run names, and connects to each
        remote one, to list its tools), and closed; ``preflight`` says what was done, and
        ``requirements`` what was found."""
        _, plan, copy_to = self._prepare(name, config, replace)
        mission = self._missions.mission_dir(name)
        assert plan.config is not None  # _prepare raises on an invalid plan
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
            ),
            problems=[p.document() for p in plan.problems],
            warnings=[w.document() for w in plan.warnings + broken],
            skipped=[skipped.document() for skipped in plan.skipped],
            requirements=requirements,
            runnable=all(required.holds for required in requirements),
            preflight=said,
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
            mission, subset, self._telemetry(None), Deadline(self._clock)
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
        found here rather than by the first query."""
        source, plan, copy_to = self._prepare(name, config, replace)
        assert plan.config is not None  # _prepare raises on an invalid plan
        emit = on_event or (lambda event: None)
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
            telemetry = self._telemetry(archive)
            about = {"recon.mission": name, "recon.archive": archive.name}
            deadline = Deadline(self._clock)
            with telemetry.span(SURVEY, about) as span:
                built = self._build_collectors(mission, plan.config, telemetry, deadline)
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
                        config_sha256=hashlib.sha256(source.text).hexdigest(),
                        resolved=self._resolved(plan.config),
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
            return SurveyDocument(
                mission=name,
                archive=str(archive),
                status="incomplete" if failed_tables or not survey.finished else "ok",
                tables=tables,
                warnings=[w.document() for w in warnings + _dropped(telemetry)],
            )
        finally:
            self._archives.release(archive)

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
        )

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
        )

    def _archive_status(self, archive: Path) -> ArchiveStatus:
        meta = self._archives.read_meta(archive)
        return ArchiveStatus(
            id=archive.name,
            path=str(archive),
            state=_STATES[self._archives.state(archive)],
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

    def query(self, name: str, sql: str, *, archive: str | None = None) -> QueryRows:
        """Run ``sql`` against the latest archive, or the one named. ``state`` says whether
        the archive is incomplete, so its rows may be partial. A row is an object keyed by
        column, so a name the SQL gives twice is made unique, in ``columns`` and every row,
        with the suffix DuckDB gives it in a subquery (``a``, ``a_1``, then ``a_1_1`` on a
        further clash)."""
        path = self._archive(name, archive)
        telemetry = self._telemetry(None)
        with telemetry.span(QUERY, {"recon.mission": name, "recon.archive": path.name}) as span:
            result = self._query.execute(path, sql)
            span.set("recon.rows", len(result.rows))
        columns = _unique(result.columns)
        rows = [dict(zip(columns, row, strict=True)) for row in result.rows]
        return QueryRows(
            mission=name,
            archive=str(path),
            archive_id=path.name,
            state=_STATES[self._archives.state(path)],
            columns=columns,
            rows=rows,
            count=len(rows),
            warnings=[w.document() for w in result.warnings + _dropped(telemetry)],
        )

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


#: An archive's state as the documents say it: the store's ``complete`` is ``ok``, the word
#: every recon document has used for an archive with every table.
_STATES: dict[WriteState, ArchiveState] = {
    "writing": "writing",
    "complete": "ok",
    "incomplete": "incomplete",
}


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

"""Composition root: the only module that joins the use cases to concrete adapters.

Every adapter (subject, probe, sensor, environment) is resolved through matrix's registry by
the type URL the experiment names, so an installed extension adds one without an edit here;
so is every runtime a session, a model call or a judge runs on, at matrix's
``agent-runtime`` or ``model-runtime`` point. A run's observers are the ones
matrix's config names for ix, resolved through the same registry. Genre and kind
declarations are data, not components: they are files, found through the ``ix.genre`` and
``ix.kind`` entry points and a lab's own folders, and read, like the experiment and its
probes, through matrix's loader.

ix's settings (:class:`~ix.composition.settings.IxSettings`) are the ``ix:`` section of
matrix's config tiers, read once per root when first needed.

A source is an experiment file (or a directory holding ``experiment.yaml``), whose relative
paths resolve against its directory and whose registration is the file beside it; or an
:class:`~ix.domain.experiment.Experiment` built in memory, given a ``base`` and, to be
registered, a ``registration`` path. A run is kept in matrix's store under
``<experiment directory>/.ix/runs/<run id>/``, or under the settings' ``runs``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matrix
from matrix import (
    Config,
    MatrixConfig,
    MatrixError,
    Registry,
    agent_runtime_type_url,
    check_type_url,
    default_registry,
    load_config,
    model_runtime_type_url,
)

from ix.adapters._in.matrix_components import COMPOSITION, NEED
from ix.adapters._out.matrix_agents import AGENT_RUNTIME, MODEL_RUNTIME, Point, Sessions
from ix.adapters._out.matrix_documents import (
    STDIN_SOURCE,
    GenreFiles,
    KindFiles,
    read_experiment,
    read_probes,
)
from ix.adapters._out.matrix_errors import carried_over, converted
from ix.adapters._out.matrix_process import MatrixLauncher
from ix.adapters._out.matrix_runs import Runs as RunRecords
from ix.adapters._out.matrix_runs import execute
from ix.adapters._out.otel_tracing import OtelTracer
from ix.adapters._out.provenance import checkout, contents, installed
from ix.adapters._out.registration_file import RegistrationFile
from ix.application import experiments as use_cases
from ix.application import running
from ix.application import skill as skill_document
from ix.application.experiments import Examined, FolderProbes, Vocabulary
from ix.application.running import Kit
from ix.composition.extension import LAUNCHER, POINTS, RUNTIMES, SESSIONS
from ix.composition.settings import IxSettings
from ix.domain import schema
from ix.domain.errors import ConfigError, IxError, NotFoundError, UsageError
from ix.domain.experiment import Experiment
from ix.domain.genre import Genres, genres
from ix.domain.kind import KIND_FAMILIES, Kinds, kinds
from ix.domain.problem import InvalidExperimentError
from ix.domain.provenance import Provenance, RuntimeOrigin, named_files
from ix.domain.report import Report, Runs
from ix.domain.report import report as build_report
from ix.domain.report import summary as summarize

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterator, Mapping

    from matrix import Entry

    from ix.api import PathLike, Source
    from ix.domain.design import Design
    from ix.domain.plan import Plan
    from ix.domain.problem import Problem, Validation
    from ix.domain.registration import Deviations, Registered, Registration
    from ix.domain.run import Reading, RunRecord

try:
    VERSION = _installed_version("ix")
except PackageNotFoundError:  # running from a source tree that was never installed
    VERSION = "0+unknown"


#: Where an experiment's runs are kept, relative to the experiment's directory, unless the
#: settings say where every run is kept.
RUNS_DIRECTORY = ".ix/runs"

#: The distributions whose installed versions each run records.
RECORDED_VERSIONS = ("ix", "matrix", "hardline")


def type_url_problem(name: str) -> str | None:
    """Why ``name`` is not a type URL under the registry's grammar; None when it is one.
    The one grammar ix holds genre and kind names to."""
    try:
        check_type_url(name)
    except MatrixError as error:
        return error.message
    return None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _run_id(experiment: str) -> str:
    """``<experiment>-<UTC time>-<4 hex digits>``: sorts by time, and two runs started in the
    same second do not collide."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{experiment}-{stamp}-{secrets.token_hex(2)}"


def load_settings() -> Config[IxSettings]:
    """The ``matrix:`` and ``ix:`` sections of matrix's config tiers, merged and validated.
    Raises :class:`ConfigError` with every problem, each naming its key and file."""
    try:
        config: Config[IxSettings] = load_config(client_type=IxSettings, client_key="ix")
    except MatrixError as error:
        raise carried_over(
            "matrix's config for ix cannot be read",
            error,
            fix="fix each problem listed; `matrix config --tool ix --sources` shows which file "
            "set a value",
        ) from error
    return config


def _unsaid(record: logging.LogRecord) -> bool:
    """A filter that lets no record through."""
    return False


#: A runtime's name at each point, as its type URL; and what one is called in an error.
_RUNTIME_URLS: dict[str, Callable[[str], str]] = {
    AGENT_RUNTIME: agent_runtime_type_url,
    MODEL_RUNTIME: model_runtime_type_url,
}
_RUNTIME_NOUNS = {AGENT_RUNTIME: "agent runtime", MODEL_RUNTIME: "model runtime"}


class _Runtimes:
    """matrix's runtimes, built from the registry by name at its two points: an agent
    runtime is what a session and an agent judge run on, a model runtime what a model call
    and a model judge make their call on. Each is handed the ``models`` of matrix's config
    and the ``cwd`` it runs in, as it declares it needs them.

    ix says a runtime's ``warnings`` itself, once, in the plan and the report: while
    :meth:`listening`, those of each runtime built are kept. What a runtime logs on
    ``matrix.RUNTIME_LOGGER`` while ix builds it is therefore not let through, so a run of
    twenty sessions does not log the same warning twenty times, and ``--json`` keeps stderr
    to the error envelope."""

    def __init__(self, registry: Registry, models: Callable[[], dict[str, Any] | None]) -> None:
        self._registry = registry
        self._models = models
        self._heard: list[matrix.Problem] | None = None

    @contextmanager
    def listening(self) -> Iterator[list[matrix.Problem]]:
        """The warnings of the runtimes built inside the block, each once."""
        heard: list[matrix.Problem] = []
        self._heard = heard
        try:
            yield heard
        finally:
            self._heard = None

    def _entry(self, point: Point, name: str) -> Entry:
        try:
            return self._registry.entry(point, _RUNTIME_URLS[point](name))
        except MatrixError as error:
            raise carried_over(f"no {_RUNTIME_NOUNS[point]} {name!r}", error) from error

    def build(
        self, point: Point, name: str, config: Mapping[str, Any], *, cwd: str | None = None
    ) -> Any:
        url = self._entry(point, name).type_url
        quiet = logging.getLogger(matrix.RUNTIME_LOGGER)
        quiet.addFilter(_unsaid)
        try:
            runtime = self._registry.create(
                point, url, config, needs={"cwd": cwd, "models": self._models()}
            )
        finally:
            quiet.removeFilter(_unsaid)
        if self._heard is not None:
            for found in getattr(runtime, "warnings", ()) or ():
                if found not in self._heard:
                    self._heard.append(found)
        return runtime

    def stand_in(self, point: Point, name: str) -> bool:
        return self._entry(point, name).stand_in

    def takes(self, point: Point, name: str) -> frozenset[str] | None:
        try:
            config = self._entry(point, name).config
        except IxError:
            return None
        return frozenset() if config is None else frozenset(config.model_fields)


@dataclass(frozen=True)
class Telemetry:
    """What :meth:`Root.configure_telemetry` installed: the warnings a command's documents
    carry, and how to flush and stop it."""

    warnings: tuple[Problem, ...]
    _handle: matrix.Telemetry | None = None

    def shutdown(self) -> None:
        if self._handle is not None:
            self._handle.shutdown()


@dataclass(frozen=True)
class _Opened:
    """A source, examined, with the vocabulary it was judged against and its registration
    file (None for an experiment object given no ``registration``)."""

    examined: Examined
    vocabulary: Vocabulary
    registration: RegistrationFile | None
    #: The directory the experiment's relative paths resolve against, and its runs live in.
    base: str


class Root:
    """The wired experiment model. Build one over another registry to bind other adapters;
    :func:`root` is the default, over every installed ``matrix.extensions`` entry point.
    ``settings`` replaces what matrix's config tiers hold."""

    def __init__(
        self,
        genre_files: GenreFiles | None = None,
        kind_files: KindFiles | None = None,
        *,
        registry: Registry | None = None,
        settings: Config[IxSettings] | None = None,
        version: str = VERSION,
        clock: Callable[[], str] = _now,
        run_id: Callable[[str], str] = _run_id,
    ) -> None:
        self._genre_files = genre_files or GenreFiles()
        self._kind_files = kind_files or KindFiles()
        self._registry = default_registry() if registry is None else registry
        self._config = settings
        self._version = version
        self._clock = clock
        self._run_id = run_id
        self._tracer = OtelTracer(version)
        self._runtimes = _Runtimes(self._registry, lambda: self._settings().matrix.models)
        self._supplies = {
            LAUNCHER: MatrixLauncher(),
            SESSIONS: Sessions(),
            RUNTIMES: self._runtimes,
        }

    def _settings(self) -> Config[IxSettings]:
        if self._config is None:
            self._config = load_settings()
        return self._config

    # --- the adapters ---

    def _adapter(self, entry: Entry) -> Any:
        try:
            return self._registry.create(entry.point, entry.type_url, needs=self._supplies)
        except MatrixError as error:
            raise ConfigError(
                f"the adapter for {entry.type_url} ({entry.origin}) cannot be built: "
                f"{error.message}",
                fix=f"an adapter may need {', '.join(sorted(self._supplies))} and nothing "
                "else; fix or uninstall the extension that registered it",
                details={**error.details, "value": entry.type_url},
            ) from error

    def _built(self, kind: str) -> Any:
        """The adapter registered for ``kind`` (a type URL), or None when there is none or
        it cannot be built: running the experiment says why."""
        point = POINTS.get(schema.family_of(kind) or "")
        entries = self._registry.entries(point) if point else []
        entry = next((entry for entry in entries if entry.type_url == kind), None)
        if entry is None:
            return None
        try:
            return self._adapter(entry)
        except ConfigError:
            return None

    @property
    def _runnable(self) -> frozenset[str]:
        """Every kind (type URL) an adapter is registered for."""
        return frozenset(
            entry.type_url
            for family in KIND_FAMILIES
            for entry in self._registry.entries(POINTS[family])
        )

    def _kit(self, kinds: Collection[str], declared: Kinds | None = None) -> Kit:
        """The adapters a run of ``kinds`` (type URLs) uses, each built from the registry,
        with the declarations of those kinds."""

        def built(family: str) -> dict[str, Any]:
            return {
                entry.type_url: self._adapter(entry)
                for entry in self._registry.entries(POINTS[family])
                if entry.type_url in kinds
            }

        found = declared if declared is not None else self.kinds()
        return Kit(
            subjects=built("subject"),
            probes=built("probe"),
            sensors=built("sensor"),
            environments=built("environment"),
            clock_ns=time.time_ns,
            kinds={name: kind for name, kind in found.kinds.items() if name in kinds},
            tracer=self._tracer,
        )

    # --- what an experiment may name ---

    def genres(self, lab: PathLike | None = None) -> Genres:
        """The installed genres, plus those in ``<lab>/genres``."""
        files = self._genre_files.files(None if lab is None else os.fspath(lab))
        return genres(files, grammar=type_url_problem)

    def kinds(self, lab: PathLike | None = None) -> Kinds:
        """The installed kinds, plus those in ``<lab>/kinds``."""
        files = self._kind_files.files(None if lab is None else os.fspath(lab))
        found = kinds(files, grammar=type_url_problem)
        quarantined = tuple(
            f"{failure.extension} ({failure.origin}): {failure.error}"
            for failure in self._registry.failures
        )
        return found.model_copy(
            update={
                "runnable": tuple(sorted(self._runnable & set(found.kinds))),
                "quarantined": quarantined,
            }
        )

    def vocabulary(self, lab: PathLike | None = None) -> Vocabulary:
        return Vocabulary(
            genres=self.genres(lab),
            kinds=self.kinds(lab),
            adapters=self._runnable,
        )

    def skill(self, reference: str | None = None) -> str:
        return skill_document.render(
            self.genres(), self.kinds(), version=self._version, reference=reference
        )

    def configure_telemetry(self) -> Telemetry:
        """Install the OpenTelemetry SDK from the standard ``OTEL_*`` variables, through
        matrix, for an application's entry point (the ``ix`` command): a library never
        installs one. Its warnings (export asked for without matrix's ``otel`` extra) are
        what every document the command prints carries."""
        handle = matrix.configure_telemetry()
        return Telemetry(warnings=converted(handle.warnings), _handle=handle)

    # --- opening a source ---

    def _folder(self, experiment: Experiment, base: str) -> FolderProbes | None:
        """What the probes folder beside an experiment holds, when it writes no probes
        inline."""
        if experiment.probes:
            return None
        read = read_probes(base)
        probes = None if read.probes is None else tuple(probe.config() for probe in read.probes)
        return FolderProbes(location=read.location, probes=probes, problems=read.problems)

    def _open(
        self,
        source: Source,
        *,
        base: PathLike | None = None,
        registration: PathLike | None = None,
    ) -> _Opened:
        record = None if registration is None else RegistrationFile(os.fspath(registration))
        if isinstance(source, Experiment):
            vocabulary = self.vocabulary(base)
            directory = os.fspath(base) if base is not None else os.getcwd()
            examined = self._examine(source, vocabulary, base=directory)
            return _Opened(examined, vocabulary, record, directory)
        read = read_experiment(os.fspath(source))
        vocabulary = self.vocabulary(read.base)
        if read.experiment is None:
            examined = use_cases.unreadable(read.source, list(read.problems))
        else:
            examined = self._examine(
                read.experiment, vocabulary, base=read.base, source=read.source
            )
        beside = None if read.source == STDIN_SOURCE else RegistrationFile.beside(read.source)
        return _Opened(examined, vocabulary, record or beside, read.base)

    def _examine(
        self, experiment: Experiment, vocabulary: Vocabulary, *, base: str, source: str = ""
    ) -> Examined:
        """The experiment judged against the vocabulary, and by the adapters it would run
        on: what only an adapter can tell is wrong is a problem too."""
        examined = use_cases.examine(
            experiment,
            vocabulary,
            base=base,
            source=source,
            folder=self._folder(experiment, base),
        )
        absolute = str(Path(base).resolve())
        return use_cases.reviewed(examined, vocabulary, self._built, base=absolute)

    def _digested(self, examined: Examined, vocabulary: Vocabulary, base: str) -> Examined:
        """``examined`` with what each file and folder its subjects and sensors name holds:
        what registration freezes and a run is held to."""
        if examined.experiment is None:
            return examined
        named = named_files(examined.experiment, vocabulary.kinds)
        return replace(examined, contents=contents(named, str(Path(base).resolve())))

    def _provenance(self, directory: str) -> Provenance:
        """Where a run in ``directory`` runs from: the commit, the installed versions of
        matrix, ix and hardline, and every runtime installed at matrix's two runtime points
        with the distribution that registered it."""
        runtimes = tuple(
            RuntimeOrigin(
                type_url=entry.type_url,
                distribution=entry.origin,
                version="" if entry.origin == "(direct)" else installed(entry.origin),
            )
            for point in (AGENT_RUNTIME, MODEL_RUNTIME)
            for entry in self._registry.entries(point)
        )
        return checkout(directory).model_copy(
            update={
                "versions": {name: installed(name) for name in RECORDED_VERSIONS},
                "runtimes": runtimes,
            }
        )

    def _stored(self, opened: _Opened) -> Registration | None:
        if opened.registration is None:
            return None
        return use_cases.stored_registration(
            opened.registration.read(), opened.registration.location
        )

    # --- the use cases ---

    def load(self, path: PathLike) -> Experiment:
        examined = self._open(path).examined
        if examined.experiment is None:
            raise InvalidExperimentError(
                f"{examined.source} is not an experiment file "
                f"({len(examined.problems)} problem(s))",
                examined.problems,
                fix=f"ix validate {examined.source}",
                complete=examined.complete,
            )
        return examined.experiment

    def validate(self, source: Source, *, base: PathLike | None = None) -> Validation:
        return use_cases.validate(self._open(source, base=base).examined)

    def design(self, source: Source, *, base: PathLike | None = None) -> Design:
        return use_cases.design(self._open(source, base=base).examined)

    def plan(
        self,
        source: Source,
        *,
        base: PathLike | None = None,
        registration: PathLike | None = None,
    ) -> Plan:
        with self._tracer.trace("ix.plan") as span:
            opened = self._open(source, base=base, registration=registration)
            # Check the experiment before the record: an invalid experiment is the first
            # thing to say, whatever state its registration is in.
            experiment = use_cases.valid(opened.examined, "planned").experiment
            unstartable, cautions = self._preflight(experiment, str(Path(opened.base).resolve()))
            made = use_cases.make_plan(
                self._digested(opened.examined, opened.vocabulary, opened.base),
                opened.vocabulary,
                self._stored(opened),
                location=opened.registration.location if opened.registration else "",
                unstartable=unstartable,
                earlier=self._earlier(opened.base, experiment.name),
            )
            made = made.model_copy(update={"warnings": (*made.warnings, *cautions)})
            span.set(
                **{
                    "ix.experiment": made.experiment,
                    "ix.plan.digest": made.digest,
                    "ix.plan.trials": made.trials.total,
                    "ix.plan.runnable": made.runnable,
                }
            )
            return made

    def _earlier(self, base: str, experiment: str) -> Iterator[RunRecord]:
        """The runs of ``experiment`` kept beside it, latest first, each read when asked."""
        records = self._records(str(Path(base).resolve()))
        for summary in reversed(records.summaries()):
            if summary.experiment == experiment and summary.status == "completed":
                yield records.record(summary.run)

    def register(
        self,
        source: Source,
        *,
        base: PathLike | None = None,
        registration: PathLike | None = None,
        amend: str | None = None,
    ) -> Registered:
        opened = self._open(source, base=base, registration=registration)
        if opened.registration is None:
            raise ConfigError(
                "an experiment built in memory or read from standard input has no file to "
                "register beside",
                fix="register the experiment's file; from the library, pass "
                "registration=<path of the registration file>",
            )
        if amend is not None and not amend.strip():
            raise ConfigError(
                "amending a registration needs a reason",
                fix='ix register <path> --amend --reason "<why the design changed>"',
                details={"path": "--reason", "value": amend},
            )
        use_cases.require_valid(opened.examined, "registered")
        outcome = use_cases.register(
            self._digested(opened.examined, opened.vocabulary, opened.base),
            opened.vocabulary,
            self._stored(opened),
            location=opened.registration.location,
            now=self._clock(),
            version=self._version,
            amend_reason=amend,
        )
        if outcome.outcome != "unchanged":
            opened.registration.write(outcome.registration.document())
        return outcome

    def deviations(
        self,
        source: Source,
        *,
        base: PathLike | None = None,
        registration: PathLike | None = None,
    ) -> Deviations:
        opened = self._open(source, base=base, registration=registration)
        use_cases.require_valid(opened.examined, "compared with its registration")
        return use_cases.find_deviations(
            self._digested(opened.examined, opened.vocabulary, opened.base),
            opened.vocabulary,
            self._stored(opened),
            location=opened.registration.location if opened.registration else "",
        )

    # --- running ---

    def _preflight(
        self, experiment: Experiment, base: str
    ) -> tuple[tuple[str, ...], tuple[Problem, ...]]:
        """What the subjects' adapters can tell before anything starts: why a subject cannot
        start here (``subjects.<name>: <why>``), and what the runtimes they would build warn
        of (a permissive ``permission_mode``), each at
        ``subjects.<name>.runtime_options.<option>``: a subject's runtime options are its
        runtime's config."""
        kinds = {schema.url("subject", subject.kind) for subject in experiment.subjects.values()}
        subjects = self._kit(kinds).subjects
        reasons: list[str] = []
        cautions: list[Problem] = []
        for name, subject in experiment.subjects.items():
            alone = experiment.model_copy(update={"subjects": {name: subject}})
            with self._runtimes.listening() as heard:
                reasons += running.unstartable(alone, subjects, base)
            cautions += [
                found.model_copy(update={"path": f"subjects.{name}.runtime_options.{found.path}"})
                for found in converted(heard)
            ]
        return tuple(reasons), tuple(cautions)

    def _records(self, base: str) -> RunRecords:
        """The runs of the experiment in the directory ``base``: under the settings' ``runs``,
        or ``.ix/runs`` beside it."""
        kept = self._settings().client.runs
        return RunRecords(kept if kept else str(Path(base).resolve() / RUNS_DIRECTORY))

    def run(
        self,
        source: Source,
        *,
        exploratory: bool = False,
        dry_run: bool = False,
        repeats: int | None = None,
        base: PathLike | None = None,
        registration: PathLike | None = None,
        on_event: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> Report:
        """Run the experiment and report what it found.

        Refuses before anything starts when the experiment is invalid, names a kind with no
        adapter, or is unregistered and the run is neither ``exploratory`` nor a
        ``dry_run``; and ``repeats`` on a run that is neither, since a gated run is held to
        the design its registration froze. Once it has started, whatever happens is in the
        record: a run that was stopped or that failed still returns its report, which says
        so.
        """
        if repeats is not None and not (exploratory or dry_run):
            raise UsageError(
                f"--repeats {repeats} changes the design, and a run that gives a verdict is "
                "held to the design its registration froze",
                fix="pilot with --exploratory --repeats N (or --dry-run --repeats N); to "
                "change the repeats a verdict rests on, edit the file and "
                '`ix register <path> --amend --reason "<why>"`',
                details={"path": "--repeats", "value": repeats},
            )
        opened = self._open(source, base=base, registration=registration)
        use_cases.require_valid(opened.examined, "run")
        directory = str(Path(opened.base).resolve())
        records = self._records(directory)
        examined, stored, notes = opened.examined, self._stored(opened), None
        overridden: tuple[str, ...] = ()
        if repeats is not None:
            checked = use_cases.valid(examined, "run")
            written = checked.design.repeats
            examined = self._examine(
                checked.experiment.model_copy(update={"repeats": repeats}),
                opened.vocabulary,
                base=opened.base,
                source=examined.source,
            )
            use_cases.require_valid(examined, f"run with --repeats {repeats}")
            overridden = (f"repeats: {repeats} in place of {written} (--repeats: a pilot)",)
        if dry_run:
            checked = use_cases.valid(examined, "dry run")
            stood, notes = running.stand_in(checked.experiment, checked.probes, self._built)
            examined = self._examine(
                stood, opened.vocabulary, base=opened.base, source=examined.source
            )
            use_cases.require_valid(examined, "dry run on its stand-ins")
        prepared = running.prepare(
            self._digested(examined, opened.vocabulary, directory),
            opened.vocabulary,
            stored,
            run=self._run_id(examined.name),
            base=directory,
            now=self._clock(),
            version=self._version,
            exploratory=exploratory,
            dry_run=notes,
            overridden=overridden,
        )
        kit = self._kit(prepared.kinds)
        running.check_startable(prepared.experiment, kit, directory)
        _, cautions = self._preflight(prepared.experiment, directory)
        manifest = prepared.manifest.model_copy(
            update={"warnings": cautions, "provenance": self._provenance(directory)}
        )
        # A stop request (Ctrl-C, SIGTERM) arrives here as the cancellation it is, after
        # matrix stopped the trial in flight, released what was started and finished the
        # record. The report then says how far the run got.
        with suppress(KeyboardInterrupt, asyncio.CancelledError):
            asyncio.run(
                execute(
                    root=records.root,
                    registry=self._registry,
                    config=MatrixConfig.model_validate(
                        {**COMPOSITION, "observers": self._settings().matrix.observers}
                    ),
                    needs={NEED: kit},
                    manifest=manifest,
                    trials=prepared.trials,
                    on_event=on_event,
                    tracer=self._tracer,
                )
            )
        return self._report(records, manifest.run)

    def _report(self, records: RunRecords, run: str) -> Report:
        record = records.record(run)
        return build_report(record, readings=self._derived_readings(record))

    def _derived_readings(self, record: RunRecord) -> list[Reading] | None:
        """The readings of a run that ended before its sensors read its interactions, taken
        now from the stored tables and never written back. None when there are none to take,
        or a sensor the run used has no adapter here."""
        if not record.interactions or record.readings or record.status == "running":
            return None
        setup = running.setup_of(record.manifest)
        sensors = {selected.kind for selected in setup.design.sensors}
        if not sensors <= self._runnable:
            return None
        derived = asyncio.run(
            running.sense(
                setup,
                self._kit(sensors),
                trials=record.trials,
                interactions=record.interactions,
            )
        )
        return derived or None

    def _locate(self, target: PathLike) -> tuple[RunRecords, str | None, str]:
        """(records, run id or None, experiment name) for a run directory or an experiment."""
        path = Path(os.fspath(target))
        found = RunRecords.at(str(path)) if path.is_dir() else None
        if found is not None:
            records, run = found
            return records, run, ""
        opened = self._open(target)
        return self._records(opened.base), None, opened.examined.name

    def report(self, target: PathLike) -> Report:
        """The report of a run: ``target`` is a run's directory, or an experiment (its file
        or directory), meaning its latest run."""
        records, run, experiment = self._locate(target)
        if run is None:
            found = [s for s in records.summaries() if not experiment or s.experiment == experiment]
            if not found:
                raise NotFoundError(
                    f"{os.fspath(target)} has no runs in {records.root}",
                    fix=f"ix run {os.fspath(target)}",
                    details={"path": os.fspath(target)},
                )
            run = found[-1].run
        return self._report(records, run)

    def runs(self, target: PathLike | None = None) -> Runs:
        """The runs kept beside an experiment, oldest first. ``target`` is the experiment
        (its file or directory); with none, the current directory's runs of any experiment."""
        if target is None:
            records, experiment = self._records(os.getcwd()), ""
        else:
            records, _, experiment = self._locate(target)
        found = []
        for listed in records.summaries():
            if experiment and listed.experiment != experiment:
                continue
            try:
                found.append(summarize(build_report(records.record(listed.run))))
            except IxError:
                found.append(listed)  # written by another ix: its id, state and counts only
        return Runs(location=records.root, runs=tuple(found))


_DEFAULT: Root | None = None


def root() -> Root:
    """The default wiring: the installed genres and kinds, every adapter in
    matrix's registry, and the registration file beside the experiment."""
    global _DEFAULT  # noqa: PLW0603
    if _DEFAULT is None:
        _DEFAULT = Root()
    return _DEFAULT

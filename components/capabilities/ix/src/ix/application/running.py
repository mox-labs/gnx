"""Carrying out a run: the manifest it is held to, one trial, all the trials, the readings.

Written over the ports of :mod:`ix.domain.ports` and a :class:`~ix.domain.ports.Scope`, so it
names no host. :func:`prepare` decides before anything starts whether the experiment may run;
:func:`invoke` carries out one trial; :func:`carry_out` does that for every trial in order;
:func:`sense` turns interactions into readings.

A failed trial never raises out of :func:`invoke` and never stops the run: it is an
interaction carrying a failure with an owner (see :mod:`ix.domain.run`).

**Spans.** When the scope is :class:`~ix.domain.ports.Traced`, the environment's bring-up
is a span ``ix.environment``, each trial a span ``ix.trial`` and each sensor reading one
interaction a span ``ix.sensor``, which records each metric it read
(:meth:`~ix.domain.ports.Span.evaluated`) and a trial's failure
(:meth:`~ix.domain.ports.Span.failed`). Which telemetry convention names them is the
tracer's to say. The agent sessions inside a trial are matrix's own spans.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from contextlib import AsyncExitStack, contextmanager, suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeGuard

from pydantic import ValidationError

from ix.application.experiments import standing, valid
from ix.domain import schema
from ix.domain.analysis import ANALYSES, analysis_unsupported
from ix.domain.design import Design, kinds_in_use, missing_adapters
from ix.domain.errors import ConfigError, UnregisteredError
from ix.domain.experiment import Experiment, ProbeConfig, SubjectConfig
from ix.domain.kind import fill_options, placed, placeholders
from ix.domain.ports import Liveness, Recorded, Sensed, StandIn, Traced
from ix.domain.registration import deviations
from ix.domain.run import (
    Failure,
    HarnessError,
    Interaction,
    Reading,
    RunManifest,
    Trial,
    TrialError,
    bound,
    schedule,
    schedule_digest,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from ix.application.experiments import Examined, Vocabulary
    from ix.domain.experiment import ProbeSet
    from ix.domain.kind import Kind, Metric
    from ix.domain.plan import Required
    from ix.domain.ports import (
        Environment,
        Measured,
        Probe,
        Scope,
        Sensor,
        Span,
        Subject,
    )
    from ix.domain.registration import Registration
    from ix.domain.run import Bindings, Target

#: Events a run emits through its scope. Telemetry: progress is read from these, results
#: never are.
ENVIRONMENT_UP = "ix.environment.up"
TRIAL_START = "ix.trial.start"
TRIAL_END = "ix.trial.end"
SENSE_END = "ix.sense.end"


@dataclass(frozen=True)
class Kit:
    """The adapters a run may use, each by the type URL of the kind it carries out, the
    clock trials are timed with (nanoseconds since the epoch), and the declarations of the
    kinds in use (to place a sensor's ``path`` options)."""

    subjects: Mapping[str, Subject]
    probes: Mapping[str, Probe]
    sensors: Mapping[str, Sensor]
    environments: Mapping[str, Environment]
    clock_ns: Callable[[], int]
    kinds: Mapping[str, Kind] = field(default_factory=dict)
    #: Spans of ix's own work; None traces nothing.
    tracer: Traced | None = None


class _Quiet:
    """A span that records nothing: what a scope that is not :class:`Traced` gives."""

    def set(self, **attributes: Any) -> None:
        return None

    def failed(self, reason: str) -> None:
        return None

    def evaluated(
        self,
        metric: str,
        *,
        score: float | None = None,
        label: str | None = None,
        failure: str | None = None,
    ) -> None:
        return None


def _recorded(scope: Scope | None) -> tuple[int, ...]:
    """The session rows recorded on the scope's behalf since it last handed a row over; none
    when it is not :class:`Recorded`."""
    return scope.sessions() if isinstance(scope, Recorded) else ()


def _since(before: tuple[int, ...], scope: Scope | None) -> tuple[int, ...]:
    """The session rows recorded on the scope's behalf since it held ``before``."""
    held = set(before)
    return tuple(row for row in _recorded(scope) if row not in held)


@contextmanager
def _span(scope: Scope | None, name: str, **attributes: Any) -> Iterator[Span]:
    """A span of ``name`` when ``scope`` traces; a quiet one when it does not."""
    if isinstance(scope, Traced):
        with scope.trace(name, **attributes) as span:
            yield span
    else:
        yield _Quiet()


@dataclass(frozen=True)
class Prepared:
    """A run that may start: its experiment, design and probes, its manifest, the trials it
    will carry out and the kinds (type URLs) it uses."""

    experiment: Experiment
    manifest: RunManifest
    trials: tuple[Trial, ...]
    kinds: tuple[str, ...]
    design: Design
    probes: ProbeSet


def prepare(
    examined: Examined,
    vocabulary: Vocabulary,
    stored: Registration | None,
    *,
    run: str,
    base: str,
    now: str,
    version: str,
    exploratory: bool = False,
    dry_run: tuple[str, ...] | None = None,
    overridden: tuple[str, ...] = (),
) -> Prepared:
    """Decide whether the experiment may run, and write down what the run is held to.

    ``dry_run`` is given for an experiment :func:`stand_in` rewrote: the notes of what it
    set aside. Such a run is exploratory and held to no registration. ``overridden`` notes
    what the caller changed from the experiment as written (a pilot's repeats), for an
    exploratory run or a dry one only.

    Refuses, before anything starts: an invalid experiment; a kind in use that has no
    adapter, or an analysis this ix does not have (:class:`ConfigError`); and an experiment
    with no registration, unless the run is ``exploratory`` (:class:`UnregisteredError`).

    A registered experiment that has changed since is not refused, and is not held to its
    registration either: the run proceeds ungated, as an exploratory one does, the
    deviations go into the manifest, and the report gives what it found as an indication,
    never a gate result. Amending the registration with a reason makes the next run gated.
    """
    checked = valid(examined, "run")
    experiment, design, probes = checked.experiment, checked.design, checked.probes
    missing = missing_adapters(experiment, design, probes, vocabulary.adapters or frozenset())
    if missing:
        broken = vocabulary.kinds.unbuildable
        named = ", ".join(
            f"{kind} ({where})"
            + (f", only one that cannot be built: {broken[kind]}" if kind in broken else "")
            for where, kind in missing
        )
        raise ConfigError(
            f"{examined.source} cannot be run here: this ix has no adapter for {named}",
            fix="`ix kinds` marks the kinds that can be run, and says why an adapter cannot "
            "be built; the experiment is still valid to plan and register",
        )
    unsupported = analysis_unsupported(design.analysis)
    if unsupported is not None:
        raise ConfigError(
            f"{examined.source} cannot be run here: {unsupported}",
            fix="use a genre whose analysis is one of: " + ", ".join(sorted(ANALYSES)),
        )
    made, frozen = standing(examined, vocabulary, now=now, version=version)
    if dry_run is not None:
        stored, exploratory = None, True
    if overridden and not exploratory:
        raise ConfigError(
            f"{examined.source} was changed for this run ({'; '.join(overridden)}), and a run "
            "that gives a verdict is held to the design as registered",
            fix="run it exploratory or dry, or change the file and amend its registration",
        )
    if stored is None and not exploratory:
        raise UnregisteredError(
            f"{examined.source} is not registered, so a run of it could not be held to a "
            "design decided beforehand",
            fix=f"ix register {examined.source}, then run it; or pass --exploratory for a "
            "run that gives no verdict",
        )
    trials = schedule(experiment, design, probes)
    changed = () if stored is None else deviations(stored, frozen)
    return Prepared(
        experiment=experiment,
        manifest=RunManifest(
            run=run,
            experiment=experiment.name,
            declared=experiment.document(),
            base=base,
            experiment_digest=experiment.digest(),
            plan_digest=made.digest,
            schedule_digest=schedule_digest(trials),
            design=design.document(),
            probes=[probe.document() for probe in probes.probes],
            probes_digest=probes.digest,
            contents=frozen.contents,
            registration=None if stored is None else stored.document(),
            deviations=changed,
            gated=stored is not None and not exploratory and not changed,
            exploratory=exploratory,
            dry_run=dry_run is not None,
            stood_in=dry_run or (),
            overridden=overridden,
            started_at=now,
            ix_version=version,
        ),
        trials=trials,
        kinds=tuple(kind for _, kind in kinds_in_use(experiment, design, probes)),
        design=design,
        probes=probes,
    )


def stand_in(
    experiment: Experiment, probes: ProbeSet, adapter: Callable[[str], object]
) -> tuple[Experiment, tuple[str, ...]]:
    """The experiment for a dry run: every subject on its adapter's stand-in (a
    :class:`~ix.domain.ports.StandIn`), and every probe and named sensor that has one, with
    a note for each thing set aside. ``adapter(kind)`` gives the adapter of a kind (its type
    URL) or None. Raises :class:`ConfigError` naming each subject that has no stand-in: a
    dry run never runs a subject for real."""
    notes: list[str] = []

    def stood(path: str, built: object, options: Mapping[str, Any]) -> dict[str, Any] | None:
        if not isinstance(built, StandIn):
            return None
        done = built.stand_in(options)
        notes.extend(f"{path}: {note}" for note in done.notes)
        return dict(done.options)

    subjects: dict[str, Any] = {}
    missing: list[str] = []
    for name, subject in experiment.subjects.items():
        options = stood(
            f"subjects.{name}", adapter(schema.url("subject", subject.kind)), subject.options
        )
        if options is None:
            missing.append(f"subjects.{name} (kind {subject.kind})")
            continue
        subjects[name] = subject.model_copy(update={"options": options})
    if missing:
        raise ConfigError(
            f"a dry run puts every subject on a stand-in, and {', '.join(missing)} "
            f"{'has' if len(missing) == 1 else 'have'} none",
            fix="dry-run an experiment of agent sessions; run this one with --exploratory to "
            "try it for real",
        )
    inline = bool(experiment.probes)
    rewritten = []
    for index, probe in enumerate(probes.probes):
        at = f"probes.{index if inline else probe.id}"
        options = stood(at, adapter(schema.url("probe", probe.kind)), probe.options)
        rewritten.append(
            probe if options is None else probe.model_copy(update={"options": options})
        )
    sensors = []
    for index, sensor in enumerate(experiment.sensors):
        options = stood(
            f"sensors.{index}", adapter(schema.url("sensor", sensor.kind)), sensor.options
        )
        sensors.append(
            sensor if options is None else sensor.model_copy(update={"options": options})
        )
    environment = experiment.environment
    kept = stood(
        "environment", adapter(schema.url("environment", environment.kind)), environment.options
    )
    if kept is None:
        # An environment with no stand-in is brought up for real by a dry run, which says so.
        notes.append(f"environment: {environment.kind} is brought up for real; it has no stand-in")
    changes: dict[str, Any] = {"subjects": subjects, "sensors": tuple(sensors)}
    if kept is not None:
        changes["environment"] = environment.model_copy(update={"options": kept})
    if tuple(rewritten) != probes.probes:
        # Probes read from files are carried inline once a stand-in changes one.
        changes["probes"] = tuple(rewritten)
    return experiment.model_copy(update=changes), tuple(notes)


def check_startable(requirements: Sequence[Required]) -> None:
    """Refuse, before anything starts, a run whose requirements do not all hold here.
    Raises :class:`ConfigError` naming each, with what was found and its fix."""
    unmet = [required.text() for required in requirements if not required.holds]
    if unmet:
        raise ConfigError(
            "the experiment cannot start on this host: " + "; ".join(unmet),
            fix="make each requirement named hold, or correct the entry that needs it; `ix "
            "plan` lists every requirement with whether it holds",
        )


@dataclass(frozen=True)
class Setup:
    """A manifest read back into the records the run path works with."""

    manifest: RunManifest
    experiment: Experiment
    design: Design
    probes: Mapping[str, ProbeConfig]

    @property
    def base(self) -> str:
        return self.manifest.base


def setup_of(manifest: RunManifest) -> Setup:
    """The experiment, design and probes a manifest holds. Raises :class:`ConfigError` when
    this ix cannot read them (the manifest was written by another)."""
    try:
        probes = [ProbeConfig.model_validate(probe) for probe in manifest.probes]
        return Setup(
            manifest=manifest,
            experiment=Experiment.model_validate(manifest.declared),
            design=Design.model_validate(manifest.design),
            probes={probe.id: probe for probe in probes},
        )
    except ValidationError as error:
        raise ConfigError(
            f"run {manifest.run} cannot be read by this ix ({error.error_count()} "
            f"problem(s)); it was written by ix {manifest.ix_version or '(unknown)'}",
            fix="read the run with the ix that wrote it",
        ) from error


def _failure(error: BaseException) -> Failure:
    if isinstance(error, TrialError):
        return Failure(owner=error.owner, reason=error.reason, message=str(error))
    return Failure(owner="harness", reason="error", message=f"{type(error).__name__}: {error}")


def _cause(during: Failure | None, at_release: Failure) -> Failure:
    """The failure a trial is recorded with, given one found when its subject was released.

    A subject that had died by then is the cause of whatever the harness reported during
    the probe (a load tool that got no answer). Otherwise the first failure stands.
    """
    if during is None or (during.owner == "harness" and at_release.owner == "subject"):
        return at_release
    return during


async def bring_up(setup: Setup, kit: Kit, scope: Scope) -> Bindings:
    """Bring the experiment's environment up, once for the run, inside a span
    ``ix.environment`` when the scope traces. It comes down when the run ends, through what
    the provider deferred."""
    environment = setup.experiment.environment
    provider = kit.environments[schema.url("environment", environment.kind)]
    attributes = {"ix.run.id": setup.manifest.run, "ix.environment.kind": environment.kind}
    with _span(scope, "ix.environment", **attributes) as span:
        try:
            bindings = await provider.up(environment, base=setup.base, scope=scope)
        except TrialError as error:
            span.failed(error.reason)
            raise
        span.set(**{"ix.environment.services": sorted(bindings)})
    scope.emit(ENVIRONMENT_UP, kind=environment.kind, services=sorted(bindings))
    return bindings


async def _apply(
    kind: Probe, options: Mapping[str, Any], target: Target, seed: int, scope: Scope
) -> dict[str, dict[str, Any]]:
    """Apply a probe under its budget, raced against the stop flag. Raises
    :class:`HarnessError` (``timeout``, ``cancelled``) when the probe does not finish."""
    budget_s = kind.budget_s(options)
    applying = asyncio.ensure_future(kind.apply(options, target, seed=seed))
    stopped = asyncio.ensure_future(scope.stopping.wait())
    try:
        await asyncio.wait(
            {applying, stopped}, timeout=budget_s, return_when=asyncio.FIRST_COMPLETED
        )
        if applying.done():
            return {facet: dict(seen) for facet, seen in applying.result().items()}
        if stopped.done():
            raise HarnessError("cancelled", "the run was asked to stop mid-trial")
        raise HarnessError("timeout", f"the probe overran its budget of {budget_s}s")
    finally:
        for task in (applying, stopped):
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task


@dataclass(frozen=True)
class _Adapters:
    """The adapters one trial uses, looked up before it starts: a lookup that fails is a
    defect in ix, and fails the run rather than the trial."""

    subject: Subject
    probe: Probe
    sensors: tuple[tuple[str, Sensor, Mapping[str, Any]], ...]


def _filled(subject: SubjectConfig, values: Mapping[str, Any], kit: Kit) -> dict[str, Any]:
    """A subject's options filled with ``values``, each escape as its kind's options say
    (:func:`~ix.domain.kind.fill_options`)."""
    return fill_options(subject.options, values, kit.kinds.get(schema.url("subject", subject.kind)))


def _adapters(trial: Trial, setup: Setup, kit: Kit) -> _Adapters:
    subject = setup.experiment.subjects[trial.subject]
    probe = setup.probes[trial.probe]
    return _Adapters(
        subject=kit.subjects[schema.url("subject", subject.kind)],
        probe=kit.probes[schema.url("probe", probe.kind)],
        sensors=tuple(
            (selected.kind, kit.sensors[selected.kind], selected.options)
            for selected in setup.design.sensors
        ),
    )


async def _watch(
    stack: AsyncExitStack, adapters: _Adapters, target: Target
) -> dict[str, dict[str, Any]]:
    """Begin every sensor that must watch, on ``stack``; what each yields, by its kind."""
    watching: dict[str, dict[str, Any]] = {}
    for kind, sensor, options in adapters.sensors:
        watch = sensor.watch(target, options)
        if watch is not None:
            watching[kind] = await stack.enter_async_context(watch)
    return watching


async def invoke(
    trial: Trial,
    setup: Setup,
    kit: Kit,
    scope: Scope,
    *,
    hold: list[AsyncExitStack] | None = None,
    bindings: Bindings | None = None,
) -> Interaction:
    """Carry out one trial and say what happened. A failure of an adapter becomes the
    trial's failure; a defect in ix itself raises.

    The subject's options are filled from the trial's cell and the environment's
    ``bindings`` (``{db.url}``); the probe's from those, the subject's options and what the
    subject offers.

    Order: the subject is reached, then each sensor that must watch begins, then the probe
    is applied; the watchers stop, then the subject is released. The interaction names the
    session rows recorded meanwhile, when the scope is :class:`Recorded`. ``hold`` holds the
    stack of whatever is open from the moment the trial starts until that stack has been
    closed, so that a caller whose trial was cancelled mid-flight can still release it.
    """
    started = kit.clock_ns()
    before = _recorded(scope)
    adapters = _adapters(trial, setup, kit)
    subject = setup.experiment.subjects[trial.subject]
    services = bound(bindings or {})
    filled = subject.model_copy(
        update={"options": _filled(subject, {**services, **trial.cell}, kit)}
    )
    probe = setup.probes[trial.probe]
    probe_kind = kit.kinds.get(schema.url("probe", probe.kind))
    observations: dict[str, dict[str, Any]] = {}
    watching: dict[str, dict[str, Any]] = {}
    failure: Failure | None = None
    simulated = False

    stack = AsyncExitStack()
    if hold is not None:
        hold.append(stack)
    try:
        target = await stack.enter_async_context(
            adapters.subject.reach(trial.subject, filled, base=setup.base)
        )
        simulated = target.simulated
        watching = await _watch(stack, adapters, target)
    except Exception as error:
        failure = _failure(error)
    if failure is None:
        known = {**services, **filled.options, **target.facets, **trial.cell}
        try:
            observations = await _apply(
                adapters.probe,
                fill_options(probe.options, known, probe_kind),
                target,
                trial.seed,
                scope,
            )
        except Exception as error:
            failure = _failure(error)
    # Watchers exit before the subject is released: the stack unwinds in reverse.
    try:
        await stack.aclose()
    except Exception as error:
        failure = _cause(failure, _failure(error))
    # Only a closed stack leaves ``hold``: one abandoned by a cancellation stays for the
    # caller to release.
    if hold is not None:
        hold.remove(stack)

    return Interaction(
        seq=trial.seq,
        subject=trial.subject,
        role=trial.role,
        probe=trial.probe,
        started_ns=started,
        duration_ns=kit.clock_ns() - started,
        observations=observations if failure is None else {},
        watched={kind: dict(seen) for kind, seen in watching.items()},
        failure=failure,
        simulated=simulated,
        sessions=_since(before, scope),
    )


def _environment(setup: Setup, kit: Kit) -> Environment:
    return kit.environments[schema.url("environment", setup.experiment.environment.kind)]


def _trial_attributes(trial: Trial, setup: Setup) -> dict[str, Any]:
    return {
        "ix.run.id": setup.manifest.run,
        "ix.experiment": setup.manifest.experiment,
        "ix.trial.seq": trial.seq,
        "ix.subject": trial.subject,
        "ix.role": trial.role,
        "ix.probe": trial.probe,
        "ix.repeat": trial.repeat,
    }


async def carry_out(
    setup: Setup,
    kit: Kit,
    *,
    trials: Sequence[Trial],
    bindings: Bindings,
    scope: Scope,
    land: Callable[[Interaction], None],
) -> int:
    """Carry out ``trials`` in order, one at a time, handing each interaction to ``land`` as
    it finishes. Returns how many were carried out.

    Stops between trials when the scope says to; a trial in flight then ends as a harness
    failure (``cancelled``) and is landed like any other. After each trial it asks the
    environment whether it is still up (when its provider is a :class:`Liveness`): when it
    is not, that trial is landed as a harness failure (``environment_lost``) and
    :class:`HarnessError` ends the run.
    """
    environment = _environment(setup, kit)
    unbound = _unbound(setup, kit, bindings)
    if unbound:
        raise HarnessError(
            "unbound",
            f"the environment came up without {', '.join(unbound)}, which the experiment "
            f"names; it bound {', '.join(sorted(bound(bindings))) or 'nothing'}",
        )
    open_now: list[AsyncExitStack] = []

    async def release_open() -> None:
        for stack in list(open_now):
            await stack.aclose()

    scope.defer(release_open)  # the backstop, for a trial abandoned mid-flight
    done = 0
    for trial in trials:
        if scope.stopping.is_set():
            break
        scope.emit(
            TRIAL_START,
            seq=trial.seq,
            total=len(trials),
            subject=trial.subject,
            role=trial.role,
            probe=trial.probe,
            cell=dict(trial.cell),
        )
        with _span(scope, "ix.trial", **_trial_attributes(trial, setup)) as span:
            interaction = await invoke(trial, setup, kit, scope, hold=open_now, bindings=bindings)
            lost = await environment.lost(bindings) if isinstance(environment, Liveness) else None
            if lost is not None:
                interaction = interaction.model_copy(
                    update={
                        "observations": {},
                        "failure": Failure(
                            owner="harness", reason="environment_lost", message=lost
                        ),
                    }
                )
            span.set(**_trial_outcome(interaction))
            if interaction.failure is not None:
                span.failed(interaction.failure.reason)
        land(interaction)
        done += 1
        scope.emit(
            TRIAL_END,
            seq=trial.seq,
            total=len(trials),
            subject=trial.subject,
            role=trial.role,
            failure=None if interaction.failure is None else interaction.failure.document(),
            duration_ms=interaction.duration_ns // 1_000_000,
        )
        if lost is not None:
            raise HarnessError("environment_lost", f"the environment went down: {lost}")
    return done


def _unbound(setup: Setup, kit: Kit, bindings: Bindings) -> tuple[str, ...]:
    """Each ``{<service>.<key>}`` (a key of the environment kind's ``service_keys``) the
    subjects or probes name that the environment, now up, did not bind: validation can hold
    them only to the services an environment's options list."""
    environment = schema.url("environment", setup.experiment.environment.kind)
    declared = kit.kinds.get(environment)
    keys = set(declared.service_keys) if declared is not None else set()
    services = bound(bindings)
    factors = set(setup.experiment.factors)
    written = [subject.options for subject in setup.experiment.subjects.values()]
    written += [probe.options for probe in setup.probes.values()]
    named = {
        name
        for options in written
        for name in placeholders(options)
        if "." in name and name.rpartition(".")[2] in keys and name not in factors
    }
    return tuple(sorted(name for name in named if name not in services))


def _trial_outcome(interaction: Interaction) -> dict[str, Any]:
    found: dict[str, Any] = {"ix.trial.simulated": interaction.simulated}
    if interaction.failure is not None:
        found["ix.failure.owner"] = interaction.failure.owner
    return found


def _number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _as_float(number: int | float) -> float:
    """``number`` as a float; an int too large for one is infinite, with its sign."""
    try:
        return float(number)
    except OverflowError:
        return math.inf if number > 0 else -math.inf


def _shown(number: int | float) -> str:
    """``number`` for a failure message; an int too large for a float is not spelled out."""
    if isinstance(number, int) and math.isinf(_as_float(number)):
        return "an int too large for a float"
    return str(number)


def _statistics(measured: object) -> dict[str, int | float] | None:
    """``measured`` as a distribution's statistics; None unless it maps each name to a
    number. A number may be NaN, infinite or too large for a float."""
    if not isinstance(measured, Mapping):
        return None
    if not all(_number(value) for value in measured.values()):
        return None
    return {str(name): value for name, value in measured.items()}


def _counted(measured: object) -> dict[str, float] | None:
    """``measured`` as a count: ``events`` out of ``units``, whole numbers, at least one
    unit and no more events than units. None when it is not one."""
    if not isinstance(measured, Mapping) or set(measured) != {"events", "units"}:
        return None
    events, units = measured["events"], measured["units"]
    if not all(_number(value) and _as_float(value).is_integer() for value in (events, units)):
        return None
    if not 0 <= events <= units or units < 1:
        return None
    return {"events": float(events), "units": float(units)}


def as_reading(
    interaction: Interaction,
    sensor: str,
    metric: Metric,
    measured: Measured | Failure | None,
    detail: Mapping[str, Any] | None = None,
) -> Reading:
    """One metric of one interaction: ``measured`` is what the sensor gave for it, or the
    failure that stopped the sensor giving anything; ``detail`` what the sensor said beside."""
    reading = Reading(
        seq=interaction.seq,
        subject=interaction.subject,
        role=interaction.role,
        sensor=sensor,
        metric=metric.name,
        shape=metric.shape,
        unit=metric.unit,
        detail=dict(detail or {}),
    )

    def failed(reason: str, message: str) -> Reading:
        return reading.model_copy(
            update={"failure": Failure(owner="sensor", reason=reason, message=message)}
        )

    if isinstance(measured, Failure):
        return reading.model_copy(update={"failure": measured})
    if measured is None:
        return failed("missing", "the sensor gave no value for this metric")
    if metric.shape == "boolean":
        if isinstance(measured, bool):
            return reading.model_copy(update={"value": measured})
    elif metric.shape == "count":
        if (counted := _counted(measured)) is not None:
            return reading.model_copy(update={"statistics": counted})
        return failed(
            "shape",
            "a count metric is {events: <whole number>, units: <whole number of at least 1>} "
            f"with events no more than units; the sensor gave {measured!r}",
        )
    elif metric.shape == "scalar":
        if _number(measured):
            value = _as_float(measured)
            if not math.isfinite(value):
                message = f"the sensor gave {_shown(measured)} for a scalar metric"
                return failed("not_finite", message)
            return reading.model_copy(update={"value": value})
    elif (statistics := _statistics(measured)) is not None:
        as_floats = {name: _as_float(value) for name, value in statistics.items()}
        not_finite = [
            f"{name}={_shown(statistics[name])}"
            for name, value in as_floats.items()
            if not math.isfinite(value)
        ]
        if not_finite:
            return failed(
                "not_finite",
                f"the sensor gave {', '.join(not_finite)} for a {metric.shape} metric",
            )
        return reading.model_copy(update={"statistics": as_floats})
    return failed(
        "shape", f"the sensor gave a {type(measured).__name__} for a {metric.shape} metric"
    )


async def measure(
    sensor: Sensor, interaction: Interaction, truth: Mapping[str, Any], options: Mapping[str, Any]
) -> Sensed | Failure:
    """What a sensor gives for one interaction, or the failure it raised. Anything but a
    mapping of metric names to values (and, in :class:`Sensed`, a mapping of detail) fails
    each of its readings."""
    try:
        given = await sensor.sense(interaction, truth=truth, options=options)
    except Exception as error:
        return Failure(owner="sensor", reason="error", message=f"{type(error).__name__}: {error}")
    sensed = given if isinstance(given, Sensed) else Sensed(values=given)
    if not isinstance(sensed.values, Mapping) or not isinstance(sensed.detail, Mapping):
        return Failure(
            owner="sensor",
            reason="shape",
            message=f"the sensor gave a {type(given).__name__}, not a mapping of metrics to values",
        )
    return sensed


def _evaluated(span: Span, metric: str, measured: Measured | Failure | None) -> None:
    """One metric read, on the sensor's span: its score as a number or a pass/fail label,
    or why there is none."""
    if isinstance(measured, bool):
        span.evaluated(metric, label="pass" if measured else "fail")
    elif _number(measured) and math.isfinite(_as_float(measured)):
        span.evaluated(metric, score=_as_float(measured))
    elif isinstance(measured, Failure):
        span.evaluated(metric, failure=measured.reason)
    else:
        span.evaluated(metric)


async def sense(
    setup: Setup,
    kit: Kit,
    *,
    trials: Sequence[Trial],
    interactions: Sequence[Interaction],
    scope: Scope | None = None,
) -> list[Reading]:
    """Every reading of every interaction that was carried out without a failure.

    One reading per metric the design names, from the sensor the design selected for it. A
    sensor that raises gives each of its metrics a ``sensor`` failure. Each reading names
    the session rows its sensor made while reading the interaction (a judge's calls), when
    the scope is :class:`Recorded`. It needs only the
    stored tables, so it can be run again over a run that ended before it got this far.
    """
    planned = {trial.seq: trial for trial in trials}
    readings: list[Reading] = []
    for interaction in interactions:
        trial = planned.get(interaction.seq)
        if interaction.failure is not None or trial is None:
            continue
        truth = setup.probes[trial.probe].truth if trial.probe in setup.probes else {}
        for selected in setup.design.sensors:
            declared = kit.kinds.get(selected.kind)
            options = selected.options
            if declared is not None:
                options = placed(declared, options, setup.base)
            with _span(
                scope,
                "ix.sensor",
                **{"ix.run.id": setup.manifest.run, "ix.sensor": selected.kind},
                **{"ix.trial.seq": interaction.seq, "ix.subject": interaction.subject},
            ) as span:
                before = _recorded(scope)
                measured = await measure(kit.sensors[selected.kind], interaction, truth, options)
                sessions = _since(before, scope)
                for name in selected.metrics:
                    metric = setup.design.metric(name)
                    if metric is None:
                        continue
                    given = measured if isinstance(measured, Failure) else measured.values.get(name)
                    detail = {} if isinstance(measured, Failure) else measured.detail
                    _evaluated(span, name, given)
                    reading = as_reading(interaction, selected.kind, metric, given, detail)
                    readings.append(reading.model_copy(update={"sessions": sessions}))
    if scope is not None:
        scope.emit(SENSE_END, readings=len(readings), interactions=len(interactions))
    return readings

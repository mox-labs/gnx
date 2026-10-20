"""Carrying out a run: the manifest it is held to, one trial, all the trials, the readings.

Written over the ports of :mod:`ix.domain.ports` and a :class:`~ix.domain.ports.Scope`, so it
names no host. :func:`prepare` decides before anything starts whether the experiment may run;
:func:`invoke` carries out one trial; :func:`carry_out` does that for every trial in order;
:func:`sense` turns interactions into readings.

A failed trial never raises out of :func:`invoke` and never stops the run: it is an
interaction carrying a failure with an owner (see :mod:`ix.domain.run`).

**Spend.** A run with a ceiling adds up what each trial reported it cost
(:data:`~ix.domain.run.COST`) and stops starting trials once the next could take it past the
ceiling: the next is assumed to cost as much as the dearest so far, or the experiment's
``spend.per_trial`` when that is more. A trial of a subject that spends and reports no cost
cannot be held to the ceiling, so without a ``per_trial`` the run stops there too. Either
way it writes down why (a :class:`~ix.domain.run.Halt`). Sensors that spend (a judge) are
held to what is left.

**Spans.** When the scope is :class:`~ix.domain.ports.Traced`, each trial is a span
``ix.trial`` and each sensor reading one interaction a span ``ix.sensor``, with the GenAI
conventions' ``gen_ai.evaluation.*`` attributes on a ``gen_ai.evaluation.result`` event per
metric. The agent sessions inside a trial are matrix's own spans.
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
from ix.domain.experiment import Experiment, ProbeConfig
from ix.domain.kind import fill, placed
from ix.domain.plan import effects
from ix.domain.ports import Liveness, Preflight, Sensed, StandIn, Traced
from ix.domain.registration import deviations
from ix.domain.run import (
    COST,
    Failure,
    Halt,
    HarnessError,
    Interaction,
    Reading,
    RunManifest,
    Trial,
    TrialError,
    schedule,
    schedule_digest,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from ix.application.experiments import Examined, Vocabulary
    from ix.domain.experiment import ProbeSet
    from ix.domain.kind import Kind, Metric
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
HALTED = "ix.run.halted"
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

    def event(self, name: str, /, **attributes: Any) -> None:
        return None


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
    """A run that may start: its experiment and manifest, the trials it will carry out and
    the kinds (type URLs) it uses."""

    experiment: Experiment
    manifest: RunManifest
    trials: tuple[Trial, ...]
    kinds: tuple[str, ...]


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
        named = ", ".join(f"{kind} ({where})" for where, kind in missing)
        raise ConfigError(
            f"{examined.source} cannot be run here: this ix has no adapter for {named}",
            fix="`ix kinds` marks the kinds that can be run; the experiment is still valid "
            "to plan and register",
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
    spends = any(
        effect.effect == "spend" for effect in effects(experiment, design, vocabulary.kinds, probes)
    )
    ceiling, source = None, ""
    if experiment.spend is not None and experiment.spend.ceiling is not None:
        ceiling, source = experiment.spend.ceiling, "experiment"
    elif spends and vocabulary.spend_cap is not None:
        ceiling, source = vocabulary.spend_cap, "settings"
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
            registration=None if stored is None else stored.document(),
            deviations=changed,
            gated=stored is not None and not exploratory and not changed,
            exploratory=exploratory,
            dry_run=dry_run is not None,
            stood_in=dry_run or (),
            overridden=overridden,
            spends=spends,
            spend_ceiling=ceiling,
            spend_ceiling_source=source,
            started_at=now,
            ix_version=version,
        ),
        trials=trials,
        kinds=tuple(kind for _, kind in kinds_in_use(experiment, design, probes)),
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
    changes: dict[str, Any] = {"subjects": subjects, "sensors": tuple(sensors)}
    if tuple(rewritten) != probes.probes:
        # Probes read from files are carried inline once a stand-in changes one.
        changes["probes"] = tuple(rewritten)
    return experiment.model_copy(update=changes), tuple(notes)


def unstartable(
    experiment: Experiment, subjects: Mapping[str, Subject], base: str
) -> tuple[str, ...]:
    """``subjects.<name>: <why>`` for each subject its adapter can already tell will not start
    on this host (a command that is not there, an unset credential). A subject whose adapter
    is not a :class:`Preflight` has nothing to tell; one whose kind has no adapter in
    ``subjects`` is skipped, as that is told as a kind with no adapter."""
    reasons: list[str] = []
    for name, subject in experiment.subjects.items():
        adapter = subjects.get(schema.url("subject", subject.kind))
        if not isinstance(adapter, Preflight):
            continue
        reason = adapter.unstartable(subject, base=base)
        if reason is not None:
            reasons.append(f"subjects.{name}: {reason}")
    return tuple(reasons)


def check_startable(experiment: Experiment, kit: Kit, base: str) -> None:
    """Refuse, before anything starts, what :func:`unstartable` finds. Raises
    :class:`ConfigError` naming each subject."""
    reasons = unstartable(experiment, kit.subjects, base)
    if reasons:
        raise ConfigError(
            "the experiment cannot start: " + "; ".join(reasons),
            fix="correct each subject named, or the host: a command and its cwd are relative "
            "to the experiment's directory, and a variable an auth names must be set",
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


def _spent(error: BaseException) -> float | None:
    """What a trial that failed with ``error`` had spent, where its adapter knew."""
    return error.cost_usd if isinstance(error, TrialError) else None


def _reported_cost(observations: Mapping[str, Mapping[str, Any]]) -> float | None:
    """What the facets a probe left behind say applying it cost; None when none says."""
    costs = [
        float(facet[COST])
        for facet in observations.values()
        if _number(facet.get(COST)) and math.isfinite(float(facet[COST]))
    ]
    return round(math.fsum(costs), 6) if costs else None


def _cause(during: Failure | None, at_release: Failure) -> Failure:
    """The failure a trial is recorded with, given one found when its subject was released.

    A subject that had died by then is the cause of whatever the harness reported during
    the probe (a load tool that got no answer). Otherwise the first failure stands.
    """
    if during is None or (during.owner == "harness" and at_release.owner == "subject"):
        return at_release
    return during


async def bring_up(setup: Setup, kit: Kit, scope: Scope) -> Bindings:
    """Bring the experiment's environment up, once for the run. It comes down when the run
    ends, through what the provider deferred."""
    environment = setup.experiment.environment
    provider = kit.environments[schema.url("environment", environment.kind)]
    bindings = await provider.up(environment, base=setup.base, scope=scope)
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
) -> Interaction:
    """Carry out one trial and say what happened. A failure of an adapter becomes the
    trial's failure; a defect in ix itself raises.

    Order: the subject is reached, then each sensor that must watch begins, then the probe
    is applied; the watchers stop, then the subject is released. ``hold`` holds the stack of
    whatever is open from the moment the trial starts until that stack has been closed, so
    that a caller whose trial was cancelled mid-flight can still release it.
    """
    started = kit.clock_ns()
    adapters = _adapters(trial, setup, kit)
    subject = setup.experiment.subjects[trial.subject]
    filled = subject.model_copy(update={"options": fill(subject.options, trial.cell)})
    probe_options = setup.probes[trial.probe].options
    observations: dict[str, dict[str, Any]] = {}
    watching: dict[str, dict[str, Any]] = {}
    failure: Failure | None = None
    spent: float | None = None
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
        bound = {**filled.options, **target.facets, **trial.cell}
        try:
            observations = await _apply(
                adapters.probe, fill(probe_options, bound), target, trial.seed, scope
            )
            spent = _reported_cost(observations)
        except Exception as error:
            failure = _failure(error)
            spent = _spent(error)
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
        cost_usd=spent,
        simulated=simulated,
    )


def _environment(setup: Setup, kit: Kit) -> Environment:
    return kit.environments[schema.url("environment", setup.experiment.environment.kind)]


@dataclass
class _Purse:
    """What a run with a ceiling has spent, and what it expects the next trial to cost."""

    ceiling: float | None
    #: The experiment's own estimate of one trial, the floor of every expectation.
    per_trial: float | None
    #: Whether a kind the run uses spends: a trial that reports no cost is then unknown.
    spends: bool
    spent: float = 0.0
    dearest: float = 0.0
    unknown: bool = False

    @classmethod
    def of(cls, manifest: RunManifest) -> _Purse:
        declared = manifest.declared.get("spend") or {}
        per_trial = declared.get("per_trial") if isinstance(declared, Mapping) else None
        return cls(
            ceiling=manifest.spend_ceiling,
            per_trial=float(per_trial) if _number(per_trial) else None,
            spends=manifest.spends,
        )

    @property
    def next_cost(self) -> float:
        return max(self.dearest, self.per_trial or 0.0)

    def charge(self, interaction: Interaction) -> None:
        cost = interaction.cost_usd
        if cost is None and self.spends and not interaction.simulated:
            if self.per_trial is None:
                self.unknown = True
            cost = self.per_trial
        self.spent += cost or 0.0
        self.dearest = max(self.dearest, cost or 0.0)

    def stop(self, done: int) -> Halt | None:
        """Why no further trial may start, or None while one may."""
        if self.ceiling is None:
            return None
        if self.unknown:
            return Halt(
                reason="cost_unknown",
                message="a trial reported no cost, so the run cannot be held to its ceiling of "
                f"{self.ceiling:g}; declare spend.per_trial to hold it by that estimate",
                carried_out=done,
                spent=round(self.spent, 6),
                ceiling=self.ceiling,
            )
        if self.spent + self.next_cost > self.ceiling + 1e-9:
            return Halt(
                reason="spend_ceiling",
                message=f"spent {self.spent:.4g} of a ceiling of {self.ceiling:g}, and the next "
                f"trial could cost {self.next_cost:.4g}",
                carried_out=done,
                spent=round(self.spent, 6),
                ceiling=self.ceiling,
            )
        return None


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
    halt: Callable[[Halt], None] | None = None,
) -> int:
    """Carry out ``trials`` in order, one at a time, handing each interaction to ``land`` as
    it finishes. Returns how many were carried out.

    Stops between trials when the scope says to; a trial in flight then ends as a harness
    failure (``cancelled``) and is landed like any other. Stops starting trials when the
    next could take the run past its spend ceiling, handing ``halt`` the reason. After each
    trial it asks the environment whether it is still up (when its provider is a
    :class:`Liveness`): when it is not, that trial is landed as a harness failure
    (``environment_lost``) and :class:`HarnessError` ends the run.
    """
    environment = _environment(setup, kit)
    purse = _Purse.of(setup.manifest)
    open_now: list[AsyncExitStack] = []

    async def release_open() -> None:
        for stack in list(open_now):
            await stack.aclose()

    scope.defer(release_open)  # the backstop, for a trial abandoned mid-flight
    done = 0
    for trial in trials:
        if scope.stopping.is_set():
            break
        stopped = purse.stop(done)
        if stopped is not None:
            if halt is not None:
                halt(stopped)
            scope.emit(HALTED, reason=stopped.reason, message=stopped.message)
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
            interaction = await invoke(trial, setup, kit, scope, hold=open_now)
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
        purse.charge(interaction)
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
            cost_usd=interaction.cost_usd,
        )
        if lost is not None:
            raise HarnessError("environment_lost", f"the environment went down: {lost}")
    return done


def _trial_outcome(interaction: Interaction) -> dict[str, Any]:
    found: dict[str, Any] = {"ix.trial.simulated": interaction.simulated}
    if interaction.cost_usd is not None:
        found["ix.trial.cost_usd"] = interaction.cost_usd
    if interaction.failure is not None:
        found["error.type"] = interaction.failure.reason
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
    if not all(_number(value) and float(value).is_integer() for value in (events, units)):
        return None
    if not 0 <= events <= units or units < 1:
        return None
    return {"events": float(events), "units": float(units)}


def _reading(
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


async def _measure(
    sensor: Sensor, interaction: Interaction, truth: Mapping[str, Any], options: Mapping[str, Any]
) -> Sensed | Failure:
    """What a sensor gives for one interaction, or the failure it raised."""
    try:
        given = await sensor.sense(interaction, truth=truth, options=options)
    except Exception as error:
        return Failure(owner="sensor", reason="error", message=f"{type(error).__name__}: {error}")
    return given if isinstance(given, Sensed) else Sensed(values=given)


def _evaluated(span: Span, metric: str, measured: Measured | Failure | None) -> None:
    """One ``gen_ai.evaluation.result`` event: the metric's name, and its score as a number
    or a pass/fail label."""
    about: dict[str, Any] = {"gen_ai.evaluation.name": metric}
    if isinstance(measured, bool):
        about["gen_ai.evaluation.score.label"] = "pass" if measured else "fail"
    elif _number(measured) and math.isfinite(_as_float(measured)):
        about["gen_ai.evaluation.score.value"] = _as_float(measured)
    elif isinstance(measured, Failure):
        about["error.type"] = measured.reason
    span.event("gen_ai.evaluation.result", **about)


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
    sensor that raises gives each of its metrics a ``sensor`` failure. A sensor whose kind
    spends is not asked once the run's ceiling would not cover it: its metrics then fail
    with ``spend_ceiling``. It needs only the stored tables, so it can be run again over a
    run that ended before it got this far.
    """
    planned = {trial.seq: trial for trial in trials}
    purse = _Purse.of(setup.manifest)
    for interaction in interactions:
        purse.charge(interaction)
    sensing_cost: dict[str, float] = {}
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
            spends = declared is not None and "spend" in declared.effects
            with _span(
                scope,
                "ix.sensor",
                **{"ix.run.id": setup.manifest.run, "ix.sensor": selected.kind},
                **{"ix.trial.seq": interaction.seq, "ix.subject": interaction.subject},
            ) as span:
                expected = sensing_cost.get(selected.kind, 0.0)
                if (
                    spends
                    and purse.ceiling is not None
                    and (purse.spent + expected > purse.ceiling + 1e-9)
                ):
                    measured: Sensed | Failure = Failure(
                        owner="sensor",
                        reason="spend_ceiling",
                        message=f"the run has spent {purse.spent:.4g} of its ceiling of "
                        f"{purse.ceiling:g}; this sensor was not asked",
                    )
                else:
                    measured = await _measure(
                        kit.sensors[selected.kind], interaction, truth, options
                    )
                if isinstance(measured, Sensed) and measured.cost_usd is not None:
                    purse.spent += measured.cost_usd
                    sensing_cost[selected.kind] = max(expected, measured.cost_usd)
                    span.set(**{"ix.sensor.cost_usd": measured.cost_usd})
                cost = None if isinstance(measured, Failure) else measured.cost_usd
                for name in selected.metrics:
                    metric = setup.design.metric(name)
                    if metric is None:
                        continue
                    given = measured if isinstance(measured, Failure) else measured.values.get(name)
                    detail = {} if isinstance(measured, Failure) else measured.detail
                    _evaluated(span, name, given)
                    read = _reading(interaction, selected.kind, metric, given, detail)
                    readings.append(read.model_copy(update={"cost_usd": cost}))
                    cost = None  # counted once, on the first
    if scope is not None:
        scope.emit(SENSE_END, readings=len(readings), interactions=len(interactions))
    return readings

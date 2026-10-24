"""Carrying out a run: the manifest it is held to, one trial, all the trials, the readings.

Written over the ports of :mod:`ix.domain.ports` and a :class:`~ix.domain.ports.Scope`, so it
names no host. :func:`prepare` decides before anything starts whether the experiment may run;
:func:`invoke` carries out one trial; :func:`carry_out` does that for every trial, started
in order, as many at once as the design's ``trials_in_flight``; :func:`sense` turns
interactions into readings.

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
from contextlib import (
    AsyncExitStack,
    asynccontextmanager,
    contextmanager,
    nullcontext,
    suppress,
)
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeGuard

from pydantic import ValidationError

from ix.application.experiments import standing, valid
from ix.domain import schema
from ix.domain.analysis import ANALYSES, analysis_unsupported
from ix.domain.design import Design, kinds_in_use, missing_adapters
from ix.domain.errors import ConfigError, UnregisteredError, UsageError
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
    from collections.abc import AsyncIterator, Callable, Iterator, Sequence

    from ix.application.experiments import Examined, Vocabulary
    from ix.domain.design import SelectedSensor
    from ix.domain.experiment import ProbeSet
    from ix.domain.kind import Kind, Metric
    from ix.domain.plan import Required
    from ix.domain.ports import (
        Attribution,
        Environment,
        Flag,
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

#: How long one liveness check may take by default (:attr:`Kit.liveness_timeout_s`).
LIVENESS_TIMEOUT_S = 30.0


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
    #: How long one check that the environment is still up may take; one that takes longer
    #: confirms nothing, and the run ends as for an environment lost.
    liveness_timeout_s: float = LIVENESS_TIMEOUT_S


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


class _Unrecorded:
    """What a scope that is not :class:`Recorded` attributes to a block: no session."""

    def sessions(self) -> tuple[int, ...]:
        return ()


@asynccontextmanager
async def _attributed(scope: Scope | None) -> AsyncIterator[Attribution]:
    """The sessions the block makes, in its own task and the tasks it starts, when the scope
    is :class:`Recorded`; none when it is not. Opened inside the task whose work it
    attributes, so trials in flight side by side each name only their own."""
    if isinstance(scope, Recorded):
        async with scope.attributed() as attribution:
            yield attribution
    else:
        yield _Unrecorded()


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
    what the caller changed from the experiment as written (a pilot's repeats, a width to
    try), for an exploratory run or a dry one only.

    Refuses, before anything starts: an invalid experiment; a kind in use that has no
    adapter, or an analysis this ix does not have (:class:`ConfigError`); an override on a
    run that gives a verdict (:class:`UsageError`, as the command's flags are); and an
    experiment with no registration, unless the run is ``exploratory``
    (:class:`UnregisteredError`).

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
        raise UsageError(
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
    kind: Probe, options: Mapping[str, Any], target: Target, seed: int, stopping: Flag
) -> dict[str, dict[str, Any]]:
    """Apply a probe under its budget, raced against the stop flag. Raises
    :class:`HarnessError` (``timeout``, ``cancelled``) when the probe does not finish."""
    budget_s = kind.budget_s(options)
    applying = asyncio.ensure_future(kind.apply(options, target, seed=seed))
    stopped = asyncio.ensure_future(stopping.wait())
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
    stopping: Flag | None = None,
) -> Interaction:
    """Carry out one trial and say what happened. A failure of an adapter becomes the
    trial's failure; a defect in ix itself raises. The probe stops early, a harness failure
    (``cancelled``), when ``stopping`` is set: the scope's stop flag unless another is
    given.

    The subject's options are filled from the trial's cell and the environment's
    ``bindings`` (``{db.url}``); the probe's from those, the subject's options and what the
    subject offers.

    Order: the subject is reached, then each sensor that must watch begins, then the probe
    is applied; the watchers stop, then the subject is released. The interaction names the
    session rows made in this trial's own task, when the scope is :class:`Recorded`: call it
    in the task that carries the trial out, and trials in flight beside it are not counted.
    ``hold`` holds the stack of whatever is open from the moment the trial starts until that
    stack has been closed, so that a caller whose trial was cancelled mid-flight can still
    release it.
    """
    async with _attributed(scope) as attribution:
        interaction = await _carried(
            trial, setup, kit, hold=hold, bindings=bindings, stopping=stopping or scope.stopping
        )
    return interaction.model_copy(update={"sessions": attribution.sessions()})


async def _carried(
    trial: Trial,
    setup: Setup,
    kit: Kit,
    *,
    hold: list[AsyncExitStack] | None,
    bindings: Bindings | None,
    stopping: Flag,
) -> Interaction:
    """One trial carried out as :func:`invoke` says, before its sessions are named."""
    started = kit.clock_ns()
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
                stopping,
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
    """Carry out ``trials``, starting them in order with at most the design's
    ``trials_in_flight`` in flight, and hand each interaction to ``land`` once it is
    finished and confirmed. Returns how many were landed. Every row carries its ``seq``;
    nothing reorders them.

    Each trial runs in a task of its own (:func:`invoke` names its sessions from it). When
    the environment's provider is a :class:`Liveness`, one checker asks it, one call at a
    time and each within the kit's ``liveness_timeout_s``, whether it is still up: a trial
    is landed as carried out only once a check that began after it ended found it up. On
    the first check that does not, nothing more starts, every trial in flight and every one
    finished and not yet confirmed lands as a harness failure (``environment_lost``), and
    :class:`HarnessError` ends the run. A check that does not answer in time is such a
    check.

    When the scope says to stop, nothing more starts, and each trial in flight ends as it
    ends or as a harness failure (``cancelled``), and is landed like any other. One trial in
    flight is exactly the serial run: start, finish, check, land, start the next.
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
        # Together: matrix bounds this release by one cleanup_s, which width releases one
        # after another could overrun; and one that raises strands none of the others.
        left = list(open_now)
        done = await asyncio.gather(*(stack.aclose() for stack in left), return_exceptions=True)
        failed = [error for error in done if isinstance(error, BaseException)]
        if failed:
            said = "; ".join(f"{type(error).__name__}: {error}" for error in failed)
            raise HarnessError(
                "release_failed",
                f"{len(failed)} of the {len(left)} trials cut off could not be released: {said}",
            ) from failed[0]

    scope.defer(release_open)  # the backstop, for a trial abandoned mid-flight
    slots = asyncio.Semaphore(setup.design.trials_in_flight)
    halt = asyncio.Event()  # set on a stop or a loss: the probes in flight end early
    checker = _Checker(environment, bindings, kit.liveness_timeout_s, halt)
    landed = 0

    async def one(trial: Trial) -> None:
        nonlocal landed
        try:
            with _span(scope, "ix.trial", **_trial_attributes(trial, setup)) as span:
                interaction = await invoke(
                    trial, setup, kit, scope, hold=open_now, bindings=bindings, stopping=halt
                )
                lost = await checker.confirm()
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
            landed += 1
            scope.emit(
                TRIAL_END,
                seq=trial.seq,
                total=len(trials),
                subject=trial.subject,
                role=trial.role,
                failure=None if interaction.failure is None else interaction.failure.document(),
                duration_ms=interaction.duration_ns // 1_000_000,
            )
        finally:
            slots.release()

    beside = [asyncio.ensure_future(_relay(scope.stopping, halt))]
    if checker.live:
        beside.append(checker.start())
    try:
        async with asyncio.TaskGroup() as group:
            for trial in trials:
                await slots.acquire()
                if scope.stopping.is_set() or checker.lost is not None:
                    slots.release()
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
                group.create_task(one(trial))
    except BaseExceptionGroup as failed:
        # A defect in ix in one trial ended them all; it raises as it would have alone.
        raise failed.exceptions[0] from failed
    finally:
        for task in beside:
            task.cancel()
            # A checker that died on a defect said so as the loss it settled every trial
            # with, which the run raises below.
            with suppress(asyncio.CancelledError, Exception):
                await task
    if checker.lost is not None:
        raise HarnessError("environment_lost", f"the environment went down: {checker.lost}")
    return landed


async def _relay(flag: Flag, to: asyncio.Event) -> None:
    """Set ``to`` once ``flag`` is set."""
    await flag.wait()
    to.set()


class _Checker:
    """The one serial check of a run that its environment is still up (dijkstra's I3).

    A finished trial asks :meth:`confirm` and waits. Each check takes every trial waiting
    when it began, and only those: a trial that finishes while a check is under way waits
    for the next one, so the trials waiting are never more than those in flight. The first
    check that finds the environment lost (or does not answer within ``timeout_s``, or
    raises) settles every trial waiting then and every trial that asks after, and sets
    ``halt``."""

    def __init__(
        self, environment: Environment, bindings: Bindings, timeout_s: float, halt: asyncio.Event
    ) -> None:
        self._liveness = environment if isinstance(environment, Liveness) else None
        self._bindings = bindings
        self._timeout_s = timeout_s
        self._halt = halt
        self._waiting: list[asyncio.Future[str | None]] = []
        #: The trials the check under way answers for.
        self._asked: list[asyncio.Future[str | None]] = []
        self._wake = asyncio.Event()
        #: Why the environment is no longer up, once a check found it; None until then.
        self.lost: str | None = None

    @property
    def live(self) -> bool:
        """Whether there is anything to ask: the environment's provider is a Liveness."""
        return self._liveness is not None

    def confirm(self) -> asyncio.Future[str | None]:
        """For a trial that has just ended: None once a check begun after now finds the
        environment up; why it is lost otherwise."""
        settled: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        if self._liveness is None or self.lost is not None:
            settled.set_result(self.lost)
        else:
            self._waiting.append(settled)
            self._wake.set()
        return settled

    def start(self) -> asyncio.Task[None]:
        """The checker as a task of its own. Should it end on a defect of its own, every trial
        waiting on it, and every one that asks after, is settled as lost: none waits for a
        check that will never come."""
        task = asyncio.ensure_future(self.run())
        task.add_done_callback(self._ended)
        return task

    def _ended(self, task: asyncio.Task[None]) -> None:
        if task.cancelled() or (error := task.exception()) is None:
            return
        if self.lost is None:
            self.lost = f"the check of whether it is up failed: {type(error).__name__}: {error}"
        self._halt.set()
        waiting, self._asked, self._waiting = [*self._asked, *self._waiting], [], []
        for settled in waiting:
            if not settled.done():
                settled.set_result(self.lost)

    async def run(self) -> None:
        while self.lost is None:
            await self._wake.wait()
            self._wake.clear()
            self._asked, self._waiting = self._waiting, []
            found = await self._ask()
            if found is not None:
                self.lost = found
                self._halt.set()
                self._asked, self._waiting = [*self._asked, *self._waiting], []
            for settled in self._asked:
                if not settled.done():  # its trial may have been cancelled meanwhile
                    settled.set_result(found)
            self._asked = []  # once settled: until then, a checker's end can still settle them

    async def _ask(self) -> str | None:
        liveness = self._liveness
        if liveness is None:
            return None
        try:
            return await asyncio.wait_for(liveness.lost(self._bindings), self._timeout_s)
        except TimeoutError:
            return f"the environment did not say whether it is up within {self._timeout_s:g}s"
        except Exception as error:
            return f"checking whether it is up raised {type(error).__name__}: {error}"


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
    the scope is :class:`Recorded`; hand each over naming those (``Reading.sessions``) and
    no more. It needs only the stored tables, so it can be run again over a run that ended
    before it got this far.

    It reads as many interactions at once as the design's ``trials_in_flight``, each reading
    in a task of its own; a sensor whose kind does not allow overlap (or is not declared here)
    reads one interaction at a time. The readings come in ``seq`` order, then the design's
    order of sensors and metrics, whatever order the interactions were given in.
    """
    planned = {trial.seq: trial for trial in trials}
    carried = sorted(
        (i for i in interactions if i.failure is None and i.seq in planned), key=lambda i: i.seq
    )
    slots = asyncio.Semaphore(setup.design.trials_in_flight)
    alone = {
        selected.kind: asyncio.Lock()
        for selected in setup.design.sensors
        if (declared := kit.kinds.get(selected.kind)) is None or declared.overlap != "allowed"
    }
    found: dict[tuple[int, int], list[Reading]] = {}

    async def read(at: int, interaction: Interaction, index: int, selected: SelectedSensor) -> None:
        try:
            lock = alone.get(selected.kind)
            async with lock if lock is not None else nullcontext():
                probe = planned[interaction.seq].probe
                truth = setup.probes[probe].truth if probe in setup.probes else {}
                found[at, index] = await _read(setup, kit, interaction, selected, truth, scope)
        finally:
            slots.release()

    try:
        async with asyncio.TaskGroup() as group:
            for at, interaction in enumerate(carried):
                for index, selected in enumerate(setup.design.sensors):
                    await slots.acquire()
                    group.create_task(read(at, interaction, index, selected))
    except BaseExceptionGroup as failed:
        raise failed.exceptions[0] from failed  # a defect in ix, as it would raise alone
    readings = [reading for key in sorted(found) for reading in found[key]]
    if scope is not None:
        scope.emit(SENSE_END, readings=len(readings), interactions=len(interactions))
    return readings


async def _read(
    setup: Setup,
    kit: Kit,
    interaction: Interaction,
    selected: SelectedSensor,
    truth: Mapping[str, Any],
    scope: Scope | None,
) -> list[Reading]:
    """What one sensor reads of one interaction: a reading per metric the design has it
    read, each naming the sessions the sensor made in this task."""
    declared = kit.kinds.get(selected.kind)
    options = selected.options
    if declared is not None:
        options = placed(declared, options, setup.base)
    readings: list[Reading] = []
    with _span(
        scope,
        "ix.sensor",
        **{"ix.run.id": setup.manifest.run, "ix.sensor": selected.kind},
        **{"ix.trial.seq": interaction.seq, "ix.subject": interaction.subject},
    ) as span:
        async with _attributed(scope) as attribution:
            measured = await measure(kit.sensors[selected.kind], interaction, truth, options)
        sessions = attribution.sessions()
        for name in selected.metrics:
            metric = setup.design.metric(name)
            if metric is None:
                continue
            given = measured if isinstance(measured, Failure) else measured.values.get(name)
            detail = {} if isinstance(measured, Failure) else measured.detail
            _evaluated(span, name, given)
            reading = as_reading(interaction, selected.kind, metric, given, detail)
            readings.append(reading.model_copy(update={"sessions": sessions}))
    return readings

"""The ports of ix: what the model and the run path ask of the world.

Each hides a decision that changes: how a subject is started and stopped (:class:`Subject`),
which tool applies a stimulus (:class:`Probe`), how a measurement is taken
(:class:`Sensor`), and what is brought up around the subjects (:class:`Environment`).
:class:`Scope` is all ix assumes of whatever runs its work. The rest are optional, so an
adapter need not answer what it cannot tell: a subject or a sensor may also be a
:class:`Review` and a :class:`StandIn`, any adapter a :class:`Preflight`, an environment a
:class:`Liveness`, and a scope :class:`Traced` and :class:`Recorded`. Without one there is
nothing to check, to stand in, to trace or to name, and the run proceeds. A kind's options,
facets and metrics are data (:mod:`ix.domain.kind`), not part of these ports.

The four kind ports are runtime-checkable: each is the port of its family's extension point,
so a class registered there that lacks one of its members is refused when it is registered,
and whatever a factory builds is held to it when it is built.

Each port is named for what it is to a run; what an experiment writes for one is its config
(:class:`~ix.domain.experiment.SubjectConfig`, ``ProbeConfig``, ``SensorConfig``,
``EnvironmentConfig``): the kind, a registered type, and that kind's options.

**Contracts the signatures do not show.**

* *Order.* ``reach`` is entered before any ``watch``; every ``watch`` exits before ``reach``
  does; ``sense`` is called only for an interaction that has no failure.
* *Failure.* ``reach`` and ``apply`` raise :class:`~ix.domain.run.SubjectError` when the
  subject failed and :class:`~ix.domain.run.HarnessError` when the harness or its tool did;
  anything else is read as the harness's. None of these fails the run.
* *Release.* Leaving ``reach`` releases everything it started, however it is left.
* *Blocking.* Every method that waits is ``async`` and yields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from contextlib import AbstractAsyncContextManager, AbstractContextManager

    from ix.domain.design import SelectedSensor
    from ix.domain.experiment import EnvironmentConfig, ProbeConfig, SubjectConfig
    from ix.domain.problem import Problem
    from ix.domain.run import Bindings, Interaction, Target

#: What a sensor returns for one metric of one trial: a boolean, a number, each statistic
#: of the trial's distribution by name (a histogram or a series), or ``{events, units}``
#: (a count: failed requests out of requests sent).
type Measured = bool | float | Mapping[str, float]


@dataclass(frozen=True)
class Sensed:
    """What a sensor read from one interaction, when it has more to say than its numbers:
    ``detail`` (the skill that fired, the tests that failed), kept with each of its readings."""

    values: Mapping[str, Measured]
    detail: Mapping[str, Any] = field(default_factory=dict)


class Flag(Protocol):
    """A flag that is set once: an ``asyncio.Event`` is one."""

    def is_set(self) -> bool: ...

    async def wait(self) -> Any: ...


class Scope(Protocol):
    """All ix assumes of whatever runs its components."""

    #: The run's id, unique to it: what an adapter names what it starts for the run after,
    #: so that two runs, or a run and what a killed one left behind, do not collide.
    @property
    def run_id(self) -> str: ...

    #: Set when the run is asked to finish now. Work that returns soon after keeps what it
    #: has recorded.
    @property
    def stopping(self) -> Flag: ...

    def defer(self, release: Callable[[], Awaitable[None] | None], /) -> None:
        """Register something to undo when the run ends, however it ends."""
        ...

    def emit(self, name: str, /, **fields: Any) -> None:
        """Say that something happened. Telemetry: it may be dropped, and nothing reads a
        result from it."""
        ...


class Span(Protocol):
    """One span of ix's own work, open while its ``with`` block runs."""

    def set(self, **attributes: Any) -> None:
        """Add attributes, as known by the time the work ends."""
        ...

    def failed(self, reason: str) -> None:
        """Say that the work ended in a failure, and its reason (``timeout``, ``exited``)."""
        ...

    def evaluated(
        self,
        metric: str,
        *,
        score: float | None = None,
        label: str | None = None,
        failure: str | None = None,
    ) -> None:
        """Record one metric read inside the span: its score as a number, or a pass/fail
        ``label``, or the reason no value could be read (``failure``)."""
        ...


@runtime_checkable
class Recorded(Protocol):
    """Optional for a :class:`Scope`: the model sessions recorded on its behalf. Whatever
    runs ix keeps a table of the agent sessions and model calls its adapters made, failed
    ones included (matrix's ``matrix.v1.session``); this names their rows, so an interaction
    or a reading can say which sessions are behind it."""

    def sessions(self) -> tuple[int, ...]:
        """The rows recorded since the scope last handed a row over, in the order made."""
        ...


@runtime_checkable
class Traced(Protocol):
    """Optional for a :class:`Scope`: spans of ix's own work (a trial, a sensor reading one),
    under ix's own instrumentation scope. Telemetry: nothing reads a result from it."""

    def trace(self, name: str, /, **attributes: Any) -> AbstractContextManager[Span]: ...


@runtime_checkable
class Subject(Protocol):
    """Brings one subject up for one trial and takes it down again."""

    def reach(
        self, name: str, subject: SubjectConfig, *, base: str
    ) -> AbstractAsyncContextManager[Target]:
        """Bring the subject up and yield how to address it; release it on exit.

        ``subject`` has its options filled in for the trial's cell.
        """
        ...


@dataclass(frozen=True)
class Requirement:
    """Something an adapter needs of this host to carry an entry out as written, and whether
    it holds here now: a program on PATH, a daemon that answers, a variable that is set, a
    file that is there, a runtime that resolves the model. ``need`` says what, in words that
    stay the same whether it holds or not; ``why`` says what was found when it does not, and
    ``fix`` how to make it hold."""

    need: str
    holds: bool
    why: str = ""
    fix: str = ""


@runtime_checkable
class Preflight(Protocol):
    """Optional for any adapter (a subject, probe, sensor or environment): what it needs of
    this host, each checked before anything starts. ``ix plan`` lists them with whether each
    holds, and ``ix run`` refuses to start while one does not."""

    def requirements(self, options: Mapping[str, Any], *, base: str) -> Sequence[Requirement]:
        """What carrying out an entry whose options are ``options`` needs of this host, each
        checked now. ``base`` is the directory the experiment's relative paths resolve
        against. It is asked once for each cell's options, a factor's ``{name}`` filled in;
        a requirement that depends on a placeholder only a trial fills (an environment's
        binding, ``{db.url}``) is left out: it is told when the trial starts."""
        ...


@dataclass(frozen=True)
class Surroundings:
    """An entry of the experiment as a :class:`Review` sees it: where it is written
    (``subjects.agent``, ``sensors.0``), the directory its relative paths resolve against,
    every sensor a run would use with its options, and every probe with where it is
    written (``probes.0``, or ``probes.<id>`` for a probe read from a file)."""

    path: str
    base: str
    sensors: tuple[SelectedSensor, ...] = ()
    probes: tuple[tuple[str, ProbeConfig], ...] = ()


@runtime_checkable
class Review(Protocol):
    """Optional for a :class:`Subject` or a :class:`Sensor`: what is wrong with how an entry
    is written that only its adapter can tell, from the experiment alone: an option the
    runtime it names would refuse, a setting that leaves a sensor nothing to observe. It
    asks nothing of the host (that is a :class:`Preflight`'s), so what it finds is a problem
    with the experiment, and ``validate`` reports it."""

    def review(self, options: Mapping[str, Any], around: Surroundings) -> Sequence[Problem]:
        """Every problem with the entry whose options are ``options``, each ``path`` dotted
        from the experiment's root; none when its options are of the wrong shape, which the
        kind's declaration reports."""
        ...


@dataclass(frozen=True)
class StoodIn:
    """An entry's options rewritten to run on a stand-in, and a note for each thing changed
    or set aside on the way."""

    options: Mapping[str, Any]
    notes: tuple[str, ...] = ()


@runtime_checkable
class StandIn(Protocol):
    """Optional for a :class:`Subject`, :class:`Probe` or :class:`Sensor`: the same entry
    on a stand-in that touches no model, for a dry run, which checks the harness and never
    the subject. A subject without one cannot be dry run."""

    def stand_in(self, options: Mapping[str, Any]) -> StoodIn: ...


@runtime_checkable
class Probe(Protocol):
    """Applies one stimulus to a target."""

    def budget_s(self, options: Mapping[str, Any]) -> float | None:
        """The longest one application may take before it is a harness timeout; None for
        no bound."""
        ...

    async def apply(
        self, options: Mapping[str, Any], target: Target, *, seed: int
    ) -> Mapping[str, Mapping[str, Any]]:
        """Apply the stimulus and return what it left behind, by the facet the probe kind
        offers. ``options`` are the probe's, with every placeholder filled in for this
        trial. ``seed`` is the trial's own, drawn from the registered seed: a probe kind that
        makes a random choice (a sampled payload, a jittered arrival) draws it from ``seed``,
        so a rerun of the plan applies the same stimulus. One that chooses nothing ignores
        it."""
        ...


@runtime_checkable
class Sensor(Protocol):
    """Measures: during the probe when it must (``watch``), from the interaction after."""

    def watch(
        self, target: Target, options: Mapping[str, Any]
    ) -> AbstractAsyncContextManager[dict[str, Any]] | None:
        """Watch the target while the probe runs, or return None when there is nothing to
        watch. What the context yields is complete once the context has exited, and is
        stored in the interaction under this sensor's kind."""
        ...

    async def sense(
        self, interaction: Interaction, *, truth: Mapping[str, Any], options: Mapping[str, Any]
    ) -> Mapping[str, Measured] | Sensed:
        """This sensor's metrics for one interaction, by metric name, or a :class:`Sensed`
        that holds them with what more it has to say. A metric it cannot give is left out;
        raising gives every metric a ``sensor`` failure. ``options`` are the sensor's, each
        option its kind declares a ``path`` already resolved against the experiment's
        directory."""
        ...


@runtime_checkable
class Environment(Protocol):
    """Brings up what the subjects need around them, once per run. May also be a
    :class:`Liveness`."""

    async def up(self, environment: EnvironmentConfig, *, base: str, scope: Scope) -> Bindings:
        """Bring the environment up and return each service by name with how to reach it.
        Whatever is started is released through ``scope.defer``, so it comes down when the
        run ends however that happens."""
        ...


@runtime_checkable
class Liveness(Protocol):
    """Optional for an :class:`Environment`: whether what it brought up is still up,
    asked after each trial."""

    async def lost(self, bindings: Bindings) -> str | None:
        """Why a service ``up`` returned as ``bindings`` is no longer up, in words; None
        while every one is."""
        ...

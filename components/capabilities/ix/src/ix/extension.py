"""What an extension of ix implements, and what it is handed: the whole adapter contract.

A package adds a kind in two parts. Its **declaration** is data: a YAML file at the
``ix.kind`` entry point (:class:`ix.Kind`), saying which options the kind takes and which
facets it offers and needs. Its **adapter** is code: registered from the
package's ``matrix.extensions`` entry point at the family's point (:data:`POINTS`) under the
kind's type URL, held to the family's port (:data:`PORTS`) when it is registered and again
when it is built, and built with what its registration says it ``needs``::

    from ix.extension import LAUNCHER, POINTS

    def register(registry):
        registry.register(
            POINTS["subject"], "acme.v1.subject.echo", EchoSubject, needs={LAUNCHER}
        )

Each need is a name the adapter's factory takes as a keyword argument, and the Protocol of
what it is given:

========================  ==========================================================
:data:`LAUNCHER`          a :class:`Launcher`: starts a child in a process group of its
                          own and ends the whole group
:data:`RUNTIMES`          :class:`Runtimes`: matrix's agent and model runtimes, by point
                          and name
:data:`SESSIONS`          :class:`Sessions`: the agent sessions and model calls open
                          now, by handle, shared by a subject and a probe
:data:`JUDGE_MODELS`      :class:`JudgeModels`: a pydantic-ai ``Model`` making its calls
                          through a matrix model runtime
========================  ==========================================================

An adapter's own role is one of the four family ports (:class:`Subject`, :class:`Probe`,
:class:`Sensor`, :class:`Environment`). What else it can say is a separate optional
Protocol, read with ``isinstance`` and never by an attribute's name: :class:`Preflight`
(what it needs of this host), :class:`Review` (what is wrong with how an entry is written),
:class:`StandIn` (the entry on a stand-in for a dry run), :class:`ModelCalls` (how many
model calls a trial starts, which ``ix plan`` counts as work), :class:`SpanReader` (whether a
sensor reads a trial's spans) and :class:`Liveness` (whether an environment is still up).
An adapter that is none of them has nothing to say there, and the run proceeds. The scope a
run hands an environment is a :class:`Scope`, and may also be :class:`Traced` and
:class:`Recorded`.

**What an adapter is given.** A :class:`Review` and a :class:`StandIn` are given an
entry's options as written: they judge or rewrite the writing. Everything that carries an
entry out (a :class:`Preflight`, :class:`ModelCalls` and every member of the family
ports) is given its options
as carried out: every option the entry leaves out that its kind declares a ``default`` for
is there, with that default, so an adapter holds no default of its own. A sensor's options
also have each ``path`` option resolved against the experiment's directory; a subject, an
environment and a preflight are given that directory as ``base``.

**Failure is part of the contract.** ``reach`` and ``apply`` raise :class:`SubjectError`
when the subject failed and :class:`HarnessError` when the harness or its tool did; anything
else is read as the harness's. A sensor that raises gives each of its metrics a sensor
failure. None of these fails the run.

**Stability.** Everything here is ix's published extension contract (``ix.v1``) and changes
only with a version's breaking section in the changelog. :class:`JudgeModels` and
:class:`SpanReader` have one built-in user each and are **provisional**: they may change
without a deprecation until a second user arrives.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

from ix.domain.design import SelectedSensor
from ix.domain.experiment import EnvironmentConfig, ProbeConfig, SubjectConfig
from ix.domain.kind import arrivals, placeholders, seconds
from ix.domain.ports import (
    Attribution,
    Environment,
    Flag,
    Liveness,
    Measured,
    ModelCalls,
    Preflight,
    Probe,
    Recorded,
    Requirement,
    Review,
    Scope,
    Sensed,
    Sensor,
    Span,
    SpanReader,
    StandIn,
    StoodIn,
    Subject,
    Surroundings,
    Traced,
)
from ix.domain.problem import Problem, problem
from ix.domain.run import Bindings, HarnessError, Interaction, SubjectError, Target, TrialError

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Mapping, Sequence

    import matrix

#: The extension point of each family of adapter.
POINTS: Final[dict[str, str]] = {
    "subject": "ix.subject",
    "probe": "ix.probe",
    "sensor": "ix.sensor",
    "environment": "ix.environment",
}

#: The port each family's point holds what is registered there to: a class that lacks one
#: of its members is refused at registration, and what a factory builds when it is built.
PORTS: Final[dict[str, type]] = {
    "subject": Subject,
    "probe": Probe,
    "sensor": Sensor,
    "environment": Environment,
}

#: The need of an adapter that starts processes: a :class:`Launcher`.
LAUNCHER: Final = "launcher"
#: The need of an adapter that runs an agent or makes a model call: :class:`Runtimes`.
RUNTIMES: Final = "runtimes"
#: The need of the adapters that share an agent session or a model call by its handle: the
#: :class:`Sessions` open now.
SESSIONS: Final = "sessions"
#: The need of an adapter whose model is a pydantic-ai ``Model`` making its calls through a
#: matrix model runtime: :class:`JudgeModels`.
JUDGE_MODELS: Final = "judge_models"

#: matrix's two runtime points: an agent session (a loop and tools) and one model call.
type Point = Literal["agent-runtime", "model-runtime"]
AGENT_RUNTIME: Final = "agent-runtime"
MODEL_RUNTIME: Final = "model-runtime"


class Launcher(Protocol):
    """Starts a child in a group of its own, and ends the whole group."""

    async def start(self, argv: Sequence[str], **options: Any) -> asyncio.subprocess.Process:
        """Start ``argv``. ``options`` are those of ``asyncio.create_subprocess_exec``."""
        ...

    async def stop(self, process: asyncio.subprocess.Process, *, grace_s: float = 5.0) -> None:
        """End the process and everything it started: politely, then for good."""
        ...


class Runtimes(Protocol):
    """matrix's runtimes, at its two points. A ``name`` is a runtime's short name at that
    point (``claude-sdk``) or its type URL."""

    def build(
        self, point: Point, name: str, config: Mapping[str, Any], *, cwd: str | None = None
    ) -> Any:
        """The runtime registered at ``point`` as ``name``, built with ``config``; ``cwd`` is
        where an agent runtime runs its sessions. Raises ``matrix.MatrixError``, or
        :class:`ix.NotFoundError` for a name nothing registered there."""
        ...

    def stand_in(self, point: Point, name: str) -> bool:
        """Whether the runtime declares itself a stand-in: its replies are canned, not a
        model's. Raises as :meth:`build` does for an unknown name."""
        ...

    def takes(self, point: Point, name: str) -> frozenset[str] | None:
        """The keys the runtime's config declares; an empty set for one that takes no
        config, None for a name nothing registered there."""
        ...

    def drives(self, point: Point, name: str) -> dict[str, str]:
        """Each distribution the runtime drives (``claude-agent-sdk`` for ``claude-sdk``), as
        its registration declares, with its installed version (``not installed`` for one
        that is not); none for a runtime that declares none. Raises as :meth:`build` does
        for an unknown name."""
        ...

    def capabilities(
        self, point: Point, name: str, config: Mapping[str, Any]
    ) -> matrix.AgentCapabilities | None:
        """What sessions on the runtime do when it is built with ``config``, as the runtime
        declares (whether they load skills, whether their tools can write): None for one that
        declares nothing, a config it refuses, or a name nothing registered there. Asks
        nothing of the host."""
        ...


class Respondent(Protocol):
    """What a probe puts its prompt to, by the handle a subject opened it under: an agent
    session or a model call."""

    #: Whether a stand-in answers: canned replies, not a model's.
    simulated: bool

    async def answer(
        self, prompt: str, options: Mapping[str, Any]
    ) -> Mapping[str, Mapping[str, Any]]:
        """Put ``prompt`` and return what was left behind, by facet: ``response`` and
        ``usage``. ``options`` are the probe's. Raises the runtime's error as it stands."""
        ...


class Sessions(Protocol):
    """What is open now, by handle: a subject opens its session or call here and offers the
    handle as its ``respondent`` facet, and a probe built over the same table looks it up,
    so a target carries only the handle."""

    def open(self, respondent: Respondent) -> str:
        """Hold ``respondent`` open and return its handle, unique in this process."""
        ...

    def close(self, handle: str) -> None:
        """Let go of the respondent open under ``handle``; nothing for one not open."""
        ...

    def __getitem__(self, handle: str) -> Respondent:
        """The respondent open under ``handle``. Raises :class:`HarnessError`
        (``no_session``) for one that is not: the subject was released."""
        ...


class JudgeModels(Protocol):
    """Where a judge evaluator's model comes from: a pydantic-ai ``Model`` that makes its
    calls through a matrix model runtime. Provisional (see the module docstring)."""

    def model(self, runtime: str, model: str | None, config: Mapping[str, Any]) -> Any:
        """The pydantic-ai ``Model`` for ``model`` on the model runtime named ``runtime``,
        built with ``config``. Raises :class:`ix.IxError` (with a ``fix``),
        ``matrix.MatrixError`` or ``ValueError`` when there is none to give."""
        ...


__all__ = [
    "AGENT_RUNTIME",
    "JUDGE_MODELS",
    "LAUNCHER",
    "MODEL_RUNTIME",
    "POINTS",
    "PORTS",
    "RUNTIMES",
    "SESSIONS",
    "Attribution",
    "Bindings",
    "Environment",
    "EnvironmentConfig",
    "Flag",
    "HarnessError",
    "Interaction",
    "JudgeModels",
    "Launcher",
    "Liveness",
    "Measured",
    "ModelCalls",
    "Point",
    "Preflight",
    "Probe",
    "ProbeConfig",
    "Problem",
    "Recorded",
    "Requirement",
    "Respondent",
    "Review",
    "Runtimes",
    "Scope",
    "SelectedSensor",
    "Sensed",
    "Sensor",
    "Sessions",
    "Span",
    "SpanReader",
    "StandIn",
    "StoodIn",
    "Subject",
    "SubjectConfig",
    "SubjectError",
    "Surroundings",
    "Target",
    "Traced",
    "TrialError",
    "arrivals",
    "placeholders",
    "problem",
    "seconds",
]

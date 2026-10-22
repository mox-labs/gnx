"""ix's ``matrix.extensions`` entry point: everything ix plugs into matrix's registry.

ix owns one extension point per family of adapter an experiment names (:data:`POINTS`). An
adapter registers at its family's point under the type URL of the kind it carries out
(``ix.v1.subject.process``), and declares what it needs built in: :data:`LAUNCHER`, for one
that starts processes; :data:`RUNTIMES`, for one that runs an agent or makes a model call on
matrix's runtimes; :data:`SESSIONS`, for a subject and a probe that share an agent session or
a model call by its handle.

A third party adds an adapter for its own kind the same way, from its own entry point.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ix.adapters._in.matrix_components import COMPONENTS, NEED, PAYLOADS
from ix.adapters._out.agent_sensors import (
    ActivationSensor,
    FunctionTestSensor,
    OutcomeSensor,
    TextMatchSensor,
    ToolUsageSensor,
    UsageSensor,
)
from ix.adapters._out.environments import NoEnvironment, ProcessEnvironment
from ix.adapters._out.k6_load import K6Load
from ix.adapters._out.matrix_agents import AgentSessionSubject, PromptProbe
from ix.adapters._out.matrix_calls import ModelCallSubject
from ix.adapters._out.matrix_judge import JudgeSensor
from ix.adapters._out.sensors import MemorySensor, TimingSensor
from ix.adapters._out.subjects import HttpSubject, ProcessSubject
from ix.domain import schema

if TYPE_CHECKING:
    from matrix import Registry

#: The extension point of each family of adapter.
POINTS: dict[str, str] = {
    "subject": "ix.subject",
    "probe": "ix.probe",
    "sensor": "ix.sensor",
    "environment": "ix.environment",
}

#: The need of an adapter that starts processes: a ``Launcher``.
LAUNCHER = "launcher"
#: The need of an adapter that runs an agent or makes a model call: matrix's runtimes, by
#: point and name.
RUNTIMES = "runtimes"
#: The need of the adapters that share an agent session or a model call: the table of what
#: is open, by handle.
SESSIONS = "sessions"


def register_run_path(registry: Registry) -> None:
    """Declare ix's points and register its components and row schemas: all of ix but its
    adapters."""
    for point in POINTS.values():
        registry.add_point(point)
    for type_url, (build, summary) in COMPONENTS.items():
        registry.register("component", type_url, build, needs={NEED}, summary=summary)
    for kind, model in PAYLOADS.items():
        registry.register_payload(kind, model)


def register(registry: Registry) -> None:
    """Register the run path and the adapters ix ships."""
    register_run_path(registry)
    agent = {SESSIONS, RUNTIMES, LAUNCHER}
    adapters = [
        ("subject", "process", ProcessSubject, "Starts a program for each trial", {LAUNCHER}),
        ("subject", "http", HttpSubject, "An endpoint that is already running", set()),
        ("subject", "agent-session", AgentSessionSubject, "A fresh agent session per trial",
         agent),
        ("subject", "model-call", ModelCallSubject, "One model call per trial",
         {SESSIONS, RUNTIMES}),
        ("probe", "http-load", K6Load, "HTTP load for a fixed time, driven by k6", {LAUNCHER}),
        ("probe", "prompt", PromptProbe, "One prompt put to an agent session or a model call",
         {SESSIONS}),
        ("sensor", "timing", TimingSensor, "Latency and throughput from a load probe", set()),
        ("sensor", "memory", MemorySensor, "Peak resident memory, sampled", set()),
        ("sensor", "activation", ActivationSensor, "Whether the expected skill fired", set()),
        ("sensor", "tool-usage", ToolUsageSensor, "Whether the expected tool was used", set()),
        ("sensor", "function-test", FunctionTestSensor, "Test cases run against the code",
         {LAUNCHER}),
        ("sensor", "outcome", OutcomeSensor, "The experiment's own graders", {LAUNCHER}),
        ("sensor", "text-match", TextMatchSensor, "The reply's text against plain rules",
         set()),
        ("sensor", "usage", UsageSensor, "What a session consumed and how it ended", set()),
        ("sensor", "judge", JudgeSensor, "A model scores against a rubric; never decides",
         {RUNTIMES}),
        ("environment", "none", NoEnvironment, "Nothing to bring up", set()),
        ("environment", "process", ProcessEnvironment, "Programs started for the run", {LAUNCHER}),
    ]  # fmt: skip
    for family, name, build, summary, needs in adapters:
        registry.register(
            POINTS[family], schema.url(family, name), build, needs=needs, summary=summary
        )

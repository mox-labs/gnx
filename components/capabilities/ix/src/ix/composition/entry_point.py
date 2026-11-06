"""ix's ``matrix.extensions`` entry point: everything ix plugs into matrix's registry.

ix declares one extension point per family of adapter (:data:`ix.extension.POINTS`), and
registers its run path's components and row schemas, and the adapters it ships, as a third
party registers its own: each at its family's point under the type URL of the kind it
carries out, with the needs :mod:`ix.extension` names.
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
from ix.adapters._out.commands import CommandProbe, ExitStatusSensor
from ix.adapters._out.compose import ComposeEnvironment
from ix.adapters._out.environments import NoEnvironment, ProcessEnvironment
from ix.adapters._out.evaluators import EvaluatorSensor
from ix.adapters._out.k6_load import K6Load
from ix.adapters._out.matrix_agents import AgentSessionSubject, PromptProbe
from ix.adapters._out.matrix_calls import ModelCallSubject
from ix.adapters._out.matrix_judge import JudgeSensor
from ix.adapters._out.sensors import MemorySensor, TimingSensor
from ix.adapters._out.subjects import HttpSubject, ProcessSubject
from ix.domain import schema
from ix.extension import (
    JUDGE_MODELS,
    LAUNCHER,
    POINTS,
    PORTS,
    RUNTIMES,
    SESSIONS,
)

if TYPE_CHECKING:
    from matrix import Registry


def register_run_path(registry: Registry) -> None:
    """Declare ix's points and register its components and row schemas: all of ix but its
    adapters."""
    for family, point in POINTS.items():
        registry.add_point(point, port=PORTS[family])
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
        ("probe", "command", CommandProbe, "A command run once against the subject, as a check",
         {LAUNCHER}),
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
        ("sensor", "exit-status", ExitStatusSensor, "Whether a command exited with its code",
         set()),
        ("sensor", "judge", JudgeSensor, "A model scores against a rubric; never decides",
         {RUNTIMES}),
        ("sensor", "evaluator", EvaluatorSensor, "A pydantic-evals evaluator reads the trial",
         {RUNTIMES, JUDGE_MODELS}),
        ("environment", "none", NoEnvironment, "Nothing to bring up", set()),
        ("environment", "process", ProcessEnvironment, "Programs started for the run", {LAUNCHER}),
        ("environment", "compose", ComposeEnvironment, "A docker compose project up for the run",
         {LAUNCHER}),
    ]  # fmt: skip
    for family, name, build, summary, needs in adapters:
        registry.register(
            POINTS[family], schema.url(family, name), build, needs=needs, summary=summary
        )

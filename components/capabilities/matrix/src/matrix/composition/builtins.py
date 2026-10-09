"""matrix's own extensions, registered through the same entry point as anyone else's.

``pyproject.toml`` declares ``[project.entry-points."matrix.extensions"] matrix = ...:register``,
so :meth:`Registry.discover` finds the built-ins exactly as it finds a third party's: one
discovery path, no special case. Optional dependencies are imported only when an entry is
built, so registering costs nothing and a missing extra fails when used, saying what to install.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from matrix.adapters._out.runtime.claude_sdk import ClaudeSdkRuntimeConfig
from matrix.adapters._out.runtime.mock import MockRuntime, MockRuntimeConfig
from matrix.adapters._out.runtime.model import ModelAgentRuntimeConfig
from matrix.domain.agent import AgentResponse
from matrix.domain.agent_step import AGENT_RESPONSE, AGENT_STEP, TASK, AgentStep, AgentStepConfig
from matrix.domain.ids import type_url

if TYPE_CHECKING:
    from collections.abc import Mapping

    from matrix.domain.ports._out.agent_runtime import Agent
    from matrix.domain.registry import Registry

#: Runtime short names in config (``type: claude-sdk``) resolve under this prefix.
RUNTIME_PREFIX = "matrix.v1.runtime."
OBSERVER_PREFIX = "matrix.v1.observer."


def runtime_type_url(name: str) -> str:
    """``claude-sdk`` → ``matrix.v1.runtime.claude-sdk``; a full type URL passes through."""
    return name if "." in name else type_url("matrix", 1, f"runtime.{name}")


def observer_type_url(name: str) -> str:
    """``otel`` → ``matrix.v1.observer.otel``; a full type URL passes through."""
    return name if "." in name else type_url("matrix", 1, f"observer.{name}")


def _claude_sdk(config: ClaudeSdkRuntimeConfig, *, cwd: str | None = None) -> Any:
    from matrix.adapters._out.runtime.claude_sdk import ClaudeSdkRuntime

    if config.cwd is None and cwd is not None:
        config = config.model_copy(update={"cwd": cwd})
    return ClaudeSdkRuntime(config)


def _model(config: ModelAgentRuntimeConfig, *, models: dict[str, Any] | None = None) -> Any:
    from matrix.adapters._out.runtime.model import ModelAgentRuntime

    if config.models is None and models is not None:
        config = config.model_copy(update={"models": models})
    return ModelAgentRuntime.from_config(config)


def _otel() -> Any:
    from matrix.adapters._out.observers.otel import OtelObserver

    return OtelObserver()


def _agent_step(config: AgentStepConfig, *, agents: Mapping[str, Agent]) -> AgentStep:
    return AgentStep(config, agents=agents)


def register(registry: Registry) -> None:
    """Register matrix's runtimes, components, payload types and observers."""
    registry.register(
        "runtime",
        runtime_type_url("claude-sdk"),
        _claude_sdk,
        config=ClaudeSdkRuntimeConfig,
        needs={"cwd"},
        effects={"model", "network", "subprocess", "filesystem"},
        summary="Claude Agent SDK sessions: tools, multiple turns, plugins",
    )
    registry.register(
        "runtime",
        runtime_type_url("model"),
        _model,
        config=ModelAgentRuntimeConfig,
        needs={"models"},
        effects={"model", "network"},
        summary="One model call through hardline; any model family, no tools",
    )
    registry.register(
        "runtime",
        runtime_type_url("mock"),
        MockRuntime,
        config=MockRuntimeConfig,
        effects=set(),
        summary="Deterministic replies from config; offline",
        stability="stable",
    )
    registry.register(
        "component",
        AGENT_STEP,
        _agent_step,
        config=AgentStepConfig,
        needs={"agents"},
        summary="An agent as a flow member: inputs rendered into its task, its reply on ports",
    )
    registry.register_payload(AGENT_RESPONSE, AgentResponse)
    registry.register(
        "payload-type", TASK, str, summary="A task for an agent: plain text", stability="stable"
    )
    registry.register(
        "observer",
        observer_type_url("otel"),
        _otel,
        effects={"network"},
        summary="OpenTelemetry spans for runs, members and agents (requires matrix[otel])",
    )

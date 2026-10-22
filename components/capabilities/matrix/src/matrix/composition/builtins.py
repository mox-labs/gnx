"""matrix's own extensions, registered through the same entry point as anyone else's.

``pyproject.toml`` declares ``[project.entry-points."matrix.extensions"] matrix = ...:register``,
so :func:`~matrix.composition.container.load_extensions` finds the built-ins exactly as it
finds a third party's: one discovery path, no special case. Optional dependencies are
imported only when an entry is built, so registering costs nothing and a missing extra fails
when used, saying what to install.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from matrix.adapters._out.observers.jsonl import JsonlObserver, JsonlObserverConfig
from matrix.adapters._out.runtime.claude_sdk import ClaudeSdkRuntimeConfig
from matrix.adapters._out.runtime.mock import MockRuntime, MockRuntimeConfig
from matrix.adapters._out.runtime.model import ModelAgentRuntimeConfig
from matrix.adapters._out.stores.jsonl import (
    JsonlConstructStore,
    JsonlStoreConfig,
    MemoryConstructStore,
)
from matrix.domain.agent import AgentResponse
from matrix.domain.agent_step import AGENT_RESPONSE, AGENT_STEP, TASK, AgentStep, AgentStepConfig
from matrix.domain.component import LEDGER
from matrix.domain.construct import LedgerEntry
from matrix.domain.errors import missing_extra
from matrix.domain.ids import type_url
from matrix.domain.ref import REF, Ref

if TYPE_CHECKING:
    from collections.abc import Mapping

    from matrix.adapters._out.observers.console import ConsoleObserver
    from matrix.adapters._out.observers.otel import OtelObserver
    from matrix.adapters._out.runtime.claude_sdk import ClaudeSdkRuntime
    from matrix.adapters._out.runtime.model import ModelAgentRuntime
    from matrix.domain.ports._out.agent_runtime import Agent
    from matrix.domain.registry import Registry

#: Runtime short names in config (``type: claude-sdk``) resolve under this prefix.
RUNTIME_PREFIX = "matrix.v1.runtime."


def runtime_type_url(name: str) -> str:
    """``claude-sdk`` → ``matrix.v1.runtime.claude-sdk``; a full type URL passes through."""
    return name if "." in name else type_url("matrix", 1, f"runtime.{name}")


def observer_type_url(name: str) -> str:
    """``otel`` → ``matrix.v1.observer.otel``; a full type URL passes through."""
    return name if "." in name else type_url("matrix", 1, f"observer.{name}")


def _claude_sdk(config: ClaudeSdkRuntimeConfig, *, cwd: str | None = None) -> ClaudeSdkRuntime:
    from matrix.adapters._out.runtime.claude_sdk import ClaudeSdkRuntime

    if config.cwd is None and cwd is not None:
        config = config.model_copy(update={"cwd": cwd})
    return ClaudeSdkRuntime(config)


def _model(
    config: ModelAgentRuntimeConfig, *, models: dict[str, Any] | None = None
) -> ModelAgentRuntime:
    from matrix.adapters._out.runtime.model import ModelAgentRuntime

    if config.models is None and models is not None:
        config = config.model_copy(update={"models": models})
    return ModelAgentRuntime.from_config(config)


def store_type_url(name: str) -> str:
    """``jsonl`` → ``matrix.v1.store.jsonl``; a full type URL passes through."""
    return name if "." in name else type_url("matrix", 1, f"store.{name}")


# Here, not beside the observer, so that registering imports no OpenTelemetry.
class OtelObserverConfig(BaseModel):
    """The ``otel`` observer's options. Without any, spans carry no content."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capture_content: bool = Field(
        default=False,
        description="Export content too: each agent's task (matrix.task), error messages "
        "(matrix.error and the span's status) and a component's string ctx.span fields. Off "
        "by default, as the GenAI semantic conventions ask; turning it on sends prompts to "
        "the collector.",
    )


def _otel(config: OtelObserverConfig) -> OtelObserver:
    try:
        from matrix.adapters._out.observers.otel import OtelObserver
    except ImportError as e:  # the API is a dependency; a broken install still says what to do
        raise missing_extra("otel", needed_by="the otel observer", module="opentelemetry") from e
    return OtelObserver(capture_content=config.capture_content)


def _console() -> ConsoleObserver:
    from matrix.adapters._out.observers.console import ConsoleObserver

    return ConsoleObserver()


def _jsonl(config: JsonlObserverConfig) -> JsonlObserver:
    return JsonlObserver(config.path)


def _agent_step(config: AgentStepConfig, *, agents: Mapping[str, Agent]) -> AgentStep:
    return AgentStep(config, agents=agents)


def register(registry: Registry) -> None:
    """Register matrix's runtimes, components, payload types, observers and stores."""
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
        summary="An agent as a component: the rows it requires rendered into its task",
    )
    registry.register_payload(AGENT_RESPONSE, AgentResponse)
    registry.register_payload(LEDGER, LedgerEntry)
    registry.register_payload(REF, Ref)
    registry.register(
        "payload-type", TASK, str, summary="A task for an agent: plain text", stability="stable"
    )
    registry.register(
        "observer",
        observer_type_url("otel"),
        _otel,
        config=OtelObserverConfig,
        effects={"network"},
        summary="OpenTelemetry spans and metrics on the GenAI conventions, without content "
        "unless capture_content; exported when the OTEL_* variables say where (requires "
        "matrix[otel])",
    )
    registry.register(
        "observer",
        observer_type_url("console"),
        _console,
        effects=set(),
        summary="Progress on stderr: component states and elapsed time, live on a terminal",
    )
    registry.register(
        "observer",
        observer_type_url("jsonl"),
        _jsonl,
        config=JsonlObserverConfig,
        effects={"filesystem"},
        summary="One JSON line per event, to a file or stderr: the stream an agent tails",
    )
    registry.register(
        "store",
        store_type_url("jsonl"),
        JsonlConstructStore.from_config,
        config=JsonlStoreConfig,
        effects={"filesystem"},
        summary="Each run a directory of JSONL tables, written as it goes, with its events",
        stability="stable",
    )
    registry.register(
        "store",
        store_type_url("memory"),
        MemoryConstructStore,
        effects=set(),
        summary="Runs kept in memory for the life of the process: tests and embedding",
    )

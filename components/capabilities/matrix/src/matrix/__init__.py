"""Matrix: component runtime for DAG execution, and the composition of agents.

Kind-agnostic. Components declare what they read and write; matrix derives the graph,
enforces the declarations at run time, and ledgers every result.

Agents are composition too: an AgentDefinition (what the agent is) bound to an AgentRuntime
(where it runs) gives a BoundAgent. ``compose`` builds all three from config.
"""

from typing import Any

from .composition.config import deep_merge, discover_sources, load_config
from .composition.container import (
    Container,
    bind_agents,
    build_runtimes,
    compose,
    default_registry,
    load_definitions,
)
from .composition.runtimes import register_builtin_runtimes, runtime_type_url
from .domain.agent import AgentDefinition, BoundAgent
from .domain.compiler import CompilationError, DagCompiler
from .domain.config import AgentConfig, Config, MatrixConfig, RuntimeConfig
from .domain.errors import ConfigError
from .domain.orchestrator import Orchestrator
from .domain.ports._out.agent_runtime import AgentRuntime
from .domain.ports._out.definition_source import DefinitionSource
from .domain.ports._out.runtime import Agent
from .domain.registry import ComponentRegistry
from .domain.scheduler import DagScheduler
from .domain.types import (
    AgentResponse,
    Artifact,
    Component,
    Construct,
    ConstructReader,
    ConstructView,
    ContractError,
    TypedStruct,
)


def configure_telemetry(**kwargs: Any) -> None:
    """Configure OpenTelemetry SDK. Requires: uv add matrix[otel]"""
    from .composition.telemetry import configure_telemetry as _configure

    return _configure(**kwargs)


__all__ = [
    "Agent",
    "AgentConfig",
    "AgentDefinition",
    "AgentResponse",
    "AgentRuntime",
    "Artifact",
    "BoundAgent",
    "CompilationError",
    "Component",
    "ComponentRegistry",
    "Config",
    "ConfigError",
    "Construct",
    "ConstructReader",
    "ConstructView",
    "Container",
    "ContractError",
    "DagCompiler",
    "DagScheduler",
    "DefinitionSource",
    "MatrixConfig",
    "Orchestrator",
    "RuntimeConfig",
    "TypedStruct",
    "bind_agents",
    "build_runtimes",
    "compose",
    "configure_telemetry",
    "deep_merge",
    "default_registry",
    "discover_sources",
    "load_config",
    "load_definitions",
    "register_builtin_runtimes",
    "runtime_type_url",
]

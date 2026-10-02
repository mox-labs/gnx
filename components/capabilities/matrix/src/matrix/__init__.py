"""Matrix: component runtime for DAG execution, and the composition of agents.

Kind-agnostic. Components declare what they read and write; matrix derives the graph,
enforces the declarations at run time, and ledgers every result.

Agents are composition too: an AgentDefinition (what the agent is) bound to an AgentRuntime
(where it runs) gives a BoundAgent. ``compose`` builds all three from config.
"""

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from .composition.config import config_env_var, discover_sources, load_config
from .composition.container import Container, compose, default_registry
from .composition.runtimes import runtime_type_url
from .domain.agent import AgentDefinition, BoundAgent
from .domain.config import AgentConfig, Config, MatrixConfig, RuntimeConfig
from .domain.errors import (
    AgentRuntimeError,
    CompilationError,
    ComponentError,
    ConfigError,
    ContractError,
    MatrixError,
    NotFoundError,
)
from .domain.orchestrator import Orchestrator
from .domain.ports._out.agent_runtime import AgentRuntime
from .domain.ports._out.definition_source import DefinitionSource
from .domain.ports._out.runtime import Agent
from .domain.registry import ComponentRegistry
from .domain.type_url import TypeUrl, parse_type_url, type_url
from .domain.types import (
    AgentResponse,
    Artifact,
    Component,
    Construct,
    ConstructReader,
    ConstructView,
    TypedStruct,
)

try:
    __version__ = version("matrix")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"


def configure_telemetry(**kwargs: Any) -> None:
    """Configure the OpenTelemetry SDK for matrix's spans. Requires ``matrix[otel]``."""
    from .composition.telemetry import configure_telemetry as _configure

    return _configure(**kwargs)


__all__ = [
    "Agent",
    "AgentConfig",
    "AgentDefinition",
    "AgentResponse",
    "AgentRuntime",
    "AgentRuntimeError",
    "Artifact",
    "BoundAgent",
    "CompilationError",
    "Component",
    "ComponentError",
    "ComponentRegistry",
    "Config",
    "ConfigError",
    "Construct",
    "ConstructReader",
    "ConstructView",
    "Container",
    "ContractError",
    "DefinitionSource",
    "MatrixConfig",
    "MatrixError",
    "NotFoundError",
    "Orchestrator",
    "RuntimeConfig",
    "TypeUrl",
    "TypedStruct",
    "__version__",
    "compose",
    "config_env_var",
    "configure_telemetry",
    "default_registry",
    "discover_sources",
    "load_config",
    "parse_type_url",
    "runtime_type_url",
    "type_url",
]

"""matrix: the executor. Typed components wired into flows, and agents composed from config.

* **Flows**: components declare named, typed ports; members bind ports to topics; matrix
  checks the wiring, runs the flow, and records every value in a :class:`Construct`.
* **Agents**: an :class:`AgentDefinition` (what) bound to an :class:`AgentRuntime` (where),
  checked at binding. ``matrix.v1.agent-step`` makes an agent an ordinary flow member.
* **Composition**: ``load_config`` reads the shared config tiers, ``compose`` builds runtimes,
  agents and observers from them, and the registry finds every extension (built-ins too)
  through the ``matrix.extensions`` entry point.

The wiring and type URL grammar follow slick's ratified rules, carried here until matrix
depends on slick.
"""

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from .adapters._out.stores.jsonl import JsonlConstructStore, MemoryConstructStore
from .composition.builtins import observer_type_url, runtime_type_url
from .composition.config import config_env_var, discover_sources, load_config
from .composition.container import Container, compose, default_registry
from .domain.agent import AgentDefinition, AgentResponse, BoundAgent
from .domain.agent_step import AGENT_RESPONSE, AGENT_STEP, TASK, AgentStep, AgentStepConfig
from .domain.config import AgentConfig, Config, MatrixConfig, RuntimeConfig
from .domain.construct import Artifact, Construct
from .domain.errors import (
    EXIT_CODES,
    AgentRuntimeError,
    CompilationError,
    ComponentError,
    ConfigError,
    ContractError,
    ErrorKind,
    MatrixError,
    NotFoundError,
    RunError,
    RuntimeReason,
)
from .domain.executor import Executor, Limits, Run
from .domain.flow import (
    CompiledFlow,
    Component,
    Flow,
    Inputs,
    Member,
    Port,
    RunContext,
    compile_flow,
)
from .domain.ids import TypeUrl, check_type_url, is_type_url, parse_type_url, type_url
from .domain.observer import Event, Observer, RecordingObserver
from .domain.ports._out.agent_runtime import Agent, AgentRuntime
from .domain.ports._out.config_source import ConfigSource
from .domain.ports._out.construct_store import ConstructStore
from .domain.ports._out.definition_source import DefinitionSource
from .domain.registry import Entry, Failure, Registry

try:
    __version__ = version("matrix")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"


def configure_telemetry(**kwargs: Any) -> None:
    """Configure the OpenTelemetry SDK exporter. Requires ``matrix[otel]``."""
    from .composition.telemetry import configure_telemetry as _configure

    return _configure(**kwargs)


__all__ = [
    "AGENT_RESPONSE",
    "AGENT_STEP",
    "EXIT_CODES",
    "TASK",
    "Agent",
    "AgentConfig",
    "AgentDefinition",
    "AgentResponse",
    "AgentRuntime",
    "AgentRuntimeError",
    "AgentStep",
    "AgentStepConfig",
    "Artifact",
    "BoundAgent",
    "CompilationError",
    "CompiledFlow",
    "Component",
    "ComponentError",
    "Config",
    "ConfigError",
    "ConfigSource",
    "Construct",
    "ConstructStore",
    "Container",
    "ContractError",
    "DefinitionSource",
    "Entry",
    "ErrorKind",
    "Event",
    "Executor",
    "Failure",
    "Flow",
    "Inputs",
    "JsonlConstructStore",
    "Limits",
    "MatrixConfig",
    "MatrixError",
    "MemoryConstructStore",
    "Member",
    "NotFoundError",
    "Observer",
    "Port",
    "RecordingObserver",
    "Registry",
    "Run",
    "RunContext",
    "RunError",
    "RuntimeConfig",
    "RuntimeReason",
    "TypeUrl",
    "__version__",
    "check_type_url",
    "compile_flow",
    "compose",
    "config_env_var",
    "configure_telemetry",
    "default_registry",
    "discover_sources",
    "is_type_url",
    "load_config",
    "observer_type_url",
    "parse_type_url",
    "runtime_type_url",
    "type_url",
]

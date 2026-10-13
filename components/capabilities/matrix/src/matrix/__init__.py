"""matrix: components wired by kind, a registry, and config.

* **Components** declare the kinds (type URLs) of data they require and provide. matrix
  infers the wiring, runs each component when what it requires is ready, and records every
  row in a :class:`Construct`: a table per kind, kept in a store as the run goes.
* **Agents**: an :class:`AgentDefinition` (what) bound to an :class:`AgentRuntime` (where),
  checked at binding. ``matrix.v1.agent-step`` makes an agent an ordinary component.
* **Composition**: ``load_config`` reads the shared config tiers, ``compose`` builds runtimes,
  agents and observers from them, and the registry finds every extension (built-ins too)
  through the ``matrix.extensions`` entry point.

The type URL grammar follows slick's ratified rules, carried here until matrix depends on
slick.
"""

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from . import process
from .adapters._out.stores.jsonl import JsonlConstructStore, MemoryConstructStore
from .composition.builtins import observer_type_url, runtime_type_url
from .composition.config import config_env_var, discover_sources, load_config
from .composition.container import Container, compose, default_registry
from .domain.agent import AgentDefinition, AgentResponse, BoundAgent
from .domain.agent_step import AGENT_RESPONSE, AGENT_STEP, TASK, AgentStep, AgentStepConfig
from .domain.component import LEDGER, Component, Inputs, RunContext, Wiring, wire
from .domain.config import (
    AgentConfig,
    ComponentConfig,
    Config,
    MatrixConfig,
    ObserverConfig,
    RuntimeConfig,
)
from .domain.construct import Construct, LedgerEntry
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
    RunCancelled,
    RunError,
    RuntimeReason,
    StoreError,
)
from .domain.executor import ComponentLimits, ComponentStatus, Executor, Limits, Run
from .domain.ids import TypeUrl, check_type_url, is_type_url, parse_type_url, type_url
from .domain.observer import Event, EventPolicy, Observer, RecordingObserver
from .domain.ports._out.agent_runtime import Agent, AgentRuntime
from .domain.ports._out.config_source import ConfigSource
from .domain.ports._out.construct_store import ConstructStore, RunWriter
from .domain.ports._out.definition_source import DefinitionSource
from .domain.ref import REF, Ref
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
    "LEDGER",
    "REF",
    "TASK",
    "Agent",
    "AgentConfig",
    "AgentDefinition",
    "AgentResponse",
    "AgentRuntime",
    "AgentRuntimeError",
    "AgentStep",
    "AgentStepConfig",
    "BoundAgent",
    "CompilationError",
    "Component",
    "ComponentConfig",
    "ComponentError",
    "ComponentLimits",
    "ComponentStatus",
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
    "EventPolicy",
    "Executor",
    "Failure",
    "Inputs",
    "JsonlConstructStore",
    "LedgerEntry",
    "Limits",
    "MatrixConfig",
    "MatrixError",
    "MemoryConstructStore",
    "NotFoundError",
    "Observer",
    "ObserverConfig",
    "RecordingObserver",
    "Ref",
    "Registry",
    "Run",
    "RunCancelled",
    "RunContext",
    "RunError",
    "RunWriter",
    "RuntimeConfig",
    "RuntimeReason",
    "StoreError",
    "TypeUrl",
    "Wiring",
    "__version__",
    "check_type_url",
    "compose",
    "config_env_var",
    "configure_telemetry",
    "default_registry",
    "discover_sources",
    "is_type_url",
    "load_config",
    "observer_type_url",
    "parse_type_url",
    "process",
    "runtime_type_url",
    "type_url",
    "wire",
]

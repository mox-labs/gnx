"""matrix: components wired by kind, a registry, and config.

* **Components** declare the kinds (type URLs) of data they require and provide. matrix
  infers the wiring, runs each component when what it requires is ready, and records every
  row in a :class:`Construct`: a table per kind, kept in a store as the run goes.
* **Agents**: an :class:`AgentDefinition` (what) bound to an :class:`AgentRuntime` (where),
  checked at binding. ``matrix.v1.agent-step`` makes an agent an ordinary component.
* **Model calls**: one call on a :class:`ModelRuntime`, with no loop or tools, recorded as a
  :class:`ModelCall`. ``matrix.v1.model-step`` makes one an ordinary component.
* **Composition**: ``load_config`` reads the shared config tiers, ``compose`` builds runtimes,
  agents, observers and the store from them, and the registry finds every extension
  (built-ins too) through the ``matrix.extensions`` entry point. ``plan_config`` says what a
  run would do; ``Container.run`` runs the configured components; ``list_runs`` lists the
  runs a store keeps.
* **Loading**: ``load`` and ``load_each`` read YAML and markdown documents as the pydantic
  type the caller names, reporting every problem as a :class:`Problem` with its key path.

The README lists which of these names are the stable public API.

A type URL is ``<namespace>.v<N>.<name>``: lowercase segments joined by dots, checked by
:func:`check_type_url`.
"""

from importlib.metadata import PackageNotFoundError, version

from . import process
from .adapters._in.signals import cancel_on_signals
from .adapters._in.skill import render as _render_skill
from .adapters._out.stores.jsonl import JsonlConstructStore, MemoryConstructStore
from .composition.builtins import (
    agent_runtime_type_url,
    model_runtime_type_url,
    observer_type_url,
    store_type_url,
)
from .composition.config import discover_sources, load, load_config, load_each
from .composition.container import Container, compose, default_registry, load_extensions
from .composition.documents import (
    Agents,
    Catalog,
    CheckReport,
    ConfigSources,
    ConfigView,
    Extensions,
    Plan,
    Runs,
    catalog,
    check_config,
    config_sources,
    config_view,
    describe_extension,
    list_agents,
    list_runs,
    plan_config,
)
from .composition.telemetry import Telemetry, configure_telemetry
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
    StoreConfig,
)
from .domain.construct import Construct, LedgerEntry
from .domain.errors import (
    EXIT_CODES,
    EXIT_MEANINGS,
    EXIT_NEXT,
    EXITS_AS,
    AgentRuntimeError,
    CompilationError,
    ComponentError,
    ConfigError,
    ContractError,
    ErrorKind,
    MatrixError,
    ModelRuntimeError,
    NotFoundError,
    RunCancelled,
    RunError,
    RuntimeReason,
    StoreError,
    UsageError,
)
from .domain.executor import (
    ComponentEnding,
    ComponentFailure,
    ComponentLimits,
    ComponentStatus,
    Executor,
    Limits,
    ReleaseOutcome,
    Retry,
    Reuse,
    Run,
)
from .domain.ids import TypeUrl, check_type_url, is_type_url, parse_type_url, type_url
from .domain.loading import Accumulate, BesideFile, Loader
from .domain.model_call import (
    CallAttempt,
    FinishReason,
    ModelCall,
    ModelRequest,
    RegistryRow,
    RequestParams,
    TokenUsage,
)
from .domain.model_step import MODEL_CALL, MODEL_STEP, PROMPT, ModelStep, ModelStepConfig
from .domain.observer import Event, EventPolicy, Observer, RecordingObserver
from .domain.ports._out.agent_runtime import RUNTIME_LOGGER, Agent, AgentRuntime
from .domain.ports._out.construct_store import ConstructStore, RunWriter
from .domain.ports._out.document_source import Document, DocumentSource
from .domain.ports._out.model_runtime import ModelRuntime
from .domain.problems import Problem
from .domain.ref import REF, Ref
from .domain.registry import Entry, Extension, Failure, Registry
from .domain.session import (
    SESSION,
    Session,
    agent_session,
    call_sent,
    call_session,
    digest,
    record_session,
)

try:
    __version__ = version("matrix")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"


def skill(reference: str | None = None) -> str:
    """The skill an agent reads: what matrix is for, the component protocol, writing and
    registering a component, the commands, and the errors and exit codes. ``reference`` names
    one of its references instead (``config``, ``running``, ``telemetry``, ``loading``).
    Raises UsageError for a reference it does not have, listing those it has."""
    return _render_skill(__version__, reference)


__all__ = [
    "AGENT_RESPONSE",
    "AGENT_STEP",
    "EXITS_AS",
    "EXIT_CODES",
    "EXIT_MEANINGS",
    "EXIT_NEXT",
    "LEDGER",
    "MODEL_CALL",
    "MODEL_STEP",
    "PROMPT",
    "REF",
    "RUNTIME_LOGGER",
    "SESSION",
    "TASK",
    "Accumulate",
    "Agent",
    "AgentConfig",
    "AgentDefinition",
    "AgentResponse",
    "AgentRuntime",
    "AgentRuntimeError",
    "AgentStep",
    "AgentStepConfig",
    "Agents",
    "BesideFile",
    "BoundAgent",
    "CallAttempt",
    "Catalog",
    "CheckReport",
    "CompilationError",
    "Component",
    "ComponentConfig",
    "ComponentEnding",
    "ComponentError",
    "ComponentFailure",
    "ComponentLimits",
    "ComponentStatus",
    "Config",
    "ConfigError",
    "ConfigSources",
    "ConfigView",
    "Construct",
    "ConstructStore",
    "Container",
    "ContractError",
    "Document",
    "DocumentSource",
    "Entry",
    "ErrorKind",
    "Event",
    "EventPolicy",
    "Executor",
    "Extension",
    "Extensions",
    "Failure",
    "FinishReason",
    "Inputs",
    "JsonlConstructStore",
    "LedgerEntry",
    "Limits",
    "Loader",
    "MatrixConfig",
    "MatrixError",
    "MemoryConstructStore",
    "ModelCall",
    "ModelRequest",
    "ModelRuntime",
    "ModelRuntimeError",
    "ModelStep",
    "ModelStepConfig",
    "NotFoundError",
    "Observer",
    "ObserverConfig",
    "Plan",
    "Problem",
    "RecordingObserver",
    "Ref",
    "Registry",
    "RegistryRow",
    "ReleaseOutcome",
    "RequestParams",
    "Retry",
    "Reuse",
    "Run",
    "RunCancelled",
    "RunContext",
    "RunError",
    "RunWriter",
    "Runs",
    "RuntimeConfig",
    "RuntimeReason",
    "Session",
    "StoreConfig",
    "StoreError",
    "Telemetry",
    "TokenUsage",
    "TypeUrl",
    "UsageError",
    "Wiring",
    "__version__",
    "agent_session",
    "agent_runtime_type_url",
    "call_sent",
    "call_session",
    "cancel_on_signals",
    "catalog",
    "check_config",
    "check_type_url",
    "compose",
    "config_sources",
    "config_view",
    "configure_telemetry",
    "default_registry",
    "describe_extension",
    "digest",
    "discover_sources",
    "is_type_url",
    "list_agents",
    "list_runs",
    "load",
    "load_config",
    "load_each",
    "load_extensions",
    "model_runtime_type_url",
    "observer_type_url",
    "parse_type_url",
    "plan_config",
    "process",
    "record_session",
    "skill",
    "store_type_url",
    "type_url",
    "wire",
]

"""hardline — one port for every model family.

Messages in; text or a validated object out; tagged with the family that answered, the
usage it cost, and the latency, so a caller can ledger the call and an evaluation harness
can tell whether a verifier was out of family.

Not an agent runtime: no tool loop, no permissions, no trajectory. Those live one layer up
(matrix's agent runtimes), which reach models through this port.

    from hardline import build_runtime
    rt = build_runtime()                       # discovers ~/.hardline/config.yaml, ./hardline.yaml
    c = await rt.complete("qwen3-8b", "Summarise this in one line: ...")
    s = await rt.extract("haiku", "...", output=MyModel)   # s.value is a validated MyModel
"""

from importlib.metadata import PackageNotFoundError, version

from hardline.composition import (
    build_runtime,
    configure_telemetry,
    discover_backends,
    discover_sources,
    load_registry,
)
from hardline.conformance import answer_problems, backend_problems
from hardline.domain.errors import (
    RETRYABLE,
    BackendError,
    ConfigError,
    ContractError,
    HardlineError,
    Reason,
    SchemaError,
    SecretError,
    UnknownModelError,
)
from hardline.domain.ports._out.backend import ModelBackend
from hardline.domain.ports._out.config_source import ConfigSource
from hardline.domain.ports._out.observer import CallObserver
from hardline.domain.ports._out.secrets import SecretResolver
from hardline.domain.registry import ModelRegistry
from hardline.domain.runtime import ModelRuntime
from hardline.domain.types import (
    COMPLETION_TYPE_URL,
    NAMESPACE,
    Answered,
    Attempt,
    AttemptFailed,
    CallEvent,
    CallLimits,
    Completion,
    FellBack,
    FinishReason,
    Message,
    ModelSpec,
    Problem,
    RawCompletion,
    Request,
    RequestParams,
    RowRecord,
    Structured,
    ToolCall,
    ToolSpec,
    Usage,
)
from hardline.skill import get_skill

try:
    __version__ = version("hardline")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"

__all__ = [
    "COMPLETION_TYPE_URL",
    "RETRYABLE",
    "NAMESPACE",
    "Answered",
    "Attempt",
    "AttemptFailed",
    "BackendError",
    "CallEvent",
    "CallLimits",
    "CallObserver",
    "Completion",
    "ConfigError",
    "ConfigSource",
    "ContractError",
    "FellBack",
    "FinishReason",
    "Message",
    "ModelBackend",
    "ModelRegistry",
    "ModelRuntime",
    "HardlineError",
    "Reason",
    "ModelSpec",
    "Problem",
    "RawCompletion",
    "Request",
    "RequestParams",
    "RowRecord",
    "SchemaError",
    "SecretError",
    "SecretResolver",
    "Structured",
    "ToolCall",
    "ToolSpec",
    "UnknownModelError",
    "Usage",
    "__version__",
    "answer_problems",
    "backend_problems",
    "build_runtime",
    "configure_telemetry",
    "discover_backends",
    "discover_sources",
    "get_skill",
    "load_registry",
]

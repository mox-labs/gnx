"""modelrt — one port for every model family.

Messages in; text or a validated object out; tagged with the family that answered, the
usage it cost, and the latency, so a caller can ledger the call and an evaluation harness
can tell whether a verifier was out of family.

Not an agent runtime: no tool loop, no permissions, no trajectory. Those live one layer up
(matrix's agent runtimes), which reach models through this port.

    from modelrt import build_runtime
    rt = build_runtime()                       # discovers ~/.modelrt/config.yaml, ./modelrt.yaml
    c = await rt.complete("qwen3-8b", "Summarise this in one line: ...")
    s = await rt.extract("haiku", "...", output=MyModel)   # s.value is a validated MyModel
"""

from modelrt.composition import build_runtime, discover_backends, discover_sources
from modelrt.domain.errors import (
    BackendError,
    ConfigError,
    ContractError,
    ModelRuntimeError,
    SchemaError,
    SecretError,
    UnknownModelError,
)
from modelrt.domain.ports._out.backend import ModelBackend
from modelrt.domain.ports._out.config_source import ConfigSource
from modelrt.domain.ports._out.secrets import SecretResolver
from modelrt.domain.registry import ModelRegistry
from modelrt.domain.runtime import ModelRuntime
from modelrt.domain.types import (
    COMPLETION_TYPE_URL,
    NAMESPACE,
    Completion,
    Message,
    ModelSpec,
    RawCompletion,
    Request,
    Structured,
    Usage,
    backend_type_url,
)

__version__ = "0.1.0"

__all__ = [
    "COMPLETION_TYPE_URL",
    "NAMESPACE",
    "BackendError",
    "Completion",
    "ConfigError",
    "ConfigSource",
    "ContractError",
    "Message",
    "ModelBackend",
    "ModelRegistry",
    "ModelRuntime",
    "ModelRuntimeError",
    "ModelSpec",
    "RawCompletion",
    "Request",
    "SchemaError",
    "SecretError",
    "SecretResolver",
    "Structured",
    "UnknownModelError",
    "Usage",
    "backend_type_url",
    "build_runtime",
    "discover_backends",
    "discover_sources",
]

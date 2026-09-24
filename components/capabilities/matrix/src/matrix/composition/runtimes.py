"""Built-in agent runtimes, registered under ``matrix.v1/runtime.<type>``.

A runtime ``type`` in config (``claude-sdk``, ``model``, ``mock``) resolves to this type URL
through the same ComponentRegistry everything else uses. Third-party runtimes (Strands,
Google ADK, agy) register the same way from a ``matrix.components`` entry point:

    def register(registry):
        registry.register_typed(runtime_type_url("strands"), StrandsConfig, StrandsRuntime)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from matrix.adapters._out.runtime.claude_sdk import ClaudeSdkRuntime, ClaudeSdkRuntimeConfig
    from matrix.adapters._out.runtime.model import ModelAgentRuntime, ModelAgentRuntimeConfig
    from matrix.domain.registry import ComponentRegistry

RUNTIME_NAMESPACE = "matrix.v1/runtime."


def runtime_type_url(runtime_type: str) -> str:
    return f"{RUNTIME_NAMESPACE}{runtime_type}"


def _claude_sdk(config: ClaudeSdkRuntimeConfig) -> ClaudeSdkRuntime:
    from matrix.adapters._out.runtime.claude_sdk import ClaudeSdkRuntime

    return ClaudeSdkRuntime(config)


def _model(config: ModelAgentRuntimeConfig) -> ModelAgentRuntime:
    from matrix.adapters._out.runtime.model import ModelAgentRuntime

    return ModelAgentRuntime.from_config(config)


def register_builtin_runtimes(registry: ComponentRegistry) -> ComponentRegistry:
    """Register claude-sdk, model and mock. Config classes import no optional dependency."""
    from matrix.adapters._out.runtime.claude_sdk import ClaudeSdkRuntimeConfig
    from matrix.adapters._out.runtime.mock import MockRuntime, MockRuntimeConfig
    from matrix.adapters._out.runtime.model import ModelAgentRuntimeConfig

    return (
        registry.register_typed(runtime_type_url("claude-sdk"), ClaudeSdkRuntimeConfig, _claude_sdk)
        .register_typed(runtime_type_url("model"), ModelAgentRuntimeConfig, _model)
        .register_typed(runtime_type_url("mock"), MockRuntimeConfig, MockRuntime)
    )

"""ModelAgentRuntime — an AgentDefinition run as one model call through hardline.

For agents that are a prompt and a model and nothing else: triage, extraction, a judge.
Any model family hardline has a registry row for — a local MLX model, Gemini, Claude.

No tool loop. A definition that declares tools is refused rather than run without them:
the agent defined and the agent measured would differ, and nothing downstream would know.
``max_turns`` is an upper bound, which one call satisfies.

Type URL: ``matrix.v1.runtime.model``. Requires the ``models`` extra (hardline).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from matrix.domain.agent import AgentResponse
from matrix.domain.errors import AgentRuntimeError, ConfigError, RuntimeReason

if TYPE_CHECKING:
    from hardline import ModelRuntime

    from matrix.domain.agent import AgentDefinition


class ModelAgentRuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: A hardline registry section. Composition fills this from the top-level ``matrix.models``
    #: when absent; ``None`` all the way down means hardline discovers its own tiers.
    models: dict[str, Any] | None = None
    #: Used when a definition names no model. ``None`` = the registry's own default.
    default_model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class ModelAgentRuntime:
    def __init__(
        self, runtime: ModelRuntime, config: ModelAgentRuntimeConfig | None = None
    ) -> None:
        self._runtime = runtime
        self._config = config or ModelAgentRuntimeConfig()

    @classmethod
    def from_config(cls, config: ModelAgentRuntimeConfig) -> ModelAgentRuntime:
        try:
            from hardline import build_runtime
        except ImportError as e:
            raise ImportError(
                "the model runtime requires 'hardline'. Install with: uv add 'matrix[models]'"
            ) from e
        runtime = (
            build_runtime(config=config.models, label="matrix.models")
            if config.models is not None
            else build_runtime()
        )
        return cls(runtime, config)

    @property
    def models(self) -> ModelRuntime:
        return self._runtime

    def check(self, definition: AgentDefinition) -> None:
        """Refuse, at binding, a definition this runtime would run as a different agent."""
        if definition.tools:
            raise ConfigError(
                f"agent {definition.name!r} declares tools {list(definition.tools)}, but the "
                "model runtime makes a single call with no tool loop",
                fix="bind it to a claude-sdk runtime, or set tools: [] if it needs none",
            )

    async def run(self, definition: AgentDefinition, task: str) -> AgentResponse:
        self.check(definition)  # run() is reachable without BoundAgent
        from hardline import HardlineError

        try:
            completion = await self._runtime.complete(
                definition.model or self._config.default_model,
                task,
                system=definition.system_prompt or None,
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
            )
        except HardlineError as e:
            raise AgentRuntimeError(
                f"agent {definition.name!r}: {e}", reason=_reason(e), agent=definition.name
            ) from e
        except Exception as e:  # the port promises a response or a classified error
            raise AgentRuntimeError(
                f"agent {definition.name!r}: {type(e).__name__}: {e}",
                reason="failed",
                agent=definition.name,
            ) from e
        return AgentResponse(
            content=completion.text,
            tokens_input=completion.usage.input_tokens,
            tokens_output=completion.usage.output_tokens,
            duration_ms=completion.latency_ms,
            num_turns=1,
            family=completion.family,
            model=completion.name,
            stop="completed",
        )


_BACKEND_REASONS: dict[str, RuntimeReason] = {
    "rate_limit": "rate_limited",
    "timeout": "timeout",
    "unavailable": "unavailable",
    "auth": "auth",
    "bad_request": "incapable",
}


def _reason(error: Exception) -> RuntimeReason:
    """hardline's classification, in the runtime port's vocabulary."""
    from hardline import BackendError, SecretError

    if isinstance(error, BackendError):
        return _BACKEND_REASONS.get(error.reason, "failed")
    if isinstance(error, SecretError):
        return "auth"
    return "failed"

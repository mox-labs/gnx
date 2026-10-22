"""HardlineModelRuntime: one model call through hardline, as a ModelRuntime.

For work that is a prompt and a model and nothing else: triage, extraction, a judge, in any
model family hardline has a registry row for. It is not an agent runtime: a call has no loop,
no tools and no turns.

A request's ``model`` is a name in the hardline registry (``matrix.models``, or hardline's
own tiers); unset, ``default_model``, then the registry's default. ``check`` resolves it when
a step is built, so an unknown name is a ``config`` problem at composition, not a failure
mid-run. With an ``output`` type the call is hardline's ``extract``: the schema is stated to
the model, and a reply that does not fit is repaired within hardline's bound.

The call's record carries what hardline reports of the call that answered: the model that
answered and its family and backend, ``fallback_from`` when a fallback answered, usage with
cache tokens, the request id, attempts, retries and latency. hardline does not report why
the model stopped or whether it refused, so ``finish_reason`` and ``refusal`` are None.
hardline reports tokens, not dollars; bound a reply with ``max_tokens``.

Type URL: ``matrix.v1.model-runtime.hardline``. Requires the ``models`` extra (hardline).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from matrix.domain.errors import ConfigError, ModelRuntimeError, RuntimeReason, missing_extra
from matrix.domain.model_call import ModelCall, TokenUsage

if TYPE_CHECKING:
    from hardline import Completion
    from hardline import ModelRuntime as Hardline

    from matrix.domain.model_call import ModelRequest


class HardlineModelRuntimeConfig(BaseModel):
    """One model call through hardline. A request's `model` on this runtime is a model name
    in the hardline registry (`models`, `matrix.models`, or hardline's own tiers); unset,
    `default_model`, then the registry's default. Calls report tokens, not cost: bound a
    reply with `max_tokens`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: A hardline registry section. Composition fills this from the top-level ``matrix.models``
    #: when absent; ``None`` all the way down means hardline discovers its own tiers.
    models: dict[str, Any] | None = None
    #: Used when a request names no model. ``None`` = the registry's own default.
    default_model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


class HardlineModelRuntime:
    def __init__(self, runtime: Hardline, config: HardlineModelRuntimeConfig | None = None) -> None:
        self._runtime = runtime
        self._config = config or HardlineModelRuntimeConfig()

    @classmethod
    def from_config(cls, config: HardlineModelRuntimeConfig) -> HardlineModelRuntime:
        try:
            from hardline import build_runtime
        except ImportError as e:
            raise missing_extra(
                "models", needed_by="the hardline model runtime", module="hardline"
            ) from e
        runtime = (
            build_runtime(config=config.models, label="matrix.models")
            if config.models is not None
            else build_runtime()
        )
        return cls(runtime, config)

    @property
    def models(self) -> Hardline:
        return self._runtime

    @property
    def resolved(self) -> dict[str, Any]:
        """The registry this runtime resolved, from ``models`` or hardline's own tiers: what
        decides which model answers a name. Keys (``api_key``) are left out."""
        registry = self._runtime.registry
        return {
            "default": registry.default,
            "models": {
                name: registry.get(name).model_dump(mode="json", exclude={"api_key"})
                for name in registry.names()
            },
        }

    def check(self, model: str | None) -> None:
        """Refuse a model name the registry does not resolve: an unknown one, or none where
        the registry has no default."""
        from hardline import UnknownModelError

        try:
            self._runtime.registry.get(model or self._config.default_model)
        except UnknownModelError as e:
            known = ", ".join(self._runtime.registry.names()) or "(none)"
            raise ConfigError(
                f"the hardline model runtime cannot resolve the model: {e}",
                fix=f"name one of its models ({known}), or add a row for it to `matrix.models` "
                "or a hardline config tier",
                got=model,
            ) from e

    async def call(self, request: ModelRequest) -> ModelCall:
        from hardline import HardlineError

        model = request.model or self._config.default_model
        options: dict[str, Any] = {
            "system": request.system,
            "max_tokens": self._config.max_tokens,
            "temperature": self._config.temperature,
        }
        value: Any = None
        try:
            if request.output is None:
                completion = await self._runtime.complete(model, request.prompt, **options)
            else:
                structured = await self._runtime.extract(
                    model, request.prompt, request.output, **options
                )
                completion = structured.completion
                value = structured.value.model_dump(mode="json")
        except HardlineError as e:
            raise ModelRuntimeError(
                f"model {model or '(default)'!r}: {e}",
                reason=_reason(e),
                retry_after_s=getattr(e, "retry_after", None),
                model=model,
            ) from e
        except Exception as e:  # the port promises a call record or a classified error
            raise ModelRuntimeError(
                f"model {model or '(default)'!r}: {type(e).__name__}: {e}",
                reason="failed",
                model=model,
            ) from e
        return _record(completion, value)


def _record(completion: Completion, value: Any) -> ModelCall:
    usage = completion.usage
    return ModelCall(
        text=completion.text,
        value=value,
        name=completion.name,
        model=completion.model,
        family=completion.family,
        backend=completion.backend,
        provider=_PROVIDERS.get(completion.backend),
        usage=TokenUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
        ),
        request_id=completion.request_id,
        attempts=completion.attempts,
        retries=completion.retries,
        fallback_from=completion.fallback_from,
        latency_ms=completion.latency_ms,
    )


#: hardline backends that speak to one provider, by the GenAI conventions' provider name. An
#: OpenAI-compatible backend may front any provider, so it names none.
_PROVIDERS: dict[str, str] = {"anthropic": "anthropic"}

_BACKEND_REASONS: dict[str, RuntimeReason] = {
    "rate_limit": "rate_limited",
    "timeout": "timeout",
    "unavailable": "unavailable",
    "auth": "auth",
    "bad_request": "incapable",
}


def _reason(error: Exception) -> RuntimeReason:
    """hardline's classification, in the runtime port's vocabulary."""
    from hardline import BackendError, SecretError, UnknownModelError

    if isinstance(error, BackendError):
        return _BACKEND_REASONS.get(error.reason, "failed")
    if isinstance(error, SecretError):
        return "auth"
    if isinstance(error, UnknownModelError):  # a name check() would have refused
        return "incapable"
    return "failed"

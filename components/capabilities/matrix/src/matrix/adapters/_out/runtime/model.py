"""HardlineModelRuntime: one model call through hardline, as a ModelRuntime.

For work that is a prompt and a model and nothing else: triage, extraction, a judge, in any
model family hardline has a registry row for. It is not an agent runtime: a call has no loop,
no tools and no turns.

A request's ``model`` is a name in the hardline registry (``matrix.models``, or hardline's
own tiers); unset, ``default_model``, then the registry's default. ``check`` resolves it when
a step is built, so an unknown name is a ``config`` problem at composition, not a failure
mid-run. With an ``output`` type the call is hardline's ``extract``: the schema is stated to
the model, and a reply that does not fit is repaired within hardline's bound.

A caller that runs pydantic-ai itself (an evaluator's judge) asks ``pydantic_ai_model(model)``
for pydantic-ai's ``Model`` on a row this runtime resolves, with its ``max_tokens`` and
``temperature``: the model a caller gets is the one this runtime's config names.

The call's record carries hardline's whole record of it: why the reply ended
(``finish_reason``, the provider's own value, and any refusal), the model that answered and
its family and backend, ``fallback_from`` when a fallback answered, usage with cache tokens
summed over every attempt, the request id, attempts, retries and latency, what was sent
(``params``, ``turns_digest``), every backend call made (``attempt_log``) and the registry
row that answered, with the tier that set each of its fields (``row``). A call that fails
raises a ``ModelRuntimeError`` whose ``call`` holds what hardline's error carried: every
attempt made and the usage they reported. A reply cut at
``max_tokens``, refused or withheld is returned and said for what it is, as hardline returns
it. hardline reports tokens, not dollars; bound a reply with ``max_tokens``.

Type URL: ``matrix.v1.model-runtime.hardline``. Requires the ``models`` extra (hardline).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from matrix.domain.errors import ConfigError, ModelRuntimeError, RuntimeReason, missing_extra
from matrix.domain.model_call import (
    CallAttempt,
    ModelCall,
    RegistryRow,
    RequestParams,
    TokenUsage,
)

if TYPE_CHECKING:
    from hardline import Attempt, Completion, Usage
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

    async def aclose(self) -> None:
        """Close the connections hardline opened in the running event loop. The runtime
        stays usable; a later call opens new ones."""
        await self._runtime.aclose()

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

    def pydantic_ai_model(self, model: str | None = None) -> Any:
        """pydantic-ai's ``Model`` on the row ``model`` names (unset: ``default_model``, then
        the registry's default), its requests made through this runtime's hardline with this
        runtime's ``max_tokens`` and ``temperature`` as defaults. Its ``aclose()`` closes the
        connections its calls opened in the running event loop. Raises ``ConfigError`` for a
        name the registry does not resolve, as :meth:`check` does."""
        from matrix.adapters._out.runtime.pydantic_ai import hardline_model

        self.check(model)
        given = {"max_tokens": self._config.max_tokens, "temperature": self._config.temperature}
        settings = {key: value for key, value in given.items() if value is not None}
        return hardline_model(self._runtime, model or self._config.default_model, settings=settings)

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
                call=_failed(e),
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
    params = completion.params
    row = completion.row
    return ModelCall(
        text=completion.text,
        value=value,
        finish_reason=completion.finish_reason,
        native_finish_reason=completion.native_finish_reason,
        refusal=completion.refusal,
        name=completion.name,
        model=completion.model,
        family=completion.family,
        backend=completion.backend,
        provider=_PROVIDERS.get(completion.backend),
        usage=_usage(completion.usage),
        request_id=completion.request_id,
        attempts=completion.attempts,
        retries=completion.retries,
        fallback_from=completion.fallback_from,
        latency_ms=completion.latency_ms,
        # Field by field, as the design record says: a field hardline adds (the tools a call
        # offered, which a model call never does) is not one matrix's record refuses.
        params=None
        if params is None
        else RequestParams(
            max_tokens=params.max_tokens,
            temperature=params.temperature,
            structured=params.structured,
        ),
        attempt_log=tuple(_attempt(attempt) for attempt in completion.attempt_log),
        turns_digest=completion.turns_digest,
        row=None if row is None else RegistryRow(digest=row.digest, rungs=dict(row.rungs)),
    )


def _failed(error: Exception) -> ModelCall | None:
    """What hardline's error says of a call that failed: every attempt made and the usage
    they reported (a ``BackendError`` or ``SchemaError`` the runtime raised). None for an
    error raised before anything was sent."""
    log: tuple[Attempt, ...] = getattr(error, "attempt_log", ())
    usage: Usage | None = getattr(error, "usage", None)
    if not log and usage is None:
        return None
    attempts = getattr(error, "attempts", None)  # a SchemaError's schema attempts
    return ModelCall(
        usage=None if usage is None else _usage(usage),
        attempts=attempts if isinstance(attempts, int) else None,
        attempt_log=tuple(_attempt(attempt) for attempt in log),
    )


def _attempt(attempt: Attempt) -> CallAttempt:
    return CallAttempt(
        name=attempt.name,
        reason=attempt.reason,
        usage=None if attempt.usage is None else _usage(attempt.usage),
        latency_ms=attempt.latency_ms,
    )


def _usage(usage: Usage) -> TokenUsage:
    return TokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
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

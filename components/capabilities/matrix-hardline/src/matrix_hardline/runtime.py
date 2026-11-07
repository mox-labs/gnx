"""hardline as a matrix model runtime: the one module that knows both.

matrix depends on no model runtime, and hardline imports nothing of matrix (owner ruling HO1,
2026-11-06). This module is the bridge between them: it implements matrix's ``ModelRuntime``
port on a hardline runtime, and registers it from this distribution's ``matrix.extensions``
entry point, as any third party's model runtime registers. It is the only module that imports
hardline and matrix both (``layers.json``).

**The call.** A request's turns, tools and overrides become one hardline ``complete``; with an
``output`` type, one ``extract``, whose schema is stated to the model and whose reply is
repaired within the row's ``schema_retries``. A request's ``model`` is a name in the hardline
registry this runtime resolved (its config's ``models``, else hardline's own tiers); unset,
``default_model``, then the registry's default. ``max_tokens`` and ``temperature`` are the
request's, else this runtime's config's, else the row's.

**The reply** is hardline's ``Completion`` mapped onto matrix's neutral ``ModelCall``: text,
tool calls, finish reason and the provider's own, refusal, the row's name, model, family and
provider, usage and request id, latency. Everything only hardline knows of the call (its
backend, attempts, retries, fallback, what it sent and the row as configured) is hardline's
own record, ``CallRecord``, carried whole in ``provenance`` under ``hardline.v1.call``, whose
schema this module registers. A call that fails carries what hardline's error said of it,
under ``matrix-hardline.v1.failed-call`` (:class:`FailedCall`), with its usage.

**One mapping of failures** (:func:`reason`): a provider's rate limit, timeout or outage, a
bad key, a malformed request, a model name hardline cannot resolve, a part it cannot carry,
each in the port's ``reason`` vocabulary, the same wherever a hardline error is met.

**What it refuses at check** (``check(model, uses=...)``): a model the registry does not
resolve; tools on a row, or a fallback of it, whose backend cannot call them (hardline's
``check_tools``); and tools with an ``output`` type together, since hardline's ``extract``
offers no tools.

**Warnings** hardline finds in the registry it resolved (a fallback sharing its primary's fate)
are the runtime's ``warnings``, so matrix's plan shows them.

Type URL: ``matrix-hardline.v1.model-runtime``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import hardline
from hardline import CALL_TYPE_URL, Attempt, CallRecord
from matrix import (
    ConfigError,
    ModelCall,
    ModelRuntimeError,
    Problem,
    TokenUsage,
    ToolCall,
)
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from collections.abc import Mapping

    from matrix import FinishReason, ModelRequest, Registry, RuntimeReason, Turn, Use

#: This runtime's type URL, under this distribution's own namespace.
TYPE_URL = "matrix-hardline.v1.model-runtime"
#: What a failed call's ``provenance`` holds: :class:`FailedCall`.
FAILED_CALL_TYPE_URL: Literal["matrix-hardline.v1.failed-call"] = "matrix-hardline.v1.failed-call"
#: The label hardline gives the registry a config holds, the tier its rows' rungs name.
REGISTRY_LABEL = "matrix-hardline models"


class HardlineModelRuntimeConfig(BaseModel):
    """One model call through hardline. A request's `model` on this runtime is a model name
    in the hardline registry (`models`, else hardline's own tiers: `~/.hardline/config.yaml`,
    `./hardline.yaml`, `$HARDLINE_CONFIG`); unset, `default_model`, then the registry's
    default. `max_tokens` and `temperature` are used where a request gives none, and the row's
    where neither does. Calls report tokens, not cost: bound a reply with `max_tokens`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: A hardline registry section (``{default?, models: {...}}``). ``None``: hardline
    #: discovers its own tiers.
    models: dict[str, Any] | None = None
    #: Used when a request names no model. ``None`` = the registry's own default.
    default_model: str | None = None
    temperature: float | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, gt=0)


class FailedCall(BaseModel):
    """What hardline's error said of a call that failed: every backend call it made, with why
    each failed and the usage it reported, the schema attempts of an ``extract``, and which of
    the row's ``limits`` stopped it. Its usage is the call's own ``usage``."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    type_url: Literal["matrix-hardline.v1.failed-call"] = FAILED_CALL_TYPE_URL
    #: The schema attempts an ``extract`` made; None for a ``complete``.
    attempts: int | None = None
    attempt_log: tuple[Attempt, ...] = ()
    #: The ``limits`` key that stopped the call; None when it ran out of retries and fallbacks.
    limit: str | None = None


class HardlineModelRuntime:
    """matrix's ``ModelRuntime`` port on a hardline runtime."""

    def __init__(
        self, runtime: hardline.ModelRuntime, config: HardlineModelRuntimeConfig | None = None
    ) -> None:
        self._runtime = runtime
        self._config = config or HardlineModelRuntimeConfig()

    @classmethod
    def from_config(cls, config: HardlineModelRuntimeConfig) -> HardlineModelRuntime:
        """The runtime on the registry ``config.models`` holds, or on the tiers hardline
        discovers. A ConfigError with each problem when hardline refuses the registry: one in
        ``models`` at ``models.<path>``, one in a tier file with its path and the file."""
        return cls(build(config.models), config)

    @property
    def hardline(self) -> hardline.ModelRuntime:
        return self._runtime

    @property
    def warnings(self) -> tuple[Problem, ...]:
        """What hardline found in the registry that is legal but likely unmeant (a fallback
        that shares its primary's fate), for the plan to show."""
        given = self._config.models is not None
        return tuple(_problem(found, given=given) for found in self._runtime.warnings)

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

    async def aclose(self) -> None:
        """Close the connections hardline opened in the running event loop. The runtime
        stays usable; a later call opens new ones."""
        await self._runtime.aclose()

    def check(self, model: str | None, *, uses: frozenset[Use] = frozenset()) -> None:
        """Refuse a model name the registry does not resolve (an unknown one, or none where
        the registry has no default); tools on a row, or a fallback of it, whose backend
        cannot call them; and tools with an output structure together."""
        name = model or self._config.default_model
        try:
            self._runtime.registry.get(name)
        except hardline.UnknownModelError as e:
            known = ", ".join(self._runtime.registry.names()) or "(none)"
            raise ConfigError(
                f"the hardline model runtime cannot resolve the model: {e}",
                fix=f"name one of its models ({known}), or add a row for it to the runtime's "
                "`models` or a hardline config tier",
                got=model,
            ) from e
        if {"tools", "output"} <= uses:
            raise ConfigError(
                f"model {name or '(default)'!r}: hardline cannot offer tools and ask for an "
                "output structure in one call (its extract takes no tools)",
                fix="ask for the structure in a call that offers no tools, or take the reply as "
                "text and read it yourself",
                got=model,
            )
        if "tools" in uses:
            try:
                self._runtime.check_tools(name)
            except hardline.HardlineError as e:
                raise ConfigError(str(e), fix=e.fix or "", got=model) from e

    async def call(self, request: ModelRequest) -> ModelCall:
        """One hardline call for ``request``: its reply, or a ``ModelRuntimeError`` with what
        hardline said of the failure."""
        model = request.model or self._config.default_model
        if request.output is not None and "tools" in request.uses:
            raise ModelRuntimeError(
                f"model {model or '(default)'!r}: hardline cannot offer tools and ask for an "
                "output structure in one call",
                reason="incapable",
                fix="ask for the structure in a call that offers no tools",
                model=model,
            )
        messages = [_message(turn) for turn in request.turns]
        max_tokens = request.max_tokens or self._config.max_tokens
        temperature = (
            request.temperature if request.temperature is not None else self._config.temperature
        )
        value: Any = None
        try:
            if request.output is None:
                completion = await self._runtime.complete(
                    model,
                    messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tools=[
                        hardline.ToolSpec(
                            name=tool.name, description=tool.description, parameters=tool.parameters
                        )
                        for tool in request.tools
                    ],
                )
            else:
                structured = await self._runtime.extract(
                    model,
                    messages,
                    request.output,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                completion = structured.completion
                value = structured.value.model_dump(mode="json")
        except Exception as e:  # the port promises a reply or a classified error
            raise classified(model, e) from e
        return reply(completion, value)


def build(models: Mapping[str, Any] | None) -> hardline.ModelRuntime:
    """hardline's runtime on the registry section ``models``, or on the tiers hardline
    discovers when it is None. Raises a matrix ConfigError when hardline refuses it: a
    problem in ``models`` is at ``models.<path>``, relative to the runtime's config, with no
    source, so whoever built the runtime places it in its document; a problem in a tier file
    keeps its path and the file."""
    try:
        if models is None:
            return hardline.build_runtime()
        return hardline.build_runtime(config=models, label=REGISTRY_LABEL)
    except hardline.HardlineError as e:
        given = models is not None
        found = [_problem(p, given=given) for p in e.problems] or [
            Problem(
                path="models" if given else "",
                kind="invalid_value",
                message=str(e),
                fix=e.fix or "",
            )
        ]
        raise ConfigError(
            "the hardline model registry cannot be loaded",
            fix=e.fix or "fix the registry as each problem says; `hardline models` lists it",
            problems=found,
        ) from None


def reply(completion: hardline.Completion, value: Any = None) -> ModelCall:
    """hardline's reply as matrix's ``ModelCall``, hardline's record whole in its
    ``provenance`` under ``hardline.v1.call``."""
    return ModelCall(
        text=completion.text,
        value=value,
        tool_calls=tuple(
            ToolCall(id=call.id, name=call.name, arguments=call.arguments)
            for call in completion.tool_calls
        ),
        finish_reason=_finish(completion.finish_reason),
        native_finish_reason=completion.native_finish_reason,
        refusal=completion.refusal,
        name=completion.name,
        model=completion.model,
        family=completion.family,
        provider=completion.provider,
        usage=_usage(completion.usage),
        request_id=completion.request_id,
        latency_ms=completion.latency_ms,
        provenance={CALL_TYPE_URL: completion.record.model_dump(mode="json")},
    )


def classified(model: str | None, error: BaseException) -> ModelRuntimeError:
    """``error``, raised by a call on ``model``, in the port's vocabulary (:func:`reason`): a
    hardline error keeps its Retry-After and what it said of the call; anything else is
    ``failed``."""
    if not isinstance(error, hardline.HardlineError):
        return ModelRuntimeError(
            f"model {model or '(default)'!r}: {type(error).__name__}: {error}",
            reason="failed",
            model=model,
        )
    retry_after = error.retry_after if isinstance(error, hardline.BackendError) else None
    return ModelRuntimeError(
        f"model {model or '(default)'!r}: {error}",
        reason=reason(error),
        fix=error.fix,
        retry_after_s=retry_after,
        call=_failed(error),
        model=model,
    )


#: hardline's backend reasons in the port's vocabulary.
_BACKEND_REASONS: dict[str, RuntimeReason] = {
    "rate_limit": "rate_limited",
    "timeout": "timeout",
    "unavailable": "unavailable",
    "auth": "auth",
    "bad_request": "incapable",
}


def reason(error: hardline.HardlineError) -> RuntimeReason:
    """hardline's classification of ``error``, in the port's vocabulary: the one mapping."""
    if isinstance(error, hardline.BackendError):
        return _BACKEND_REASONS.get(error.reason, "failed")
    if isinstance(error, hardline.SecretError):
        return "auth"
    if isinstance(error, hardline.UnknownModelError | hardline.ConfigError):
        return "incapable"  # what check() refuses: a name, or a part, the row cannot serve
    return "failed"  # a SchemaError, a backend that broke its contract


def _failed(error: hardline.HardlineError) -> ModelCall | None:
    """What hardline's error said of a call that failed (a ``BackendError`` or a
    ``SchemaError`` the runtime raised): its usage, and :class:`FailedCall`. None for an
    error raised before anything was sent."""
    if isinstance(error, hardline.SchemaError):
        attempts: int | None = error.attempts
    elif isinstance(error, hardline.BackendError):
        attempts = None
    else:
        return None
    if not error.attempt_log and error.usage is None:
        return None
    record = FailedCall(attempts=attempts, attempt_log=error.attempt_log, limit=error.limit)
    return ModelCall(
        usage=None if error.usage is None else _usage(error.usage),
        provenance={FAILED_CALL_TYPE_URL: record.model_dump(mode="json")},
    )


def _message(turn: Turn) -> hardline.Message:
    return hardline.Message(
        role=turn.role,
        content=turn.content,
        tool_calls=tuple(
            hardline.ToolCall(id=call.id, name=call.name, arguments=call.arguments)
            for call in turn.tool_calls
        ),
        tool_call_id=turn.tool_call_id,
        is_error=turn.is_error,
    )


def _usage(usage: hardline.Usage) -> TokenUsage:
    return TokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
    )


def _problem(found: hardline.Problem, *, given: bool) -> Problem:
    """hardline's problem as matrix's, at ``models.<path>`` when the registry was given in the
    runtime's config, else at its path in the tier file that holds it."""
    return Problem(
        path=f"models.{found.path}" if given else found.path,
        kind=found.kind,
        message=found.message,
        fix=found.fix or "",
        allowed=found.allowed,
        got=found.got,
        source="" if given else found.source or "",
    )


#: hardline's finish reasons in matrix's vocabulary: the same words, mapped one by one so a
#: word either adds is a type error here, not a value passed through.
_FINISH: dict[hardline.FinishReason, FinishReason] = {
    "stop": "stop",
    "length": "length",
    "content_filter": "content_filter",
    "refusal": "refusal",
    "tool_call": "tool_call",
    "other": "other",
}


def _finish(reason: hardline.FinishReason | None) -> FinishReason | None:
    return None if reason is None else _FINISH[reason]


def _build(config: HardlineModelRuntimeConfig) -> HardlineModelRuntime:
    return HardlineModelRuntime.from_config(config)


def register(registry: Registry) -> None:
    """This distribution's ``matrix.extensions`` entry point: the model runtime, hardline's
    record of a call, and what a failed call carries."""
    registry.register(
        "model-runtime",
        TYPE_URL,
        _build,
        config=HardlineModelRuntimeConfig,
        summary="One model call through hardline, in any model family it has a registry row for",
        drives=("hardline",),
    )
    registry.register_payload(CALL_TYPE_URL, CallRecord)
    registry.register_payload(FAILED_CALL_TYPE_URL, FailedCall)

"""ModelRuntime — resolve, call, retry, fall back, validate, tag. The behaviour hardline owns.

Everything a provider library does on the wire (HTTP, auth headers) stays in the backend.
What is policy lives here, once: a registry lookup that fails with the legal names; retries
of transient failures with exponential backoff and jitter; fallback to other registry rows
when a model stays unavailable; a contract check on what the backend returned; schema
validation with bounded repair; and a Completion carrying the family, usage and latency of
the model that actually answered.

Retry and fallback are separate mechanisms, as in most model libraries: a retry asks the
same model again after a transient failure; a fallback asks a different model once retries
are spent. A non-transient failure (bad key, malformed request) is neither retried nor
fallen back from — it is raised, because both would hide a fault someone has to fix.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel, ValidationError

from hardline.domain.errors import BackendError, ConfigError, ContractError, SchemaError
from hardline.domain.structured import extract_json, retry_instruction, schema_instruction
from hardline.domain.types import (
    Completion,
    Message,
    ModelSpec,
    RawCompletion,
    Request,
    Structured,
    Usage,
)

if TYPE_CHECKING:
    from hardline.domain.ports._out.backend import ModelBackend
    from hardline.domain.ports._out.secrets import SecretResolver
    from hardline.domain.registry import ModelRegistry

T = TypeVar("T", bound=BaseModel)

Messages = str | Sequence[Message]

logger = logging.getLogger("hardline")

_BACKOFF_BASE_S = 0.5
_BACKOFF_CAP_S = 8.0
_RETRY_AFTER_CAP_S = 60.0


@dataclass(frozen=True)
class _Answer:
    raw: RawCompletion
    spec: ModelSpec  # the model that answered
    retries: int
    usage: Usage  # every call this answer cost, failed ones included where reported


def _as_messages(messages: Messages, system: str | None) -> tuple[Message, ...]:
    turns = (
        [Message(role="user", content=messages)] if isinstance(messages, str) else list(messages)
    )
    if system is not None:
        turns.insert(0, Message(role="system", content=system))
    if not turns:
        raise ValueError("at least one message is required")
    return tuple(turns)


class ModelRuntime:
    """Messages in; text or a validated object out; family, usage and latency attached."""

    def __init__(
        self,
        registry: ModelRegistry,
        backends: Mapping[str, ModelBackend],
        secrets: SecretResolver,
        *,
        schema_retries: int = 1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        problems = []
        for spec in registry:
            for fallback in spec.fallbacks:
                if fallback == spec.name or fallback not in registry:
                    problems.append(
                        f"  models.{spec.name}.fallbacks: {fallback!r} is "
                        + ("the model itself" if fallback == spec.name else "not in the registry")
                        + f". Known: {', '.join(registry.names())}"
                    )
            backend = backends.get(spec.backend)
            if backend is None:
                problems.append(
                    f"  models.{spec.name}.backend: {spec.backend!r} is not an installed "
                    f"backend. Installed: {', '.join(sorted(backends)) or '(none)'}"
                )
            elif spec.structured not in backend.structured_modes:
                problems.append(
                    f"  models.{spec.name}.structured: {spec.structured!r} is not supported "
                    f"by backend {spec.backend!r}. Supported: "
                    f"{', '.join(sorted(backend.structured_modes))}"
                )
        if problems:
            raise ConfigError(
                "model registry does not match installed backends:\n" + "\n".join(problems)
            )
        if schema_retries < 0:
            raise ValueError("schema_retries must be >= 0")
        self._registry = registry
        self._backends = dict(backends)
        self._secrets = secrets
        self._schema_retries = schema_retries
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()

    @property
    def registry(self) -> ModelRegistry:
        return self._registry

    async def complete(
        self,
        model: str | None,
        messages: Messages,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Completion:
        """One call, text out. ``model=None`` uses the registry default."""
        spec = self._registry.get(model)
        turns = _as_messages(messages, system)
        started = self._clock()
        answer = await self._call(
            spec, lambda s: self._request(s, turns, None, max_tokens, temperature)
        )
        return self._completion(
            spec, answer, answer.usage, attempts=1, retries=answer.retries, started=started
        )

    async def extract(
        self,
        model: str | None,
        messages: Messages,
        output: type[T],
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Structured[T]:
        """Structured output validated against ``output``, with bounded repair.

        The schema is always stated in an instruction, and additionally handed to the
        provider when the registry row asks for ``json_object`` or ``json_schema``. The
        response is validated here either way — a provider's "structured" mode is an
        optimisation, never the guarantee.
        """
        spec = self._registry.get(model)
        schema = output.model_json_schema()
        turns = list(_as_messages(messages, system))
        turns.append(Message(role="user", content=schema_instruction(schema)))

        started = self._clock()
        usage = Usage()
        retries = 0
        last_text = ""
        last_error = ""
        attempts = 0
        for _ in range(self._schema_retries + 1):
            attempts += 1
            sent = tuple(turns)

            def build(s: ModelSpec, sent: tuple[Message, ...] = sent) -> Request:
                return self._request(s, sent, schema, max_tokens, temperature)

            answer = await self._call(spec, build)
            usage = usage + answer.usage
            retries += answer.retries
            last_text = answer.raw.text
            try:
                value = output.model_validate_json(extract_json(answer.raw.text))
            except (ValueError, ValidationError) as e:
                last_error = str(e)
                turns.append(Message(role="assistant", content=answer.raw.text))
                turns.append(Message(role="user", content=retry_instruction(last_error)))
                continue
            completion = self._completion(
                spec, answer, usage, attempts=attempts, retries=retries, started=started
            )
            return Structured(value=value, completion=completion)

        raise SchemaError(
            f"model {spec.name!r} did not produce a valid {output.__name__} "
            f"after {attempts} attempt(s): {last_error}",
            last_text=last_text,
            validation=last_error,
            attempts=attempts,
        )

    # --- internals -------------------------------------------------------------------

    def _request(
        self,
        spec: ModelSpec,
        turns: tuple[Message, ...],
        schema: dict[str, object] | None,
        max_tokens: int | None,
        temperature: float | None,
    ) -> Request:
        return Request(
            messages=turns,
            json_schema=schema,
            max_tokens=max_tokens if max_tokens is not None else spec.max_tokens,
            temperature=temperature if temperature is not None else spec.temperature,
        )

    async def _call(self, spec: ModelSpec, build: Callable[[ModelSpec], Request]) -> _Answer:
        """One logical call: retries on ``spec``, then each fallback, until one answers."""
        retries = 0
        last: BackendError | None = None
        chain = [spec, *(self._registry.get(name) for name in spec.fallbacks)]
        for candidate in chain:
            request = build(candidate)
            for attempt in range(candidate.retries + 1):
                try:
                    raw = await self._attempt(candidate, request)
                except BackendError as e:
                    if not e.retryable:
                        raise
                    last = e
                    if attempt < candidate.retries:
                        retries += 1
                        delay = self._delay(attempt, e.retry_after)
                        logger.info(
                            "model %r: %s; retry %d/%d in %.2fs",
                            candidate.name,
                            e.reason,
                            attempt + 1,
                            candidate.retries,
                            delay,
                        )
                        await self._sleep(delay)
                    continue
                return _Answer(raw=raw, spec=candidate, retries=retries, usage=raw.usage)
            if candidate is not chain[-1]:
                logger.warning(
                    "model %r still failing (%s) after %d retries; falling back",
                    candidate.name,
                    last.reason if last else "?",
                    candidate.retries,
                )
        assert last is not None  # the loop only falls through after a retryable failure
        tried = ", ".join(c.name for c in chain)
        raise BackendError(
            f"model {spec.name!r}: transient failures exhausted retries"
            + (f" and fallbacks ({tried})" if len(chain) > 1 else "")
            + f". Last: {last}",
            reason=last.reason,
        ) from last

    def _delay(self, attempt: int, retry_after: float | None) -> float:
        """Full-jitter exponential backoff; a provider's Retry-After, when given, wins."""
        if retry_after is not None:
            return min(retry_after, _RETRY_AFTER_CAP_S)
        return self._rng.uniform(0, min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * 2**attempt))

    async def _attempt(self, spec: ModelSpec, request: Request) -> RawCompletion:
        backend = self._backends[spec.backend]
        api_key = self._secrets.resolve(spec.api_key) if spec.api_key else None
        raw = await backend.complete(spec, request, api_key)
        if not isinstance(raw, RawCompletion):
            raise ContractError(
                f"backend {spec.backend!r} returned {type(raw).__name__}, "
                "not RawCompletion — the ModelBackend port's declared output"
            )
        return raw

    def _completion(
        self,
        requested: ModelSpec,
        answer: _Answer,
        usage: Usage,
        *,
        attempts: int,
        retries: int,
        started: float,
    ) -> Completion:
        spec, raw = answer.spec, answer.raw
        return Completion(
            name=spec.name,
            text=raw.text,
            family=spec.family,
            model=raw.model or spec.model,
            backend=spec.backend,
            local=spec.local,
            usage=usage,
            request_id=raw.request_id,
            latency_ms=int((self._clock() - started) * 1000),
            attempts=attempts,
            retries=retries,
            fallback_from=requested.name if spec.name != requested.name else None,
            raw=raw.raw,
        )

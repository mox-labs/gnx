"""ModelRuntime — resolve, call, validate, tag. The only behaviour hardline owns.

Everything a provider library already does (HTTP, auth headers, transport retries) stays
in the backend. What no provider library does is here: a registry lookup that fails with
the legal names, a contract check on what the backend returned, schema validation with
one repair attempt, and a Completion that carries family, usage and latency so a caller
can ledger the call and an eval harness can tell whether two calls were out of family.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel, ValidationError

from hardline.domain.errors import ConfigError, ContractError, SchemaError
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
    ) -> None:
        problems = []
        for spec in registry:
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
        raw = await self._attempt(spec, self._request(spec, turns, None, max_tokens, temperature))
        return self._completion(spec, raw, raw.usage, attempts=1, started=started)

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
        last_text = ""
        last_error = ""
        attempts = 0
        for _ in range(self._schema_retries + 1):
            attempts += 1
            request = self._request(spec, tuple(turns), schema, max_tokens, temperature)
            raw = await self._attempt(spec, request)
            usage = usage + raw.usage
            last_text = raw.text
            try:
                value = output.model_validate_json(extract_json(raw.text))
            except (ValueError, ValidationError) as e:
                last_error = str(e)
                turns.append(Message(role="assistant", content=raw.text))
                turns.append(Message(role="user", content=retry_instruction(last_error)))
                continue
            completion = self._completion(spec, raw, usage, attempts=attempts, started=started)
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
        self, spec: ModelSpec, raw: RawCompletion, usage: Usage, *, attempts: int, started: float
    ) -> Completion:
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
            raw=raw.raw,
        )

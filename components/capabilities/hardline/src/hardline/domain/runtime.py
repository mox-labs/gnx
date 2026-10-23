"""ModelRuntime — resolve, call, retry, fall back, validate, tag. The behaviour hardline owns.

Everything a provider library does on the wire (HTTP, auth headers) stays in the backend.
What is policy lives here, once: a registry lookup that fails with the legal names; retries
of transient failures with exponential backoff and jitter; fallback to other registry rows
when a model stays unavailable; a contract check on what the backend returned; schema
validation with bounded repair; the call's own limits on attempts, wall time and tokens
across all of these; and a Completion carrying the family, usage and latency of the model that
actually answered.

Retry and fallback are separate mechanisms, as in most model libraries: a retry asks the
same model again after a transient failure; a fallback asks a different model once retries
are spent. A non-transient failure (bad key, malformed request) is neither retried nor
fallen back from — it is raised, because both would hide a fault someone has to fix.

Connections belong to the event loop that opened them. A runtime may be called from more than
one loop (each ``asyncio.run``); ``aclose()``, or leaving ``async with runtime``, closes the
running loop's connections before that loop ends, and the runtime stays usable.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, TypeVar

from pydantic import BaseModel, ValidationError

from hardline.domain.digests import turns_digest
from hardline.domain.errors import (
    BackendError,
    ConfigError,
    ContractError,
    SchemaError,
    SecretError,
)
from hardline.domain.ports._out.backend import CheckedBackend, ClosableBackend
from hardline.domain.structured import (
    EMPTY_REPLY,
    empty_reply_instruction,
    json_candidates,
    retry_instruction,
    schema_instruction,
)
from hardline.domain.types import (
    Answered,
    Attempt,
    AttemptFailed,
    CallBound,
    CallEvent,
    Completion,
    FellBack,
    Message,
    ModelSpec,
    Problem,
    RawCompletion,
    Request,
    RequestParams,
    Structured,
    ToolSpec,
    Usage,
)

if TYPE_CHECKING:
    from types import TracebackType

    from hardline.domain.ports._out.backend import ModelBackend
    from hardline.domain.ports._out.observer import CallObserver
    from hardline.domain.ports._out.secrets import SecretResolver
    from hardline.domain.registry import ModelRegistry
    from hardline.domain.types import Limit

T = TypeVar("T", bound=BaseModel)

Messages = str | Sequence[Message]

logger = logging.getLogger("hardline")

# Backoff is full jitter (a wait drawn from [0, min(cap, base * 2**attempt)]), which spreads
# concurrent callers that failed together instead of sending them back together. The numbers are
# fixed, where `retries` is per row, because they describe providers, not rows:
#: A rate limit or an overload commonly clears within a second, so the first wait is short.
_BACKOFF_BASE_S = 0.5
#: Doubling stops at 8 s: with `retries` at most 10, one row's waits stay under a minute
#: (at most 0.5 + 1 + 2 + 4 + 6 x 8 = 55.5 s) before its fallbacks are tried.
_BACKOFF_CAP_S = 8.0
#: A provider's Retry-After is honoured up to a minute. One asking for longer is better reported
#: to the caller as transient, with its hint, than waited out inside a single call; a row's
#: `limits.seconds` bounds every wait regardless.
_RETRY_AFTER_CAP_S = 60.0


class _Unobserved:
    """The default observer: hears nothing."""

    def observe(self, event: CallEvent) -> None:
        pass


@dataclass(frozen=True)
class _Answer:
    raw: RawCompletion
    spec: ModelSpec  # the model that answered
    request: Request  # what it was sent
    retries: int
    position: int  # where in the requested row's chain the answering row is


def _total(log: Sequence[Attempt]) -> Usage:
    """The usage the calls in ``log`` reported, summed. A failed call reported none."""
    total = Usage()
    for attempt in log:
        if attempt.usage is not None:
            total = total + attempt.usage
    return total


def _logged[E: BackendError](error: E, log: Sequence[Attempt]) -> E:
    """``error`` carrying the calls made so far and what they cost, for the caller to read."""
    error.attempt_log = tuple(log)
    error.usage = _total(log)
    return error


def _as_messages(messages: Messages, system: str | None) -> tuple[Message, ...]:
    turns = (
        [Message(role="user", content=messages)] if isinstance(messages, str) else list(messages)
    )
    if system is not None:
        turns.insert(0, Message(role="system", content=system))
    if not turns:
        raise ConfigError("at least one message is required")
    return tuple(turns)


def _shared_fate(registry: ModelRegistry) -> list[Problem]:
    """A warning for each fallback that sends where its primary sends: the same backend and
    the same ``base_url``. When that server is down, the fallback fails with its primary, so it
    guards against a failing model, not against the server going down. Rows with no location
    (the mock) share no server."""
    found = []
    for spec in registry:
        for name in spec.fallbacks:
            fallback = registry.get(name)
            if (fallback.backend, fallback.base_url) == (spec.backend, spec.base_url) and (
                spec.base_url is not None
            ):
                found.append(
                    Problem(
                        path=f"models.{spec.name}.fallbacks",
                        kind="shared_fate",
                        message=f"fallback {name!r} sends where {spec.name!r} does "
                        f"({spec.backend} → {spec.base_url}): when that server is down, both fail",
                        fix="name a fallback on another server or backend, or keep this one "
                        "knowing it guards against a failing model only",
                        got=name,
                    )
                )
    return found


def _uses_tools(turns: Sequence[Message], tools: Sequence[ToolSpec]) -> bool:
    """Whether a call offers tools or sends a turn that only a backend that can call tools can
    send: an assistant turn with tool calls, or a tool's result."""
    return bool(tools) or any(turn.role == "tool" or turn.tool_calls for turn in turns)


def _check_overrides(max_tokens: int | None, temperature: float | None) -> None:
    """The call's overrides, held to a registry row's bounds before anything is sent."""
    if max_tokens is not None and max_tokens <= 0:
        raise ConfigError(f"max_tokens must be > 0, got {max_tokens}")
    if temperature is not None and not temperature >= 0:  # also refuses nan
        raise ConfigError(f"temperature must be >= 0, got {temperature}")


def _validate[M: BaseModel](output: type[M], text: str) -> M:
    """The first JSON candidate in ``text`` that validates as ``output``.

    Bracketed prose can itself be JSON ("see [1]"), so each candidate is tried in turn. When
    none validates, the error is the longest candidate's: the answer is usually the largest
    value in the reply, and its error is the one worth showing the model.
    """
    candidates = json_candidates(text)
    if not candidates:
        raise ValueError("no JSON object or array found in the response")
    errors: dict[str, ValidationError] = {}
    for candidate in candidates:
        try:
            return output.model_validate_json(candidate)
        except ValidationError as e:
            errors[candidate] = e
    raise errors[max(candidates, key=len)]


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
        observer: CallObserver | None = None,
    ) -> None:
        problems: list[Problem] = []
        for spec in registry:
            row = f"models.{spec.name}"
            for fallback in spec.fallbacks:
                if fallback == spec.name or fallback not in registry:
                    itself = fallback == spec.name
                    problems.append(
                        Problem(
                            path=f"{row}.fallbacks",
                            kind="invalid_value" if itself else "name_unknown",
                            message=f"{fallback!r} is "
                            + ("the model itself" if itself else "not in the registry")
                            + f". Known: {', '.join(registry.names())}",
                            allowed=() if itself else registry.names(),
                            got=fallback,
                        )
                    )
            backend = backends.get(spec.backend)
            if backend is None:
                problems.append(
                    Problem(
                        path=f"{row}.backend",
                        kind="name_unknown",
                        message=f"{spec.backend!r} is not an installed backend. "
                        f"Installed: {', '.join(sorted(backends)) or '(none)'}",
                        allowed=tuple(sorted(backends)),
                        got=spec.backend,
                    )
                )
                continue
            if isinstance(backend, CheckedBackend):
                for found in backend.check(spec.endpoint()):
                    key, _, message = found.partition(": ")
                    problems.append(
                        Problem(path=f"{row}.{key}", kind="invalid_value", message=message)
                    )
            supported = backend.structured_modes
            if spec.structured not in supported:
                problems.append(
                    Problem(
                        path=f"{row}.structured",
                        kind="invalid_value",
                        message=f"{spec.structured!r} is not supported by backend "
                        f"{spec.backend!r}. Supported: {', '.join(sorted(supported))}",
                        allowed=tuple(sorted(supported)),
                        got=spec.structured,
                    )
                )
        if problems:
            raise ConfigError(
                "model registry does not match installed backends:\n"
                + "\n".join(f"  {p.path}: {p.message}" for p in problems),
                problems=problems,
            )
        if schema_retries < 0:
            raise ConfigError(f"schema_retries must be >= 0, got {schema_retries}")
        self._warnings = tuple(_shared_fate(registry))
        for warning in self._warnings:
            # ``problem`` lets a host that reports ``warnings`` itself (the CLI) drop the record.
            logger.warning("%s: %s", warning.path, warning.message, extra={"problem": warning})
        self._registry = registry
        self._backends = dict(backends)
        self._secrets = secrets
        self._schema_retries = schema_retries
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._observer: CallObserver = observer or _Unobserved()

    @property
    def registry(self) -> ModelRegistry:
        return self._registry

    @property
    def warnings(self) -> tuple[Problem, ...]:
        """What is legal in the registry but likely not what was meant: each fallback that
        shares its primary's server (``shared_fate``)."""
        return self._warnings

    async def aclose(self) -> None:
        """Close the connections the backends opened in the running event loop.

        Call it, or leave ``async with runtime``, before that loop ends: a connection can be
        closed only in its own loop. The runtime stays usable; a later call opens new ones.
        """
        for backend in self._backends.values():
            if isinstance(backend, ClosableBackend):
                await backend.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    def secret_problems(self) -> dict[str, str]:
        """Rows whose ``api_key`` reference does not resolve now, as name -> message.

        Resolves through the same resolver a call would use, so a row that passes here will
        not fail on its key's *existence* at call time (the provider may still reject it).
        The resolved values are discarded; the messages name the reference, never the value.
        """
        problems: dict[str, str] = {}
        for spec in self._registry:
            if spec.api_key:
                try:
                    self._secrets.resolve(spec.api_key)
                except SecretError as e:
                    problems[spec.name] = str(e)
        return problems

    def worst_case(self, model: str | None) -> CallBound:
        """The most one call on the row ``model`` names (None: the default) can cost: what its
        chain allows, lowered by its ``limits``. Each limit is unset by default (owner ruling
        2026-10-22), so this is what a call costs at worst unless a row says less."""
        spec = self._registry.get(model)
        chain = [spec, *(self._registry.get(name) for name in spec.fallbacks)]
        sends = [row.retries + 1 for row in chain]
        complete, extract = sum(sends), sum(sends) + self._schema_retries * max(sends)
        attempts = spec.limits.attempts
        if attempts is not None:
            complete, extract = min(complete, attempts), min(extract, attempts)
        return CallBound(
            complete_calls=complete,
            extract_calls=extract,
            seconds=spec.limits.seconds,
            tokens=spec.limits.tokens,
        )

    def check_tools(self, model: str | None) -> None:
        """Raise ``ConfigError`` unless the row ``model`` names, and each of its fallbacks, is
        served by a backend that can call tools (``supports_tools``): one that offers a model
        tools, reads the calls it asks for, and sends tool turns. ``model=None`` is the
        registry default."""
        spec = self._registry.get(model)
        for row in (spec, *(self._registry.get(name) for name in spec.fallbacks)):
            if getattr(self._backends[row.backend], "supports_tools", False) is not True:
                raise ConfigError(
                    f"model {row.name!r}: backend {row.backend!r} cannot call tools, so it "
                    "cannot be offered tools or sent tool turns"
                    + (f" (it is a fallback of {spec.name!r})" if row is not spec else ""),
                    fix="ask a row whose backend can (openai-compat, anthropic, mock), or offer "
                    "no tools",
                    problems=[
                        Problem(
                            path=f"models.{row.name}.backend",
                            kind="invalid_value",
                            message=f"{row.backend!r} cannot call tools",
                            got=row.backend,
                        )
                    ],
                )

    async def complete(
        self,
        model: str | None,
        messages: Messages,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: Sequence[ToolSpec] = (),
    ) -> Completion:
        """One call, text out. ``model=None`` uses the registry default.

        Whatever the provider sent is returned, and said for what it is: a reply cut at
        ``max_tokens``, refused, withheld or empty is a Completion whose ``answered`` is false,
        with ``finish_reason`` and ``refusal`` saying why. It is never raised, and never looks
        like a whole answer.

        With ``tools``, the model may ask to call them: the calls come back as
        ``tool_calls``, for the caller to run and answer with ``tool`` turns in its next
        call. hardline runs none. Such a reply is a whole answer. Every row the call may reach
        must be served by a backend that can call tools (:meth:`check_tools`), or the call is
        refused before anything is sent.
        """
        _check_overrides(max_tokens, temperature)
        spec = self._registry.get(model)
        turns = _as_messages(messages, system)
        offered = tuple(tools)
        if _uses_tools(turns, offered):
            self.check_tools(spec.name)
        started = self._clock()
        log: list[Attempt] = []
        answer = await self._call(
            spec,
            lambda s: self._request(s, turns, None, max_tokens, temperature, offered),
            log,
            started=started,
        )
        completion = self._completion(
            spec, answer, log, attempts=1, retries=answer.retries, started=started
        )
        self._observe(Answered(requested=spec.name, completion=completion))
        return completion

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
        _check_overrides(max_tokens, temperature)
        spec = self._registry.get(model)
        schema = output.model_json_schema()
        turns = list(_as_messages(messages, system))
        if _uses_tools(turns, ()):
            self.check_tools(spec.name)
        turns.append(Message(role="user", content=schema_instruction(schema)))

        started = self._clock()
        log: list[Attempt] = []
        retries = 0
        last_text = ""
        last_error = ""
        attempts = 0
        # A repair goes on from the row that answered: the rows before it in the chain have just
        # failed, and asking them again would pay for that failure twice and could mix replies
        # from two models in one conversation.
        position = 0
        for _ in range(self._schema_retries + 1):
            reached = self._limit_reached(spec, started, log) if attempts else None
            if reached is not None:
                limit, setting = reached
                raise SchemaError(
                    f"model {spec.name!r} did not produce a valid {output.__name__}, and "
                    f"{setting} stopped the repairs after {attempts} attempt(s): {last_error}",
                    last_text=last_text,
                    validation=last_error,
                    attempts=attempts,
                    attempt_log=tuple(log),
                    usage=_total(log),
                    limit=limit,
                )
            attempts += 1
            sent = tuple(turns)

            def build(s: ModelSpec, sent: tuple[Message, ...] = sent) -> Request:
                return self._request(s, sent, schema, max_tokens, temperature)

            answer = await self._call(spec, build, log, started=started, start=position)
            position = answer.position
            retries += answer.retries
            last_text = answer.raw.text
            try:
                value = _validate(output, answer.raw.text)
            except (ValueError, ValidationError) as e:
                if answer.raw.text.strip():
                    last_error = str(e)
                    turns.append(Message(role="assistant", content=answer.raw.text))
                    turns.append(Message(role="user", content=retry_instruction(last_error)))
                else:
                    # An empty reply is not echoed back: it holds nothing to correct, and the
                    # Messages API refuses an assistant turn that says nothing.
                    last_error = EMPTY_REPLY
                    turns.append(Message(role="user", content=empty_reply_instruction()))
                continue
            completion = self._completion(
                spec, answer, log, attempts=attempts, retries=retries, started=started
            )
            self._observe(Answered(requested=spec.name, completion=completion))
            return Structured(value=value, completion=completion)

        raise SchemaError(
            f"model {spec.name!r} did not produce a valid {output.__name__} "
            f"after {attempts} attempt(s): {last_error}",
            last_text=last_text,
            validation=last_error,
            attempts=attempts,
            attempt_log=tuple(log),
            usage=_total(log),
        )

    # --- internals -------------------------------------------------------------------

    def _request(
        self,
        spec: ModelSpec,
        turns: tuple[Message, ...],
        schema: dict[str, object] | None,
        max_tokens: int | None,
        temperature: float | None,
        tools: tuple[ToolSpec, ...] = (),
    ) -> Request:
        return Request(
            messages=turns,
            json_schema=schema,
            max_tokens=max_tokens if max_tokens is not None else spec.max_tokens,
            temperature=temperature if temperature is not None else spec.temperature,
            tools=tools,
        )

    async def _call(
        self,
        spec: ModelSpec,
        build: Callable[[ModelSpec], Request],
        log: list[Attempt],
        *,
        started: float,
        start: int = 0,
    ) -> _Answer:
        """One logical call: retries on ``spec``, then each fallback, until one answers.

        ``start`` skips the rows of the chain before it. Each backend call is appended to
        ``log``; a BackendError raised from here carries it. ``spec.limits``, counted from
        ``started`` over ``log``, stops the call before a retry or a fallback that would pass
        them, and cuts an attempt that outlives ``limits.seconds``.
        """
        retries = 0
        last: BackendError | None = None
        chain = [spec, *(self._registry.get(name) for name in spec.fallbacks)]
        for position, candidate in enumerate(chain):
            if position < start:
                continue
            request = build(candidate)
            for attempt in range(candidate.retries + 1):
                attempt_started = self._clock()
                deadline = asyncio.timeout(self._remaining(spec, started))
                try:
                    async with deadline:
                        raw = await self._attempt(candidate, request)
                except TimeoutError:
                    if not deadline.expired():
                        raise  # the backend's own, not the call's limit
                    log.append(
                        Attempt(
                            name=candidate.name,
                            reason="timeout",
                            latency_ms=self._ms_since(attempt_started),
                        )
                    )
                    cut = BackendError(
                        f"model {candidate.name!r}: no reply before the call's "
                        f"{self._setting(spec, 'seconds')} passed",
                        reason="timeout",
                    )
                    reached: tuple[Limit, str] = ("seconds", self._setting(spec, "seconds"))
                    raise self._stopped(spec, reached, cut, log) from None
                except BackendError as e:
                    failed = Attempt(
                        name=candidate.name,
                        reason=e.reason,
                        latency_ms=self._ms_since(attempt_started),
                    )
                    log.append(failed)
                    if not e.retryable:
                        self._observe(AttemptFailed(requested=spec.name, attempt=failed))
                        _logged(e, log)
                        raise
                    last = e
                    retrying = attempt < candidate.retries
                    delay = self._delay(attempt, e.retry_after) if retrying else 0.0
                    stop = None
                    if retrying or position < len(chain) - 1:
                        stop = self._limit_reached(spec, started, log, wait=delay)
                    self._observe(
                        AttemptFailed(
                            requested=spec.name,
                            attempt=failed,
                            retry_in_s=delay if retrying and stop is None else None,
                        )
                    )
                    if stop is not None:
                        raise self._stopped(spec, stop, e, log) from e
                    if retrying:
                        retries += 1
                        await self._sleep(delay)
                    continue
                log.append(
                    Attempt(
                        name=candidate.name,
                        usage=raw.usage,
                        latency_ms=self._ms_since(attempt_started),
                    )
                )
                return _Answer(
                    raw=raw, spec=candidate, request=request, retries=retries, position=position
                )
            if position < len(chain) - 1 and last is not None:
                self._observe(
                    FellBack(
                        requested=spec.name,
                        name=candidate.name,
                        to=chain[position + 1].name,
                        reason=last.reason,
                    )
                )
        assert last is not None  # the loop only falls through after a retryable failure
        tried = ", ".join(c.name for c in chain[start:])
        exhausted = BackendError(
            f"model {spec.name!r}: transient failures exhausted retries"
            + (f" and fallbacks ({tried})" if len(chain) - start > 1 else "")
            + f". Last: {last}",
            reason=last.reason,
            retry_after=last.retry_after,
        )
        raise _logged(exhausted, log) from last

    def _observe(self, event: CallEvent) -> None:
        """Tell the observer; one that raises is logged, never allowed to fail the call."""
        try:
            self._observer.observe(event)
        except Exception as e:  # an observer's fault is not the call's
            logger.warning("call observer %r failed: %s: %s", self._observer, type(e).__name__, e)

    def _limit_reached(
        self, spec: ModelSpec, started: float, log: Sequence[Attempt], *, wait: float = 0.0
    ) -> tuple[Limit, str] | None:
        """The first of ``spec.limits`` the call has reached, or would reach by waiting
        ``wait`` seconds first, with its setting as written (``limits.attempts=3``)."""
        limits = spec.limits
        if limits.attempts is not None and len(log) >= limits.attempts:
            return "attempts", self._setting(spec, "attempts")
        if limits.seconds is not None and self._clock() - started + wait >= limits.seconds:
            return "seconds", self._setting(spec, "seconds")
        used = _total(log)
        if limits.tokens is not None and used.input_tokens + used.output_tokens >= limits.tokens:
            return "tokens", self._setting(spec, "tokens")
        return None

    @staticmethod
    def _setting(spec: ModelSpec, limit: Limit) -> str:
        return f"limits.{limit}={getattr(spec.limits, limit):g}"

    def _remaining(self, spec: ModelSpec, started: float) -> float | None:
        """Seconds left of the call's ``limits.seconds``; None when it sets none."""
        if spec.limits.seconds is None:
            return None
        return max(0.0, spec.limits.seconds - (self._clock() - started))

    @staticmethod
    def _stopped(
        spec: ModelSpec, reached: tuple[Limit, str], last: BackendError, log: Sequence[Attempt]
    ) -> BackendError:
        """The error for a call one of its limits stopped: the last failure's reason, so a
        transient one is still retryable later, and the limit named."""
        limit, setting = reached
        stopped = BackendError(
            f"model {spec.name!r}: {setting} stopped the call after {len(log)} backend "
            f"call(s). Last: {last}",
            reason=last.reason,
            retry_after=last.retry_after,
        )
        stopped.limit = limit
        return _logged(stopped, log)

    def _ms_since(self, started: float) -> int:
        return int((self._clock() - started) * 1000)

    def _delay(self, attempt: int, retry_after: float | None) -> float:
        """Full-jitter exponential backoff; a provider's Retry-After, when given, wins."""
        if retry_after is not None:
            return min(retry_after, _RETRY_AFTER_CAP_S)
        return self._rng.uniform(0, min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * 2**attempt))

    async def _attempt(self, spec: ModelSpec, request: Request) -> RawCompletion:
        backend = self._backends[spec.backend]
        api_key = self._secrets.resolve(spec.api_key) if spec.api_key else None
        raw = await backend.complete(spec.endpoint(), request, api_key)
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
        log: Sequence[Attempt],
        *,
        attempts: int,
        retries: int,
        started: float,
    ) -> Completion:
        spec, raw, request = answer.spec, answer.raw, answer.request
        return Completion(
            name=spec.name,
            text=raw.text,
            family=spec.family,
            model=raw.model or spec.model,
            backend=spec.backend,
            local=spec.local,
            usage=_total(log),
            request_id=raw.request_id,
            latency_ms=self._ms_since(started),
            attempts=attempts,
            retries=retries,
            fallback_from=requested.name if spec.name != requested.name else None,
            finish_reason=raw.finish_reason,
            native_finish_reason=raw.native_finish_reason,
            refusal=raw.refusal,
            tool_calls=raw.tool_calls,
            params=RequestParams(
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                structured=spec.structured if request.json_schema is not None else None,
                tools=tuple(tool.name for tool in request.tools),
            ),
            attempt_log=tuple(log),
            turns_digest=turns_digest(request.messages),
            row=self._registry.record(spec.name),
            raw=raw.raw,
        )

"""The session table: ``matrix.v1.session``, one row per agent session or model call.

A reply is a sample, and the record must say which model gave it and how. Those facts live
here, one row per session, beside the board rows the session produced:

* a **reply row** (an :class:`~matrix.domain.agent.AgentResponse` or a
  :class:`~matrix.domain.model_call.ModelCall` a step provides) keeps the reply and the facts
  a consumer acts on, and points at its session row (``session``, the row's position in this
  table);
* the **ledger entry** of every row a component hands over names the sessions that
  component ran since its previous hand-over (``LedgerEntry.sessions``).

Like the ledger, the table is matrix's own: no component provides or requires it. It is
written through the Construct like any table, durably and in ledger order, by whatever ran
the session inside a run: a :class:`~matrix.domain.agent.BoundAgent`, or a model step.
Outside a run nothing records a session, and a reply keeps every fact itself.

The ids are the runtime's (``session_id``, ``request_id``) and the OpenTelemetry context's
the session ended in (``trace_id``, ``span_id``), when there is one. matrix mints none.
"""

from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

from matrix.domain.model_call import (  # noqa: TC001 -- pydantic reads them at runtime
    CallAttempt,
    FinishReason,
    RegistryRow,
    RequestParams,
    TokenUsage,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from matrix.domain.agent import AgentResponse
    from matrix.domain.model_call import ModelCall

SESSION = "matrix.v1.session"


class Session(BaseModel):
    """One agent session or one model call: a row of ``matrix.v1.session``.

    A fact the runtime did not report is None, never a constant.
    """

    model_config = ConfigDict(frozen=True)

    #: Which port ran it: ``agent-runtime`` (a session) or ``model-runtime`` (one call).
    port: Literal["agent-runtime", "model-runtime"]
    #: The runtime's type URL (``matrix.v1.agent-runtime.claude-sdk``), when the caller knew it.
    runtime: str | None = None
    #: The distributions the runtime drives (its registry entry's ``drives``).
    drives: tuple[str, ...] = ()
    #: The agent's name, for an agent session.
    agent: str | None = None
    #: The registry name of the model that answered, for a model call.
    name: str | None = None
    #: The provider's id of the model that answered.
    model: str | None = None
    family: str | None = None
    #: Who served it, as the GenAI conventions name providers.
    provider: str | None = None
    #: The backend that served a model call (hardline's ``anthropic``, ``openai-compat``).
    backend: str | None = None
    #: How an agent session ended: ``completed``, or the limit it reached.
    stop: str | None = None
    #: Why the (last) reply ended, in one vocabulary; the provider's own value beside it.
    finish_reason: FinishReason | None = None
    native_finish_reason: str | None = None
    #: What the model said when it refused.
    refusal: str | None = None
    #: Input tokens count every token read, those read from and written to the cache included.
    usage: TokenUsage | None = None
    #: The cost the provider reported: a fact of the session, never limited or estimated.
    cost_usd: float | None = None
    duration_ms: int | None = None
    #: An agent session's turns; a model call has none.
    turns: int | None = None
    #: The runtime's id for the session (the Claude Agent SDK's ``session_id``).
    session_id: str | None = None
    #: The provider's id for the request that answered a model call.
    request_id: str | None = None
    #: The OpenTelemetry trace and span the session ended in, as hex; None outside one.
    trace_id: str | None = None
    span_id: str | None = None
    #: The model asked for, when a fallback answered instead.
    fallback_from: str | None = None
    attempts: int | None = None
    retries: int | None = None
    attempt_log: tuple[CallAttempt, ...] | None = None
    params: RequestParams | None = None
    #: The digest of the turns the answering call was sent, as the runtime computed it.
    turns_digest: str | None = None
    row: RegistryRow | None = None
    #: The digest of what matrix sent the runtime (:func:`digest`): for an agent session its
    #: definition and task, for a model call its prompt, system prompt, model and output
    #: schema. Equal digests, equal requests.
    sent: str


def digest(value: Any) -> str:
    """``sha256:<hex>`` over canonical JSON (keys sorted, no whitespace, UTF-8), so a reader
    can recompute one without matrix. hardline's digests are the same."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def agent_session(
    response: AgentResponse,
    *,
    agent: str,
    sent: str,
    runtime: str | None = None,
    drives: tuple[str, ...] = (),
) -> Session:
    """The session row of an agent session, from what its runtime reported."""
    reported = (
        response.tokens_input,
        response.tokens_output,
        response.cache_read_tokens,
        response.cache_write_tokens,
    )
    usage = None
    if any(count is not None for count in reported):
        tokens_in, tokens_out, cache_read, cache_write = (count or 0 for count in reported)
        usage = TokenUsage(
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )
    return Session(
        port="agent-runtime",
        runtime=runtime,
        drives=drives,
        agent=agent,
        model=response.model,
        family=response.family,
        provider=response.provider,
        stop=response.stop,
        finish_reason=response.finish_reason,
        native_finish_reason=response.native_finish_reason,
        usage=usage,
        cost_usd=response.cost_usd,
        duration_ms=response.duration_ms,
        turns=response.num_turns,
        session_id=response.session_id,
        sent=sent,
    )


def call_session(
    call: ModelCall, *, sent: str, runtime: str | None = None, drives: tuple[str, ...] = ()
) -> Session:
    """The session row of a model call, from what its runtime reported."""
    return Session(
        port="model-runtime",
        runtime=runtime,
        drives=drives,
        name=call.name,
        model=call.model,
        family=call.family,
        provider=call.provider,
        backend=call.backend,
        finish_reason=call.finish_reason,
        native_finish_reason=call.native_finish_reason,
        refusal=call.refusal,
        usage=call.usage,
        duration_ms=call.latency_ms,
        request_id=call.request_id,
        fallback_from=call.fallback_from,
        attempts=call.attempts,
        retries=call.retries,
        attempt_log=call.attempt_log,
        params=call.params,
        turns_digest=call.turns_digest,
        row=call.row,
        sent=sent,
    )


#: Records a session in the run of the component running in this task and returns its row,
#: or None when it was not recorded. The executor sets it in each component's task.
CURRENT_SESSIONS: ContextVar[Callable[[Session], int | None] | None] = ContextVar(
    "matrix_sessions", default=None
)


def record_session(session: Session) -> int | None:
    """Record ``session`` in the run this task is part of; its row, or None outside a run."""
    record = CURRENT_SESSIONS.get()
    return None if record is None else record(session)

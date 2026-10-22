"""Agents as composition: a definition (what) bound to a runtime (where), checked at binding.

* **AgentDefinition** is what the agent *is*: prompt, tools, model, turn bound. Pure data, the
  same shape as a Claude Code agent file, so a plugin's agents are definitions as they stand.
* **AgentRuntime** (a port) is *where and how* it runs: a session with a loop and tools (the
  Claude Agent SDK, a mock). Deployment config lives on the runtime. One model call is not an
  agent session: it has its own port, ``ModelRuntime``, and its own step.
* **BoundAgent** pairs them. The runtime's ``check`` runs at binding, so a definition the
  runtime cannot honour fails at composition, before anything has spent a token.

A runtime reports how a session *ended* (``AgentResponse.stop``: ``completed``, or the limit
it reached) and raises :class:`~matrix.domain.errors.AgentRuntimeError` with a classified
``reason`` when the session *failed*. Content is never guaranteed, only a response or an error.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from matrix.domain.errors import AgentRuntimeError
from matrix.domain.model_call import FinishReason  # noqa: TC001 -- pydantic reads it
from matrix.domain.observer import (
    AGENT_END,
    AGENT_START,
    CURRENT_SPAN,
    TOOL_END,
    TOOL_START,
    Event,
    around,
    emit,
    observing,
)
from matrix.domain.session import agent_session, digest, record_session

if TYPE_CHECKING:
    from collections.abc import Sequence

    from matrix.domain.observer import Observer
    from matrix.domain.ports._out.agent_runtime import AgentRuntime

_SLUG = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class AgentDefinition(BaseModel):
    """What an agent is, independent of where it runs.

    It reads a Claude Code agent file as it stands (``matrix.load(AgentDefinition, path)``):
    the file's body is the ``system_prompt``, ``tools`` may be a comma-separated string,
    ``maxTurns`` is ``max_turns``, and any key no runtime interprets (``color``) is kept in
    ``metadata``::

        ---
        name: reviewer
        description: Reviews a diff for correctness.
        tools: Read, Grep, Glob
        model: sonnet
        ---
        You are a careful reviewer...
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    name: str = Field(pattern=_SLUG)
    description: str = ""
    #: The text after a definition file's frontmatter (``body``), or given by name.
    system_prompt: str = Field("", validation_alias=AliasChoices("system_prompt", "body"))
    #: Interpreted by the runtime: an alias or model id for the Claude Agent SDK.
    #: ``None`` = the runtime's default.
    model: str | None = None
    #: ``None`` = the runtime's default toolset. ``()`` = **no tools**: never collapsed into
    #: the default (see matrix/SECURITY.md M-2).
    tools: tuple[str, ...] | None = None
    #: The most turns a session may take; ``None`` = the runtime's default.
    max_turns: int | None = Field(
        default=None, ge=1, validation_alias=AliasChoices("max_turns", "maxTurns")
    )
    #: Keys a source carried that no runtime interprets (``color`` in a Claude Code agent
    #: file). Kept so a round trip loses nothing; never read for behaviour.
    metadata: dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _keep_the_rest(cls, data: Any) -> Any:
        """Keys this model does not interpret go to ``metadata``, as the format allows."""
        if not isinstance(data, dict):
            return data
        known = {"system_prompt", "body", "max_turns", "maxTurns", *cls.model_fields}
        rest = {k: v for k, v in data.items() if k not in known}
        if not rest:
            return data
        kept = {k: v for k, v in data.items() if k in known}
        metadata = data.get("metadata")
        return {**kept, "metadata": {**rest, **(metadata if isinstance(metadata, dict) else {})}}

    @field_validator("tools", mode="before")
    @classmethod
    def _tool_list(cls, value: Any) -> Any:
        """``Read, Grep`` as written in an agent file is ``("Read", "Grep")``."""
        if isinstance(value, str):
            return tuple(name.strip() for name in value.split(",") if name.strip())
        return value

    @field_validator("system_prompt")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        return value.strip()


class AgentResponse(BaseModel):
    """The runtime-neutral record of one agent session: what a runtime returns.

    On a reply row an agent step provides, the facts the session row holds are moved there:
    the row keeps the reply and what a consumer acts on (``content``, ``transcript``,
    ``tool_calls``, ``stop``, ``finish_reason``, ``family``, ``task``) and ``session``, the
    position of its row in ``matrix.v1.session``; every field the session row holds is None
    (:meth:`as_reply`). A fact the runtime did not report is None, never a constant.
    """

    model_config = ConfigDict(frozen=True)

    #: The session's reply: its final result, not every turn's text.
    content: str = ""
    #: What the agent wrote, turn by turn: each of its text blocks, in order.
    transcript: tuple[str, ...] = ()
    #: ``{name, input}`` per tool call, as plain dicts. A runtime that sees calls happen adds
    #: ``id``, ``started`` and ``ended`` (ISO times) and ``error`` (bool), and matrix reports
    #: each such call as an ``execute_tool`` span.
    tool_calls: tuple[dict[str, Any], ...] = ()
    #: Why the session ended: ``completed``, or the limit reached (``max_turns``,
    #: ``max_budget_usd``). A limit is not a failure; a failure raises instead.
    stop: str | None = None
    #: Why the session's last reply ended, in the model call's vocabulary (``stop``,
    #: ``length``, ``refusal``, ...); None when the runtime does not say.
    finish_reason: FinishReason | None = None
    #: The model family that answered (``claude``, ``qwen``, ``gemini``), stamped by the
    #: runtime.
    family: str | None = None
    #: The task the agent was given, as sent: an ``agent-step`` records it here, after
    #: substituting its inputs. None when the caller did not record it.
    task: str | None = None
    #: The position of this session's row in the run's ``matrix.v1.session`` table, when a
    #: run recorded it: the run that ran the session, which for a reused row is the one
    #: ``reused_from`` names. None outside a run.
    session: int | None = None
    #: Every input token the session read, those served from and written to the prompt
    #: cache included; ``cache_read_tokens`` and ``cache_write_tokens`` say how many of them.
    tokens_input: int | None = None
    tokens_output: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    duration_ms: int | None = None
    #: The cost the runtime reported, when it reports one: a fact of the session.
    cost_usd: float | None = None
    num_turns: int | None = None
    #: Who served it, as the GenAI conventions name providers (``anthropic``, ``openai``,
    #: ``gcp.gemini``), when the runtime knows.
    provider: str | None = None
    #: The model id or registry name that answered, when the runtime knows it: not the one
    #: the definition asked for.
    model: str | None = None
    #: The provider's own value for why the last reply ended, verbatim.
    native_finish_reason: str | None = None
    #: The runtime's id for the session, when it gives one.
    session_id: str | None = None

    def as_reply(self) -> AgentResponse:
        """This response as a reply row: with ``session`` set, the facts its session row
        holds are moved off. Without it nothing else holds them, and they stay."""
        if self.session is None:
            return self
        return self.model_copy(update=dict.fromkeys(_SESSION_FACTS))


#: The facts of an agent session its session row holds, and a reply row does not repeat.
_SESSION_FACTS = (
    "tokens_input",
    "tokens_output",
    "cache_read_tokens",
    "cache_write_tokens",
    "duration_ms",
    "cost_usd",
    "num_turns",
    "provider",
    "model",
    "native_finish_reason",
    "session_id",
)


class BoundAgent:
    """A definition bound to a runtime: what a component calls.

    Inside a run, each session it runs is recorded in the run's ``matrix.v1.session`` table,
    with ``runtime_url`` (the runtime's type URL) and ``drives`` (its registry entry's) when
    the binder knows them, and the response it returns points at that row (``session``). A
    session that fails is recorded too, as failed, before its error is raised.
    """

    def __init__(
        self,
        definition: AgentDefinition,
        runtime: AgentRuntime,
        *,
        observers: Sequence[Observer] = (),
        runtime_url: str | None = None,
        drives: tuple[str, ...] = (),
    ) -> None:
        runtime.check(definition)  # refuse here, at binding, not mid-run
        self._definition = definition
        self._runtime = runtime
        self._observers = tuple(observers)
        self._runtime_url = runtime_url
        self._drives = tuple(drives)

    @property
    def name(self) -> str:
        return self._definition.name

    @property
    def definition(self) -> AgentDefinition:
        return self._definition

    @property
    def runtime(self) -> AgentRuntime:
        return self._runtime

    async def run(self, task: str) -> AgentResponse:
        """Run the definition on the runtime. Emits ``matrix.agent.start`` (with the task as
        sent) and ``.end``, and a ``matrix.tool.start``/``.end`` pair for each tool call the
        runtime timed. Inside a run, records the session (see the class docstring)."""
        span = uuid.uuid4().hex
        name = self._definition.name
        scope = CURRENT_SPAN.get()
        run_id, parent, component = (
            (scope.run_id, scope.span, scope.component) if scope else (None, None, None)
        )
        begun = Event(
            AGENT_START,
            span=span,
            run_id=run_id,
            parent=parent,
            fields={"agent": name, "model": self._definition.model, "task": task},
            component=component,
        )
        await emit(self._observers, begun)
        started = time.monotonic()
        fields: dict[str, Any] = {"agent": name}
        # The session's span is current in this task while the runtime works, so what a
        # runtime or a library opens inside it is the session's child.
        with around(observing(self._observers), begun) as end:
            try:
                response = await self._runtime.run(self._definition, task)
            except asyncio.CancelledError:
                fields.update(status="cancelled", reason="cancelled")
                raise
            except Exception as e:
                fields.update(status="error", error=str(e), reason=getattr(e, "reason", None))
                fields["kind"] = getattr(e, "kind", type(e).__name__)
                self._record(task, None if not isinstance(e, AgentRuntimeError) else e.response, e)
                raise
            else:
                fields.update(
                    status=response.stop or "completed",
                    tokens_input=response.tokens_input,
                    tokens_output=response.tokens_output,
                    cost_usd=response.cost_usd,
                    model=response.model,
                    family=response.family,
                    provider=response.provider,
                    tool_calls=len(response.tool_calls),
                )
                for call in response.tool_calls:
                    await self._tool(call, span, run_id, component)
                row = self._record(task, response)
                return response if row is None else response.model_copy(update={"session": row})
            finally:
                fields["duration_ms"] = int((time.monotonic() - started) * 1000)
                ended = Event(
                    AGENT_END,
                    span=span,
                    run_id=run_id,
                    parent=parent,
                    fields=fields,
                    component=component,
                )
                end(ended)
                await emit(self._observers, ended)

    def _record(
        self, task: str, response: AgentResponse | None, error: Exception | None = None
    ) -> int | None:
        """Record the session in the run this is part of, whether it replied or failed; its
        row, or None outside a run."""
        definition = self._definition
        sent = digest({"definition": definition.model_dump(mode="json"), "task": task})
        return record_session(
            agent_session(
                response,
                agent=definition.name,
                sent=sent,
                runtime=self._runtime_url,
                drives=self._drives,
                error=error,
            )
        )

    async def _tool(
        self, call: dict[str, Any], parent: str, run_id: str | None, component: str | None
    ) -> None:
        """Report one tool call the runtime timed, under the agent's span, at the times the
        runtime saw it start and end. A call with no times is not reported as a span."""
        began, ended = _when(call.get("started")), _when(call.get("ended"))
        if began is None or ended is None:
            return
        span = uuid.uuid4().hex
        about: dict[str, Any] = {"agent": self._definition.name, "tool": call.get("name")}
        if call.get("id"):
            about["call_id"] = call["id"]
        error = bool(call.get("error"))
        finished = {
            **about,
            "status": "error" if error else "ok",
            "duration_ms": int((ended - began).total_seconds() * 1000),
        }
        start, end = (
            Event(
                event_name,
                span=span,
                run_id=run_id,
                parent=parent,
                fields=fields,
                component=component,
                time=at,
            )
            for event_name, at, fields in ((TOOL_START, began, about), (TOOL_END, ended, finished))
        )
        # Inside the session's span, at the times the runtime saw the call start and end.
        with around(observing(self._observers), start) as ending:
            await emit(self._observers, start)
            await emit(self._observers, end)
            ending(end)


def _when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None

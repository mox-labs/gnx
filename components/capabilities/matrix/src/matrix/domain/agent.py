"""Agents as composition: a definition (what) bound to a runtime (where), checked at binding.

* **AgentDefinition** is what the agent *is*: prompt, tools, model, turn bound. Pure data, the
  same shape as a Claude Code agent file, so a plugin's agents are definitions as they stand.
* **AgentRuntime** (a port) is *where and how* it runs: the Claude Agent SDK, one model call
  through hardline, a mock. Deployment config lives on the runtime.
* **BoundAgent** pairs them. The runtime's ``check`` runs at binding, so a definition the
  runtime cannot honour fails at composition, before anything has spent a token.

A runtime reports how a session *ended* (``AgentResponse.stop``: ``completed``, or the limit
it reached) and raises :class:`~matrix.domain.errors.AgentRuntimeError` with a classified
``reason`` when the session *failed*. Content is never guaranteed, only a response or an error.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from matrix.domain.observer import (
    AGENT_END,
    AGENT_START,
    CURRENT_SPAN,
    TOOL_END,
    TOOL_START,
    Event,
    emit,
)

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
    #: Interpreted by the runtime: an SDK alias or model id for the Claude runtime, a
    #: hardline registry name for the model runtime. ``None`` = the runtime's default.
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
    """The runtime-neutral record of one agent session."""

    model_config = ConfigDict(frozen=True)

    content: str = ""
    #: ``{name, input}`` per tool call, as plain dicts. A runtime that sees calls happen adds
    #: ``id``, ``started`` and ``ended`` (ISO times) and ``error`` (bool), and matrix reports
    #: each such call as an ``execute_tool`` span.
    tool_calls: tuple[dict[str, Any], ...] = ()
    tokens_input: int = 0
    tokens_output: int = 0
    duration_ms: int = 0
    cost_usd: float | None = None
    num_turns: int = 0
    #: The model family that answered (``claude``, ``qwen``, ``gemini``), stamped by the
    #: runtime.
    family: str | None = None
    #: Who served it, as the GenAI conventions name providers (``anthropic``, ``openai``,
    #: ``gcp.gemini``), when the runtime knows.
    provider: str | None = None
    #: The model id or registry name that answered, when the runtime knows it.
    model: str | None = None
    #: Why the session ended: ``completed``, or the limit reached (``max_turns``,
    #: ``max_budget_usd``). A limit is not a failure; a failure raises instead.
    stop: str | None = None
    #: The task the agent was given, as sent: an ``agent-step`` records it here, after
    #: substituting its inputs. None when the caller did not record it.
    task: str | None = None


class BoundAgent:
    """A definition bound to a runtime: what a component calls."""

    def __init__(
        self,
        definition: AgentDefinition,
        runtime: AgentRuntime,
        *,
        observers: Sequence[Observer] = (),
    ) -> None:
        runtime.check(definition)  # refuse here, at binding, not mid-run
        self._definition = definition
        self._runtime = runtime
        self._observers = tuple(observers)

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
        runtime timed."""
        span = uuid.uuid4().hex
        name = self._definition.name
        scope = CURRENT_SPAN.get()
        run_id, parent, component = (
            (scope.run_id, scope.span, scope.component) if scope else (None, None, None)
        )
        await emit(
            self._observers,
            Event(
                AGENT_START,
                span=span,
                run_id=run_id,
                parent=parent,
                fields={"agent": name, "model": self._definition.model, "task": task},
                component=component,
            ),
        )
        started = time.monotonic()
        fields: dict[str, Any] = {"agent": name}
        try:
            response = await self._runtime.run(self._definition, task)
        except Exception as e:
            fields.update(status="error", error=str(e), reason=getattr(e, "reason", None))
            fields["kind"] = getattr(e, "kind", type(e).__name__)
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
            return response
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
            await emit(self._observers, ended)

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
        for event_name, at, fields in ((TOOL_START, began, about), (TOOL_END, ended, finished)):
            event = Event(
                event_name,
                span=span,
                run_id=run_id,
                parent=parent,
                fields=fields,
                component=component,
                time=at,
            )
            await emit(self._observers, event)


def _when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None

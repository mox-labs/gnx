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
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from matrix.domain.observer import CURRENT_SPAN, Event, emit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from matrix.domain.observer import Observer
    from matrix.domain.ports._out.agent_runtime import AgentRuntime

_SLUG = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class AgentDefinition(BaseModel):
    """What an agent is, independent of where it runs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=_SLUG)
    description: str = ""
    system_prompt: str = ""
    #: Interpreted by the runtime: an SDK alias or model id for the Claude runtime, a
    #: hardline registry name for the model runtime. ``None`` = the runtime's default.
    model: str | None = None
    #: ``None`` = the runtime's default toolset. ``()`` = **no tools**: never collapsed into
    #: the default (see matrix/SECURITY.md M-2).
    tools: tuple[str, ...] | None = None
    #: ``None`` = the runtime's default. Set it to bound a session (a routing eval sets 1).
    max_turns: int | None = Field(default=None, ge=1)
    #: Keys a source carried that no runtime interprets (``color`` in a Claude Code agent
    #: file). Kept so a round trip loses nothing; never read for behaviour.
    metadata: dict[str, Any] = {}


class AgentResponse(BaseModel):
    """The runtime-neutral record of one agent session."""

    model_config = ConfigDict(frozen=True)

    content: str = ""
    #: ``{name, input}`` per tool call, as plain dicts.
    tool_calls: tuple[dict[str, Any], ...] = ()
    tokens_input: int = 0
    tokens_output: int = 0
    duration_ms: int = 0
    cost_usd: float | None = None
    num_turns: int = 0
    #: The model family that answered (``claude``, ``qwen``, ``gemini``), stamped by the
    #: runtime. What an eval reads to know whether a judge was out of family.
    family: str | None = None
    #: The model id or registry name that answered, when the runtime knows it.
    model: str | None = None
    #: Why the session ended: ``completed``, or the limit reached (``max_turns``,
    #: ``max_budget_usd``). A limit is not a failure; a failure raises instead.
    stop: str | None = None


class BoundAgent:
    """A definition bound to a runtime: what a flow member or an eval actually calls."""

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
        """Run the definition on the runtime. Emits ``agent.start``/``agent.end``."""
        span = uuid.uuid4().hex
        name = self._definition.name
        run_id, parent = CURRENT_SPAN.get() or (None, None)
        emit(
            self._observers,
            Event(
                "agent.start",
                span,
                run_id,
                parent,
                {"agent": name, "model": self._definition.model},
            ),
        )
        started = time.monotonic()
        fields: dict[str, Any] = {"agent": name}
        try:
            response = await self._runtime.run(self._definition, task)
        except Exception as e:
            fields.update(status="error", error=str(e), reason=getattr(e, "reason", None))
            raise
        else:
            fields.update(
                status=response.stop or "completed",
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
                cost_usd=response.cost_usd,
                model=response.model,
                family=response.family,
            )
            return response
        finally:
            fields["duration_ms"] = int((time.monotonic() - started) * 1000)
            emit(self._observers, Event("agent.end", span, run_id, parent, fields))

"""The mock runtimes, one per port: deterministic, offline stand-ins.

Each registers with ``stand_in: true``, so a caller can tell its canned answers from a
model's. Structural typing would not notice a mock whose signature drifted from its port, so
registration checks each against its port, as it checks any runtime.

**MockAgentRuntime** (``matrix.v1.agent-runtime.mock``) plays any agent definition. A reply is
chosen by the first of: ``responses`` keyed by the whole task text as sent (after an agent
step substitutes its inputs), ``by_agent`` keyed by the agent's name, ``default``, or else an
echo, ``mock:<agent>:<task>``, which shows the task each agent was sent. The task as sent is
also on every response an agent step records (``AgentResponse.task``) and in the run's
``matrix.agent.start`` events, and ``calls`` keeps each ``(agent, task)``.

**MockModelRuntime** (``matrix.v1.model-runtime.mock``) answers any model name. A reply is the
first of ``responses`` keyed by the whole prompt as sent, ``by_model`` keyed by the model
name, ``default``, or else the echo ``mock:<model>:<prompt>``; ``calls`` keeps each
``(model, prompt)``. A request with an ``output`` type gets the reply validated against it,
as a real model runtime does. A call records what is true of a canned reply: one attempt, no
retries, no tokens, the time it took, and the reply as whole (``finish_reason: stop``).

Neither reports a cost: a reported cost is what a provider said, and a mock is not one.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, ValidationError

from matrix.domain.agent import AgentResponse
from matrix.domain.errors import ModelRuntimeError
from matrix.domain.model_call import ModelCall, TokenUsage
from matrix.domain.template import json_document

if TYPE_CHECKING:
    from matrix.domain.agent import AgentDefinition
    from matrix.domain.model_call import ModelRequest


class MockAgentRuntimeConfig(BaseModel):
    """Offline replies, chosen by the first that matches: `responses`, keyed by the whole task
    text as sent (after an agent step fills its placeholders); `by_agent`, keyed by the
    agent's name, so each agent step gets its own reply; `default`; else the echo
    `mock:<agent>:<task>`. An agent step with `output` needs a JSON reply. An agent's `model`
    is recorded on its responses, never used; any value is accepted. A reply reports no cost
    (`cost_usd: null`): no provider reported one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The whole task text, as sent → reply.
    responses: dict[str, str] = {}
    #: Agent name → reply, for any task that ``responses`` does not match.
    by_agent: dict[str, str] = {}
    #: Reply for an unmatched task and agent. ``None`` echoes ``mock:<agent>:<task>``.
    default: str | None = None
    family: str = "mock"


class MockAgentRuntime:
    def __init__(self, config: MockAgentRuntimeConfig | None = None) -> None:
        self._config = config or MockAgentRuntimeConfig()
        self.calls: list[tuple[str, str]] = []

    def check(self, definition: AgentDefinition) -> None:
        """Every definition can be mocked."""

    async def run(self, definition: AgentDefinition, task: str) -> AgentResponse:
        self.calls.append((definition.name, task))
        text = self._config.responses.get(task, self._config.by_agent.get(definition.name))
        if text is None:
            text = (
                self._config.default
                if self._config.default is not None
                else (f"mock:{definition.name}:{task}")
            )
        return AgentResponse(
            content=text,
            num_turns=1,
            family=self._config.family,
            model=definition.model,
            stop="completed",
        )


class MockModelRuntimeConfig(BaseModel):
    """Offline replies, chosen by the first that matches: `responses`, keyed by the whole
    prompt as sent (after a model step fills its placeholders); `by_model`, keyed by the model
    name a step asks for; `default`; else the echo `mock:<model>:<prompt>`. Any model name is
    accepted. A step whose `output` kind has a schema needs a reply of that shape, as JSON. A
    call reports no cost (no provider reported one), no tokens, and its reply as whole
    (`finish_reason: stop`): the reply is exactly what was configured."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The whole prompt, as sent → reply.
    responses: dict[str, str] = {}
    #: Model name → reply, for any prompt that ``responses`` does not match.
    by_model: dict[str, str] = {}
    #: Reply for an unmatched prompt and model. ``None`` echoes ``mock:<model>:<prompt>``.
    default: str | None = None
    family: str = "mock"


class MockModelRuntime:
    def __init__(self, config: MockModelRuntimeConfig | None = None) -> None:
        self._config = config or MockModelRuntimeConfig()
        self.calls: list[tuple[str | None, str]] = []

    def check(self, model: str | None) -> None:
        """Every model name can be mocked."""

    async def call(self, request: ModelRequest) -> ModelCall:
        started = time.monotonic()
        self.calls.append((request.model, request.prompt))
        config = self._config
        text = config.responses.get(request.prompt)
        if text is None and request.model is not None:
            text = config.by_model.get(request.model)
        if text is None:
            text = (
                config.default
                if config.default is not None
                else (f"mock:{request.model or ''}:{request.prompt}")
            )
        value = None
        if request.output is not None:
            try:
                value = request.output.model_validate(json_document(text)).model_dump(mode="json")
            except (LookupError, ValidationError) as e:
                raise ModelRuntimeError(
                    f"the mock's reply is not a {request.output.__name__}: {text[:200]!r}",
                    reason="failed",
                    fix=f"make the mock's reply one JSON document of a {request.output.__name__}"
                    " (`responses`, `by_model` or `default`)",
                    model=request.model,
                ) from e
        return ModelCall(
            text=text,
            value=value,
            finish_reason="stop",
            name=request.model,
            model=request.model,
            family=config.family,
            backend="mock",
            usage=TokenUsage(),
            attempts=1,
            retries=0,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

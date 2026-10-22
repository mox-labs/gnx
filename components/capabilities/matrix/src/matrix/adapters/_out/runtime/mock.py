"""MockRuntime — deterministic, offline, and a faithful implementation of AgentRuntime.

Structural typing would not notice a mock whose signature drifted from the port, so
``test_every_runtime_satisfies_the_port`` checks it.

A reply is chosen by the first of: ``responses`` keyed by the whole task text as sent (after
an agent step substitutes its inputs), ``by_agent`` keyed by the agent's name, ``default``,
or else an echo, ``mock:<agent>:<task>``, which shows the task each agent was sent. The task
as sent is also on every response an agent step records (``AgentResponse.task``) and in the
run's ``matrix.agent.start`` events, and ``calls`` keeps each ``(agent, task)``. A reply
reports no cost (``cost_usd`` None): a reported cost is what a provider said, and the mock is
not one.

Type URL: ``matrix.v1.runtime.mock``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from matrix.domain.agent import AgentResponse

if TYPE_CHECKING:
    from matrix.domain.agent import AgentDefinition


class MockRuntimeConfig(BaseModel):
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


class MockRuntime:
    def __init__(self, config: MockRuntimeConfig | None = None) -> None:
        self._config = config or MockRuntimeConfig()
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

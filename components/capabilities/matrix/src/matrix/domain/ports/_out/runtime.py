"""Agent — a runnable agent: ``run(prompt) -> AgentResponse``.

What a consumer calls. Produced by binding an AgentDefinition to an AgentRuntime
(:class:`matrix.domain.agent.BoundAgent`); anything else with the same method satisfies it
structurally.
"""

from typing import Protocol, runtime_checkable

from matrix.domain.types import AgentResponse


@runtime_checkable
class Agent(Protocol):
    """Execute a prompt and return a structured response."""

    async def run(self, prompt: str) -> AgentResponse: ...

"""MockRuntime — deterministic, offline, and a faithful implementation of AgentRuntime.

The previous mock exposed ``invoke(system, messages) -> str``, a different signature from
the port it stood in for; under structural typing nothing noticed. This one is checked
against the port in ``test_every_runtime_satisfies_the_port``.

Type URL: ``matrix.v1/runtime.mock``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from matrix.domain.types import AgentResponse

if TYPE_CHECKING:
    from matrix.domain.agent import AgentDefinition


class MockRuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Task text → reply.
    responses: dict[str, str] = {}
    #: Reply for an unmatched task. ``None`` echoes ``mock:<agent>:<task>``.
    default: str | None = None
    family: str = "mock"


class MockRuntime:
    def __init__(self, config: MockRuntimeConfig | None = None) -> None:
        self._config = config or MockRuntimeConfig()
        self.calls: list[tuple[str, str]] = []

    async def run(self, definition: AgentDefinition, task: str) -> AgentResponse:
        self.calls.append((definition.name, task))
        text = self._config.responses.get(task)
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
        )

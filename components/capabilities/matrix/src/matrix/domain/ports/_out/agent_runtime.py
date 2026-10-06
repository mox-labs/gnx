"""AgentRuntime — executes a definition against a task.

Adapters: Claude Agent SDK (``matrix.v1/runtime.claude-sdk``), a single model call through
hardline (``matrix.v1/runtime.model``), and a mock (``matrix.v1/runtime.mock``). Strands,
Google ADK or agy are further adapters behind the same two-argument call.

A runtime that cannot honour part of a definition raises rather than ignoring it: a
single-call model runtime handed a definition that declares tools refuses, because silently
running without them would measure a different agent than the one defined. A runtime that
can tell from the definition alone also offers ``check(definition)``, raising ConfigError;
BoundAgent calls it when binding, so composition and dry runs (``ix experiment validate``)
see the fault before anything runs. ``check`` is optional: a runtime without it is unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from matrix.domain.agent import AgentDefinition
    from matrix.domain.types import AgentResponse


@runtime_checkable
class AgentRuntime(Protocol):
    async def run(self, definition: AgentDefinition, task: str) -> AgentResponse: ...

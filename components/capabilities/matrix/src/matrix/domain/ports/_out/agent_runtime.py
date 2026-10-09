"""AgentRuntime — hosts one agent session for a definition and a task.

Adapters: the Claude Agent SDK (``matrix.v1.runtime.claude-sdk``), a single model call through
hardline (``matrix.v1.runtime.model``), and a mock (``matrix.v1.runtime.mock``). Strands,
Google ADK or agy are further adapters behind the same two calls.

**Assumes** a definition that passed ``check`` and a task string. **Guarantees** exactly one
of: an :class:`AgentResponse` whose ``stop`` says why the session ended, or an
:class:`AgentRuntimeError` with a ``reason`` (``unavailable``, ``rate_limited``, ``timeout``,
``auth``, ``incapable``, ``refused``, ``failed``). Never an empty normal response for a failed
session. Content is a sample, never guaranteed; a caller judges it, the runtime does not.

``check(definition)`` is required: it raises ``ConfigError`` for a definition the runtime
cannot honour (a single-call runtime handed tools), so binding refuses it before anything runs.
A runtime that accepts every definition implements it as a no-op.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from matrix.domain.agent import AgentDefinition, AgentResponse


@runtime_checkable
class AgentRuntime(Protocol):
    def check(self, definition: AgentDefinition) -> None: ...

    async def run(self, definition: AgentDefinition, task: str) -> AgentResponse: ...


@runtime_checkable
class Agent(Protocol):
    """A runnable agent: what callers use. A :class:`BoundAgent` is one."""

    @property
    def name(self) -> str: ...

    async def run(self, task: str) -> AgentResponse: ...

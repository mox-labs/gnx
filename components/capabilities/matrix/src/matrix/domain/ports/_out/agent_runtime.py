"""AgentRuntime: hosts one agent session for a definition and a task.

An agent session has a loop and may have tools and turns. One model call, with none of
these, is the other port: :class:`~matrix.domain.ports._out.model_runtime.ModelRuntime`.

**Assumes** a definition that passed ``check`` and a task string. **Guarantees** exactly one
of: an :class:`AgentResponse` whose ``stop`` says why the session ended, or an
:class:`AgentRuntimeError` with a ``reason`` (``unavailable``, ``rate_limited``, ``timeout``,
``auth``, ``incapable``, ``refused``, ``failed``). Never an empty normal response for a failed
session. Content is a sample, never guaranteed; a caller judges it, the runtime does not.

``check(definition)`` is required: it raises ``ConfigError`` for a definition the runtime
cannot play (skills its tools leave no way to use, an option its SDK does not have), so
binding refuses it before anything runs. A runtime that plays every definition implements it
as a no-op.

Two members are optional, read with ``getattr`` so a runtime written before them works
unedited: ``warnings`` (problems a reader of its config should see), and ``resolved``: JSON
data for what the runtime settled when it was built, beyond its own config, that decides how
it answers. Composition digests it into the reuse fingerprint of every component that runs
an agent on it.

A runtime that also logs what its ``warnings`` say logs it on :data:`RUNTIME_LOGGER`, once per
process, so a caller that builds a runtime per session is not told once per session. A
program that reports ``warnings`` itself (the ``matrix`` command, ix) quiets that logger.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

if TYPE_CHECKING:
    from matrix.domain.agent import AgentDefinition, AgentResponse

#: The logger a runtime logs its config warnings on, once per process, at WARNING.
RUNTIME_LOGGER: Final = "matrix.runtime"


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

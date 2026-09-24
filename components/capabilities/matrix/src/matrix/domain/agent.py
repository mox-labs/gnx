"""Agents as composition: a definition (data) bound to a runtime (execution).

The split the old ``Agent`` protocol hid:

* **AgentDefinition** — what the agent *is*: its prompt, the tools it may use, the model it
  asks for, how many turns it gets. Pure data. The same shape as a Claude Code agent file
  (``name``/``description``/``tools``/``model`` frontmatter + a markdown body), so a plugin's
  agents are matrix definitions without translation.
* **AgentRuntime** — *where and how* it runs: the Claude Agent SDK with a permission mode
  and a working directory, a single model call through modelrt, a mock. Deployment config.
* **BoundAgent** — the component produced by pairing them. It satisfies the ``Agent`` port
  (``run(prompt) -> AgentResponse``), so everything that consumed an agent before still does.

One definition can run on several runtimes (an eval comparing the SDK against a local
model), and one runtime serves many definitions (a bench of agents sharing a sandbox).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from matrix.domain.ports._out.agent_runtime import AgentRuntime
    from matrix.domain.types import AgentResponse

_SLUG = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class AgentDefinition(BaseModel):
    """What an agent is, independent of where it runs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=_SLUG)
    description: str = ""
    system_prompt: str = ""
    #: Interpreted by the runtime: an SDK alias or model id for the Claude runtime, a
    #: modelrt registry name for the model runtime. ``None`` = the runtime's default.
    model: str | None = None
    #: ``None`` = the runtime's default toolset. ``()`` = **no tools** — never collapsed into
    #: the default (the old ClaudeAgent turned ``[]`` into ``None`` and handed the agent the
    #: full toolset; see matrix/SECURITY.md M-2).
    tools: tuple[str, ...] | None = None
    max_turns: int = Field(default=1, ge=1)
    #: Keys a source carried that no runtime interprets (``color`` in a Claude Code agent
    #: file). Kept so a round trip loses nothing; never read for behaviour.
    metadata: dict[str, Any] = {}


class BoundAgent:
    """A definition bound to a runtime — the thing a DAG node or an eval actually calls."""

    def __init__(self, definition: AgentDefinition, runtime: AgentRuntime) -> None:
        self._definition = definition
        self._runtime = runtime

    @property
    def name(self) -> str:
        return self._definition.name

    @property
    def definition(self) -> AgentDefinition:
        return self._definition

    @property
    def runtime(self) -> AgentRuntime:
        return self._runtime

    async def run(self, prompt: str) -> AgentResponse:
        return await self._runtime.run(self._definition, prompt)

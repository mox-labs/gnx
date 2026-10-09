"""Matrix config types — platform schema + composed config container.

``MatrixConfig`` is the ``matrix:`` section of any tool's config. It declares the
composition: which runtimes exist, where agent definitions live, and which agents bind
which definition to which runtime. ``matrix.composition.compose`` turns it into running
objects; nothing here does I/O.

Example (``./matrix.yaml``, or the ``matrix:`` section of any tool's config file)::

    matrix:
      definitions: [agents/]                 # *.md agent files, Claude Code format
      models:                                # a hardline registry, for `type: model` runtimes
        default: qwen3-8b
        models:
          qwen3-8b: {backend: openai-compat, base_url: "http://127.0.0.1:8080/v1",
                     model: mlx-community/Qwen3-8B-4bit, family: qwen, local: true}
      runtimes:
        sdk:   {type: claude-sdk, permission_mode: default, setting_sources: []}
        local: {type: model}
      observers: [otel]                      # optional; tracing is an observer extension
      agents:
        reviewer: {runtime: sdk}                         # definition agents/reviewer.md
        triage:   {runtime: local, definition: reviewer, model: qwen3-8b}
        greeter:  {runtime: local, system_prompt: "Say hello."}   # fully inline

``Config[C]`` composes that section with a client-provided schema.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

C = TypeVar("C", bound=BaseModel)


class RuntimeConfig(BaseModel):
    """One named runtime: a ``type`` plus that runtime's own options.

    ``type`` is a matrix built-in's short name (``claude-sdk``, ``model``, ``mock``) or the
    full type URL of a runtime another package registered (``acme.v1.runtime.strands``).
    Options are validated by the runtime's own typed config, so an unknown option fails
    naming the runtime.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    type: str = Field(min_length=1)

    def options(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class AgentConfig(BaseModel):
    """Binds a definition to a runtime, with optional inline overrides.

    ``definition`` names a loaded definition (defaults to the agent's own key). Any of the
    override fields replace that definition's value. An agent with no loaded definition
    must supply ``system_prompt`` inline.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    runtime: str
    definition: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    model: str | None = None
    tools: tuple[str, ...] | None = None
    max_turns: int | None = Field(default=None, ge=1)


class MatrixConfig(BaseModel):
    """What Matrix composes. Platform-owned schema — clients don't redefine it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    definitions: tuple[str, ...] = ()
    runtimes: dict[str, RuntimeConfig] = {}
    #: Observer extensions to attach: built-in short names (``otel``) or type URLs.
    observers: tuple[str, ...] = ()
    agents: dict[str, AgentConfig] = {}
    #: A hardline registry section (``{default?, models: {...}}``) handed to every
    #: ``type: model`` runtime that does not carry its own. ``None`` = hardline discovers its
    #: own tiers (``~/.hardline/config.yaml``, ``./hardline.yaml``, ``$HARDLINE_CONFIG``).
    models: dict[str, Any] | None = None


class Config(BaseModel, Generic[C]):  # noqa: UP046
    """Composed config: Matrix platform settings + client settings.

    Generic over C — the client provides its Pydantic model type.
    """

    model_config = ConfigDict(frozen=True)

    matrix: MatrixConfig = MatrixConfig()
    client: C
    #: Every config file consulted, lowest priority first, each marked when absent: what
    #: ``matrix config --sources`` prints, so "which file won" is never a guess.
    sources: tuple[str, ...] = ()

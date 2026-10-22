"""The ``matrix:`` section of a tool's config, and the container that joins it to the tool's own.

:class:`MatrixConfig` declares a composition: the agent runtimes and the model runtimes, where
agent definitions live, the agents that bind a definition to an agent runtime, the observers,
the components a run wires, where runs are recorded and the limits a run keeps.
``matrix.composition.compose`` builds it; nothing here does I/O. :class:`Config` pairs that
section with a tool's own typed section. The matrix README has a complete example.
"""

from __future__ import annotations

from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from matrix.domain.executor import Limits
from matrix.domain.loading import Accumulate, BesideFile

C = TypeVar("C", bound=BaseModel)


class RuntimeConfig(BaseModel):
    """One named runtime, of either kind: a ``type`` plus that runtime's own options.

    ``type`` is a matrix built-in's short name or the full type URL of a runtime another
    package registered. Under ``agent_runtimes`` the built-ins are ``claude-sdk`` and
    ``mock`` (``acme.v1.agent-runtime.strands`` names another's); under ``model_runtimes``
    they are ``hardline`` and ``mock``. Options are validated by the runtime's own typed
    config, so an unknown option fails naming the runtime.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    type: str = Field(min_length=1)

    def options(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class ObserverConfig(BaseModel):
    """One observer with options: a ``type`` plus that observer's own config.

    ``type`` is a built-in's short name (``console``, ``jsonl``, ``otel``) or a full type URL.
    An observer that takes no options can be named by a bare string instead.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    type: str = Field(min_length=1)

    def options(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class StoreConfig(BaseModel):
    """Where runs are recorded: a ``type`` plus that store's own options.

    ``type`` is a built-in's short name (``jsonl``, ``memory``) or the full type URL of a
    store another package registered at the ``store`` point. Options are validated by the
    store's own typed config (``matrix describe matrix.v1.store.jsonl``).
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    type: str = Field(min_length=1)

    def options(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class ComponentConfig(BaseModel):
    """One component to use in a run: a ``name`` for this instance, the ``type`` (the type
    URL of a registered component) and that component's own ``config``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    config: dict[str, Any] = {}


class AgentConfig(BaseModel):
    """Binds a definition to an agent runtime, with optional inline overrides.

    ``definition`` names a loaded definition (defaults to the agent's own key). Any of the
    override fields replace that definition's value. An agent with no loaded definition
    must supply ``system_prompt`` inline.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: A name under ``agent_runtimes``.
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

    #: Directories of agent definitions. Each tier's are kept (yours plus the project's), and
    #: a relative one is read from beside the file that names it.
    definitions: Annotated[tuple[str, ...], Accumulate(), BesideFile()] = ()
    #: Agent sessions, with a loop and tools: what ``agents`` bind to.
    agent_runtimes: dict[str, RuntimeConfig] = {}
    #: One model call each: what a ``model-step`` names.
    model_runtimes: dict[str, RuntimeConfig] = {}
    #: Observer extensions to attach: a built-in short name (``console``, ``jsonl``,
    #: ``otel``) or type URL, or ``{type, ...options}`` for one that takes config.
    observers: tuple[str | ObserverConfig, ...] = ()
    agents: dict[str, AgentConfig] = {}
    #: The components of a run. Their order of work is inferred from the kinds they require
    #: and provide; the order here means nothing.
    components: tuple[ComponentConfig, ...] = ()
    #: The kinds a caller supplies when it runs ``components``.
    inputs: tuple[str, ...] = ()
    #: Where runs are recorded. The default keeps each run as JSONL tables under
    #: ``.matrix/runs`` in the working directory.
    store: StoreConfig = StoreConfig(type="jsonl")
    #: Bounds on a run: concurrency, deadlines, grace, cleanup, the failure policy, retries,
    #: and per-component overrides (:class:`~matrix.domain.executor.Limits`).
    limits: Limits = Limits()
    #: A hardline registry section (``{default?, models: {...}}``) handed to every
    #: ``type: hardline`` model runtime that does not carry its own. ``None`` = hardline
    #: discovers its own tiers (``~/.hardline/config.yaml``, ``./hardline.yaml``,
    #: ``$HARDLINE_CONFIG``).
    models: dict[str, Any] | None = None


class Config(BaseModel, Generic[C]):  # noqa: UP046
    """Composed config: matrix's own settings and one client tool's."""

    model_config = ConfigDict(frozen=True)

    matrix: MatrixConfig = MatrixConfig()
    client: C
    #: Every config file consulted, lowest priority first, each marked when absent: what
    #: ``matrix config --sources`` prints, so "which file won" is never a guess.
    sources: tuple[str, ...] = ()
    #: Dotted key path -> the file that set it, so a problem found after loading (an unknown
    #: runtime type, a component that does not wire) names its file too.
    origins: dict[str, str] = Field(default={}, repr=False)

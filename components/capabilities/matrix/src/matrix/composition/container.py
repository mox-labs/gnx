"""Composition root — config in, runtimes + definitions + bound agents out.

The only place that combines domain concepts with concrete adapters. ``compose`` reads a
``MatrixConfig`` and builds, in order:

1. **runtimes** — each ``runtimes.<name>`` resolved through the registry by its type URL
   (``matrix.v1/runtime.<type>``), its options validated by that runtime's typed config;
2. **definitions** — every ``*.md`` in each ``definitions`` directory;
3. **agents** — each ``agents.<name>`` binding a definition (plus inline overrides) to a
   named runtime, producing a :class:`BoundAgent`.

Every failure is a ConfigError naming the key path (``agents.triage.runtime``) and the
legal values. The Container then hands out agents by name and builds DAG orchestrators
from ``(type_url, config)`` specs.

**Shared context.** Some settings belong to the composition, not to one runtime: the
``matrix.models`` registry, the working directory a host tool runs in. ``compose`` hands each
of them to every runtime whose typed config declares a field of that name and leaves it
unset. Composition never names a runtime type to do this, so a third-party runtime that
declares ``models`` gets the registry exactly as the built-in ``model`` runtime does.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from matrix.adapters._out.definitions.markdown import MarkdownDefinitionSource
from matrix.composition.runtimes import (
    RUNTIME_NAMESPACE,
    register_builtin_runtimes,
    runtime_type_url,
)
from matrix.domain.agent import AgentDefinition, BoundAgent
from matrix.domain.config import Config, MatrixConfig
from matrix.domain.errors import ConfigError, NotFoundError
from matrix.domain.orchestrator import Orchestrator
from matrix.domain.registry import ComponentRegistry

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from matrix.domain.ports._out.agent_runtime import AgentRuntime
    from matrix.domain.ports._out.definition_source import DefinitionSource


class Container:
    """What composition produced. Hands out agents by name; builds orchestrators."""

    def __init__(
        self,
        config: Config[Any],
        registry: ComponentRegistry,
        *,
        runtimes: Mapping[str, AgentRuntime] | None = None,
        definitions: Mapping[str, AgentDefinition] | None = None,
        agents: Mapping[str, BoundAgent] | None = None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._runtimes = dict(runtimes or {})
        self._definitions = dict(definitions or {})
        self._agents = dict(agents or {})

    @property
    def config(self) -> Config[Any]:
        return self._config

    @property
    def registry(self) -> ComponentRegistry:
        return self._registry

    @property
    def runtimes(self) -> Mapping[str, AgentRuntime]:
        return dict(self._runtimes)

    @property
    def definitions(self) -> Mapping[str, AgentDefinition]:
        return dict(self._definitions)

    @property
    def agents(self) -> Mapping[str, BoundAgent]:
        return dict(self._agents)

    def agent(self, name: str) -> BoundAgent:
        agent = self._agents.get(name)
        if agent is None:
            known = ", ".join(sorted(self._agents)) or "(none configured)"
            raise NotFoundError(f"no agent named {name!r}. Configured: {known}")
        return agent

    def runtime(self, name: str) -> AgentRuntime:
        runtime = self._runtimes.get(name)
        if runtime is None:
            known = ", ".join(sorted(self._runtimes)) or "(none configured)"
            raise NotFoundError(f"no runtime named {name!r}. Configured: {known}")
        return runtime

    def create_component(self, type_url: str, config: dict[str, Any] | None = None) -> Any:
        return self._registry.create(type_url, config)

    def build_orchestrator(
        self, component_specs: Sequence[tuple[str, dict[str, Any] | None]]
    ) -> Orchestrator:
        """Resolve ``(type_url, config)`` specs through the registry into an Orchestrator."""
        components = [self._registry.create(t, c) for t, c in component_specs]
        return Orchestrator(components)


def default_registry(*, discover: bool = True) -> ComponentRegistry:
    """Built-in runtimes, plus every ``matrix.components`` entry point when ``discover``."""
    registry = register_builtin_runtimes(ComponentRegistry())
    return registry.discover() if discover else registry


def shared_context(matrix: MatrixConfig, **extra: Any) -> dict[str, Any]:
    """The composition-wide settings offered to runtimes: ``models`` plus ``extra``."""
    context = {k: v for k, v in extra.items() if v is not None}
    if matrix.models is not None:
        context.setdefault("models", matrix.models)
    return context


def with_context(
    registry: ComponentRegistry,
    type_url: str,
    options: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """``options`` plus each context value the entry's typed config declares and lacks."""
    merged = dict(options)
    config_cls = registry.config_class(type_url)
    if config_cls is None:
        return merged
    for key, value in context.items():
        if key in config_cls.model_fields and key not in merged:
            merged[key] = value
    return merged


def build_runtimes(
    matrix: MatrixConfig,
    registry: ComponentRegistry,
    *,
    source: str = "matrix",
    context: Mapping[str, Any] | None = None,
) -> dict[str, AgentRuntime]:
    shared = shared_context(matrix) if context is None else dict(context)
    runtimes: dict[str, AgentRuntime] = {}
    for name, spec in matrix.runtimes.items():
        type_url = runtime_type_url(spec.type)
        if type_url not in registry:
            legal = sorted(
                t.removeprefix(RUNTIME_NAMESPACE)
                for t in registry.types()
                if t.startswith(RUNTIME_NAMESPACE)
            )
            raise ConfigError(
                f"{source}: runtimes.{name}.type: {spec.type!r} is not a registered runtime. "
                f"Registered: {', '.join(legal) or '(none)'}"
            )
        options = with_context(registry, type_url, spec.options(), shared)
        runtimes[name] = registry.create(type_url, options, source=f"{source}: runtimes.{name}")
    return runtimes


def load_definitions(sources: Sequence[DefinitionSource]) -> dict[str, AgentDefinition]:
    definitions: dict[str, AgentDefinition] = {}
    origin: dict[str, str] = {}
    for source in sources:
        for definition in source.load():
            if definition.name in definitions:
                raise ConfigError(
                    f"agent definition {definition.name!r} is defined in both "
                    f"{origin[definition.name]} and {source.describe()}"
                )
            definitions[definition.name] = definition
            origin[definition.name] = source.describe()
    return definitions


def bind_agents(
    matrix: MatrixConfig,
    runtimes: Mapping[str, AgentRuntime],
    definitions: Mapping[str, AgentDefinition],
    *,
    source: str = "matrix",
) -> dict[str, BoundAgent]:
    agents: dict[str, BoundAgent] = {}
    for name, spec in matrix.agents.items():
        runtime = runtimes.get(spec.runtime)
        if runtime is None:
            raise ConfigError(
                f"{source}: agents.{name}.runtime: {spec.runtime!r} is not a configured runtime. "
                f"Configured: {', '.join(sorted(runtimes)) or '(none)'}"
            )
        base_name = spec.definition or name
        base = definitions.get(base_name)
        overrides = {
            k: v
            for k, v in spec.model_dump(exclude={"runtime", "definition"}).items()
            if v is not None
        }
        if base is None:
            if spec.definition is not None or "system_prompt" not in overrides:
                raise ConfigError(
                    f"{source}: agents.{name}: no definition named {base_name!r} was loaded "
                    f"(loaded: {', '.join(sorted(definitions)) or '(none)'}), and no inline "
                    "system_prompt was given"
                )
            definition = AgentDefinition.model_validate({"name": name, **overrides})
        else:
            definition = base.model_copy(update={**overrides, "name": name})
            definition = AgentDefinition.model_validate(definition.model_dump())
        agents[name] = BoundAgent(definition, runtime)
    return agents


def compose(
    config: Config[Any] | MatrixConfig,
    *,
    registry: ComponentRegistry | None = None,
    base_dir: Path | None = None,
    source: str = "matrix",
    definition_sources: Sequence[DefinitionSource] = (),
    context: Mapping[str, Any] | None = None,
) -> Container:
    """Config → Container with runtimes, definitions and bound agents.

    ``base_dir`` anchors relative ``definitions`` directories (the directory of the config
    file that declared them). ``definition_sources`` adds sources beyond the configured
    directories — a plugin's agents, a mapping from another root. ``context`` adds shared
    settings (e.g. ``{"cwd": ...}``) on top of ``matrix.models``; see the module docstring.
    """
    full = config if isinstance(config, Config) else Config[Any](matrix=config, client=None)
    matrix = full.matrix
    reg = registry if registry is not None else default_registry()
    root = base_dir or Path.cwd()
    sources: list[DefinitionSource] = [
        MarkdownDefinitionSource((root / d) if not Path(d).is_absolute() else Path(d))
        for d in matrix.definitions
    ]
    sources.extend(definition_sources)
    runtimes = build_runtimes(
        matrix, reg, source=source, context=shared_context(matrix, **dict(context or {}))
    )
    definitions = load_definitions(sources)
    agents = bind_agents(matrix, runtimes, definitions, source=source)
    return Container(full, reg, runtimes=runtimes, definitions=definitions, agents=agents)

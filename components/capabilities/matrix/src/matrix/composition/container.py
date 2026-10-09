"""Composition root: config in, runtimes, definitions, agents and observers out.

The only place that joins domain concepts to concrete adapters. :func:`compose` builds, in
order:

1. **observers**, each ``matrix.observers`` entry resolved through the registry;
2. **runtimes**, each ``matrix.runtimes.<name>`` resolved by its type (a built-in short name
   or a full type URL), its options validated by that runtime's typed config;
3. **definitions**, every ``*.md`` in each ``matrix.definitions`` directory;
4. **agents**, each ``matrix.agents.<name>`` binding a definition (plus inline overrides) to a
   named runtime. The runtime's ``check`` runs here, so a definition it cannot honour fails now.

Nothing is called: composition makes no model call and no tool call. Every failure is a
ConfigError naming the key path and the legal values.

**Needs.** An extension declares the shared things it needs (``agents``, ``models``, ``cwd``)
when it registers; composition hands over exactly those. Nothing is matched by field name.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from matrix.adapters._out.definitions.markdown import MarkdownDefinitionSource
from matrix.composition.builtins import RUNTIME_PREFIX, observer_type_url, runtime_type_url
from matrix.composition.builtins import register as register_builtins
from matrix.domain.agent import AgentDefinition, BoundAgent
from matrix.domain.config import Config, MatrixConfig
from matrix.domain.errors import ConfigError, NotFoundError
from matrix.domain.executor import Executor
from matrix.domain.flow import CompiledFlow, Flow, compile_flow
from matrix.domain.registry import Registry

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from matrix.domain.observer import Observer
    from matrix.domain.ports._out.agent_runtime import AgentRuntime
    from matrix.domain.ports._out.definition_source import DefinitionSource


class Container:
    """What composition produced: agents by name, and the means to compile and run flows."""

    def __init__(
        self,
        config: Config[Any],
        registry: Registry,
        *,
        runtimes: Mapping[str, AgentRuntime] | None = None,
        definitions: Mapping[str, AgentDefinition] | None = None,
        agents: Mapping[str, BoundAgent] | None = None,
        observers: Sequence[Observer] = (),
        context: Mapping[str, Any] | None = None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._runtimes = dict(runtimes or {})
        self._definitions = dict(definitions or {})
        self._agents = dict(agents or {})
        self._observers = tuple(observers)
        self._context = dict(context or {})

    @property
    def config(self) -> Config[Any]:
        return self._config

    @property
    def registry(self) -> Registry:
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

    @property
    def observers(self) -> tuple[Observer, ...]:
        return self._observers

    def agent(self, name: str) -> BoundAgent:
        agent = self._agents.get(name)
        if agent is None:
            known = ", ".join(sorted(self._agents)) or "(none configured)"
            raise NotFoundError(
                f"no agent named {name!r}. Configured: {known}",
                fix="define it under matrix.agents",
            )
        return agent

    def runtime(self, name: str) -> AgentRuntime:
        runtime = self._runtimes.get(name)
        if runtime is None:
            known = ", ".join(sorted(self._runtimes)) or "(none configured)"
            raise NotFoundError(
                f"no runtime named {name!r}. Configured: {known}",
                fix="define it under matrix.runtimes",
            )
        return runtime

    def bind(self, definition: AgentDefinition, runtime: str) -> BoundAgent:
        """Bind a definition to a configured runtime, with this container's observers."""
        return BoundAgent(definition, self.runtime(runtime), observers=self._observers)

    def needs(self) -> dict[str, Any]:
        """The shared things extensions may declare they need."""
        return {
            "agents": dict(self._agents),
            "models": self._config.matrix.models,
            "cwd": self._context.get("cwd"),
            **{k: v for k, v in self._context.items() if k != "cwd"},
        }

    def compile(self, flow: Flow) -> CompiledFlow:
        """Compile ``flow``, building members given by extension id through the registry."""
        needs = self.needs()

        def resolve(extension: str, config: dict[str, Any], where: str) -> Any:
            return self._registry.create("component", extension, config, needs=needs, where=where)

        return compile_flow(flow, resolve=resolve, schemas=self._registry.payload_schemas())

    def executor(self) -> Executor:
        return Executor(self._observers)


def default_registry(*, discover: bool = True) -> Registry:
    """A registry with every ``matrix.extensions`` entry point loaded (built-ins included).

    ``discover=False`` registers only matrix's built-ins, for tests and hermetic callers.
    """
    registry = Registry()
    if discover:
        registry.discover()
        if ("runtime", runtime_type_url("mock")) not in registry:
            register_builtins(registry)  # running from a source tree with no installed metadata
    else:
        register_builtins(registry)
    return registry


def build_runtimes(
    matrix: MatrixConfig,
    registry: Registry,
    needs: Mapping[str, Any],
    *,
    source: str = "matrix",
) -> dict[str, AgentRuntime]:
    runtimes: dict[str, AgentRuntime] = {}
    for name, spec in matrix.runtimes.items():
        url = runtime_type_url(spec.type)
        if ("runtime", url) not in registry:
            legal = sorted(
                e.type_url.removeprefix(RUNTIME_PREFIX) for e in registry.entries("runtime")
            )
            raise ConfigError(
                f"{source}: runtimes.{name}.type: {spec.type!r} is not a registered runtime. "
                f"Registered: {', '.join(legal) or '(none)'}",
                fix="use one of the registered runtimes, or install the package providing it",
            )
        runtimes[name] = registry.create(
            "runtime", url, spec.options(), needs=needs, where=f"{source}: runtimes.{name}"
        )
    return runtimes


def build_observers(matrix: MatrixConfig, registry: Registry) -> list[Observer]:
    return [
        registry.create("observer", observer_type_url(name), where=f"matrix: observers[{i}]")
        for i, name in enumerate(matrix.observers)
    ]


def load_definitions(sources: Sequence[DefinitionSource]) -> dict[str, AgentDefinition]:
    definitions: dict[str, AgentDefinition] = {}
    origin: dict[str, str] = {}
    for source in sources:
        for definition in source.load():
            if definition.name in definitions:
                raise ConfigError(
                    f"agent definition {definition.name!r} is defined in both "
                    f"{origin[definition.name]} and {source.describe()}",
                    fix="rename one, or drop one of the definition directories",
                )
            definitions[definition.name] = definition
            origin[definition.name] = source.describe()
    return definitions


def bind_agents(
    matrix: MatrixConfig,
    runtimes: Mapping[str, AgentRuntime],
    definitions: Mapping[str, AgentDefinition],
    observers: Sequence[Observer] = (),
    *,
    source: str = "matrix",
) -> dict[str, BoundAgent]:
    agents: dict[str, BoundAgent] = {}
    for name, spec in matrix.agents.items():
        runtime = runtimes.get(spec.runtime)
        if runtime is None:
            raise ConfigError(
                f"{source}: agents.{name}.runtime: {spec.runtime!r} is not a configured runtime. "
                f"Configured: {', '.join(sorted(runtimes)) or '(none)'}",
                fix="name a runtime defined under matrix.runtimes",
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
                    "system_prompt was given",
                    fix="add the definition file, or give system_prompt inline",
                )
            definition = AgentDefinition.model_validate({"name": name, **overrides})
        else:
            definition = AgentDefinition.model_validate(
                {**base.model_dump(), **overrides, "name": name}
            )
        try:
            agents[name] = BoundAgent(definition, runtime, observers=observers)
        except ConfigError as e:
            raise ConfigError(
                f"{source}: agents.{name}: {e.message}", fix=e.fix, **e.details
            ) from None
    return agents


def compose(
    config: Config[Any] | MatrixConfig,
    *,
    registry: Registry | None = None,
    base_dir: Path | str | None = None,
    source: str = "matrix",
    definition_sources: Sequence[DefinitionSource] = (),
    context: Mapping[str, Any] | None = None,
) -> Container:
    """Config → Container. Makes no model or tool call.

    ``base_dir`` anchors relative ``definitions`` directories that ``load_config`` did not
    already resolve. ``definition_sources`` adds sources beyond the configured directories.
    ``context`` supplies shared needs (``{"cwd": ...}``) to extensions that declare them.
    """
    full = config if isinstance(config, Config) else Config[Any](matrix=config, client=None)
    matrix = full.matrix
    reg = registry if registry is not None else default_registry()
    root = Path(base_dir) if base_dir is not None else Path.cwd()
    sources: list[DefinitionSource] = [
        MarkdownDefinitionSource(Path(d) if Path(d).is_absolute() else root / d)
        for d in matrix.definitions
    ]
    sources.extend(definition_sources)
    shared = {"cwd": None, "models": matrix.models, **dict(context or {})}
    observers = build_observers(matrix, reg)
    runtimes = build_runtimes(matrix, reg, shared, source=source)
    definitions = load_definitions(sources)
    agents = bind_agents(matrix, runtimes, definitions, observers, source=source)
    return Container(
        full,
        reg,
        runtimes=runtimes,
        definitions=definitions,
        agents=agents,
        observers=observers,
        context=shared,
    )

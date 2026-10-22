"""The documents the ``matrix`` command prints: what is installed, whether a config
composes, and what a run would do. Each is a frozen record whose :meth:`document` is the
``matrix.v1.*`` document ``--json`` prints; the field lists here are the published shape.
Nothing here runs a component or calls a model.

Each function that reads a config takes the ``tool`` whose tiers to read and, optionally, more
files (``config``: one location or several) layered above them, what ``matrix plan CONFIG``
names; with ``only``, the files named are read and the tiers are not.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from matrix.composition.builtins import (
    AGENT_RUNTIME,
    MODEL_RUNTIME,
    agent_runtime_type_url,
    model_runtime_type_url,
    store_type_url,
)
from matrix.composition.config import discover_sources, load_config
from matrix.composition.container import build_store, compose, default_registry
from matrix.domain.agent_step import AGENT_STEP
from matrix.domain.errors import ConfigError, NotFoundError, UsageError, invalid
from matrix.domain.model_step import MODEL_STEP
from matrix.domain.ports._out.construct_store import location_of
from matrix.domain.problems import Problem, problem, sourced

if TYPE_CHECKING:
    from matrix.composition.config import Location
    from matrix.composition.container import Container
    from matrix.domain.component import Wiring
    from matrix.domain.config import Config
    from matrix.domain.registry import Registry


class _Document(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    def document(self) -> dict[str, Any]:
        """The record as JSON data, keyed as ``--json`` prints it."""
        data: dict[str, Any] = self.model_dump(mode="json", by_alias=True)
        return data


class CatalogEntry(_Document):
    point: str
    type_url: str
    summary: str
    stability: str
    #: The distribution that registered it, or ``(direct)``.
    origin: str
    needs: list[str]
    #: Whether it stands in for the real thing: a mock's canned answers, not a model's.
    stand_in: bool = False
    #: The distributions it drives, by their installed names.
    drives: list[str] = []
    #: Whether it takes a config (``matrix describe`` shows the schema).
    configurable: bool


class CatalogFailure(_Document):
    """An extension that was quarantined: ``extension`` is its entry point's name."""

    extension: str
    origin: str
    error: str


class Catalog(_Document):
    schema_: Literal["matrix.v1.catalog"] = Field(default="matrix.v1.catalog", alias="schema")
    entries: list[CatalogEntry]
    failures: list[CatalogFailure]


class ExtensionDescription(_Document):
    point: str
    type_url: str
    summary: str
    stability: str
    origin: str
    needs: list[str]
    stand_in: bool = False
    drives: list[str] = []
    #: The JSON schema its config must meet; None when it takes none.
    config: dict[str, Any] | None


class Extensions(_Document):
    schema_: Literal["matrix.v1.extension"] = Field(default="matrix.v1.extension", alias="schema")
    #: One per point the type URL is registered at.
    entries: list[ExtensionDescription]


class ConfigSources(_Document):
    schema_: Literal["matrix.v1.config-sources"] = Field(
        default="matrix.v1.config-sources", alias="schema"
    )
    #: Lowest priority first.
    sources: list[str]


class ConfigView(_Document):
    schema_: Literal["matrix.v1.config"] = Field(default="matrix.v1.config", alias="schema")
    tool: str
    sources: list[str]
    #: The merged ``matrix:`` section.
    matrix: dict[str, Any]


class CheckReport(_Document):
    schema_: Literal["matrix.v1.check"] = Field(default="matrix.v1.check", alias="schema")
    #: Always true: a config that does not compose raises instead.
    ok: Literal[True] = True
    agent_runtimes: list[str]
    model_runtimes: list[str]
    agents: list[str]
    #: How many observers composed.
    observers: int
    #: The configured components in the order they would run.
    components: list[str]
    sources: list[str]
    #: What composed but a reader should know: a permissive runtime, export the install
    #: cannot do.
    warnings: list[Problem] = []


class AgentSummary(_Document):
    name: str
    runtime: str
    runtime_type: str
    model: str | None
    #: None when the definition does not restrict its tools.
    tools: list[str] | None
    description: str


class Agents(_Document):
    schema_: Literal["matrix.v1.agents"] = Field(default="matrix.v1.agents", alias="schema")
    agents: list[AgentSummary]
    warnings: list[Problem] = []


class PlannedComponent(_Document):
    name: str
    type: str
    requires: list[str]
    provides: list[str]
    #: The components it waits for.
    after: list[str]
    #: For an ``agent-step``: the agent it runs, and that agent's agent runtime. For a
    #: ``model-step``: its model runtime, and the model it names (None: the runtime's
    #: default).
    agent: str | None = None
    runtime: str | None = None
    model: str | None = None
    #: How many times it may be run (its retry policy's attempts).
    attempts: int = 1


class PlannedAgent(_Document):
    name: str
    runtime: str
    runtime_type: str
    model: str | None


class PlannedRuntime(_Document):
    name: str
    type: str
    #: Whether it stands in for the real thing: its answers are canned, not a model's.
    stand_in: bool


class RunSummary(_Document):
    run_id: str
    #: ``running``, ``completed``, ``failed``, ``cancelled``, or ``interrupted`` for one whose
    #: writer is gone. Open: tolerate a status you do not know.
    status: str
    started: str | None
    ended: str | None
    #: Where its record is: for the JSONL store, the directory holding ``run.json``, a
    #: ``<kind>.jsonl`` per kind, ``matrix.v1.ledger.jsonl`` and ``events.jsonl``.
    location: str


class Runs(_Document):
    schema_: Literal["matrix.v1.runs"] = Field(default="matrix.v1.runs", alias="schema")
    #: The configured store's type URL.
    store: str
    #: Oldest first.
    runs: list[RunSummary]


class StorePlan(_Document):
    type: str
    #: Where a run's record would be, with ``RUN_ID`` standing for the run's id.
    location: str


class Plan(_Document):
    schema_: Literal["matrix.v1.plan"] = Field(default="matrix.v1.plan", alias="schema")
    #: Always true: a plan with a problem raises instead, with every problem.
    ok: Literal[True] = True
    tool: str
    sources: list[str]
    #: The kinds a run takes (``--input KIND=PATH``).
    inputs: list[str]
    #: The order of work: each stage's components start together once the stage before has
    #: finished, as far as the wiring orders them.
    stages: list[list[str]]
    components: list[PlannedComponent]
    agents: list[PlannedAgent]
    agent_runtimes: list[PlannedRuntime]
    model_runtimes: list[PlannedRuntime]
    #: Agent sessions: at least one per agent step, at most one per attempt. ``max`` is None
    #: when a component that is neither an agent step nor a model step needs matrix's agents
    #: or model runtimes, the only way it can make a session of its own.
    agent_calls_min: int
    agent_calls_max: int | None
    #: Model calls: at least one per model step, at most one per attempt (a structured
    #: reply's repairs are inside one). ``max`` is None as for agent calls.
    model_calls_min: int
    model_calls_max: int | None
    store: StorePlan
    limits: dict[str, Any]
    warnings: list[Problem] = []


def catalog(point: str | None = None, *, registry: Registry | None = None) -> Catalog:
    """Every registered extension, at ``point`` or at every point, and every quarantined one.

    Raises :class:`UsageError` when ``point`` is not a declared extension point.
    """
    registry = default_registry() if registry is None else registry
    if point is not None and point not in registry.points:
        points = sorted(registry.points)
        raise UsageError(
            f"no extension point {point!r}. Points: {', '.join(points)}",
            fix=f"name one of the points: {', '.join(points)}",
            path="point",
            value=point,
            allowed=points,
        )
    entries = [
        CatalogEntry(
            point=e.point,
            type_url=e.type_url,
            summary=e.summary,
            stability=e.stability,
            origin=e.origin,
            needs=sorted(e.needs),
            stand_in=e.stand_in,
            drives=list(e.drives),
            configurable=e.config is not None,
        )
        for e in registry.entries(point)
    ]
    failures = [
        CatalogFailure(extension=f.extension, origin=f.origin, error=f.error)
        for f in registry.failures
    ]
    return Catalog(entries=entries, failures=failures)


def describe_extension(type_url: str, *, registry: Registry | None = None) -> Extensions:
    """What ``type_url`` is registered as: its config schema and needs.

    Raises :class:`NotFoundError` listing every registered type URL when it is not one.
    """
    registry = default_registry() if registry is None else registry
    matches = [e for e in registry.entries() if e.type_url == type_url]
    if not matches:
        known = sorted({e.type_url for e in registry.entries()})
        raise NotFoundError(
            f"nothing is registered as {type_url!r}. Registered: {', '.join(known)}",
            fix="name a registered type URL; `matrix catalog` lists them",
            value=type_url,
            allowed=known,
        )
    return Extensions(
        entries=[
            ExtensionDescription(
                point=e.point,
                type_url=e.type_url,
                summary=e.summary,
                stability=e.stability,
                origin=e.origin,
                needs=sorted(e.needs),
                stand_in=e.stand_in,
                drives=list(e.drives),
                config=None if e.config is None else e.config.model_json_schema(),
            )
            for e in matches
        ]
    )


def _sources(
    tool: str, config: Location | Sequence[Location] | None, only: bool
) -> list[Any] | None:
    """The tiers for ``tool`` with the ``config`` files above them, or with ``only`` those
    files alone; None for the tiers as discovered."""
    named: list[Any] = (
        []
        if config is None
        else list(config)
        if isinstance(config, Sequence) and not isinstance(config, str)
        else [config]
    )
    if only:
        if not named:
            raise UsageError(
                "--only reads the CONFIG files named and no tier, but none was named",
                fix="name the config to read: matrix plan CONFIG --only",
            )
        return named
    return [*discover_sources(tool), *named] if named else None


def _load(tool: str, config: Location | Sequence[Location] | None, only: bool) -> Config[Any]:
    return load_config(client_key=tool, sources=_sources(tool, config, only))


def config_sources(
    tool: str = "matrix",
    config: Location | Sequence[Location] | None = None,
    *,
    only: bool = False,
) -> ConfigSources:
    """The files ``tool``'s config is read from. Raises ConfigError as :func:`load_config`."""
    return ConfigSources(sources=list(_load(tool, config, only).sources))


def config_view(
    tool: str = "matrix",
    config: Location | Sequence[Location] | None = None,
    *,
    only: bool = False,
) -> ConfigView:
    """The merged config ``tool`` would see. Raises ConfigError as :func:`load_config`."""
    loaded = _load(tool, config, only)
    return ConfigView(
        tool=tool, sources=list(loaded.sources), matrix=loaded.matrix.model_dump(mode="json")
    )


def _composed(loaded: Config[Any]) -> Container:
    """``loaded`` composed. A config that does not compose leaves its components unwired, so
    the error says the check is incomplete when there are components to wire."""
    try:
        return compose(loaded)
    except ConfigError as e:
        if loaded.matrix.components and e.complete:
            raise invalid(
                "the config does not compose", e.problems, complete=False, **e.details
            ) from None
        raise


def check_config(
    tool: str = "matrix",
    config: Location | Sequence[Location] | None = None,
    *,
    only: bool = False,
) -> CheckReport:
    """Compose ``tool``'s config and wire its components, calling nothing.

    Raises the ConfigError or NotFoundError that composing or wiring raises, with every
    problem it found.
    """
    loaded = _load(tool, config, only)
    container = _composed(loaded)
    return CheckReport(
        agent_runtimes=sorted(container.agent_runtimes),
        model_runtimes=sorted(container.model_runtimes),
        agents=sorted(container.agents),
        observers=len(container.observers),
        components=list(container.wire().order()),
        sources=list(loaded.sources),
        warnings=list(container.warnings),
    )


def list_agents(
    tool: str = "matrix",
    config: Location | Sequence[Location] | None = None,
    *,
    only: bool = False,
) -> Agents:
    """The agents ``tool``'s config defines, composed, with their runtimes. Calls nothing."""
    loaded = _load(tool, config, only)
    container = _composed(loaded)
    rows = []
    for name, agent in sorted(container.agents.items()):
        spec = loaded.matrix.agents[name]
        tools = agent.definition.tools
        rows.append(
            AgentSummary(
                name=name,
                runtime=spec.runtime,
                runtime_type=loaded.matrix.agent_runtimes[spec.runtime].type,
                model=agent.definition.model,
                tools=None if tools is None else list(tools),
                description=agent.definition.description,
            )
        )
    return Agents(agents=rows, warnings=list(container.warnings))


def plan_config(
    tool: str = "matrix",
    config: Location | Sequence[Location] | None = None,
    *,
    only: bool = False,
) -> Plan:
    """What a run of ``tool``'s configured components would do: the order of work, the
    agents and runtimes it uses, how many agent and model calls it
    makes, where it would be recorded. Composes and wires; runs nothing and calls nothing.

    Raises ConfigError with every problem found (``complete`` False when one kept a check
    from being made).
    """
    loaded = _load(tool, config, only)
    container = _composed(loaded)
    wiring = container.wire()
    strangers = sorted(set(container.limits.components) - set(wiring.components))
    if strangers:
        raise invalid(
            "the limits do not fit the components",
            [
                problem(
                    f"matrix.limits.components.{name}",
                    "name_unknown",
                    f"limits name {name!r}, which is not a configured component",
                    allowed=sorted(wiring.components),
                    got=name,
                )
                for name in strangers
            ],
        )
    return _plan(tool, loaded, container, wiring)


def list_runs(
    tool: str = "matrix",
    config: Location | Sequence[Location] | None = None,
    *,
    only: bool = False,
) -> Runs:
    """The runs kept in ``tool``'s configured store, oldest first, each with its status,
    times and location. Builds only the store: runtimes and agents are neither built nor
    checked, so a config that does not compose still lists its runs.

    Raises ConfigError when the store cannot be built, StoreError when it cannot be read.
    """
    loaded = _load(tool, config, only)
    try:
        store = build_store(loaded.matrix, default_registry())
    except ConfigError as e:
        raise invalid("the store cannot be built", sourced(e.problems, loaded.origins)) from None
    if store is None:  # the default registry always holds matrix's own stores
        raise NotFoundError("no store is registered", fix="install matrix with its built-ins")
    found = []
    for run_id in store.runs():
        record = store.describe(run_id)
        found.append(
            RunSummary(
                run_id=run_id,
                status=str(record.get("status")),
                started=record.get("started"),
                ended=record.get("ended"),
                location=location_of(store, run_id),
            )
        )
    return Runs(store=store_type_url(loaded.matrix.store.type), runs=found)


#: The needs that hand a component matrix's runtimes: the only way one that is neither an
#: agent step nor a model step can make a session of its own.
_RUNTIME_NEEDS = frozenset({"agents", "model_runtimes"})


def _stages(wiring: Wiring) -> list[list[str]]:
    placed: set[str] = set()
    stages: list[list[str]] = []
    while len(placed) < len(wiring.components):
        stage = [
            name
            for name in wiring.components
            if name not in placed and all(d in placed for d in wiring.depends[name])
        ]
        stages.append(stage)
        placed.update(stage)
    return stages


def _plan(tool: str, loaded: Config[Any], container: Container, wiring: Wiring) -> Plan:
    matrix = loaded.matrix
    entries = {entry.name: entry for entry in matrix.components}
    components: list[PlannedComponent] = []
    used: dict[str, str] = {}
    models_used: set[str] = set()
    agent_min = agent_max = model_min = model_max = 0
    countable = True  # no component but an agent or a model step can reach a runtime
    for name in wiring.order():
        component = wiring.components[name]
        entry = entries[name]
        attempts = container.limits.retry_of(name).attempts
        agent = runtime = model = None
        if entry.type == AGENT_STEP:
            agent = str(entry.config.get("agent"))
            runtime = matrix.agents[agent].runtime
            used[agent] = runtime
            agent_min, agent_max = agent_min + 1, agent_max + attempts
        elif entry.type == MODEL_STEP:
            runtime = str(entry.config.get("runtime"))
            model = entry.config.get("model")
            models_used.add(runtime)
            model_min, model_max = model_min + 1, model_max + attempts
        elif _RUNTIME_NEEDS & container.registry.entry("component", entry.type).needs:
            countable = False
        components.append(
            PlannedComponent(
                name=name,
                type=entry.type,
                requires=sorted(component.requires),
                provides=sorted(component.provides),
                after=list(wiring.depends[name]),
                agent=agent,
                runtime=runtime,
                model=None if model is None else str(model),
                attempts=attempts,
            )
        )
    return Plan(
        tool=tool,
        sources=list(loaded.sources),
        inputs=sorted(wiring.inputs),
        stages=_stages(wiring),
        components=components,
        agents=[
            PlannedAgent(
                name=name,
                runtime=runtime,
                runtime_type=matrix.agent_runtimes[runtime].type,
                model=container.agents[name].definition.model,
            )
            for name, runtime in sorted(used.items())
        ],
        agent_runtimes=[
            _planned(container, AGENT_RUNTIME, name, matrix.agent_runtimes[name].type)
            for name in sorted(set(used.values()))
        ],
        model_runtimes=[
            _planned(container, MODEL_RUNTIME, name, matrix.model_runtimes[name].type)
            for name in sorted(models_used)
        ],
        agent_calls_min=agent_min,
        agent_calls_max=agent_max if countable else None,
        model_calls_min=model_min,
        model_calls_max=model_max if countable else None,
        store=StorePlan(
            type=store_type_url(matrix.store.type),
            location=location_of(container.store, "RUN_ID"),
        ),
        limits=container.limits.model_dump(mode="json", exclude_defaults=True),
        warnings=list(container.warnings),
    )


def _planned(container: Container, point: str, name: str, kind: str) -> PlannedRuntime:
    """The runtime ``name`` of type ``kind`` at ``point``, as a plan lists it."""
    url = agent_runtime_type_url(kind) if point == AGENT_RUNTIME else model_runtime_type_url(kind)
    entry = container.registry.entry(point, url)
    return PlannedRuntime(name=name, type=kind, stand_in=entry.stand_in)

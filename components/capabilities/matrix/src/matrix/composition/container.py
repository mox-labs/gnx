"""Composition root: config in, runtimes, definitions, agents, observers and a store out.

The only place that joins domain concepts to concrete adapters. :func:`compose` builds, in
order, and reports **every** problem it meets as one ConfigError, each a
:class:`~matrix.domain.problems.Problem` with its key path:

1. **observers**, each ``matrix.observers`` entry resolved through the registry, and the
   ``otel`` observer when the standard ``OTEL_*`` variables ask for export;
2. **runtimes**, each ``matrix.runtimes.<name>`` resolved by its type (a built-in short name
   or a full type URL), its options validated by that runtime's typed config;
3. **the store**, ``matrix.store``, resolved the same way at the ``store`` point;
4. **definitions**, every ``*.md`` in each ``matrix.definitions`` directory, read by the
   loader as :class:`~matrix.domain.agent.AgentDefinition`;
5. **agents**, each ``matrix.agents.<name>`` binding a definition (plus inline overrides) to a
   named runtime. The runtime's ``check`` runs here, so a definition it cannot honour fails now.

Nothing is called: composition makes no model call and no tool call. What a reader should
know but that is not wrong (a permissive runtime, export asked for without its extra) is in
:attr:`Container.warnings`.

:meth:`Container.wire` then turns a list of ``{name, type, config}`` entries (the config's
``matrix.components``, or the caller's own) into components and wires them by kind, and
:meth:`Container.run` runs them with the configured store and limits.

**Needs.** An extension declares the shared things it needs (``agents``, ``models``, ``cwd``;
a component may also ask for its ``name``) when it registers; composition hands over exactly
those. Nothing is matched by field name.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any

from matrix.adapters._in.signals import cancel_on_signals
from matrix.composition import telemetry
from matrix.composition.builtins import (
    RUNTIME_PREFIX,
    observer_type_url,
    runtime_type_url,
    store_type_url,
)
from matrix.composition.builtins import register as register_builtins
from matrix.composition.config import default_loader
from matrix.domain.agent import AgentDefinition, BoundAgent
from matrix.domain.agent_step import AGENT_RESPONSE, AGENT_STEP
from matrix.domain.component import Wiring, wire
from matrix.domain.config import ComponentConfig, Config, MatrixConfig
from matrix.domain.errors import (
    CompilationError,
    ConfigError,
    MatrixError,
    NotFoundError,
    invalid,
)
from matrix.domain.executor import Executor, Limits, Reuse
from matrix.domain.ports._out.construct_store import location_of
from matrix.domain.problems import problem, sourced
from matrix.domain.registry import Extension, Registry

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from importlib.metadata import EntryPoint

    from matrix.domain.executor import Run
    from matrix.domain.loading import Loader
    from matrix.domain.observer import Observer
    from matrix.domain.ports._out.agent_runtime import AgentRuntime
    from matrix.domain.ports._out.construct_store import ConstructStore
    from matrix.domain.problems import Problem


class Container:
    """What composition produced: agents by name, the store, the limits, and the means to
    wire and run components."""

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
        store: ConstructStore | None = None,
        warnings: Sequence[Problem] = (),
    ) -> None:
        self._config = config
        self._registry = registry
        self._runtimes = dict(runtimes or {})
        self._definitions = dict(definitions or {})
        self._agents = dict(agents or {})
        self._observers = tuple(observers)
        self._context = dict(context or {})
        self._store = store
        self._warnings = tuple(warnings)

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

    @property
    def store(self) -> ConstructStore:
        """Where runs are recorded: ``matrix.store``, built."""
        if self._store is None:
            raise ConfigError(
                "this container has no store",
                fix="compose it from a config (matrix.store), or pass a store to the Executor",
            )
        return self._store

    @property
    def limits(self) -> Limits:
        """``matrix.limits``: the bounds and the retry policy every run keeps."""
        return self._config.matrix.limits

    @property
    def warnings(self) -> tuple[Problem, ...]:
        """What composition found that a reader should know and that is not an error."""
        return self._warnings

    def agent(self, name: str) -> BoundAgent:
        agent = self._agents.get(name)
        if agent is None:
            known = ", ".join(sorted(self._agents)) or "(none configured)"
            raise NotFoundError(
                f"no agent named {name!r}. Configured: {known}",
                fix="define it under matrix.agents",
                value=name,
                allowed=sorted(self._agents),
            )
        return agent

    def runtime(self, name: str) -> AgentRuntime:
        runtime = self._runtimes.get(name)
        if runtime is None:
            known = ", ".join(sorted(self._runtimes)) or "(none configured)"
            raise NotFoundError(
                f"no runtime named {name!r}. Configured: {known}",
                fix="define it under matrix.runtimes",
                value=name,
                allowed=sorted(self._runtimes),
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

    def wire(
        self,
        components: Sequence[ComponentConfig | Mapping[str, Any]] | None = None,
        *,
        inputs: Iterable[str] | None = None,
    ) -> Wiring:
        """Build each ``{name, type, config}`` entry through the registry and wire them.

        ``inputs`` are the kinds the caller will supply to a run. Both default to what the
        config holds (``matrix.components``, ``matrix.inputs``), and then every problem's
        path starts ``matrix.``. Every problem is reported at once, as one CompilationError:
        an entry that cannot be built, and whatever :func:`~matrix.domain.component.wire`
        finds in those that can.

        The wiring carries each component's declared effects (an ``agent-step`` has its
        agent's runtime's) and its fingerprint, which a later run compares before it reuses
        the component's rows: a digest of its type, its config, and each need it is handed
        (an ``agent-step``'s own agent, every agent for another component that needs
        ``agents``, ``models``, ``cwd``). An agent counts by its definition, its runtime's
        config and needs, and what that runtime resolved when it was built.
        """
        configured = components is None
        prefix = "matrix." if configured else ""
        entries = [
            c if isinstance(c, ComponentConfig) else ComponentConfig.model_validate(dict(c))
            for c in (self._config.matrix.components if components is None else components)
        ]
        kinds = self._config.matrix.inputs if inputs is None else tuple(inputs)
        built: dict[str, Any] = {}
        effects: dict[str, frozenset[str] | None] = {}
        prints: dict[str, str] = {}
        problems: list[Problem] = []
        seen: set[str] = set()
        for entry in entries:
            where = f"{prefix}components.{entry.name}"
            if entry.name in seen:
                problems.append(
                    problem(
                        where, "name_taken", "name used twice", fix="rename one", got=entry.name
                    )
                )
                continue
            seen.add(entry.name)
            try:
                built[entry.name] = self._registry.create(
                    "component",
                    entry.type,
                    entry.config,
                    needs={**self.needs(), "name": entry.name},
                    where=f"{where}.config",
                )
                effects[entry.name] = self._effects(entry)
                prints[entry.name] = self._fingerprint(entry)
            except MatrixError as e:
                problems += _problems_of(e, where, entry)
        try:
            wiring = wire(
                built,
                inputs=kinds,
                schemas=self._registry.payload_schemas(),
                effects=effects,
                fingerprints=prints,
            )
            if not problems:
                return wiring
        except CompilationError as e:
            found = _agent_step_clashes(e.problems, entries)
            problems += [p.at(prefix.rstrip(".")) if prefix else p for p in found]
        raise invalid(
            "the components cannot be wired",
            sourced(problems, self._config.origins) if configured else problems,
            fix="fix each listed component or kind; every problem is reported at once",
            error=CompilationError,
        )

    def _effects(self, entry: ComponentConfig) -> frozenset[str] | None:
        if entry.type == AGENT_STEP:
            spec = self._config.matrix.agents.get(str(entry.config.get("agent")))
            runtime = self._config.matrix.runtimes.get(spec.runtime) if spec else None
            if runtime is None:
                return None
            return self._registry.entry("runtime", runtime_type_url(runtime.type)).effects
        return self._registry.entry("component", entry.type).effects

    def _fingerprint(self, entry: ComponentConfig) -> str:
        """A digest of what the component is built from: its type and config, and each need
        it is handed that decides what it does. An agent step is handed its own agent; any
        other component that needs ``agents`` is handed every agent."""
        needs = set(self._registry.entry("component", entry.type).needs)
        made_of: dict[str, Any] = {"type": entry.type, "config": entry.config}
        if entry.type == AGENT_STEP:
            needs.discard("agents")
            made_of["agent"] = self._agent_made_of(str(entry.config.get("agent")))
        made_of["needs"] = self._needs_made_of(needs)
        text = json.dumps(made_of, sort_keys=True, default=str)
        return "sha256:" + hashlib.sha256(text.encode()).hexdigest()

    def _agent_made_of(self, name: str) -> dict[str, Any] | None:
        """What decides how the agent ``name`` answers: its definition, its runtime's config
        and the needs that runtime was handed, and what the runtime resolved when it was
        built (``resolved``: a model runtime's registry, from ``matrix.models`` or the tiers
        hardline discovered)."""
        agent = self._agents.get(name)
        spec = self._config.matrix.agents.get(name)
        if agent is None or spec is None:
            return None
        runtime = self._config.matrix.runtimes.get(spec.runtime)
        found: dict[str, Any] = {
            "definition": agent.definition.model_dump(mode="json"),
            "runtime": None if runtime is None else runtime.model_dump(mode="json"),
        }
        url = None if runtime is None else runtime_type_url(runtime.type)
        if url is not None and ("runtime", url) in self._registry:
            found["needs"] = self._needs_made_of(self._registry.entry("runtime", url).needs)
        resolved = getattr(agent.runtime, "resolved", None)
        if resolved is not None:
            found["resolved"] = resolved
        return found

    def _needs_made_of(self, needs: Iterable[str]) -> dict[str, Any]:
        """The value of each need matrix hands over, as it decides the work: every agent, the
        model registry, the working directory (the process's when none was given). A need a
        host adds through ``context`` is its own, and not digested."""
        found: dict[str, Any] = {}
        for need in sorted(needs):
            if need == "agents":
                found[need] = {name: self._agent_made_of(name) for name in sorted(self._agents)}
            elif need == "models":
                found[need] = self._config.matrix.models
            elif need == "cwd":
                found[need] = str(self._context.get("cwd") or Path.cwd())
        return found

    def executor(self, observers: Sequence[Observer] = ()) -> Executor:
        """An executor over the composed observers (and ``observers``) that turns SIGTERM and
        SIGINT into the run's cancellation."""
        return Executor([*self._observers, *observers], interrupts=cancel_on_signals)

    def location(self, run_id: str) -> str:
        """Where the store keeps (or would keep) the run ``run_id``."""
        return location_of(self.store, run_id)

    def reuse(self, run_id: str, wiring: Wiring | None = None) -> Reuse:
        """What of the recorded run ``run_id`` a run of ``wiring`` (the configured one by
        default) may reuse: each component that completed there (``completed`` or
        ``reused``, never ``stopped``), was built from the same fingerprint, and whose
        providers are all reused too, with its rows as recorded.

        Rows come back as the store keeps them (JSON data); the executor validates each into
        its kind's schema as it records it, as on a fresh run. Raises NotFoundError when there
        is no such run.
        """
        wiring = wiring or self.wire()
        described = self.store.describe(run_id)
        construct = self.store.load(run_id)
        prints = described.get("fingerprints") or {}
        endings = described.get("components") or {}
        reused: dict[str, dict[str, tuple[Any, ...]]] = {}
        for name in wiring.order():
            mine = wiring.fingerprints.get(name)
            ending = endings.get(name) or {}
            if (
                mine is not None
                and prints.get(name) == mine
                and _finished(ending)
                and all(d in reused for d in wiring.depends[name])
            ):
                provides = wiring.components[name].provides
                reused[name] = {kind: construct.rows(kind) for kind in provides}
        return Reuse(run_id=run_id, rows=reused)

    def inputs_of(self, run_id: str, wiring: Wiring | None = None) -> dict[str, tuple[Any, ...]]:
        """The run inputs recorded by run ``run_id``, by kind, for a run of ``wiring``."""
        wiring = wiring or self.wire()
        construct = self.store.load(run_id)
        return {kind: construct.rows(kind) for kind in sorted(wiring.inputs)}

    async def run(
        self,
        inputs: Mapping[str, Any] | None = None,
        *,
        run_id: str | None = None,
        reuse_from: str | None = None,
        observers: Sequence[Observer] = (),
    ) -> Run:
        """Run the configured components, recorded in the configured store, within the
        configured limits; return the Run.

        ``inputs`` maps each run-input kind to its rows. ``reuse_from`` starts from the
        recorded run of that id: its inputs, and the rows of every component
        :meth:`reuse` finds reusable; it takes no ``inputs``. ``run_id`` defaults to
        ``<UTC time>-<4 hex digits>``, which sorts by time. Raises as
        :meth:`Executor.run <matrix.domain.executor.Executor.run>` does.
        """
        wiring = self.wire()
        reuse = None
        if reuse_from is not None:
            if inputs is not None:
                raise ConfigError(
                    "a run that reuses another takes that run's inputs, not new ones",
                    fix="drop the inputs, or start a new run without reuse_from",
                )
            inputs = self.inputs_of(reuse_from, wiring)
            reuse = self.reuse(reuse_from, wiring)
        return await self.executor(observers).run(
            wiring,
            inputs,
            limits=self.limits,
            store=self.store,
            run_id=run_id or new_run_id(),
            reuse=reuse,
        )


def _finished(ending: Mapping[str, Any]) -> bool:
    """Whether a recorded ending is finished work. A ``completed`` that carries a stop's
    reason is a component that returned after a stop request, as records written before
    ``stopped`` existed say it: partial, like ``stopped``."""
    status = ending.get("status")
    return status == "reused" or (status == "completed" and ending.get("reason") is None)


def new_run_id() -> str:
    """``<UTC time>-<4 hex digits>``: sorts by time; two runs in one second do not collide."""
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(2)}"


def _agent_step_clashes(
    problems: Sequence[Problem], entries: Sequence[ComponentConfig]
) -> list[Problem]:
    """``problems``, with a kind that two agent steps both provide as their reply pointed at
    the key that fixes it: each step's own ``response``."""
    responses = {
        entry.name: str(entry.config.get("response", AGENT_RESPONSE))
        for entry in entries
        if entry.type == AGENT_STEP
    }
    found: list[Problem] = []
    for item in problems:
        name = item.path.removeprefix("components.").removesuffix(".provides")
        clashing = sorted(step for step, kind in responses.items() if kind == item.got)
        if item.kind == "name_taken" and name in clashing and len(clashing) > 1:
            item = item.model_copy(
                update={
                    "path": f"components.{name}.config.response",
                    "message": f"agent steps {', '.join(clashing)} all provide their reply as "
                    f"{item.got!r}; a kind has one provider",
                    "fix": "give each agent step its own `response:` kind in its config (as "
                    f"`response: <namespace>.v1.{name}-reply`); the default, "
                    f"{AGENT_RESPONSE}, serves one step",
                }
            )
        found.append(item)
    return found


def _problems_of(error: MatrixError, where: str, entry: ComponentConfig) -> list[Problem]:
    """``error`` from building one component entry, as problems under ``where``."""
    if error.problems:
        return list(error.problems)
    if error.kind == "not_found" and error.details.get("type_url") == entry.type:
        return [
            problem(
                f"{where}.type",
                "name_unknown",
                error.message,
                fix=error.fix
                or "name a registered component; `matrix catalog --point component` lists them",
                got=entry.type,
            )
        ]
    kind = "name_unknown" if error.kind == "not_found" else "invalid_value"
    return [problem(f"{where}.config", kind, error.message, fix=error.fix)]


#: The entry point group every extension, matrix's own built-ins included, registers through.
ENTRY_POINT_GROUP = "matrix.extensions"


def _registers(point: EntryPoint) -> Callable[[Registry], None]:
    """``point``'s ``register``, imported only when it is called, so that an import that
    fails quarantines that one extension."""

    def register(registry: Registry) -> None:
        found = point.load()
        if not callable(found):
            raise TypeError(f"{point.value} is not callable; expected register(registry)")
        found(registry)

    return register


def load_extensions(registry: Registry, group: str = ENTRY_POINT_GROUP) -> Registry:
    """Load every extension installed at the ``group`` entry point into ``registry``."""
    return registry.load(
        Extension(
            name=point.name,
            origin=point.dist.name if point.dist is not None else point.value,
            register=_registers(point),
        )
        for point in entry_points(group=group)
    )


def default_registry(*, discover: bool = True) -> Registry:
    """A registry with every ``matrix.extensions`` entry point loaded, matrix's built-ins
    among them.

    ``discover=False`` loads only matrix's built-ins, for tests and hermetic callers.
    """
    if discover:
        return load_extensions(Registry())
    builtins = Extension(name="matrix", origin="matrix", register=register_builtins)
    return Registry().load([builtins])


class _Problems:
    """The problems composition has met so far, and whether any check was skipped."""

    def __init__(self) -> None:
        self.found: list[Problem] = []
        self.complete = True

    def add(
        self, error: MatrixError, where: str, *, got: Any = None, kind: str | None = None
    ) -> None:
        if error.problems:
            self.found += error.problems
            return
        if "extra" in error.details:  # selected, but its optional dependency is missing
            kind, got = "extra_missing", error.details.get("missing")
        kind = kind or ("name_unknown" if error.kind == "not_found" else "invalid_value")
        self.found.append(problem(where, kind, error.message, fix=error.fix, got=got))


def build_runtimes(
    matrix: MatrixConfig,
    registry: Registry,
    needs: Mapping[str, Any],
    *,
    source: str = "matrix",
    problems: _Problems | None = None,
) -> dict[str, AgentRuntime]:
    """Each configured runtime, built. Raises ConfigError listing every problem unless
    ``problems`` collects them."""
    found = problems or _Problems()
    runtimes: dict[str, AgentRuntime] = {}
    for name, spec in matrix.runtimes.items():
        where = f"{source}.runtimes.{name}"
        url = runtime_type_url(spec.type)
        if ("runtime", url) not in registry:
            legal = sorted(
                e.type_url.removeprefix(RUNTIME_PREFIX) for e in registry.entries("runtime")
            )
            found.found.append(
                problem(
                    f"{where}.type",
                    "name_unknown",
                    f"{spec.type!r} is not a registered runtime",
                    fix="use one of the registered runtimes, or install the package providing it",
                    allowed=legal,
                    got=spec.type,
                )
            )
            continue
        try:
            runtimes[name] = registry.create(
                "runtime", url, spec.options(), needs=needs, where=where
            )
        except MatrixError as e:
            found.add(e, where)
    if problems is None and found.found:
        raise invalid("the runtimes cannot be built", found.found)
    return runtimes


def build_observers(
    matrix: MatrixConfig,
    registry: Registry,
    *,
    source: str = "matrix",
    problems: _Problems | None = None,
) -> list[Observer]:
    """Each configured observer, built, and ``otel`` when the environment asks for export."""
    found = problems or _Problems()
    observers: list[Observer] = []
    named: set[str] = set()
    for i, spec in enumerate(matrix.observers):
        name, options = (spec, {}) if isinstance(spec, str) else (spec.type, spec.options())
        url = observer_type_url(name)
        named.add(url)
        try:
            observers.append(
                registry.create("observer", url, options, where=f"{source}.observers.{i}")
            )
        except MatrixError as e:
            found.add(e, f"{source}.observers.{i}", got=name)
    otel = observer_type_url("otel")
    if telemetry.requested() is not None and otel not in named and ("observer", otel) in registry:
        try:
            observers.append(registry.create("observer", otel))
        except MatrixError as e:
            found.add(e, f"{source}.observers")
    if problems is None and found.found:
        raise invalid("the observers cannot be built", found.found)
    return observers


def build_store(
    matrix: MatrixConfig,
    registry: Registry,
    *,
    source: str = "matrix",
    problems: _Problems | None = None,
) -> ConstructStore | None:
    """The configured store, built; None when the config names none and the registry holds
    no default (a registry without matrix's built-ins). Raises ConfigError unless
    ``problems`` collects them."""
    if problems is None:
        found = _Problems()
        built = build_store(matrix, registry, source=source, problems=found)
        if found.found:
            raise invalid("the store cannot be built", found.found)
        return built
    where = f"{source}.store"
    url = store_type_url(matrix.store.type)
    if ("store", url) not in registry and "store" not in matrix.model_fields_set:
        return None  # the default is matrix's own, and this registry has none of matrix's
    if ("store", url) not in registry:
        legal = sorted(e.type_url for e in registry.entries("store"))
        problems.found.append(
            problem(
                f"{where}.type",
                "name_unknown",
                f"{matrix.store.type!r} is not a registered store",
                fix="name a registered store (jsonl, memory), or install the package providing it",
                allowed=legal,
                got=matrix.store.type,
            )
        )
        return None
    try:
        store: ConstructStore = registry.create("store", url, matrix.store.options(), where=where)
    except MatrixError as e:
        problems.add(e, where)
        return None
    return store


def _definition_problems(
    problems: Sequence[Problem], where: str, directories: Sequence[str]
) -> list[Problem]:
    """The loader's ``problems`` with the definitions, a definition named twice put at the
    config key that brought both in, and why both were read."""
    found: list[Problem] = []
    for item in problems:
        if item.kind == "name_taken":
            item = item.model_copy(
                update={
                    "path": where,
                    "message": f"{item.message}: the `definitions` directories of every "
                    f"config tier are read together ({', '.join(directories)})",
                    "fix": "rename one of the two definitions, drop one directory from a "
                    "tier's `definitions`, or read one config on its own: "
                    "`matrix run CONFIG --only`",
                }
            )
        found.append(item)
    return found


def load_definitions(
    directories: Sequence[str], *, loader: Loader | None = None
) -> dict[str, AgentDefinition]:
    """Every agent definition (``*.md``) in ``directories``, by name. A name defined twice,
    in one directory or across them, is refused with every other problem."""
    if not directories:
        return {}
    found = (loader or default_loader()).each(
        AgentDefinition, *directories, key="name", name_from_file=True, suffixes=(".md",)
    )
    return {definition.name: definition for definition in found}


def bind_agents(
    matrix: MatrixConfig,
    runtimes: Mapping[str, AgentRuntime],
    definitions: Mapping[str, AgentDefinition],
    observers: Sequence[Observer] = (),
    *,
    source: str = "matrix",
    problems: _Problems | None = None,
    unread: bool = False,
) -> dict[str, BoundAgent]:
    """Each configured agent, bound. An agent whose runtime is configured but could not be
    built is skipped, and the check it would have had is reported as not made; so is one
    whose definition is missing when ``unread`` says the definitions could not all be read."""
    found = problems or _Problems()
    agents: dict[str, BoundAgent] = {}
    for name, spec in matrix.agents.items():
        where = f"{source}.agents.{name}"
        runtime = runtimes.get(spec.runtime)
        if runtime is None and spec.runtime not in matrix.runtimes:
            found.found.append(
                problem(
                    f"{where}.runtime",
                    "name_unknown",
                    f"{spec.runtime!r} is not a configured runtime",
                    fix="name a runtime defined under matrix.runtimes",
                    allowed=sorted(matrix.runtimes),
                    got=spec.runtime,
                )
            )
        base_name = spec.definition or name
        base = definitions.get(base_name)
        overrides = {
            k: v
            for k, v in spec.model_dump(exclude={"runtime", "definition"}).items()
            if v is not None
        }
        if base is None and (spec.definition is not None or "system_prompt" not in overrides):
            if unread:  # it may be among the definitions that could not be read
                found.complete = False
                continue
            found.found.append(
                problem(
                    f"{where}.definition",
                    "name_unknown",
                    f"no definition named {base_name!r} was loaded, and no inline "
                    "system_prompt was given",
                    fix="add the definition file, or give system_prompt inline",
                    allowed=sorted(definitions),
                    got=base_name,
                )
            )
            continue
        if runtime is None:
            if spec.runtime in matrix.runtimes:
                found.complete = False  # its runtime failed to build: its check was not made
            continue
        fields = {**(base.model_dump() if base is not None else {}), **overrides, "name": name}
        try:
            definition = AgentDefinition.model_validate(fields)
            agents[name] = BoundAgent(definition, runtime, observers=observers)
        except MatrixError as e:
            found.add(e, where)
        except ValueError as e:  # pydantic's ValidationError
            found.found.append(problem(where, "invalid_value", str(e)))
    if problems is None and found.found:
        raise invalid("the agents cannot be bound", found.found, complete=found.complete)
    return agents


def compose(
    config: Config[Any] | MatrixConfig,
    *,
    registry: Registry | None = None,
    base_dir: Path | str | None = None,
    source: str = "matrix",
    definitions: Iterable[AgentDefinition] = (),
    context: Mapping[str, Any] | None = None,
    loader: Loader | None = None,
) -> Container:
    """Config → Container. Makes no model or tool call.

    ``base_dir`` anchors relative ``definitions`` directories that ``load_config`` did not
    already resolve. ``definitions`` adds definitions the caller holds to those the
    configured directories hold. ``context`` supplies shared needs (``{"cwd": ...}``) to
    extensions that declare them. ``source`` is the key every problem's path starts with.
    ``loader`` reads the definition directories (default: the file source).

    Raises ConfigError listing every problem; ``complete`` is False when a problem kept a
    check from being made (an agent whose runtime could not be built).
    """
    full = config if isinstance(config, Config) else Config[Any](matrix=config, client=None)
    matrix = full.matrix
    reg = registry if registry is not None else default_registry()
    root = Path(base_dir) if base_dir is not None else Path.cwd()
    problems = _Problems()
    shared = {"cwd": None, "models": matrix.models, **dict(context or {})}
    observers = build_observers(matrix, reg, source=source, problems=problems)
    runtimes = build_runtimes(matrix, reg, shared, source=source, problems=problems)
    store = build_store(matrix, reg, source=source, problems=problems)
    loaded: dict[str, AgentDefinition] = {}
    directories = [str(d if Path(d).is_absolute() else root / d) for d in matrix.definitions]
    unread = False
    try:
        loaded = load_definitions(directories, loader=loader)
    except MatrixError as e:
        unread = True
        if e.problems:
            problems.found += _definition_problems(e.problems, f"{source}.definitions", directories)
        else:
            problems.add(e, f"{source}.definitions", kind="invalid_value")
    for definition in definitions:
        if definition.name in loaded:
            problems.found.append(
                problem(
                    f"{source}.definitions",
                    "name_taken",
                    f"agent definition {definition.name!r} is in a definitions directory and "
                    "was also given",
                    fix="rename one, or drop one of them",
                    got=definition.name,
                )
            )
        loaded[definition.name] = definition
    agents = bind_agents(
        matrix, runtimes, loaded, observers, source=source, problems=problems, unread=unread
    )
    if problems.found:
        raise invalid(
            "the config does not compose",
            sourced(problems.found, full.origins),
            complete=problems.complete,
        )
    warnings = [
        notice.at(f"{source}.runtimes.{name}")
        for name, runtime in runtimes.items()
        for notice in getattr(runtime, "warnings", ())
    ]
    warnings += telemetry.warnings()
    return Container(
        full,
        reg,
        runtimes=runtimes,
        definitions=loaded,
        agents=agents,
        observers=observers,
        context=shared,
        store=store,
        warnings=warnings,
    )

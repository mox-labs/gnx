"""Composition root: config in, runtimes, definitions, agents, observers and a store out.

The only place that joins domain concepts to concrete adapters. :func:`compose` builds, in
order, and reports **every** problem it meets as one ConfigError, each a
:class:`~matrix.domain.problems.Problem` with its key path:

1. **observers**, each ``matrix.observers`` entry resolved through the registry, and the
   ``otel`` observer when the standard ``OTEL_*`` variables ask for export;
2. **runtimes**, each ``matrix.model_runtimes.<name>`` at the ``model-runtime`` point, and
   then each ``matrix.agent_runtimes.<name>`` at the ``agent-runtime`` point, which may be
   handed the model runtimes (the ``model_runtimes`` need), resolved by its type (a built-in
   short name or a full type URL), its options validated by that runtime's typed config;
3. **the store**, ``matrix.store``, resolved the same way at the ``store`` point;
4. **definitions**, every ``*.md`` in each ``matrix.definitions`` directory, read by the
   loader as :class:`~matrix.domain.agent.AgentDefinition`;
5. **agents**, each ``matrix.agents.<name>`` binding a definition (plus inline overrides) to a
   named agent runtime. The runtime's ``check`` runs here, so a definition it cannot play
   fails now.

A model step checks its model name with its model runtime when :meth:`Container.wire` builds
it, so an unknown model fails there, with every other problem of the wiring.

Nothing is called: composition makes no model call and no tool call. What a reader should
know but that is not wrong (a permissive runtime, export asked for in an install that cannot
do it) is in :attr:`Container.warnings`.

:meth:`Container.wire` then turns a list of ``{name, type, config}`` entries (the config's
``matrix.components``, or the caller's own) into components and wires them by kind, and
:meth:`Container.run` runs them with the configured store and limits.

**Needs.** An extension declares the shared things it needs when it registers, from
matrix's table (:data:`~matrix.domain.needs.NEEDS`) or the host's own (``context``), and
composition hands over exactly those. Nothing is matched by field name.
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
    AGENT_RUNTIME,
    AGENT_RUNTIME_PREFIX,
    MODEL_RUNTIME,
    MODEL_RUNTIME_PREFIX,
    agent_runtime_type_url,
    model_runtime_type_url,
    observer_type_url,
    store_type_url,
)
from matrix.composition.builtins import register as register_builtins
from matrix.composition.config import default_loader
from matrix.domain.agent import AgentDefinition, BoundAgent
from matrix.domain.agent_step import AGENT_RESPONSE, AGENT_STEP
from matrix.domain.component import Wiring, wire
from matrix.domain.config import ComponentConfig, Config, MatrixConfig, RuntimeConfig
from matrix.domain.errors import (
    CompilationError,
    ConfigError,
    MatrixError,
    NotFoundError,
    invalid,
)
from matrix.domain.executor import Executor, Limits, Reuse
from matrix.domain.model_step import MODEL_CALL, MODEL_STEP
from matrix.domain.needs import NEEDS, ModelRuntimes, model_runtime_named
from matrix.domain.optional import SupportsResolved, SupportsWarnings
from matrix.domain.ports._out.construct_store import location_of
from matrix.domain.problems import problem, sourced
from matrix.domain.registry import MATRIX_POINTS, Extension, Registry

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
    from importlib.metadata import EntryPoint

    from matrix.domain.executor import Run
    from matrix.domain.loading import Loader
    from matrix.domain.observer import Observer
    from matrix.domain.ports._out.agent_runtime import AgentRuntime
    from matrix.domain.ports._out.construct_store import ConstructStore
    from matrix.domain.ports._out.model_runtime import ModelRuntime
    from matrix.domain.problems import Problem


class Container:
    """What composition produced: agents by name, the store, the limits, and the means to
    wire and run components."""

    def __init__(
        self,
        config: Config[Any],
        registry: Registry,
        *,
        agent_runtimes: Mapping[str, AgentRuntime] | None = None,
        model_runtimes: ModelRuntimes | None = None,
        definitions: Mapping[str, AgentDefinition] | None = None,
        agents: Mapping[str, BoundAgent] | None = None,
        observers: Sequence[Observer] = (),
        context: Mapping[str, Any] | None = None,
        store: ConstructStore | None = None,
        warnings: Sequence[Problem] = (),
    ) -> None:
        self._config = config
        self._registry = registry
        self._agent_runtimes = dict(agent_runtimes or {})
        self._model_runtimes = (
            model_runtimes
            if model_runtimes is not None
            else _ComposedModelRuntimes({}, config.matrix.model_runtimes, registry)
        )
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
    def agent_runtimes(self) -> Mapping[str, AgentRuntime]:
        return dict(self._agent_runtimes)

    @property
    def model_runtimes(self) -> Mapping[str, ModelRuntime]:
        return dict(self._model_runtimes)

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

    def agent_runtime(self, name: str) -> AgentRuntime:
        runtime = self._agent_runtimes.get(name)
        if runtime is None:
            known = ", ".join(sorted(self._agent_runtimes)) or "(none configured)"
            raise NotFoundError(
                f"no agent runtime named {name!r}. Configured: {known}",
                fix="define it under matrix.agent_runtimes",
                value=name,
                allowed=sorted(self._agent_runtimes),
            )
        return runtime

    def model_runtime(self, name: str) -> ModelRuntime:
        runtime = self._model_runtimes.get(name)
        if runtime is None:
            known = ", ".join(sorted(self._model_runtimes)) or "(none configured)"
            raise NotFoundError(
                f"no model runtime named {name!r}. Configured: {known}",
                fix="define it under matrix.model_runtimes",
                value=name,
                allowed=sorted(self._model_runtimes),
            )
        return runtime

    def bind(self, definition: AgentDefinition, runtime: str) -> BoundAgent:
        """Bind a definition to a configured agent runtime, with this container's
        observers."""
        built = self.agent_runtime(runtime)
        url = agent_runtime_type_url(self._config.matrix.agent_runtimes[runtime].type)
        return BoundAgent(
            definition,
            built,
            observers=self._observers,
            runtime_url=url,
            drives=_drives(self._registry, AGENT_RUNTIME, url),
            through_model_runtime=_through_model_runtime(self._registry, url),
        )

    def needs(self) -> dict[str, Any]:
        """The value of each of matrix's needs (:data:`~matrix.domain.needs.NEEDS`) but a
        component's ``name``, which :meth:`wire` adds for each component, and each need the
        host handed composition (``context``)."""
        return {
            **self._context,
            "agents": dict(self._agents),
            "model_runtimes": self._model_runtimes,
            "schemas": self._registry.payload_schemas(),
            "cwd": self._context.get("cwd"),
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

        The wiring carries each component's fingerprint, which a later run compares before
        it reuses the component's rows: a digest of its type, its config, and each need it
        is handed (an ``agent-step``'s own agent, a ``model-step``'s
        own model runtime, every agent or model runtime for another component that needs
        them, ``cwd``). An agent counts by its definition, its runtime's config and needs (the
        model runtimes, for one that makes its calls on them), and what that runtime resolved
        when it was built; a model runtime by its config and needs and what it resolved.
        """
        configured = components is None
        prefix = "matrix." if configured else ""
        entries = [
            c if isinstance(c, ComponentConfig) else ComponentConfig.model_validate(dict(c))
            for c in (self._config.matrix.components if components is None else components)
        ]
        kinds = self._config.matrix.inputs if inputs is None else tuple(inputs)
        built: dict[str, Any] = {}
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
                prints[entry.name] = self._fingerprint(entry)
            except MatrixError as e:
                problems += _problems_of(e, where, entry)
        try:
            wiring = wire(
                built,
                inputs=kinds,
                schemas=self._registry.payload_schemas(),
                fingerprints=prints,
            )
            if not problems:
                return wiring
        except CompilationError as e:
            found = _reply_clashes(e.problems, entries)
            problems += [p.at(prefix.rstrip(".")) if prefix else p for p in found]
        raise invalid(
            "the components cannot be wired",
            sourced(problems, self._config.origins) if configured else problems,
            fix="fix each listed component or kind; every problem is reported at once",
            error=CompilationError,
        )

    def _fingerprint(self, entry: ComponentConfig) -> str:
        """A digest of what the component is built from: its type and config, and each need
        it is handed that decides what it does. An agent step is handed its own agent; any
        other component that needs ``agents`` is handed every agent."""
        needs = set(self._registry.entry("component", entry.type).needs)
        made_of: dict[str, Any] = {"type": entry.type, "config": entry.config}
        if entry.type == AGENT_STEP:
            needs.discard("agents")
            made_of["agent"] = self._agent_made_of(str(entry.config.get("agent")))
        if entry.type == MODEL_STEP:
            needs.discard("model_runtimes")
            named = model_runtime_named(
                entry.config.get("runtime"), self._model_runtimes, key="runtime"
            )
            made_of["model_runtime"] = self._model_runtime_made_of(named)
        made_of["needs"] = self._needs_made_of(needs)
        text = json.dumps(made_of, sort_keys=True, default=str)
        return "sha256:" + hashlib.sha256(text.encode()).hexdigest()

    def _agent_made_of(self, name: str) -> dict[str, Any] | None:
        """What decides how the agent ``name`` answers: its definition, its runtime's config
        and the needs that runtime was handed, and what the runtime resolved when it was
        built (``resolved``)."""
        agent = self._agents.get(name)
        spec = self._config.matrix.agents.get(name)
        if agent is None or spec is None:
            return None
        runtime = self._config.matrix.agent_runtimes.get(spec.runtime)
        found: dict[str, Any] = {
            "definition": agent.definition.model_dump(mode="json"),
            "runtime": None if runtime is None else runtime.model_dump(mode="json"),
        }
        url = None if runtime is None else agent_runtime_type_url(runtime.type)
        if url is not None and (AGENT_RUNTIME, url) in self._registry:
            found["needs"] = self._needs_made_of(self._registry.entry(AGENT_RUNTIME, url).needs)
        if isinstance(agent.runtime, SupportsResolved):
            found["resolved"] = agent.runtime.resolved
        return found

    def _model_runtime_made_of(self, name: str) -> dict[str, Any] | None:
        """What decides how the model runtime ``name`` answers: its config, the needs it was
        handed, and what it resolved when it was built (``resolved``: a model registry it
        read)."""
        built = self._model_runtimes.get(name)
        runtime = self._config.matrix.model_runtimes.get(name)
        if built is None or runtime is None:
            return None
        found: dict[str, Any] = {"runtime": runtime.model_dump(mode="json")}
        url = model_runtime_type_url(runtime.type)
        if (MODEL_RUNTIME, url) in self._registry:
            found["needs"] = self._needs_made_of(self._registry.entry(MODEL_RUNTIME, url).needs)
        if isinstance(built, SupportsResolved):
            found["resolved"] = built.resolved
        return found

    def _needs_made_of(self, needs: Iterable[str]) -> dict[str, Any]:
        """The value of each need matrix hands over, as it decides the work: every agent,
        every model runtime, the working directory (the process's when none was given). The
        payload ``schemas`` are code, not config, and a need a host adds through ``context``
        is its own: neither is digested."""
        found: dict[str, Any] = {}
        for need in sorted(needs):
            if need == "agents":
                found[need] = {name: self._agent_made_of(name) for name in sorted(self._agents)}
            elif need == "model_runtimes":
                found[need] = {
                    name: self._model_runtime_made_of(name) for name in sorted(self._model_runtimes)
                }
            elif need == "cwd":
                found[need] = str(self._context.get("cwd") or Path.cwd())
        return found

    def executor(self, observers: Sequence[Observer] = ()) -> Executor:
        """An executor over the composed observers (and ``observers``) that turns SIGTERM and
        SIGINT into the run's cancellation, and records in each session row the
        OpenTelemetry trace and span the session ended in."""
        return Executor(
            [*self._observers, *observers],
            interrupts=cancel_on_signals,
            trace=telemetry.current_trace,
        )

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


#: The kind each step provides its reply as when its config names none.
_REPLIES = {AGENT_STEP: AGENT_RESPONSE, MODEL_STEP: MODEL_CALL}


def _reply_clashes(
    problems: Sequence[Problem], entries: Sequence[ComponentConfig]
) -> list[Problem]:
    """``problems``, with a kind that two agent or model steps both provide as their reply
    pointed at the key that fixes it: each step's own ``response``."""
    responses = {
        entry.name: str(entry.config.get("response", _REPLIES[entry.type]))
        for entry in entries
        if entry.type in _REPLIES
    }
    found: list[Problem] = []
    for item in problems:
        name = item.path.removeprefix("components.").removesuffix(".provides")
        clashing = sorted(step for step, kind in responses.items() if kind == item.got)
        if item.kind == "name_taken" and name in clashing and len(clashing) > 1:
            item = item.model_copy(
                update={
                    "path": f"components.{name}.config.response",
                    "message": f"steps {', '.join(clashing)} all provide their reply as "
                    f"{item.got!r}; a kind has one provider",
                    "fix": "give each step its own `response:` kind in its config (as "
                    f"`response: <namespace>.v1.{name}-reply`); the default, {item.got}, "
                    "serves one step",
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
#: An entry point's name is a point the extension registers at or declares, and its value the
#: extension's ``register(registry)``: an extension at three points has three entry points,
#: one ``register``.
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


def installed_extensions(group: str = ENTRY_POINT_GROUP) -> list[Extension]:
    """Every extension installed at the ``group`` entry point, with the points it declares,
    read from the installed metadata: nothing is imported. The entry points of one
    distribution that name one ``register`` are one extension."""
    declared: dict[tuple[str, str], list[EntryPoint]] = {}
    for point in entry_points(group=group):
        origin = point.dist.name if point.dist is not None else point.value
        declared.setdefault((origin, point.value), []).append(point)
    return [
        Extension(
            name=value,
            origin=origin,
            register=_registers(points[0]),
            points=frozenset(point.name for point in points),
        )
        for (origin, value), points in declared.items()
    ]


def load_extensions(registry: Registry, group: str = ENTRY_POINT_GROUP) -> Registry:
    """Load into ``registry`` every extension installed at the ``group`` entry point that
    declares a point of its :attr:`~matrix.domain.registry.Registry.scope` (every one, when it
    has none). An extension that declares none of them is not imported."""
    found = installed_extensions(group)
    scope = registry.scope
    if scope is not None:
        found = [extension for extension in found if (extension.points or frozenset()) & scope]
    return registry.load(found)


def default_registry(*, discover: bool = True, points: Iterable[str] | None = None) -> Registry:
    """A registry with the ``matrix.extensions`` entry points loaded that declare one of
    ``points``, the points the session it serves reads (None: every point, and every
    extension): matrix's built-ins among them, when a point is one of matrix's.

    ``discover=False`` loads only matrix's built-ins, for tests and hermetic callers.
    """
    registry = Registry(scope=None if points is None else frozenset(points))
    if discover:
        return load_extensions(registry)
    builtins = Extension(
        name="matrix.composition.builtins:register",
        origin="matrix",
        register=register_builtins,
        points=frozenset(MATRIX_POINTS),
    )
    return registry.load([builtins])


class _Problems:
    """The problems composition has met so far, and whether any check was skipped."""

    def __init__(self) -> None:
        self.found: list[Problem] = []
        self.complete = True

    def add(
        self, error: MatrixError, where: str, *, got: Any = None, kind: str | None = None
    ) -> None:
        if error.problems:  # one problem read by several parts is said once
            self.found += [p for p in error.problems if p not in self.found]
            return
        if "missing" in error.details:  # selected, but a dependency of it is not installed
            kind, got = "dependency_missing", error.details.get("missing")
        kind = kind or ("name_unknown" if error.kind == "not_found" else "invalid_value")
        self.found.append(problem(where, kind, error.message, fix=error.fix, got=got))


def build_runtimes(
    runtimes: Mapping[str, RuntimeConfig],
    point: str,
    registry: Registry,
    needs: Mapping[str, Any],
    *,
    where: str,
    problems: _Problems | None = None,
) -> dict[str, Any]:
    """Each runtime in ``runtimes`` (an ``agent_runtimes`` or ``model_runtimes`` section),
    built at ``point`` (``agent-runtime`` or ``model-runtime``). ``where`` is the section's
    key path. Raises ConfigError listing every problem unless ``problems`` collects them."""
    found = problems or _Problems()
    prefix = AGENT_RUNTIME_PREFIX if point == AGENT_RUNTIME else MODEL_RUNTIME_PREFIX
    type_url_of = agent_runtime_type_url if point == AGENT_RUNTIME else model_runtime_type_url
    built: dict[str, Any] = {}
    for name, spec in runtimes.items():
        at = f"{where}.{name}"
        url = type_url_of(spec.type)
        if (point, url) not in registry:
            legal = sorted(e.type_url.removeprefix(prefix) for e in registry.entries(point))
            found.found.append(
                problem(
                    f"{at}.type",
                    "name_unknown",
                    f"{spec.type!r} is not a registered {point}",
                    fix=_install_fix(point, registry),
                    allowed=legal,
                    got=spec.type,
                )
            )
            continue
        try:
            built[name] = registry.create(point, url, spec.options(), needs=needs, where=at)
        except MatrixError as e:
            found.add(e, at)
    if problems is None and found.found:
        raise invalid(f"the {point}s cannot be built", found.found)
    return built


#: What to install for a model runtime that calls a model, which matrix does not ship: matrix
#: privileges none, and this is a pointer, not a default (owner ruling HO1, 2026-11-06).
MODEL_RUNTIME_PACKAGE = "matrix-hardline"
#: Where it is installed from: the name on a package index is not this project's.
MODEL_RUNTIME_SOURCE = (
    "https://github.com/mox-labs/gnx (components/capabilities/matrix-hardline, whose README "
    "has the install line)"
)


def _install_fix(point: str, registry: Registry) -> str:
    """The fix for a type nothing registered at ``point``: install the package that registers
    it, and, where only stand-ins are installed at the model-runtime point, which one does."""
    fix = (
        f"use one of the registered {point}s, or install the package providing it; a "
        "third party's is named by its full type URL"
    )
    if point == MODEL_RUNTIME and all(e.stand_in for e in registry.entries(point)):
        fix += (
            f". No model runtime that calls a model is installed: matrix ships only `mock`. "
            f"Install one, as `{MODEL_RUNTIME_PACKAGE}`, which registers "
            "`matrix-hardline.v1.model-runtime`, into the environment matrix runs in. It is on "
            f"no package index: install it from {MODEL_RUNTIME_SOURCE}"
        )
    return fix


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
    registry: Registry | None = None,
) -> dict[str, BoundAgent]:
    """Each configured agent, bound to its agent runtime, with the runtime's type URL and, from
    ``registry``, the distributions it drives, for the sessions it records. An agent whose
    runtime is configured but could not be built is skipped, and the check it would have had
    is reported as not made; so is one whose definition is missing when ``unread`` says the
    definitions could not all be read."""
    found = problems or _Problems()
    agents: dict[str, BoundAgent] = {}
    for name, spec in matrix.agents.items():
        where = f"{source}.agents.{name}"
        runtime = runtimes.get(spec.runtime)
        if runtime is None and spec.runtime not in matrix.agent_runtimes:
            model = spec.runtime in matrix.model_runtimes
            found.found.append(
                problem(
                    f"{where}.runtime",
                    "name_unknown",
                    f"{spec.runtime!r} is a model runtime: one call, with no loop or tools"
                    if model
                    else f"{spec.runtime!r} is not a configured agent runtime",
                    fix="name an agent runtime defined under matrix.agent_runtimes"
                    + (", or make the call with a matrix.v1.model-step" if model else ""),
                    allowed=sorted(matrix.agent_runtimes),
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
            if spec.runtime in matrix.agent_runtimes:
                found.complete = False  # its runtime failed to build: its check was not made
            continue
        fields = {**(base.model_dump() if base is not None else {}), **overrides, "name": name}
        try:
            definition = AgentDefinition.model_validate(fields)
            url = agent_runtime_type_url(matrix.agent_runtimes[spec.runtime].type)
            agents[name] = BoundAgent(
                definition,
                runtime,
                observers=observers,
                runtime_url=url,
                drives=_drives(registry, AGENT_RUNTIME, url),
                through_model_runtime=_through_model_runtime(registry, url),
            )
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
    configured directories hold. ``context`` is what the host hands the extensions that
    declare they need it: ``cwd``, and needs of the host's own. ``source`` is the key every
    problem's path starts with. ``loader`` reads the definition directories (default: the
    file source).

    Raises ConfigError listing every problem; ``complete`` is False when a problem kept a
    check from being made (an agent whose agent runtime could not be built). A ``context``
    key that names another of matrix's needs is a ConfigError: composition supplies those.
    """
    full = config if isinstance(config, Config) else Config[Any](matrix=config, client=None)
    matrix = full.matrix
    reg = registry if registry is not None else default_registry(points=MATRIX_POINTS)
    root = Path(base_dir) if base_dir is not None else Path.cwd()
    problems = _Problems()
    given = dict(context or {})
    taken = sorted((set(given) & set(NEEDS)) - {"cwd"})
    if taken:
        raise ConfigError(
            f"context names {', '.join(taken)}, which composition supplies",
            fix="hand over only `cwd` and needs of your own; matrix builds the rest from config",
            value=taken,
            allowed=["cwd"],
        )
    shared = {**given, "cwd": given.get("cwd")}
    observers = build_observers(matrix, reg, source=source, problems=problems)
    # Model runtimes first: an agent runtime that makes its calls on one is handed them.
    model_runtimes = build_runtimes(
        matrix.model_runtimes,
        MODEL_RUNTIME,
        reg,
        shared,
        where=f"{source}.model_runtimes",
        problems=problems,
    )
    composed = _ComposedModelRuntimes(model_runtimes, matrix.model_runtimes, reg)
    agent_runtimes = build_runtimes(
        matrix.agent_runtimes,
        AGENT_RUNTIME,
        reg,
        {**shared, "model_runtimes": composed},
        where=f"{source}.agent_runtimes",
        problems=problems,
    )
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
        matrix,
        agent_runtimes,
        loaded,
        observers,
        source=source,
        problems=problems,
        unread=unread,
        registry=reg,
    )
    if problems.found:
        raise invalid(
            "the config does not compose",
            sourced(problems.found, full.origins),
            complete=problems.complete,
        )
    warnings = [
        notice.at(f"{source}.{section}.{name}")
        for section, built in (
            ("agent_runtimes", agent_runtimes),
            ("model_runtimes", model_runtimes),
        )
        for name, runtime in built.items()
        if isinstance(runtime, SupportsWarnings)
        for notice in runtime.warnings
    ]
    warnings += telemetry.warnings()
    return Container(
        full,
        reg,
        agent_runtimes=agent_runtimes,
        model_runtimes=composed,
        definitions=loaded,
        agents=agents,
        observers=observers,
        context=shared,
        store=store,
        warnings=warnings,
    )


class _ComposedModelRuntimes(ModelRuntimes):
    """The model runtimes ``compose`` built, by name, each with the type URL its config's
    ``type`` resolves to and what that registry entry drives."""

    def __init__(
        self,
        built: Mapping[str, ModelRuntime],
        specs: Mapping[str, RuntimeConfig],
        registry: Registry,
    ) -> None:
        self._built = dict(built)
        self._urls = {name: model_runtime_type_url(specs[name].type) for name in self._built}
        self._drives = {
            name: _drives(registry, MODEL_RUNTIME, url) for name, url in self._urls.items()
        }

    def __getitem__(self, name: str) -> ModelRuntime:
        return self._built[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._built)

    def __len__(self) -> int:
        return len(self._built)

    def type_url(self, name: str) -> str:
        return self._urls[name]

    def drives(self, name: str) -> tuple[str, ...]:
        return self._drives[name]


def _drives(registry: Registry | None, point: str, url: str) -> tuple[str, ...]:
    """The distributions the runtime registered at ``url`` drives; none when unknown."""
    if registry is None or (point, url) not in registry:
        return ()
    return registry.entry(point, url).drives


def _through_model_runtime(registry: Registry | None, url: str) -> bool:
    """Whether the agent runtime registered at ``url`` makes its sessions' model calls on
    matrix's model runtimes: it declared it needs them, so composition handed them over."""
    if registry is None or (AGENT_RUNTIME, url) not in registry:
        return False
    return "model_runtimes" in registry.entry(AGENT_RUNTIME, url).needs

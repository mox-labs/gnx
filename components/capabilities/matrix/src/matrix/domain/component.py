"""Components, and the wiring inferred from what they declare.

A **kind** is the type URL of some data (``acme.v1.review``). A **component** declares the
kinds it ``requires`` and the kinds it ``provides``, and does its work in ``run``. That is the
whole contract; nothing else is declared and nobody draws an edge:

* a component depends on the provider of each kind it requires;
* a kind the caller supplies when the run starts is a **run input** and has no provider;
* each kind has exactly one provider. Two streams of the same shape are two kinds.

:func:`wire` infers that wiring from a set of named components and checks it, reporting every
problem at once: a required kind nobody provides, a kind with two providers, a cycle. What it
returns, a :class:`Wiring`, can be run any number of times.

A component's **name** is the name of the configured instance: one component class may be used
twice in a run under two names. It receives :class:`Inputs`, the rows of the kinds it requires
and nothing else, and a :class:`RunContext`. It never sees the record of the run.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Collection,
    Iterable,
    Iterator,
    Mapping,
)
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from matrix.domain.errors import CompilationError, invalid
from matrix.domain.ids import versioned_problem
from matrix.domain.problems import problem
from matrix.domain.session import SESSION

if TYPE_CHECKING:
    from pydantic import BaseModel

    from matrix.domain.problems import Problem

#: A deferred release: called with no arguments; may return an awaitable.
Release = Callable[[], Awaitable[None] | None]

#: The kind of the provenance table every run keeps. No component provides or requires it.
LEDGER = "matrix.v1.ledger"

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ContextHooks(Protocol):
    """What a run does on a component's behalf. The executor supplies it."""

    def defer(self, release: Release) -> None: ...

    def provide(self, kind: str, row: Any) -> None: ...

    def emit(self, name: str, fields: dict[str, Any]) -> None: ...

    def measure(
        self, name: str, value: float, *, unit: str, kind: str, attrs: dict[str, Any]
    ) -> None: ...

    def span(self, name: str, fields: dict[str, Any]) -> AbstractAsyncContextManager[None]: ...


class _Detached:
    """Hooks for a context built outside a run (a component under a unit test)."""

    def defer(self, release: Release) -> None:
        return None

    def provide(self, kind: str, row: Any) -> None:
        return None

    def emit(self, name: str, fields: dict[str, Any]) -> None:
        return None

    def measure(
        self, name: str, value: float, *, unit: str, kind: str, attrs: dict[str, Any]
    ) -> None:
        return None

    @asynccontextmanager
    async def span(self, name: str, fields: dict[str, Any]) -> AsyncIterator[None]:
        yield


class RunContext:
    """What a component may know about the run it is part of, and ask of it. Nothing about
    its neighbours.

    ``provide(kind, row)`` hands over one row of a kind the component provides, *while it is
    still running*. When the call returns the row has been checked against the kind's schema,
    is part of the run's record, and is durable in the run's store. A component that works
    for hours hands its rows over as it makes them, and killing it loses none of them.

    ``stopping`` is set when the run asks the component to finish now: its deadline passed,
    another component failed, or the run was cancelled. A component that returns soon after
    keeps what it returns; one that does not is cancelled after the grace period.

    ``defer(release)`` hands the run something to undo. Every deferred release is called once
    when the run ends, in reverse order, whether the run completed, failed or was cancelled.

    ``emit``, ``measure`` and ``span`` are the component's telemetry: events on the run's
    channel, under a name the component namespaces with its own root (``acme.probe.sample``;
    ``matrix.*`` is matrix's). They are never part of the result, and may be dropped under
    load.

    Outside a run (a context built by hand) nothing is recorded, released or emitted. After
    the component has ended, each of these is ignored and reported once as
    ``matrix.component.late``.
    """

    def __init__(
        self,
        run_id: str,
        component: str,
        deadline: float | None = None,
        *,
        hooks: ContextHooks | None = None,
    ) -> None:
        self.run_id = run_id
        self.component = component
        #: ``time.monotonic()`` deadline for this component, or None.
        self.deadline = deadline
        self.stopping = asyncio.Event()
        self._hooks: ContextHooks = hooks if hooks is not None else _Detached()

    def provide(self, kind: str, row: Any, /) -> None:
        """Hand over one row of ``kind`` now. Recorded and durable when this returns."""
        self._hooks.provide(kind, row)

    def defer(self, release: Release) -> None:
        """Register ``release`` (sync or async, no arguments) to run when the run ends."""
        self._hooks.defer(release)

    def emit(self, name: str, /, **fields: Any) -> None:
        """Raises :class:`ContractError` for a ``name`` that is not namespaced, or is under
        ``matrix.*``."""
        self._hooks.emit(name, fields)

    def measure(
        self,
        name: str,
        value: float,
        /,
        *,
        unit: str = "",
        kind: str = "gauge",
        **attrs: Any,
    ) -> None:
        """Record one measurement: a ``gauge`` reading, a ``counter`` increment, or one
        ``histogram`` observation. It travels as a ``matrix.measure`` event, so a metrics
        exporter and an event log see the same thing."""
        self._hooks.measure(name, value, unit=unit, kind=kind, attrs=attrs)

    def span(self, name: str, /, **fields: Any) -> AbstractAsyncContextManager[None]:
        """``async with ctx.span("acme.probe"):`` emits ``acme.probe.start`` and
        ``acme.probe.end`` (with ``status`` and ``duration_ms``) around the block. Spans and
        agent calls made inside it are its children."""
        return self._hooks.span(name, fields)

    def __repr__(self) -> str:
        return f"RunContext(run_id={self.run_id!r}, component={self.component!r})"


class Inputs(Mapping[str, tuple[Any, ...]]):
    """The rows of each kind a component requires, by kind. Read-only.

    Every required kind is present and maps to a tuple of rows in the order they were
    recorded: empty when the provider handed over none.
    """

    def __init__(self, rows: Mapping[str, Iterable[Any]]) -> None:
        self._rows = {kind: tuple(found) for kind, found in rows.items()}

    def __getitem__(self, kind: str) -> tuple[Any, ...]:
        try:
            return self._rows[kind]
        except KeyError:
            required = ", ".join(sorted(self._rows)) or "(none)"
            raise KeyError(f"{kind!r} is not a kind this component requires: {required}") from None

    def one(self, kind: str) -> Any:
        """The single row of ``kind``. Raises ``LookupError`` unless there is exactly one."""
        rows = self[kind]
        if len(rows) != 1:
            raise LookupError(f"expected exactly one row of {kind!r}, found {len(rows)}")
        return rows[0]

    def __iter__(self) -> Iterator[str]:
        return iter(self._rows)

    def __len__(self) -> int:
        return len(self._rows)

    def __repr__(self) -> str:
        return f"Inputs({sorted(self._rows)})"


@runtime_checkable
class Component(Protocol):
    """An executable unit. Structural: implement it without importing matrix.

    ``requires`` and ``provides`` are collections of kinds. ``run`` receives the rows of the
    kinds it requires and its context. Rows of the kinds it provides leave it two ways, and it
    may use both: handed over as it goes with ``ctx.provide(kind, row)``, and returned, as a
    mapping of kind to one row or to a list or tuple of rows. It may return nothing.

    The component's name is the name it is given in the run (see :func:`wire`), so the same
    class can be used twice. Configuration arrives when the component is built, never through
    ``run``.
    """

    @property
    def requires(self) -> Collection[str]: ...

    @property
    def provides(self) -> Collection[str]: ...

    async def run(self, inputs: Inputs, ctx: RunContext) -> Mapping[str, Any] | None: ...


@dataclass(frozen=True)
class Wiring:
    """Checked components and the order of work inferred from their kinds. Reusable: run it
    any number of times with different inputs."""

    #: name -> component, in the order given
    components: Mapping[str, Component]
    #: the kinds the caller supplies when a run starts
    inputs: frozenset[str]
    #: kind -> the name of the one component that provides it
    providers: Mapping[str, str]
    #: name -> the components it waits for: the providers of what it requires
    depends: Mapping[str, tuple[str, ...]]
    #: kind -> a pydantic model that rows of that kind must validate against
    schemas: Mapping[str, type[BaseModel]] = field(default_factory=dict)
    #: name -> the effects its registration declares; ``None`` means unknown
    effects: Mapping[str, frozenset[str] | None] = field(default_factory=dict)
    #: name -> a digest of what the component was built from (its type and config), when
    #: the caller knows it: a later run reuses a component's rows only when its digest is
    #: the same
    fingerprints: Mapping[str, str] = field(default_factory=dict)

    @property
    def kinds(self) -> tuple[str, ...]:
        """Every kind a run of this wiring has a table for: inputs first, then provided."""
        return (*sorted(self.inputs), *self.providers)

    def order(self) -> tuple[str, ...]:
        """The components in one valid order of work. A view for a reader: the executor
        starts each component as soon as what it depends on has finished."""
        placed: list[str] = []
        while len(placed) < len(self.components):
            placed.extend(
                name
                for name in self.components
                if name not in placed and all(d in placed for d in self.depends[name])
            )
        return tuple(placed)

    def declared_effects(self) -> frozenset[str] | None:
        """Every effect the components declare; ``None`` if any component's are unknown.

        Unknown is never read as "none": a component built outside the registry, or
        registered without effects, makes the whole run's effects unknown.
        """
        found: set[str] = set()
        for name in self.components:
            effects = self.effects.get(name)
            if effects is None:
                return None
            found |= effects
        return frozenset(found)


def wire(
    components: Mapping[str, Any] | Iterable[Any],
    *,
    inputs: Iterable[str] = (),
    schemas: Mapping[str, type[BaseModel]] | None = None,
    effects: Mapping[str, frozenset[str] | None] | None = None,
    fingerprints: Mapping[str, str] | None = None,
) -> Wiring:
    """Infer the wiring of ``components`` and check it. Raises CompilationError listing every
    problem (``error.problems``, each with the path of the component or kind it is about).

    ``components`` maps each instance's name to the component, or is a sequence of
    components that each carry a ``name``. ``inputs`` are the kinds the caller will supply to
    every run. ``schemas`` maps kinds to the models their rows are validated against.

    The rules: every kind is a type URL; a required kind is provided by a component or is a
    run input; no kind has two providers, and a run input has none; no component requires
    what it provides; the dependencies have no cycle.
    """
    problems: list[Problem] = []
    named = _named(components, problems)
    run_inputs = frozenset(inputs)
    for kind in sorted(run_inputs):
        _check_kind(kind, "inputs", problems)
    requires, provided_by = _declared(named, problems)
    problems += _source_problems(requires, provided_by, run_inputs)

    providers = {kind: names[0] for kind, names in provided_by.items()}
    depends = {
        name: tuple(
            other
            for other in named
            if other != name and any(providers.get(kind) == other for kind in kinds)
        )
        for name, kinds in requires.items()
    }
    problems += _cycle_problems(depends, providers, requires)

    if problems:
        raise invalid(
            "the components cannot be wired",
            problems,
            fix="fix each listed component or kind; every problem is reported at once",
            error=CompilationError,
        )
    return Wiring(
        components=named,
        inputs=run_inputs,
        providers=providers,
        depends=depends,
        schemas=dict(schemas or {}),
        effects=dict(effects or {}),
        fingerprints=dict(fingerprints or {}),
    )


def _named(
    components: Mapping[str, Any] | Iterable[Any], problems: list[Problem]
) -> dict[str, Any]:
    """Each component by its name in the run."""
    if isinstance(components, Mapping):
        return dict(components)
    named: dict[str, Any] = {}
    for index, component in enumerate(components):
        name = getattr(component, "name", None)
        if not isinstance(name, str):
            problems.append(
                problem(
                    f"components.{index}",
                    "missing_key",
                    f"{type(component).__name__} has no name",
                    fix="give it a `name`, or pass a mapping of name to component",
                )
            )
        elif name in named:
            problems.append(
                problem(
                    f"components.{name}",
                    "name_taken",
                    "name used twice",
                    fix="give each component its own name",
                    got=name,
                )
            )
        else:
            named[name] = component
    return named


def _declared(
    named: Mapping[str, Any], problems: list[Problem]
) -> tuple[dict[str, tuple[str, ...]], dict[str, list[str]]]:
    """What each component requires, and which components provide each kind, as declared."""
    requires: dict[str, tuple[str, ...]] = {}
    provided_by: dict[str, list[str]] = {}
    for name, component in named.items():
        where = f"components.{name}"
        if not isinstance(name, str) or not _NAME.match(name):
            problems.append(
                problem(
                    where,
                    "invalid_value",
                    f"a name must match {_NAME.pattern}",
                    fix="name it with letters, digits, '.', '_' and '-', starting with a "
                    "letter or digit",
                    got=name,
                )
            )
        declared: dict[str, Any] = {
            side: getattr(component, side, None) for side in ("requires", "provides")
        }
        for side, kinds in declared.items():
            if isinstance(kinds, str) or not isinstance(kinds, Collection):
                problems.append(
                    problem(
                        f"{where}.{side}",
                        "wrong_shape",
                        f"a component declares `{side}` as a collection of kinds",
                        fix=f"declare {side} = {{'<namespace>.v1.<name>', ...}}",
                    )
                )
                declared[side] = ()
        if not callable(getattr(component, "run", None)):
            problems.append(
                problem(
                    where,
                    "wrong_shape",
                    "a component has an async `run(inputs, ctx)`",
                    fix="define `async def run(self, inputs, ctx)`",
                )
            )
        requires[name] = tuple(dict.fromkeys(declared["requires"]))
        for kind in requires[name]:
            _check_kind(kind, f"{where}.requires", problems)
        for kind in dict.fromkeys(declared["provides"]):
            if _check_kind(kind, f"{where}.provides", problems):
                provided_by.setdefault(kind, []).append(name)
            if kind in requires[name]:
                problems.append(
                    problem(
                        where,
                        "cycle",
                        f"requires and provides {kind!r}; it cannot wait on itself",
                        fix="provide a new kind, or do the repetition inside the component",
                        got=kind,
                    )
                )
    return requires, provided_by


def _source_problems(
    requires: Mapping[str, tuple[str, ...]],
    provided_by: Mapping[str, list[str]],
    run_inputs: frozenset[str],
) -> list[Problem]:
    """Each kind with more than one source, and each required kind with none."""
    problems: list[Problem] = []
    for kind, names in provided_by.items():
        if len(names) > 1:
            problems.append(
                problem(
                    f"components.{names[1]}.provides",
                    "name_taken",
                    f"kind {kind!r} is provided by {', '.join(names)}; a kind has one "
                    "provider (two streams of the same shape are two kinds)",
                    fix="give each stream its own kind",
                    got=kind,
                )
            )
        if kind in run_inputs:
            problems.append(
                problem(
                    f"components.{names[0]}.provides",
                    "name_taken",
                    f"kind {kind!r} is a run input and is also provided by {', '.join(names)}; "
                    "a kind has one source",
                    fix=f"remove {kind!r} from the inputs, or from what the component provides",
                    got=kind,
                )
            )
    for name, kinds in requires.items():
        for kind in kinds:
            if (
                kind not in provided_by
                and kind not in run_inputs
                and versioned_problem(kind) is None
            ):
                problems.append(
                    problem(
                        f"components.{name}.requires",
                        "unprovided",
                        f"nothing provides {kind!r} and it is not a run input",
                        fix=f"add a component that provides {kind!r}, or list it as an input",
                        got=kind,
                    )
                )
    return problems


def _cycle_problems(
    depends: Mapping[str, tuple[str, ...]],
    providers: Mapping[str, str],
    requires: Mapping[str, tuple[str, ...]],
) -> list[Problem]:
    cycle = _cycle(depends)
    if not cycle:
        return []
    through = sorted(
        kind
        for kind, provider in providers.items()
        if provider in cycle and any(kind in requires[name] for name in cycle)
    )
    return [
        problem(
            f"components.{cycle[0]}",
            "cycle",
            f"cycle: {' -> '.join([*cycle, cycle[0]])} (through {', '.join(through)})",
            fix="to repeat work, run again or repeat inside one component",
        )
    ]


def _check_kind(kind: Any, where: str, problems: list[Problem]) -> bool:
    reason = versioned_problem(kind)
    if reason is not None:
        problems.append(
            problem(
                where,
                "invalid_value",
                f"{kind!r} is not a kind: {reason}",
                fix="name a kind '<namespace>.v<N>.<name>', e.g. 'acme.v1.review'",
                got=kind,
            )
        )
        return False
    if kind in (LEDGER, SESSION):
        what = "provenance table" if kind == LEDGER else "table of agent sessions and model calls"
        problems.append(
            problem(
                where,
                "invalid_value",
                f"{kind!r} is the run's own {what}",
                fix="name a kind of your own",
                got=kind,
            )
        )
        return False
    return True


def _cycle(depends: Mapping[str, tuple[str, ...]]) -> list[str]:
    """One cycle among the dependencies, or an empty list when there is none."""
    done: set[str] = set()
    progressed = True
    while progressed:
        progressed = False
        for name, needs in depends.items():
            if name not in done and all(n in done for n in needs):
                done.add(name)
                progressed = True
    stuck = [name for name in depends if name not in done]
    if not stuck:
        return []
    path: list[str] = []
    seen: dict[str, int] = {}
    node = stuck[0]
    while node not in seen:
        seen[node] = len(path)
        path.append(node)
        node = next(d for d in depends[node] if d in stuck)
    return path[seen[node] :]

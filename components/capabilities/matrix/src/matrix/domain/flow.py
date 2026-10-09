"""Flows: components wired through typed topics, checked before anything runs.

The wiring model is slick's (SD-05, SD-08, SD-09), carried here until matrix depends on slick:

* a **component** declares named ports, ``requires`` and ``provides``, each typed by a type URL;
* a **member** is one configured use of a component in a flow, under an alias, with
  **bindings** mapping its port names to **topic** names. Topics are the rendezvous; there are
  no edges to declare. ``after`` adds data-free ordering;
* a **flow** is a set of members plus named **inputs**: topics a caller supplies.

:func:`compile_flow` checks the rules below, reports every violation at once, and returns a
:class:`CompiledFlow` that can run many times with different inputs. The checks follow slick's
wiring kernel (rule names in brackets):

1. every binding names a declared port [FW-4];
2. every required port is bound, unless it is optional [FW-5];
3. every topic carries exactly one type [FW-6];
4. every consumed topic has a producer or is a flow input [FW-7];
5. a topic with several producers (fan-in) feeds only ports declared ``many``. Merge order is
   the producers' declaration order (SD-09 leaves merge order to the executor);
6. no cycle through topics or ``after`` [FW-10]. That rule is a placeholder (SD-09): a flow
   that must repeat does so by running an acyclic flow again under a bound, never by wiring a
   loop.

A component receives :class:`Inputs`, only the values it bound and a run context, never the
run's state, and returns its outputs keyed by provide-port name.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from matrix.domain.errors import CompilationError
from matrix.domain.ids import problem as type_url_problem

if TYPE_CHECKING:
    from pydantic import BaseModel

_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class Port:
    """A typed slot on a component. ``many``: receives every value on its topic, as a tuple.

    ``optional`` (requires only): may be left unbound; the component then sees no value.
    (provides only): may be absent from the component's result.
    """

    type_url: str
    many: bool = False
    optional: bool = False


PortSpec = str | Port


def ports(spec: Mapping[str, PortSpec] | None) -> dict[str, Port]:
    """Normalise a ``{name: type_url | Port}`` map to ``{name: Port}``."""
    return {
        name: p if isinstance(p, Port) else Port(type_url=p) for name, p in (spec or {}).items()
    }


@dataclass(frozen=True)
class RunContext:
    """What a member may know about the run it is part of. Nothing about its neighbours."""

    run_id: str
    member: str
    episode: int = 0
    #: ``time.monotonic()`` deadline for this member, or None.
    deadline: float | None = None


class Inputs(Mapping[str, Any]):
    """The values a member bound, by port name, plus its :class:`RunContext`. Read-only.

    A ``many`` port maps to a tuple (possibly empty); an unbound optional port is absent.
    """

    def __init__(self, values: Mapping[str, Any], context: RunContext) -> None:
        self._values = dict(values)
        self.context = context

    def __getitem__(self, port: str) -> Any:
        try:
            return self._values[port]
        except KeyError:
            bound = ", ".join(sorted(self._values)) or "(none)"
            raise KeyError(f"no input on port {port!r}; bound ports: {bound}") from None

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"Inputs({sorted(self._values)}, member={self.context.member!r})"


@runtime_checkable
class Component(Protocol):
    """An executable unit. Structural: implement it without importing matrix.

    ``requires`` / ``provides`` map port names to type URLs (or :class:`Port`). ``run``
    receives the bound inputs and returns one value per provide port, keyed by port name.
    Configuration arrives when the component is built, never through ``run``.
    """

    requires: Mapping[str, Any]
    provides: Mapping[str, Any]

    async def run(self, inputs: Inputs) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class Member:
    """One use of a component in a flow.

    ``component`` is either a built component, or the extension id of a registered component
    plus its ``config``, which :func:`compile_flow` resolves through ``resolve``.
    """

    alias: str
    component: Component | str
    bindings: Mapping[str, str] = field(default_factory=dict)
    config: Mapping[str, Any] | None = None
    after: tuple[str, ...] = ()


@dataclass(frozen=True)
class Flow:
    """Desired state: members and the inputs a caller supplies. Never mutated by a run."""

    name: str
    members: tuple[Member, ...]
    #: topic -> type URL, supplied by whoever runs the flow
    inputs: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CompiledMember:
    alias: str
    component: Component
    requires: Mapping[str, Port]
    provides: Mapping[str, Port]
    bindings: Mapping[str, str]
    after: tuple[str, ...]


@dataclass(frozen=True)
class CompiledFlow:
    """A checked flow: resolved components, topic types, producers per topic, and an order.

    ``levels`` groups members that may run together: every member of a level depends only on
    earlier levels. Reusable: run it any number of times with different inputs.
    """

    name: str
    members: Mapping[str, CompiledMember]
    inputs: Mapping[str, str]
    topics: Mapping[str, str]
    producers: Mapping[str, tuple[tuple[str, str], ...]]
    levels: tuple[tuple[str, ...], ...]
    #: type URL -> a pydantic model that values of that type must validate against
    schemas: Mapping[str, type[BaseModel]] = field(default_factory=dict)


def compile_flow(
    flow: Flow,
    *,
    resolve: Any = None,
    schemas: Mapping[str, type[BaseModel]] | None = None,
) -> CompiledFlow:
    """Check ``flow`` and return it compiled. Raises CompilationError listing every problem.

    ``resolve(extension_id, config, where)`` builds a member given by extension id;
    ``schemas`` maps payload type URLs to models outputs are validated against.
    """
    problems: list[str] = []
    members: dict[str, CompiledMember] = {}

    for topic, url in flow.inputs.items():
        if (reason := type_url_problem(url)) is not None:
            problems.append(f"inputs.{topic}: {url!r}: {reason}")

    for m in flow.members:
        where = f"members.{m.alias}"
        if not _ALIAS.match(m.alias):
            problems.append(f"{where}: alias must match {_ALIAS.pattern}")
            continue
        if m.alias in members:
            problems.append(f"{where}: alias used twice")
            continue
        component = m.component
        if isinstance(component, str):
            if resolve is None:
                problems.append(f"{where}: {component!r} is an extension id but no registry")
                continue
            try:
                component = resolve(component, dict(m.config or {}), where)
            except CompilationError as e:
                problems.extend(e.details.get("problems") or [str(e)])
                continue
            except Exception as e:  # a factory that raises is reported with the others
                problems.append(f"{where}: {component!r} could not be built: {e}")
                continue
        elif m.config:
            problems.append(f"{where}: config is given but the component is already built")
        requires = ports(getattr(component, "requires", None))
        provides = ports(getattr(component, "provides", None))
        for side, declared in (("requires", requires), ("provides", provides)):
            for name, port in declared.items():
                if (reason := type_url_problem(port.type_url)) is not None:
                    problems.append(f"{where}.{side}.{name}: {port.type_url!r}: {reason}")
        overlap = sorted(set(requires) & set(provides))
        if overlap:
            problems.append(f"{where}: ports {overlap} are both required and provided (SD-05)")
        for port_name in m.bindings:
            if port_name not in requires and port_name not in provides:
                declared_ports = sorted({*requires, *provides}) or ["(none)"]
                problems.append(
                    f"{where}.bindings.{port_name}: no such port; declared: "
                    + ", ".join(declared_ports)
                )
        for port_name, port in requires.items():
            if port_name not in m.bindings and not port.optional:
                problems.append(f"{where}.requires.{port_name}: required port is not bound")
        members[m.alias] = CompiledMember(
            alias=m.alias,
            component=component,
            requires=requires,
            provides=provides,
            bindings=dict(m.bindings),
            after=tuple(m.after),
        )

    topics: dict[str, str] = dict(flow.inputs)
    origin: dict[str, str] = {t: f"inputs.{t}" for t in flow.inputs}
    producers: dict[str, list[tuple[str, str]]] = {}
    consumers: dict[str, list[tuple[str, str]]] = {}
    for alias, cm in members.items():
        for port_name, topic in cm.bindings.items():
            bound = cm.requires.get(port_name) or cm.provides.get(port_name)
            if bound is None:
                continue
            here = f"members.{alias}.{port_name}"
            if topic in topics and topics[topic] != bound.type_url:
                problems.append(
                    f"topic {topic!r} carries {topics[topic]!r} (from {origin[topic]}) but "
                    f"{here} is {bound.type_url!r}"
                )
            else:
                topics.setdefault(topic, bound.type_url)
                origin.setdefault(topic, here)
            if port_name in cm.provides:
                if topic in flow.inputs:
                    problems.append(f"{here}: topic {topic!r} is a flow input; it has no producer")
                producers.setdefault(topic, []).append((alias, port_name))
            else:
                consumers.setdefault(topic, []).append((alias, port_name))

    for topic, uses in consumers.items():
        made = producers.get(topic, [])
        if not made and topic not in flow.inputs:
            readers = ", ".join(f"{a}.{p}" for a, p in uses)
            problems.append(
                f"topic {topic!r} is consumed by {readers} but nothing produces it "
                "and it is not a flow input"
            )
        if len(made) > 1:
            for alias, port_name in uses:
                if not members[alias].requires[port_name].many:
                    sources = ", ".join(f"{a}.{p}" for a, p in made)
                    problems.append(
                        f"members.{alias}.{port_name}: topic {topic!r} has {len(made)} "
                        f"producers ({sources}); declare the port many=True to receive them all"
                    )

    for alias, cm in members.items():
        for predecessor in cm.after:
            if predecessor not in members:
                problems.append(f"members.{alias}.after: no member named {predecessor!r}")

    if problems:
        raise CompilationError(
            f"flow {flow.name!r} is invalid ({len(problems)} problem"
            f"{'s' if len(problems) != 1 else ''}):\n  " + "\n  ".join(problems),
            problems=problems,
            fix="fix each listed binding or port; every problem is reported at once",
        )

    depends: dict[str, set[str]] = {alias: set(cm.after) for alias, cm in members.items()}
    for topic, uses in consumers.items():
        for alias, _ in uses:
            depends[alias].update(a for a, _ in producers.get(topic, []))
    levels = _levels(flow.name, list(members), depends, producers, consumers)

    return CompiledFlow(
        name=flow.name,
        members=members,
        inputs=dict(flow.inputs),
        topics=topics,
        producers={t: tuple(p) for t, p in producers.items()},
        levels=levels,
        schemas=dict(schemas or {}),
    )


def _levels(
    name: str,
    order: list[str],
    depends: dict[str, set[str]],
    producers: Mapping[str, list[tuple[str, str]]],
    consumers: Mapping[str, list[tuple[str, str]]],
) -> tuple[tuple[str, ...], ...]:
    """Group members into dependency levels, keeping declaration order within a level."""
    done: set[str] = set()
    levels: list[tuple[str, ...]] = []
    while len(done) < len(order):
        ready = tuple(a for a in order if a not in done and depends[a] <= done)
        if not ready:
            stuck = [a for a in order if a not in done]
            cycle = _cycle(stuck, depends)
            through = sorted(
                t
                for t, made in producers.items()
                if any(a in cycle for a, _ in made)
                and any(a in cycle for a, _ in consumers.get(t, []))
            )
            raise CompilationError(
                f"flow {name!r} has a cycle: {' -> '.join([*cycle, cycle[0]])}"
                + (f" (through topics {through})" if through else "")
                + ". Acyclic wiring is a placeholder rule (slick SD-09), not doctrine: to "
                "repeat work, run an acyclic flow again under a bound",
                problems=[f"cycle: {cycle}"],
                fix="break the loop; repeat by re-running the flow, not by wiring it back",
            )
        levels.append(ready)
        done.update(ready)
    return tuple(levels)


def _cycle(stuck: list[str], depends: dict[str, set[str]]) -> list[str]:
    """One cycle among ``stuck`` members, for the error message."""
    pending = set(stuck)
    start = stuck[0]
    path: list[str] = []
    seen: dict[str, int] = {}
    node = start
    while node not in seen:
        seen[node] = len(path)
        path.append(node)
        node = next(d for d in sorted(depends[node]) if d in pending)
    return path[seen[node] :]

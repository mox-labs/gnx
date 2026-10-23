"""The registry: extension points, and the implementations registered at each.

An **entry** is one implementation at one point, under a type URL, with what it needs built
in. An entry that stands in for the real thing (a mock runtime: canned replies, no model)
declares ``stand_in``, so a caller can tell a stand-in's answers from a model's without
reading anything else into it.

Everything, built-ins included, arrives as an :class:`Extension`. One that fails to load or
register is **quarantined**: recorded in :attr:`Registry.failures`, and the rest load.

**A point may have a port.** What is registered at ``agent-runtime`` must keep the
:class:`AgentRuntime` port, at ``model-runtime`` the :class:`ModelRuntime` port, at ``store``
the :class:`ConstructStore` port and at ``observer`` the :class:`Observer` port: a class is
held to it when it is registered, and whatever an entry builds when it is built, so an
implementation that lacks a member is a ``ConfigError`` naming it, not an ``AttributeError``
the first time a caller reaches for it. Only the members a port requires are held; its
optional ones (an observer's ``around``, a store's ``events``) are read with ``getattr``.

**Points have owners.** A point is owned by the distribution that declared it
(:meth:`Registry.add_point`), and matrix owns its own (:attr:`Registry.owners`). Another
distribution that declares a point already owned, or gives it a port, is refused and
quarantined whole; the owner may declare it again. Code registering directly (not through
discovery) is trusted, as for type URLs.

**Namespaces have owners.** A distribution owns the type URLs whose root segment is its own
name (``matrix`` owns ``matrix.*``). When two extensions register one type URL, the owner
keeps it whichever loads first, and the other is quarantined whole. Between two non-owners
the first in name order keeps it and the second is refused, naming both.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ValidationError

from matrix.domain.agent import AgentCapabilities
from matrix.domain.errors import ConfigError, NotFoundError, invalid
from matrix.domain.ids import parse_type_url
from matrix.domain.observer import Observer
from matrix.domain.ports._out.agent_runtime import AgentRuntime
from matrix.domain.ports._out.construct_store import ConstructStore
from matrix.domain.ports._out.model_runtime import ModelRuntime
from matrix.domain.problems import from_validation_error, problem, summary

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

Stability = Literal["stable", "beta", "experimental"]

#: The points matrix owns. Packages add theirs with :meth:`Registry.add_point`.
MATRIX_POINTS = (
    "agent-runtime",
    "model-runtime",
    "component",
    "payload-type",
    "observer",
    "store",
)

#: The port each of matrix's points holds what is registered there to.
MATRIX_PORTS: dict[str, type] = {
    "agent-runtime": AgentRuntime,
    "model-runtime": ModelRuntime,
    "store": ConstructStore,
    "observer": Observer,
}

#: Who a point or a type URL registered directly belongs to: whoever composes the registry.
_DIRECT = "(direct)"


def _breaks(found: Any, port: type) -> list[str]:
    """What ``found`` (a class, or what one built) lacks of ``port``: each member that is
    missing or not callable, and each the port awaits that is not a coroutine function."""
    lacking = []
    for name in sorted(getattr(port, "__protocol_attrs__", ())):
        member = getattr(found, name, None)
        if not callable(member):
            lacking.append(f"no {name}")
        elif inspect.iscoroutinefunction(getattr(port, name)) and not (
            inspect.iscoroutinefunction(member)
        ):
            lacking.append(f"{name} is not async")
    return lacking


def _placed(where: str, build: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """``build(*args, **kwargs)``, with the problems of a ConfigError it raises placed under
    ``where``: a builder states them relative to its own config. One with a ``source`` is in
    that source, and kept as it is."""
    try:
        return build(*args, **kwargs)
    except ConfigError as e:
        if not (where and e.problems):
            raise
        placed = [p if p.source else p.at(where) for p in e.problems]
        head = e.message.splitlines()[0]
        raise type(e)(
            f"{where}: {head}\n{summary(placed)}",
            fix=e.fix,
            problems=placed,
            complete=e.complete,
            **e.details,
        ) from e


def _port_fix(port: type) -> str:
    members = ", ".join(sorted(getattr(port, "__protocol_attrs__", ())))
    return f"implement {members} as the {port.__name__} port declares them"


@dataclass(frozen=True)
class Entry:
    point: str
    type_url: str
    build: Callable[..., Any]
    config: type[BaseModel] | None = None
    needs: frozenset[str] = frozenset()
    summary: str = ""
    stability: Stability = "beta"
    origin: str = _DIRECT
    #: Whether it stands in for the real thing: a mock's canned answers, not a model's.
    stand_in: bool = False
    #: The distributions it drives, by their installed names (``claude-agent-sdk``): what a
    #: record of the versions behind a run reads, rather than a table of runtime names.
    drives: tuple[str, ...] = ()

    def capabilities(self, config: Mapping[str, Any]) -> AgentCapabilities | None:
        """What sessions on this runtime do when it is built with ``config``, as its config
        class declares (a method ``capabilities()`` returning :class:`AgentCapabilities`).
        None when it declares nothing, or when ``config`` is not one it takes: building the
        runtime says why."""
        if self.config is None:
            return None
        try:
            given = self.config.model_validate(dict(config))
        except ValidationError:
            return None
        declare = getattr(given, "capabilities", None)
        declared = declare() if callable(declare) else None
        return declared if isinstance(declared, AgentCapabilities) else None

    def describe(self) -> dict[str, Any]:
        """A JSON-ready description: what an agent reads to decide whether to use it."""
        return {
            "point": self.point,
            "type_url": self.type_url,
            "summary": self.summary,
            "stability": self.stability,
            "origin": self.origin,
            "needs": sorted(self.needs),
            "stand_in": self.stand_in,
            "drives": list(self.drives),
            "config": None if self.config is None else self.config.model_json_schema(),
        }


class _UnknownPointError(ConfigError):
    """A registration at a point nobody has declared (yet)."""


def _owns(origin: str, type_url: str) -> bool:
    """Whether ``origin`` owns ``type_url``'s namespace: its root is the distribution's name.

    Code registering directly (not through discovery) is trusted as the owner.
    """
    if origin == _DIRECT:
        return True
    name = origin.lower().replace("_", "-").replace(".", "-")
    return type_url.split(".", 1)[0] == name


@dataclass(frozen=True, kw_only=True)
class Failure:
    """An extension that could not be loaded, or a registration that was refused.
    ``extension`` is its entry point's name; ``origin`` the distribution it came from."""

    extension: str
    origin: str
    error: str


@dataclass(frozen=True, kw_only=True)
class Extension:
    """One extension to load: its entry point's name, the distribution it comes from, and
    its ``register(registry)``. Anything ``register`` raises, importing included,
    quarantines the extension."""

    name: str
    origin: str
    register: Callable[[Registry], None]


@dataclass
class Registry:
    points: set[str] = field(default_factory=lambda: set(MATRIX_POINTS))
    #: The port a point holds its entries to, for the points that have one.
    ports: dict[str, type] = field(default_factory=lambda: dict(MATRIX_PORTS))
    #: The distribution that declared each point; ``(direct)`` for one declared by the code
    #: composing the registry.
    owners: dict[str, str] = field(default_factory=lambda: dict.fromkeys(MATRIX_POINTS, "matrix"))
    failures: list[Failure] = field(default_factory=list)
    _entries: dict[tuple[str, str], Entry] = field(default_factory=dict)
    _origin: str = _DIRECT
    #: The entry point name of each distribution loaded so far.
    _extensions: dict[str, str] = field(default_factory=dict)

    def add_point(self, point: str, *, port: type | None = None) -> Registry:
        """Declare an extension point owned by the caller; with ``port``, a Protocol that
        whatever is registered there must keep. Raises ConfigError when another distribution
        owns the point already."""
        owner = self.owners.get(point)
        if owner is not None and owner not in (_DIRECT, self._origin) and self._origin != _DIRECT:
            if _owns(self._origin, point) and not _owns(owner, point):
                # The point is under this distribution's name, and an impostor declared it
                # first: the owner keeps it, and the impostor is quarantined whole.
                self._quarantine(
                    owner,
                    f"declared extension point {point!r}, which is "
                    f"{self._origin}'s; all of its entries and points were dropped",
                )
            else:
                raise ConfigError(
                    f"extension point {point!r} is declared by {owner}; {self._origin} cannot "
                    "declare it again",
                    fix=f"register at {owner}'s point as it declared it, or declare a point "
                    "of your own, under your own name",
                )
        self.points.add(point)
        if self.owners.get(point) in (None, _DIRECT):
            self.owners[point] = self._origin
        if port is not None:
            self.ports[point] = port
        return self

    def register(
        self,
        point: str,
        type_url: str,
        build: Callable[..., Any],
        *,
        config: type[BaseModel] | None = None,
        needs: frozenset[str] | set[str] | tuple[str, ...] = (),
        summary: str = "",
        stability: Stability = "beta",
        stand_in: bool = False,
        drives: tuple[str, ...] | list[str] = (),
    ) -> Registry:
        """Register ``build(config, **needs)`` (or ``build(**needs)`` when untyped).

        Raises ConfigError for an unknown point, a type URL without a version segment, a
        duplicate, or a class that does not keep its point's port.
        """
        if point not in self.points:
            raise _UnknownPointError(
                f"no extension point {point!r}. Points: {', '.join(sorted(self.points))}",
                fix="register at an existing point, or have its owner declare it",
            )
        parse_type_url(type_url, where=f"{point} registration")
        port = self.ports.get(point)
        if port is not None and isinstance(build, type):
            lacking = _breaks(build, port)
            if lacking:
                raise ConfigError(
                    f"{point} {type_url!r}: {build.__name__} does not keep the {port.__name__} "
                    f"port: {', '.join(lacking)}",
                    fix=_port_fix(port),
                )
        key = (point, type_url)
        existing = self._entries.get(key)
        if existing is not None:
            if _owns(self._origin, type_url) and not _owns(existing.origin, type_url):
                # The namespace's owner arrived after an impostor: the owner keeps its name,
                # and every entry the impostor registered is quarantined with it.
                self._quarantine(
                    existing.origin,
                    f"registered {point} {type_url!r} in {self._origin}'s namespace; all of "
                    "its entries and points were dropped",
                )
            else:
                raise ConfigError(
                    f"{point} {type_url!r} is registered by both {existing.origin} and "
                    f"{self._origin}",
                    fix="uninstall one of the two, or have one register under its own namespace",
                )
        self._entries[key] = Entry(
            point=point,
            type_url=type_url,
            build=build,
            config=config,
            needs=frozenset(needs),
            summary=summary,
            stability=stability,
            origin=self._origin,
            stand_in=stand_in,
            drives=tuple(drives),
        )
        return self

    def _quarantine(self, origin: str, why: str) -> None:
        """Drop everything ``origin`` registered or declared, and record why."""
        self._entries = {k: e for k, e in self._entries.items() if e.origin != origin}
        for point in [p for p, owner in self.owners.items() if owner == origin]:
            del self.owners[point]
            self.points.discard(point)
            self.ports.pop(point, None)
        self.failures.append(
            Failure(extension=self._extensions.get(origin, origin), origin=origin, error=why)
        )

    def register_payload(
        self, type_url: str, model: type[BaseModel], summary: str = ""
    ) -> Registry:
        """Register the schema values of ``type_url`` must validate against. Without a
        ``summary``, the first line of the model's docstring is its summary."""
        first_line = (model.__doc__ or "").strip().split("\n", 1)[0]
        return self.register("payload-type", type_url, model, summary=summary or first_line)

    def entry(self, point: str, type_url: str) -> Entry:
        found = self._entries.get((point, type_url))
        if found is None:
            known = ", ".join(sorted(u for p, u in self._entries if p == point)) or "(none)"
            raise NotFoundError(
                f"no {point} {type_url!r} is registered. Registered: {known}",
                fix="install the extension that provides it, or fix the type URL",
                point=point,
                type_url=type_url,
            )
        return found

    def entries(self, point: str | None = None) -> list[Entry]:
        return sorted(
            (e for e in self._entries.values() if point is None or e.point == point),
            key=lambda e: (e.point, e.type_url),
        )

    def __contains__(self, key: tuple[str, str]) -> bool:
        return key in self._entries

    def create(
        self,
        point: str,
        type_url: str,
        config: Mapping[str, Any] | None = None,
        *,
        needs: Mapping[str, Any] | None = None,
        where: str = "",
    ) -> Any:
        """Validate ``config`` and build the entry, handing it the needs it declared.

        ``where`` is the key path of the config in its document (``matrix.agent_runtimes.fast``):
        every problem's path starts with it. A builder that raises ConfigError states each
        problem relative to its config, and ``where`` is put before it; a problem with a
        ``source`` is in that source (a file the entry read) and is kept as it is. Raises
        NotFoundError for a type URL nothing registered, and ConfigError listing every problem
        with the config, or when what was built does not keep its point's port.
        """
        entry = self.entry(point, type_url)
        available = dict(needs or {})
        missing = sorted(entry.needs - set(available))
        if missing:
            raise ConfigError(
                f"{where or type_url}: {type_url} needs {missing}, which composition did not "
                "provide",
                fix="compose it through matrix.compose, or pass the needs explicitly",
            )
        given = {n: available[n] for n in entry.needs}
        if entry.config is None:
            if config:
                raise invalid(
                    f"{type_url} takes no config",
                    [
                        problem(
                            f"{where}.{key}" if where else str(key),
                            "unknown_key",
                            f"{type_url} takes no config, but got {key!r}",
                            fix=f"remove {key!r}",
                        )
                        for key in sorted(config)
                    ],
                )
            return self._kept(entry, _placed(where, entry.build, **given), where)
        try:
            validated = entry.config.model_validate(dict(config or {}))
        except ValidationError as e:
            raise invalid(
                f"invalid config for {type_url}",
                from_validation_error(e, prefix=where),
                fix=f"see `matrix describe {type_url}` for its config schema",
            ) from None
        return self._kept(entry, _placed(where, entry.build, validated, **given), where)

    def _kept(self, entry: Entry, built: Any, where: str) -> Any:
        """``built``, when it keeps the port of ``entry``'s point; ConfigError when not."""
        port = self.ports.get(entry.point)
        lacking = [] if port is None else _breaks(built, port)
        if port is not None and lacking:
            raise ConfigError(
                f"{where or entry.type_url}: {entry.type_url} built a {type(built).__name__}, "
                f"which is not a {port.__name__}: {', '.join(lacking)}",
                fix=f"{_port_fix(port)}; {entry.type_url} is registered by {entry.origin}",
            )
        return built

    def payload_schemas(self) -> dict[str, type[BaseModel]]:
        return {
            e.type_url: e.build
            for e in self.entries("payload-type")
            if isinstance(e.build, type) and issubclass(e.build, BaseModel)
        }

    def load(self, extensions: Iterable[Extension]) -> Registry:
        """Load ``extensions`` in name order; quarantine any that fail.

        Each extension registers all-or-nothing: if its ``register`` raises part way, none of
        its entries stay. An extension that registers at a point not yet declared (a plugin
        loading before the package that owns the point) is retried once the others have loaded.
        """
        pending = sorted(extensions, key=lambda extension: extension.name)
        for attempt in (1, 2):
            deferred = []
            for extension in pending:
                error = self._load(extension)
                if error is None:
                    continue
                if attempt == 1 and isinstance(error, _UnknownPointError):
                    deferred.append(extension)
                else:
                    self.failures.append(
                        Failure(
                            extension=extension.name,
                            origin=extension.origin,
                            error=f"{type(error).__name__}: {error}",
                        )
                    )
            pending = deferred
        return self

    def _load(self, extension: Extension) -> Exception | None:
        before = dict(self._entries)
        points = set(self.points)
        ports = dict(self.ports)
        owners = dict(self.owners)
        failures = len(self.failures)
        self._origin = extension.origin
        self._extensions[extension.origin] = extension.name
        try:
            extension.register(self)
        except Exception as e:
            self._entries = before  # all-or-nothing
            self.points = points
            self.ports = ports
            self.owners = owners
            del self.failures[failures:]
            return e
        finally:
            self._origin = _DIRECT
        return None

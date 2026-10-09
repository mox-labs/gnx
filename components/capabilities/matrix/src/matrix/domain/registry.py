"""The registry: extension points, and the implementations registered at each.

An **extension point** is a named place where implementations of one protocol plug in
(``runtime``, ``component``, ``payload-type``, ``observer``; ix adds ``sensor`` and
``engine``). An **entry** is one implementation, registered under a type URL, with:

* ``config``: the pydantic model its config validates against (typed extension config: the
  type URL selects the implementation, the model checks its settings);
* ``needs``: shared things composition hands it (``agents``, ``models``, ``cwd``), declared,
  never matched by field name;
* ``effects``: what it may touch (``network``, ``subprocess``, ``filesystem``, ``model``).
  ``None`` means **unknown**, which is never read as "none" (slick SD-12);
* ``summary``, ``stability`` (``stable``/``beta``/``experimental``) and ``origin`` (the
  distribution that registered it).

Everything, built-ins included, arrives the same way: an entry point in the
``matrix.extensions`` group whose value is ``register(registry) -> None``. One extension that
fails to import or register is **quarantined**: recorded in :attr:`Registry.failures`,
reported by ``matrix catalog``, and the rest load. A second registration of the same type URL
at the same point is refused and recorded, naming both origins; install order never decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ValidationError

from matrix.domain.errors import ConfigError, NotFoundError
from matrix.domain.ids import check_type_url

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

ENTRY_POINT_GROUP = "matrix.extensions"
Stability = Literal["stable", "beta", "experimental"]

#: The points matrix owns. Packages add theirs with :meth:`Registry.add_point`.
MATRIX_POINTS = ("runtime", "component", "payload-type", "observer")


@dataclass(frozen=True)
class Entry:
    point: str
    type_url: str
    build: Callable[..., Any]
    config: type[BaseModel] | None = None
    needs: frozenset[str] = frozenset()
    effects: frozenset[str] | None = None
    summary: str = ""
    stability: Stability = "beta"
    origin: str = "(direct)"

    def describe(self) -> dict[str, Any]:
        """A JSON-ready description: what an agent reads to decide whether to use it."""
        return {
            "point": self.point,
            "type_url": self.type_url,
            "summary": self.summary,
            "stability": self.stability,
            "origin": self.origin,
            "needs": sorted(self.needs),
            "effects": None if self.effects is None else sorted(self.effects),
            "config": None if self.config is None else self.config.model_json_schema(),
        }


class _UnknownPointError(ConfigError):
    """A registration at a point nobody has declared (yet)."""


@dataclass(frozen=True)
class Failure:
    """An extension that could not be loaded, or a registration that was refused."""

    extension: str
    origin: str
    error: str


@dataclass
class Registry:
    points: set[str] = field(default_factory=lambda: set(MATRIX_POINTS))
    failures: list[Failure] = field(default_factory=list)
    _entries: dict[tuple[str, str], Entry] = field(default_factory=dict)
    _origin: str = "(direct)"

    def add_point(self, point: str) -> Registry:
        """Declare an extension point owned by the caller (ix declares ``sensor``)."""
        self.points.add(point)
        return self

    def register(
        self,
        point: str,
        type_url: str,
        build: Callable[..., Any],
        *,
        config: type[BaseModel] | None = None,
        needs: frozenset[str] | set[str] | tuple[str, ...] = (),
        effects: frozenset[str] | set[str] | tuple[str, ...] | None = None,
        summary: str = "",
        stability: Stability = "beta",
    ) -> Registry:
        """Register ``build(config, **needs)`` (or ``build(**needs)`` when untyped).

        Raises ConfigError for an unknown point, a malformed type URL, or a duplicate.
        """
        if point not in self.points:
            raise _UnknownPointError(
                f"no extension point {point!r}. Points: {', '.join(sorted(self.points))}",
                fix="register at an existing point, or have its owner declare it",
            )
        check_type_url(type_url, where=f"{point} registration")
        key = (point, type_url)
        if key in self._entries:
            raise ConfigError(
                f"{point} {type_url!r} is registered by both {self._entries[key].origin} and "
                f"{self._origin}",
                fix="uninstall one of the two, or have one register under its own namespace",
            )
        self._entries[key] = Entry(
            point=point,
            type_url=type_url,
            build=build,
            config=config,
            needs=frozenset(needs),
            effects=None if effects is None else frozenset(effects),
            summary=summary,
            stability=stability,
            origin=self._origin,
        )
        return self

    def register_payload(
        self, type_url: str, model: type[BaseModel], summary: str = ""
    ) -> Registry:
        """Register the schema values of ``type_url`` must validate against."""
        return self.register(
            "payload-type", type_url, model, summary=summary or (model.__doc__ or "").strip()
        )

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
        where: str = "<config>",
    ) -> Any:
        """Validate ``config`` and build the entry, handing it the needs it declared."""
        entry = self.entry(point, type_url)
        available = dict(needs or {})
        missing = sorted(entry.needs - set(available))
        if missing:
            raise ConfigError(
                f"{where}: {type_url} needs {missing}, which composition did not provide",
                fix="compose it through matrix.compose, or pass the needs explicitly",
            )
        given = {n: available[n] for n in entry.needs}
        if entry.config is None:
            if config:
                raise ConfigError(
                    f"{where}: {type_url} takes no config, but got keys {sorted(config)}"
                )
            return entry.build(**given)
        try:
            validated = entry.config.model_validate(dict(config or {}))
        except ValidationError as e:
            problems = [
                f"{where}.{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}"
                for err in e.errors()
            ]
            raise ConfigError(
                f"invalid config for {type_url}:\n  " + "\n  ".join(problems),
                problems=problems,
                fix=f"see `matrix describe {type_url}` for its config schema",
            ) from None
        return entry.build(validated, **given)

    def payload_schemas(self) -> dict[str, type[BaseModel]]:
        return {
            e.type_url: e.build
            for e in self.entries("payload-type")
            if isinstance(e.build, type) and issubclass(e.build, BaseModel)
        }

    def discover(self, group: str = ENTRY_POINT_GROUP) -> Registry:
        """Load every ``group`` entry point, in name order; quarantine any that fail.

        Each extension registers all-or-nothing: if its ``register`` raises part way, none of
        its entries stay. An extension that registers at a point not yet declared (a sensor
        plugin loading before ix declares ``sensor``) is retried once the others have loaded.
        """
        pending = sorted(entry_points(group=group), key=lambda e: e.name)
        for attempt in (1, 2):
            deferred = []
            for ep in pending:
                origin = ep.dist.name if ep.dist is not None else ep.value
                error = self._load(ep, origin)
                if error is None:
                    continue
                if attempt == 1 and isinstance(error, _UnknownPointError):
                    deferred.append(ep)
                else:
                    self.failures.append(
                        Failure(ep.name, origin, f"{type(error).__name__}: {error}")
                    )
            pending = deferred
        return self

    def _load(self, ep: Any, origin: str) -> Exception | None:
        before = dict(self._entries)
        points = set(self.points)
        self._origin = origin
        try:
            register = ep.load()
            if not callable(register):
                raise TypeError(f"{ep.value} is not callable; expected register(registry)")
            register(self)
        except Exception as e:
            self._entries = before  # all-or-nothing
            self.points = points
            return e
        finally:
            self._origin = "(direct)"
        return None

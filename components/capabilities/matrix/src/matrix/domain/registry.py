"""ComponentRegistry — type URL to factory, with typed config and discovery.

Inspired by x.uma's xDS typed config registry: a type URL maps to a factory that builds a
component from config. Two registration forms:

* ``register(type_url, factory)`` — ``factory(**config)``. A config key the factory does not
  accept is reported as a ConfigError naming the type URL and the offending keys, not as a
  bare ``TypeError: unexpected keyword argument``.
* ``register_typed(type_url, config_cls, build)`` — the config dict is validated through a
  pydantic model first, then ``build(validated)``. A typo in YAML fails at validation with the
  key path and the source it came from.

Extensions register themselves: :meth:`discover` loads every ``matrix.components`` entry
point, each a callable ``register(registry) -> None``. A duplicate type URL still raises —
discovery never silently overrides. A malformed type URL is refused at registration, so a
typo surfaces when the extension loads rather than when a config first names it.
"""

from __future__ import annotations

import inspect
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ValidationError

from .errors import ConfigError, NotFoundError
from .type_url import parse_type_url

if TYPE_CHECKING:
    from collections.abc import Callable

ENTRY_POINT_GROUP = "matrix.components"

C = TypeVar("C", bound=BaseModel)


@runtime_checkable
class ComponentFactory(Protocol):
    """Anything callable that produces a Component from keyword args."""

    def __call__(self, **config: Any) -> Any: ...


class ComponentRegistry:
    """Type URL → component factory.

    Usage::

        registry = (
            ComponentRegistry()
            .register("ix.v1/probe.prompt", PromptProbe)
            .register_typed("ix.v1/sensor.activation", ActivationConfig, ActivationSensor)
        )
        probe = registry.create("ix.v1/probe.prompt", {"template": "..."})
    """

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[dict[str, Any], str], Any]] = {}
        self._config_classes: dict[str, type[BaseModel]] = {}

    def register(self, type_url: str, factory: ComponentFactory) -> ComponentRegistry:
        """Register ``factory(**config)``. Raises ConfigError on a duplicate or malformed URL."""

        def create(config: dict[str, Any], source: str) -> Any:
            try:
                return factory(**config)
            except TypeError as e:
                problem = _unaccepted_keys(factory, config)
                if problem is None:
                    raise
                raise ConfigError(f"{source}: {type_url}: {problem}") from e

        return self._add(type_url, create)

    def register_typed(
        self, type_url: str, config_cls: type[C], build: Callable[[C], Any]
    ) -> ComponentRegistry:
        """Register a builder whose config is validated through ``config_cls`` first."""

        def create(config: dict[str, Any], source: str) -> Any:
            try:
                validated = config_cls.model_validate(config)
            except ValidationError as e:
                lines = [
                    f"  {source}: {type_url}: {'.'.join(str(p) for p in err['loc']) or '(root)'}: "
                    f"{err['msg']}"
                    for err in e.errors()
                ]
                raise ConfigError("invalid component config:\n" + "\n".join(lines)) from None
            return build(validated)

        self._add(type_url, create)
        self._config_classes[type_url] = config_cls
        return self

    def _add(
        self, type_url: str, create: Callable[[dict[str, Any], str], Any]
    ) -> ComponentRegistry:
        parse_type_url(type_url)
        if type_url in self._factories:
            raise ConfigError(f"Duplicate registration: {type_url!r} is already registered")
        self._factories[type_url] = create
        return self

    def create(
        self, type_url: str, config: dict[str, Any] | None = None, *, source: str = "<config>"
    ) -> Any:
        """Create a component. ``source`` names where the config came from, for errors.

        Raises NotFoundError (a KeyError) listing the registered type URLs if ``type_url``
        is unknown.
        """
        create = self._factories.get(type_url)
        if create is None:
            known = ", ".join(sorted(self._factories)) or "(none)"
            raise NotFoundError(f"Unknown component type: {type_url!r}. Registered: {known}")
        return create(dict(config or {}), source)

    def config_class(self, type_url: str) -> type[BaseModel] | None:
        """The typed config a ``register_typed`` entry validates against; None if untyped.

        Composition reads it to supply shared context — a ``models`` registry, a working
        directory — to any component whose config declares that field, without naming the
        component.
        """
        return self._config_classes.get(type_url)

    def discover(self, group: str = ENTRY_POINT_GROUP) -> ComponentRegistry:
        """Load every entry point in ``group``; each is ``register(registry) -> None``."""
        for ep in entry_points(group=group):
            register = ep.load()
            if not callable(register):
                raise ConfigError(
                    f"entry point {ep.name!r} ({ep.value}) in {group!r} is not callable; "
                    "expected register(registry) -> None"
                )
            register(self)
        return self

    def __contains__(self, type_url: str) -> bool:
        return type_url in self._factories

    def __len__(self) -> int:
        return len(self._factories)

    def types(self) -> frozenset[str]:
        """Return all registered type URLs."""
        return frozenset(self._factories)


def _unaccepted_keys(factory: Callable[..., Any], config: dict[str, Any]) -> str | None:
    """Explain a TypeError from ``factory(**config)`` in config terms, or None if unrelated."""
    try:
        params = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        return None
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return None
    accepted = {
        n
        for n, p in params.items()
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    unknown = sorted(set(config) - accepted)
    if unknown:
        return f"unknown config key(s) {unknown}. Accepted: {sorted(accepted)}"
    required = {
        n
        for n, p in params.items()
        if p.default is inspect.Parameter.empty
        and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    missing = sorted(required - set(config))
    if missing:
        return f"missing required config key(s) {missing}"
    return None

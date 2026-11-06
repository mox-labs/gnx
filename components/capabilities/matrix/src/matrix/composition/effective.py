"""The effective value of every key a run is planned from, and where each came from.

A plan reports each value as composition will use it: given in a config file (``config``,
with the file), taken from an agent definition (``definition``), the default of an
extension's own typed config (``extension``: a runtime's, the store's, a component type's),
or matrix's own default (``matrix``). A default is never left out. A key whose default means
"no bound" says so in its ``note``.

Whether a key was given is what its validated model records (``model_fields_set``); which
file gave it is what loading recorded (``Config.origins``), found by the key's path or the
nearest path above it. A value is JSON data, as ``model_dump(mode="json")`` gives it. A model
is walked field by field, and a mapping of models key by key; any other value is one leaf.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from matrix.composition.builtins import (
    AGENT_RUNTIME,
    MODEL_RUNTIME,
    agent_runtime_type_url,
    model_runtime_type_url,
    observer_type_url,
    store_type_url,
)
from matrix.domain.config import ObserverConfig
from matrix.domain.executor import Limits, Retry

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from matrix.composition.container import Container
    from matrix.domain.component import Wiring
    from matrix.domain.config import Config, RuntimeConfig
    from matrix.domain.registry import Registry

#: Who set a value: a config file, an agent definition, an extension's typed config's
#: default, or matrix's own default.
Origin = Literal["config", "definition", "extension", "matrix"]

#: What a limit left at its default of None means.
_UNBOUNDED = {
    "concurrency": "no cap: as many components at once as the wiring allows",
    "deadline_s": "no deadline: a component runs until it returns or the run is stopped",
}


class SourcedValue(BaseModel):
    """One effective setting, by its key path, and where it came from (``origin``, and
    ``file`` when a config file set it). ``note`` says what a default means when the value
    alone does not."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    value: Any
    origin: Origin
    file: str | None = None
    note: str = ""


def settings(loaded: Config[Any], container: Container, wiring: Wiring) -> list[SourcedValue]:
    """Every effective value a run of ``wiring`` is made from: the limits, each component's
    own limits and config, the store, every runtime, agent and observer, the definition
    directories and the model registry."""
    matrix = loaded.matrix
    origins = loaded.origins
    registry = container.registry
    run = list(_limits(matrix.limits, origins))
    found = list(run)
    for name in wiring.order():
        found += _component_limits(name, matrix.limits, origins, run)
    found += _typed(
        "matrix.store",
        matrix.store.type,
        "store" in matrix.model_fields_set and "type" in matrix.store.model_fields_set,
        registry,
        "store",
        store_type_url(matrix.store.type),
        matrix.store.options(),
        origins,
    )
    found += _runtimes(
        "matrix.agent_runtimes", matrix.agent_runtimes, AGENT_RUNTIME, registry, origins
    )
    found += _runtimes(
        "matrix.model_runtimes", matrix.model_runtimes, MODEL_RUNTIME, registry, origins
    )
    found += _agents(loaded, container)
    for index, spec in enumerate(matrix.observers):
        kind, options = (
            (spec, {}) if not isinstance(spec, ObserverConfig) else (spec.type, spec.options())
        )
        found += _typed(
            f"matrix.observers.{index}",
            kind,
            True,
            registry,
            "observer",
            observer_type_url(kind),
            options,
            origins,
        )
    for entry in matrix.components:
        found += _options(
            f"matrix.components.{entry.name}.config",
            registry,
            "component",
            entry.type,
            entry.config,
            origins,
        )
    for key in ("definitions", "models"):
        found.append(
            _leaf(
                f"matrix.{key}",
                matrix.model_dump(mode="json")[key],
                key in matrix.model_fields_set,
                origins,
                "matrix",
            )
        )
    return found


def _file(path: str, origins: Mapping[str, str]) -> str | None:
    """The file that set ``path``, or the nearest path above it; None when no file did."""
    parts = path.split(".")
    for end in range(len(parts), 0, -1):
        found = origins.get(".".join(parts[:end]))
        if found:
            return found
    return None


def _leaf(
    path: str,
    value: Any,
    given: bool,
    origins: Mapping[str, str],
    fallback: Origin,
    note: str = "",
) -> SourcedValue:
    if given:
        file = _file(path, origins)
        return SourcedValue(path=path, value=value, origin="config", file=file, note=note)
    return SourcedValue(path=path, value=value, origin=fallback, note=note)


def _walk(
    model: BaseModel,
    at: str,
    origins: Mapping[str, str],
    fallback: Origin,
    *,
    skip: frozenset[str] = frozenset(),
) -> Iterator[SourcedValue]:
    """Each leaf of ``model`` under ``at``."""
    dumped = model.model_dump(mode="json")
    for name, value in model:
        if name in skip:
            continue
        path = f"{at}.{name}"
        if isinstance(value, BaseModel):
            yield from _walk(value, path, origins, fallback)
        elif (
            isinstance(value, dict)
            and value
            and all(isinstance(inner, BaseModel) for inner in value.values())
        ):
            for key, inner in value.items():
                yield from _walk(inner, f"{path}.{key}", origins, fallback)
        else:
            note = _UNBOUNDED.get(name, "") if value is None and isinstance(model, Limits) else ""
            yield _leaf(path, dumped[name], name in model.model_fields_set, origins, fallback, note)


def _limits(limits: Limits, origins: Mapping[str, str]) -> Iterator[SourcedValue]:
    """The run's own limits; each component's are reported by :func:`_component_limits`."""
    yield from _walk(limits, "matrix.limits", origins, "matrix", skip=frozenset({"components"}))


def _component_limits(
    name: str, limits: Limits, origins: Mapping[str, str], run: list[SourcedValue]
) -> list[SourcedValue]:
    """The limits the component ``name`` runs under: its own where it overrides the run's,
    the run's where it does not."""
    at = f"matrix.limits.components.{name}"
    own = limits.components.get(name)
    overrides = {} if own is None else own.model_dump(mode="json")
    inherited = {value.path.removeprefix("matrix.limits."): value for value in run}
    found: list[SourcedValue] = []
    for key in ("deadline_s", "grace_s", "cleanup_s"):
        if overrides.get(key) is not None:  # None, given or not, is the run's
            found.append(_leaf(f"{at}.{key}", overrides[key], True, origins, "config"))
        else:
            found.append(_inherited(f"{at}.{key}", inherited[key]))
    if own is not None and own.retry is not None:
        found += _walk(own.retry, f"{at}.retry", origins, "matrix")
    else:
        found += [
            _inherited(f"{at}.retry.{key}", inherited[f"retry.{key}"]) for key in Retry.model_fields
        ]
    return found


def _inherited(path: str, run: SourcedValue) -> SourcedValue:
    note = f"the run's {run.path}" + (f": {run.note}" if run.note else "")
    return run.model_copy(update={"path": path, "note": note})


def _typed(
    at: str,
    kind: str,
    given: bool,
    registry: Registry,
    point: str,
    url: str,
    options: Mapping[str, Any],
    origins: Mapping[str, str],
) -> list[SourcedValue]:
    """An entry's ``type``, then each of its options."""
    return [
        _leaf(f"{at}.type", kind, given, origins, "matrix"),
        *_options(at, registry, point, url, options, origins),
    ]


def _options(
    at: str,
    registry: Registry,
    point: str,
    url: str,
    options: Mapping[str, Any],
    origins: Mapping[str, str],
) -> list[SourcedValue]:
    """Each option of the entry at ``point`` under ``url``, as its typed config validates
    ``options``; none for an entry that takes no config."""
    if (point, url) not in registry:
        return []
    config = registry.entry(point, url).config
    if config is None:
        return []
    try:
        validated = config.model_validate(dict(options))
    except ValidationError:  # composition reported it; a plan is never made of it
        return []
    return list(_walk(validated, at, origins, "extension"))


def _runtimes(
    at: str,
    runtimes: Mapping[str, RuntimeConfig],
    point: str,
    registry: Registry,
    origins: Mapping[str, str],
) -> list[SourcedValue]:
    url_of = agent_runtime_type_url if point == AGENT_RUNTIME else model_runtime_type_url
    found: list[SourcedValue] = []
    for name, spec in runtimes.items():
        found += _typed(
            f"{at}.{name}", spec.type, True, registry, point, url_of(spec.type), spec.options(),
            origins,
        )  # fmt: skip
    return found


#: The keys of an agent a definition, or its inline config, decides: what bounds a session.
_AGENT_KEYS = ("model", "tools", "max_turns")


def _agents(loaded: Config[Any], container: Container) -> list[SourcedValue]:
    """Each agent's runtime, and the model, tools and turns its definition asks for: given in
    config, taken from its definition, or unset, which leaves them to its runtime."""
    origins = loaded.origins
    found: list[SourcedValue] = []
    for name, spec in loaded.matrix.agents.items():
        at = f"matrix.agents.{name}"
        found.append(_leaf(f"{at}.runtime", spec.runtime, True, origins, "config"))
        agent = container.agents.get(name)
        if agent is None:
            continue
        effective = agent.definition.model_dump(mode="json")
        base_name = spec.definition or name
        base = container.definitions.get(base_name)
        inline = spec.model_dump()
        for key in _AGENT_KEYS:
            path = f"{at}.{key}"
            if key in spec.model_fields_set and inline[key] is not None:
                found.append(_leaf(path, effective[key], True, origins, "config"))
            elif base is not None and base.model_dump()[key] is not None:
                found.append(
                    SourcedValue(
                        path=path,
                        value=effective[key],
                        origin="definition",
                        note=f"from the definition {base_name!r}",
                    )
                )
            else:
                found.append(
                    SourcedValue(
                        path=path,
                        value=effective[key],
                        origin="extension",
                        note="unset: the agent runtime's own default",
                    )
                )
    return found

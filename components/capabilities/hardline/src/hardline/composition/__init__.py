"""Composition root — the only module that names concrete adapters.

Two ways in, one result:

* ``build_runtime()`` discovers config tiers the way every capability in this workspace
  does (user ``~/.hardline/config.yaml`` < project ``./hardline.yaml`` < ``$HARDLINE_CONFIG``)
  and the backends installed in the environment. Each tier is named with how it was found
  (``user``, ``cwd``, ``$HARDLINE_CONFIG``), since the working directory and the environment
  decide which files are read.
* ``build_runtime(config={...})`` takes a mapping directly. This is how another
  composition root — ix's, matrix's — wires hardline from a section of its own config
  without hardline knowing that config exists.

Backends are discovered, not hardcoded: every backend, hardline's own three included, is a
``hardline.backends`` entry point. A third-party backend is a package that declares one entry
point; hardline is not edited. Each entry point is loaded on its own, so a broken one fails
only the rows that name it.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hardline.adapters._out.otel import OtelCallObserver, Telemetry, TracedBackend
from hardline.adapters._out.secrets import EnvFileSecretResolver
from hardline.adapters._out.yaml_source import MappingConfigSource, YamlConfigSource
from hardline.domain.errors import ConfigError
from hardline.domain.ports._out.backend import ModelBackend
from hardline.domain.registry import ModelRegistry, models_not_a_mapping, unknown_keys
from hardline.domain.runtime import ModelRuntime
from hardline.domain.types import Problem

if TYPE_CHECKING:
    import random
    from importlib.metadata import EntryPoint

    from hardline.adapters._out.otel import MeterProvider, TracerProvider
    from hardline.domain.ports._out.config_source import ConfigSource
    from hardline.domain.ports._out.observer import CallObserver
    from hardline.domain.ports._out.secrets import SecretResolver
    from hardline.domain.types import ModelSpec, RawCompletion, Request

TOOL = "hardline"
BACKEND_GROUP = "hardline.backends"
CONFIG_ENV = "HARDLINE_CONFIG"

logger = logging.getLogger("hardline")


class _Unusable:
    """Stands in for a backend entry point that cannot be used, and says why.

    A row that names it fails composition with that reason (``check``); a row that does not is
    never affected. It claims every structured mode so that the reason is the only problem a
    row reports.
    """

    structured_modes = frozenset({"prompt", "json_object", "json_schema"})

    def __init__(self, problem: str) -> None:
        self._problem = problem

    def check(self, spec: ModelSpec) -> list[str]:
        return [f"backend: {self._problem}"]

    async def complete(
        self, spec: ModelSpec, request: Request, api_key: str | None
    ) -> RawCompletion:
        raise ConfigError(f"models.{spec.name}.backend: {self._problem}")


def discover_backends() -> dict[str, ModelBackend]:
    """Every installed ``hardline.backends`` entry point, hardline's own included.

    Each is loaded on its own. One that cannot be loaded, does not produce a ModelBackend, or
    shares its name with another entry point is kept as a stand-in that names the fault: a row
    naming it fails composition with that fault, and a warning is logged. No entry point may
    shadow another; a collision is never resolved silently.
    """
    found: dict[str, list[EntryPoint]] = {}
    for ep in entry_points(group=BACKEND_GROUP):
        same_name = found.setdefault(ep.name, [])
        if all(other.value != ep.value for other in same_name):
            same_name.append(ep)
    backends: dict[str, ModelBackend] = {}
    for name, eps in sorted(found.items()):
        if len(eps) > 1:
            values = ", ".join(ep.value for ep in eps)
            backends[name] = _unusable(
                f"backend {name!r} is registered by more than one entry point ({values}); "
                "uninstall all but one"
            )
        else:
            backends[name] = _load(eps[0])
    return backends


def _load(ep: EntryPoint) -> ModelBackend:
    try:
        backend = ep.load()()
    except Exception as e:  # a plugin's import or factory may raise anything
        return _unusable(
            f"backend entry point {ep.name!r} ({ep.value}) could not be loaded: "
            f"{type(e).__name__}: {e}"
        )
    if not isinstance(backend, ModelBackend):
        return _unusable(
            f"backend entry point {ep.name!r} ({ep.value}) did not produce a ModelBackend "
            "(needs a `structured_modes` frozenset and an async `complete`)"
        )
    return backend


def _unusable(problem: str) -> _Unusable:
    logger.warning("%s; rows that name it will fail", problem)
    return _Unusable(problem)


def discover_sources(project_root: Path | None = None) -> list[ConfigSource]:
    """Config tiers in priority order, lowest first, each named with how it was found.

    The user and project files may be absent, and read as empty. A file named by
    ``$HARDLINE_CONFIG`` must exist and be a file.
    """
    tiers: list[ConfigSource] = [
        YamlConfigSource(Path.home() / f".{TOOL}" / "config.yaml", rung="user"),
        YamlConfigSource((project_root or Path.cwd()) / f"{TOOL}.yaml", rung="cwd"),
    ]
    explicit = os.environ.get(CONFIG_ENV)
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            raise ConfigError(f"${CONFIG_ENV} points at {path}, which does not exist")
        if not path.is_file():
            raise ConfigError(f"${CONFIG_ENV} points at {path}, which is not a file")
        tiers.append(YamlConfigSource(path, rung=f"${CONFIG_ENV}"))
    return tiers


def merge_tiers(
    sources: Sequence[ConfigSource],
) -> tuple[dict[str, Any], dict[str, dict[str, str]], str]:
    """Merge tiers: later tiers override earlier ones field by field, per model.

    Returns the merged mapping; for each model, ``{field: source}``, the tier that set each
    field, ordered so the last entry is the last tier that touched the row; and a one-line
    description of the tiers consulted.
    """
    merged_models: dict[str, dict[str, Any]] = {}
    origins: dict[str, dict[str, str]] = {}
    default: Any = None
    consulted: list[str] = []
    for source in sources:
        data = source.read()
        consulted.append(source.describe() + ("" if data else " (absent/empty)"))
        if not data:
            continue
        unknown = set(data) - {"default", "models"}
        if unknown:
            raise unknown_keys(unknown, source.describe())
        if "default" in data:
            default = data["default"]
        models = data.get("models") or {}
        if not isinstance(models, Mapping):
            raise models_not_a_mapping(source.describe())
        for name, row in models.items():
            if not isinstance(row, Mapping):
                raise ConfigError(
                    f"{source.describe()}: models.{name}: must be a mapping",
                    problems=[
                        Problem(
                            path=f"models.{name}",
                            kind="wrong_shape",
                            message="must be a mapping",
                            source=source.describe(),
                        )
                    ],
                )
            merged_models[str(name)] = {**merged_models.get(str(name), {}), **row}
            fields = origins.setdefault(str(name), {})
            for field in row:
                fields.pop(str(field), None)  # re-inserted, so the latest tier is last
                fields[str(field)] = source.describe()
    merged: dict[str, Any] = {"models": merged_models}
    if default is not None:
        merged["default"] = default
    return merged, origins, "; ".join(consulted) or "(no sources)"


def load_registry(
    sources: Sequence[ConfigSource], backends: Mapping[str, ModelBackend] | None = None
) -> ModelRegistry:
    """The merged tiers as a registry. Given the ``backends``, each row that names no
    ``base_url`` gets its backend's ``default_base_url``, so the row records where it sends."""
    merged, origins, consulted = merge_tiers(sources)
    registry = ModelRegistry.from_config(merged, source=consulted, origins=origins)
    return registry if backends is None else registry.with_locations(default_locations(backends))


def default_locations(backends: Mapping[str, ModelBackend]) -> dict[str, str]:
    """Backend name -> the ``default_base_url`` it declares, for the backends that declare one."""
    found: dict[str, str] = {}
    for name, backend in backends.items():
        url = getattr(backend, "default_base_url", None)
        if isinstance(url, str):
            found[name] = url
    return found


def build_runtime(
    config: Mapping[str, Any] | None = None,
    *,
    sources: Sequence[ConfigSource] | None = None,
    backends: Mapping[str, ModelBackend] | None = None,
    secrets: SecretResolver | None = None,
    schema_retries: int = 1,
    label: str = "<mapping>",
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: random.Random | None = None,
    observer: CallObserver | None = None,
    tracer_provider: TracerProvider | None = None,
    meter_provider: MeterProvider | None = None,
) -> ModelRuntime:
    """Wire a ModelRuntime.

    ``config`` wins over ``sources``; with neither, tiers are discovered from disk.
    ``backends`` defaults to the installed entry points; tests pass their own, and inject
    ``sleep``/``rng`` to observe retry backoff without waiting.

    Every backend, found or handed in, is wrapped in ``TracedBackend``: one ``chat`` span and
    the inference metrics per backend call. ``observer`` hears each logical call's failed
    attempts, fallbacks and answer; by default ``OtelCallObserver`` counts the retries and
    fallbacks. Both use the OpenTelemetry API on ``tracer_provider`` and ``meter_provider``,
    the global ones by default, which record nothing until an application installs an SDK.
    """
    if config is not None:
        tiers: Sequence[ConfigSource] = [MappingConfigSource(dict(config), label=label)]
    elif sources is not None:
        tiers = sources
    else:
        tiers = discover_sources()
    found = backends if backends is not None else discover_backends()
    telemetry = Telemetry(tracer_provider, meter_provider)
    traced: dict[str, ModelBackend] = {
        name: TracedBackend(backend, name, telemetry) for name, backend in found.items()
    }
    return ModelRuntime(
        load_registry(tiers, traced),
        traced,
        secrets if secrets is not None else EnvFileSecretResolver(),
        schema_retries=schema_retries,
        sleep=sleep,
        rng=rng,
        observer=observer if observer is not None else OtelCallObserver(telemetry),
    )


__all__ = [
    "BACKEND_GROUP",
    "CONFIG_ENV",
    "build_runtime",
    "default_locations",
    "discover_backends",
    "discover_sources",
    "load_registry",
    "merge_tiers",
]

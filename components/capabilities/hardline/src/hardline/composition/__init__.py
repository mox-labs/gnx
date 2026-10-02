"""Composition root — the only module that names concrete adapters.

Two ways in, one result:

* ``build_runtime()`` discovers config tiers the way every capability in this workspace
  does (user ``~/.hardline/config.yaml`` < project ``./hardline.yaml`` < ``$HARDLINE_CONFIG``)
  and the backends installed in the environment.
* ``build_runtime(config={...})`` takes a mapping directly. This is how another
  composition root — ix's, matrix's — wires hardline from a section of its own config
  without hardline knowing that config exists.

Backends are discovered, not hardcoded: the three built-ins are registered first, then
every ``hardline.backends`` entry point. A third-party backend is a package that declares
one entry point; hardline is not edited.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hardline.adapters._out.anthropic_native import AnthropicBackend
from hardline.adapters._out.mock import MockBackend
from hardline.adapters._out.openai_compat import OpenAICompatBackend
from hardline.adapters._out.secrets import EnvFileSecretResolver
from hardline.adapters._out.yaml_source import MappingConfigSource, YamlConfigSource
from hardline.domain.errors import ConfigError
from hardline.domain.ports._out.backend import ModelBackend
from hardline.domain.registry import ModelRegistry
from hardline.domain.runtime import ModelRuntime

if TYPE_CHECKING:
    from hardline.domain.ports._out.config_source import ConfigSource
    from hardline.domain.ports._out.secrets import SecretResolver

TOOL = "hardline"
BACKEND_GROUP = "hardline.backends"
CONFIG_ENV = "HARDLINE_CONFIG"

_BUILTINS: dict[str, Callable[[], ModelBackend]] = {
    "openai-compat": OpenAICompatBackend,
    "anthropic": AnthropicBackend,
    "mock": MockBackend,
}


def discover_backends(*, include_entry_points: bool = True) -> dict[str, ModelBackend]:
    """Built-in backends plus every installed ``hardline.backends`` entry point.

    An entry point may not shadow a built-in or another entry point: a name collision is a
    ConfigError naming both, never a silent override.
    """
    backends: dict[str, ModelBackend] = {name: make() for name, make in _BUILTINS.items()}
    if not include_entry_points:
        return backends
    for ep in entry_points(group=BACKEND_GROUP):
        if ep.name in backends:
            raise ConfigError(
                f"backend entry point {ep.name!r} ({ep.value}) collides with an "
                "already-registered backend of the same name"
            )
        factory = ep.load()
        backend = factory()
        if not isinstance(backend, ModelBackend):
            raise ConfigError(
                f"backend entry point {ep.name!r} ({ep.value}) did not produce a ModelBackend "
                "(needs a `structured_modes` frozenset and an async `complete`)"
            )
        backends[ep.name] = backend
    return backends


def discover_sources(project_root: Path | None = None) -> list[ConfigSource]:
    """Config tiers in priority order, lowest first. Missing files read as empty."""
    tiers: list[ConfigSource] = [
        YamlConfigSource(Path.home() / f".{TOOL}" / "config.yaml"),
        YamlConfigSource((project_root or Path.cwd()) / f"{TOOL}.yaml"),
    ]
    explicit = os.environ.get(CONFIG_ENV)
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            raise ConfigError(f"${CONFIG_ENV} points at {path}, which does not exist")
        tiers.append(YamlConfigSource(path))
    return tiers


def merge_tiers(sources: Sequence[ConfigSource]) -> tuple[dict[str, Any], dict[str, str], str]:
    """Merge tiers: later tiers override earlier ones field by field, per model.

    Returns the merged mapping, a model-name → source map for error attribution, and a
    one-line description of the tiers consulted.
    """
    merged_models: dict[str, dict[str, Any]] = {}
    origins: dict[str, str] = {}
    default: Any = None
    consulted: list[str] = []
    for source in sources:
        data = source.read()
        consulted.append(source.describe() + ("" if data else " (absent/empty)"))
        if not data:
            continue
        unknown = set(data) - {"default", "models"}
        if unknown:
            raise ConfigError(
                f"{source.describe()}: unknown top-level key(s) {sorted(unknown)}. "
                "Legal: ['default', 'models']"
            )
        if "default" in data:
            default = data["default"]
        models = data.get("models") or {}
        if not isinstance(models, Mapping):
            raise ConfigError(f"{source.describe()}: 'models' must be a mapping of name -> spec")
        for name, row in models.items():
            if not isinstance(row, Mapping):
                raise ConfigError(f"{source.describe()}: models.{name}: must be a mapping")
            merged_models[str(name)] = {**merged_models.get(str(name), {}), **row}
            origins[str(name)] = source.describe()
    merged: dict[str, Any] = {"models": merged_models}
    if default is not None:
        merged["default"] = default
    return merged, origins, "; ".join(consulted) or "(no sources)"


def load_registry(sources: Sequence[ConfigSource]) -> ModelRegistry:
    merged, origins, consulted = merge_tiers(sources)
    return ModelRegistry.from_config(merged, source=consulted, origins=origins)


def build_runtime(
    config: Mapping[str, Any] | None = None,
    *,
    sources: Sequence[ConfigSource] | None = None,
    backends: Mapping[str, ModelBackend] | None = None,
    secrets: SecretResolver | None = None,
    schema_retries: int = 1,
    label: str = "<mapping>",
) -> ModelRuntime:
    """Wire a ModelRuntime.

    ``config`` wins over ``sources``; with neither, tiers are discovered from disk.
    ``backends`` defaults to built-ins plus entry points; tests pass their own.
    """
    if config is not None:
        tiers: Sequence[ConfigSource] = [MappingConfigSource(dict(config), label=label)]
    elif sources is not None:
        tiers = sources
    else:
        tiers = discover_sources()
    return ModelRuntime(
        load_registry(tiers),
        backends if backends is not None else discover_backends(),
        secrets if secrets is not None else EnvFileSecretResolver(),
        schema_retries=schema_retries,
    )


__all__ = [
    "BACKEND_GROUP",
    "CONFIG_ENV",
    "build_runtime",
    "discover_backends",
    "discover_sources",
    "load_registry",
    "merge_tiers",
]

"""Config composition — 3-tier discovery, merge, validate.

Clients call load_config(MyConfigType) and get back Config[MyConfigType]
with both Matrix platform settings and their own validated section.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from matrix.domain.config import Config, MatrixConfig
from matrix.domain.errors import ConfigError

C = TypeVar("C", bound=BaseModel)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge. Override wins. Lists replace entirely."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def config_env_var(tool: str) -> str:
    """The environment variable naming an explicit config file: ``ix`` → ``IX_CONFIG``."""
    return tool.upper().replace("-", "_").replace(".", "_") + "_CONFIG"


def discover_sources(
    tool: str,
    project_root: Path | None = None,
) -> list[Path]:
    """Config files for a tool, lowest priority first. The convention every capability shares.

    0. the schema's own defaults (no file)
    1. user — ``~/.{tool}/config.yaml``
    2. project — ``./{tool}.yaml`` (or ``project_root/{tool}.yaml``)
    3. explicit — the file named by ``${TOOL}_CONFIG``, which must exist

    Missing user and project files read as empty. Matrix provides the pattern; the tool
    provides the name. Command-line flags, applied by the tool, sit above all of these.
    """
    root = project_root or Path.cwd()
    sources = [Path.home() / f".{tool}" / "config.yaml", root / f"{tool}.yaml"]
    variable = config_env_var(tool)
    explicit = os.environ.get(variable)
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            raise ConfigError(f"${variable} points at {path}, which does not exist")
        sources.append(path)
    return sources


def load_config(  # noqa: UP047
    client_type: type[C],
    client_key: str,
    sources: list[Path] | None = None,
) -> Config[C]:
    """Load and validate composed config. Raises ConfigError naming key paths and sources.

    Args:
        client_type: Pydantic model class for client config section.
        client_key: YAML key for the client section (e.g., "ix", "memex", "radix").
        sources: Config file paths in priority order.
            If None, uses discover_sources(client_key) for 3-tier discovery.

    Returns:
        Validated Config[C] with both matrix and client sections.
    """
    from matrix.adapters._out.config.yaml_source import YamlConfigSource

    if sources is None:
        sources = discover_sources(client_key)

    merged: dict[str, Any] = {}
    consulted: list[str] = []
    for path in sources:
        source = YamlConfigSource(path)
        tier_data = source.read()
        consulted.append(source.describe() + ("" if tier_data else " (absent/empty)"))
        if tier_data:
            merged = deep_merge(merged, tier_data)

    matrix_config = _validate(MatrixConfig, merged.get("matrix", {}), "matrix", consulted)
    client_config = _validate(client_type, merged.get(client_key, {}), client_key, consulted)
    return Config(matrix=matrix_config, client=client_config)


def _validate(model: type[C], data: Any, section: str, consulted: list[str]) -> C:  # noqa: UP047
    """Validate one section; on failure name the key path and every file consulted."""
    try:
        return model.model_validate(data)
    except ValidationError as e:
        lines = [
            f"  {section}.{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in e.errors()
        ]
        raise ConfigError(
            "invalid config:\n"
            + "\n".join(lines)
            + "\nsources (lowest priority first): "
            + "; ".join(consulted)
        ) from None

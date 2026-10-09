"""Configuration: one convention for every tool, and one file shape they share.

A tool (ix, recon, matrix's own CLI) names itself; matrix finds its config files and merges
them, later tiers winning, before validating anything:

====  ==============================  ==================================================
tier  file                            what it is for
====  ==============================  ==================================================
1     ``~/.matrix/config.yaml``       your runtimes, models and agents, for every tool
2     ``~/.<tool>/config.yaml``       your defaults for this tool
3     ``./matrix.yaml``               the project's shared runtimes and agents
4     ``./<tool>.yaml``               the project's settings for this tool
5     ``$MATRIX_CONFIG``              an explicit shared file (must exist)
6     ``$<TOOL>_CONFIG``              an explicit tool file (must exist)
====  ==============================  ==================================================

Any file may hold a ``matrix:`` section (runtimes, models, agents, definitions, observers)
and one section per tool (``ix:``). Schema defaults sit below tier 1; command-line flags,
applied by the tool, sit above tier 6. Absent files read as empty. ``Config.sources`` lists
every file consulted, so ``matrix config --sources`` shows which one set a value.

Two merge rules differ from "later wins": ``matrix.definitions`` directories **accumulate**
across tiers (your agents plus the project's), and each relative directory resolves against
the file that declared it. Other lists replace; mappings merge key by key.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ValidationError

from matrix.domain.config import Config, MatrixConfig
from matrix.domain.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from matrix.domain.ports._out.config_source import ConfigSource

C = TypeVar("C", bound=BaseModel)


class _Empty(BaseModel):
    """The client section of a config that only matrix reads."""


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
    """The variable naming an explicit config file: ``ix`` → ``IX_CONFIG``."""
    return tool.upper().replace("-", "_").replace(".", "_") + "_CONFIG"


def discover_sources(tool: str, project_root: Path | None = None) -> list[Path]:
    """The config files for ``tool``, lowest priority first (see the module docstring)."""
    root = project_root or Path.cwd()
    home = Path.home()
    paths = [
        home / ".matrix" / "config.yaml",
        home / f".{tool}" / "config.yaml",
        root / "matrix.yaml",
        root / f"{tool}.yaml",
    ]
    for variable in dict.fromkeys(("MATRIX_CONFIG", config_env_var(tool))):
        explicit = os.environ.get(variable)
        if explicit:
            path = Path(explicit).expanduser()
            if not path.exists():
                raise ConfigError(
                    f"${variable} points at {path}, which does not exist",
                    fix=f"create the file or unset {variable}",
                )
            paths.append(path)
    return list(dict.fromkeys(paths))  # a tool named "matrix" must not read files twice


def load_config(  # noqa: UP047
    client_type: type[C] | None = None,
    client_key: str = "matrix",
    sources: Sequence[Path | ConfigSource] | None = None,
) -> Config[Any]:
    """Load, merge and validate the config for ``client_key``.

    Returns ``Config[client_type]``: the shared ``matrix`` section plus the tool's section.
    Raises ConfigError naming every bad key path and every file consulted.
    """
    from matrix.adapters._out.config.yaml_source import YamlConfigSource

    if sources is None:
        sources = discover_sources(client_key)
    merged: dict[str, Any] = {}
    consulted: list[str] = []
    definitions: list[str] = []
    for item in sources:
        source = YamlConfigSource(item) if isinstance(item, Path) else item
        data = source.read()
        consulted.append(source.describe() + ("" if data else " (absent)"))
        if not data:
            continue
        declared = (data.get("matrix") or {}).get("definitions")
        if isinstance(declared, list) and isinstance(item, Path):
            base = item.parent
            definitions.extend(
                d if Path(d).is_absolute() else str((base / d).resolve()) for d in declared
            )
        merged = deep_merge(merged, data)
    if definitions:
        merged.setdefault("matrix", {})["definitions"] = list(dict.fromkeys(definitions))

    schema: type[BaseModel] = client_type or _Empty
    matrix_config = _validate(MatrixConfig, merged.get("matrix") or {}, "matrix", consulted)
    client_config = (
        _validate(schema, merged.get(client_key) or {}, client_key, consulted)
        if client_key != "matrix"
        else schema()
    )
    return Config[Any](matrix=matrix_config, client=client_config, sources=tuple(consulted))


def _validate(model: type[C], data: Any, section: str, consulted: list[str]) -> C:  # noqa: UP047
    try:
        return model.model_validate(data)
    except ValidationError as e:
        problems = [
            f"{section}.{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()
        ]
        raise ConfigError(
            "invalid config:\n  "
            + "\n  ".join(problems)
            + "\nsources (lowest priority first): "
            + "; ".join(consulted),
            problems=problems,
            sources=consulted,
            fix="fix the key paths listed; `matrix config --sources` shows which file set them",
        ) from None

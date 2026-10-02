"""YamlConfigSource — one YAML file as one config tier. Missing file reads as empty."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from hardline.domain.errors import ConfigError

if TYPE_CHECKING:
    from pathlib import Path


class YamlConfigSource:
    def __init__(self, path: Path) -> None:
        self._path = path

    def read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            data = yaml.safe_load(self._path.read_text())
        except yaml.YAMLError as e:
            raise ConfigError(f"{self._path}: not valid YAML: {e}") from e
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ConfigError(f"{self._path}: top level must be a mapping")
        return data

    def describe(self) -> str:
        return str(self._path)


class MappingConfigSource:
    """An in-memory tier — how another composition root hands hardline its own section."""

    def __init__(self, data: dict[str, Any], label: str = "<mapping>") -> None:
        self._data = data
        self._label = label

    def read(self) -> dict[str, Any]:
        return dict(self._data)

    def describe(self) -> str:
        return self._label

"""YamlConfigSource — one YAML file as one config tier. Missing file reads as empty."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from hardline.domain.errors import ConfigError
from hardline.domain.types import Problem

if TYPE_CHECKING:
    from pathlib import Path


class YamlConfigSource:
    """``rung`` says how the file was found (``cwd``, ``$HARDLINE_CONFIG``), and is part of its
    name wherever the tier is named: in errors, in ``hardline check``, in a row's record."""

    def __init__(self, path: Path, rung: str | None = None) -> None:
        self._path = path
        self._rung = rung

    def read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            data = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise self._unread(f"not valid YAML: {e}", "yaml_syntax") from e
        except UnicodeDecodeError as e:
            raise self._unread(f"not UTF-8 text: {e}", "yaml_syntax") from e
        except OSError as e:
            raise self._unread(f"cannot be read: {e.strerror}", "wrong_shape") from e
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise self._unread("top level must be a mapping", "wrong_shape")
        return data

    def _unread(self, message: str, kind: str) -> ConfigError:
        """The whole tier is the problem: its path is the document's root."""
        return ConfigError(
            f"{self.describe()}: {message}",
            problems=[Problem(path="", kind=kind, message=message, source=self.describe())],
        )

    def describe(self) -> str:
        return f"{self._rung} {self._path}" if self._rung else str(self._path)


class MappingConfigSource:
    """An in-memory tier — how another composition root hands hardline its own section."""

    def __init__(self, data: dict[str, Any], label: str = "<mapping>") -> None:
        self._data = data
        self._label = label

    def read(self) -> dict[str, Any]:
        return dict(self._data)

    def describe(self) -> str:
        return self._label

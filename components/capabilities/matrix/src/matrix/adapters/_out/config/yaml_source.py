"""YamlConfigSource — reads config from a single YAML file.

One file, one read(). No discovery logic — that's the composition concern.
Satisfies ConfigSource protocol via structural typing.
"""

from pathlib import Path
from typing import Any

import yaml

from matrix.domain.errors import ConfigError


class YamlConfigSource:
    """Reads a single YAML file as a config source.

    Returns empty dict if the file doesn't exist or is empty. A file that exists but is not a
    YAML mapping is an error naming the file — previously it was silently read as empty.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def read(self) -> dict[str, Any]:
        """Read and parse YAML. Empty dict if file missing or empty."""
        if not self._path.exists():
            return {}
        try:
            with open(self._path) as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(f"{self._path}: not valid YAML: {e}") from e
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ConfigError(
                f"{self._path}: top level must be a mapping, got {type(data).__name__}"
            )
        return data

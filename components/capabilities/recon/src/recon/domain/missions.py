"""The mission store port: where missions live and how their config files are read.

A mission is a name with a config file. Where that file sits (``.recon/<name>/config.yaml``
under the project root today), what format it is in (YAML today) and where the built-in
templates come from are the store's decisions; the rest of recon sees names, paths and the
parsed config.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from recon.domain.capture import CaptureLog


#: A mission's name: letters, digits, ``.``, ``_`` and ``-``, starting with a letter or a digit,
#: so it is one safe path component, and one word in every file a schedule projects.
MISSION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class ConfigFile:
    """A mission config as read: its exact bytes (hashed into the archive) and its data."""

    path: Path
    text: bytes
    data: Any


@dataclass(frozen=True)
class Template:
    name: str
    description: str
    text: str


class MissionStore(Protocol):
    @property
    def project(self) -> Path:
        """The project the missions belong to: a relative directory in a mission's config is
        relative to it."""
        ...

    @property
    def root(self) -> Path:
        """The directory every mission lives under."""
        ...

    def mission_dir(self, name: str) -> Path:
        """The mission's directory. Raises UsageError for a name that is not a mission name."""
        ...

    def config_path(self, name: str) -> Path: ...

    def config_text(self, name: str) -> bytes | None:
        """The mission's config exactly as stored, or None when the mission has none."""
        ...

    def read_config(self, path: Path) -> ConfigFile:
        """The config at ``path``; ``-`` reads standard input. Raises NotFoundError when there
        is no file, ConfigError when it does not parse."""
        ...

    def write_config(self, name: str, text: bytes) -> Path:
        """Write the mission's config, creating the mission; return where it went."""
        ...

    def missions(self) -> list[str]:
        """The names of the missions that have a config, sorted."""
        ...

    def capture_log(self, name: str) -> CaptureLog:
        """The mission's external capture log, the one ``recon capture`` appends to."""
        ...

    def capture_count(self, name: str) -> int: ...

    def exposed(self, name: str) -> Path | None:
        """The first path of the mission's own (its directory, or one under it) that a user
        other than its owner can reach: group or world permission bits set. None when every
        one is the owner's alone. Changes nothing."""
        ...

    def change_key(self, name: str) -> bytes:
        """The key the mission's change detection hashes records with (``recon.domain.
        changes``): random, made on first use where only its owner can read it, and the same
        on every later call, so the same tables give the same change rows."""
        ...

    def is_directory(self, path: Path) -> bool:
        """Whether ``path`` is a directory: a ``local`` source must name one."""
        ...

    def templates(self) -> list[Template]:
        """The built-in templates, sorted by name."""
        ...

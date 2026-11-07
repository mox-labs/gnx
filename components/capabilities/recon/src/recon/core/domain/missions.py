"""The mission store port: which missions there are, and how their config files are read.

A mission is a name with a config. Where that config is kept, what format it is in (YAML
today), where its capture log and change key are, and where the built-in templates come from
are the store's decisions: the rest of recon names a mission by its name alone. The paths
that do appear here are a person's own: the project directory a mission's relative
directories resolve against, a config file someone names, a directory a ``local`` source
names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from recon.core.domain.capture import CaptureLog
    from recon.core.domain.models import ReconConfig


#: A mission's name: letters, digits, ``.``, ``_`` and ``-``, starting with a letter or a digit,
#: so it is one safe name in every store, and one word in every file a schedule projects.
MISSION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


#: A config as an operation takes it: a file (``-``: standard input), a value (a
#: ``ReconConfig``, or the mapping a YAML file would hold), or None, the mission's own.
type ConfigGiven = Path | ReconConfig | Mapping[str, Any] | None


@dataclass(frozen=True)
class ConfigFile:
    """A mission config as read: where it was read from, said for a person (a file named, ``-``
    for standard input, or where the mission's own is kept), its exact bytes (hashed into the
    archive) and its data."""

    origin: str
    text: bytes
    data: Any


@dataclass(frozen=True)
class Template:
    name: str
    description: str
    text: str


@dataclass(frozen=True)
class Exposed:
    """The first of a mission's own files another user can reach, ``where`` it is and the
    ``fix`` that makes the mission its owner's alone, both said for a person by the store that
    knows its medium."""

    where: str
    fix: str


class MissionStore(Protocol):
    @property
    def project(self) -> Path:
        """The project the missions belong to: a relative directory in a mission's config is
        relative to it, and a command a mission runs without naming a directory runs there."""
        ...

    def where(self, name: str) -> str:
        """Where a person finds the mission's config to read or edit it. Raises UsageError
        for a name that is not a mission name."""
        ...

    def config_text(self, name: str) -> bytes | None:
        """The mission's config exactly as stored, or None when the mission has none."""
        ...

    def config(self, name: str) -> ConfigFile:
        """The mission's own config. Raises NotFoundError when it has none, ConfigError when
        it does not parse."""
        ...

    def read_config(self, path: Path) -> ConfigFile:
        """The config file a person names at ``path``; ``-`` reads standard input. Raises
        NotFoundError when there is no file, ConfigError when it does not parse."""
        ...

    def write_config(self, name: str, text: bytes) -> None:
        """Keep ``text`` as the mission's config, creating the mission."""
        ...

    def missions(self) -> list[str]:
        """The names of the missions that have a config, sorted."""
        ...

    def capture_log(self, name: str) -> CaptureLog:
        """The mission's external capture log, the one ``recon capture`` appends to."""
        ...

    def capture_count(self, name: str) -> int: ...

    def exposed(self, name: str) -> Exposed | None:
        """The first of the mission's own files (its home, or one under it) that a user other
        than its owner can reach: group or world permission bits set. None when every one is
        the owner's alone. Changes nothing."""
        ...

    def change_key(self, name: str) -> bytes:
        """The key the mission's change detection hashes records with (``recon.core.domain.
        changes``): random, made on first use where only its owner can read it, and the same
        on every later call, so the same tables give the same change rows. Raises ReconError
        (``config``) for a key that is not whole, which it never replaces."""
        ...

    def is_directory(self, path: Path) -> bool:
        """Whether ``path`` is a directory: a ``local`` source must name one."""
        ...

    def templates(self) -> list[Template]:
        """The built-in templates, sorted by name."""
        ...

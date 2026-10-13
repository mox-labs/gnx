"""Genre files and kind files: the installed ones, and those a lab keeps.

**Installed.** A package registers its genres at the ``ix.genre`` entry point and its kinds
at ``ix.kind``; ix owns both and registers its own there. An entry point names a package of
``*.yaml`` files, one record per file::

    [project.entry-points."ix.genre"]
    acme = "acme_ix.genres"

The entry point is never called: a genre or a kind is data, read with ``yaml.safe_load``.

**In a lab.** A ``genres/`` or ``kinds/`` directory next to an experiment file holds that
lab's own.

A file or package that cannot be read becomes a file with an ``error``; the others are
unaffected.
"""

from __future__ import annotations

from importlib import metadata, resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ix.domain.genre import GenreFile
from ix.domain.kind import KindFile

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from importlib.resources.abc import Traversable

GENRE_GROUP = "ix.genre"
KIND_GROUP = "ix.kind"


def _parse(text: str) -> tuple[Mapping[str, Any] | None, str]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        return None, " ".join(str(error).split())
    if not isinstance(data, dict):
        return None, "the file is not a YAML mapping"
    return data, ""


def _yaml_files(root: Traversable) -> list[Traversable]:
    return sorted(
        (item for item in root.iterdir() if item.is_file() and item.name.endswith(".yaml")),
        key=lambda item: item.name,
    )


def _read(
    group: str, directory: str, packages: Sequence[str] | None, lab: str | None
) -> list[tuple[str, Mapping[str, Any] | None, str]]:
    """(origin, data, error) for every file: installed packages first, then the lab's."""
    if packages is not None:
        installed = [(package, package) for package in packages]
    else:
        installed = sorted(
            (point.name, point.value) for point in metadata.entry_points(group=group)
        )
    found: list[tuple[str, Mapping[str, Any] | None, str]] = []
    for label, package in installed:
        try:
            files = _yaml_files(resources.files(package))
        except (ImportError, OSError, TypeError, ValueError) as error:
            found.append(
                (
                    f"{group} entry point {label!r} ({package})",
                    None,
                    f"the package could not be read: {error}",
                )
            )
            continue
        found += [
            (f"{package}/{file.name}", *_parse(file.read_text(encoding="utf-8"))) for file in files
        ]
    if lab is not None and (Path(lab) / directory).is_dir():
        found += [
            (str(file), *_parse(file.read_text(encoding="utf-8")))
            for file in sorted((Path(lab) / directory).glob("*.yaml"))
        ]
    return found


class GenreFiles:
    """Finds genre files. ``packages`` names the packages to read instead of discovering
    them through the entry point (for a test, or an embedding that pins its genres)."""

    def __init__(self, packages: Sequence[str] | None = None) -> None:
        self._packages = packages

    def files(self, lab: str | None = None) -> tuple[GenreFile, ...]:
        """Every installed genre file, then those in ``<lab>/genres``."""
        return tuple(
            GenreFile(origin=origin, data=data, error=error)
            for origin, data, error in _read(GENRE_GROUP, "genres", self._packages, lab)
        )


class KindFiles:
    """Finds kind files, the same way."""

    def __init__(self, packages: Sequence[str] | None = None) -> None:
        self._packages = packages

    def files(self, lab: str | None = None) -> tuple[KindFile, ...]:
        """Every installed kind file, then those in ``<lab>/kinds``."""
        return tuple(
            KindFile(origin=origin, data=data, error=error)
            for origin, data, error in _read(KIND_GROUP, "kinds", self._packages, lab)
        )

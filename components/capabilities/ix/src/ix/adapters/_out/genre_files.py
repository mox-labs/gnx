"""Genre files: the installed ones, and those kept beside an experiment.

**Installed.** A package registers its genres at the ``ix.genre`` entry point, which ix owns.
The entry point names a package of ``*.yaml`` files, one genre per file::

    [project.entry-points."ix.genre"]
    acme = "acme_ix.genres"

ix's own four genres are registered the same way. The entry point is never called: a genre is
data, read with ``yaml.safe_load``.

**Beside an experiment.** A ``genres/`` directory next to the experiment file holds that
lab's own genres.

A file or package that cannot be read becomes a record with an ``error``; the catalogue
refuses that one and keeps the rest.
"""

from __future__ import annotations

from importlib import metadata, resources
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from ix.domain.genre import GenreRecord

if TYPE_CHECKING:
    from collections.abc import Sequence
    from importlib.resources.abc import Traversable

#: The entry-point group a package registers genres under.
ENTRY_POINT_GROUP = "ix.genre"

#: The directory beside an experiment file that holds a lab's own genres.
LAB_GENRES = "genres"


def _record(origin: str, text: str) -> GenreRecord:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        return GenreRecord(origin=origin, error=" ".join(str(error).split()))
    if not isinstance(data, dict):
        return GenreRecord(origin=origin, error="the file is not a YAML mapping")
    return GenreRecord(origin=origin, data=data)


def _yaml_files(root: Traversable) -> list[Traversable]:
    return sorted(
        (item for item in root.iterdir() if item.is_file() and item.name.endswith(".yaml")),
        key=lambda item: item.name,
    )


class GenreFiles:
    """The genre library. ``packages`` names the packages to read instead of discovering
    them through the entry point (for a test, or an embedding that pins its genres)."""

    def __init__(self, packages: Sequence[str] | None = None) -> None:
        self._packages = packages

    def _installed(self) -> list[tuple[str, str]]:
        """(label, package) for every registered genre package, in a fixed order."""
        if self._packages is not None:
            return [(package, package) for package in self._packages]
        found = metadata.entry_points(group=ENTRY_POINT_GROUP)
        return sorted((point.name, point.value) for point in found)

    def records(self, beside: str | None = None) -> Sequence[GenreRecord]:
        records: list[GenreRecord] = []
        for label, package in self._installed():
            try:
                root = resources.files(package)
                files = _yaml_files(root)
            except (ImportError, OSError, TypeError, ValueError) as error:
                records.append(
                    GenreRecord(
                        origin=f"{ENTRY_POINT_GROUP} entry point {label!r} ({package})",
                        error=f"the package could not be read: {error}",
                    )
                )
                continue
            records += [
                _record(f"{package}/{file.name}", file.read_text(encoding="utf-8"))
                for file in files
            ]
        if beside is not None:
            lab = Path(beside).parent / LAB_GENRES
            if lab.is_dir():
                records += [
                    _record(str(file), file.read_text(encoding="utf-8"))
                    for file in sorted(lab.glob("*.yaml"))
                ]
        return records

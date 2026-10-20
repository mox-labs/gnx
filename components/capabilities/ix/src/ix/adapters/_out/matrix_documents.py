"""What an author writes, read through matrix's loader: an experiment, its probe files, and
the genre and kind declarations, installed or a lab's own.

matrix reads a document (YAML, or markdown with its frontmatter as keys and its text under
``body``) and validates it against the type ix names, reporting every problem with its key
path and file. This module says where to look, by convention, and turns matrix's problems and
errors into ix's:

* **The experiment** is a YAML file, the ``experiment.yaml`` inside a directory, or standard
  input (``-``).
* **Its probes**, when it writes none inline, are the files of the ``probes`` folder beside
  it: one probe a file (:class:`~ix.domain.experiment.ProbeFile`).
* **Genres and kinds.** A package registers its own at the ``ix.genre`` or ``ix.kind`` entry
  point, which ix owns and registers its own at; the entry point names a package of
  ``*.yaml`` files, one record a file, and is never called. A declaration whose name is in
  the namespace of the distribution that installed it (``ix`` owns ``ix.*``) is *owned*. A
  lab's own are the ``genres/`` and ``kinds/`` folders beside its experiments; they own
  nothing. Each file is read alone, so one that cannot be read hides no other.

Files are data: matrix reads them with ``yaml.safe_load``, which runs no code.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from importlib import metadata, resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matrix
from pydantic import BaseModel, ConfigDict

from ix.adapters._out.matrix_errors import problems_of
from ix.domain.errors import NotFoundError
from ix.domain.experiment import PROBE_SUFFIXES, PROBES_FOLDER, Experiment, ProbeFile
from ix.domain.problem import Problem, problem
from ix.domain.schema import DeclarationFile

if TYPE_CHECKING:
    from collections.abc import Sequence
    from importlib.resources.abc import Traversable

GENRE_GROUP = "ix.genre"
KIND_GROUP = "ix.kind"

EXPERIMENT_FILE = "experiment.yaml"

#: The path that means standard input, and how an experiment read from it is named.
STDIN = "-"
STDIN_SOURCE = "<stdin>"

# --- the experiment and its probes ---------------------------------------------------------


@dataclass(frozen=True)
class ExperimentRead:
    """An experiment file as read: where it is, and the experiment or why there is none."""

    #: The file's path as messages show it, or ``<stdin>``.
    source: str
    #: The directory the experiment's relative paths resolve against.
    base: str
    experiment: Experiment | None = None
    problems: tuple[Problem, ...] = ()


def experiment_location(path: str) -> Path:
    """The file an experiment path names: the path itself, or the ``experiment.yaml`` in the
    directory it names."""
    file = Path(path)
    return file / EXPERIMENT_FILE if file.is_dir() else file


def read_experiment(path: str) -> ExperimentRead:
    """Read the experiment at ``path``: a file, the ``experiment.yaml`` in a directory, or
    standard input for ``-`` (whose relative paths then resolve against the current
    directory). Raises :class:`NotFoundError` when there is no file."""
    location: Any
    if path == STDIN:
        source, base, location = STDIN_SOURCE, str(Path.cwd()), sys.stdin
    else:
        file = experiment_location(path)
        if not file.is_file():
            raise NotFoundError(
                f"no experiment file at {file}",
                fix="pass the path of an experiment file, or of a directory that holds "
                f"{EXPERIMENT_FILE}",
                details={"path": str(file)},
            )
        source, base, location = str(file), str(file.parent), file
    try:
        experiment = matrix.load(Experiment, location)
    except matrix.ConfigError as error:
        return ExperimentRead(source=source, base=base, problems=problems_of(error))
    return ExperimentRead(source=source, base=base, experiment=experiment)


@dataclass(frozen=True)
class ProbesRead:
    """The probes folder beside an experiment as read."""

    location: str
    #: None when there is no folder, or when a file in it could not be read.
    probes: tuple[ProbeFile, ...] | None = None
    problems: tuple[Problem, ...] = ()


def read_probes(base: str) -> ProbesRead:
    """The probe files in the ``probes`` folder of the directory ``base``, in name order;
    every problem with any of them, each naming its file."""
    folder = Path(base) / PROBES_FOLDER
    if not folder.is_dir():
        return ProbesRead(location=str(folder))
    try:
        found = matrix.load_each(
            ProbeFile, folder, key="id", name_from_file=True, suffixes=PROBE_SUFFIXES
        )
    except matrix.ConfigError as error:
        return ProbesRead(location=str(folder), problems=problems_of(error))
    return ProbesRead(location=str(folder), probes=found)


# --- genre and kind declarations -----------------------------------------------------------


class _Declaration(BaseModel):
    """Any mapping: a declaration's meaning is the domain's to judge, and a genre that
    extends another is judged only once merged onto its parent."""

    model_config = ConfigDict(extra="allow")


def _owns(owner: str | None, data: dict[str, Any] | None) -> bool:
    """Whether the distribution ``owner`` owns the namespace of the name ``data`` declares;
    a package named directly (``owner`` None) is trusted as the owner."""
    name = None if data is None else data.get("name")
    if not isinstance(name, str):
        return False
    root = name.split(".", 1)[0]
    return owner is None or root == owner.lower().replace("_", "-").replace(".", "-")


def _declaration(
    location: Path | Traversable, origin: str, *, owner: str | None, owned: bool
) -> DeclarationFile:
    """One declaration file; ``owned`` when it is installed, and then only for a name in its
    owner's namespace."""
    try:
        data = matrix.load(_Declaration, location).model_dump()
    except matrix.ConfigError as error:
        return DeclarationFile(origin=origin, problems=problems_of(error))
    return DeclarationFile(origin=origin, data=data, owned=owned and _owns(owner, data))


def _yaml_files(folder: Traversable) -> list[Traversable]:
    return sorted(
        (item for item in folder.iterdir() if item.is_file() and item.name.endswith(".yaml")),
        key=lambda item: item.name,
    )


def _package(package: str, *, label: str, owner: str | None) -> list[DeclarationFile]:
    try:
        files = _yaml_files(resources.files(package))
    except (ImportError, OSError, TypeError, ValueError) as error:
        return [
            DeclarationFile(
                origin=label,
                problems=(
                    problem(
                        "",
                        kind="name_unknown",
                        message=f"the package {package} could not be read: {error}",
                        fix="install the package that provides it, or remove the entry point",
                        got=package,
                        source=label,
                    ),
                ),
            )
        ]
    return [_declaration(file, f"{package}/{file.name}", owner=owner, owned=True) for file in files]


def _read(
    group: str, *, folder: str, packages: Sequence[str] | None, lab: str | None
) -> tuple[DeclarationFile, ...]:
    """Every declaration file: the installed packages' first, then the lab's."""
    found: list[DeclarationFile] = []
    if packages is not None:
        for package in packages:
            found += _package(package, label=package, owner=None)
    else:
        points = sorted(metadata.entry_points(group=group), key=lambda point: point.name)
        for point in points:
            label = f"{group} entry point {point.name!r} ({point.value})"
            owner = point.dist.name if point.dist is not None else point.name
            found += _package(point.value, label=label, owner=owner)
    if lab is not None and (Path(lab) / folder).is_dir():
        found += [
            _declaration(file, str(file), owner=None, owned=False)
            for file in sorted((Path(lab) / folder).glob("*.yaml"))
        ]
    return tuple(found)


class GenreFiles:
    """Finds genre files. ``packages`` names the packages to read instead of discovering
    them through the entry point (for a test, or an embedding that pins its genres)."""

    def __init__(self, packages: Sequence[str] | None = None) -> None:
        self._packages = packages

    def files(self, lab: str | None = None) -> tuple[DeclarationFile, ...]:
        """Every installed genre file, then those in ``<lab>/genres``."""
        return _read(GENRE_GROUP, folder="genres", packages=self._packages, lab=lab)


class KindFiles:
    """Finds kind files, the same way."""

    def __init__(self, packages: Sequence[str] | None = None) -> None:
        self._packages = packages

    def files(self, lab: str | None = None) -> tuple[DeclarationFile, ...]:
        """Every installed kind file, then those in ``<lab>/kinds``."""
        return _read(KIND_GROUP, folder="kinds", packages=self._packages, lab=lab)

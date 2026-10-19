"""Configuration: one convention for every tool, and one file shape they share.

A tool that reads config (matrix's own CLI, or a client such as ``acme``) names itself;
matrix finds its config files and merges them, later tiers winning, before validating
anything:

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

Any file may hold a ``matrix:`` section (runtimes, agents, components, store, limits, ...)
and one section per tool (``acme:``). Schema defaults sit below tier 1; a file the command
line names (``matrix run CONFIG``) and its flags sit above tier 6. Absent files read as
empty. ``Config.sources`` lists every file consulted, so ``matrix config --sources`` shows
which one set a value, and every problem names the file that set the value it is about.

Two merge rules differ from "later wins", both declared on the field by a marker
(:class:`~matrix.domain.loading.Accumulate`, :class:`~matrix.domain.loading.BesideFile`):
``matrix.definitions`` directories **accumulate** across tiers (your agents plus the
project's), and each relative directory resolves against the file that declared it. Other
lists replace; mappings merge key by key.

The same loader reads any document a caller names a type for: :func:`load` one file,
:func:`load_each` every file in a folder.
"""

from __future__ import annotations

import os
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel

from matrix.adapters._out.documents.files import FileSource
from matrix.domain.config import Config, MatrixConfig
from matrix.domain.errors import ConfigError
from matrix.domain.loading import DOCUMENT_SUFFIXES, Loader

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

M = TypeVar("M", bound=BaseModel)

#: What the file source reads: a path, a folder or file in an installed package, or an open
#: text stream (``sys.stdin``), which is read as YAML.
Location = str | os.PathLike[str] | Traversable | IO[str]


class _Empty(BaseModel):
    """The client section of a config that only matrix reads."""


def default_loader() -> Loader:
    """A :class:`~matrix.domain.loading.Loader` over files, package folders and streams."""
    return Loader(FileSource())


def load(model: type[M], location: Location) -> M:  # noqa: UP047
    """The one document at ``location`` (a YAML file, a markdown file, or a stream of YAML)
    as a ``model``.

    A markdown file is its frontmatter, with the text after it under the key ``body``; a
    field takes it by name or alias. Raises NotFoundError when nothing is there, and
    ConfigError listing every problem (``error.problems``: key path, kind, fix, file).
    """
    return default_loader().one(model, location)


def load_each(  # noqa: UP047
    model: type[M],
    *locations: Location,
    key: str | None = "name",
    name_from_file: bool = False,
    suffixes: Collection[str] = DOCUMENT_SUFFIXES,
) -> tuple[M, ...]:
    """Every document in the folders ``locations`` (a file is a folder of one) as a
    ``model``, in location order and then file-name order.

    ``key`` is the field two documents may not share (None: none); ``name_from_file`` fills
    it from the file's stem where a document leaves it out; ``suffixes`` are the files read.
    Raises NotFoundError for a location with nothing there, and ConfigError listing every
    problem in every document.
    """
    return default_loader().each(
        model, *locations, key=key, name_from_file=name_from_file, suffixes=suffixes
    )


def _config_env_var(tool: str) -> str:
    """The variable naming an explicit config file: ``acme`` → ``ACME_CONFIG``."""
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
    for variable in dict.fromkeys(("MATRIX_CONFIG", _config_env_var(tool))):
        explicit = os.environ.get(variable)
        if explicit:
            path = Path(explicit).expanduser()
            if not path.exists():
                raise ConfigError(
                    f"${variable} points at {path}, which does not exist",
                    fix=f"create the file or unset {variable}",
                    path=variable,
                    value=str(path),
                )
            paths.append(path)
    return list(dict.fromkeys(paths))  # a tool named "matrix" must not read files twice


def load_config(  # noqa: UP047
    client_type: type[M] | None = None,
    client_key: str = "matrix",
    sources: Sequence[Location] | None = None,
    *,
    loader: Loader | None = None,
) -> Config[Any]:
    """Load, merge and validate the config for ``client_key``.

    Returns ``Config[client_type]``: the shared ``matrix`` section plus the tool's section.
    ``sources`` replaces the discovered tiers (paths, package files or streams, lowest
    priority first); ``loader`` replaces the file source. Raises ConfigError listing every
    problem in both sections, each with its key path and the file that set it.
    """
    locations: Sequence[Location] = discover_sources(client_key) if sources is None else sources
    sections: dict[str, type[BaseModel]] = {"matrix": MatrixConfig}
    if client_key != "matrix":
        sections[client_key] = client_type or _Empty
    merged = (loader or default_loader()).merge(sections, locations)
    matrix = merged.sections["matrix"]
    assert isinstance(matrix, MatrixConfig)
    client = merged.sections.get(client_key) if client_key != "matrix" else _Empty()
    return Config[Any](matrix=matrix, client=client, sources=merged.sources, origins=merged.origins)

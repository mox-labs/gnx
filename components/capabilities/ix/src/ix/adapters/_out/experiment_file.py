"""The experiment file, and the registration file beside it.

An experiment is a YAML file, or the ``experiment.yaml`` inside a directory. Its registration
is a JSON document beside it, named after it: ``experiment.yaml`` is registered in
``experiment.registration.json``. Both layouts are published: they are what an author
writes and commits.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ix.domain.errors import ConfigError, NotFoundError

if TYPE_CHECKING:
    from collections.abc import Mapping

EXPERIMENT_FILE = "experiment.yaml"


@dataclass(frozen=True)
class ExperimentFile:
    """An experiment file as read: where it is, and its data or why it has none."""

    #: The file's path, as messages show it.
    source: str
    #: The directory relative paths in the experiment resolve against.
    base: str
    #: The parsed file, or None when it is not well-formed YAML (``error`` says why).
    data: Any = None
    error: str = ""


def read_experiment(path: str) -> ExperimentFile:
    """Read the experiment at ``path``, or the ``experiment.yaml`` in the directory ``path``.

    ``yaml.safe_load``: a file is data and loads no code. Raises :class:`NotFoundError` when
    there is no file.
    """
    file = Path(path)
    if file.is_dir():
        file = file / EXPERIMENT_FILE
    if not file.is_file():
        raise NotFoundError(
            f"no experiment file at {file}",
            fix="pass the path of an experiment file, or of a directory that holds "
            f"{EXPERIMENT_FILE}",
        )
    source, base = str(file), str(file.parent)
    try:
        data = yaml.safe_load(file.read_bytes())
    except yaml.YAMLError as error:
        return ExperimentFile(source=source, base=base, error=" ".join(str(error).split()))
    return ExperimentFile(source=source, base=base, data=data)


class RegistrationFile:
    """One registration record on disk."""

    def __init__(self, location: str) -> None:
        self.location = location

    @classmethod
    def beside(cls, experiment_source: str) -> RegistrationFile:
        """The registration file of the experiment file ``experiment_source``."""
        file = Path(experiment_source)
        return cls(str(file.with_name(f"{file.stem}.registration.json")))

    def read(self) -> Mapping[str, Any] | None:
        """The stored document, or None when the experiment was never registered."""
        file = Path(self.location)
        if not file.is_file():
            return None
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ConfigError(
                f"the registration at {file} is not JSON: {error}",
                fix=f"restore {file} from version control; a registration is written by "
                "`ix register` only",
            ) from error
        if not isinstance(data, dict):
            raise ConfigError(
                f"the registration at {file} is not a JSON object",
                fix=f"restore {file} from version control",
            )
        return data

    def write(self, document: Mapping[str, Any]) -> None:
        Path(self.location).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

"""Experiment files and registration records on the filesystem.

An experiment is a YAML file, or the ``experiment.yaml`` inside a directory. Its registration
is a JSON document beside it, named after it: ``experiment.yaml`` is registered in
``experiment.registration.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ix.domain.errors import ConfigError, NotFoundError
from ix.domain.ports import ExperimentDocument

if TYPE_CHECKING:
    from collections.abc import Mapping

EXPERIMENT_FILE = "experiment.yaml"


class YamlExperimentFiles:
    """Reads an experiment from YAML. ``yaml.safe_load``: a file is data and loads no code."""

    def read(self, path: str) -> ExperimentDocument:
        file = Path(path)
        if file.is_dir():
            file = file / EXPERIMENT_FILE
        if not file.is_file():
            raise NotFoundError(
                f"no experiment file at {file}",
                fix="pass the path of an experiment file, or of a directory that holds "
                f"{EXPERIMENT_FILE}",
            )
        raw = file.read_bytes()
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as error:
            return ExperimentDocument(source=str(file), raw=raw, error=_one_line(error))
        return ExperimentDocument(source=str(file), raw=raw, data=data)


class FileRegistrations:
    """Keeps a registration beside its experiment file."""

    def location(self, experiment_source: str) -> str:
        file = Path(experiment_source)
        return str(file.with_name(f"{file.stem}.registration.json"))

    def read(self, experiment_source: str) -> Mapping[str, Any] | None:
        file = Path(self.location(experiment_source))
        if not file.is_file():
            return None
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ConfigError(
                f"the registration at {file} is not JSON: {error}",
                fix=f"restore {file} from version control; a registration is written once "
                "by `ix register` and not edited",
            ) from error
        if not isinstance(data, dict):
            raise ConfigError(
                f"the registration at {file} is not a JSON object",
                fix=f"restore {file} from version control",
            )
        return data

    def write(self, experiment_source: str, document: Mapping[str, Any]) -> str:
        file = Path(self.location(experiment_source))
        file.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        return str(file)


def _one_line(error: Exception) -> str:
    return " ".join(str(error).split())

"""The registration file beside an experiment: a JSON document ix writes and reads back.

``experiment.yaml`` is registered in ``experiment.registration.json``. The layout is
published: it is what an author commits beside the experiment. A registration is written by
``ix register`` only; reading one checks only that it is a JSON object, and the use case
judges what it holds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ix.domain.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping


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
                details={"path": str(file)},
            ) from error
        if not isinstance(data, dict):
            raise ConfigError(
                f"the registration at {file} is not a JSON object",
                fix=f"restore {file} from version control",
                details={"path": str(file)},
            )
        return data

    def write(self, document: Mapping[str, Any]) -> None:
        Path(self.location).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

"""The registration file beside an experiment: a JSON document ix writes and reads back.

``experiment.yaml`` is registered in ``experiment.registration.json``. The layout is
published: it is what an author commits beside the experiment. A registration is written by
``ix register`` only; reading one checks only that it is a JSON object, and the use case
judges what it holds.
"""

from __future__ import annotations

import json
import os
import secrets
from contextlib import suppress
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
        """Write ``document`` whole or not at all: to a file beside the record, flushed to
        disk, then moved over it, so a write cut short leaves the old record as it was."""
        file = Path(self.location)
        text = json.dumps(document, indent=2) + "\n"
        temporary = file.with_name(f".{file.name}.{secrets.token_hex(8)}.tmp")
        # Made as a plain write makes a file (0o666 less the umask, which the system applies):
        # a registration is committed and shared, and the umask is never changed to learn it.
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, file)
        except BaseException:
            with suppress(OSError):
                os.unlink(temporary)
            raise

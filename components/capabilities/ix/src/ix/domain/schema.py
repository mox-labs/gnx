"""Schema ids of the experiment model's documents, the record base, and the digest.

Every document ix reads or prints names its shape in a ``schema`` field. The experiment
file, the plan and the validation are ``v2``: the earlier harness still prints
``ix.v1.plan``, ``ix.v1.validation`` and ``ix.v1.experiment`` with other shapes, and one id
never names two shapes. Documents the earlier harness has no counterpart for start at ``v1``.

Genres are named by type URL in the same dotted grammar as sensors: an experiment names a
built-in by its short form (``genre: compare``) and a third party's in full
(``genre: acme.v1.genre.soak``).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

NAMESPACE = "ix"

EXPERIMENT: Final = "ix.v2.experiment"
VALIDATION: Final = "ix.v2.validation"
PLAN: Final = "ix.v2.plan"
GENRE: Final = "ix.v1.genre"
GENRES: Final = "ix.v1.genres"
DESIGN: Final = "ix.v1.design"
REGISTRATION: Final = "ix.v1.registration"
REGISTERED: Final = "ix.v1.registered"
DEVIATIONS: Final = "ix.v1.deviations"

GENRE_PREFIX = f"{NAMESPACE}.v1.genre."

#: A genre's full name: ``<namespace>.v<N>.genre.<name>``.
GENRE_NAME_PATTERN = r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)*\.v[0-9]+\.genre\.[a-z][a-z0-9-]*$"


def genre(name: str) -> str:
    """``compare`` → ``ix.v1.genre.compare``; a full type URL passes through."""
    return name if "." in name else f"{GENRE_PREFIX}{name}"


def short(url: str) -> str:
    """A built-in genre's short name for display; a third party's type URL as it stands."""
    return url.removeprefix(GENRE_PREFIX)


class Record(BaseModel):
    """Base of every model in the experiment model: frozen, and unknown keys are refused.

    A record that is a document carries ``schema_``, read and written as ``schema``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    def document(self) -> dict[str, Any]:
        """The record as plain JSON data, keyed as a file or ``--json`` shows it."""
        data: dict[str, Any] = self.model_dump(mode="json", by_alias=True)
        return data


def digest(value: Any) -> str:
    """sha256 (hex) of ``value`` as canonical JSON: sorted keys, no insignificant space."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def digest_bytes(raw: bytes) -> str:
    """sha256 (hex) of a file's bytes."""
    return hashlib.sha256(raw).hexdigest()

"""Schema ids, the two record bases, type URLs and the digest.

Every document ix reads or prints names its shape in a ``schema`` field.

**Authored and stored.** A file a person or an agent writes (an experiment, a genre, a kind)
is a :class:`Record`: an unknown key is refused, so a typo is caught. A document ix wrote and
reads back later (a registration) is :class:`Stored`: an unknown key is ignored, so a record
written by a newer ix still loads, and a field an older record lacks reads as its default.

**Type URLs.** Genres and kinds are named ``<namespace>.v<N>.<family>.<name>``.
A file names a built-in by its short form (``genre: compare``, ``kind: process``) and a third
party's in full (``genre: acme.v1.genre.soak``). ix checks only that family structure; which
segments are legal is the one type-URL grammar of the registry, given to ix as a
:data:`Grammar`.

**Digests are over meaning.** :func:`canonical` dumps a record without the fields that still
hold their defaults; :mod:`ix.domain.plan` says what that makes a digest stable against.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ix.domain.problem import Problem

NAMESPACE = "ix"

EXPERIMENT: Final = "ix.v1.experiment"
GENRE: Final = "ix.v1.genre"
KIND: Final = "ix.v1.kind"
VALIDATION: Final = "ix.v1.validation"
DESIGN: Final = "ix.v1.design"
PLAN: Final = "ix.v1.plan"
GENRES: Final = "ix.v1.genres"
KINDS: Final = "ix.v1.kinds"
REGISTRATION: Final = "ix.v1.registration"
REGISTERED: Final = "ix.v1.registered"
DEVIATIONS: Final = "ix.v1.deviations"
ERROR: Final = "ix.v1.error"
REPORT: Final = "ix.v1.report"
RUNS: Final = "ix.v1.runs"

#: The kinds of the rows a run records, one table each. ``RUN`` and ``TRIAL`` are computed
#: before the run starts and supplied to it; the others are provided by components.
RUN: Final = "ix.v1.run"
TRIAL: Final = "ix.v1.trial"
BINDINGS: Final = "ix.v1.bindings"
INTERACTION: Final = "ix.v1.interaction"
READING: Final = "ix.v1.reading"

FAMILIES: Final = ("genre", "subject", "probe", "sensor", "environment")

#: Why a name is not a type URL, as a sentence; None when it is one.
type Grammar = Callable[[str], str | None]
#: How to name a genre or a kind, in words; the grammar's sentence names the segment it
#: refuses.
NAMING = "<namespace>.v<N>.<family>.<name>, each segment in lowercase kebab-case"

_URL = re.compile(
    rf"^(?P<namespace>[^.]+(?:\.[^.]+)*)\.v(?P<major>[0-9]+)"
    rf"\.(?P<family>{'|'.join(FAMILIES)})\.(?P<name>[^.]+)$"
)


def url(family: str, name: str) -> str:
    """``("genre", "compare")`` → ``ix.v1.genre.compare``; a full type URL passes through."""
    return name if "." in name else f"{NAMESPACE}.v1.{family}.{name}"


def short(type_url: str) -> str:
    """A built-in's short name; a third party's type URL as it stands."""
    match = _URL.match(type_url)
    if match and match.group("namespace") == NAMESPACE and match.group("major") == "1":
        return match.group("name")
    return type_url


def family_of(type_url: str) -> str | None:
    """The family a type URL names, or None when it is not a type URL."""
    match = _URL.match(type_url)
    return match.group("family") if match else None


@dataclass(frozen=True)
class DeclarationFile:
    """One genre or kind file as read: where it is, and its data or the problems that say
    why it could not be read. ``owned`` is true when an installed package declares a name in
    its own namespace; that declaration keeps its name when another file declares it too."""

    origin: str
    data: Mapping[str, Any] | None = None
    problems: tuple[Problem, ...] = ()
    owned: bool = False


class Record(BaseModel):
    """Base of every authored or computed record: frozen, and unknown keys are refused.

    A record that is a document carries ``schema_``, read and written as ``schema``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    def document(self) -> dict[str, Any]:
        """The record as plain JSON data, keyed as a file or ``--json`` shows it."""
        data: dict[str, Any] = self.model_dump(mode="json", by_alias=True)
        return data


class Stored(BaseModel):
    """Base of a document ix writes and reads back: unknown keys are ignored on load."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    def document(self) -> dict[str, Any]:
        data: dict[str, Any] = self.model_dump(mode="json", by_alias=True)
        return data


def canonical(record: BaseModel, *, exclude: set[str] | None = None) -> dict[str, Any]:
    """A record's meaning: its fields that differ from their defaults, as JSON data."""
    data: dict[str, Any] = record.model_dump(
        mode="json", by_alias=True, exclude_defaults=True, exclude=exclude
    )
    return data


def digest(value: Any) -> str:
    """sha256 (hex) of ``value`` as canonical JSON: sorted keys, no insignificant space."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

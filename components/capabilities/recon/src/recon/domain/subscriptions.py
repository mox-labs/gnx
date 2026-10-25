"""Subscriptions: who is told what changed in a mission's outputs (round-4 rulings R2-R4).

A mission declares each in its config, ``subscriptions:``, a list as ``catalog:`` and
``collectors:`` are (:class:`SubscriptionSpec`): its ``name``; the ``output`` whose change rows
it reads (an output whose entry has ``changes:``); and ``match:``, which of them. A
subscriber reads them itself (``recon subscribe``). What a subscription has read and acked is
kept beside the mission (:class:`SubscriptionStore`), never in the config.

**match:** maps a field of a change row to a glob, or a list of globs any of which it may
match, the shape of a ``capture`` collector's ``match:``: ``op``, ``key.<field>`` (a field of
``changes.key``) or ``record.<field>``. A field absent or null matches no glob (a delete's
``record`` is null). No SQL: a filter reads one row, never another table.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from recon.domain.missions import MISSION_NAME

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

#: A field of a change row that ``match:`` may name as it is; the others are named in it.
ROW_FIELDS = ("op",)

#: The parts of a change row whose fields ``match:`` names by ``<part>.<field>``.
ROW_PARTS = ("key", "record")


class SubscriptionSpec(BaseModel):
    """One of a mission's ``subscriptions:``. ``name`` names it among the mission's, and is
    what ``recon subscribe`` takes; ``output`` is the output whose change rows it reads;
    ``match:`` chooses which (every row by default)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    output: str
    match: dict[str, str | list[str]] = {}

    @field_validator("name")
    @classmethod
    def _a_name(cls, value: str) -> str:
        if not MISSION_NAME.match(value):
            raise ValueError(
                "a subscription's name is letters, digits, ., _ and -, starting with a letter "
                "or a digit"
            )
        return value

    @model_validator(mode="after")
    def _globs(self) -> SubscriptionSpec:
        for field, globs in self.match.items():
            if isinstance(globs, list) and not globs:
                raise ValueError(f"match.{field} is an empty list, which no row matches")
        return self


def match_field(field: str) -> tuple[str, str | None]:
    """A ``match:`` field as the part of a change row it reads and the field in it: ``op`` is
    ``("op", None)``, ``record.subject`` is ``("record", "subject")``; one of neither shape is
    ``("", None)``."""
    if field in ROW_FIELDS:
        return field, None
    part, _, name = field.partition(".")
    if part in ROW_PARTS and name:
        return part, name
    return "", None


def matches(match: Mapping[str, str | list[str]], row: Mapping[str, Any]) -> bool:
    """Whether the change row ``row`` (``{op, key, record}``) matches every field of ``match``:
    its value, as text, matches the glob, or one of the list's. A field absent or null matches
    none."""
    for field, globs in match.items():
        part, name = match_field(field)
        value = row.get(part)
        if name is not None:
            value = value.get(name) if isinstance(value, dict) else None
        if value is None:
            return False
        text = value if isinstance(value, str) else str(value)
        either = [globs] if isinstance(globs, str) else globs
        if not any(fnmatchcase(text, glob) for glob in either):
            return False
    return True


class SubscriptionState(BaseModel):
    """What is kept of a subscription beside its mission: the output it reads, the cursor it
    acked last (None: nothing yet) and when, and the row a command failed last, by its ``id``,
    with how many times and why, until it is given again or set aside."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    output: str
    cursor: str | None = None
    acked_at: str | None = None
    failing: Failing | None = None


class Failing(BaseModel):
    """A row a command failed: its ``id``, how many times (``attempts``), and the last error."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    attempts: int
    error: str


SubscriptionState.model_rebuild()


class SubscriptionStore(Protocol):
    """Where what is kept of a mission's subscriptions is: each one's state, its dead letters,
    and the lock its one consumer holds."""

    def hold(self, mission_dir: Path, name: str) -> None:
        """Hold the subscription ``name`` for this consumer until :meth:`release` or its death:
        no other, in this process or another on this host, holds it meanwhile. Raises
        ReconError (``busy``), naming the holder's pid in ``details`` when it is known."""
        ...

    def release(self, mission_dir: Path, name: str) -> None:
        """Let go of a subscription :meth:`hold` held; one not held is left as it is."""
        ...

    def state(self, mission_dir: Path, name: str) -> SubscriptionState | None:
        """What is kept of ``name``; None when nothing is."""
        ...

    def keep(self, mission_dir: Path, name: str, state: SubscriptionState) -> None:
        """Keep ``state`` as ``name``'s, whole or not at all. Raises OSError when it cannot."""
        ...

    def dead_letter(self, mission_dir: Path, name: str, letter: Mapping[str, Any]) -> None:
        """Keep ``letter``, a row set aside, as one line of ``name``'s dead letters: bounded,
        the oldest let go first. Raises OSError when it cannot."""
        ...

    def dead_letters(self, mission_dir: Path, name: str) -> int:
        """How many dead letters ``name`` keeps."""
        ...

    def names(self, mission_dir: Path) -> list[str]:
        """The subscriptions something is kept of, sorted."""
        ...

    def remove(self, mission_dir: Path, name: str) -> bool:
        """Remove everything kept of ``name``: its state and dead letters. False when there was
        nothing."""
        ...

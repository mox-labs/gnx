"""Subscriptions: who is told what changed in a mission's outputs (round-4 rulings R2-R4).

A mission declares each in its config, ``subscriptions:``, a list as ``catalog:`` and
``collectors:`` are (:class:`SubscriptionSpec`): its ``name``; the ``output`` whose change rows
it reads (an output whose entry has ``changes:``); ``match:``, which of them; and ``deliver:``,
the command each is given to (:class:`DeliverSpec`), or none, for a subscriber that reads them
itself (``recon subscribe``). What a subscription has read and acked is kept beside the mission
(:class:`SubscriptionStore`), never in the config.

**match:** maps a field of a change row to a glob, or a list of globs any of which it may
match, the shape of a ``capture`` collector's ``match:``: ``op``, ``key.<field>`` (a field of
``changes.key``) or ``record.<field>``. A field absent or null matches no glob (a delete's
``record`` is null). No SQL: a filter reads one row, never another table.

**What a command is given** is one ``recon.v1.delivery`` document, as JSON on its standard
input; its argument list is the config's, fixed, so no text of a row reaches it but there:
never a shell, an AppleScript or a URL. Every string of a row in it is :func:`clean`, then cut
to ``max_chars``: a terminal's escapes, bidirectional controls and Unicode tag characters
stripped. The content stays untrusted all the same, and its schema says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from recon.domain.missions import MISSION_NAME

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

#: A field of a change row that ``match:`` may name as it is; the others are named in it.
ROW_FIELDS = ("op",)

#: The parts of a change row whose fields ``match:`` names by ``<part>.<field>``.
ROW_PARTS = ("key", "record")

#: By default, the most rows one delivery gives a command one at a time; the rest are given as
#: one row that counts them.
MAX_PER_TICK = 10

#: By default, how many times a row is given to a command that fails it before it is set aside.
ATTEMPTS = 3

#: By default, the seconds one delivery of a subscription may take, every command it runs
#: included; and the seconds one run of the command may take.
DELIVER_DEADLINE_S = 30.0
DELIVER_TIMEOUT_S = 10.0

#: By default, the most characters a string of a row keeps in a delivery: a mail's subject
#: whole, a body's opening lines.
MAX_CHARS = 200


class DeliverSpec(BaseModel):
    """``deliver:`` on a subscription: give each row to ``command``.

    ``command`` is the program run for each row and ``args`` its arguments (none by default),
    the shape an ``mcp`` source's stdio server has: run as given, by no shell, the row on its
    standard input and in no argument. ``fields``: the record's fields
    a delivery carries besides the row's op, output and key (none by default: no body).
    ``max_per_tick``: the most rows one delivery gives one at a time; the rest it gives as one
    row counting them. ``attempts``: how many times a row is given to a command that fails it,
    one a delivery, before it is set aside as a dead letter, so it never holds the rows after
    it. ``deadline``: the seconds one delivery may take; ``timeout``: the seconds one run of the
    command may take. ``max_chars``: the most characters each string of a row keeps."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: str = Field(min_length=1)
    args: list[str] = []
    fields: list[str] = []
    max_per_tick: int = Field(default=MAX_PER_TICK, ge=1)
    attempts: int = Field(default=ATTEMPTS, ge=1)
    deadline: float = Field(default=DELIVER_DEADLINE_S, gt=0, allow_inf_nan=False)
    timeout: float = Field(default=DELIVER_TIMEOUT_S, gt=0, allow_inf_nan=False)
    max_chars: int = Field(default=MAX_CHARS, ge=1)

    @field_validator("args")
    @classmethod
    def _arguments(cls, value: list[str]) -> list[str]:
        if any("\0" in argument for argument in value):
            raise ValueError("an argument holds a NUL character, which no program is given")
        return value

    @field_validator("command")
    @classmethod
    def _a_program(cls, value: str) -> str:
        if "\0" in value:
            raise ValueError("the command holds a NUL character, which names no program")
        return value

    @property
    def argv(self) -> list[str]:
        """The argument list each run is: ``command``, then ``args``."""
        return [self.command, *self.args]

    @field_validator("fields")
    @classmethod
    def _record_fields(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("fields names a field twice")
        if own := [field for field in value if field.startswith("_")]:
            raise ValueError(f"fields names {', '.join(own)}: a field starting _ is recon's own")
        return value


class SubscriptionSpec(BaseModel):
    """One of a mission's ``subscriptions:``. ``name`` names it among the mission's, and is
    what ``recon subscribe`` takes; ``output`` is the output whose change rows it reads;
    ``match:`` chooses which (every row by default); ``deliver:`` gives each to a command (none
    by default: the subscriber reads them itself)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    output: str
    match: dict[str, str | list[str]] = {}
    deliver: DeliverSpec | None = None

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


# A terminal's escape sequences: OSC (ESC ], to BEL or ST), DCS, SOS, PM, APC (ESC P X ^ _, to
# ST), CSI (ESC [, to its final byte), any other ESC and its byte, and the same as C1 bytes.
_ESCAPES = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"
    r"|\x1b[PX^_][^\x1b]*(?:\x1b\\)?"
    r"|\x1b\[[0-?]*[ -/]*[@-~]?"
    r"|\x1b[ -~]?"
    r"|\x9d[^\x07\x9c\x1b]*(?:\x07|\x9c)?"
    r"|[\x90\x98\x9e\x9f][^\x9c\x1b]*\x9c?"
    r"|\x9b[0-?]*[ -/]*[@-~]?"
)

# What is left of control characters (but tab and line feed), bidirectional controls (marks,
# embeddings, overrides, isolates), Unicode tag characters (which can spell text no reader
# sees), and lone surrogates (no character at all).
_STRIPPED = re.compile(
    r"[\x00-\x08\x0b-\x1f\x7f-\x9f"
    r"\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069"
    r"\U000e0000-\U000e007f"
    r"\ud800-\udfff]"
)


def clean(text: str, max_chars: int) -> str:
    """``text`` with every escape sequence of a terminal, every control character but tab and
    line feed, every bidirectional control, every Unicode tag character and every lone
    surrogate taken out, then cut to ``max_chars`` characters, its last ``…`` when it was
    cut."""
    kept = _STRIPPED.sub("", _ESCAPES.sub("", text))
    if len(kept) <= max_chars:
        return kept
    return kept[: max_chars - 1] + "…"


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


@dataclass(frozen=True)
class Delivered:
    """How one run of a command given a row ended: ``ok`` when it exited 0; else its exit
    code (None when it did not exit in time, or could not start) and what went wrong."""

    ok: bool
    exit: int | None = None
    error: str = ""


class Deliverer(Protocol):
    """Gives one delivery to a command."""

    def deliver(
        self, argv: Sequence[str], payload: bytes, *, timeout_s: float, cwd: Path
    ) -> Delivered:
        """Run ``argv`` as given, by no shell, in ``cwd``, with ``payload`` on its standard
        input, and wait at most ``timeout_s`` for it to exit: past that it is stopped, with
        every process it started."""
        ...

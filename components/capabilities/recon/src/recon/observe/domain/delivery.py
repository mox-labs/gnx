"""What a subscription is given, and what is kept of it beside its mission (round-4 rulings
R2-R4): which change rows a ``match:`` keeps, what of a row a command may see, the state a
subscription keeps (its acked cursor, the row failing), and the ports that keep it and give a
row to a ``deliver:`` command.

What a mission's ``subscriptions:`` may say is the config's (:class:`recon.core.SubscriptionSpec`).

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

from pydantic import BaseModel, ConfigDict

from recon.core import match_field

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


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
# sees), variation selectors, zero-width characters (spaces, joiners, the word joiner and the
# invisible operators, the byte order mark), the line and paragraph separators (Vector, review
# S3), and lone surrogates (no character at all).
_STRIPPED = re.compile(
    r"[\x00-\x08\x0b-\x1f\x7f-\x9f"
    r"\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069"
    r"\U000e0000-\U000e007f"
    r"\ufe00-\ufe0f\U000e0100-\U000e01ef"
    r"\u200b-\u200d\u2060-\u2064\ufeff"
    r"\u2028\u2029"
    r"\ud800-\udfff]"
)


def clean(text: str, max_chars: int) -> str:
    """``text`` with every escape sequence of a terminal, every control character but tab and
    line feed, every bidirectional control, every Unicode tag character, variation selector and
    zero-width character, the line and paragraph separators and every lone surrogate taken
    out, then cut to ``max_chars`` characters, its last ``…`` when it was
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

    def hold(self, mission: str, name: str) -> None:
        """Hold the subscription ``name`` for this consumer until :meth:`release` or its death:
        no other, in this process or another on this host, holds it meanwhile. Raises
        ReconError (``busy``), naming the holder's pid in ``details`` when it is known."""
        ...

    def release(self, mission: str, name: str) -> None:
        """Let go of a subscription :meth:`hold` held; one not held is left as it is."""
        ...

    def state(self, mission: str, name: str) -> SubscriptionState | None:
        """What is kept of ``name``; None when nothing is."""
        ...

    def keep(self, mission: str, name: str, state: SubscriptionState) -> None:
        """Keep ``state`` as ``name``'s, whole or not at all. Raises OSError when it cannot."""
        ...

    def dead_letter(self, mission: str, name: str, letter: Mapping[str, Any]) -> None:
        """Keep ``letter``, a row set aside, as one line of ``name``'s dead letters: bounded,
        the oldest let go first. Raises OSError when it cannot."""
        ...

    def dead_letters(self, mission: str, name: str) -> int:
        """How many dead letters ``name`` keeps."""
        ...

    def names(self, mission: str) -> list[str]:
        """The subscriptions something is kept of, sorted."""
        ...

    def remove(self, mission: str, name: str) -> bool:
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

"""The book: one organization as data, ready for a page to show.

A book holds what the six documents say and what follows from reading them together: who
holds each position, whose work each position reads, and where each chain of checking ends.
It carries Markdown as written and no HTML, so any front end can render it: the `<dao-book>`
element dao ships, a project's own docsite, or a panel in an editor.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from dao.domain.models import Participant
from dao.domain.spec import Key, Status


class BookDocument(BaseModel):
    """One of the six documents, with the verdict `dao check` gave it."""

    model_config = ConfigDict(frozen=True)

    key: Key
    name: str
    file: str
    summary: str
    status: Status
    gaps: tuple[str, ...] = ()
    # The document as written, without its own title line. Empty when the file is absent.
    markdown: str = ""


class BookPosition(BaseModel):
    """A position, with the participants who hold it and the positions whose work it reads."""

    model_config = ConfigDict(frozen=True)

    name: str
    does: str = ""
    must_not: str = ""
    checked_by: tuple[str, ...] = ()
    held_by: tuple[str, ...] = ()
    # True when someone holds the position and every holder is a person: such a position may
    # end a chain of checking, because a person's accountability is where it stops.
    held_only_by_people: bool = False
    checks: tuple[str, ...] = ()


class Book(BaseModel):
    """An organization, in the order its documents are specified."""

    model_config = ConfigDict(frozen=True)

    # The shape of this data. A reader built for one format refuses another; it is raised
    # when a field is removed or changes meaning, not when one is added.
    format: int = 1
    title: str
    directory: str
    # Whether `dao check` found all six documents PRESENT when the book was made.
    complete: bool
    # The digest of the six documents this book was made from.
    sources: str
    documents: tuple[BookDocument, ...]
    positions: tuple[BookPosition, ...] = ()
    participants: tuple[Participant, ...] = ()
    # Each chain starts at a position and follows "checked by" until nobody checks the last.
    chains: tuple[tuple[str, ...], ...] = ()

    def document(self, key: Key | str) -> BookDocument:
        return next(d for d in self.documents if d.key == key)

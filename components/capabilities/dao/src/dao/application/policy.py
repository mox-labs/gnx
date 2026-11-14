"""The declared policy: every threshold and file name dao is governed by.

M2's ruling: every governed surface is a declared policy contract, with no hardcoded
constants. These values are named so they can be cited and amended rather than discovered by
reading code. `dao policy` prints them.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Policy(BaseModel):
    """The thresholds and names. Frozen: a run is judged by one declared set."""

    model_config = ConfigDict(frozen=True)

    # Where the organization's documents live, relative to the project root.
    directory: str = "dao"
    # The files an agent reads at the start of a session. One of them must name the directory.
    agent_instructions: tuple[str, ...] = ("CLAUDE.md", "AGENTS.md")
    # The three files `dao book` writes into the directory, and `dao check` holds to the
    # documents: the page a person opens, the book as data, and the element that draws it.
    book: str = "book.html"
    book_data: str = "book.json"
    book_element: str = "dao-book.js"
    # The largest file dao reads from a project. A document is about a page; this is far
    # above any real one, and is what stops a file that is not a document from being read.
    document_max_bytes: int = 1_048_576
    # M1: a position's remit must be prose. Below this it is closer to a label, which
    # measured about 0.5 worse than either richer condition.
    position_remit_min_chars: int = 240
    # One position cannot have its work checked by another.
    positions_min: int = 2
    principles_min: int = 3
    # A section with a heading and nothing under it is not a section.
    section_min_chars: int = 40


POLICY = Policy()

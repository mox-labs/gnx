"""Make a book from a project's documents, and hold a book on disk to them.

The book is a copy for reading, and the documents stay the source. So `build` adds nothing
the documents do not say: it reads them, asks the audit for its verdicts, and works out the
three things a reader would otherwise have to cross-reference by hand (who holds a position,
whose work it reads, and how far each chain of checking runs).

`check` is the audit plus one question about each of a book's files that is on disk: is it,
byte for byte, the file dao would write from these documents now? A digest in a file could be
copied onto anything, so the answer comes from making the files again and comparing.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from dao.application.audit import audit, complete
from dao.application.organization import Organization, made_from
from dao.application.policy import POLICY
from dao.application.reading import heading
from dao.domain.book import Book, BookDocument, BookPosition
from dao.domain.models import Kind
from dao.domain.spec import Coverage, Key

if TYPE_CHECKING:
    from pathlib import Path

    from dao.domain.models import Position

# What turns a book into its files, by name. The application does not know how; an adapter does.
Render = Callable[[Book], Mapping[str, str]]

UNTITLED = "How this project is run"
_H1 = re.compile(r"^#[ \t]++(\S.*)$")
# A file of the book holds at most the six documents and some framing. Past this, or past
# the size of the file dao would write, it is not one dao wrote.
_BOOK_MAX_DOCUMENTS = 8


def _split_title(text: str) -> tuple[str, str]:
    """A document's own title line, and the rest. ("", text) when it does not open with one."""
    stripped = text.lstrip()
    first, _, rest = stripped.partition("\n")
    title = heading(first.rstrip(), _H1)
    if title is None:
        return "", text.strip()
    return title, rest.strip()


def chains(positions: list[Position]) -> tuple[tuple[str, ...], ...]:
    """The chains of checking: from each position, follow its checker until there is none.

    A chain starts at a position nobody else is checked by. When it reaches a position an
    earlier chain already passed through, it names that position and stops, so every
    position is walked once however the chains join. A loop no chain enters is walked once,
    from its first position.
    """
    names = list(dict.fromkeys(p.name for p in positions))
    known = set(names)
    checker: dict[str, str | None] = {}
    for p in positions:
        checker.setdefault(
            p.name, next((c for c in p.checked_by if c in known and c != p.name), None)
        )
    pointed_at = {c for c in checker.values() if c is not None}

    seen: set[str] = set()
    out: list[tuple[str, ...]] = []

    def walk(start: str) -> None:
        chain = [start]
        mine = {start}
        seen.add(start)
        while (nxt := checker[chain[-1]]) is not None:
            if nxt in seen:
                if nxt not in mine:
                    chain.append(nxt)
                break
            chain.append(nxt)
            mine.add(nxt)
            seen.add(nxt)
        out.append(tuple(chain))

    for name in names:
        if name not in pointed_at:
            walk(name)
    for name in names:
        if name not in seen:
            walk(name)
    return tuple(out)


def _assemble(org: Organization, coverage: list[Coverage]) -> Book:
    title = ""
    documents: list[BookDocument] = []
    for c in coverage:
        own_title, body = _split_title(org.text(c.element.key))
        if c.element.key is Key.FRONT_DOOR:
            title = own_title
        documents.append(
            BookDocument(
                key=c.element.key,
                name=c.element.name,
                file=c.element.file,
                summary=c.element.summary,
                status=c.status,
                gaps=tuple(c.gaps),
                markdown=body,
            )
        )

    checked: dict[str, list[str]] = {}
    for p in org.positions:
        for name in dict.fromkeys(p.checked_by):
            if name != p.name:
                checked.setdefault(name, []).append(p.name)

    positions = []
    for p in org.positions:
        holders = org.holders(p.name)
        positions.append(
            BookPosition(
                name=p.name,
                does=p.does,
                must_not=p.must_not,
                checked_by=p.checked_by,
                held_by=tuple(h.name for h in holders),
                held_only_by_people=bool(holders) and all(h.kind is Kind.HUMAN for h in holders),
                checks=tuple(checked.get(p.name, ())),
            )
        )
    return Book(
        title=title or UNTITLED,
        directory=org.directory,
        complete=complete(coverage),
        sources=org.sources(),
        documents=tuple(documents),
        positions=tuple(positions),
        participants=tuple(org.participants or ()),
        chains=chains(org.positions),
    )


def build(root: Path, directory: str | None = None) -> Book:
    """The book of the organization under `root`. Reads files; writes nothing."""
    return _assemble(Organization(root, directory), audit(root, directory))


def check(root: Path, directory: str | None = None, *, render: Render) -> list[Coverage]:
    """The audit, with a book on disk held to the documents under the front door.

    A project need not have a book, or all of its files. Each one it has must be exactly the
    file `render` gives for the documents as they are now; otherwise it is a gap under the
    front door. That holds when the front door has gaps of its own: a book's files are a
    page and a script somebody will open, whatever else is missing.
    """
    coverage = audit(root, directory)
    org = Organization(root, directory)
    front = next(c for c in coverage if c.element.key is Key.FRONT_DOOR)
    names = (POLICY.book, POLICY.book_data, POLICY.book_element)
    on_disk = [name for name in names if os.path.lexists(org.book_file(name))]
    if not on_disk:
        return coverage

    made = render(_assemble(org, coverage))
    current: list[str] = []
    for name in on_disk:
        where = f"{org.directory}/{name}"
        expected = made[name].encode("utf-8")
        limit = max(len(expected), _BOOK_MAX_DOCUMENTS * POLICY.document_max_bytes)
        content, refused = org.read_book_file(name, limit)
        if content == expected:
            current.append(name)
        elif content is None:
            front.gaps.append(f"{where} {refused}, so it is not read")
        elif made_from(content.decode("utf-8", "replace")) not in (None, org.sources()):
            front.gaps.append(f"{where} was made from older documents: run `dao book`")
        else:
            front.gaps.append(
                f"{where} is not the file `dao book` writes from these documents (it was "
                "edited by hand, or made by another version of dao): run `dao book`"
            )
    if current:
        front.evidence.append(f"{', '.join(current)}: as `dao book` writes")
    return coverage

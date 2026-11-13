"""Make a book from a project's documents, and hold a book on disk to them.

The book is a copy for reading, and the documents stay the source. So `build` adds nothing
the documents do not say: it reads them, asks the audit for its verdicts, and works out the
three things a reader would otherwise have to cross-reference by hand (who holds a position,
whose work it reads, and how far each chain of checking runs).

`check` is the audit plus one question about a book that is on disk: is it, byte for byte,
the page dao would write from these documents now? A digest in the page could be copied onto
any HTML, so the answer comes from rendering again and comparing.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from dao.application.audit import audit, complete
from dao.application.organization import Organization, rendered_from
from dao.application.policy import POLICY
from dao.application.reading import heading
from dao.domain.book import Book, BookDocument, BookPosition
from dao.domain.models import Kind
from dao.domain.spec import Coverage, Key

if TYPE_CHECKING:
    from pathlib import Path

    from dao.domain.models import Position

# What turns a book into a page. The application does not know how; an adapter does.
Render = Callable[[Book], str]

UNTITLED = "How this project is run"
_H1 = re.compile(r"^#[ \t]++(\S.*)$")
# A page holds the six documents and the template. Past this it is not a page dao wrote.
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

    A project need not have a book. When it has one, the book must be exactly the page
    `render` gives for the documents as they are now; otherwise the front door is PARTIAL.
    """
    coverage = audit(root, directory)
    org = Organization(root, directory)
    front = next(c for c in coverage if c.element.key is Key.FRONT_DOOR)
    if not front.present or not os.path.lexists(org.book):
        return coverage

    where = f"{org.directory}/{POLICY.book}"
    expected = render(_assemble(org, coverage)).encode("utf-8")
    limit = max(len(expected), _BOOK_MAX_DOCUMENTS * POLICY.document_max_bytes)
    content, refused = org.read_book(limit)
    if content == expected:
        front.evidence.append(f"{POLICY.book} is the page `dao book` writes")
    elif content is None:
        front.gaps.append(f"{where} {refused}, so it is not read")
    elif rendered_from(content.decode("utf-8", "replace")) not in (None, org.sources()):
        front.gaps.append(f"{where} was rendered from older documents: run `dao book`")
    else:
        front.gaps.append(
            f"{where} is not the page `dao book` writes from these documents (it was edited "
            "by hand, or made by another version of dao): run `dao book`"
        )
    return coverage

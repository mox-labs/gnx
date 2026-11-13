"""A project's dao documents, read once and shared by the audit and the book.

Every file dao reads from a project comes through here, by one rule: a regular file, inside
the project, no larger than the declared limit, and never through a symbolic link in the
documents' directory. A repository is somebody else's input. A document that is a link to
`~/.ssh/config` would otherwise be copied into a page meant for sharing, and one that is a
link to `/dev/zero` would be read until memory ran out.

Also here: the digest that ties a rendered book to the documents it was rendered from.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from functools import cached_property
from typing import TYPE_CHECKING

from dao.application.policy import POLICY
from dao.application.reading import read_participants, read_positions
from dao.domain.spec import SPEC, Key

if TYPE_CHECKING:
    from pathlib import Path

    from dao.domain.models import Participant, Position

# How a book records its sources. `dao book` writes it; nothing else should.
SOURCES_META = "dao-sources"
_RECORDED = re.compile(rf'<meta name="{SOURCES_META}" content="([0-9a-f]{{64}})">')

# (content, reason). Content is None when the file is not read; the reason is empty when
# that is because there is no file, and says why otherwise.
Read = tuple[bytes | None, str]


def file_of(key: Key) -> str:
    """The file that holds an element, relative to the organization's directory."""
    return next(e.file for e in SPEC if e.key is key)


def _read_regular(path: Path, limit: int, *, follow: bool) -> Read:
    """Read a regular file of at most `limit` bytes. With `follow=False`, never through a link."""
    try:
        found = os.stat(path) if follow else os.lstat(path)
    except FileNotFoundError:
        return None, ""
    except (OSError, ValueError):
        return None, "cannot be read"
    if stat.S_ISLNK(found.st_mode):
        return None, "is a symbolic link"
    if not stat.S_ISREG(found.st_mode):
        return None, "is not a regular file"
    if found.st_size > limit:
        return None, f"is larger than {limit} bytes"
    try:
        fd = os.open(path, os.O_RDONLY | (0 if follow else os.O_NOFOLLOW))
        with os.fdopen(fd, "rb") as f:
            # One byte past the limit: a file that grew after the stat is still refused.
            content = f.read(limit + 1)
    except (OSError, ValueError):
        return None, "cannot be read"
    if len(content) > limit:
        return None, f"is larger than {limit} bytes"
    return content, ""


def _decode(content: bytes | None) -> str:
    if content is None:
        return ""
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return ""


class Organization:
    """The documents of one project. Nothing is read until it is asked for."""

    def __init__(self, root: Path, directory: str | None = None) -> None:
        self.root = root
        self.directory = directory or POLICY.directory
        self.dir = root / self.directory
        self._documents: dict[str, Read] = {}

    @cached_property
    def inside(self) -> bool:
        """Whether the documents' directory is inside the project."""
        try:
            return self.dir.resolve().is_relative_to(self.root.resolve())
        except (OSError, ValueError):
            return False

    def _document(self, name: str) -> Read:
        if name not in self._documents:
            read = _read_regular(self.dir / name, POLICY.document_max_bytes, follow=False)
            if not self.inside and (read[0] is not None or read[1]):
                read = (None, "is outside the project")
            self._documents[name] = read
        return self._documents[name]

    def text(self, key: Key) -> str:
        """A document's text; empty when it is absent, refused, or not UTF-8."""
        return _decode(self._document(file_of(key))[0])

    def has(self, key: Key) -> bool:
        return self._document(file_of(key))[0] is not None

    def refused(self, key: Key) -> str:
        """Why a document that is on disk was not read; empty when it was, or is not there."""
        return self._document(file_of(key))[1]

    def instructions(self, name: str) -> str | None:
        """An agent-instruction file at the project root, or None when there is none to read.

        `CLAUDE.md` is often a link to `AGENTS.md`, so a link is followed here, as long as
        it stays inside the project. The text is only searched, never shown.
        """
        path = self.root / name
        try:
            if not path.resolve().is_relative_to(self.root.resolve()):
                return None
        except (OSError, ValueError):
            return None
        content, _ = _read_regular(path, POLICY.document_max_bytes, follow=True)
        return None if content is None else _decode(content)

    @cached_property
    def positions(self) -> list[Position]:
        return read_positions(self.text(Key.POSITIONS))

    @cached_property
    def participants(self) -> list[Participant] | None:
        """None when `participants.md` has no participants table."""
        return read_participants(self.text(Key.PARTICIPANTS))

    def holders(self, position: str) -> list[Participant]:
        return [p for p in (self.participants or []) if position in p.positions]

    @property
    def book(self) -> Path:
        """Where this organization's book is written."""
        return self.dir / POLICY.book

    def read_book(self, limit: int) -> Read:
        """The book on disk, by the same rule as a document, up to `limit` bytes."""
        read = _read_regular(self.book, limit, follow=False)
        if not self.inside and (read[0] is not None or read[1]):
            return None, "is outside the project"
        return read

    def sources(self) -> str:
        """A digest of the six documents as dao reads them now.

        Each file contributes its name and its bytes, in the spec's order, so a renamed, an
        emptied and a missing document all change the digest. A document dao refuses to read
        counts as missing.
        """
        h = hashlib.sha256()
        for element in SPEC:
            content = self._document(element.file)[0]
            h.update(element.file.encode())
            h.update(b"\0")
            h.update(b"\0absent" if content is None else content)
            h.update(b"\0")
        return h.hexdigest()


def rendered_from(book_html: str) -> str | None:
    """The digest a book says it was rendered from, or None when it records none."""
    m = _RECORDED.search(book_html)
    return m.group(1) if m else None

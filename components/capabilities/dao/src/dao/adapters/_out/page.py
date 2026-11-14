"""The files `dao book` writes: the book as data, the page that shows it, and the element.

The page is for a person, and it is made from documents that agents and contributors edit.
dao puts none of their words into HTML. The book travels as JSON, inside a block the browser
does not run, with every `<` written as an escape so no text can close that block. The one
script the page runs is the `<dao-book>` element, a file dao ships and copies unchanged; it
turns the documents' Markdown into HTML with raw HTML off and a short list of allowed
addresses (see `web/src/markdown.ts`). The page's content security policy allows that script
and no network load.

The element is built from `web/` into the skill's `assets/`, the same directory the plugin
carries, so the package, the plugin and a project's copy are one file.
"""

from __future__ import annotations

import contextlib
import html
import os
from importlib import resources
from string import Template
from typing import TYPE_CHECKING

from dao.application.policy import POLICY

if TYPE_CHECKING:
    from pathlib import Path

    from dao.domain.book import Book

# Inside a <script> block these three could end the block or open a comment. As JSON escapes
# they mean the same characters and cannot.
_IN_SCRIPT = str.maketrans({"<": "\\u003c", ">": "\\u003e", "&": "\\u0026"})


def _asset(name: str) -> str:
    path = resources.files("dao.assets") / "skills" / "dao" / "assets" / name
    return path.read_text(encoding="utf-8")


def data(book: Book) -> str:
    """The book as JSON. The same book always gives the same bytes."""
    return book.model_dump_json(indent=2) + "\n"


def element() -> str:
    """The `<dao-book>` element, as shipped."""
    return _asset(POLICY.book_element)


def page(book: Book, *, version: str) -> str:
    """The page for `book`: the element's host, carrying the book so it opens from disk."""
    return Template(_asset(POLICY.book)).substitute(
        version=html.escape(version, quote=True),
        sources=html.escape(book.sources, quote=True),
        title=html.escape(book.title),
        data=book.model_dump_json().translate(_IN_SCRIPT),
        element=html.escape(POLICY.book_element, quote=True),
    )


def files(book: Book, *, version: str) -> dict[str, str]:
    """Every file `dao book` writes into the documents' directory, by name."""
    return {
        POLICY.book_data: data(book),
        POLICY.book: page(book, version=version),
        POLICY.book_element: element(),
    }


def write(content: str, target: Path) -> None:
    """Put `content` at `target`, replacing what is there, and never through a link.

    The content is written to a new file beside the target and moved into place. Moving
    replaces a link instead of following it, so nothing planted at `target` between the
    caller's checks and this write can send the file somewhere else.
    """
    directory = target.parent.resolve(strict=True)
    scratch = directory / f".{target.name}.{os.getpid()}.tmp"
    fd = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o666)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        os.replace(scratch, directory / target.name)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(scratch)
        raise

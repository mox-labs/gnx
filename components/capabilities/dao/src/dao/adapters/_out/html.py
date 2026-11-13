"""Render a book as one self-contained HTML page.

The page is for a person, and it is made from documents that agents and contributors edit.
So nothing a document contains is allowed to act: raw HTML in the Markdown is shown as text,
the template escapes every value, and the page carries a content security policy that
forbids scripts and network loads outright. The second and third are there for the day the
first has a hole. Links and images are held to a short list of kinds of address; one with
any other address is shown as its text.

The template is the skill's `assets/book.html.j2`, the same file the plugin carries.
"""

from __future__ import annotations

import contextlib
import os
import re
from importlib import resources
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, StrictUndefined
from markdown_it import MarkdownIt
from markdown_it.token import Token
from markupsafe import Markup

from dao.domain.spec import SPEC

if TYPE_CHECKING:
    from pathlib import Path

    from dao.domain.book import Book

# A link from one document to another becomes a link within the page.
_ANCHORS = {e.file: f"#{e.key.value}" for e in SPEC} | {"README.md": "#top", "book.html": "#top"}
_SCHEME = re.compile(r"^([a-z][a-z0-9+.-]*+):", re.I)
_LINK_SCHEMES = frozenset({"http", "https", "mailto"})
_NOT_SLUG = re.compile(r"[^a-z0-9]+")
# A document's `## ` sections sit under the page's own `<h2>` for that document.
_HEADING_SHIFT = 1


def slug(name: str) -> str:
    """A name as an id: lower case, anything else a hyphen."""
    return _NOT_SLUG.sub("-", name.lower()).strip("-") or "unnamed"


def _markdown() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False}).enable("table")
    # A wide table scrolls inside its own box, not the page.
    md.add_render_rule("table_open", lambda *_: '<div class="table"><table>\n')
    md.add_render_rule("table_close", lambda *_: "</table></div>\n")
    return md


def _relative(address: str) -> bool:
    """A path beside the page: no scheme, and not from a root or another host."""
    return bool(address) and not address.startswith(("/", "\\")) and not _SCHEME.match(address)


def _link(href: str) -> bool:
    """Whether a link may be a link: within the page, beside it, or web or mail."""
    scheme = _SCHEME.match(href)
    if scheme:
        return scheme.group(1).lower() in _LINK_SCHEMES
    return href.startswith("#") or _relative(href)


def _image(src: str) -> bool:
    """Whether an image may be shown: a file beside the page, or one carried in the page."""
    return _relative(src) or src.lower().startswith("data:image/")


def _inline(children: list[Token]) -> list[Token]:
    """A line's tokens with its links and images held to `_link` and `_image`."""
    out: list[Token] = []
    dropped = 0  # links whose address is refused and whose closing tag is still to come
    for child in children:
        if child.type == "link_open":
            href = str(child.attrGet("href") or "")
            if href.removeprefix("./") in _ANCHORS:
                child.attrSet("href", _ANCHORS[href.removeprefix("./")])
            elif not _link(href):
                dropped += 1
                continue
            elif _SCHEME.match(href):
                child.attrSet("rel", "noopener noreferrer")
        elif child.type == "link_close" and dropped:
            dropped -= 1
            continue
        elif child.type == "image" and not _image(str(child.attrGet("src") or "")):
            child = Token("text", "", 0, content=child.content)
        out.append(child)
    return out


def _settle(tokens: list[Token]) -> None:
    """Fit a document's tokens to the page: heading levels, and where its links go."""
    for token in tokens:
        if token.type in ("heading_open", "heading_close"):
            token.tag = f"h{min(int(token.tag[1]) + _HEADING_SHIFT, 6)}"
        if token.children:
            token.children = _inline(token.children)


def _template() -> str:
    path = resources.files("dao.assets") / "skills" / "dao" / "assets" / "book.html.j2"
    return path.read_text(encoding="utf-8")


def render(book: Book, *, version: str) -> str:
    """The page for `book`. The same book and version always give the same bytes."""
    md = _markdown()

    def block(text: str) -> Markup:
        tokens = md.parse(text)
        _settle(tokens)
        return Markup(md.renderer.render(tokens, md.options, {}))

    def inline(text: str) -> Markup:
        tokens = md.parseInline(text)
        _settle(tokens)
        return Markup(md.renderer.render(tokens, md.options, {}))

    env = Environment(
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    filters: dict[str, Any] = {"md": block, "mdi": inline, "slug": slug}
    env.filters.update(filters)
    kinds = {p.name: (p.kind.value if p.kind else None) for p in book.participants}
    return env.from_string(_template()).render(book=book, version=version, kinds=kinds)


def write(page: str, target: Path) -> None:
    """Put `page` at `target`, replacing what is there, and never through a link.

    The page is written to a new file beside the target and moved into place. Moving
    replaces a link instead of following it, so nothing planted at `target` between the
    caller's checks and this write can send the page somewhere else.
    """
    directory = target.parent.resolve(strict=True)
    scratch = directory / f".{target.name}.{os.getpid()}.tmp"
    fd = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o666)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(page)
        os.replace(scratch, directory / target.name)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(scratch)
        raise

"""The file source: YAML and markdown files, folders of them, and text streams.

A location is a path (``str`` or ``os.PathLike``), a folder or file inside an installed
package (an ``importlib.resources`` ``Traversable``), or an open text stream such as
``sys.stdin``, which is read as YAML. A markdown file (``.md``, ``.markdown``) is its
frontmatter, the YAML mapping between a first line ``---`` and the next ``---`` line, with
the text after it under ``body``, exactly as written; a markdown file with no frontmatter is
all body.

Files are data: ``yaml.safe_load`` builds no objects and runs no code.
"""

from __future__ import annotations

import os
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from matrix.domain.errors import ConfigError
from matrix.domain.ports._out.document_source import BODY, Document
from matrix.domain.problems import problem

if TYPE_CHECKING:
    from collections.abc import Collection

_MARKDOWN = (".md", ".markdown")


def _flat(error: Exception) -> str:
    return " ".join(str(error).split())


def _mapping(text: str, source: str, *, what: str) -> tuple[dict[str, Any] | None, Any]:
    """``text`` parsed as a YAML mapping, or None and the problem that says why not."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return None, problem(
            "",
            "yaml_syntax",
            f"{what} is not valid YAML: {_flat(e)}",
            fix="correct the YAML at the line and column the message gives",
            source=source,
        )
    if data is None:
        return {}, None
    if not isinstance(data, dict):
        return None, problem(
            "",
            "wrong_shape",
            f"{what} must be a mapping of keys to values, not a {type(data).__name__}",
            fix="write `key: value` lines at the top level",
            source=source,
        )
    return data, None


def parse(text: str, source: str, *, markdown: bool) -> tuple[dict[str, Any] | None, Any]:
    """A document's text as its data, or None and the problem that says why it has none."""
    if not markdown:
        return _mapping(text, source, what="the file")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return {BODY: text}, None
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == "---":
            head = "".join(lines[1:index])
            data, found = _mapping(head, source, what="the frontmatter")
            if data is None:
                return None, found
            return {**data, BODY: "".join(lines[index + 1 :])}, None
    return None, problem(
        "",
        "yaml_syntax",
        "the frontmatter opens with '---' but never closes",
        fix="end the frontmatter with a line holding only ---",
        source=source,
    )


class FileSource:
    """Reads documents from files, package folders and streams."""

    def document(self, location: Any) -> Document | None:
        if hasattr(location, "read") and not isinstance(location, Traversable):
            return self._stream(location)
        entry = _entry(location)
        if entry.is_dir():
            return Document(
                source=str(entry),
                stem=_stem(entry.name),
                data=None,
                problem=problem(
                    "",
                    "wrong_shape",
                    f"{entry} is a folder; a file was expected",
                    fix="name one file inside it",
                    source=str(entry),
                ),
            )
        if not entry.is_file():
            return None
        return self._file(entry)

    def documents(self, location: Any, suffixes: Collection[str]) -> tuple[Document, ...] | None:
        if hasattr(location, "read") and not isinstance(location, Traversable):
            return (self._stream(location),)
        entry = _entry(location)
        if entry.is_file():
            return (self._file(entry),)
        if not entry.is_dir():
            return None
        wanted = {suffix.lower() for suffix in suffixes}
        files = sorted(
            (
                item
                for item in entry.iterdir()
                if item.is_file() and os.path.splitext(item.name)[1].lower() in wanted
            ),
            key=lambda item: item.name,
        )
        return tuple(self._file(item) for item in files)

    def beside(self, document: Document, path: str) -> str:
        expanded = Path(path).expanduser()
        if expanded.is_absolute():
            return str(expanded)
        if document.folder is None:
            return path
        return str((Path(document.folder) / path).resolve())

    def _file(self, entry: Traversable) -> Document:
        source = str(entry)
        try:
            text = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise ConfigError(
                f"{source} cannot be read: {type(e).__name__}: {e}",
                fix="check the file's permissions and that it is UTF-8 text",
                path=source,
            ) from e
        markdown = os.path.splitext(entry.name)[1].lower() in _MARKDOWN
        data, found = parse(text, source, markdown=markdown)
        folder = str(Path(source).resolve().parent) if isinstance(entry, Path) else None
        return Document(
            source=source, stem=_stem(entry.name), data=data, problem=found, folder=folder
        )

    def _stream(self, stream: Any) -> Document:
        name = str(getattr(stream, "name", "") or "<stream>")
        source = name if name.startswith("<") else f"<{name}>"
        data, found = parse(stream.read(), source, markdown=False)
        return Document(source=source, stem="", data=data, problem=found)


def _entry(location: Any) -> Traversable:
    if isinstance(location, Traversable):
        return location
    if isinstance(location, str | os.PathLike):
        return Path(location).expanduser()
    raise ConfigError(
        f"{location!r} is not a location the file source reads",
        fix="pass a path, a package folder (importlib.resources.files(...)), or a text stream",
    )


def _stem(name: str) -> str:
    return os.path.splitext(name)[0]

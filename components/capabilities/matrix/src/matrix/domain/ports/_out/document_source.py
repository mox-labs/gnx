"""DocumentSource: reads what is at a location into plain data, knowing nothing of types.

**Assumes** a location the source can read (for the file source: a path, a folder in an
installed package, or an open text stream). **Guarantees**, for each document, plain data and
where it came from, or a problem saying why it could not be read; never a typed object, never
a partial mapping.

* ``document(location)`` is the one document at a location, or None when nothing is there. A
  YAML file is its mapping as it stands; a markdown file is its frontmatter mapping with the
  text after the frontmatter under the key ``body``.
* ``documents(location, suffixes)`` is every document in a folder, one per file whose suffix
  is in ``suffixes``, in file-name order; a file is a folder of one. None when nothing is
  there.
* ``beside(document, path)`` is ``path`` resolved against the folder of the document's file;
  as written when it is absolute, or when the document has no folder (a stream).

A file that is not valid YAML or frontmatter, or whose top level is not a mapping, is a
:class:`Document` with ``data`` None and a ``problem`` (``yaml_syntax`` or ``wrong_shape``):
one unreadable file in a folder does not hide the others. A location the source cannot read
at all raises :class:`~matrix.domain.errors.ConfigError`. What a key means, ``body``
included, is the business of the type the caller validates against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Collection

    from matrix.domain.problems import Problem

#: The key a markdown file's text arrives under.
BODY = "body"


@dataclass(frozen=True)
class Document:
    """One document as read."""

    #: Where it came from, as messages show it (a path, ``<stdin>``).
    source: str
    #: The file's name without its suffix: what a type may take as a default name.
    stem: str
    #: The document's top-level mapping; None when it could not be read (see ``problem``).
    data: dict[str, Any] | None
    problem: Problem | None = None
    #: The folder the file is in, which relative paths in it are read against; None for a
    #: document with no place (a stream).
    folder: str | None = None


@runtime_checkable
class DocumentSource(Protocol):
    def document(self, location: Any) -> Document | None: ...

    def documents(
        self, location: Any, suffixes: Collection[str]
    ) -> tuple[Document, ...] | None: ...

    def beside(self, document: Document, path: str) -> str: ...

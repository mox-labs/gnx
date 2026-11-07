"""MarkitdownConverter: the ``document`` converter (``recon.v1.converter.document``) and the
``$markitdown`` transform, one adapter over Microsoft's markitdown.

A document's bytes become one record, ``{url, title, content, content_type}``: the markdown of
HTML, PDF, DOCX, PPTX, XLSX, XLS, EPub, CSV, RSS, notebooks, Outlook messages, ZIP and images,
its runs of blank lines folded and its content cut at 100,000 characters, marked. A body names
it (an ``api`` collector's ``response_format: document``), or its media type picks it. The
``$markitdown`` transform converts a local file a record names through the same conversion.
Installed via `markitdown[all]`, which pulls the format-specific libraries (pdfminer,
python-docx, python-pptx, openpyxl, ...).

Lazy-imports markitdown so plain `import recon` stays fast, and builds it once per converter.
A document is converted by the content type its server gave, not by a guess from its bytes,
and from its bytes alone. recon builds markitdown with only the converters in
``_CONVERTERS``, none of which reaches the network or starts a process. (Importing markitdown
starts two local ones, once per process: ``uname -p`` and ``file -b <python>``, from Python's
``platform`` module, which ``azure.identity`` reads when markitdown's Document Intelligence
module imports it. Neither reaches the network.) Left out, as audited for markitdown 0.1.5:

* the audio converter, which uploads the whole recording to Google's speech API;
* the YouTube converter, which asks YouTube for a watch page's transcript and prints its
  failures on stdout, where ``--json`` writes the document;
* the Azure Document Intelligence converter, which uploads the document to Azure;
* markitdown's plugins, which are third-party converters recon has not read;
* exiftool, which markitdown would run as a process on images and audio.

Each would be a request outside the requester, its rate limit, ``max_bytes``, spans and
capture log. The image and PowerPoint converters call a model only when given a client, and
recon gives none. A recording or a YouTube page therefore converts as what its bytes are: a
YouTube page as HTML, audio not at all.
A document markitdown cannot read is a CollectionError, never an empty string: an empty
column reads as an empty document. Only markitdown's own errors and an unreadable file are
classified; anything else is a defect and propagates as it is.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from recon.core.domain.converters import essence
from recon.core.domain.errors import CollectionError

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

#: What markitdown's ``convert`` would fetch or decode by itself, given a string.
_URI_SCHEMES = ("http:", "https:", "file:", "data:")

#: The most characters of a document's content a record keeps.
MAX_CONTENT_CHARS = 100_000

#: The media types the ``document`` converter is picked for by a body that names none.
DOCUMENT_TYPES = frozenset({
    "text/html",
    "application/xhtml+xml",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/vnd.ms-outlook",
    "application/epub+zip",
    "application/rss+xml",
    "application/atom+xml",
    "application/x-ipynb+json",
    "application/zip",
    "text/csv",
    "image/*",
})  # fmt: skip


class MarkitdownConverter:
    """The ``document`` converter. A document markitdown cannot read is a CollectionError."""

    media_types = DOCUMENT_TYPES

    def __init__(self) -> None:
        self._markitdown: Any = None

    def convert(self, chunks: Iterable[bytes], media_type: str, origin: str) -> Iterator[Any]:
        title, text = self.document(b"".join(chunks), origin, mimetype=essence(media_type))
        content = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(content) > MAX_CONTENT_CHARS:
            content = content[:MAX_CONTENT_CHARS] + "\n\n[Content truncated due to length...]"
        yield {"url": origin, "title": title, "content": content,
               "content_type": essence(media_type)}  # fmt: skip

    def document(
        self, data: bytes, origin: str, *, mimetype: str = "", extension: str = ""
    ) -> tuple[str, str]:
        """The title and the markdown of a document's bytes, by its media type (or its file's
        extension), from its bytes alone."""
        from markitdown import MarkItDownException, StreamInfo

        info = StreamInfo(mimetype=mimetype or None, extension=extension or None,
                          url=origin if "://" in origin else None)  # fmt: skip
        try:
            result = self._converter().convert_stream(io.BytesIO(data), stream_info=info)
        except MarkItDownException as exc:
            raise _conversion_error(origin, exc) from exc
        if result is None:
            return "", ""
        return (getattr(result, "title", "") or ""), (getattr(result, "text_content", "") or "")

    def _converter(self) -> Any:
        if self._markitdown is None:
            self._markitdown = confined_markitdown()
        return self._markitdown


#: The markitdown converters recon registers, by class name, in markitdown's own order:
#: a later registration at the same priority is tried first. Every one converts from the
#: bytes it is given and nothing else. A name that is not here is not registered, so a
#: converter a later markitdown adds is left out until it has been read.
_CONVERTERS = (
    "RssConverter",
    "WikipediaConverter",
    "BingSerpConverter",
    "DocxConverter",
    "XlsxConverter",
    "XlsConverter",
    "PptxConverter",
    "ImageConverter",
    "IpynbConverter",
    "PdfConverter",
    "OutlookMsgConverter",
    "EpubConverter",
    "CsvConverter",
)


def confined_markitdown() -> Any:
    """A MarkItDown that holds only the converters in ``_CONVERTERS`` and its generic ones.

    Not ``MarkItDown()``: that registers every built-in, among them the audio converter,
    which sends a recording to Google. Built-ins and plugins are both turned off, and each
    converter recon keeps is registered by hand, as ``enable_builtins`` would. The zip
    converter converts each entry with this same instance, so what is inside a zip is held
    to the same list.
    """
    import markitdown
    from markitdown import converters

    md = markitdown.MarkItDown(enable_builtins=False, enable_plugins=False)
    generic = markitdown.PRIORITY_GENERIC_FILE_FORMAT
    md.register_converter(converters.PlainTextConverter(), priority=generic)
    md.register_converter(converters.ZipConverter(markitdown=md), priority=generic)
    md.register_converter(converters.HtmlConverter(), priority=generic)
    for name in _CONVERTERS:
        md.register_converter(getattr(converters, name)())
    return md


def markitdown_path(path: Any, *, roots: Sequence[Path] = (), base: Path | None = None) -> str:
    """The ``$markitdown`` transform: convert a local file the record names to markdown.

    Converts what the ``document`` converter converts, through it (the module docstring lists
    what is left out, audio among it). Registered under the ``recon.transform`` point as
    ``markitdown``.

    A relative path is relative to ``base``, the directory its record came from (a ``cli``
    collector binds the directory its command ran in), else to the working directory.

    The value is collected data, so what it may name is confined. Only a file under one of
    ``roots`` is read (symlinks and ``..`` resolved first); recon binds the mission
    directory and the catalog's ``local`` sources, and with no roots nothing is read. A URI
    is never fetched: that would be a request outside the requester, its rate limit and the
    capture log. Both refusals raise a config error rather than return
    nothing, because an empty column would read as an empty document.

    A file that cannot be read, or that markitdown cannot convert, raises CollectionError
    and fails the table, naming the file.
    """
    if not isinstance(path, str) or not path.strip():
        return ""
    if path.strip().lower().startswith(_URI_SCHEMES):
        raise CollectionError(
            f"$markitdown converts local files and does not fetch: {_shown(path)}",
            kind="config",
            fix="fetch the document with an api collector's response_format: document, which "
            "converts it by its content type",
        )
    resolved = (Path(base) / path if base is not None else Path(path)).resolve()
    if not any(resolved.is_relative_to(Path(root).resolve()) for root in roots):
        raise CollectionError(
            f"$markitdown may read only under the mission directory and the catalog's "
            f"local sources; {_shown(path)} is outside them",
            kind="config",
            fix="add the file's directory to catalog: as a source with `type: local`, "
            "or move the file under the mission directory",
        )
    try:
        data = resolved.read_bytes()
    except OSError as exc:
        raise CollectionError(
            f"$markitdown could not read {path!r}: {exc.strerror or exc}",
            fix="check that the file the record names exists and is readable",
        ) from exc
    _, text = _FILES.document(data, repr(path), extension=resolved.suffix)
    return text


#: The converter the ``$markitdown`` transform converts through, made once.
_FILES = MarkitdownConverter()


def register(registry: Any) -> None:
    """The ``document`` converter at ``recon.converter`` and the ``$markitdown`` transform at
    ``recon.transform``, over one conversion."""
    registry.register("recon.converter", "recon.v1.converter.document", MarkitdownConverter,
                      summary="a document (HTML, PDF, DOCX, ...) as markdown: one record, url, "
                      "title, content, content_type")  # fmt: skip
    registry.register("recon.transform", "recon.v1.transform.markitdown", _markitdown,
                      summary="a local file the record names, as markdown")  # fmt: skip


def _markitdown() -> Any:
    return markitdown_path


def _conversion_error(what: str, exc: Exception) -> CollectionError:
    from markitdown import MissingDependencyException

    if isinstance(exc, MissingDependencyException):
        return CollectionError(
            f"markitdown cannot convert {what}: a converter's dependency is not installed",
            kind="config",
            fix="install recon with markitdown's [all] extra (it is a recon dependency); "
            "reinstall recon if it was installed without it",
        )
    return CollectionError(
        f"markitdown could not convert {what}: {exc}",
        fix="leave this column out of normalize:, or collect only documents markitdown "
        "converts (PDF, DOCX, PPTX, XLSX, HTML, ...); recon does not convert audio, "
        "which markitdown would send to a speech service",
    )


def _shown(value: str) -> str:
    """A collected value quoted for an error message, cut short: it may be a whole data: URI."""
    return repr(value if len(value) <= 120 else value[:120] + "…")

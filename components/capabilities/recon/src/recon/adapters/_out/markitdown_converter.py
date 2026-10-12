"""MarkitdownConverter — DocumentConverter adapter using Microsoft's markitdown.

Handles HTML, PDF, DOCX, PPTX, XLSX, EPub, CSV, JSON, XML, ZIP, images (OCR),
audio (transcription), YouTube URLs, and more. Installed via `markitdown[all]`
which pulls the format-specific plugins (pdfminer, python-docx, python-pptx,
openpyxl, pillow, speechrecognition, yt-dlp, etc.).

Lazy-imports markitdown so plain `import recon` stays fast.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING, Any

from recon.domain.converters import ConversionResult
from recon.domain.exceptions import CollectionError

if TYPE_CHECKING:
    from collections.abc import Sequence

#: What markitdown's ``convert`` would fetch or decode by itself, given a string.
_URI_SCHEMES = ("http:", "https:", "file:", "data:")


class MarkitdownConverter:
    """Convert document bytes via markitdown."""

    def convert(self, content: bytes, content_type: str, url: str) -> ConversionResult:
        from markitdown import MarkItDown

        try:
            result = MarkItDown().convert_stream(io.BytesIO(content), url=url)
        except Exception as exc:
            msg = f"markitdown conversion failed for {url}: {exc}"
            raise CollectionError(msg) from exc

        if result is None:
            return ConversionResult(title="", text="")

        return ConversionResult(
            title=(getattr(result, "title", "") or ""),
            text=(getattr(result, "text_content", "") or ""),
        )


def markitdown_path(path: Any, *, roots: Sequence[Path] = ()) -> str:
    """The ``$markitdown`` transform: convert a local file the record names to markdown.

    Handles PDF, DOCX, PPTX, XLSX, EPub, images (OCR), audio (transcription),
    CSV, JSON, XML, ZIP, HTML. Registered under the entry-point group ``recon.transforms``
    as ``markitdown``.

    The value is collected data, so what it may name is confined. Only a file under one of
    ``roots`` is read (symlinks and ``..`` resolved first); the composition root binds the
    mission directory and the catalog's ``local`` sources, and with no roots nothing is
    read. A URI is never fetched: that would be a request outside the requester, its rate
    limit and the capture log. Both refusals raise a config error rather than return
    nothing, because an empty column would read as an empty document.

    A file that is missing, or that markitdown cannot convert, still yields an empty
    string — a transform is a best-effort leaf of the pipeline, and one unreadable file
    must not fail the table.
    """
    if not isinstance(path, str) or not path.strip():
        return ""
    if path.strip().lower().startswith(_URI_SCHEMES):
        raise CollectionError(
            f"$markitdown converts local files and does not fetch: {_shown(path)}",
            kind="config",
            fix="fetch the document with a `web` collector, which converts it by content type",
        )
    resolved = Path(path).resolve()
    if not any(resolved.is_relative_to(Path(root).resolve()) for root in roots):
        raise CollectionError(
            f"$markitdown may read only under the mission directory and the catalog's "
            f"local sources; {_shown(path)} is outside them",
            kind="config",
            fix="add the file's directory to catalog: as a source with `type: local`, "
            "or move the file under the mission directory",
        )
    try:
        from markitdown import MarkItDown

        result = MarkItDown().convert_local(resolved)
        return getattr(result, "text_content", "") or ""
    except Exception:
        return ""


def _shown(value: str) -> str:
    """A collected value quoted for an error message, cut short: it may be a whole data: URI."""
    return repr(value if len(value) <= 120 else value[:120] + "…")

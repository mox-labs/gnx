"""MarkitdownConverter — DocumentConverter adapter using Microsoft's markitdown.

Handles HTML, PDF, DOCX, PPTX, XLSX, EPub, CSV, JSON, XML, ZIP, images (OCR),
audio (transcription), YouTube URLs, and more. Installed via `markitdown[all]`
which pulls the format-specific plugins (pdfminer, python-docx, python-pptx,
openpyxl, pillow, speechrecognition, yt-dlp, etc.).

Lazy-imports markitdown so plain `import recon` stays fast.
"""

from __future__ import annotations

import io

from recon.domain.converters import ConversionResult
from recon.domain.exceptions import CollectionError


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


def markitdown_path(path: str) -> str:
    """The ``$markitdown`` transform: convert a local file the record names to markdown.

    Handles PDF, DOCX, PPTX, XLSX, EPub, images (OCR), audio (transcription),
    CSV, JSON, XML, ZIP, HTML. Returns an empty string on any failure — a transform is a
    best-effort leaf of the pipeline, and one unreadable file must not fail the table.
    Registered under the entry-point group ``recon.transforms`` as ``markitdown``.
    """
    try:
        from markitdown import MarkItDown

        result = MarkItDown().convert(path)
        return getattr(result, "text_content", "") or ""
    except Exception:
        return ""

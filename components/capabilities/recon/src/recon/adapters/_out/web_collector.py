"""WebCollector — fetch a document, convert it to markdown, yield a record.

HTTP is delegated to the injected Requester (shared rate-limited instance);
conversion is delegated to the injected DocumentConverter (markitdown by
default — handles HTML, PDF, DOCX, PPTX, XLSX, EPub, CSV, JSON, XML, ZIP,
images with OCR, audio transcription, YouTube).

The collector is format-agnostic: same code path for a web page, a PDF
URL, a DOCX download, or a YouTube transcript. Returns Iterator[dict] —
yields one record, `{url, title, content, status_code, content_type}`, which a
`normalize:` spec may reshape like any other.

Registered as the built-in collector type ``web`` (entry-point group ``recon.collectors``).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from recon.application.transforms import BUILTIN_TRANSFORMS, apply_normalize
from recon.domain.collector import CollectorType
from recon.domain.exceptions import CollectionError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from recon.domain.capture import CaptureLog
    from recon.domain.collector import Transform
    from recon.domain.converters import DocumentConverter
    from recon.domain.http import HttpResponse, Requester
    from recon.domain.models import CollectorEntry, SourceEntry

_MAX_CONTENT_CHARS = 100_000


class WebCollector:
    """HTTP GET → document-to-markdown conversion → one JSONL record."""

    def __init__(
        self,
        requester: Requester,
        converter: DocumentConverter,
        transforms: Mapping[str, Transform] = BUILTIN_TRANSFORMS,
    ) -> None:
        self._requester = requester
        self._converter = converter
        self._transforms = transforms

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
    ) -> Iterator[dict[str, Any]]:
        if not source:
            msg = f"Web collector '{entry.name}' requires a source"
            raise CollectionError(msg, kind="config")

        url = _url(entry, source)
        headers = {
            "User-Agent": source.user_agent or "recon/web",
            # Widened from HTML-only so non-HTML content types (PDF, DOCX,
            # images, audio) are served directly by the origin.
            "Accept": "*/*",
        }
        resp: HttpResponse = self._requester.get(source, url, headers=headers)

        if captures is not None:
            captures.record(
                collector=entry.name,
                source=source.name,
                kind="web",
                request={"method": "GET", "url": resp.url},
                status=resp.status_code,
                content_type=resp.content_type,
                headers=resp.headers,
                body=resp.body,
            )

        result = self._converter.convert(resp.body, resp.content_type, resp.url)

        content = re.sub(r"\n{3,}", "\n\n", result.text).strip()
        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS] + "\n\n[Content truncated due to length...]"

        record: dict[str, Any] = {
            "url": resp.url,
            "title": result.title,
            "content": content,
            "status_code": resp.status_code,
            "content_type": resp.content_type,
        }
        yield (
            apply_normalize(record, entry.normalize, self._transforms)
            if entry.normalize
            else record
        )


def _url(entry: CollectorEntry, source: SourceEntry) -> str:
    url = source.url.rstrip("/")
    return f"{url}{entry.endpoint}" if entry.endpoint else url


collector_type = CollectorType(
    create=lambda ctx: WebCollector(ctx.requester, ctx.converter, ctx.transforms),
    effects=frozenset({"network"}),
    summary="HTTP GET → document converted to markdown → one record",
    requires_source=True,
    describe=lambda entry, source: {"method": "GET", "url": _url(entry, source)} if source else {},
)

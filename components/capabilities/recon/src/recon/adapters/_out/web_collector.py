"""WebCollector — fetch a document, convert it to markdown, yield a record.

HTTP is delegated to the injected Requester (shared rate-limited instance);
conversion is delegated to the injected DocumentConverter (markitdown by
default — handles HTML, PDF, DOCX, PPTX, XLSX, EPub, CSV, JSON, XML, ZIP,
images with OCR, audio transcription). Everything it converts is what the
Requester fetched: nothing more is fetched for a page.

The collector is format-agnostic: same code path for a web page, a PDF
URL or a DOCX download. Returns Iterator[dict] —
yields one record, `{url, title, content, status_code, content_type}`, which a
`normalize:` spec may reshape like any other.

Registered as the built-in collector type ``web`` (entry-point group ``recon.collectors``).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from recon.adapters._out.user_agent import DEFAULT_USER_AGENT
from recon.domain.collector import CollectorType
from recon.domain.errors import Problem
from recon.domain.http import reported_url
from recon.domain.normalize import BUILTIN_TRANSFORMS, apply_normalize
from recon.domain.substitution import find_unresolved

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from recon.domain.capture import CaptureLog
    from recon.domain.collector import Transform
    from recon.domain.converters import DocumentConverter
    from recon.domain.http import HttpResponse, Requester
    from recon.domain.models import CollectorEntry, SourceEntry

_MAX_CONTENT_CHARS = 100_000


class WebSettings(BaseModel):
    """A ``web`` collector's own key: ``endpoint``, appended to the source's url."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: str | None = None


def _endpoint(entry: CollectorEntry) -> str | None:
    return WebSettings.model_validate(entry.settings).endpoint


class WebCollector:
    """HTTP GET → document-to-markdown conversion → one JSONL record."""

    def __init__(
        self,
        requester: Requester,
        converter: DocumentConverter,
        user_agent: str,
        transforms: Mapping[str, Transform] = BUILTIN_TRANSFORMS,
    ) -> None:
        self._requester = requester
        self._converter = converter
        self._user_agent = user_agent
        self._transforms = transforms

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
    ) -> Iterator[dict[str, Any]]:
        assert source is not None  # the type requires a source, and the plan holds it
        url = _url(entry, source)
        headers = _headers(source, self._user_agent)
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
    endpoint = _endpoint(entry)
    return f"{url}{endpoint}" if endpoint else url


def _headers(source: SourceEntry, user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": source.user_agent or user_agent,
        # Any type: the converter handles PDF, DOCX, images and audio as well as HTML.
        "Accept": "*/*",
    }


def _check(entry: CollectorEntry) -> list[Problem]:
    """A web collector fills no placeholder, so one in ``endpoint`` would be fetched as
    written."""
    endpoint = _endpoint(entry)
    unresolved = sorted(set(find_unresolved(endpoint or "")))
    if not unresolved:
        return []
    names = ", ".join(f"{{{name}}}" for name in unresolved)
    return [
        Problem(
            "endpoint",
            f"unresolved placeholder(s) {names}: a web collector fills none",
            kind="unresolved",
            fix="write the value into endpoint:, or use an api collector, whose params: fill "
            "an endpoint's placeholders",
            got=endpoint,
        )
    ]


def _describe(entry: CollectorEntry, source: SourceEntry | None) -> dict[str, Any]:
    """The request a run sends, as the api type describes one."""
    if source is None:
        return {}
    return {
        "method": "GET",
        "url": reported_url(_url(entry, source)),
        "headers": _headers(source, DEFAULT_USER_AGENT),
    }


collector_type = CollectorType(
    create=lambda ctx: WebCollector(ctx.requester, ctx.converter, ctx.user_agent, ctx.transforms),
    effects=frozenset({"network"}),
    summary="HTTP GET → document converted to markdown → one record",
    settings=WebSettings,
    source_kinds=frozenset({"web"}),
    requires_source=True,
    normalizes=True,
    check=_check,
    describe=_describe,
)

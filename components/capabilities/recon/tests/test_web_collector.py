"""Tests for WebCollector — fetches + converts via injected ports.

Both Requester and DocumentConverter are injected at construction time;
tests pass fakes directly. No monkeypatching, no network, no markitdown
dependency in the test path.
"""

from typing import Any

import pytest

from recon.adapters._out.user_agent import DEFAULT_USER_AGENT
from recon.adapters._out.web_collector import WebCollector
from recon.domain.converters import ConversionResult
from recon.domain.errors import CollectionError
from recon.domain.http import HttpResponse
from recon.domain.models import CollectorEntry, SourceEntry
from tests.recon_fakes import collector_entry


class _FakeRequester:
    def __init__(
        self,
        response: HttpResponse | None = None,
        raise_on_call: Exception | None = None,
    ) -> None:
        self.response = response
        self.raise_on_call = raise_on_call
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        source: SourceEntry,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> HttpResponse:
        self.calls.append({"method": method, "url": url, "headers": headers})
        if self.raise_on_call:
            raise self.raise_on_call
        assert self.response is not None, "the double was given no response to return"
        return self.response

    def get(
        self, source: SourceEntry, url: str, *, headers: dict[str, str] | None = None
    ) -> HttpResponse:
        return self.request(source, "GET", url, params={}, headers=headers)


class _FakeConverter:
    def __init__(self, result: ConversionResult | None = None) -> None:
        self.result = result or ConversionResult(title="", text="")
        self.calls: list[dict[str, Any]] = []

    def convert(self, content: bytes, content_type: str, url: str) -> ConversionResult:
        self.calls.append({"content": content, "content_type": content_type, "url": url})
        return self.result


def _resp(url: str, body: bytes, content_type: str) -> HttpResponse:
    return HttpResponse(
        status_code=200,
        headers={"content-type": content_type},
        body=body,
        text=body.decode(errors="ignore"),
        url=url,
        content_type=content_type,
    )


class TestUserAgent:
    def _sent(self, **source_fields: Any) -> str:
        req = _FakeRequester(_resp("https://x/", b"<p>hi</p>", "text/html"))
        source = SourceEntry(name="s", type="web", url="https://x", **source_fields)
        entry = CollectorEntry(name="c", type="web", source="s")
        list(WebCollector(req, _FakeConverter(), DEFAULT_USER_AGENT).collect(entry, source))
        return str(req.calls[0]["headers"]["User-Agent"])

    def test_the_default_is_the_one_every_collector_sends(self) -> None:
        from recon import __version__

        assert self._sent() == DEFAULT_USER_AGENT
        assert DEFAULT_USER_AGENT.startswith(f"recon/{__version__} ")

    def test_a_mission_s_own_user_agent_is_sent_as_written(self) -> None:
        assert self._sent(user_agent="docs-bot/1") == "docs-bot/1"


class TestWebCollector:
    def test_html_flows_through_converter(self) -> None:
        req = _FakeRequester(_resp("https://x/", b"<html><title>T</title></html>", "text/html"))
        conv = _FakeConverter(ConversionResult(title="My Page", text="# Heading\n\nBody"))
        source = SourceEntry(name="s", url="https://x")
        entry = CollectorEntry(name="t", type="web", source="s")

        records = list(WebCollector(req, conv, DEFAULT_USER_AGENT).collect(entry, source))
        assert len(records) == 1
        record = records[0]
        assert record["title"] == "My Page"
        assert record["content"] == "# Heading\n\nBody"
        assert record["status_code"] == 200
        assert record["content_type"] == "text/html"
        # Converter received the raw bytes + content type + URL
        assert conv.calls[0]["content"] == b"<html><title>T</title></html>"
        assert conv.calls[0]["content_type"] == "text/html"

    def test_pdf_flows_through_same_path(self) -> None:
        """Same code path for PDFs — converter handles format detection."""
        req = _FakeRequester(_resp("https://x/report.pdf", b"%PDF-1.4 ...", "application/pdf"))
        conv = _FakeConverter(ConversionResult(title="Q4 Report", text="## Summary\n\nText."))
        source = SourceEntry(name="s", url="https://x")
        entry = collector_entry(name="t", type="web", source="s", endpoint="/report.pdf")

        records = list(WebCollector(req, conv, DEFAULT_USER_AGENT).collect(entry, source))
        assert records[0]["title"] == "Q4 Report"
        assert records[0]["content_type"] == "application/pdf"
        assert "Summary" in records[0]["content"]
        assert conv.calls[0]["content_type"] == "application/pdf"

    def test_endpoint_appended_to_source_url(self) -> None:
        req = _FakeRequester(_resp("https://x/docs", b"<html></html>", "text/html"))
        conv = _FakeConverter()
        source = SourceEntry(name="s", url="https://x")
        entry = collector_entry(name="t", type="web", source="s", endpoint="/docs")
        list(WebCollector(req, conv, DEFAULT_USER_AGENT).collect(entry, source))
        assert req.calls[0]["url"] == "https://x/docs"

    def test_requester_error_propagates(self) -> None:
        err = CollectionError("network down")
        req = _FakeRequester(raise_on_call=err)
        conv = _FakeConverter()
        source = SourceEntry(name="s", url="https://x")
        entry = CollectorEntry(name="t", type="web", source="s")
        with pytest.raises(CollectionError, match="network down"):
            list(WebCollector(req, conv, DEFAULT_USER_AGENT).collect(entry, source))

    def test_converter_error_propagates(self) -> None:
        req = _FakeRequester(_resp("https://x", b"garbage", "application/octet-stream"))

        class _BadConverter:
            def convert(self, content: bytes, content_type: str, url: str) -> ConversionResult:
                raise CollectionError("unsupported format")

        source = SourceEntry(name="s", url="https://x")
        entry = CollectorEntry(name="t", type="web", source="s")
        with pytest.raises(CollectionError, match="unsupported format"):
            list(WebCollector(req, _BadConverter(), DEFAULT_USER_AGENT).collect(entry, source))

    def test_content_truncation(self) -> None:
        huge = "x" * 200_000
        req = _FakeRequester(_resp("https://x", b"<html></html>", "text/html"))
        conv = _FakeConverter(ConversionResult(title="T", text=huge))
        source = SourceEntry(name="s", url="https://x")
        entry = CollectorEntry(name="t", type="web", source="s")
        records = list(WebCollector(req, conv, DEFAULT_USER_AGENT).collect(entry, source))
        content = records[0]["content"]
        assert content.startswith("x" * 100_000) and not content.startswith("x" * 100_001)
        assert content.endswith("[Content truncated due to length...]")

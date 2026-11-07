"""An api collector's ``response_format: document``: what the ``web`` type was (ruling R10,
removed in 0.19), an HTTP GET whose answer recon converts to markdown by its content type with
the ``document`` converter, one record ``{url, title, content, content_type}``.

The Requester and the converter are fakes, given as the act would give them (the requester as
the type's need, the converter among the installed ones), so no network and no markitdown is in
the test path but where a test says so.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest

from recon.adapters._out.api_collector import ApiCollector
from recon.adapters._out.credentials import Credentials
from recon.adapters._out.markitdown_converter import MAX_CONTENT_CHARS, MarkitdownConverter
from recon.adapters._out.user_agent import DEFAULT_USER_AGENT
from recon.composition import load_plugins
from recon.core.application.plan import build_plan
from recon.core.domain.errors import CollectionError
from recon.core.domain.http import HttpResponse
from recon.testing import records
from tests.recon_fakes import run_for

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from recon.core.domain.collector import Plugins
    from recon.core.domain.models import SourceEntry


class _FakeRequester:
    def __init__(
        self, response: HttpResponse | None = None, raise_on_call: Exception | None = None
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


class _FakeDocuments:
    """The ``document`` converter, standing in for markitdown: a fixed title and text."""

    media_types = frozenset({"text/html"})

    def __init__(self, title: str = "", text: str = "") -> None:
        self.title, self.text = title, text
        self.calls: list[dict[str, Any]] = []

    def convert(self, chunks: Iterable[bytes], media_type: str, origin: str) -> Iterator[Any]:
        self.calls.append({"content": b"".join(chunks), "media_type": media_type, "url": origin})
        yield {"url": origin, "title": self.title, "content": self.text,
               "content_type": media_type.split(";")[0]}  # fmt: skip


def _resp(url: str, body: bytes, content_type: str) -> HttpResponse:
    return HttpResponse(
        status_code=200,
        headers={"content-type": content_type},
        body=body,
        text=body.decode(errors="ignore"),
        url=url,
        content_type=content_type,
    )


def _with(documents: Any) -> Plugins:
    plugins = load_plugins()
    found = plugins.converters["document"]
    plugins.converters["document"] = replace(found, converter=documents)
    return plugins


def _document(
    requester: _FakeRequester, documents: Any, endpoint: str = ""
) -> list[dict[str, Any]]:
    entry = {"name": "t", "type": "api", "source": "s", "endpoint": endpoint or "/",
             "response_format": "document"}  # fmt: skip
    run = run_for(entry, {"name": "s", "url": "https://x"})
    api = ApiCollector(requester=requester, credentials=Credentials({}),
                       user_agent=DEFAULT_USER_AGENT)  # fmt: skip
    return records(api, run, _with(documents))


def test_a_page_flows_through_the_document_converter_into_one_record() -> None:
    req = _FakeRequester(_resp("https://x/", b"<html><title>T</title></html>", "text/html"))
    docs = _FakeDocuments("My Page", "# Heading\n\nBody")
    [record] = _document(req, docs)
    assert record == {"url": "https://x/", "title": "My Page", "content": "# Heading\n\nBody",
                      "content_type": "text/html"}  # fmt: skip
    # The converter received the raw bytes, the content type and the URL.
    assert docs.calls[0]["content"] == b"<html><title>T</title></html>"
    assert docs.calls[0]["media_type"] == "text/html"


def test_a_pdf_flows_through_the_same_converter_whatever_its_type() -> None:
    """``document`` names its converter, so its content type does not choose another: a PDF
    and a JSON body are each one document."""
    for url, body, content_type in (
        ("https://x/report.pdf", b"%PDF-1.4 ...", "application/pdf"),
        ("https://x/data.json", b'{"a": 1}', "application/json"),
    ):
        docs = _FakeDocuments("Q4 Report", "## Summary\n\nText.")
        [record] = _document(_FakeRequester(_resp(url, body, content_type)), docs, "/r")
        assert record["content_type"] == content_type
        assert "Summary" in record["content"]
        assert docs.calls[0]["media_type"] == content_type


def test_the_endpoint_is_appended_to_the_source_url() -> None:
    req = _FakeRequester(_resp("https://x/docs", b"<html></html>", "text/html"))
    _document(req, _FakeDocuments(), "/docs")
    assert req.calls[0]["url"] == "https://x/docs"


def test_a_requester_error_propagates() -> None:
    req = _FakeRequester(raise_on_call=CollectionError("network down"))
    with pytest.raises(CollectionError, match="network down"):
        _document(req, _FakeDocuments())


def test_a_converter_error_propagates() -> None:
    class _Bad(_FakeDocuments):
        def convert(self, chunks: Iterable[bytes], media_type: str, origin: str) -> Iterator[Any]:
            raise CollectionError("unsupported format")

    req = _FakeRequester(_resp("https://x", b"garbage", "application/octet-stream"))
    with pytest.raises(CollectionError, match="unsupported format"):
        _document(req, _Bad())


def test_a_document_s_content_is_cut_at_its_bound_and_marked() -> None:
    class _Huge(MarkitdownConverter):
        def document(self, data: bytes, origin: str, *, mimetype: str = "",
                     extension: str = "") -> tuple[str, str]:  # fmt: skip
            return "T", "x" * 200_000

    [record] = _Huge().convert([b"<html></html>"], "text/html", "https://x")
    content = record["content"]
    assert content.startswith("x" * MAX_CONTENT_CHARS)
    assert not content.startswith("x" * (MAX_CONTENT_CHARS + 1))
    assert content.endswith("[Content truncated due to length...]")


def test_markitdown_converts_a_real_page() -> None:
    """The document converter itself, over markitdown: an HTML page's title and its text."""
    [record] = MarkitdownConverter().convert(
        [
            b"<html><head><title>Hello</title></head><body><h1>Big</h1>\n\n\n\n<p>p</p></body></html>"
        ],
        "text/html; charset=utf-8",
        "https://x/page",
    )
    assert record["title"] == "Hello" and record["url"] == "https://x/page"
    assert "# Big" in record["content"] and "\n\n\n" not in record["content"]
    assert record["content_type"] == "text/html"


@pytest.mark.parametrize(
    ("where", "written", "fix"),
    [
        ("collectors.0.type", {"collectors": [{"name": "p", "type": "web"}]}, "response_format"),
        (
            "catalog.0.type",
            {
                "catalog": [{"name": "s", "type": "web", "url": "https://x"}],
                "collectors": [{"name": "p", "type": "api", "endpoint": "/"}],
            },
            "type: api",
        ),
    ],  # fmt: skip
    ids=["collector", "source"],
)
def test_type_web_is_a_plan_problem_that_says_what_to_write(
    where: str, written: dict[str, Any], fix: str
) -> None:
    """Ruling R10: ``type: web`` is gone, a collector's and a source's; a config naming it is
    refused before anything runs, with what to write instead."""
    plan = build_plan(written, load_plugins(), {})
    [problem] = [p for p in plan.problems if p.path == where]
    assert "removed from recon in 0.19" in problem.message
    assert fix in problem.fix

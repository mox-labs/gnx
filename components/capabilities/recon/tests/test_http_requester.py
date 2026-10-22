"""A failed request is classified by what the caller should do: retry, re-key, or fix.
A response body is bounded by its source's ``max_bytes``.

The body tests talk to a server on 127.0.0.1 started by the test; nothing leaves the machine.
"""

import collections
import contextlib
import email.utils
import gzip
import http.server
import re
import threading
import time

import httpx
import pytest

from recon.adapters._out.http_requester import HttpxRequester, classify_http_failure
from recon.adapters._out.system_clock import SystemClock
from recon.domain.errors import CollectionError
from recon.domain.models import SourceEntry
from recon.domain.ratelimit import RateLimiter


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test/x")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=response)


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        (401, "auth"),
        (403, "auth"),
        (429, "transient"),
        (500, "transient"),
        (503, "transient"),
        (400, "run_failed"),
        (404, "run_failed"),
    ],
)
def test_status_codes(code, kind):
    assert classify_http_failure(_status_error(code)) == kind


def test_transport_errors_are_transient():
    request = httpx.Request("GET", "https://example.test/x")
    assert classify_http_failure(httpx.ConnectError("refused", request=request)) == "transient"
    assert classify_http_failure(httpx.ReadTimeout("slow", request=request)) == "transient"


# --- body size ------------------------------------------------------------------------

BIG = 2_000_000


class _Handler(http.server.BaseHTTPRequestHandler):
    hits: collections.Counter[str] = collections.Counter()

    def do_GET(self):
        if self.path == "/small":
            self._send(200, b'{"ok": "caf\xc3\xa9"}', {"Content-Type": "application/json"})
        elif self.path == "/declared":
            self._send(200, b"x" * BIG, {"Content-Type": "text/plain"})
        elif self.path == "/undeclared":
            # No Content-Length: the size is only known by reading.
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Connection", "close")
            self.end_headers()
            with contextlib.suppress(OSError):  # the client hangs up once it has read enough
                for _ in range(BIG // 10_000):
                    self.wfile.write(b"x" * 10_000)
        elif self.path == "/bomb":
            packed = gzip.compress(b"x" * BIG)
            assert len(packed) < 10_000
            self._send(200, packed, {"Content-Type": "text/plain", "Content-Encoding": "gzip"})
        elif self.path.startswith("/echo"):
            self._send(200, self.path.encode(), {"Content-Type": "text/plain"})
        elif self.path == "/forbidden":
            self._send(403, b"key revoked " + b"x" * BIG, {"Content-Type": "text/plain"})
        elif self.path == "/missing":
            self._send(404, b"no such record", {"Content-Type": "text/plain"})
        elif self.path == "/quota-spent":
            _Handler.hits["/quota-spent"] += 1
            self._send(429, b"daily quota spent", {"Retry-After": "3600"})
        elif self.path.startswith("/odd-wait/"):
            _Handler.hits[self.path] += 1
            self._send(429, b"slow down", {"Retry-After": self.path.rsplit("/", 1)[1]})
        elif self.path == "/busy":
            _Handler.hits["/busy"] += 1
            self._send(429, b"slow down", {"Retry-After": "0"})
        elif self.path == "/quota-spent-until":
            _Handler.hits["/quota-spent-until"] += 1
            an_hour_on = email.utils.formatdate(time.time() + 3600, usegmt=True)
            self._send(429, b"daily quota spent", {"Retry-After": an_hour_on})
        elif self.path == "/busy-until":
            _Handler.hits["/busy-until"] += 1
            passed = email.utils.formatdate(time.time() - 60, usegmt=True)
            self._send(503, b"slow down", {"Retry-After": passed})

    def _send(self, status, body, headers):
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        with contextlib.suppress(OSError):
            self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def base_url():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def _get(base_url, path, **source_fields):
    source = SourceEntry(name="local", url=base_url, timeout=10, **source_fields)
    clock = SystemClock()
    return HttpxRequester(RateLimiter(clock), clock).get(source, f"{base_url}{path}")


def test_a_body_within_the_limit_is_returned_whole(base_url):
    resp = _get(base_url, "/small", max_bytes=1_000)
    assert resp.status_code == 200
    assert resp.body == b'{"ok": "caf\xc3\xa9"}'
    assert resp.text == '{"ok": "café"}'
    assert resp.content_type == "application/json"
    assert resp.url == f"{base_url}/small"


def test_a_query_in_the_url_is_kept_and_params_are_added_to_it(base_url):
    """Regression: the query string of a URL (a web source's ``?id=3``) was dropped, since
    an empty ``params`` replaced it."""
    assert _get(base_url, "/echo?id=3").text == "/echo?id=3"
    source = SourceEntry(name="local", url=base_url, timeout=10)
    clock = SystemClock()
    requester = HttpxRequester(RateLimiter(clock), clock)
    sent = requester.request(source, "GET", f"{base_url}/echo?id=3", params={"key": "k"})
    assert sent.text == "/echo?id=3&key=k"


def test_the_default_limit_admits_an_ordinary_large_response(base_url):
    assert len(_get(base_url, "/declared").body) == BIG


@pytest.mark.parametrize("path", ["/declared", "/undeclared", "/bomb"])
def test_a_body_over_the_limit_is_an_error_that_names_the_limit(base_url, path):
    """Whether the server declares the size, hides it, or compresses it: recon used to
    buffer whatever arrived."""
    with pytest.raises(CollectionError) as excinfo:
        _get(base_url, path, max_bytes=100_000)
    error = excinfo.value
    assert error.kind == "run_failed"
    assert "100,000" in str(error) and "'local'" in str(error)
    assert "max_bytes" in error.fix


def test_an_error_status_keeps_its_kind_and_the_start_of_its_body(base_url):
    """An oversized error page is still the error it is, not a size problem."""
    with pytest.raises(CollectionError) as excinfo:
        _get(base_url, "/forbidden", max_bytes=100_000)
    assert excinfo.value.kind == "auth"
    assert "HTTP 403" in str(excinfo.value) and "key revoked" in str(excinfo.value)
    assert len(str(excinfo.value)) < 1_000

    with pytest.raises(CollectionError, match="no such record") as excinfo:
        _get(base_url, "/missing")
    assert excinfo.value.kind == "run_failed"


def test_a_failed_request_says_what_to_do_next(base_url):
    """Regression: a failed request carried kind and message only, so the table's error in
    meta.yaml and the survey document had no fix."""
    with pytest.raises(CollectionError) as excinfo:
        _get(base_url, "/forbidden", max_bytes=100_000)
    assert "auth.env" in excinfo.value.fix
    with pytest.raises(CollectionError) as excinfo:
        _get(base_url, "/missing")
    assert "endpoint" in excinfo.value.fix


@pytest.mark.parametrize("value", [0, -1])
def test_the_limit_must_be_positive(value):
    with pytest.raises(ValueError, match="max_bytes"):
        SourceEntry(name="s", url="https://x", max_bytes=value)


def test_a_retry_after_longer_than_the_cap_fails_at_once_and_says_how_long(base_url):
    """A spent quota can ask for hours; the survey must not sit silent through it."""
    started = time.monotonic()
    with pytest.raises(CollectionError) as excinfo:
        _get(base_url, "/quota-spent")
    assert time.monotonic() - started < 5
    assert _Handler.hits["/quota-spent"] == 1
    error = excinfo.value
    assert error.kind == "transient"
    assert "3600s" in str(error) and "3600s" in error.fix


def test_a_short_retry_after_is_waited_out_and_retried(base_url):
    with pytest.raises(CollectionError, match="after retries") as excinfo:
        _get(base_url, "/busy")
    assert _Handler.hits["/busy"] == 3
    assert excinfo.value.kind == "transient"


def test_a_retry_after_date_past_the_cap_fails_at_once_and_says_how_long(base_url):
    """Regression: a Retry-After given as an HTTP-date was ignored, so the survey retried
    a spent quota with backoff instead of failing at once."""
    started = time.monotonic()
    with pytest.raises(CollectionError) as excinfo:
        _get(base_url, "/quota-spent-until")
    assert time.monotonic() - started < 5
    assert _Handler.hits["/quota-spent-until"] == 1
    error = excinfo.value
    assert error.kind == "transient"
    assert re.search(r"asks for 3[56][0-9]{2}s", str(error)), str(error)
    assert "retry the survey after" in error.fix


def test_a_retry_after_date_already_passed_is_retried_at_once(base_url):
    started = time.monotonic()
    with pytest.raises(CollectionError, match="after retries"):
        _get(base_url, "/busy-until")
    assert _Handler.hits["/busy-until"] == 3
    assert time.monotonic() - started < 2, "a passed date asks for no wait, not backoff"


@pytest.mark.parametrize("asked", ["-5", "nan", "inf"])
def test_a_retry_after_that_is_not_a_wait_falls_back_to_backoff(base_url, asked):
    with pytest.raises(CollectionError, match="after retries") as excinfo:
        _get(base_url, f"/odd-wait/{asked}")
    assert _Handler.hits[f"/odd-wait/{asked}"] == 3
    assert excinfo.value.kind == "transient"


# --- redirects ------------------------------------------------------------------------


def _redirecting(monkeypatch, location, origin="https://api.example.test"):
    """Requests sent through a MockTransport on which ``<origin>/start`` answers 302 to
    ``location`` and any other URL answers 200: each sent request's URL and headers."""
    sent = []

    def answer(request):
        sent.append((str(request.url), dict(request.headers)))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": location})
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(answer), **kw)
    )
    source = SourceEntry(name="keyed", url=origin, auth={"header": "x-api-key"})
    clock = SystemClock()
    requester = HttpxRequester(RateLimiter(clock), clock)
    requester.request(source, "GET", f"{source.url}/start", headers={"x-api-key": "SECRET1"})
    return sent


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example.net/landing",  # another host
        "http://api.example.test/landing",  # the same host, down to plain http
        "https://api.example.test:8443/landing",  # the same host, another port
    ],
)
def test_a_redirect_to_another_origin_does_not_carry_the_sources_credential(monkeypatch, location):
    """Regression: httpx strips only Authorization on a cross-origin redirect, so a key in
    the source's own ``auth.header`` (``x-api-key``, as Exa and Serper take it) went to
    whatever host the first one redirected to."""
    sent = _redirecting(monkeypatch, location)
    assert [url for url, _ in sent] == ["https://api.example.test/start", location]
    assert sent[0][1]["x-api-key"] == "SECRET1"
    assert "x-api-key" not in sent[1][1]


@pytest.mark.parametrize(
    ("origin", "location"),
    [
        ("https://api.example.test", "https://api.example.test/landing"),  # the same origin
        ("https://api.example.test", "/landing"),  # a relative redirect
        ("http://api.example.test", "https://api.example.test/landing"),  # up to https
    ],
)
def test_a_redirect_within_the_origin_keeps_the_credential(monkeypatch, origin, location):
    """The credential goes where the source's URL sends it, and only there: a redirect
    inside that origin, or its upgrade to https, still authenticates, as httpx keeps
    ``Authorization`` there."""
    sent = _redirecting(monkeypatch, location, origin)
    assert sent[1][0] == "https://api.example.test/landing"
    assert sent[1][1]["x-api-key"] == "SECRET1"

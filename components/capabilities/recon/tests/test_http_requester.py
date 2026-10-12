"""A failed request is classified by what the caller should do: retry, re-key, or fix.
A response body is bounded by its source's ``max_bytes``.

The body tests talk to a server on 127.0.0.1 started by the test; nothing leaves the machine.
"""

import contextlib
import gzip
import http.server
import threading

import httpx
import pytest

from recon.adapters._out.http_requester import HttpxRequester, classify_http_failure
from recon.application.utilization import RateLimiter
from recon.domain.exceptions import CollectionError
from recon.domain.models import SourceEntry


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
        (400, "collection"),
        (404, "collection"),
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
        elif self.path == "/forbidden":
            self._send(403, b"key revoked " + b"x" * BIG, {"Content-Type": "text/plain"})
        elif self.path == "/missing":
            self._send(404, b"no such record", {"Content-Type": "text/plain"})

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
    return HttpxRequester(RateLimiter()).get(source, f"{base_url}{path}")


def test_a_body_within_the_limit_is_returned_whole(base_url):
    resp = _get(base_url, "/small", max_bytes=1_000)
    assert resp.status_code == 200
    assert resp.body == b'{"ok": "caf\xc3\xa9"}'
    assert resp.text == '{"ok": "café"}'
    assert resp.content_type == "application/json"
    assert resp.url == f"{base_url}/small"


def test_the_default_limit_admits_an_ordinary_large_response(base_url):
    assert len(_get(base_url, "/declared").body) == BIG


@pytest.mark.parametrize("path", ["/declared", "/undeclared", "/bomb"])
def test_a_body_over_the_limit_is_an_error_that_names_the_limit(base_url, path):
    """Whether the server declares the size, hides it, or compresses it: recon used to
    buffer whatever arrived."""
    with pytest.raises(CollectionError) as excinfo:
        _get(base_url, path, max_bytes=100_000)
    error = excinfo.value
    assert error.kind == "collection"
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
    assert excinfo.value.kind == "collection"


@pytest.mark.parametrize("value", [0, -1])
def test_the_limit_must_be_positive(value):
    with pytest.raises(ValueError, match="max_bytes"):
        SourceEntry(name="s", url="https://x", max_bytes=value)

"""A failed request is classified by what the caller should do: retry, re-key, or fix.
A response body is bounded by its source's ``max_bytes``.

The body tests talk to a server on 127.0.0.1 started by the test; nothing leaves the machine.
"""

import collections
import contextlib
import email.utils
import gzip
import http.server
import math
import re
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx2
import pytest
from pydantic import ValidationError

from recon.adapters._out.http_requester import HttpRequester, classify_http_failure
from recon.adapters._out.system_clock import SystemClock
from recon.domain.clock import LONGEST_WAIT
from recon.domain.deadline import Deadline
from recon.domain.errors import CollectionError
from recon.domain.http import HttpResponse
from recon.domain.models import AuthConfig, RateLimitConfig, SourceEntry
from recon.domain.ratelimit import RateLimiter
from tests.recon_fakes import FixedClock, HurriedClock


def _status_error(code: int) -> httpx2.HTTPStatusError:
    request = httpx2.Request("GET", "https://example.test/x")
    response = httpx2.Response(code, request=request)
    return httpx2.HTTPStatusError(f"HTTP {code}", request=request, response=response)


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
def test_status_codes(code: int, kind: str) -> None:
    assert classify_http_failure(_status_error(code)) == kind


def test_transport_errors_are_transient() -> None:
    request = httpx2.Request("GET", "https://example.test/x")
    assert classify_http_failure(httpx2.ConnectError("refused", request=request)) == "transient"
    assert classify_http_failure(httpx2.ReadTimeout("slow", request=request)) == "transient"


# --- body size ------------------------------------------------------------------------

BIG = 2_000_000


class _Handler(http.server.BaseHTTPRequestHandler):
    hits: collections.Counter[str] = collections.Counter()

    def do_GET(self) -> None:
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

    def _send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        with contextlib.suppress(OSError):
            self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def _get(base_url: str, path: str, **source_fields: Any) -> HttpResponse:
    source = SourceEntry(name="local", url=base_url, timeout=10, **source_fields)
    clock = SystemClock()
    return HttpRequester(RateLimiter(clock), clock).get(source, f"{base_url}{path}")


def test_the_longest_timeout_a_source_may_have_is_one_a_send_takes(base_url: str) -> None:
    """The bound on ``timeout`` is the platform's own, :data:`LONGEST_WAIT`, not a number
    picked: a send under it succeeds where 1e12 raised ``OverflowError`` from the socket, and
    a timeout past it is refused before anything is sent."""
    source = SourceEntry(name="local", url=base_url, timeout=LONGEST_WAIT)
    clock = SystemClock()
    sent = HttpRequester(RateLimiter(clock), clock).get(source, f"{base_url}/small")
    assert sent.status_code == 200
    with pytest.raises(ValidationError):
        SourceEntry(name="local", url=base_url, timeout=math.nextafter(LONGEST_WAIT, math.inf))


def test_a_body_within_the_limit_is_returned_whole(base_url: str) -> None:
    resp = _get(base_url, "/small", max_bytes=1_000)
    assert resp.status_code == 200
    assert resp.body == b'{"ok": "caf\xc3\xa9"}'
    assert resp.text == '{"ok": "café"}'
    assert resp.content_type == "application/json"
    assert resp.url == f"{base_url}/small"


def test_a_query_in_the_url_is_kept_and_params_are_added_to_it(base_url: str) -> None:
    """Regression: the query string of a URL (a web source's ``?id=3``) was dropped, since
    an empty ``params`` replaced it."""
    assert _get(base_url, "/echo?id=3").text == "/echo?id=3"
    source = SourceEntry(name="local", url=base_url, timeout=10)
    clock = SystemClock()
    requester = HttpRequester(RateLimiter(clock), clock)
    sent = requester.request(source, "GET", f"{base_url}/echo?id=3", params={"key": "k"})
    assert sent.text == "/echo?id=3&key=k"


def test_the_default_limit_admits_an_ordinary_large_response(base_url: str) -> None:
    assert len(_get(base_url, "/declared").body) == BIG


@pytest.mark.parametrize("path", ["/declared", "/undeclared", "/bomb"])
def test_a_body_over_the_limit_is_an_error_that_names_the_limit(base_url: str, path: str) -> None:
    """Whether the server declares the size, hides it, or compresses it: recon used to
    buffer whatever arrived."""
    with pytest.raises(CollectionError) as excinfo:
        _get(base_url, path, max_bytes=100_000)
    error = excinfo.value
    assert error.kind == "run_failed"
    assert "100,000" in str(error) and "'local'" in str(error)
    assert "max_bytes" in (error.fix or "")


def test_an_error_status_keeps_its_kind_and_the_start_of_its_body(base_url: str) -> None:
    """An oversized error page is still the error it is, not a size problem."""
    with pytest.raises(CollectionError) as excinfo:
        _get(base_url, "/forbidden", max_bytes=100_000)
    assert excinfo.value.kind == "auth"
    assert "HTTP 403" in str(excinfo.value) and "key revoked" in str(excinfo.value)
    assert len(str(excinfo.value)) < 1_000

    with pytest.raises(CollectionError, match="no such record") as excinfo:
        _get(base_url, "/missing")
    assert excinfo.value.kind == "run_failed"


def test_a_failed_request_says_what_to_do_next(base_url: str) -> None:
    """Regression: a failed request carried kind and message only, so the table's error in
    meta.yaml and the survey document had no fix."""
    with pytest.raises(CollectionError) as excinfo:
        _get(base_url, "/forbidden", max_bytes=100_000)
    assert "auth.env" in (excinfo.value.fix or "")
    with pytest.raises(CollectionError) as excinfo:
        _get(base_url, "/missing")
    assert "endpoint" in (excinfo.value.fix or "")


@pytest.mark.parametrize("value", [0, -1])
def test_the_limit_must_be_positive(value: int) -> None:
    with pytest.raises(ValueError, match="max_bytes"):
        SourceEntry(name="s", url="https://x", max_bytes=value)


def test_a_retry_after_longer_than_the_cap_fails_at_once_and_says_how_long(base_url: str) -> None:
    """A spent quota can ask for hours; the survey must not sit silent through it."""
    started = time.monotonic()
    with pytest.raises(CollectionError) as excinfo:
        _get(base_url, "/quota-spent")
    assert time.monotonic() - started < 5
    assert _Handler.hits["/quota-spent"] == 1
    error = excinfo.value
    assert error.kind == "transient"
    assert "3600s" in str(error) and "3600s" in (error.fix or "")


def test_a_short_retry_after_is_waited_out_and_retried(base_url: str) -> None:
    with pytest.raises(CollectionError, match="after retries") as excinfo:
        _get(base_url, "/busy")
    assert _Handler.hits["/busy"] == 3
    assert excinfo.value.kind == "transient"


def test_a_retry_after_date_past_the_cap_fails_at_once_and_says_how_long(base_url: str) -> None:
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
    assert "retry the survey after" in (error.fix or "")


def test_a_retry_after_date_already_passed_is_retried_at_once(base_url: str) -> None:
    started = time.monotonic()
    with pytest.raises(CollectionError, match="after retries"):
        _get(base_url, "/busy-until")
    assert _Handler.hits["/busy-until"] == 3
    assert time.monotonic() - started < 2, "a passed date asks for no wait, not backoff"


@pytest.mark.parametrize("asked", ["-5", "nan", "inf"])
def test_a_retry_after_that_is_not_a_wait_falls_back_to_backoff(base_url: str, asked: str) -> None:
    with pytest.raises(CollectionError, match="after retries") as excinfo:
        _get(base_url, f"/odd-wait/{asked}")
    assert _Handler.hits[f"/odd-wait/{asked}"] == 3
    assert excinfo.value.kind == "transient"


# --- redirects ------------------------------------------------------------------------


def _redirecting(
    location: str, origin: str = "https://api.example.test"
) -> list[tuple[str, dict[str, str]]]:
    """Requests sent through a MockTransport on which ``<origin>/start`` answers 302 to
    ``location`` and any other URL answers 200: each sent request's URL and headers."""
    sent: list[tuple[str, dict[str, str]]] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        sent.append((str(request.url), dict(request.headers)))
        if request.url.path == "/start":
            return httpx2.Response(302, headers={"Location": location})
        return httpx2.Response(200, json={"ok": True})

    source = SourceEntry(name="keyed", url=origin, auth=AuthConfig(header="x-api-key"))
    requester = _answered_by(answer)
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
def test_a_redirect_to_another_origin_does_not_carry_the_sources_credential(location: str) -> None:
    """Regression: httpx2 (as httpx before it) strips only Authorization on a cross-origin
    redirect, so a key in the source's own ``auth.header`` (``x-api-key``, as Exa and Serper
    take it) went to whatever host the first one redirected to."""
    sent = _redirecting(location)
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
def test_a_redirect_within_the_origin_keeps_the_credential(origin: str, location: str) -> None:
    """The credential goes where the source's URL sends it, and only there: a redirect
    inside that origin, or its upgrade to https, still authenticates, as httpx2 keeps
    ``Authorization`` there."""
    sent = _redirecting(location, origin)
    assert sent[1][0] == "https://api.example.test/landing"
    assert sent[1][1]["x-api-key"] == "SECRET1"


# --- failures httpx2 raises that are not transport errors -------------------------------


def _answered_by(answer: Callable[[httpx2.Request], httpx2.Response]) -> HttpRequester:
    """A requester whose every request is answered by ``answer``, through a MockTransport."""
    clock = SystemClock()
    return HttpRequester(RateLimiter(clock), clock, transport=httpx2.MockTransport(answer))


def test_a_redirect_loop_is_a_classified_failure() -> None:
    """Regression: ``TooManyRedirects`` is not a transport error, so it escaped unclassified
    and its table failed with no fix."""
    requester = _answered_by(lambda request: httpx2.Response(302, headers={"Location": "/again"}))
    source = SourceEntry(name="s", url="https://api.example.test")
    with pytest.raises(CollectionError) as excinfo:
        requester.request(source, "GET", "https://api.example.test/start")
    error = excinfo.value
    assert error.kind == "run_failed" and "TooManyRedirects" in str(error)
    assert "redirect" in (error.fix or "")


def test_a_body_that_does_not_decode_is_a_classified_failure() -> None:
    """Regression: a body whose Content-Encoding lies (``DecodingError``) escaped
    unclassified."""
    requester = _answered_by(
        lambda request: httpx2.Response(
            200, headers={"Content-Encoding": "gzip"}, content=b"not gzip at all"
        ),
    )
    source = SourceEntry(name="s", url="https://api.example.test")
    with pytest.raises(CollectionError) as excinfo:
        requester.request(source, "GET", "https://api.example.test/x")
    error = excinfo.value
    assert error.kind == "run_failed" and "DecodingError" in str(error)
    assert "Content-Encoding" in (error.fix or "")


def test_a_url_that_is_not_one_is_a_config_error() -> None:
    """Regression: ``InvalidURL`` escaped unclassified; the URL is the mission's to fix."""
    requester = _answered_by(lambda request: httpx2.Response(200))
    source = SourceEntry(name="s", url="http://[::1")
    with pytest.raises(CollectionError) as excinfo:
        requester.request(source, "GET", "http://[::1/x")
    error = excinfo.value
    assert error.kind == "config" and "url:" in (error.fix or "")


# --- what a retry costs ----------------------------------------------------------------


class _CountingLimiter(RateLimiter):
    """The rate limiter, counting the tokens drawn and never waiting."""

    def __init__(self) -> None:
        super().__init__(FixedClock())
        self.drawn = 0

    def acquire(self, source: SourceEntry, deadline: Deadline | None = None) -> None:
        self.drawn += 1


def _always_busy(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(503)


def test_every_send_draws_a_token_resends_included() -> None:
    """Regression: the token was drawn once per request, before its retries, so a source
    answering 5xx took three sends for one token and its rate limit did not hold."""
    limiter = _CountingLimiter()
    clock = FixedClock()
    requester = HttpRequester(limiter, clock, transport=httpx2.MockTransport(_always_busy))
    source = SourceEntry(name="s", url="https://api.example.test")
    with pytest.raises(CollectionError, match="after retries"):
        requester.request(source, "GET", "https://api.example.test/x")
    assert limiter.drawn == 3


class _SlowRedirects:
    """The reviewer's chain: ``/0`` redirects to ``/1`` and so on to ``/6``, which answers;
    the server takes 0.8 s over each hop, on the test's clock. Records each hop's path and
    the read timeout it was sent with."""

    def __init__(self, clock: FixedClock) -> None:
        self.clock = clock
        self.hops: list[tuple[str, float]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.hops.append((request.url.path, request.extensions["timeout"]["read"]))
        self.clock.sleep(0.8)  # the server's time, passing on the test's clock
        hop = int(request.url.path.strip("/"))
        if hop < 6:
            return httpx2.Response(302, headers={"location": f"/{hop + 1}"})
        return httpx2.Response(200, json={"ok": 1})


def test_a_redirect_chain_is_bounded_by_the_timeout_and_each_hop_draws_a_token() -> None:
    """Regression: the send's budget was checked only as the body was read, and every hop
    was sent with the whole per-phase timeout, so seven hops of 0.8 s each under
    ``timeout: 1`` took 20.1 s across three sends; and only the first hop of a send drew a
    token. Now each hop is checked against what is left of the send, is sent with only that
    as its timeout, and draws a token."""
    clock = FixedClock()
    limiter = _CountingLimiter()
    chain = _SlowRedirects(clock)
    requester = HttpRequester(limiter, clock, transport=httpx2.MockTransport(chain))
    source = SourceEntry(name="s", url="https://api.example.test", timeout=1)

    with pytest.raises(CollectionError) as excinfo:
        requester.request(source, "GET", "https://api.example.test/0")

    assert excinfo.value.kind == "transient" and "timeout" in (excinfo.value.fix or "")
    paths = [path for path, _timeout in chain.hops]
    assert paths == ["/0", "/1"] * 3, "each send stops at the hop past its timeout"
    first, second = chain.hops[0][1], chain.hops[1][1]
    assert first == 1 and second == pytest.approx(0.2), "a hop is given what is left"
    assert limiter.drawn == len(paths), "every hop draws a token"


def test_a_redirect_chain_is_bounded_by_the_deadline() -> None:
    clock = FixedClock()
    deadline = Deadline(clock)
    chain = _SlowRedirects(clock)
    requester = HttpRequester(
        RateLimiter(clock), clock, deadline=deadline, transport=httpx2.MockTransport(chain)
    )
    source = SourceEntry(name="s", url="https://api.example.test", timeout=30)

    with (
        deadline.bound(2, "the survey's deadline", "raise deadline:"),
        pytest.raises(CollectionError) as excinfo,
    ):
        requester.request(source, "GET", "https://api.example.test/0")

    assert "the survey's deadline passed" in str(excinfo.value)
    assert [path for path, _timeout in chain.hops] == ["/0", "/1", "/2"]


def test_a_retry_waits_on_the_clock_port() -> None:
    """Regression: tenacity slept on the real clock, so a test clock could not see a retry's
    backoff, and nothing that reads the clock port could bound it."""
    clock = FixedClock()
    requester = HttpRequester(
        RateLimiter(clock), clock, transport=httpx2.MockTransport(_always_busy)
    )
    source = SourceEntry(name="s", url="https://api.example.test")
    started = time.monotonic()
    with pytest.raises(CollectionError, match="after retries"):
        requester.request(source, "GET", "https://api.example.test/x")
    assert clock.slept == [1.0, 2.0], "the backoff before the second and third sends"
    assert time.monotonic() - started < 1, "no real time was slept"


# --- one session per survey --------------------------------------------------------------


class _KeepAlive(http.server.BaseHTTPRequestHandler):
    """HTTP/1.1, so a client may send more than one request on a connection; records the
    client port each request came from."""

    protocol_version = "HTTP/1.1"
    ports: list[int] = []

    def do_GET(self) -> None:
        _KeepAlive.ports.append(self.client_address[1])
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


def test_a_survey_s_requests_share_one_session_which_closing_ends() -> None:
    """Regression: the requester opened a client per request, so nothing was kept between
    requests (a connection, a session a later collector type would hold). It keeps one
    session for the survey, and ``close()`` ends it."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _KeepAlive)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _KeepAlive.ports.clear()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        clock = SystemClock()
        requester = HttpRequester(RateLimiter(clock), clock)
        source = SourceEntry(name="s", url=base, timeout=10)
        requester.get(source, f"{base}/a")
        requester.get(source, f"{base}/b")
        requester.close()
        requester.get(source, f"{base}/c")
        requester.close()
    finally:
        server.shutdown()
        server.server_close()
    first, second, after_close = _KeepAlive.ports
    assert first == second, "the second request went on the first one's connection"
    assert after_close != first, "a closed session's connection is not reused"


# --- bounds in time --------------------------------------------------------------------


class _Trickle(http.server.BaseHTTPRequestHandler):
    """A 200 whose 20-byte body comes one byte every 0.25 s: within any per-read timeout."""

    def do_GET(self) -> None:
        body = b"[" + b" " * 18 + b"]"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        with contextlib.suppress(OSError):  # the client hangs up once it has waited enough
            for i in range(len(body)):
                self.wfile.write(body[i : i + 1])
                self.wfile.flush()
                time.sleep(0.25)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture(scope="module")
def trickle() -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Trickle)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_a_source_s_timeout_bounds_each_send_in_wall_time(trickle: str) -> None:
    """Regression: ``timeout`` bounded each read, not the request, so a body trickled within
    it took as long as the server liked (5 s here, any time at all in general)."""
    clock = HurriedClock()  # retries' backoff is skipped, the sends' wall time is real
    requester = HttpRequester(RateLimiter(clock), clock)
    source = SourceEntry(name="slow", url=trickle, timeout=1)
    started = time.monotonic()
    with pytest.raises(CollectionError) as excinfo:
        requester.get(source, f"{trickle}/x")
    assert time.monotonic() - started < 3 * 1 + 1.5, "three sends of at most ~1 s each"
    assert excinfo.value.kind == "transient" and "timeout" in (excinfo.value.fix or "")


def test_a_deadline_ends_a_request_whatever_its_timeout(trickle: str) -> None:
    clock = HurriedClock()
    deadline = Deadline(clock)
    requester = HttpRequester(RateLimiter(clock), clock, deadline=deadline)
    source = SourceEntry(name="slow", url=trickle, timeout=60)
    started = time.monotonic()
    with (
        deadline.bound(1.0, "table t's deadline", "raise deadline: on collector 't'"),
        pytest.raises(CollectionError) as excinfo,
    ):
        requester.get(source, f"{trickle}/x")
    assert time.monotonic() - started < 2
    assert excinfo.value.kind == "transient"
    assert "table t's deadline" in str(excinfo.value) and "deadline:" in (excinfo.value.fix or "")


def test_a_wait_past_the_deadline_is_not_waited() -> None:
    """A Retry-After, a backoff or a rate-limit wait that would end past the deadline fails
    the request at once rather than sleep into it."""
    clock = FixedClock()
    deadline = Deadline(clock)
    answer = httpx2.MockTransport(lambda r: httpx2.Response(503, headers={"Retry-After": "30"}))
    requester = HttpRequester(RateLimiter(clock), clock, deadline=deadline, transport=answer)
    source = SourceEntry(name="s", url="https://api.example.test")
    with deadline.bound(10, "the survey's deadline", "raise deadline:"):
        with pytest.raises(CollectionError, match="the survey's deadline"):
            requester.request(source, "GET", "https://api.example.test/x")
        assert clock.slept == []
        slow = SourceEntry(name="slow", url="https://api.example.test",
                           rate_limit=RateLimitConfig(rps=0.01, burst=1))  # fmt: skip
        fine = httpx2.MockTransport(lambda r: httpx2.Response(200))
        quick = HttpRequester(RateLimiter(clock), clock, deadline=deadline, transport=fine)
        quick.request(slow, "GET", "https://api.example.test/x")
        with pytest.raises(CollectionError, match="the survey's deadline"):
            quick.request(slow, "GET", "https://api.example.test/x")  # the next token: 100 s
        assert clock.slept == []

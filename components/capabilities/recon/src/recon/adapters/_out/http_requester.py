"""HttpRequester — the only adapter that imports httpx2.

Rate limiting lives here, not in collectors. Every send, a retry included, acquires a token
for its source before it goes, so no caller can forget the limit and a failing source is
not sent to faster than its limit allows. Each wait, the limit's and a retry's, is on the
clock port. Adapters construct HttpResponse domain value objects; collectors never see
httpx2.Response.

A body is read as a stream and counted against the source's ``max_bytes``, so a response
that is larger than the mission allowed is refused instead of buffered. A body that does not
end (server-sent events) is read through :meth:`HttpRequester.follow` instead, line by line as
it arrives, each line bounded by ``max_bytes``; closing it shuts its socket down, which is
what ends a read blocked on it in another thread (closing the response or the client does
not, measured on macOS against a loopback server).

Every attempt to send a request is one ``recon.http.request`` span of kind client, with the
attributes OpenTelemetry's HTTP client span conventions (semconv v1.44.0, stable) require:
``http.request.method``, ``server.address``, ``server.port``, ``url.full`` and, when there
is one, ``http.response.status_code``; ``http.request.resend_count`` on a retry; and
``error.type`` on a failure: the status code, the httpx2 exception class
(``httpx2.ConnectError``, ``httpx2.TooManyRedirects``), or ``body_too_large``. Each send
carries its span's trace context (``traceparent``) when a sink traces. ``url.full``
carries no credentials: user information, the source's ``auth.param`` and the convention's
signature parameters are ``REDACTED``, as they are in an error's message; a failed attempt's
span message is the status, the exception class or the size refusal, never the URL. A
redirect httpx2 follows stays inside its attempt's span.

A source's credential goes only to the origin its request was built for. httpx2 drops an
``Authorization`` header when a redirect leaves the origin; recon drops by the same rule every
header its caller gave but the few that carry no credential (``User-Agent``, ``Accept``,
``Cache-Control``, ``Last-Event-ID``): the source's ``auth.header`` (``x-api-key``), and each
header a ``headers_helper`` printed, whose names are known only once it has run. So a redirect
to another host, port or scheme (other than http to https on the same host) is followed
without any of them.

How httpx2 (2.10) is used, each choice checked against its source:

* **Redirects are followed** (``follow_redirects=True``; httpx2's default is not to), up to
  its default 20, and the request hook sees every hop, so the credential rule above holds,
  and each hop after the first draws a rate-limit token as a send does.
* **Timeouts.** httpx2's are per phase (connecting, each read, each write), so a body
  trickled within one, or a chain of redirects each within one, would take as long as the
  server liked. ``source.timeout`` bounds each send's wall time, every hop of its redirects
  included: each hop is checked against it before it goes and is given only what is left
  as httpx2's per-phase timeout, and the body is read against it chunk by chunk. Past it the
  send fails as a read timeout (``transient``, retried). A wait for a hop's token is the
  limiter's, not the server's, so it does not count against ``timeout``. A deadline the
  mission set (:mod:`recon.domain.deadline`) shortens all of these when less is left, and a
  wait for a token, a backoff or a Retry-After that would end past it fails the request at
  once.
* **TLS** is verified against the operating system's trust store, through ``truststore``,
  which httpx2 uses by default.
* **The environment is trusted** (``trust_env``, the default): a proxy, ``SSL_CERT_FILE``
  and ``.netrc`` apply.
* **The transport** is httpx2's own unless one is given: a test hands the requester an
  ``httpx2.MockTransport``.
* **One client per survey**, opened on the first request and closed by ``close()``; each
  request's timeout is set on the request (``build_request(timeout=...)``), and the cookie
  jar is emptied before each request, so requests share connections and nothing else.
"""

from __future__ import annotations

import contextlib
import itertools
import math
import socket
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

import httpx2
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from recon.domain.errors import CollectionError, ErrorKind
from recon.domain.http import LONG_LINE, HttpResponse, reported_url
from recon.domain.telemetry import HTTP_REQUEST, Telemetry

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from datetime import datetime

    from recon.domain.clock import Clock
    from recon.domain.deadline import Deadline
    from recon.domain.models import SourceEntry
    from recon.domain.ratelimit import RateLimiter


#: The longest Retry-After a survey waits out. A server that asks for longer (a spent daily
#: quota can ask for hours) fails the request now, saying how long it asked for.
_MAX_RETRY_AFTER_S = 60.0


def _retry_after(exc: BaseException) -> float | None:
    """The seconds a status error's Retry-After header asks for, when it gives any."""
    return exc.retry_after if isinstance(exc, _StatusError) else None


def _seconds_asked(retry_after: str, now: datetime) -> float | None:
    """A Retry-After value as seconds from ``now``: its number, or the time until its
    HTTP-date (0 once the date has passed). None when it is neither, or a number that is
    not a wait (negative, NaN, infinite)."""
    try:
        seconds = float(retry_after)
    except ValueError:
        pass
    else:
        return seconds if math.isfinite(seconds) and seconds >= 0 else None
    try:
        until = parsedate_to_datetime(retry_after)
    except ValueError:
        return None
    if until.tzinfo is None:  # a "-0000" zone: UTC, said without vouching for local time
        until = until.replace(tzinfo=UTC)
    return float(max(0, math.ceil((until - now).total_seconds())))


def _is_retryable(exc: BaseException) -> bool:
    """Retry on 429, 5xx and transport errors, unless the server asks for a longer wait
    than :data:`_MAX_RETRY_AFTER_S`."""
    wait = _retry_after(exc)
    if wait is not None and wait > _MAX_RETRY_AFTER_S:
        return False
    if isinstance(exc, httpx2.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx2.TransportError)


#: The next step after a failed request, by the kind :func:`classify_http_failure` gives.
_HTTP_FIX: dict[ErrorKind, str] = {
    "auth": "check the credential the source's auth.token or auth.headers_helper gives",
    "transient": "retry the survey later; lower the source's rate_limit if it was a 429",
    "run_failed": "check the source's url, the collector's endpoint and params against the API",
}

#: The next step after a request error that is not a transport error, by its class.
_REQUEST_FIX: dict[type[httpx2.HTTPError], str] = {
    httpx2.TooManyRedirects: "the source redirected more times than httpx2 follows, most likely "
    "in a loop: request the URL the redirects should end at, as the source's url: and the "
    "endpoint:",
    httpx2.DecodingError: "the source sent a body that does not decode as its Content-Encoding "
    "says: a retry may get a sound one; if not, the fault is the source's",
}


def classify_http_failure(exc: BaseException) -> ErrorKind:
    """What the caller should do about a failed request, once retries are spent.

    401 / 403 → ``auth`` (a new credential, not a retry); 429, 5xx and transport errors
    (timeouts, connection refused) → ``transient`` (retrying later may succeed); any other
    status, and a request error that is not a transport error (too many redirects, a body
    that does not decode) → ``run_failed``.
    """
    if isinstance(exc, httpx2.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return "auth"
        if code == 429 or code >= 500:
            return "transient"
        return "run_failed"
    if isinstance(exc, httpx2.TransportError):
        return "transient"
    return "run_failed"


def _retry_wait(retry_state: Any) -> float:
    """The server's Retry-After when it gives one, else exponential backoff."""
    wait = _retry_after(retry_state.outcome.exception())
    if wait is not None:
        return wait
    return wait_exponential(multiplier=1, min=1, max=8)(retry_state)


#: How much of an error response's body is read, to quote its start in the message.
_ERROR_SNIPPET_BYTES = 2048


class _StatusError(httpx2.HTTPStatusError):
    """A non-success status, with the start of its body (the stream is closed by the time
    the error is reported, so the part worth quoting is read first) and the seconds its
    Retry-After asked for, counted from when it arrived."""

    def __init__(self, response: httpx2.Response, snippet: str, retry_after: float | None) -> None:
        super().__init__(
            f"HTTP {response.status_code}", request=response.request, response=response
        )
        self.snippet = snippet
        self.retry_after = retry_after


_DEFAULT_PORTS = {"http": 80, "https": 443}

#: The headers a caller gives that carry no credential, so they follow a redirect anywhere.
#: Every other header it gives is held to the request's origin.
_NOT_CREDENTIALS = frozenset({"user-agent", "accept", "cache-control", "last-event-id"})


def _origin_bound(headers: dict[str, str] | None, source: SourceEntry) -> frozenset[str]:
    """The headers held to the request's origin: each the caller gave but those of
    :data:`_NOT_CREDENTIALS`, and the source's ``auth.header`` whether given or not."""
    given = {name.lower() for name in headers or {}} - _NOT_CREDENTIALS
    return frozenset(given | ({source.auth.header.lower()} if source.auth.header else set()))


class _SendBudget:
    """The wall time one send may take, from connecting to its body's last byte, every
    redirect hop included: the source's ``timeout``, or what is left of the deadline when
    that is less. httpx2's own timeouts are per phase, so each hop is checked against this
    and given what is left of it, and the body is read against it, chunk by chunk. Time
    spent waiting for a hop's rate-limit token is not the server's, so it is not counted
    against ``timeout``; the deadline counts all of it."""

    def __init__(self, clock: Clock, timeout: float, deadline: Deadline | None) -> None:
        self._clock = clock
        self._started = clock.monotonic()
        self._deadline = deadline
        self._timeout = timeout
        self._not_the_servers = 0.0

    def left(self) -> float:
        """The seconds this send may still take, never below 0."""
        own = max(0.0, self._timeout - self._spent())
        return self._deadline.limit(own) if self._deadline is not None else own

    def check(self, request: httpx2.Request) -> None:
        """Raise once the send has run past its budget: the deadline's error when the
        deadline has passed, else a read timeout, which is retried as any timeout is."""
        if self._deadline is not None:
            self._deadline.check()
        if self._spent() > self._timeout:
            raise httpx2.ReadTimeout(
                f"the response took longer than its source's timeout ({self._timeout:g}s)",
                request=request,
            )

    def waiting(self, wait: Callable[[], None]) -> None:
        """Run ``wait``, a wait of recon's own, without counting it against ``timeout``."""
        before = self._clock.monotonic()
        try:
            wait()
        finally:
            self._not_the_servers += self._clock.monotonic() - before

    def _spent(self) -> float:
        return self._clock.monotonic() - self._started - self._not_the_servers


def _send(
    client: httpx2.Client,
    request: httpx2.Request,
    source: SourceEntry,
    clock: Clock,
    budget: _SendBudget,
) -> HttpResponse:
    resp = client.send(request, stream=True)
    try:
        if not resp.is_success:
            snippet = _decode(resp, _read(resp, _ERROR_SNIPPET_BYTES, budget))
            asked = _seconds_asked(resp.headers.get("retry-after", "").strip(), clock.now())
            raise _StatusError(resp, snippet, asked)
        url = reported_url(str(request.url).split("?", 1)[0], source.auth.param)
        body = _read_bounded(resp, source, url, budget)
        return _to_domain(resp, body)
    finally:
        resp.close()


def _port(url: httpx2.URL) -> int | None:
    return url.port or _DEFAULT_PORTS.get(url.scheme)


def _same_origin(url: httpx2.URL, origin: httpx2.URL) -> bool:
    """Whether ``url`` is at ``origin``, or is its upgrade from http to https on the same
    host: where httpx2 itself keeps an ``Authorization`` header across a redirect."""
    if url.host != origin.host:
        return False
    if (url.scheme, _port(url)) == (origin.scheme, _port(origin)):
        return True
    return (origin.scheme, _port(origin), url.scheme, _port(url)) == ("http", 80, "https", 443)


def _span_attributes(
    request: httpx2.Request, source: SourceEntry, resend: int
) -> dict[str, str | int | None]:
    url = request.url
    return {
        "http.request.method": request.method,
        "server.address": url.host,
        "server.port": _port(url),
        "url.full": reported_url(str(url), source.auth.param),
        "http.request.resend_count": resend or None,
    }


def _read(resp: httpx2.Response, limit: int, budget: _SendBudget) -> bytes:
    """The first ``limit`` bytes of the body; the rest is left unread."""
    buffer = bytearray()
    for chunk in resp.iter_bytes():
        budget.check(resp.request)
        buffer += chunk
        if len(buffer) >= limit:
            break
    return bytes(buffer[:limit])


def _read_bounded(
    resp: httpx2.Response, source: SourceEntry, url: str, budget: _SendBudget
) -> bytes:
    """The whole body, or a CollectionError once it is known to exceed ``max_bytes``.

    A declared Content-Length over the limit is refused before a byte is read. Otherwise
    the count is of decoded bytes, chunk by chunk, so a small compressed body that inflates
    past the limit is refused too; the overshoot held in memory is at most one chunk.
    """
    limit = source.max_bytes
    declared = resp.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise _too_large(source, url)
    chunks: list[bytes] = []
    size = 0
    for chunk in resp.iter_bytes():
        budget.check(resp.request)
        size += len(chunk)
        if size > limit:
            raise _too_large(source, url)
        chunks.append(chunk)
    return b"".join(chunks)


class _TooLargeError(CollectionError):
    """A body over the source's ``max_bytes``."""


def _too_large(source: SourceEntry, url: str) -> CollectionError:
    return _TooLargeError(
        f"response body from {url} is larger than max_bytes ({source.max_bytes:,} bytes) "
        f"for source {source.name!r}; it was not read",
        kind="run_failed",
        fix=f"raise max_bytes on source {source.name!r} in catalog:, or narrow the request "
        "so it returns less",
    )


def _decode(resp: httpx2.Response, body: bytes) -> str:
    return body.decode(resp.encoding or "utf-8", errors="replace")


def _to_domain(resp: httpx2.Response, body: bytes) -> HttpResponse:
    """Convert a streamed httpx2.Response and its body to the domain value object."""
    content_type = resp.headers.get("content-type", "")
    return HttpResponse(
        status_code=resp.status_code,
        headers=dict(resp.headers),
        body=body,
        text=_decode(resp, body),
        url=str(resp.url),
        content_type=content_type.split(";")[0].strip(),
    )


class _LiveBody:
    """A streaming response's body, line by line as it arrives (``LiveBody``)."""

    def __init__(
        self, resp: httpx2.Response, source: SourceEntry, shown: str, max_line: int | None
    ) -> None:
        self._resp = resp
        self._source = source
        self._shown = shown
        self._max_line = max_line
        self._closed = False

    def __iter__(self) -> Iterator[str]:
        pending = b""
        skipping = False  # in a line past max_line: let go to its newline
        try:
            for chunk in self._resp.iter_bytes():
                if skipping:
                    end = chunk.find(b"\n")
                    if end == -1:
                        continue
                    skipping, chunk = False, chunk[end + 1 :]
                    yield LONG_LINE
                pending += chunk
                *lines, pending = pending.split(b"\n")
                for line in lines:
                    yield LONG_LINE if self._too_long(line) else _text(line)
                if self._too_long(pending):
                    skipping, pending = True, b""
                elif len(pending) > self._source.max_bytes:
                    raise CollectionError(
                        f"{self._shown} sent a line longer than max_bytes "
                        f"({self._source.max_bytes:,} bytes) for source {self._source.name!r}",
                        kind="run_failed",
                        fix=f"raise max_bytes on source {self._source.name!r}, if its lines are "
                        "meant to be that long",
                    )
        except httpx2.HTTPError as exc:
            if self._closed:
                return
            raise _broke_off(exc, self._shown, self._source) from exc
        if self._closed:
            return
        if skipping:
            yield LONG_LINE
        elif pending:
            yield LONG_LINE if self._too_long(pending) else _text(pending)

    def _too_long(self, line: bytes) -> bool:
        return self._max_line is not None and len(line) > self._max_line

    def close(self) -> None:
        """Shut the connection's socket down, which ends a read blocked on it in another
        thread at once (closing the response would not: that read would wait on)."""
        self._closed = True
        stream = self._resp.extensions.get("network_stream")
        sock = stream.get_extra_info("socket") if stream is not None else None
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)


def _text(line: bytes) -> str:
    return line.removesuffix(b"\r").decode("utf-8", errors="replace")


def _broke_off(exc: httpx2.HTTPError, shown: str, source: SourceEntry) -> CollectionError:
    """A followed request that failed to send, or whose body broke off: ``transient`` for a
    transport failure (a reconnect may mend it), as :func:`classify_http_failure` says."""
    kind = classify_http_failure(exc)
    fix = _REQUEST_FIX.get(type(exc), _HTTP_FIX[kind])
    if isinstance(exc, httpx2.TimeoutException):
        fix = (
            f"the source sent nothing for longer than its timeout ({source.timeout:g}s): raise "
            f"timeout: on source {source.name!r} above the longest it goes quiet"
        )
    return CollectionError(f"{shown} broke off (httpx2.{type(exc).__name__})", kind=kind, fix=fix)


class HttpRequester:
    """HTTP adapter with rate limiting and retry. Returns HttpResponse VO. ``clock`` dates a
    Retry-After given as an HTTP-date; ``telemetry`` receives a span per attempt;
    ``transport`` replaces httpx2's network transport (a test's ``MockTransport``).

    One requester serves a survey, over one session (a client, so connections are reused
    between requests to a host) that :meth:`close` ends when the survey does. Requests are
    sent one at a time; no cookie carries from one to the next."""

    def __init__(
        self,
        rate_limiter: RateLimiter,
        clock: Clock,
        telemetry: Telemetry | None = None,
        *,
        deadline: Deadline | None = None,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        self._rate_limiter = rate_limiter
        self._deadline = deadline
        self._clock = clock
        self._telemetry = telemetry if telemetry is not None else Telemetry((), clock)
        self._transport = transport
        self._client: httpx2.Client | None = None
        #: The request in hand's origin and the headers that go to that origin only
        #: (requests are sent one at a time).
        self._origin: tuple[httpx2.URL, frozenset[str]] | None = None
        #: The send in hand: its first request, its source and its budget, which every hop
        #: of its redirects is held to.
        self._sending: tuple[httpx2.Request, SourceEntry, _SendBudget] | None = None

    def close(self) -> None:
        """End the session: its connections close. A later request opens a new one."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def _session(self) -> httpx2.Client:
        """The one client this requester's requests share, opened on first use."""
        if self._client is None:
            self._client = httpx2.Client(
                follow_redirects=True,
                event_hooks={
                    "request": [self._credential_stays_at_its_origin, self._hop_within_budget]
                },
                transport=self._transport,
            )
        return self._client

    def _credential_stays_at_its_origin(self, request: httpx2.Request) -> None:
        """A request hook: each header held to the origin (:func:`_origin_bound`) is removed
        from any request, a redirect included, sent anywhere but the origin of the one the
        collector built."""
        if self._origin is None:
            return
        origin, held = self._origin
        if held and not _same_origin(request.url, origin):
            for name in held:
                request.headers.pop(name, None)

    def _hop_within_budget(self, request: httpx2.Request) -> None:
        """A request hook: every hop of a send, its redirects included, is held to the send's
        budget and given only what is left of it as its timeout; each hop after the first
        draws a token from the source's rate limit, as a send does."""
        if self._sending is None:
            return
        first, source, budget = self._sending
        budget.check(request)
        if request is not first:
            budget.waiting(lambda: self._rate_limiter.acquire(source, self._deadline))
        timeout = httpx2.Timeout(budget.left()).as_dict()
        request.extensions = {**request.extensions, "timeout": timeout}

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
        """Single HTTP request, sent up to three times. Every send, a retry included,
        draws a token from the source's rate limit first, and every wait (the limit's, a
        retry's backoff or Retry-After) is on the clock port. A body larger than
        ``source.max_bytes`` raises CollectionError (kind ``run_failed``) and is not retried.
        """
        try:
            # Merged, not passed as params=, which would replace a query the URL has.
            target = httpx2.URL(url).copy_merge_params(params or {})
        except httpx2.InvalidURL as exc:
            raise CollectionError(
                f"source {source.name!r}: its url and the collector's endpoint do not make a "
                f"URL that can be requested ({exc})",
                kind="config",
                fix="correct the source's url: and the collector's endpoint: so they join into "
                "one URL",
            ) from exc
        resends = itertools.count()
        retrying = Retrying(
            stop=stop_after_attempt(3),
            wait=_retry_wait,
            retry=retry_if_exception(_is_retryable),
            sleep=self._wait,
            reraise=True,
        )
        # Sync on purpose: a survey runs its requests one at a time, so async buys nothing.
        client = self._session()
        # Each request starts as one alone would: no cookie carries over from another.
        client.cookies.clear()
        self._origin = (target, _origin_bound(headers, source))

        def attempt() -> HttpResponse:
            if self._deadline is not None:
                self._deadline.check()
            self._rate_limiter.acquire(source, self._deadline)
            budget = _SendBudget(self._clock, source.timeout, self._deadline)
            request = client.build_request(
                method, target, headers=headers or {}, json=json_body, timeout=budget.left()
            )
            self._sending = (request, source, budget)
            return self._attempt(client, request, source, next(resends), budget)

        try:
            return retrying(attempt)
        except (httpx2.HTTPStatusError, httpx2.RequestError) as exc:
            status = ""
            snippet = ""
            if isinstance(exc, httpx2.HTTPStatusError):
                status = f" (HTTP {exc.response.status_code})"
                text = exc.snippet if isinstance(exc, _StatusError) else ""
                if text:
                    snippet = f"\nResponse body: {text[:200]}"
            elif not isinstance(exc, httpx2.TransportError):
                status = f" (httpx2.{type(exc).__name__}: {exc})"
            kind = classify_http_failure(exc)
            fix = _REQUEST_FIX.get(type(exc), _HTTP_FIX[kind])
            if isinstance(exc, httpx2.TimeoutException):
                fix = (
                    f"the source was slower than its timeout ({source.timeout:g}s): raise "
                    f"timeout: on source {source.name!r}, or retry the survey later"
                )
            wait = _retry_after(exc)
            shown = reported_url(url, source.auth.param)
            if wait is not None and wait > _MAX_RETRY_AFTER_S:
                msg = f"HTTP request refused: {shown}{status}; the server asks for {wait:g}s"
                fix = f"retry the survey after {wait:g}s; lower the source's rate_limit"
            else:
                msg = f"HTTP request failed after retries: {shown}{status}"
            raise CollectionError(msg + snippet, kind=kind, fix=fix) from exc

    def _wait(self, seconds: float) -> None:
        """A wait before a retry, on the clock; one that would end past the deadline fails
        the request now instead."""
        if self._deadline is not None:
            self._deadline.allow_wait(seconds)
        self._clock.sleep(seconds)

    def _attempt(
        self,
        client: httpx2.Client,
        request: httpx2.Request,
        source: SourceEntry,
        resend: int,
        budget: _SendBudget,
    ) -> HttpResponse:
        """One send of ``request``, as one client span."""
        attributes = _span_attributes(request, source, resend)
        with self._telemetry.span(HTTP_REQUEST, attributes, kind="client") as span:
            # Each send names its own span as the parent of whatever the server records.
            request.headers.update(self._telemetry.trace_headers(span))
            try:
                response = _send(client, request, source, self._clock, budget)
            except _StatusError as exc:
                code = exc.response.status_code
                span.set("http.response.status_code", code)
                span.fail(str(code), f"HTTP {code}")
                raise
            except httpx2.RequestError as exc:
                span.fail(f"httpx2.{type(exc).__name__}", type(exc).__name__)
                raise
            except _TooLargeError:
                span.fail("body_too_large", "the response body is larger than max_bytes")
                raise
            span.set("http.response.status_code", response.status_code)
            return response

    def get(
        self,
        source: SourceEntry,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        """Simple GET shortcut for the web collector. Same rate-limit path."""
        return self.request(source, "GET", url, headers=headers)

    @contextlib.contextmanager
    def follow(
        self,
        source: SourceEntry,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        max_line: int | None = None,
    ) -> Iterator[_LiveBody]:
        """One GET whose body is read as it arrives (``recon.domain.http.LiveBody``), for a
        body that does not end: server-sent events. A token is drawn from the source's rate
        limit first; a connect, and each read, times out after the source's ``timeout``; a
        status other than success fails it as :meth:`request` would, with no retry, since a
        stream's collector reconnects itself. The credential rule for redirects holds. With
        ``max_line``, a longer line is let go as it arrives and given as ``LONG_LINE``, so no
        more than that and a chunk of a line is held. The response is closed when the block
        ends."""
        try:
            target = httpx2.URL(url).copy_merge_params(params or {})
        except httpx2.InvalidURL as exc:
            raise CollectionError(
                f"source {source.name!r}: its url and the collector's endpoint do not make a "
                f"URL that can be requested ({exc})",
                kind="config",
                fix="correct the source's url: and the collector's endpoint: so they join into "
                "one URL",
            ) from exc
        client = self._session()
        client.cookies.clear()
        self._origin = (target, _origin_bound(headers, source))
        self._sending = None  # a stream's body has no end to hold it to
        self._rate_limiter.acquire(source, self._deadline)
        shown = reported_url(url, source.auth.param)
        request = client.build_request(
            "GET", target, headers=headers or {}, timeout=httpx2.Timeout(source.timeout)
        )
        try:
            resp = client.send(request, stream=True)
        except httpx2.HTTPError as exc:
            raise _broke_off(exc, shown, source) from exc
        try:
            if not resp.is_success:
                snippet = _decode(resp, resp.read()[:_ERROR_SNIPPET_BYTES])
                status = httpx2.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=request, response=resp
                )
                kind = classify_http_failure(status)
                raise CollectionError(
                    f"HTTP {resp.status_code} from {shown}"
                    + (f"\nResponse body: {snippet[:200]}" if snippet else ""),
                    kind=kind,
                    fix=_HTTP_FIX[kind],
                ) from status
            yield _LiveBody(resp, source, shown, max_line)
        finally:
            resp.close()

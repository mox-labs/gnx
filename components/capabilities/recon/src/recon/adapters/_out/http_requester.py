"""HttpRequester — the only adapter that imports httpx2.

Rate limiting lives here, not in collectors. Every send, a retry included, acquires a token
for its source before it goes, so no caller can forget the limit and a failing source is
not sent to faster than its limit allows. Each wait, the limit's and a retry's, is on the
clock port. Adapters construct HttpResponse domain value objects; collectors never see
httpx2.Response.

A body is read as a stream and counted against the source's ``max_bytes``, so a response
that is larger than the mission allowed is refused instead of buffered.

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
``Authorization`` header when a redirect leaves the origin; recon drops the source's own
``auth.header`` (``x-api-key``) by the same rule, so a redirect to another host, port or
scheme (other than http to https on the same host) is followed without the key.

How httpx2 (2.10) is used, each choice checked against its source:

* **Redirects are followed** (``follow_redirects=True``; httpx2's default is not to), up to
  its default 20, and the request hook sees every hop, so the credential rule above holds.
* **Timeouts are httpx2's, per phase**: ``source.timeout`` bounds connecting, each read and
  each write, not the request as a whole.
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

import itertools
import math
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

import httpx2
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from recon.domain.errors import CollectionError, ErrorKind
from recon.domain.http import HttpResponse, reported_url
from recon.domain.telemetry import HTTP_REQUEST, Telemetry

if TYPE_CHECKING:
    from datetime import datetime

    from recon.domain.clock import Clock
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
    "auth": "set the environment variable the source's auth.env names to a valid key",
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


def _send(
    client: httpx2.Client, request: httpx2.Request, source: SourceEntry, clock: Clock
) -> HttpResponse:
    resp = client.send(request, stream=True)
    try:
        if not resp.is_success:
            snippet = _decode(resp, _read(resp, _ERROR_SNIPPET_BYTES))
            asked = _seconds_asked(resp.headers.get("retry-after", "").strip(), clock.now())
            raise _StatusError(resp, snippet, asked)
        url = reported_url(str(request.url).split("?", 1)[0], source.auth.param)
        body = _read_bounded(resp, source, url)
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


def _read(resp: httpx2.Response, limit: int) -> bytes:
    """The first ``limit`` bytes of the body; the rest is left unread."""
    buffer = bytearray()
    for chunk in resp.iter_bytes():
        buffer += chunk
        if len(buffer) >= limit:
            break
    return bytes(buffer[:limit])


def _read_bounded(resp: httpx2.Response, source: SourceEntry, url: str) -> bytes:
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
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        self._rate_limiter = rate_limiter
        self._clock = clock
        self._telemetry = telemetry if telemetry is not None else Telemetry((), clock)
        self._transport = transport
        self._client: httpx2.Client | None = None
        #: The request in hand's origin and the source's credential header, which goes to
        #: that origin only (requests are sent one at a time).
        self._origin: tuple[httpx2.URL, str] | None = None

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
                event_hooks={"request": [self._credential_stays_at_its_origin]},
                transport=self._transport,
            )
        return self._client

    def _credential_stays_at_its_origin(self, request: httpx2.Request) -> None:
        """A request hook: the source's ``auth.header`` is removed from any request, a
        redirect included, sent anywhere but the origin of the one the collector built."""
        if self._origin is None:
            return
        origin, header = self._origin
        if header and not _same_origin(request.url, origin):
            request.headers.pop(header, None)

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
            sleep=self._clock.sleep,
            reraise=True,
        )
        # Sync on purpose: a survey runs its requests one at a time, so async buys nothing.
        client = self._session()
        # Each request starts as one alone would: no cookie carries over from another.
        client.cookies.clear()
        self._origin = (target, source.auth.header)

        def attempt() -> HttpResponse:
            request = client.build_request(
                method, target, headers=headers or {}, json=json_body, timeout=source.timeout
            )
            return self._attempt(client, request, source, next(resends))

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
            wait = _retry_after(exc)
            shown = reported_url(url, source.auth.param)
            if wait is not None and wait > _MAX_RETRY_AFTER_S:
                msg = f"HTTP request refused: {shown}{status}; the server asks for {wait:g}s"
                fix = f"retry the survey after {wait:g}s; lower the source's rate_limit"
            else:
                msg = f"HTTP request failed after retries: {shown}{status}"
            raise CollectionError(msg + snippet, kind=kind, fix=fix) from exc

    def _attempt(
        self, client: httpx2.Client, request: httpx2.Request, source: SourceEntry, resend: int
    ) -> HttpResponse:
        """One send of ``request``, as one client span, once the source's rate limit lets it
        go: a resend draws a token as the first send did."""
        self._rate_limiter.acquire(source)
        attributes = _span_attributes(request, source, resend)
        with self._telemetry.span(HTTP_REQUEST, attributes, kind="client") as span:
            # Each send names its own span as the parent of whatever the server records.
            request.headers.update(self._telemetry.trace_headers(span))
            try:
                response = _send(client, request, source, self._clock)
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

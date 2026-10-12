"""HttpxRequester — the only adapter that imports httpx.

Rate limiting lives here, not in collectors. Every call to `request()` acquires
a token for its source before hitting the network, so paginated collectors
can't forget to rate-limit per-page. Adapters construct HttpResponse domain
value objects; collectors never see httpx.Response.

A body is read as a stream and counted against the source's ``max_bytes``, so a response
that is larger than the mission allowed is refused instead of buffered.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from recon.domain.exceptions import CollectionError, ErrorKind
from recon.domain.http import HttpResponse

if TYPE_CHECKING:
    from recon.application.utilization import RateLimiter
    from recon.domain.models import SourceEntry


def _is_retryable(exc: BaseException) -> bool:
    """Retry on 429, 5xx, and transport errors."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


def classify_http_failure(exc: BaseException) -> ErrorKind:
    """What the caller should do about a failed request, once retries are spent.

    401 / 403 → ``auth`` (a new credential, not a retry); 429, 5xx and transport errors
    (timeouts, connection refused) → ``transient`` (retrying later may succeed); any other
    status → ``collection``.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return "auth"
        if code == 429 or code >= 500:
            return "transient"
        return "collection"
    if isinstance(exc, httpx.TransportError):
        return "transient"
    return "collection"


def _retry_wait(retry_state: Any) -> float:
    """Use retry-after header if present, else exponential backoff."""
    exc = retry_state.outcome.exception()
    if isinstance(exc, httpx.HTTPStatusError):
        retry_after = exc.response.headers.get("retry-after")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
    return wait_exponential(multiplier=1, min=1, max=8)(retry_state)


#: How much of an error response's body is read, to quote its start in the message.
_ERROR_SNIPPET_BYTES = 2048


class _StatusError(httpx.HTTPStatusError):
    """A non-success status, with the start of its body: the stream is closed by the time
    the error is reported, so the part worth quoting is read first."""

    def __init__(self, response: httpx.Response, snippet: str) -> None:
        super().__init__(
            f"HTTP {response.status_code}", request=response.request, response=response
        )
        self.snippet = snippet


@retry(
    stop=stop_after_attempt(3),
    wait=_retry_wait,
    retry=retry_if_exception(_is_retryable),
    reraise=True,
)
def _do_request(
    client: httpx.Client,
    source: SourceEntry,
    method: str,
    url: str,
    *,
    params: dict[str, str],
    headers: dict[str, str],
    json_body: dict[str, Any] | None,
) -> HttpResponse:
    with client.stream(method, url, params=params, headers=headers, json=json_body) as resp:
        if not resp.is_success:
            raise _StatusError(resp, _decode(resp, _read(resp, _ERROR_SNIPPET_BYTES)))
        body = _read_bounded(resp, source, url)
        return _to_domain(resp, body)


def _read(resp: httpx.Response, limit: int) -> bytes:
    """The first ``limit`` bytes of the body; the rest is left unread."""
    buffer = bytearray()
    for chunk in resp.iter_bytes():
        buffer += chunk
        if len(buffer) >= limit:
            break
    return bytes(buffer[:limit])


def _read_bounded(resp: httpx.Response, source: SourceEntry, url: str) -> bytes:
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


def _too_large(source: SourceEntry, url: str) -> CollectionError:
    return CollectionError(
        f"response body from {url} is larger than max_bytes ({source.max_bytes:,} bytes) "
        f"for source {source.name!r}; it was not read",
        kind="collection",
        fix=f"raise max_bytes on source {source.name!r} in catalog:, or narrow the request "
        "so it returns less",
    )


def _decode(resp: httpx.Response, body: bytes) -> str:
    return body.decode(resp.encoding or "utf-8", errors="replace")


def _to_domain(resp: httpx.Response, body: bytes) -> HttpResponse:
    """Convert a streamed httpx.Response and its body to the domain value object."""
    content_type = resp.headers.get("content-type", "")
    return HttpResponse(
        status_code=resp.status_code,
        headers=dict(resp.headers),
        body=body,
        text=_decode(resp, body),
        url=str(resp.url),
        content_type=content_type.split(";")[0].strip(),
    )


class HttpxRequester:
    """HTTP adapter with rate limiting and retry. Returns HttpResponse VO."""

    def __init__(self, rate_limiter: RateLimiter) -> None:
        self._rate_limiter = rate_limiter

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
        """Single HTTP request. Rate-limits against the source before firing.

        Paginated callers call this in a loop; each call acquires its own
        token, so rate limits are always honored at the page level. A body larger than
        ``source.max_bytes`` raises CollectionError (kind ``collection``) and is not retried.
        """
        self._rate_limiter.acquire(source)
        try:
            with httpx.Client(timeout=source.timeout, follow_redirects=True) as client:
                return _do_request(
                    client,
                    source,
                    method,
                    url,
                    params=params or {},
                    headers=headers or {},
                    json_body=json_body,
                )
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            status = ""
            snippet = ""
            if isinstance(exc, httpx.HTTPStatusError):
                status = f" (HTTP {exc.response.status_code})"
                text = exc.snippet if isinstance(exc, _StatusError) else ""
                if text:
                    snippet = f"\nResponse body: {text[:200]}"
            msg = f"HTTP request failed after retries: {url}{status}{snippet}"
            raise CollectionError(msg, kind=classify_http_failure(exc)) from exc

    def get(
        self,
        source: SourceEntry,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        """Simple GET shortcut for the web collector. Same rate-limit path."""
        return self.request(source, "GET", url, headers=headers)

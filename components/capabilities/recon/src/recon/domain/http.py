"""HTTP in the domain: the HttpResponse value object, the Requester port, and how a request
is reported.

Adapters construct HttpResponse from httpx2.Response (or any transport); collectors consume
only the domain type and talk to the network only through a Requester. httpx2 stays in one
adapter module.

A request is reported in two places, a ``recon.http.request`` span's ``url.full`` and a
plan's runs, and both redact it the one way :func:`reported_url` does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel

from recon.domain.capture import redact_query_params

if TYPE_CHECKING:
    from recon.domain.models import SourceEntry

#: What a redacted part of a reported request reads, as OpenTelemetry's conventions write it.
REDACTED = "REDACTED"

#: Query parameters whose values OpenTelemetry's conventions (semconv v1.44.0) say to redact
#: from ``url.full``: signed-URL signatures and credentials.
SIGNATURE_PARAMS = ("X-Amz-Signature", "X-Amz-Credential", "X-Amz-Security-Token", "sig",
                    "X-Goog-Signature")  # fmt: skip


def reported_url(url: str, auth_param: str = "") -> str:
    """``url`` as recon reports it: user information, the source's ``auth.param`` and the
    conventions' signature parameters :data:`REDACTED`."""
    parts = urlsplit(url)
    if "@" in parts.netloc:
        host = parts.netloc.rpartition("@")[2]
        url = urlunsplit(parts._replace(netloc=f"{REDACTED}:{REDACTED}@{host}"))
    return redact_query_params(url, [auth_param, *SIGNATURE_PARAMS], REDACTED)


class HttpResponse(BaseModel, frozen=True):
    """The minimal HTTP shape collectors need.

    `body` is raw bytes so binary content (PDFs, images) flows through
    without forced text decoding. `text` is the decoded string, eagerly
    resolved so it is safe to read multiple times.
    """

    status_code: int
    headers: dict[str, str]
    body: bytes
    text: str
    url: str
    content_type: str


class Requester(Protocol):
    """The port: given a source + HTTP parameters, return an HttpResponse.

    Rate limiting and retries are the Requester's responsibility, not the collector's.
    A failure raises CollectionError whose ``kind`` is ``transient`` (429, 5xx, transport
    errors, after retries), ``auth`` (401, 403) or ``run_failed`` (any other status, or a
    body larger than the source's ``max_bytes``).
    """

    def request(
        self,
        source: SourceEntry,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = ...,
        headers: dict[str, str] | None = ...,
        json_body: dict[str, Any] | None = ...,
    ) -> HttpResponse: ...

    def get(
        self,
        source: SourceEntry,
        url: str,
        *,
        headers: dict[str, str] | None = ...,
    ) -> HttpResponse: ...

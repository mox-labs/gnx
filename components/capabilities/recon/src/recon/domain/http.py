"""HTTP in the domain: the HttpResponse value object and the Requester port.

Adapters construct HttpResponse from httpx.Response (or any transport); collectors consume
only the domain type and talk to the network only through a Requester. httpx stays in one
adapter module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel

if TYPE_CHECKING:
    from recon.domain.models import SourceEntry


class HttpResponse(BaseModel, frozen=True, arbitrary_types_allowed=True):
    """The minimal HTTP shape collectors need.

    `body` is raw bytes so binary content (PDFs, images) flows through
    without forced text decoding. `text` is the decoded string, eagerly
    resolved so it is safe to read multiple times. `iter_lines` is a
    hook for streaming responses; it may be None when the full body was
    buffered.
    """

    status_code: int
    headers: dict[str, str]
    body: bytes
    text: str
    url: str
    content_type: str
    iter_lines: Any = None  # Iterator[str] | None — opt-in streaming


class Requester(Protocol):
    """The port: given a source + HTTP parameters, return an HttpResponse.

    Rate limiting and retries are the Requester's responsibility, not the collector's.
    A failure raises CollectionError whose ``kind`` is ``transient`` (429, 5xx, transport
    errors, after retries), ``auth`` (401, 403) or ``collection`` (any other status).
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

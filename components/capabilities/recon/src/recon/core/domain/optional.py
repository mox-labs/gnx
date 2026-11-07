"""The optional members of recon's ports and extension contracts, each a protocol of its own
(ruling G3): a member an implementation may have is read only after ``isinstance`` against its
protocol says it has it, never by looking for its name.

- :class:`SupportsClose`: ``close()``, what it holds let go once its work ends: a collector's
  session, a requester's connections, a telemetry sink's exporter, a records iterator a
  consumer stops early.
- :class:`SupportsFollow`: a requester that reads a body as it arrives (server-sent events).
- :class:`SupportsAround` and :class:`SupportsTraceHeaders`: a telemetry sink that makes each
  span current while its block runs, and one that carries a span's trace context to a server.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from recon.core.domain.http import LiveBody
    from recon.core.domain.models import SourceEntry
    from recon.core.domain.telemetry import Event


@runtime_checkable
class SupportsClose(Protocol):
    def close(self) -> None:
        """Let go of what it holds, once; called whether its work finished, failed or was
        interrupted."""
        ...


@runtime_checkable
class SupportsFollow(Protocol):
    def follow(
        self,
        source: SourceEntry,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        max_line: int | None = None,
    ) -> AbstractContextManager[LiveBody]:
        """One GET whose body is read as it arrives, for a body that does not end; see
        :class:`~recon.core.domain.http.Requester`."""
        ...


@runtime_checkable
class SupportsAround(Protocol):
    def around(self, event: Event) -> AbstractContextManager[Any] | None:
        """What to hold open while the span ``event`` starts runs (its context made current);
        None for nothing."""
        ...


@runtime_checkable
class SupportsTraceHeaders(Protocol):
    def trace_headers(self, span_id: str) -> dict[str, str]:
        """The headers that carry the span ``span_id``'s trace context to a server."""
        ...

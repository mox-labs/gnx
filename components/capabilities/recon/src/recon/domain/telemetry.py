"""Telemetry: what a survey or a query did, as events, for whoever wants to know.

recon opens spans; each span is a ``<name>.start`` and a ``<name>.end`` event sharing a
``span`` id, under the span that was open when it began. :class:`EventSink` is the port:
an event log in the archive and OpenTelemetry are the sinks recon wires. Attribute keys
are OpenTelemetry attribute names, the semantic conventions' where one exists
(``http.request.method``, ``error.type``) and ``recon.*`` otherwise, so a sink passes them
on unchanged. Metrics are derived from the events, never measured separately, so an event
log replays into the numbers an exporter saw.

**Telemetry is not data.** Nothing a survey writes depends on an event being delivered. A
sink that raises is dropped for the rest of the operation and named in ``failed``, which
the operation reports as a warning. Span ids count up from 1 and every time comes from the
clock port, so a survey against a fixed clock emits the same events twice. Attributes
carry no secret and no request or response body.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

from recon.domain.errors import ReconError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from datetime import datetime

    from recon.domain.clock import Clock

EVENT_SCHEMA = "recon.v1.event"

#: The spans recon opens, and the attributes each carries beyond ``error.type``.
SURVEY = "recon.survey"  # recon.mission, recon.archive; at its end recon.tables, .failed, .records
COLLECT = "recon.collect"  # recon.table, .collector, .collector.type, .source?; end: .records
HTTP_REQUEST = "recon.http.request"  # the HTTP client span attributes, semconv v1.44.0
QUERY = "recon.query"  # recon.mission, recon.archive; at its end recon.rows

#: OpenTelemetry's span kinds that recon uses: ``client`` for an outgoing HTTP request.
SpanKind = Literal["internal", "client"]

AttributeValue = str | bool | int | float


@dataclass(frozen=True)
class Event:
    """One event. ``status``, ``message`` and ``duration_ms`` are set on an end event only;
    ``message`` is the first line of the error, when the span failed."""

    name: str
    span: str
    parent: str | None
    time: datetime
    kind: SpanKind = "internal"
    attributes: dict[str, AttributeValue] = field(default_factory=dict)
    status: Literal["ok", "error"] | None = None
    message: str | None = None
    duration_ms: float | None = None

    def document(self) -> dict[str, Any]:
        """The event as the ``recon.v1.event`` line an event log holds."""
        return {
            "schema": EVENT_SCHEMA,
            "name": self.name,
            "time": self.time.isoformat(),
            "span": self.span,
            "parent": self.parent,
            "kind": self.kind,
            "attributes": self.attributes,
            "status": self.status,
            "message": self.message,
            "duration_ms": self.duration_ms,
        }


class EventSink(Protocol):
    def on_event(self, event: Event) -> None: ...


class Span:
    """An open span: attributes to add before it ends, and how it failed."""

    def __init__(self, attributes: Mapping[str, AttributeValue | None]) -> None:
        self.attributes: dict[str, AttributeValue] = {}
        self.error: tuple[str, str] | None = None
        for key, value in attributes.items():
            self.set(key, value)

    def set(self, key: str, value: AttributeValue | None) -> None:
        """Add an attribute; None adds nothing."""
        if value is not None:
            self.attributes[key] = value

    def fail(self, error_type: str, message: str) -> None:
        """End the span as failed: ``error_type`` is low-cardinality (an error kind, a status
        code, an exception class); ``message`` is shortened to its first line."""
        self.error = (error_type, message.split("\n", 1)[0])


class Telemetry:
    """Opens spans and hands their events to the sinks. One per operation: it is not
    thread-safe, and recon runs an operation's work on one thread."""

    def __init__(self, sinks: Sequence[EventSink], clock: Clock) -> None:
        self._sinks = list(sinks)
        self._clock = clock
        self._open: list[str] = []
        self._next_id = 1
        #: Each sink that raised and was dropped, with its error.
        self.failed: list[str] = []

    @contextmanager
    def span(
        self,
        name: str,
        attributes: Mapping[str, AttributeValue | None] | None = None,
        *,
        kind: SpanKind = "internal",
    ) -> Iterator[Span]:
        """A span around the block. An exception that leaves the block fails the span, unless
        the block already said how, and goes on."""
        span_id = str(self._next_id)
        self._next_id += 1
        parent = self._open[-1] if self._open else None
        span = Span(attributes or {})
        start = self._clock.monotonic()
        now = self._clock.now()
        self._emit(Event(f"{name}.start", span_id, parent, now, kind, dict(span.attributes)))
        self._open.append(span_id)
        try:
            yield span
        except BaseException as exc:
            if span.error is None:
                span.fail(error_type(exc), str(exc) or type(exc).__name__)
            raise
        finally:
            self._open.pop()
            if span.error is not None:
                span.set("error.type", span.error[0])
            self._emit(
                Event(
                    f"{name}.end",
                    span_id,
                    parent,
                    self._clock.now(),
                    kind,
                    dict(span.attributes),
                    status="ok" if span.error is None else "error",
                    message=None if span.error is None else span.error[1],
                    duration_ms=round((self._clock.monotonic() - start) * 1000, 3),
                )
            )

    def _emit(self, event: Event) -> None:
        for sink in list(self._sinks):
            try:
                sink.on_event(event)
            except Exception as exc:  # a sink must not fail the work it observes
                self._sinks.remove(sink)
                self.failed.append(f"{type(sink).__name__}: {type(exc).__name__}: {exc}")


def error_type(exc: BaseException) -> str:
    """``error.type`` for an exception: a recon error's kind, ``interrupted`` for Ctrl-C,
    else the exception's class."""
    if isinstance(exc, ReconError):
        return exc.kind
    if isinstance(exc, KeyboardInterrupt):
        return "interrupted"
    return type(exc).__name__

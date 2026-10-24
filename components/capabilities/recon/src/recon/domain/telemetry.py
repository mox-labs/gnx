"""Telemetry: what a survey or a query did, as events, for whoever wants to know.

recon opens spans; each span is a ``<name>.start`` and a ``<name>.end`` event sharing a
``span`` id, under the span that was open when it began. Between them, a span may report how
far its work has got, as ``<name>.progress`` events with the same ``span`` id
(:meth:`Telemetry.progress`); a sink that knows only starts and ends ignores them.
:class:`EventSink` is the port: an event log in the archive and OpenTelemetry are the sinks
recon wires. Attribute keys
are OpenTelemetry attribute names, the semantic conventions' where one exists
(``http.request.method``, ``error.type``) and ``recon.*`` otherwise, so a sink passes them
on unchanged. Metrics are derived from the events, never measured separately, so an event
log replays into the numbers an exporter saw.

**Telemetry is not data.** Nothing a survey writes depends on an event being delivered. A
sink that raises is dropped for the rest of the operation and named in ``failed``, which
the operation reports as a warning. Span ids count up from 1 and every time comes from the
clock port, so a survey against a fixed clock emits the same events twice. Attributes
carry no secret and no request or response body, and a failed span's message quotes
nothing from its error: an error's text can hold a command, a URL with its credentials or
the SQL, and an event goes wherever a sink sends it.

A sink may have three more members, each optional and read only when present, so a sink
with ``on_event`` alone is never asked for them:

* ``around(event)`` returns a context manager (or None) that is entered once the start
  ``event`` is delivered and left before the end event is: the block's work runs inside it.
  The OpenTelemetry sink makes its span current there, so a span another library opens
  during the work nests under recon's. It must not block, and it cannot swallow the work's
  exception; one that raises is dropped as a raising sink is, and is still left.
* ``trace_headers(span)`` returns the headers that carry the trace context of the open span
  ``span`` (its id) to a server, W3C ``traceparent`` and ``tracestate``; the requester sends
  every sink's, merged by ``Telemetry.trace_headers``.
* ``close()`` is called once, when the sink is dropped or when the operation ends
  (:meth:`Telemetry.close`), so it can end whatever it holds open: the event log closes its
  file, and the OpenTelemetry sink ends any span still open as failed, so it is exported.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager, suppress
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
#                            progress, at most once a second: recon.records written so far
HTTP_REQUEST = "recon.http.request"  # the HTTP client span attributes, semconv v1.44.0
MCP_CALL = "recon.mcp.call"  # mcp.method.name, gen_ai.tool.name?, recon.source
QUERY = "recon.query"  # recon.mission, recon.archive; at its end recon.rows

#: OpenTelemetry's span kinds that recon uses: ``client`` for an outgoing HTTP request or an
#: MCP call.
SpanKind = Literal["internal", "client"]

AttributeValue = str | bool | int | float


@dataclass(frozen=True)
class Event:
    """One event. ``status``, ``message`` and ``duration_ms`` are set on an end event only;
    ``message`` describes the failure, when the span failed, without quoting it."""

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
    """Where events go. ``around``, ``trace_headers`` and ``close`` are optional members,
    described above."""

    def on_event(self, event: Event) -> None: ...


class Span:
    """An open span: its ``id`` (the events' ``span``), its ``name`` and ``parent`` (the
    open span it began under, by id), attributes to add before it ends, and how it failed."""

    def __init__(
        self,
        span_id: str,
        attributes: Mapping[str, AttributeValue | None],
        *,
        name: str = "",
        parent: str | None = None,
    ) -> None:
        self.id = span_id
        self.name = name
        self.parent = parent
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
        code, an exception class); ``message`` describes the failure in recon's own words and
        quotes nothing from the error (no command, URL or SQL). It is shortened to its first
        line."""
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
        span = Span(span_id, attributes or {}, name=name, parent=parent)
        start = self._clock.monotonic()
        now = self._clock.now()
        started = Event(f"{name}.start", span_id, parent, now, kind, dict(span.attributes))
        self._emit(started)
        self._open.append(span_id)
        entered = self._enter(started)
        failure: BaseException | None = None
        try:
            yield span
        except BaseException as exc:
            failure = exc
            if span.error is None:
                span.fail(error_type(exc), type(exc).__name__)
            raise
        finally:
            self._leave(entered, failure)
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

    def progress(self, span: Span, attributes: Mapping[str, AttributeValue]) -> None:
        """Say how far the open ``span``'s work has got: a ``<name>.progress`` event with
        ``attributes``, between its start and its end. The caller says how often."""
        self._emit(
            Event(
                f"{span.name}.progress",
                span.id,
                span.parent,
                self._clock.now(),
                attributes=dict(attributes),
            )
        )

    def close(self) -> None:
        """End the operation's telemetry: close each sink that has a ``close``, and send
        nothing more. A sink whose ``close`` raises is named in ``failed``."""
        sinks, self._sinks = self._sinks, []
        for sink in sinks:
            close = getattr(sink, "close", None)
            if close is None:
                continue
            try:
                close()
            except Exception as exc:  # a sink must not fail the work it observed
                self.failed.append(f"{type(sink).__name__}: {type(exc).__name__}: {exc}")

    def trace_headers(self, span: Span) -> dict[str, str]:
        """The headers that carry ``span``'s trace context to a server, from every sink that
        traces (W3C ``traceparent``); empty when none does."""
        headers: dict[str, str] = {}
        for sink in list(self._sinks):
            given = getattr(sink, "trace_headers", None)
            if given is None:
                continue
            try:
                headers.update(given(span.id))
            except Exception as exc:  # a sink must not fail the work it observes
                self._drop(sink, exc)
        return headers

    def _emit(self, event: Event) -> None:
        for sink in list(self._sinks):
            try:
                sink.on_event(event)
            except Exception as exc:  # a sink must not fail the work it observes
                self._drop(sink, exc)

    def _enter(self, event: Event) -> list[tuple[EventSink, AbstractContextManager[Any]]]:
        """Enter each sink's ``around`` for the span ``event`` starts."""
        entered: list[tuple[EventSink, AbstractContextManager[Any]]] = []
        for sink in list(self._sinks):
            around = getattr(sink, "around", None)
            if around is None:
                continue
            try:
                manager = around(event)
                if manager is not None:
                    manager.__enter__()
                    entered.append((sink, manager))
            except Exception as exc:
                self._drop(sink, exc)
        return entered

    def _leave(
        self,
        entered: list[tuple[EventSink, AbstractContextManager[Any]]],
        failure: BaseException | None,
    ) -> None:
        """Leave what :meth:`_enter` entered, innermost first, a dropped sink's too (what it
        made current must not stay so). What a sink's exit returns is ignored: it cannot
        swallow the work's exception."""
        for sink, manager in reversed(entered):
            try:
                if failure is None:
                    manager.__exit__(None, None, None)
                else:
                    manager.__exit__(type(failure), failure, failure.__traceback__)
            except Exception as exc:
                self._drop(sink, exc)

    def _drop(self, sink: EventSink, exc: Exception) -> None:
        """Stop sending to ``sink``, name it in ``failed``, and let it end what it holds."""
        if sink not in self._sinks:
            return
        self._sinks.remove(sink)
        self.failed.append(f"{type(sink).__name__}: {type(exc).__name__}: {exc}")
        close = getattr(sink, "close", None)
        if close is not None:
            with suppress(Exception):  # already dropped and named: nothing more to say
                close()


def error_type(exc: BaseException) -> str:
    """``error.type`` for an exception: a recon error's kind, ``interrupted`` for Ctrl-C,
    else the exception's class."""
    if isinstance(exc, ReconError):
        return exc.kind
    if isinstance(exc, KeyboardInterrupt):
        return "interrupted"
    return type(exc).__name__

"""OpenTelemetry spans and metrics from matrix events. Requires ``matrix[otel]``.

**Spans.** Every ``*.start``/``*.end`` pair becomes a span, parented as the events are:
``matrix.run`` over ``matrix.component`` over whatever the component opened (``ctx.span``) or
called.
``matrix.agent.*`` becomes ``invoke_agent {name}``, following the GenAI semantic conventions.

**Metrics.** Derived from the same events, never measured separately, so an event log replays
into the numbers an exporter saw:

* ``matrix.run.duration`` (histogram, s) by status;
* ``matrix.component.duration`` (histogram, s) and ``matrix.component.count``, by component
  and status;
* ``matrix.agent.tokens`` (by agent and ``gen_ai.token.type``) and ``matrix.agent.cost_usd``;
* ``matrix.events.dropped``;
* every ``ctx.measure`` as an instrument named by the measure, of the kind it declares.

Configure the SDK (exporter, endpoint) with ``matrix.configure_telemetry`` or directly; this
observer only creates spans and records measurements.

Type URL: ``matrix.v1.observer.otel``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from matrix.domain.observer import (
    AGENT_END,
    AGENT_START,
    COMPONENT_END,
    EVENTS_DROPPED,
    MEASURE,
    RUN_END,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from matrix.domain.observer import Event

_START, _END = ".start", ".end"


class OtelObserver:
    def __init__(self) -> None:
        try:
            from opentelemetry import metrics, trace
        except ImportError as e:
            raise ImportError(
                "the otel observer requires opentelemetry. Install with: uv add 'matrix[otel]'"
            ) from e
        self._trace = trace
        self._tracer = trace.get_tracer("matrix")
        self._open: dict[str, Any] = {}
        meter = metrics.get_meter("matrix")
        self._meter = meter
        self._run_duration = meter.create_histogram("matrix.run.duration", unit="s")
        self._component_duration = meter.create_histogram("matrix.component.duration", unit="s")
        self._component_count = meter.create_counter("matrix.component.count")
        self._agent_tokens = meter.create_counter("matrix.agent.tokens", unit="{token}")
        self._agent_cost = meter.create_counter("matrix.agent.cost_usd", unit="USD")
        self._dropped = meter.create_counter("matrix.events.dropped")
        #: (measure name, kind) -> the bound method that records one value to its instrument
        self._measures: dict[tuple[str, str], Callable[..., None]] = {}

    def on_event(self, event: Event) -> None:
        if event.name.endswith(_START):
            parent = self._open.get(event.parent or "")
            context = self._trace.set_span_in_context(parent) if parent is not None else None
            self._open[event.span] = self._tracer.start_span(
                _span_name(event), context=context, attributes=_attributes(event)
            )
        elif event.name.endswith(_END):
            self._end_span(event)
        self._record(event)

    def _end_span(self, event: Event) -> None:
        span = self._open.pop(event.span, None)
        if span is None:
            return  # an end with no start: a component skipped before it ran
        for key, value in _attributes(event).items():
            span.set_attribute(key, value)
        status = event.fields.get("status")
        if event.name == AGENT_END:
            if status == "error":
                span.set_status(self._trace.StatusCode.ERROR, str(event.fields.get("error")))
        elif status in ("failed", "error"):
            span.set_status(self._trace.StatusCode.ERROR, str(status))
        span.end()

    def _record(self, event: Event) -> None:
        fields = event.fields
        status = str(fields.get("status"))
        if event.name == RUN_END:
            seconds = fields.get("duration_ms", 0) / 1000
            self._run_duration.record(seconds, {"matrix.status": status})
        elif event.name == COMPONENT_END:
            about = {"matrix.component": str(event.component), "matrix.status": status}
            self._component_count.add(1, about)
            if "duration_ms" in fields:
                self._component_duration.record(fields["duration_ms"] / 1000, about)
        elif event.name == AGENT_END:
            agent = {"gen_ai.agent.name": str(fields.get("agent"))}
            for direction in ("input", "output"):
                tokens = fields.get(f"tokens_{direction}")
                if tokens:
                    self._agent_tokens.add(tokens, {**agent, "gen_ai.token.type": direction})
            if fields.get("cost_usd"):
                self._agent_cost.add(fields["cost_usd"], agent)
        elif event.name == EVENTS_DROPPED:
            self._dropped.add(fields.get("count", 0))
        elif event.name == MEASURE:
            self._measure(event)

    def _measure(self, event: Event) -> None:
        fields = event.fields
        name, kind, value = str(fields["name"]), str(fields.get("kind")), fields["value"]
        record = self._measures.get((name, kind))
        if record is None:
            unit = fields.get("unit") or ""
            if kind == "counter":
                record = self._meter.create_counter(name, unit=unit).add
            elif kind == "histogram":
                record = self._meter.create_histogram(name, unit=unit).record
            else:
                record = self._meter.create_gauge(name, unit=unit).set
            self._measures[(name, kind)] = record
        about = {k: v for k, v in (fields.get("attrs") or {}).items() if _plain(v)}
        if event.component is not None:
            about["matrix.component"] = event.component
        record(value, about)


def _plain(value: Any) -> bool:
    return isinstance(value, str | int | float | bool)


def _span_name(event: Event) -> str:
    if event.name == AGENT_START:
        return f"invoke_agent {event.fields.get('agent')}"
    return event.name.removesuffix(_START)


def _attributes(event: Event) -> dict[str, Any]:
    names = {
        "agent": "gen_ai.agent.name",
        "model": "gen_ai.request.model",
        "tokens_input": "gen_ai.usage.input_tokens",
        "tokens_output": "gen_ai.usage.output_tokens",
    }
    root = event.name.split(".", 1)[0]
    attributes: dict[str, Any] = {}
    if event.name in (AGENT_START, AGENT_END):
        attributes["gen_ai.operation.name"] = "invoke_agent"
    if event.component is not None:
        attributes["matrix.component"] = event.component
    for key, value in event.fields.items():
        if value is None or not _plain(value):
            continue
        attributes[names.get(key, f"{root}.{key}")] = value
    return attributes

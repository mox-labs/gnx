"""OpenTelemetry spans and metrics from matrix events, on the GenAI semantic conventions.

The conventions followed are those of the OpenTelemetry GenAI semantic conventions
repository (``open-telemetry/semantic-conventions-genai``) as read on 2026-10-19: no release
tagged yet, status Development, building on semantic conventions v1.44.0. They may change;
the names here are pinned to that reading, and ``docs/reference/api.md`` lists them.

**Spans**, each timed by the events' own times and parented as the events are, under the
instrumentation scope ``matrix``:

* a run is ``invoke_workflow`` (``gen_ai.operation.name: invoke_workflow``, ``matrix.run.id``);
* a component is ``matrix.component`` (``matrix.component``): the conventions have no step;
* an agent session is ``invoke_agent {agent}`` (kind INTERNAL: the agent is invoked in this
  process), with ``gen_ai.agent.name``, ``gen_ai.request.model``, ``gen_ai.provider.name``,
  ``gen_ai.response.model``, ``gen_ai.usage.input_tokens``, ``gen_ai.usage.output_tokens``
  and, when it failed, ``error.type``;
* a tool call the runtime timed is ``execute_tool {tool}``, with ``gen_ai.tool.name`` and
  ``gen_ai.tool.call.id``;
* a component's own ``ctx.span`` is a span of that name.

**No content by default.** Span attributes come from an allowlist: the conventions' names
above, and matrix's own fields that carry no content (statuses, reasons and error kinds,
counts, times, ids). Of a component's span fields only numbers and booleans are exported,
with the ``ok``/``error`` status of its ``ctx.span``. An agent's task, any error message
(an attribute, or a span's status description) and a component's text fields are content:
exported only when ``capture_content`` is set, as the conventions ask ("SHOULD NOT capture
them by default, but SHOULD provide an option for users to opt in"). ``error.type`` is
always set when something failed.

**Metrics**, derived from the same events, so an event log replays into the numbers an
exporter saw. The conventions' names where they define one: ``gen_ai.invoke_workflow.duration``,
``gen_ai.invoke_agent.duration``, ``gen_ai.invoke_agent.tool_calls``,
``gen_ai.execute_tool.duration`` (histograms, s and ``{tool_call}``), and
``gen_ai.client.inference.usage.input_tokens`` / ``output_tokens`` (counters, ``{token}``,
``gen_ai.token.modality: unknown``, since a session's total mixes whatever it exchanged).
matrix's own where they define none: ``matrix.component.duration``,
``matrix.component.count``, ``matrix.component.retries``, ``matrix.agent.cost_usd`` and
``matrix.events.dropped``; and every ``ctx.measure`` as an instrument of its own name.

This observer creates spans and records measurements through the OpenTelemetry API, which
does nothing until an application installs an SDK: matrix as a library never installs one.
``matrix run`` does, from the ``OTEL_*`` variables, and an application calls
:func:`matrix.configure_telemetry` (or configures OpenTelemetry itself). A caller may hand
the observer its own ``tracer_provider`` and ``meter_provider`` instead of the global ones.

Config: ``capture_content`` (default false).

Type URL: ``matrix.v1.observer.otel``.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from opentelemetry import metrics, trace
from opentelemetry.trace import SpanKind, StatusCode

from matrix.domain.observer import (
    AGENT_END,
    AGENT_START,
    COMPONENT_END,
    COMPONENT_RETRY,
    EVENTS_DROPPED,
    MEASURE,
    RUN_END,
    RUN_START,
    TOOL_END,
    TOOL_START,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from matrix.domain.observer import Event

_START, _END = ".start", ".end"
SCOPE = "matrix"


def _version() -> str:
    try:
        return version("matrix")
    except PackageNotFoundError:
        return "0+unknown"


def _ns(at: datetime) -> int:
    return int(at.timestamp() * 1_000_000_000)


class OtelObserver:
    def __init__(
        self,
        *,
        tracer_provider: trace.TracerProvider | None = None,
        meter_provider: metrics.MeterProvider | None = None,
        capture_content: bool = False,
    ) -> None:
        #: Whether content (tasks, error messages, a component's text fields) is exported.
        self.capture_content = capture_content
        self._tracer = trace.get_tracer(SCOPE, _version(), tracer_provider=tracer_provider)
        self._open: dict[str, Any] = {}
        meter = metrics.get_meter(SCOPE, _version(), meter_provider=meter_provider)
        self._meter = meter
        self._workflow_duration = meter.create_histogram(
            "gen_ai.invoke_workflow.duration", unit="s", description="A run, end to end"
        )
        self._agent_duration = meter.create_histogram(
            "gen_ai.invoke_agent.duration", unit="s", description="One agent session"
        )
        self._agent_tool_calls = meter.create_histogram(
            "gen_ai.invoke_agent.tool_calls", unit="{tool_call}"
        )
        self._tool_duration = meter.create_histogram("gen_ai.execute_tool.duration", unit="s")
        self._input_tokens = meter.create_counter(
            "gen_ai.client.inference.usage.input_tokens", unit="{token}"
        )
        self._output_tokens = meter.create_counter(
            "gen_ai.client.inference.usage.output_tokens", unit="{token}"
        )
        self._component_duration = meter.create_histogram("matrix.component.duration", unit="s")
        self._component_count = meter.create_counter("matrix.component.count")
        self._component_retries = meter.create_counter("matrix.component.retries")
        self._agent_cost = meter.create_counter("matrix.agent.cost_usd", unit="USD")
        self._dropped = meter.create_counter("matrix.events.dropped")
        #: (measure name, kind) -> the bound method that records one value to its instrument
        self._measures: dict[tuple[str, str], Callable[..., None]] = {}

    def on_event(self, event: Event) -> None:
        if event.name.endswith(_START):
            parent = self._open.get(event.parent or "")
            context = trace.set_span_in_context(parent) if parent is not None else None
            self._open[event.span] = self._tracer.start_span(
                _span_name(event),
                context=context,
                kind=SpanKind.INTERNAL,
                attributes=_attributes(event, content=self.capture_content),
                start_time=_ns(event.time),
            )
        elif event.name.endswith(_END):
            self._end_span(event)
        self._record(event)

    def _end_span(self, event: Event) -> None:
        span = self._open.pop(event.span, None)
        if span is None:
            return  # an end with no start: a component skipped before it ran
        for key, value in _attributes(event, content=self.capture_content).items():
            span.set_attribute(key, value)
        error = _error_type(event)
        if error is not None:
            span.set_attribute("error.type", error)
            message = event.fields.get("error") if self.capture_content else None
            span.set_status(StatusCode.ERROR, str(message or error))
        span.end(end_time=_ns(event.time))

    def _record(self, event: Event) -> None:
        fields = event.fields
        error = _error_type(event)
        if event.name == RUN_END:
            about = {"gen_ai.operation.name": "invoke_workflow", "matrix.status": _s(fields)}
            if error is not None:
                about["error.type"] = error
            self._workflow_duration.record(fields.get("duration_ms", 0) / 1000, about)
        elif event.name == COMPONENT_END:
            about = {"matrix.component": str(event.component), "matrix.status": _s(fields)}
            self._component_count.add(1, about)
            if "duration_ms" in fields:
                self._component_duration.record(fields["duration_ms"] / 1000, about)
        elif event.name == COMPONENT_RETRY:
            self._component_retries.add(
                1, {"matrix.component": str(event.component), "error.type": str(fields["kind"])}
            )
        elif event.name == AGENT_END:
            self._agent(event, error)
        elif event.name == TOOL_END:
            about = {
                "gen_ai.tool.name": str(fields.get("tool")),
                "gen_ai.agent.name": str(fields.get("agent")),
            }
            if error is not None:
                about["error.type"] = error
            self._tool_duration.record(fields.get("duration_ms", 0) / 1000, about)
        elif event.name == EVENTS_DROPPED:
            self._dropped.add(fields.get("count", 0))
        elif event.name == MEASURE:
            self._measure(event)

    def _agent(self, event: Event, error: str | None) -> None:
        fields = event.fields
        about: dict[str, Any] = {"gen_ai.agent.name": str(fields.get("agent"))}
        for key, name in (
            ("provider", "gen_ai.provider.name"),
            ("model", "gen_ai.response.model"),
        ):
            if fields.get(key):
                about[name] = str(fields[key])
        timed = dict(about)
        if error is not None:
            timed["error.type"] = error
        self._agent_duration.record(fields.get("duration_ms", 0) / 1000, timed)
        if "tool_calls" in fields:
            self._agent_tool_calls.record(fields["tool_calls"], about)
        tokens = {**about, "gen_ai.token.modality": "unknown"}
        if fields.get("tokens_input"):
            self._input_tokens.add(fields["tokens_input"], tokens)
        if fields.get("tokens_output"):
            self._output_tokens.add(fields["tokens_output"], tokens)
        if fields.get("cost_usd"):
            self._agent_cost.add(fields["cost_usd"], about)

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


def _s(fields: dict[str, Any]) -> str:
    return str(fields.get("status"))


def _plain(value: Any) -> bool:
    return isinstance(value, str | int | float | bool)


def _error_type(event: Event) -> str | None:
    """``error.type`` for an ending that failed: the classified reason or kind, else the
    status that says it failed."""
    fields = event.fields
    status = fields.get("status")
    if status not in ("failed", "error"):
        return None
    for key in ("reason", "kind"):
        if fields.get(key):
            return str(fields[key])
    return str(status) if event.name != TOOL_END else "tool_error"


def _span_name(event: Event) -> str:
    if event.name == RUN_START:
        return "invoke_workflow"
    if event.name == AGENT_START:
        return f"invoke_agent {event.fields.get('agent')}"
    if event.name == TOOL_START:
        return f"execute_tool {event.fields.get('tool')}"
    return event.name.removesuffix(_START)


#: matrix's agent and tool event fields under their conventions' names.
_GENAI = {
    "agent": "gen_ai.agent.name",
    "tokens_input": "gen_ai.usage.input_tokens",
    "tokens_output": "gen_ai.usage.output_tokens",
    "provider": "gen_ai.provider.name",
    "tool": "gen_ai.tool.name",
    "call_id": "gen_ai.tool.call.id",
}


#: matrix's own fields that carry no content, exported as ``matrix.<key>``: statuses, reasons
#: and error kinds, counts, times and the ids matrix made. ``task`` and ``error`` are content.
_MATRIX = frozenset(
    {
        "status",
        "reason",
        "kind",
        "abandoned",
        "attempts",
        "rows",
        "duration_ms",
        "reused_from",
        "family",
        "tool_calls",
        "cost_usd",
    }
)
#: The statuses matrix gives a component's ``ctx.span`` at its end.
_SPAN_STATUSES = frozenset({"ok", "error"})


def _no_content(root: str, key: str, value: Any) -> bool:
    """Whether a field may leave without the opt-in: one of matrix's own listed above, or a
    component's number, boolean, or ``ctx.span`` status. A string is content."""
    if root == "matrix":
        return key in _MATRIX
    return not isinstance(value, str) or (key == "status" and value in _SPAN_STATUSES)


def _attributes(event: Event, *, content: bool = False) -> dict[str, Any]:
    """The span attributes for ``event``: the allowlist, or every plain field with
    ``content``."""
    root = event.name.split(".", 1)[0]
    attributes: dict[str, Any] = {}
    if event.name in (RUN_START, RUN_END):
        attributes["gen_ai.operation.name"] = "invoke_workflow"
        attributes["matrix.run.id"] = str(event.run_id)
    elif event.name in (AGENT_START, AGENT_END):
        attributes["gen_ai.operation.name"] = "invoke_agent"
    elif event.name in (TOOL_START, TOOL_END):
        attributes["gen_ai.operation.name"] = "execute_tool"
    if event.component is not None:
        attributes["matrix.component"] = event.component
    for key, value in event.fields.items():
        if value is None or not _plain(value):
            continue
        if event.name == AGENT_START and key == "model":
            attributes["gen_ai.request.model"] = value
        elif event.name == AGENT_END and key == "model":
            attributes["gen_ai.response.model"] = value
        elif key in _GENAI and event.name in (AGENT_START, AGENT_END, TOOL_START, TOOL_END):
            attributes[_GENAI[key]] = value
        elif content or _no_content(root, key, value):
            attributes[f"{root}.{key}"] = value
    return attributes

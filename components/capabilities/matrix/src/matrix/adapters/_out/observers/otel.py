"""OpenTelemetry spans and metrics from matrix events, on the GenAI semantic conventions.

The conventions followed are those of the OpenTelemetry GenAI semantic conventions
repository (``open-telemetry/semantic-conventions-genai``) as read on 2026-10-19: no release
tagged yet, status Development, building on semantic conventions v1.44.0. They may change;
the names here are pinned to that reading, and ``docs/reference/api.md`` lists them.

**Spans**, under the instrumentation scope ``matrix``, each opened through the observer's
``around``: in the task doing the work, timed by the events' own times, and **current** in
that task while the work goes on, so a span a library opens inside it (an instrumented HTTP
client, a model runtime's own) is its child in the same trace. A span ends when its work
does, from the end event matrix hands ``around``, so an end event the channel drops, or a
channel that closes before it drained, leaves no span open. An event a component emits with
``ctx.emit`` opens no span, whatever its name ends with.

* a run is ``invoke_workflow`` (``gen_ai.operation.name: invoke_workflow``, ``matrix.run.id``),
  the child of whatever span was current where the run was awaited;
* a component is ``matrix.component`` (``matrix.component``): the conventions have no step;
* an agent session is ``invoke_agent {agent}`` (kind INTERNAL: the agent is invoked in this
  process), with ``gen_ai.agent.name``, ``gen_ai.request.model``, ``gen_ai.provider.name``,
  ``gen_ai.response.model``, ``gen_ai.usage.input_tokens``, ``gen_ai.usage.output_tokens``
  and, when it failed or was cancelled, ``error.type``;
* a model call a model step makes is ``matrix.model`` (the conventions have no operation for
  one logical call, which may span retries and fallbacks), with ``gen_ai.request.model``,
  ``gen_ai.response.model``, ``gen_ai.provider.name``, ``matrix.finish_reason`` and
  ``error.type``. It carries no usage: the inference spans and counters of each attempt are
  the model runtime's (hardline's ``chat`` spans), which nest under it;
* a tool call the runtime timed is ``execute_tool {tool}``, with ``gen_ai.tool.name`` and
  ``gen_ai.tool.call.id``;
* a deferred release is ``matrix.release``;
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

**One owner per token.** These counters count agent sessions only, whose tokens no model
runtime's call carries. A model call's tokens are counted by the model runtime that made it
(hardline counts each attempt, on the same instruments), never here: matrix keeps them on the
call's session row, and its ``matrix.model`` span carries no usage. An agent runtime whose
model calls something else counts already (the ``pydantic-ai`` runtime's go through hardline)
declares it, ``usage_counted_by``, which the session's end event carries; such a session's
tokens are not added here, and stay on its span and its session row. So nothing is counted
twice, and no runtime is told apart by its name.
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

from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from opentelemetry import context, metrics, trace
from opentelemetry.trace import SpanKind, StatusCode

from matrix.domain.observer import (
    AGENT_END,
    AGENT_START,
    COMPONENT_END,
    COMPONENT_RETRY,
    EVENTS_DROPPED,
    MEASURE,
    MODEL_END,
    MODEL_START,
    RUN_END,
    RUN_START,
    TOOL_END,
    TOOL_START,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from datetime import datetime

    from matrix.domain.observer import Event

_START = ".start"
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

    @contextmanager
    def around(self, event: Event) -> Iterator[Callable[[Event], None]]:
        """The span of the work ``event`` starts, current in this task until the work ends:
        the child of the span current here, which a span opened inside the work is the
        child of. Ended when the block exits, with the facts of the end event handed to
        what this gives back."""
        span = self._tracer.start_span(
            _span_name(event),
            kind=SpanKind.INTERNAL,
            attributes=_attributes(event, content=self.capture_content),
            start_time=_ns(event.time),
        )
        token = context.attach(trace.set_span_in_context(span))
        ended: list[Event] = []
        try:
            yield ended.append
        finally:
            context.detach(token)
            self._end(span, ended[-1] if ended else None)

    def _end(self, span: Any, event: Event | None) -> None:
        """End ``span`` with what its end ``event`` says; with none (the work ended without
        one), now."""
        if event is None:
            span.end()
            return
        for key, value in _attributes(event, content=self.capture_content).items():
            span.set_attribute(key, value)
        error = _error_type(event)
        if error is not None:
            span.set_attribute("error.type", error)
            message = event.fields.get("error") if self.capture_content else None
            span.set_status(StatusCode.ERROR, str(message or error))
        span.end(end_time=_ns(event.time))

    def on_event(self, event: Event) -> None:
        """Metrics: spans are made through :meth:`around`, never from queued events."""
        self._record(event)

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
        if fields.get("cost_usd"):
            self._agent_cost.add(fields["cost_usd"], about)
        if fields.get("usage_counted_by"):
            return  # its model calls' tokens are counted where they were made
        tokens = {**about, "gen_ai.token.modality": "unknown"}
        if fields.get("tokens_input"):
            self._input_tokens.add(fields["tokens_input"], tokens)
        if fields.get("tokens_output"):
            self._output_tokens.add(fields["tokens_output"], tokens)

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
    """``error.type`` for an ending that failed or was cancelled: the classified reason or
    kind, else the status that says so."""
    fields = event.fields
    status = fields.get("status")
    if status not in ("failed", "error", "cancelled"):
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
#: The events whose fields the conventions name.
_CONVENTIONAL = frozenset({AGENT_START, AGENT_END, MODEL_START, MODEL_END, TOOL_START, TOOL_END})


#: matrix's own fields that carry no content, exported as ``matrix.<key>``: statuses, reasons
#: and error kinds, counts, times, the ids matrix made, and the names of a model step's runtime
#: and of the registry row that answered. ``task`` and ``error`` are content.
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
        "runtime",
        "name",
        "finish_reason",
        "usage_counted_by",
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
        if event.name in (AGENT_START, MODEL_START) and key == "model":
            attributes["gen_ai.request.model"] = value
        elif event.name in (AGENT_END, MODEL_END) and key == "model":
            attributes["gen_ai.response.model"] = value
        elif key in _GENAI and event.name in _CONVENTIONAL:
            attributes[_GENAI[key]] = value
        elif content or _no_content(root, key, value):
            attributes[f"{root}.{key}"] = value
    return attributes

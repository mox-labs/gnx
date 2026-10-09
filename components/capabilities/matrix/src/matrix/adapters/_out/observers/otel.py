"""OpenTelemetry spans from matrix events. Requires ``matrix[otel]``.

``run.start``/``run.end`` become a ``matrix.run`` span; ``member.*`` a ``matrix.member`` span
under it; ``agent.*`` an ``invoke_agent {name}`` span following the GenAI semantic
conventions. Configure the SDK (exporter, endpoint) with ``matrix.configure_telemetry`` or
directly; this observer only creates spans.

Type URL: ``matrix.v1.observer.otel``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from matrix.domain.observer import Event

_STARTS = {"run.start": "run", "member.start": "member", "agent.start": "agent"}
_ENDS = {"run.end", "member.end", "agent.end"}


class OtelObserver:
    def __init__(self) -> None:
        try:
            from opentelemetry import trace
        except ImportError as e:
            raise ImportError(
                "the otel observer requires opentelemetry. Install with: uv add 'matrix[otel]'"
            ) from e
        self._trace = trace
        self._tracer = trace.get_tracer("matrix")
        self._open: dict[str, Any] = {}

    def on_event(self, event: Event) -> None:
        if event.name in _STARTS:
            parent = self._open.get(event.parent or "")
            context = self._trace.set_span_in_context(parent) if parent is not None else None
            self._open[event.span] = self._tracer.start_span(
                _span_name(event), context=context, attributes=_attributes(event)
            )
        elif event.name in _ENDS:
            span = self._open.pop(event.span, None)
            if span is None:
                return
            for key, value in _attributes(event).items():
                span.set_attribute(key, value)
            status = event.fields.get("status")
            if status not in (None, "ok", "completed") and event.name != "agent.end":
                span.set_status(self._trace.StatusCode.ERROR, str(status))
            elif event.name == "agent.end" and status == "error":
                span.set_status(self._trace.StatusCode.ERROR, str(event.fields.get("error")))
            span.end()


def _span_name(event: Event) -> str:
    if event.name == "agent.start":
        return f"invoke_agent {event.fields.get('agent')}"
    return f"matrix.{_STARTS[event.name]}"


def _attributes(event: Event) -> dict[str, Any]:
    fields = event.fields
    names = {
        "agent": "gen_ai.agent.name",
        "model": "gen_ai.request.model",
        "tokens_input": "gen_ai.usage.input_tokens",
        "tokens_output": "gen_ai.usage.output_tokens",
    }
    attributes: dict[str, Any] = {}
    if event.name.startswith("agent."):
        attributes["gen_ai.operation.name"] = "invoke_agent"
    for key, value in fields.items():
        if value is None or not isinstance(value, str | int | float | bool):
            continue
        attributes[names.get(key, f"matrix.{key}")] = value
    return attributes

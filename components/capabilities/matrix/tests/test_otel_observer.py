"""The otel observer turns matrix events into nested spans and into metrics."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry import trace  # noqa: E402
from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

from matrix import Executor, Inputs, RunContext, wire  # noqa: E402
from matrix.adapters._out.observers.otel import OtelObserver  # noqa: E402

# Providers of the tests' own, handed to each observer: nothing here installs a global one, so
# no test depends on which suite ran first in the process.
_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))
_READER = InMemoryMetricReader()
_METERS = MeterProvider(metric_readers=[_READER])


def _observer() -> OtelObserver:
    return OtelObserver(tracer_provider=_PROVIDER, meter_provider=_METERS)


class Emit:
    requires: tuple[str, ...] = ()
    provides = ("acme.v1.text",)

    async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
        return {"acme.v1.text": "x"}


class Probe:
    requires: tuple[str, ...] = ()
    provides = ("acme.v1.text",)

    async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
        async with ctx.span("acme.probe"):
            ctx.measure("acme.rss", 7.0, unit="By", host="a")
            ctx.measure("acme.requests", 3, kind="counter")
            ctx.measure("acme.latency", 0.25, unit="s", kind="histogram")
        return {"acme.v1.text": "x"}


def _points() -> dict[str, list[Any]]:
    data = _READER.get_metrics_data()
    found: dict[str, list[Any]] = {}
    for resource in data.resource_metrics if data else ():
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                found.setdefault(metric.name, []).extend(metric.data.data_points)
    return found


async def test_run_and_component_spans_nest() -> None:
    _EXPORTER.clear()
    await Executor([_observer()]).run(wire({"e": Emit()}))
    spans = {s.name: s for s in _EXPORTER.get_finished_spans()}
    assert {"invoke_workflow", "matrix.component"} <= set(spans)
    component, run = spans["matrix.component"], spans["invoke_workflow"]
    assert component.parent is not None and component.parent.span_id == run.context.span_id
    assert component.attributes["matrix.component"] == "e"
    assert run.attributes["gen_ai.operation.name"] == "invoke_workflow"
    assert run.attributes["matrix.status"] == "completed"
    assert run.instrumentation_scope.name == "matrix"


async def test_a_span_a_component_opens_nests_under_it() -> None:
    _EXPORTER.clear()
    await Executor([_observer()]).run(wire({"p": Probe()}))
    spans = {s.name: s for s in _EXPORTER.get_finished_spans()}
    probe, component = spans["acme.probe"], spans["matrix.component"]
    assert probe.parent is not None and probe.parent.span_id == component.context.span_id
    assert probe.attributes["acme.status"] == "ok"


async def test_metrics_come_from_the_events() -> None:
    await Executor([_observer()]).run(wire({"p": Probe()}))
    points = _points()

    def about(name: str, **attributes: Any) -> list[Any]:
        return [
            p
            for p in points.get(name, [])
            if all(p.attributes.get(k) == v for k, v in attributes.items())
        ]

    (ended,) = about(
        "matrix.component.count", **{"matrix.component": "p", "matrix.status": "completed"}
    )
    assert ended.value >= 1
    assert about("matrix.component.duration", **{"matrix.component": "p"})
    assert about("gen_ai.invoke_workflow.duration", **{"matrix.status": "completed"})
    (gauge,) = about("acme.rss", host="a")
    assert gauge.value == 7.0 and gauge.attributes["matrix.component"] == "p"
    (counter,) = about("acme.requests")
    assert counter.value >= 3
    (histogram,) = about("acme.latency")
    assert histogram.sum >= 0.25


class _Session:
    """A runtime that reports what the Claude runtime reports: a provider, and tool calls it
    saw start and end."""

    def check(self, definition: Any) -> None:
        return None

    async def run(self, definition: Any, task: str) -> Any:
        from matrix import AgentResponse

        return AgentResponse(
            content="done",
            tokens_input=40,
            tokens_output=9,
            provider="anthropic",
            model="claude-x",
            tool_calls=(
                {
                    "name": "Read",
                    "id": "toolu_1",
                    "input": {},
                    "started": "2026-10-19T10:00:00+00:00",
                    "ended": "2026-10-19T10:00:02+00:00",
                    "error": False,
                },
                {"name": "Grep", "input": {}},  # not timed: no span
            ),
        )


class _Fails:
    def check(self, definition: Any) -> None:
        return None

    async def run(self, definition: Any, task: str) -> Any:
        from matrix import AgentRuntimeError

        raise AgentRuntimeError("slow down", reason="rate_limited")


class _Asks:
    requires: tuple[str, ...] = ()
    provides = ("acme.v1.text",)

    def __init__(self, agent: Any) -> None:
        self.agent = agent

    async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
        return {"acme.v1.text": (await self.agent.run("go")).content}


async def test_an_agent_session_is_invoke_agent_and_its_tools_execute_tool() -> None:
    from matrix import AgentDefinition, BoundAgent

    observer = _observer()
    agent = BoundAgent(
        AgentDefinition(name="rev", system_prompt="p", model="claude-x"),
        _Session(),
        observers=[observer],
    )
    _EXPORTER.clear()
    await Executor([observer]).run(wire({"ask": _Asks(agent)}))
    spans = {s.name: s for s in _EXPORTER.get_finished_spans()}
    invoked, tool = spans["invoke_agent rev"], spans["execute_tool Read"]
    assert "execute_tool Grep" not in spans
    assert invoked.parent is not None
    assert invoked.parent.span_id == spans["matrix.component"].context.span_id
    for key, value in {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": "rev",
        "gen_ai.request.model": "claude-x",
        "gen_ai.response.model": "claude-x",
        "gen_ai.provider.name": "anthropic",
        "gen_ai.usage.input_tokens": 40,
        "gen_ai.usage.output_tokens": 9,
    }.items():
        assert invoked.attributes[key] == value, key
    assert tool.parent is not None and tool.parent.span_id == invoked.context.span_id
    assert tool.attributes["gen_ai.tool.name"] == "Read"
    assert tool.attributes["gen_ai.tool.call.id"] == "toolu_1"
    assert (tool.end_time - tool.start_time) == 2_000_000_000  # timed as the runtime saw it

    points = _points()
    assert any(
        p.attributes.get("gen_ai.agent.name") == "rev"
        for p in points["gen_ai.invoke_agent.duration"]
    )
    tokens = points["gen_ai.client.inference.usage.input_tokens"]
    assert any(p.attributes.get("gen_ai.provider.name") == "anthropic" for p in tokens)
    assert points["gen_ai.execute_tool.duration"]


async def test_a_failed_session_carries_its_error_type() -> None:
    from matrix import AgentDefinition, BoundAgent, RunError

    observer = _observer()
    agent = BoundAgent(AgentDefinition(name="hurried"), _Fails(), observers=[observer])
    _EXPORTER.clear()
    with pytest.raises(RunError):
        await Executor([observer]).run(wire({"ask": _Asks(agent)}))
    spans = {s.name: s for s in _EXPORTER.get_finished_spans()}
    failed = spans["invoke_agent hurried"]
    assert failed.attributes["error.type"] == "rate_limited"
    assert failed.status.status_code == trace.StatusCode.ERROR

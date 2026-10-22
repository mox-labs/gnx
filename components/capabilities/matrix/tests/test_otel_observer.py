"""The otel observer turns matrix events into nested spans and into metrics."""

from __future__ import annotations

import contextlib
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


async def test_a_cancelled_session_ends_cancelled_and_its_span_carries_an_error_type() -> None:
    import asyncio

    from matrix import AgentDefinition, BoundAgent, RecordingObserver

    started = asyncio.Event()

    class Slow:
        def check(self, definition: AgentDefinition) -> None:
            pass

        async def run(self, definition: AgentDefinition, task: str) -> Any:
            started.set()
            await asyncio.Event().wait()

    observer, recorder = _observer(), RecordingObserver()
    agent = BoundAgent(AgentDefinition(name="slow"), Slow(), observers=[observer, recorder])
    _EXPORTER.clear()
    session = asyncio.ensure_future(agent.run("task"))
    await started.wait()
    session.cancel()
    with pytest.raises(asyncio.CancelledError):
        await session
    (end,) = [e for e in recorder.events if e.name == "matrix.agent.end"]
    assert end.fields["status"] == "cancelled"
    (span,) = [s for s in _EXPORTER.get_finished_spans() if s.name == "invoke_agent slow"]
    assert span.attributes["error.type"] == "cancelled"
    assert span.status.status_code == trace.StatusCode.ERROR


_SECRET = "SECRET"


class _Refuses:
    """A runtime whose error message carries what the task said."""

    def check(self, definition: Any) -> None:
        return None

    async def run(self, definition: Any, task: str) -> Any:
        from matrix import AgentRuntimeError

        raise AgentRuntimeError(f"refused: {task}", reason="refused")


class _Leaky:
    """Content in every place a component can put it: a span's fields, an agent's task, an
    agent's error, and a release's error."""

    requires: tuple[str, ...] = ()
    provides = ("acme.v1.text",)

    def __init__(self, agent: Any, refuses: Any) -> None:
        self.agent, self.refuses = agent, refuses

    async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
        from matrix import AgentRuntimeError

        def release() -> None:
            raise RuntimeError(f"{_SECRET} release")

        ctx.defer(release)
        async with ctx.span(
            "acme.fetch", url=f"https://user:{_SECRET}@host/x", tool=_SECRET, rows=3, cached=True
        ):
            await self.agent.run(f"{_SECRET} task")
            with contextlib.suppress(AgentRuntimeError):
                await self.refuses.run(f"{_SECRET} task")
        return {"acme.v1.text": "x"}


async def _leaky_run(observer: OtelObserver) -> dict[str, Any]:
    from matrix import AgentDefinition, BoundAgent

    agent = BoundAgent(AgentDefinition(name="rev"), _Session(), observers=[observer])
    refuses = BoundAgent(AgentDefinition(name="shy"), _Refuses(), observers=[observer])
    _EXPORTER.clear()
    await Executor([observer]).run(wire({"leaky": _Leaky(agent, refuses)}))
    return {s.name: s for s in _EXPORTER.get_finished_spans()}


async def test_no_content_leaves_on_a_span_by_default() -> None:
    spans = await _leaky_run(_observer())
    leaked = {
        (name, key)
        for name, span in spans.items()
        for key, value in {**span.attributes, "status": span.status.description}.items()
        if _SECRET in str(value)
    }
    assert leaked == set()
    fetch = spans["acme.fetch"]
    # a component's numbers and booleans, and its span's own status, carry no content
    assert fetch.attributes["acme.rows"] == 3
    assert fetch.attributes["acme.cached"] is True
    assert fetch.attributes["acme.status"] == "ok"
    assert "gen_ai.tool.name" not in fetch.attributes  # a component's field is not a convention's
    refused = spans["invoke_agent shy"]
    assert refused.attributes["error.type"] == "refused"  # the type stays; its text does not
    assert refused.status.status_code == trace.StatusCode.ERROR
    assert spans["matrix.release"].attributes["error.type"] == "failed"


async def test_content_leaves_on_a_span_only_when_asked_for() -> None:
    observer = OtelObserver(tracer_provider=_PROVIDER, meter_provider=_METERS, capture_content=True)
    spans = await _leaky_run(observer)
    assert spans["invoke_agent rev"].attributes["matrix.task"] == f"{_SECRET} task"
    refused = spans["invoke_agent shy"]
    assert refused.attributes["matrix.error"] == f"refused: {_SECRET} task"
    assert refused.status.description == f"refused: {_SECRET} task"
    assert refused.attributes["error.type"] == "refused"
    assert spans["acme.fetch"].attributes["acme.url"] == f"https://user:{_SECRET}@host/x"
    assert _SECRET in str(spans["matrix.release"].status.description)

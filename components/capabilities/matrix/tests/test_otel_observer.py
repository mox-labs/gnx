"""The otel observer turns matrix events into nested spans and into metrics."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry import metrics, trace  # noqa: E402
from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

from matrix import Executor, Flow, Inputs, Member, compile_flow  # noqa: E402
from matrix.adapters._out.observers.otel import OtelObserver  # noqa: E402

_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))
trace.set_tracer_provider(_PROVIDER)
_READER = InMemoryMetricReader()
metrics.set_meter_provider(MeterProvider(metric_readers=[_READER]))


class Emit:
    requires: dict[str, Any] = {}
    provides = {"out": "acme.v1.text"}

    async def run(self, inputs: Inputs) -> dict[str, Any]:
        return {"out": "x"}


class Probe:
    requires: dict[str, Any] = {}
    provides = {"out": "acme.v1.text"}

    async def run(self, inputs: Inputs) -> dict[str, Any]:
        ctx = inputs.context
        async with ctx.span("acme.probe"):
            ctx.measure("acme.rss", 7.0, unit="By", host="a")
            ctx.measure("acme.requests", 3, kind="counter")
            ctx.measure("acme.latency", 0.25, unit="s", kind="histogram")
        return {"out": "x"}


def _points() -> dict[str, list[Any]]:
    data = _READER.get_metrics_data()
    found: dict[str, list[Any]] = {}
    for resource in data.resource_metrics if data else ():
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                found.setdefault(metric.name, []).extend(metric.data.data_points)
    return found


async def test_run_and_member_spans_nest() -> None:
    _EXPORTER.clear()
    compiled = compile_flow(Flow("traced", (Member("e", Emit(), {"out": "t"}),)))
    await Executor([OtelObserver()]).run(compiled)
    spans = {s.name: s for s in _EXPORTER.get_finished_spans()}
    assert {"matrix.run", "matrix.member"} <= set(spans)
    member, run = spans["matrix.member"], spans["matrix.run"]
    assert member.parent is not None and member.parent.span_id == run.context.span_id
    assert member.attributes["matrix.member"] == "e"
    assert run.attributes["matrix.status"] == "completed"


async def test_a_component_span_nests_under_its_member() -> None:
    _EXPORTER.clear()
    compiled = compile_flow(Flow("probed", (Member("p", Probe(), {"out": "t"}),)))
    await Executor([OtelObserver()]).run(compiled)
    spans = {s.name: s for s in _EXPORTER.get_finished_spans()}
    probe, member = spans["acme.probe"], spans["matrix.member"]
    assert probe.parent is not None and probe.parent.span_id == member.context.span_id
    assert probe.attributes["acme.status"] == "ok"


async def test_metrics_come_from_the_events() -> None:
    compiled = compile_flow(Flow("measured", (Member("p", Probe(), {"out": "t"}),)))
    await Executor([OtelObserver()]).run(compiled)
    points = _points()

    def about(name: str, **attributes: Any) -> list[Any]:
        return [
            p
            for p in points.get(name, [])
            if all(p.attributes.get(k) == v for k, v in attributes.items())
        ]

    (ended,) = about("matrix.member.count", **{"matrix.member": "p", "matrix.status": "completed"})
    assert ended.value >= 1
    assert about("matrix.member.duration", **{"matrix.member": "p"})
    assert about("matrix.run.duration", **{"matrix.flow": "measured", "matrix.status": "completed"})
    (gauge,) = about("acme.rss", host="a")
    assert gauge.value == 7.0 and gauge.attributes["matrix.member"] == "p"
    (counter,) = about("acme.requests")
    assert counter.value >= 3
    (histogram,) = about("acme.latency")
    assert histogram.sum >= 0.25

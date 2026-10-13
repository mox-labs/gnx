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

from matrix import Executor, Inputs, RunContext, wire  # noqa: E402
from matrix.adapters._out.observers.otel import OtelObserver  # noqa: E402

_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))
trace.set_tracer_provider(_PROVIDER)
_READER = InMemoryMetricReader()
metrics.set_meter_provider(MeterProvider(metric_readers=[_READER]))


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
    await Executor([OtelObserver()]).run(wire({"e": Emit()}))
    spans = {s.name: s for s in _EXPORTER.get_finished_spans()}
    assert {"matrix.run", "matrix.component"} <= set(spans)
    component, run = spans["matrix.component"], spans["matrix.run"]
    assert component.parent is not None and component.parent.span_id == run.context.span_id
    assert component.attributes["matrix.component"] == "e"
    assert run.attributes["matrix.status"] == "completed"


async def test_a_span_a_component_opens_nests_under_it() -> None:
    _EXPORTER.clear()
    await Executor([OtelObserver()]).run(wire({"p": Probe()}))
    spans = {s.name: s for s in _EXPORTER.get_finished_spans()}
    probe, component = spans["acme.probe"], spans["matrix.component"]
    assert probe.parent is not None and probe.parent.span_id == component.context.span_id
    assert probe.attributes["acme.status"] == "ok"


async def test_metrics_come_from_the_events() -> None:
    await Executor([OtelObserver()]).run(wire({"p": Probe()}))
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
    assert about("matrix.run.duration", **{"matrix.status": "completed"})
    (gauge,) = about("acme.rss", host="a")
    assert gauge.value == 7.0 and gauge.attributes["matrix.component"] == "p"
    (counter,) = about("acme.requests")
    assert counter.value >= 3
    (histogram,) = about("acme.latency")
    assert histogram.sum >= 0.25

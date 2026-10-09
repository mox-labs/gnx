"""The otel observer turns matrix events into spans, nested run > member."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry import trace  # noqa: E402
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


class Emit:
    requires: dict[str, Any] = {}
    provides = {"out": "acme.v1.text"}

    async def run(self, inputs: Inputs) -> dict[str, Any]:
        return {"out": "x"}


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

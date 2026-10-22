"""OpenTelemetry from hardline, on the GenAI semantic conventions (W4.4).

One ``chat {model}`` span per backend call, with the ``gen_ai.client.inference.*`` duration
and token metrics, so retries and fallbacks are sibling spans under the caller's own. hardline
owns these counters: matrix counts its agent sessions, not the model calls inside them. No
content leaves: no prompt, reply or error message is an attribute, an event or a status.

Each test hands ``build_runtime`` its own SDK providers, read in memory; nothing global is set.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from hardline_helpers import DictSecrets
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from hardline import BackendError, build_runtime
from hardline.adapters._out.mock import MockBackend

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import ReadableSpan

PROMPT = "a prompt nobody may export"
REPLY = "a reply nobody may export"


class Telemetry:
    def __init__(self) -> None:
        self.spans = InMemorySpanExporter()
        self.tracer_provider = TracerProvider()
        self.tracer_provider.add_span_processor(SimpleSpanProcessor(self.spans))
        self.metrics = InMemoryMetricReader()
        self.meter_provider = MeterProvider(metric_readers=[self.metrics])

    def finished(self) -> list[ReadableSpan]:
        return list(self.spans.get_finished_spans())

    def points(self, name: str) -> list[Any]:
        data = self.metrics.get_metrics_data()
        found: list[Any] = []
        for resource in data.resource_metrics if data else ():
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    if metric.name == name:
                        found.extend(metric.data.data_points)
        return found


async def _no_sleep(seconds: float) -> None:
    pass


def _runtime(models: dict[str, Any], otel: Telemetry) -> Any:
    return build_runtime(
        {"models": models},
        backends={"mock": MockBackend()},
        secrets=DictSecrets(),
        sleep=_no_sleep,
        tracer_provider=otel.tracer_provider,
        meter_provider=otel.meter_provider,
    )


def _row(script: list[Any], **kw: Any) -> dict[str, Any]:
    return {
        "backend": "mock",
        "model": "qwen3-8b",
        "family": "qwen",
        "options": {"script": script},
        **kw,
    }


async def test_each_backend_call_is_one_chat_span_with_the_conventions_attributes() -> None:
    # The review's cheapest check: a scripted [rate_limit, answer] row is two sibling spans,
    # the first failed with error.type, the second carrying the response and its usage.
    otel = Telemetry()
    rt = _runtime({"q": _row([{"error": "rate_limit"}, REPLY], retries=1)}, otel)
    c = await rt.complete("q", PROMPT, max_tokens=64, temperature=0.2)
    failed, answered = otel.finished()
    for span in (failed, answered):
        assert span.name == "chat qwen3-8b" and span.kind is SpanKind.CLIENT
        assert span.instrumentation_scope is not None
        assert span.instrumentation_scope.name == "hardline"
        attributes = dict(span.attributes or {})
        assert attributes["gen_ai.operation.name"] == "chat"
        assert attributes["gen_ai.provider.name"] == "mock"
        assert attributes["gen_ai.request.model"] == "qwen3-8b"
        assert attributes["gen_ai.request.max_tokens"] == 64
        assert attributes["gen_ai.request.temperature"] == 0.2
        assert attributes["hardline.row"] == "q"
    assert failed.status.status_code is StatusCode.ERROR
    assert failed.status.description is None  # a message is content
    assert dict(failed.attributes or {})["error.type"] == "rate_limit"
    assert not failed.events  # no exception event: its message and traceback are content
    assert answered.status.status_code is StatusCode.UNSET
    attributes = dict(answered.attributes or {})
    assert "error.type" not in attributes
    assert attributes["gen_ai.response.model"] == "qwen3-8b"
    assert attributes["gen_ai.response.id"] == c.request_id
    assert attributes["gen_ai.response.finish_reasons"] == ("stop",)
    assert attributes["gen_ai.usage.input_tokens"] == c.usage.input_tokens
    assert attributes["gen_ai.usage.output_tokens"] == c.usage.output_tokens


async def test_no_content_leaves_in_a_span() -> None:
    otel = Telemetry()
    rt = _runtime({"q": _row([{"error": "unavailable"}, REPLY], retries=1)}, otel)
    await rt.complete("q", PROMPT, system="a system prompt nobody may export")
    for span in otel.finished():
        values = [str(v) for v in (span.attributes or {}).values()]
        values += [str(e.attributes) for e in span.events]
        values.append(str(span.status.description))
        for text in (PROMPT, REPLY, "system prompt", "scripted"):
            assert not any(text in value for value in values), (span.name, text)
        assert not {"gen_ai.input.messages", "gen_ai.output.messages"} & set(span.attributes or {})


async def test_the_chat_spans_are_children_of_the_callers_span() -> None:
    otel = Telemetry()
    rt = _runtime(
        {"a": _row([{"error": "unavailable"}], retries=0, fallbacks=["b"]), "b": _row([REPLY])},
        otel,
    )
    caller = otel.tracer_provider.get_tracer("caller")
    with caller.start_as_current_span("model step") as step:
        await rt.complete("a", PROMPT)
    *chats, outer = otel.finished()
    assert outer.name == "model step" and len(chats) == 2
    assert {s.context.trace_id for s in chats if s.context} == {step.get_span_context().trace_id}
    assert all(
        s.parent is not None and s.parent.span_id == step.get_span_context().span_id for s in chats
    )
    assert [dict(s.attributes or {})["hardline.row"] for s in chats] == ["a", "b"]


async def test_hardline_counts_the_inference_tokens_and_times_each_call() -> None:
    otel = Telemetry()
    rt = _runtime({"q": _row([{"error": "timeout"}, REPLY], retries=1)}, otel)
    c = await rt.complete("q", PROMPT)
    (input_tokens,) = otel.points("gen_ai.client.inference.usage.input_tokens")
    (output_tokens,) = otel.points("gen_ai.client.inference.usage.output_tokens")
    assert (input_tokens.value, output_tokens.value) == (
        c.usage.input_tokens,
        c.usage.output_tokens,
    )
    expected = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "mock",
        "gen_ai.token.modality": "text",
        "gen_ai.request.model": "qwen3-8b",
        "gen_ai.response.model": "qwen3-8b",
    }
    assert dict(input_tokens.attributes) == expected
    # The cache counters record strictly positive contributions only, and the mock caches none.
    assert otel.points("gen_ai.client.inference.usage.cache_read.input_tokens") == []
    durations = otel.points("gen_ai.client.inference.duration")
    assert sorted(d.attributes.get("error.type", "") for d in durations) == ["", "timeout"]
    assert all(d.count == 1 for d in durations)


async def test_a_call_that_fails_is_still_timed_and_raises_as_before() -> None:
    otel = Telemetry()
    rt = _runtime({"q": _row([{"error": "auth"}])}, otel)
    with pytest.raises(BackendError) as e:
        await rt.complete("q", PROMPT)
    assert e.value.reason == "auth"
    (span,) = otel.finished()
    assert dict(span.attributes or {})["error.type"] == "auth"
    (duration,) = otel.points("gen_ai.client.inference.duration")
    assert duration.attributes["error.type"] == "auth"
    assert otel.points("gen_ai.client.inference.usage.input_tokens") == []


async def test_fallbacks_and_retries_are_counted_for_whoever_edits_the_registry() -> None:
    # T31: a signal reaches the person who changes the system when a fallback succeeds.
    otel = Telemetry()
    rt = _runtime(
        {"a": _row([{"error": "unavailable"}], retries=1, fallbacks=["b"]), "b": _row([REPLY])},
        otel,
    )
    await rt.complete("a", PROMPT)
    (fallback,) = otel.points("hardline.fallbacks")
    assert fallback.value == 1
    assert dict(fallback.attributes) == {
        "hardline.row": "a",
        "hardline.fallback": "b",
        "error.type": "unavailable",
    }
    (retry,) = otel.points("hardline.retries")
    assert retry.value == 1
    assert dict(retry.attributes) == {"hardline.row": "a", "error.type": "unavailable"}


async def test_a_host_observer_replaces_the_root_s_own() -> None:
    otel = Telemetry()
    heard: list[str] = []

    class Mine:
        def observe(self, event: Any) -> None:
            heard.append(type(event).__name__)

    rt = build_runtime(
        {
            "models": {
                "a": _row([{"error": "unavailable"}], retries=0, fallbacks=["b"]),
                "b": _row([REPLY]),
            }
        },
        backends={"mock": MockBackend()},
        observer=Mine(),
        tracer_provider=otel.tracer_provider,
        meter_provider=otel.meter_provider,
    )
    await rt.complete("a", PROMPT)
    assert heard == ["AttemptFailed", "FellBack", "Answered"]
    assert otel.points("hardline.fallbacks") == []
    assert len(otel.finished()) == 2  # the spans are the backends', whoever observes the call

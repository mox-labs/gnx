"""The otel observer turns matrix events into nested spans and into metrics."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry import trace  # noqa: E402
from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

from matrix import (  # noqa: E402
    Event,
    EventPolicy,
    Executor,
    Inputs,
    RunContext,
    RunError,
    wire,
)
from matrix.adapters._out.observers.otel import OtelObserver  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Mapping

    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.util.types import AttributeValue

# Providers of the tests' own, handed to each observer: nothing here installs a global one, so
# no test depends on which suite ran first in the process. A test that reads metrics gives its
# observer a reader of its own, so it reads exactly what its run recorded.
_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))
_METERS = MeterProvider(metric_readers=[InMemoryMetricReader()])


def _observer(reader: InMemoryMetricReader | None = None) -> OtelObserver:
    meters = _METERS if reader is None else MeterProvider(metric_readers=[reader])
    return OtelObserver(tracer_provider=_PROVIDER, meter_provider=meters)


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


def _attrs(span: ReadableSpan) -> Mapping[str, AttributeValue]:
    assert span.attributes is not None, span.name
    return span.attributes


def _scope_name(span: ReadableSpan) -> str:
    assert span.instrumentation_scope is not None, span.name
    return span.instrumentation_scope.name


def _duration_ns(span: ReadableSpan) -> int:
    assert span.start_time is not None and span.end_time is not None, span.name
    return span.end_time - span.start_time


def _points(reader: InMemoryMetricReader) -> dict[str, list[Any]]:
    data = reader.get_metrics_data()
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
    assert _attrs(component)["matrix.component"] == "e"
    assert _attrs(run)["gen_ai.operation.name"] == "invoke_workflow"
    assert _attrs(run)["matrix.status"] == "completed"
    assert _scope_name(run) == "matrix"


async def test_a_span_a_component_opens_nests_under_it() -> None:
    _EXPORTER.clear()
    await Executor([_observer()]).run(wire({"p": Probe()}))
    spans = {s.name: s for s in _EXPORTER.get_finished_spans()}
    probe, component = spans["acme.probe"], spans["matrix.component"]
    assert probe.parent is not None and probe.parent.span_id == component.context.span_id
    assert _attrs(probe)["acme.status"] == "ok"


_LIBRARY = _PROVIDER.get_tracer("acme.library")


class _Traced:
    """An agent runtime whose work opens a span of its own, as an instrumented HTTP client
    would."""

    def check(self, definition: Any) -> None:
        return None

    async def run(self, definition: Any, task: str) -> Any:
        from matrix import AgentResponse

        with _LIBRARY.start_as_current_span("library.in-session"):
            return AgentResponse(content="done")


class _UsesLibraries:
    """Opens a library span in its own work, inside its own ``ctx.span``, and through an
    agent's runtime."""

    requires: tuple[str, ...] = ()
    provides = ("acme.v1.text",)

    def __init__(self, agent: Any) -> None:
        self.agent = agent

    async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
        with _LIBRARY.start_as_current_span("library.in-component"):
            pass
        async with ctx.span("acme.fetch"):
            with _LIBRARY.start_as_current_span("library.in-span"):
                pass
        await self.agent.run("go")
        return {"acme.v1.text": "x"}


async def test_a_library_span_nests_under_the_matrix_span_whose_work_opened_it() -> None:
    """A span a library opens through the OpenTelemetry API, in a component's work, is in the
    run's trace under the span of the work it was opened in: the component's,
    a ``ctx.span``'s, an agent session's."""
    from matrix import AgentDefinition, BoundAgent

    observer = _observer()
    agent = BoundAgent(AgentDefinition(name="rev"), _Traced(), observers=[observer])
    _EXPORTER.clear()
    await Executor([observer]).run(wire({"lib": _UsesLibraries(agent)}))
    spans = {s.name: s for s in _EXPORTER.get_finished_spans()}
    run = spans["invoke_workflow"]
    for library, parent in (
        ("library.in-component", "matrix.component"),
        ("library.in-span", "acme.fetch"),
        ("library.in-session", "invoke_agent rev"),
    ):
        opened, under = spans[library], spans[parent]
        assert opened.context.trace_id == run.context.trace_id, library
        assert opened.parent is not None and opened.parent.span_id == under.context.span_id, library


class _TracedModel:
    """A model runtime whose call opens a span of its own, as hardline's ``chat`` span per
    attempt does."""

    def check(self, model: str | None) -> None:
        return None

    async def call(self, request: Any) -> Any:
        from matrix import ModelCall

        with _LIBRARY.start_as_current_span("chat m"):
            return ModelCall(
                text="ok", finish_reason="stop", name="m", model="m-1", provider="openai"
            )


async def test_a_model_call_is_a_span_of_its_own_under_its_component() -> None:
    """A model step's call is ``matrix.model``, under its component's span; what the model
    runtime opens nests under it, and the span names the model asked for and the one that
    answered."""
    from matrix import MODEL_CALL, PROMPT, ModelStep, ModelStepConfig

    step = ModelStep(ModelStepConfig(runtime="r", model="m"), model_runtimes={"r": _TracedModel()})
    _EXPORTER.clear()
    run = await Executor([_observer()]).run(wire({"ask": step}, inputs=[PROMPT]), {PROMPT: "hi"})
    assert run.rows(MODEL_CALL)[0].text == "ok"
    spans = {s.name: s for s in _EXPORTER.get_finished_spans()}
    called, component, chat = spans["matrix.model"], spans["matrix.component"], spans["chat m"]
    assert called.parent is not None and called.parent.span_id == component.context.span_id
    assert chat.parent is not None and chat.parent.span_id == called.context.span_id
    for key, value in {
        "gen_ai.request.model": "m",
        "gen_ai.response.model": "m-1",
        "gen_ai.provider.name": "openai",
        "matrix.finish_reason": "stop",
        "matrix.status": "completed",
    }.items():
        assert _attrs(called)[key] == value, key


class _Counted:
    """A model runtime that reports usage, as hardline does for each call."""

    def check(self, model: str | None) -> None:
        return None

    async def call(self, request: Any) -> Any:
        from matrix import ModelCall, TokenUsage

        usage = TokenUsage(input_tokens=500, output_tokens=20)
        return ModelCall(text="ok", finish_reason="stop", name="m", usage=usage)


async def test_matrix_counts_no_inference_usage_for_a_model_call() -> None:
    """A model call's tokens are counted once, by the model runtime that made it (hardline
    counts each attempt): matrix's inference counters and its ``matrix.model`` span carry
    none of them, so nothing is counted twice (W4.5). The session row still holds them."""
    from matrix import PROMPT, ModelStep, ModelStepConfig

    reader = InMemoryMetricReader()
    step = ModelStep(ModelStepConfig(runtime="r"), model_runtimes={"r": _Counted()})
    _EXPORTER.clear()
    run = await Executor([_observer(reader)]).run(
        wire({"ask": step}, inputs=[PROMPT]), {PROMPT: "hi"}
    )
    (row,) = run.rows("matrix.v1.session")
    assert row.usage is not None and row.usage.input_tokens == 500
    points = _points(reader)
    assert "gen_ai.client.inference.usage.input_tokens" not in points
    assert "gen_ai.client.inference.usage.output_tokens" not in points
    called = {s.name: s for s in _EXPORTER.get_finished_spans()}["matrix.model"]
    assert not [key for key in _attrs(called) if key.startswith("gen_ai.usage.")]


class _Tracked(SpanProcessor):
    """Remembers every span started and every span ended."""

    def __init__(self) -> None:
        self.started: dict[int, str] = {}
        self.ended: set[int] = set()

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        self.started[span.context.span_id] = span.name

    def on_end(self, span: Any) -> None:
        self.ended.add(span.context.span_id)

    def open(self) -> list[str]:
        return sorted(name for i, name in self.started.items() if i not in self.ended)


def _tracked() -> tuple[OtelObserver, _Tracked]:
    tracked = _Tracked()
    provider = TracerProvider()
    provider.add_span_processor(tracked)
    return OtelObserver(tracer_provider=provider, meter_provider=_METERS), tracked


async def test_an_end_event_the_channel_never_delivers_leaves_no_span_open() -> None:
    """The channel stops delivering at a component's end (an observer before the otel one
    holds it), and drops the rest when the run closes it: every span still ends (W4.8)."""

    class Holds:
        async def on_event(self, event: Event) -> None:
            if event.name == "matrix.component.end":
                await asyncio.Event().wait()

    observer, tracked = _tracked()
    run = await Executor([Holds(), observer], events=EventPolicy(drain_s=0.01)).run(
        wire({"p": Probe()})
    )
    assert run.events_dropped > 0
    assert {"invoke_workflow", "matrix.component", "acme.probe"} <= set(tracked.started.values())
    assert tracked.open() == []


async def test_an_event_a_component_emits_opens_no_span_whatever_its_name() -> None:
    """``ctx.emit`` makes one event with a span id of its own: a name ending ``.start`` (ix's
    ``ix.trial.start``) opens no span that nothing would end (W4.8, W6.8)."""

    class Trials:
        requires: tuple[str, ...] = ()
        provides = ("acme.v1.text",)

        async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
            ctx.emit("acme.trial.start", seq=1)
            ctx.emit("acme.trial.end", seq=1)
            return {"acme.v1.text": "x"}

    observer, tracked = _tracked()
    await Executor([observer]).run(wire({"t": Trials()}))
    assert "acme.trial" not in tracked.started.values()
    assert tracked.open() == []


async def test_an_observer_whose_around_fails_is_logged_and_the_run_goes_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``around`` runs in the work's task, so what it raises entering, ending or leaving is
    logged and skipped; it cannot fail the run, nor swallow the work's own exception."""

    class Broken:
        def __init__(self, where: str) -> None:
            self.where = where

        def on_event(self, event: Event) -> None:
            return None

        @contextlib.contextmanager
        def around(self, event: Event) -> Any:
            if self.where == "enter":
                raise RuntimeError("entering")

            def end(finished: Event) -> None:
                if self.where == "end":
                    raise RuntimeError("ending")

            yield end
            if self.where == "exit":
                raise RuntimeError("leaving")

    class Fails:
        requires: tuple[str, ...] = ()
        provides = ("acme.v1.text",)

        async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
            raise ValueError("the work's own")

    broken = [Broken("enter"), Broken("end"), Broken("exit")]
    run = await Executor(broken).run(wire({"p": Probe()}))
    assert run.status == "completed" and run.rows("acme.v1.text") == ("x",)
    assert {"entering", "ending", "leaving"} <= {
        str(r.exc_info[1]) for r in caplog.records if r.exc_info
    }
    with pytest.raises(RunError) as failed:
        await Executor(broken).run(wire({"f": Fails()}))
    assert "the work's own" in str(failed.value)


async def test_a_session_row_names_the_span_of_its_session() -> None:
    """The trace and span ids a session row records are those of the session's own span,
    current while it ran."""
    from matrix import AgentDefinition, BoundAgent
    from matrix.composition.telemetry import current_trace

    observer = _observer()
    agent = BoundAgent(AgentDefinition(name="rev"), _Session(), observers=[observer])
    _EXPORTER.clear()
    run = await Executor([observer], trace=current_trace).run(wire({"ask": _Asks(agent)}))
    (row,) = run.rows("matrix.v1.session")
    invoked = {s.name: s for s in _EXPORTER.get_finished_spans()}["invoke_agent rev"]
    assert row.trace_id == trace.format_trace_id(invoked.context.trace_id)
    assert row.span_id == trace.format_span_id(invoked.context.span_id)


async def test_metrics_come_from_the_events() -> None:
    reader = InMemoryMetricReader()
    await Executor([_observer(reader)]).run(wire({"p": Probe()}))
    points = _points(reader)

    def about(name: str, **attributes: Any) -> list[Any]:
        return [
            p
            for p in points.get(name, [])
            if all(p.attributes.get(k) == v for k, v in attributes.items())
        ]

    (ended,) = about(
        "matrix.component.count", **{"matrix.component": "p", "matrix.status": "completed"}
    )
    assert ended.value == 1
    (took,) = about("matrix.component.duration", **{"matrix.component": "p"})
    assert took.count == 1
    (ran,) = about("gen_ai.invoke_workflow.duration", **{"matrix.status": "completed"})
    assert ran.count == 1
    (gauge,) = about("acme.rss", host="a")
    assert gauge.value == 7.0 and gauge.attributes["matrix.component"] == "p"
    (counter,) = about("acme.requests")
    assert counter.value == 3
    (histogram,) = about("acme.latency")
    assert (histogram.count, histogram.sum) == (1, 0.25)


class _Session:
    """A runtime that reports what the Claude runtime reports: a provider, and tool calls it
    saw start and end, the timed one with ``arguments``."""

    def __init__(self, arguments: dict[str, Any] | None = None) -> None:
        self.arguments = {"file_path": "notes.md"} if arguments is None else arguments

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
                    "input": self.arguments,
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

    reader = InMemoryMetricReader()
    observer = _observer(reader)
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
        assert _attrs(invoked)[key] == value, key
    assert tool.parent is not None and tool.parent.span_id == invoked.context.span_id
    assert _attrs(tool)["gen_ai.tool.name"] == "Read"
    assert _attrs(tool)["gen_ai.tool.call.id"] == "toolu_1"
    assert _attrs(tool)["gen_ai.operation.name"] == "execute_tool"
    # The arguments are content: not on the span without the opt-in.
    assert "gen_ai.tool.call.arguments" not in _attrs(tool)
    assert _duration_ns(tool) == 2_000_000_000  # timed as the runtime saw it

    points = _points(reader)
    (took,) = points["gen_ai.invoke_agent.duration"]
    assert (took.count, took.attributes["gen_ai.agent.name"]) == (1, "rev")
    (tokens_in,) = points["gen_ai.client.inference.usage.input_tokens"]
    (tokens_out,) = points["gen_ai.client.inference.usage.output_tokens"]
    assert (tokens_in.value, tokens_out.value) == (40, 9)
    assert tokens_in.attributes["gen_ai.provider.name"] == "anthropic"
    (tool_took,) = points["gen_ai.execute_tool.duration"]
    assert tool_took.count == 1 and tool_took.sum == 2.0


class _Cached:
    """A runtime whose session read most of its input from the prompt cache."""

    def check(self, definition: Any) -> None:
        return None

    async def run(self, definition: Any, task: str) -> Any:
        from matrix import AgentResponse

        return AgentResponse(
            content="done", tokens_input=100, cache_read_tokens=60, cache_write_tokens=10
        )


async def test_input_tokens_are_counted_once_with_the_cache_inside_them() -> None:
    """``tokens_input`` already holds the cache's tokens, as the conventions' input tokens
    do; the counter adds it as it stands and never adds the cache counts again."""
    from matrix import AgentDefinition, BoundAgent

    reader = InMemoryMetricReader()
    observer = _observer(reader)
    agent = BoundAgent(AgentDefinition(name="cached"), _Cached(), observers=[observer])
    _EXPORTER.clear()
    await Executor([observer]).run(wire({"ask": _Asks(agent)}))
    (counted,) = _points(reader)["gen_ai.client.inference.usage.input_tokens"]
    assert counted.value == 100
    invoked = {s.name: s for s in _EXPORTER.get_finished_spans()}["invoke_agent cached"]
    assert _attrs(invoked)["gen_ai.usage.input_tokens"] == 100


async def test_a_failed_session_carries_its_error_type() -> None:
    from matrix import AgentDefinition, BoundAgent, RunError

    observer = _observer()
    agent = BoundAgent(AgentDefinition(name="hurried"), _Fails(), observers=[observer])
    _EXPORTER.clear()
    with pytest.raises(RunError):
        await Executor([observer]).run(wire({"ask": _Asks(agent)}))
    spans = {s.name: s for s in _EXPORTER.get_finished_spans()}
    failed = spans["invoke_agent hurried"]
    assert _attrs(failed)["error.type"] == "rate_limited"
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
    assert _attrs(span)["error.type"] == "cancelled"
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

    tools = _Session({"pattern": f"{_SECRET} pattern"})
    agent = BoundAgent(AgentDefinition(name="rev"), tools, observers=[observer])
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
    # A tool call's arguments, as the conventions name them: a JSON object, as the runtime
    # reported it (W4.11: what pydantic-evals' ArgumentCorrectness reads).
    arguments = spans["execute_tool Read"].attributes["gen_ai.tool.call.arguments"]
    assert json.loads(arguments) == {"pattern": f"{_SECRET} pattern"}
    assert "matrix.arguments" not in spans["execute_tool Read"].attributes


async def test_the_span_names_are_the_pinned_ones() -> None:
    """The span names a trace consumer relies on (W5.3): a run, a component, an agent
    session, a timed tool call, a model step's call, a release and a component's own span."""
    from matrix import PROMPT, AgentDefinition, BoundAgent, ModelStep, ModelStepConfig

    agent = BoundAgent(AgentDefinition(name="rev"), _Session())

    class Works:
        requires: tuple[str, ...] = ()
        provides = ("acme.v1.text",)

        async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
            ctx.defer(lambda: None)
            async with ctx.span("acme.probe"):
                return {"acme.v1.text": (await agent.run("go")).content}

    step = ModelStep(ModelStepConfig(runtime="r"), model_runtimes={"r": _TracedModel()})
    _EXPORTER.clear()
    await Executor([_observer()]).run(
        wire({"works": Works(), "rate": step}, inputs=[PROMPT]), {PROMPT: "hi"}
    )
    matrix_spans = {s.name for s in _EXPORTER.get_finished_spans() if _scope_name(s) == "matrix"}
    assert matrix_spans == {
        "invoke_workflow",
        "matrix.component",
        "invoke_agent rev",
        "execute_tool Read",
        "matrix.model",
        "matrix.release",
        "acme.probe",
    }

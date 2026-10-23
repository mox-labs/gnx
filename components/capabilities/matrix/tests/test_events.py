"""The event channel: bounded, lossy on purpose, fed by components, and never in a run's way."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import pytest

from matrix import (
    Event,
    EventPolicy,
    Executor,
    Inputs,
    JsonlConstructStore,
    RecordingObserver,
    RunContext,
    RunError,
    wire,
)

if TYPE_CHECKING:
    from pathlib import Path

OUT = "acme.v1.out"


class Step:
    """A component whose body is a coroutine function of its context; what the body returns
    is one row of the kind the step provides."""

    def __init__(self, body: Any, *, requires: Any = (), provides: str | None = None) -> None:
        self.requires = frozenset(requires)
        self.provides = frozenset([provides] if provides else [])
        self._body = body

    async def run(self, inputs: Inputs, ctx: RunContext) -> Any:
        row = await self._body(ctx)
        return {kind: row for kind in self.provides}


class Stuck:
    """An observer that never returns from the first event it is handed."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()

    async def on_event(self, event: Event) -> None:
        self.entered.set()
        await asyncio.Event().wait()


async def _value() -> str:
    return "v"


async def test_an_observer_that_never_returns_cannot_stall_the_run() -> None:
    stuck = Stuck()
    ran: list[str] = []

    async def body(ctx: Any) -> str:
        await stuck.entered.wait()  # the observer is holding the dispatcher by now
        ran.append("component")
        return "v"

    executor = Executor([stuck], events=EventPolicy(drain_s=0.01))
    async with asyncio.timeout(30):
        run = await executor.run(wire({"a": Step(body, provides=OUT)}))
    assert run.status == "completed" and ran == ["component"]
    assert run.rows(OUT) == ("v",)
    # run.start is in the observer's hands for good, never delivered, and nothing after it
    # was: every event is counted, as many as the same run emits to an observer that returns.
    seen = RecordingObserver()
    await Executor([seen]).run(wire({"a": Step(lambda ctx: _value(), provides=OUT)}))
    assert run.events_dropped == len(seen.events) > 1


async def test_a_full_queue_drops_and_counts_and_every_event_is_delivered_or_counted() -> None:
    gate = asyncio.Event()
    recorder = RecordingObserver()

    class Gated:
        async def on_event(self, event: Event) -> None:
            await gate.wait()

    async def chatty(ctx: Any) -> str:
        for i in range(20):  # no await: the dispatcher cannot run, so the queue must fill
            ctx.emit("acme.tick", i=i)
        gate.set()
        return "v"

    executor = Executor([Gated(), recorder], events=EventPolicy(queue=4))
    run = await executor.run(wire({"a": Step(chatty, provides=OUT)}))
    assert run.status == "completed"
    notices = [e for e in recorder.events if e.name == "matrix.events.dropped"]
    delivered = [e for e in recorder.events if e.name != "matrix.events.dropped"]
    emitted = 20 + 5  # the ticks, plus run.start, component.start/end, row.append, run.end
    assert run.events_dropped >= 20 - (4 + 1)  # at most the queue and one in hand got through
    assert len(delivered) + run.events_dropped == emitted
    assert sum(e.fields["count"] for e in notices) == run.events_dropped  # and it was said


async def test_components_emit_measure_and_open_spans_under_their_own_span() -> None:
    recorder = RecordingObserver()

    async def body(ctx: Any) -> str:
        ctx.emit("acme.probe.sent", n=1)
        async with ctx.span("acme.probe", target="x"):
            ctx.measure("acme.rss", 12.5, unit="By", kind="gauge", host="a")
        return "v"

    run = await Executor([recorder]).run(wire({"m": Step(body, provides=OUT)}))
    by_name = {e.name: e for e in recorder.events}
    component = by_name["matrix.component.start"]
    sent = by_name["acme.probe.sent"]
    start, end = by_name["acme.probe.start"], by_name["acme.probe.end"]
    assert (sent.component, sent.run_id, sent.parent) == ("m", run.run_id, component.span)
    assert sent.fields == {"n": 1}
    assert start.span == end.span and start.parent == component.span
    assert start.fields == {"target": "x"} and end.fields["status"] == "ok"
    measure = by_name["matrix.measure"]
    assert measure.parent == start.span  # made inside the span, so it is the span's child
    assert measure.fields == {
        "name": "acme.rss",
        "value": 12.5,
        "unit": "By",
        "kind": "gauge",
        "attrs": {"host": "a"},
    }
    assert all(e.schema == "matrix.v1.event" for e in recorder.events)


async def test_a_span_that_raises_ends_with_status_error() -> None:
    recorder = RecordingObserver()

    async def body(ctx: Any) -> str:
        async with ctx.span("acme.probe"):
            raise RuntimeError("kaput")

    with pytest.raises(RunError):
        await Executor([recorder]).run(wire({"m": Step(body)}))
    end = next(e for e in recorder.events if e.name == "acme.probe.end")
    assert end.fields["status"] == "error"


@pytest.mark.parametrize(
    ("call", "says"),
    [
        (lambda ctx: ctx.emit("matrix.component.end", status="completed"), "matrix reserves"),
        (lambda ctx: ctx.emit("tick"), "not namespaced"),
        (lambda ctx: ctx.measure("matrix.run.duration", 1.0), "matrix reserves"),
        (lambda ctx: ctx.span("Acme.Probe"), "not namespaced"),
        (lambda ctx: ctx.measure("acme.rss", 1.0, kind="timer"), "not one of"),
        (lambda ctx: ctx.measure("acme.rss", "high"), "must be a number"),
    ],
)
async def test_a_component_cannot_speak_for_matrix_or_without_a_namespace(
    call: Any, says: str
) -> None:
    async def body(ctx: Any) -> str:
        made = call(ctx)
        if made is not None:  # a span: checked when it is entered
            async with made:
                pass
        return "v"

    with pytest.raises(RunError, match=says) as e:
        await Executor().run(wire({"m": Step(body)}))
    assert (e.value.kind, e.value.cause_kind) == ("run_failed", "contract")


async def test_the_stop_path_rows_and_releases_are_narrated() -> None:
    recorder = RecordingObserver()
    running = asyncio.Event()

    async def cooperative(ctx: Any) -> str:
        ctx.defer(lambda: None)
        running.set()
        await ctx.stopping.wait()
        return "v"

    async def fails(ctx: Any) -> str:
        await running.wait()
        raise RuntimeError("kaput")

    async def never(ctx: Any) -> None:
        return None

    wiring = wire(
        {
            "good": Step(cooperative, provides=OUT),
            "bad": Step(fails),
            "later": Step(never, requires=[OUT]),
        }
    )
    with pytest.raises(RunError):
        await Executor([recorder]).run(wiring)
    told = [
        (e.name, e.component, e.fields.get("status") or e.fields.get("reason"))
        for e in recorder.events
    ]
    assert ("matrix.component.stopping", "good", "run_failed") in told
    assert ("matrix.component.end", "bad", "failed") in told
    assert ("matrix.component.end", "good", "stopped") in told
    assert ("matrix.component.end", "later", "skipped") in told
    assert ("matrix.release.end", "good", "released") in told
    assert told[0][0] == "matrix.run.start" and told[-1] == ("matrix.run.end", None, "failed")
    ends = [e.component for e in recorder.events if e.name == "matrix.component.end"]
    assert sorted(ends, key=str) == ["bad", "good", "later"]  # exactly one ending per component
    failed = next(
        e for e in recorder.events if e.name == "matrix.component.end" and e.component == "bad"
    )
    assert failed.fields["kind"] == "component"
    (landed,) = [e for e in recorder.events if e.name == "matrix.row.append"]
    assert (landed.component, landed.fields) == ("good", {"kind": OUT, "row": 0, "seq": 0})


async def test_using_a_context_after_the_component_ended_is_ignored_and_said_once() -> None:
    recorder = RecordingObserver()
    kept: list[Any] = []

    async def leaks(ctx: Any) -> str:
        kept.append(ctx)
        return "v"

    async def late(ctx: Any) -> None:
        kept[0].emit("acme.late")
        kept[0].measure("acme.late", 1)
        kept[0].provide(OUT, "too late")

    wiring = wire({"a": Step(leaks, provides=OUT), "b": Step(late, requires=[OUT])})
    run = await Executor([recorder]).run(wiring)
    names = recorder.names()
    assert "acme.late" not in names and "matrix.measure" not in names
    assert run.rows(OUT) == ("v",)
    late_events = [e for e in recorder.events if e.name == "matrix.component.late"]
    assert [(e.component, e.fields) for e in late_events] == [("a", {"call": "emit"})]


def test_the_recorder_keeps_only_its_last_events() -> None:
    recorder = RecordingObserver(limit=2)
    for name in ("acme.a", "acme.b", "acme.c"):
        recorder.on_event(Event(name, span="s"))
    assert recorder.names() == ["acme.b", "acme.c"]


def test_an_event_document_is_versioned_and_json_ready() -> None:
    event = Event("acme.tick", span="s1", run_id="r1", parent="p1", fields={"n": 1}, component="m")
    document = json.loads(json.dumps(event.document()))
    assert document == {
        "schema": "matrix.v1.event",
        "name": "acme.tick",
        "time": event.time.isoformat(),
        "run_id": "r1",
        "span": "s1",
        "parent": "p1",
        "component": "m",
        "fields": {"n": 1},
    }


async def test_the_events_no_observer_received_are_counted_in_the_run_s_record(
    tmp_path: Path,
) -> None:
    """A reader of ``events.jsonl`` can tell it is incomplete only from ``run.json``: a
    ``matrix.events.dropped`` event may itself be lost."""
    stuck = Stuck()

    async def body(ctx: Any) -> str:
        await stuck.entered.wait()
        return "v"

    store = JsonlConstructStore(tmp_path)
    executor = Executor([stuck], events=EventPolicy(drain_s=0.01))
    run = await executor.run(wire({"a": Step(body, provides=OUT)}), store=store, run_id="r")
    assert run.events_dropped > 0
    recorded = json.loads((tmp_path / "r" / "run.json").read_text())
    assert recorded["events_dropped"] == run.events_dropped
    assert store.describe("r")["events_dropped"] == run.events_dropped


#: Every event matrix emits, with the fields it always carries. Telemetry names are not
#: contract (owner ruling 2026-10-22); this table makes a change to them deliberate: a new
#: event is listed here or the test below fails. Fields that apply only sometimes (a failure's
#: ``error``, a retried component's ``attempts``) are in docs/reference/api.md.
PINNED: dict[str, set[str]] = {
    "matrix.run.start": {"components"},
    "matrix.run.end": {"status", "rows", "duration_ms"},
    "matrix.component.start": set(),
    "matrix.component.stopping": {"reason"},
    "matrix.component.end": {"status"},
    "matrix.component.late": {"call"},
    "matrix.component.retry": {"attempt", "kind", "error", "wait_s"},
    "matrix.row.append": {"kind", "row", "seq"},
    "matrix.release.start": set(),
    "matrix.release.end": {"status", "error"},
    "matrix.agent.start": {"agent", "model", "task"},
    "matrix.agent.end": {"agent", "status", "duration_ms"},
    "matrix.model.start": {"runtime", "model"},
    "matrix.model.end": {"runtime", "status", "duration_ms"},
    "matrix.tool.start": {"agent", "tool"},
    "matrix.tool.end": {"agent", "tool", "status", "duration_ms"},
    "matrix.measure": {"name", "value", "unit", "kind", "attrs"},
    "matrix.events.dropped": {"count"},
}
#: What a session or a call that replied adds to its end.
REPLIED = {
    "matrix.agent.end": {
        "tokens_input",
        "tokens_output",
        "cost_usd",
        "model",
        "family",
        "provider",
        "tool_calls",
    },
    "matrix.model.end": {"name", "model", "family", "provider", "finish_reason"},
}


async def test_every_event_matrix_emits_carries_its_pinned_fields() -> None:
    from matrix import (
        PROMPT,
        AgentDefinition,
        AgentResponse,
        AgentRuntimeError,
        BoundAgent,
        ComponentLimits,
        Limits,
        ModelStep,
        ModelStepConfig,
        Retry,
    )
    from matrix.adapters._out.runtime.mock import MockModelRuntime

    class Tools:
        def check(self, definition: Any) -> None:
            return None

        async def run(self, definition: Any, task: str) -> AgentResponse:
            call = {"name": "Read", "id": "t1", "input": {}}
            call |= {"started": "2026-10-22T10:00:00+00:00", "ended": "2026-10-22T10:00:01+00:00"}
            return AgentResponse(content="ok", tool_calls=(call,))

    agent = BoundAgent(AgentDefinition(name="rev"), Tools())
    kept: list[Any] = []
    tries: list[int] = []

    async def asks(ctx: Any) -> str:
        kept.append(ctx)
        ctx.defer(lambda: None)
        async with ctx.span("acme.work"):
            ctx.measure("acme.rss", 1.0)
            return (await agent.run("go")).content

    async def late(ctx: Any) -> None:
        kept[0].emit("acme.after")  # the asking component has ended

    async def flaky(ctx: Any) -> str:
        tries.append(1)
        if len(tries) == 1:
            raise AgentRuntimeError("not yet", reason="unavailable")  # transient
        return "v"

    async def slow(ctx: Any) -> None:
        await ctx.stopping.wait()

    recorder = RecordingObserver()
    step = ModelStep(ModelStepConfig(runtime="m"), model_runtimes={"m": MockModelRuntime()})
    wiring = wire(
        {
            "asks": Step(asks, provides=OUT),
            "late": Step(late, requires=[OUT]),
            "flaky": Step(flaky, provides="acme.v1.flaky"),
            "slow": Step(slow),
            "rate": step,
        },
        inputs=[PROMPT],
    )
    limits = Limits(
        on_failure="isolate",
        retry=Retry(attempts=2, backoff_s=0),
        components={"slow": ComponentLimits(deadline_s=0.05, grace_s=1)},
    )
    with pytest.raises(RunError):  # slow overruns its deadline
        await Executor([recorder]).run(wiring, {PROMPT: "hi"}, limits=limits)

    class Gated:
        def __init__(self) -> None:
            self.gate = asyncio.Event()

        async def on_event(self, event: Event) -> None:
            await self.gate.wait()

    gated = Gated()

    async def chatty(ctx: Any) -> str:
        for i in range(10):
            ctx.emit("acme.tick", i=i)
        gated.gate.set()
        return "v"

    await Executor([gated, recorder], events=EventPolicy(queue=2)).run(
        wire({"c": Step(chatty, provides=OUT)})
    )

    seen = {e.name for e in recorder.events if e.name.startswith("matrix.")}
    assert seen == set(PINNED)
    for event in recorder.events:
        pinned = PINNED.get(event.name)
        if pinned is None:
            continue
        if event.fields.get("status") not in ("error", "failed", "cancelled"):
            pinned = pinned | REPLIED.get(event.name, set())
        assert pinned <= set(event.fields), (event.name, sorted(event.fields))

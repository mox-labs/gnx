"""The event channel: bounded, lossy on purpose, fed by components, and never in a run's way."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from matrix import (
    Event,
    EventPolicy,
    Executor,
    Flow,
    Inputs,
    Limits,
    Member,
    RecordingObserver,
    RunError,
    compile_flow,
)

TEXT = "acme.v1.text"


class Step:
    """A component whose body is the coroutine function it is given."""

    requires: dict[str, Any] = {}
    provides = {"out": TEXT}

    def __init__(self, body: Any) -> None:
        self._body = body

    async def run(self, inputs: Inputs) -> dict[str, Any]:
        return {"out": await self._body(inputs.context)}


def flow_of(*members: Member) -> Any:
    return compile_flow(Flow("f", members))


async def _ok(ctx: Any) -> str:
    return "v"


class Stuck:
    """An observer that never returns from the first event it is handed."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()

    async def on_event(self, event: Event) -> None:
        self.entered.set()
        await asyncio.Event().wait()


async def test_an_observer_that_never_returns_cannot_stall_the_run() -> None:
    stuck = Stuck()
    ran: list[str] = []

    async def body(ctx: Any) -> str:
        await stuck.entered.wait()  # the observer is holding the dispatcher by now
        ran.append("member")
        return "v"

    executor = Executor([stuck], events=EventPolicy(drain_s=0.01))
    async with asyncio.timeout(30):
        run = await executor.run(flow_of(Member("a", Step(body), {"out": "t"})))
    assert run.status == "completed" and ran == ["member"]
    assert run.outputs("t") == ("v",)
    # run.start is in the observer's hands for good; everything after it went undelivered.
    assert run.events_dropped == 5


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
    run = await executor.run(flow_of(Member("a", Step(chatty), {"out": "t"})))
    assert run.status == "completed"
    notices = [e for e in recorder.events if e.name == "matrix.events.dropped"]
    delivered = [e for e in recorder.events if e.name != "matrix.events.dropped"]
    emitted = 20 + 5  # the ticks, plus run.start, member.start/end, artifact.append, run.end
    assert run.events_dropped >= 20 - (4 + 1)  # at most the queue and one in hand got through
    assert len(delivered) + run.events_dropped == emitted
    assert sum(e.fields["count"] for e in notices) == run.events_dropped  # and it was said


async def test_components_emit_measure_and_open_spans_under_their_member() -> None:
    recorder = RecordingObserver()

    async def body(ctx: Any) -> str:
        ctx.emit("acme.probe.sent", n=1)
        async with ctx.span("acme.probe", target="x"):
            ctx.measure("acme.rss", 12.5, unit="By", kind="gauge", host="a")
        return "v"

    run = await Executor([recorder]).run(flow_of(Member("m", Step(body), {"out": "t"})))
    by_name = {e.name: e for e in recorder.events}
    member = by_name["matrix.member.start"]
    sent, start, end = (
        by_name["acme.probe.sent"],
        by_name["acme.probe.start"],
        by_name["acme.probe.end"],
    )
    assert (sent.member, sent.run_id, sent.parent, sent.fields) == (
        "m",
        run.run_id,
        member.span,
        {"n": 1},
    )
    assert start.span == end.span and start.parent == member.span
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
        await Executor([recorder]).run(flow_of(Member("m", Step(body))))
    end = next(e for e in recorder.events if e.name == "acme.probe.end")
    assert end.fields["status"] == "error"


@pytest.mark.parametrize(
    ("call", "says"),
    [
        (lambda ctx: ctx.emit("matrix.member.end", status="completed"), "matrix reserves"),
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
        await Executor().run(flow_of(Member("m", Step(body))))
    assert e.value.kind == "contract"


async def test_the_stop_path_and_releases_are_narrated() -> None:
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

    class Reader:
        requires = {"in": TEXT}
        provides: dict[str, Any] = {}

        async def run(self, inputs: Inputs) -> dict[str, Any]:
            return {}

    compiled = flow_of(
        Member("good", Step(cooperative), {"out": "t"}),
        Member("bad", Step(fails)),
        Member("later", Reader(), {"in": "t"}),
    )
    with pytest.raises(RunError):
        await Executor([recorder]).run(compiled, limits=Limits(concurrency=2))
    told = [
        (e.name, e.member, e.fields.get("status") or e.fields.get("reason"))
        for e in recorder.events
    ]
    assert ("matrix.member.stopping", "good", "run_failed") in told
    assert ("matrix.member.end", "bad", "failed") in told
    assert ("matrix.member.end", "good", "completed") in told
    assert ("matrix.member.end", "later", "skipped") in told
    assert ("matrix.release.end", "good", "released") in told
    assert told[0][0] == "matrix.run.start" and told[-1] == ("matrix.run.end", None, "failed")
    ends = [e.member for e in recorder.events if e.name == "matrix.member.end"]
    assert sorted(ends) == ["bad", "good", "later"]  # exactly one ending per member
    failed = next(e for e in recorder.events if e.name == "matrix.member.end" and e.member == "bad")
    assert failed.fields["kind"] == "component"


async def test_telemetry_after_the_member_ended_is_ignored_and_said_once() -> None:
    recorder = RecordingObserver()
    kept: list[Any] = []

    async def leaks(ctx: Any) -> str:
        kept.append(ctx)
        return "v"

    class Late:
        requires = {"in": TEXT}
        provides: dict[str, Any] = {}

        async def run(self, inputs: Inputs) -> dict[str, Any]:
            kept[0].emit("acme.late")
            kept[0].measure("acme.late", 1)
            return {}

    compiled = flow_of(Member("a", Step(leaks), {"out": "t"}), Member("b", Late(), {"in": "t"}))
    await Executor([recorder]).run(compiled)
    names = recorder.names()
    assert "acme.late" not in names and "matrix.measure" not in names
    late = [e for e in recorder.events if e.name == "matrix.member.late"]
    assert [(e.member, e.fields) for e in late] == [("a", {"call": "emit"})]


def test_the_recorder_keeps_only_its_last_events() -> None:
    recorder = RecordingObserver(limit=2)
    for name in ("acme.a", "acme.b", "acme.c"):
        recorder.on_event(Event(name, "s"))
    assert recorder.names() == ["acme.b", "acme.c"]


def test_an_event_document_is_versioned_and_json_ready() -> None:
    event = Event("acme.tick", "s1", "r1", "p1", {"n": 1}, "m")
    document = json.loads(json.dumps(event.document()))
    assert document == {
        "schema": "matrix.v1.event",
        "name": "acme.tick",
        "time": event.time.isoformat(),
        "run_id": "r1",
        "span": "s1",
        "parent": "p1",
        "member": "m",
        "fields": {"n": 1},
    }

"""Running components: inputs in, a Run out, every row recorded, a component held to its kinds."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError, field_validator

from matrix import (
    ComponentError,
    ConfigError,
    ContractError,
    Executor,
    Inputs,
    Limits,
    RecordingObserver,
    RunContext,
    RunError,
    wire,
)

TEXT, UPPER, SCORE, OTHER = "acme.v1.text", "acme.v1.upper", "acme.v1.score", "acme.v1.other"


class Step:
    """A component whose body is the coroutine function it is given."""

    def __init__(self, body: Any, requires: Any = (), provides: Any = ()) -> None:
        self.requires = frozenset(requires)
        self.provides = frozenset(provides)
        self._body = body
        self.seen: list[Inputs] = []

    async def run(self, inputs: Inputs, ctx: RunContext) -> Any:
        self.seen.append(inputs)
        return await self._body(inputs, ctx)


def emits(kind: str, value: Any) -> Step:
    async def body(inputs: Inputs, ctx: RunContext) -> Any:
        return {kind: value}

    return Step(body, provides=[kind])


async def upper(inputs: Inputs, ctx: RunContext) -> Any:
    return {UPPER: [text.upper() for text in inputs[TEXT]]}


async def test_inputs_reach_the_component_and_rows_land_in_the_construct() -> None:
    step = Step(upper, requires=[TEXT], provides=[UPPER])
    wiring = wire({"up": step}, inputs=[TEXT])
    run = await Executor().run(wiring, {TEXT: ["hello", "world"]})
    assert run.status == "completed" and run.ended is not None
    assert run.rows(TEXT) == ("hello", "world")  # rows are the records, nothing around them
    assert run.rows(UPPER) == ("HELLO", "WORLD")
    assert [(e.kind, e.row, e.component) for e in run.construct.ledger] == [
        (TEXT, 0, "$input"),
        (TEXT, 1, "$input"),
        (UPPER, 0, "up"),
        (UPPER, 1, "up"),
    ]


async def test_a_component_sees_only_the_kinds_it_requires() -> None:
    reader = Step(upper, requires=[TEXT], provides=[UPPER])
    wiring = wire(
        {"secret": emits(OTHER, "not for the reader"), "reader": reader}, inputs=[TEXT, SCORE]
    )
    await Executor().run(wiring, {TEXT: "hi", SCORE: 1})
    (seen,) = reader.seen
    assert dict(seen) == {TEXT: ("hi",)}  # not SCORE, not OTHER, not the Construct
    with pytest.raises(KeyError, match="is not a kind this component requires"):
        seen[OTHER]
    assert seen.one(TEXT) == "hi"


async def test_a_run_input_is_one_row_or_a_list_of_rows() -> None:
    reader = Step(upper, requires=[TEXT], provides=[UPPER])
    one = await Executor().run(wire({"r": reader}, inputs=[TEXT]), {TEXT: "a"})
    many = await Executor().run(wire({"r": reader}, inputs=[TEXT]), {TEXT: ("a", "b")})
    none = await Executor().run(wire({"r": reader}, inputs=[TEXT]), {TEXT: []})
    assert (one.rows(UPPER), many.rows(UPPER), none.rows(UPPER)) == (("A",), ("A", "B"), ())


async def test_a_component_starts_when_what_it_requires_is_ready_not_before() -> None:
    order: list[str] = []
    gate = asyncio.Event()
    independent_ran = asyncio.Event()

    async def slow(inputs: Inputs, ctx: RunContext) -> Any:
        order.append("slow starts")
        await independent_ran.wait()  # it can only finish once its neighbour has run
        await gate.wait()
        order.append("slow ends")
        return {TEXT: "from slow"}

    async def independent(inputs: Inputs, ctx: RunContext) -> Any:
        order.append("independent runs")
        independent_ran.set()
        return {OTHER: "x"}

    async def after_independent(inputs: Inputs, ctx: RunContext) -> Any:
        order.append("its dependent runs")
        gate.set()  # which is what lets the slow one finish
        return None

    async def after_slow(inputs: Inputs, ctx: RunContext) -> Any:
        order.append(f"reader sees {inputs.one(TEXT)}")
        return None

    wiring = wire(
        {
            "after-slow": Step(after_slow, requires=[TEXT]),
            "slow": Step(slow, provides=[TEXT]),
            "after-independent": Step(after_independent, requires=[OTHER]),
            "independent": Step(independent, provides=[OTHER]),
        }
    )
    async with asyncio.timeout(30):  # scheduling by anything but readiness deadlocks here
        run = await Executor().run(wiring)
    assert run.status == "completed"
    # A component two steps from the start ran while an unrelated slow one was still going,
    # and nothing that requires the slow one's rows started until it had finished.
    assert order == [
        "slow starts",
        "independent runs",
        "its dependent runs",
        "slow ends",
        "reader sees from slow",
    ]


async def test_concurrency_caps_how_many_run_at_once() -> None:
    running = 0
    most = 0

    async def body(inputs: Inputs, ctx: RunContext) -> Any:
        nonlocal running, most
        running += 1
        most = max(most, running)
        await asyncio.sleep(0)
        running -= 1

    parts = {f"p{i}": Step(body) for i in range(5)}
    await Executor().run(wire(parts), limits=Limits(concurrency=2))
    assert most == 2
    most = 0
    await Executor().run(wire(parts))
    assert most == 5


async def test_wiring_runs_again_with_new_inputs() -> None:
    wiring = wire({"up": Step(upper, requires=[TEXT], provides=[UPPER])}, inputs=[TEXT])
    executor = Executor()
    first = await executor.run(wiring, {TEXT: "a"})
    second = await executor.run(wiring, {TEXT: "b"})
    assert first.run_id != second.run_id
    assert (first.rows(UPPER), second.rows(UPPER)) == (("A",), ("B",))


@pytest.mark.parametrize(
    ("given", "says"),
    [
        ({}, [(f"inputs.{TEXT}", "missing_key")]),
        ({TEXT: "x", SCORE: 1}, [(f"inputs.{SCORE}", "unknown_key")]),
    ],
)
async def test_inputs_must_match_the_declaration(
    given: dict[str, Any], says: list[tuple[str, str]]
) -> None:
    wiring = wire({"up": Step(upper, requires=[TEXT], provides=[UPPER])}, inputs=[TEXT])
    with pytest.raises(ConfigError) as e:
        await Executor().run(wiring, given)
    assert [(p.path, p.kind) for p in e.value.problems] == says
    assert all(p.allowed == (TEXT,) for p in e.value.problems)


async def test_a_row_of_a_kind_it_does_not_provide_breaks_the_contract() -> None:
    async def body(inputs: Inputs, ctx: RunContext) -> Any:
        return {TEXT: "x", OTHER: "y"}

    with pytest.raises(RunError) as e:
        await Executor().run(wire({"w": Step(body, provides=[TEXT])}))
    assert (e.value.kind, e.value.cause_kind, e.value.component) == ("run_failed", "contract", "w")
    assert "returned rows of ['acme.v1.other'], which it does not provide" in str(e.value)
    assert e.value.run.rows(TEXT) == ()  # nothing from a broken return is recorded


async def test_returning_something_that_is_not_a_mapping_breaks_the_contract() -> None:
    async def body(inputs: Inputs, ctx: RunContext) -> Any:
        return "x"

    with pytest.raises(RunError, match="returned str"):
        await Executor().run(wire({"b": Step(body, provides=[TEXT])}))


async def test_a_component_may_return_nothing() -> None:
    async def body(inputs: Inputs, ctx: RunContext) -> Any:
        return None

    run = await Executor().run(wire({"quiet": Step(body, provides=[TEXT])}))
    assert run.status == "completed" and run.rows(TEXT) == ()


async def test_rows_validate_against_the_kinds_registered_schema() -> None:
    class Score(BaseModel):
        value: float

    bad = wire({"s": emits(SCORE, [{"value": 1.0}, {"value": "high"}])}, schemas={SCORE: Score})
    with pytest.raises(RunError) as e:
        await Executor().run(bad)
    assert isinstance(e.value.__cause__, ContractError)
    assert "not a valid acme.v1.score" in str(e.value)
    assert e.value.run.rows(SCORE) == ()  # every row is checked before any is recorded
    good = wire({"s": emits(SCORE, Score(value=2.0))}, schemas={SCORE: Score})
    assert (await Executor().run(good)).rows(SCORE) == (Score(value=2.0),)
    with pytest.raises(ContractError, match="input 'acme.v1.score'"):
        await Executor().run(wire({}, inputs=[SCORE], schemas={SCORE: Score}), {SCORE: {}})


async def test_a_row_handed_over_that_its_schema_refuses_with_any_error_is_a_contract_error() -> (
    None
):
    class Strict(BaseModel):
        value: float

        @field_validator("value")
        @classmethod
        def _finite(cls, value: float) -> float:
            if value != value:
                raise TypeError("not a number")
            return value

    raised: list[BaseException] = []

    async def body(inputs: Inputs, ctx: RunContext) -> Any:
        try:
            ctx.provide(SCORE, {"value": float("nan")})
        except Exception as e:
            raised.append(e)
            raise

    wiring = wire({"s": Step(body, provides=[SCORE])}, schemas={SCORE: Strict})
    with pytest.raises(RunError) as e:
        await Executor().run(wiring)
    assert [type(r) for r in raised] == [ContractError] and "not a number" in str(raised[0])
    assert e.value.cause_kind == "contract"


async def test_a_failure_keeps_the_partial_run() -> None:
    async def boom(inputs: Inputs, ctx: RunContext) -> Any:
        raise RuntimeError("kaput")

    wiring = wire(
        {
            "ok": emits(TEXT, "kept"),
            "bad": Step(boom, requires=[TEXT], provides=[OTHER]),
            "later": Step(upper, requires=[TEXT, OTHER]),
        }
    )
    with pytest.raises(RunError) as e:
        await Executor().run(wiring)
    run = e.value.run
    assert run.status == "failed" and e.value.component == "bad"
    assert run.rows(TEXT) == ("kept",)
    assert [f.document() for f in run.failures] == [
        {
            "component": "bad",
            "kind": "component",
            "message": "component 'bad' failed: RuntimeError: kaput",
        }
    ]
    assert isinstance(e.value.__cause__, ComponentError)
    assert (e.value.kind, e.value.cause_kind) == ("run_failed", "component")
    assert e.value.construct is run.construct and e.value.exit_code == 8


async def test_a_component_past_its_deadline_is_a_failure_naming_it() -> None:
    async def hangs(inputs: Inputs, ctx: RunContext) -> Any:
        await asyncio.Event().wait()

    with pytest.raises(RunError, match="'slow' timed out after 0.01s") as e:
        await Executor().run(wire({"slow": Step(hangs)}), limits=Limits(deadline_s=0.01, grace_s=0))
    assert e.value.cause_kind == "component"


def test_limits_are_checked_when_made() -> None:
    for bad in (
        {"concurrency": 0},
        {"deadline_s": 0},
        {"grace_s": -1},
        {"cleanup_s": 0},
        {"on_failure": "retry"},
    ):
        with pytest.raises(ValidationError):
            Limits(**bad)  # type: ignore[arg-type]


async def test_observers_see_the_run_and_one_failing_observer_cannot_stop_it() -> None:
    class Broken:
        def on_event(self, event: Any) -> None:
            raise RuntimeError("observer bug")

    recorder = RecordingObserver()
    run = await Executor([Broken(), recorder]).run(wire({"e": emits(TEXT, "x")}))
    assert run.status == "completed"
    assert recorder.names() == [
        "matrix.run.start",
        "matrix.component.start",
        "matrix.row.append",
        "matrix.component.end",
        "matrix.run.end",
    ]
    end = recorder.events[-1]
    assert end.fields["status"] == "completed" and end.fields["rows"] == 1


async def test_a_component_cannot_change_what_it_read_or_what_it_handed_over() -> None:
    handed: dict[str, int] = {"n": 1}

    async def source(inputs: Inputs, ctx: RunContext) -> Any:
        ctx.provide(TEXT, handed)
        handed["n"] = 2  # after the hand-over: the record already has its own copy
        return None

    async def mutator(inputs: Inputs, ctx: RunContext) -> Any:
        inputs.one(TEXT)["n"] = 999
        return None

    async def reader(inputs: Inputs, ctx: RunContext) -> Any:
        return {SCORE: inputs.one(TEXT)["n"]}

    wiring = wire(
        {
            "src": Step(source, provides=[TEXT]),
            "m": Step(mutator, requires=[TEXT]),
            "r": Step(reader, requires=[TEXT], provides=[SCORE]),
        }
    )
    run = await Executor().run(wiring)
    assert run.rows(TEXT) == ({"n": 1},)  # the record is untouched
    assert run.rows(SCORE) == (1,)  # and so is what the sibling read


async def test_live_handles_cannot_be_rows() -> None:
    import threading

    with pytest.raises(RunError, match="must be data"):
        await Executor().run(wire({"src": emits(TEXT, threading.Lock())}))


async def test_a_context_built_by_hand_refuses_the_names_a_run_refuses() -> None:
    """``emit``, ``measure`` and ``span`` raise ``ContractError`` for a bad name; a context
    built outside a run (a component's unit test) accepted any, so a test could not catch
    what the run would refuse (mx-src F24)."""
    from matrix import ContractError

    ctx = RunContext("r", "c")
    for call in (
        lambda: ctx.emit("tick"),
        lambda: ctx.emit("matrix.tick"),
        lambda: ctx.measure("rss", 1.0),
        lambda: ctx.measure("acme.rss", 1.0, kind="meter"),
        lambda: ctx.measure("acme.rss", "lots"),
    ):
        with pytest.raises(ContractError):
            call()
    with pytest.raises(ContractError):
        async with ctx.span("matrix.probe"):
            pass
    ctx.emit("acme.tick", n=1)  # a good name is still accepted, and goes nowhere
    async with ctx.span("acme.probe"):
        ctx.measure("acme.rss", 1.0, kind="gauge")

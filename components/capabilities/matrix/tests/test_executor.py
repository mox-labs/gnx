"""Running flows: inputs in, a Run out, every value recorded, failures keep what was done."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from matrix import (
    ComponentError,
    ConfigError,
    ContractError,
    Executor,
    Flow,
    Inputs,
    Limits,
    Member,
    Port,
    RecordingObserver,
    RunError,
    compile_flow,
)

TEXT = "acme.v1.text"
SCORE = "acme.v1.score"


class Upper:
    requires = {"text": TEXT}
    provides = {"upper": TEXT}

    def __init__(self) -> None:
        self.seen: list[Inputs] = []

    async def run(self, inputs: Inputs) -> dict[str, Any]:
        self.seen.append(inputs)
        return {"upper": inputs["text"].upper()}


class Emit:
    requires: dict[str, Any] = {}

    def __init__(self, value: Any, delay: float = 0.0, port: str = "out", type_url: str = TEXT):
        self.provides = {port: type_url}
        self._value, self._delay, self._port = value, delay, port

    async def run(self, inputs: Inputs) -> dict[str, Any]:
        await asyncio.sleep(self._delay)
        return {self._port: self._value}


class Collect:
    requires = {"all": Port(TEXT, many=True)}
    provides = {"joined": TEXT}

    async def run(self, inputs: Inputs) -> dict[str, Any]:
        return {"joined": "+".join(inputs["all"])}


class Boom:
    requires: dict[str, Any] = {}
    provides = {"out": TEXT}

    async def run(self, inputs: Inputs) -> dict[str, Any]:
        raise RuntimeError("kaput")


async def test_inputs_flow_through_and_land_in_the_construct() -> None:
    upper = Upper()
    compiled = compile_flow(
        Flow(
            "upper",
            (Member("up", upper, {"text": "q"}), Member("e", Emit("x"), {"out": "r"})),
            inputs={"q": TEXT},
        )
    )
    run = await Executor().run(compiled, {"q": "hello"})
    assert run.status == "completed" and run.ended is not None
    assert run.outputs("q") == ("hello",)
    assert run.outputs("r") == ("x",)
    assert run.construct.last("q").producer == "$input"
    seen = upper.seen[0]
    assert dict(seen) == {"text": "hello"}  # only what it bound
    assert seen.context.member == "up" and seen.context.run_id == run.run_id


async def test_provides_bound_to_topics_are_recorded_with_provenance() -> None:
    compiled = compile_flow(
        Flow(
            "two",
            (
                Member("src", Emit("abc"), {"out": "t"}),
                Member("up", Upper(), {"text": "t"}),
            ),
        )
    )
    run = await Executor().run(compiled)
    artifact = run.construct.last("t")
    assert (artifact.producer, artifact.port, artifact.type_url) == ("src", "out", TEXT)
    assert [a.topic for a in run.construct.ledger] == ["t"]  # Upper's output was not bound


async def test_fan_in_delivers_every_value_in_declaration_order() -> None:
    compiled = compile_flow(
        Flow(
            "fan",
            (
                Member("slow", Emit("a", delay=0.02), {"out": "t"}),
                Member("fast", Emit("b"), {"out": "t"}),
                Member("c", Collect(), {"all": "t", "joined": "j"}),
            ),
        )
    )
    run = await Executor().run(compiled, limits=Limits(concurrency=2))
    assert run.outputs("j") == ("a+b",)  # declaration order, not finishing order
    assert [a.producer for a in run.construct.rows("t")] == ["slow", "fast"]


async def test_a_compiled_flow_runs_again_with_new_inputs() -> None:
    compiled = compile_flow(Flow("u", (Member("up", Upper(), {"text": "q"}),), inputs={"q": TEXT}))
    executor = Executor()
    first = await executor.run(compiled, {"q": "a"})
    second = await executor.run(compiled, {"q": "b"})
    assert first.run_id != second.run_id
    assert (first.outputs("q"), second.outputs("q")) == (("a",), ("b",))


@pytest.mark.parametrize(
    ("given", "says"),
    [({}, "missing inputs ['q']"), ({"q": "x", "z": 1}, "unknown inputs ['z']")],
)
async def test_inputs_must_match_the_declaration(given: dict[str, Any], says: str) -> None:
    compiled = compile_flow(Flow("u", (Member("up", Upper(), {"text": "q"}),), inputs={"q": TEXT}))
    with pytest.raises(ConfigError) as e:
        await Executor().run(compiled, given)
    assert says in str(e.value)


async def test_undeclared_or_missing_outputs_break_the_contract() -> None:
    class Wrong:
        requires: dict[str, Any] = {}
        provides = {"a": TEXT, "b": TEXT}

        async def run(self, inputs: Inputs) -> dict[str, Any]:
            return {"a": "x", "c": "y"}

    compiled = compile_flow(Flow("w", (Member("w", Wrong(), {"a": "t"}),)))
    with pytest.raises(RunError) as e:
        await Executor().run(compiled)
    assert e.value.kind == "contract"
    assert "undeclared ['c']" in str(e.value) and "missing ['b']" in str(e.value)


async def test_returning_a_non_mapping_breaks_the_contract() -> None:
    class Bare:
        requires: dict[str, Any] = {}
        provides = {"a": TEXT}

        async def run(self, inputs: Inputs) -> Any:
            return "x"

    with pytest.raises(RunError, match="returned str"):
        await Executor().run(compile_flow(Flow("b", (Member("b", Bare()),))))


async def test_values_validate_against_registered_payload_schemas() -> None:
    class Score(BaseModel):
        value: float

    compiled = compile_flow(
        Flow("s", (Member("s", Emit({"value": "high"}, type_url=SCORE), {"out": "t"}),)),
        schemas={SCORE: Score},
    )
    with pytest.raises(RunError) as e:
        await Executor().run(compiled)
    assert isinstance(e.value.__cause__, ContractError)
    assert "not a valid acme.v1.score" in str(e.value)


async def test_a_failure_keeps_the_partial_run_and_finished_siblings() -> None:
    compiled = compile_flow(
        Flow(
            "f",
            (
                Member("ok", Emit("kept"), {"out": "t"}),
                Member("bad", Boom(), {"out": "u"}),
                Member("later", Upper(), {"text": "t"}),
            ),
        )
    )
    with pytest.raises(RunError) as e:
        await Executor().run(compiled)
    run = e.value.run
    assert run.status == "failed" and e.value.member == "bad"
    assert run.outputs("t") == ("kept",)  # the sibling's work was not lost
    assert run.failures == [
        {
            "member": "bad",
            "kind": "component",
            "message": "member 'bad' failed: RuntimeError: kaput",
        }
    ]
    assert isinstance(e.value.__cause__, ComponentError)
    assert e.value.kind == "component"


async def test_member_timeout_is_a_component_failure_naming_it() -> None:
    compiled = compile_flow(Flow("t", (Member("slow", Emit("x", delay=1.0)),)))
    with pytest.raises(RunError, match="'slow' timed out after 0.01s"):
        await Executor().run(compiled, limits=Limits(member_timeout_s=0.01))


def test_limits_are_checked_when_made() -> None:
    with pytest.raises(ConfigError):
        Limits(concurrency=0)
    with pytest.raises(ConfigError):
        Limits(member_timeout_s=0)


async def test_observers_see_the_run_and_one_failing_observer_cannot_stop_it() -> None:
    class Broken:
        def on_event(self, event: Any) -> None:
            raise RuntimeError("observer bug")

    recorder = RecordingObserver()
    compiled = compile_flow(Flow("o", (Member("e", Emit("x"), {"out": "t"}),)))
    run = await Executor([Broken(), recorder]).run(compiled)
    assert run.status == "completed"
    assert recorder.names() == [
        "run.start",
        "member.start",
        "member.end",
        "artifact.append",
        "run.end",
    ]
    end = recorder.events[-1]
    assert end.fields["status"] == "completed" and end.fields["artifacts"] == 1


async def test_the_flow_is_not_changed_by_running_it() -> None:
    flow = Flow("u", (Member("up", Upper(), {"text": "q"}),), inputs={"q": TEXT})
    before = repr(flow)
    await Executor().run(compile_flow(flow), {"q": "x"})
    assert repr(flow) == before

"""One model call as a component: what a model step requires, provides and refuses."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from pydantic import BaseModel

from matrix import (
    MODEL_CALL,
    MODEL_STEP,
    PROMPT,
    CompilationError,
    ConfigError,
    Inputs,
    MatrixConfig,
    ModelCall,
    ModelRuntimeError,
    ModelStep,
    ModelStepConfig,
    NotFoundError,
    RunContext,
    RunError,
    compose,
    default_registry,
)
from matrix.adapters._out.runtime.mock import MockModelRuntime, MockModelRuntimeConfig

CLAIMS = "acme.v1.claims"
RATING = "acme.v1.rating"


class Rating(BaseModel):
    score: int


def container(reply: str = '{"score": 4}', **more: Any) -> Any:
    return compose(
        MatrixConfig.model_validate(
            {"model_runtimes": {"m": {"type": "mock", "default": reply}}, **more}
        ),
        registry=default_registry(discover=False),
    )


def _step(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"name": "rate", "type": MODEL_STEP, "config": {"runtime": "m", **config}}]


async def test_a_model_step_renders_its_prompt_and_records_the_call() -> None:
    box = container()
    wiring = box.wire(
        _step({"inputs": {"claims": CLAIMS}, "prompt": "Rate:\n{claims}", "system": "Be fair."}),
        inputs=[CLAIMS],
    )
    assert wiring.components["rate"].requires == {CLAIMS}
    assert wiring.components["rate"].provides == {MODEL_CALL}
    run = await box.executor().run(wiring, {CLAIMS: "b is added"})
    (call,) = run.rows(MODEL_CALL)
    assert isinstance(call, ModelCall)
    assert (call.prompt, call.text, call.family) == ("Rate:\nb is added", '{"score": 4}', "mock")
    assert box.model_runtime("m").calls == [(None, "Rate:\nb is added")]


async def test_an_output_with_a_registered_schema_is_asked_for_as_that_structure() -> None:
    box = container()
    box.registry.register_payload(RATING, Rating)
    wiring = box.wire(_step({"model": "judge", "output": RATING}), inputs=[PROMPT])
    run = await box.executor().run(wiring, {PROMPT: "rate it"})
    assert run.rows(RATING) == (Rating(score=4),)
    assert run.rows(MODEL_CALL)[0].value == {"score": 4}


async def test_an_output_without_a_schema_is_read_as_one_json_document() -> None:
    box = container(reply='Here:\n```json\n{"score": 2}\n```')
    wiring = box.wire(_step({"output": RATING}), inputs=[PROMPT])
    run = await box.executor().run(wiring, {PROMPT: "rate it"})
    assert run.rows(RATING) == ({"score": 2},)


async def test_a_reply_without_json_breaks_the_declared_output() -> None:
    box = container(reply="a four, I'd say")
    wiring = box.wire(_step({"output": RATING}), inputs=[PROMPT])
    with pytest.raises(RunError) as e:
        await box.executor().run(wiring, {PROMPT: "rate it"})
    assert (e.value.kind, e.value.cause_kind) == ("run_failed", "contract")
    assert "a four" in str(e.value) and "register a schema" in (e.value.fix or "")


def test_an_unknown_model_is_refused_when_the_step_is_built() -> None:
    """Regression (mx-src F12): with the model runtime, an unknown model name got through
    compose, `matrix check` and `matrix plan`, then failed at run time as ``component``
    (exit 8). The step checks it with its runtime when it is built: ``config`` (exit 3)."""
    pytest.importorskip("hardline")
    box = compose(
        MatrixConfig.model_validate(
            {
                "models": {"models": {"q": {"backend": "mock", "model": "q", "family": "qwen"}}},
                "model_runtimes": {"fast": {"type": "hardline"}},
            }
        ),
        registry=default_registry(discover=False),
    )
    step = [{"name": "rate", "type": MODEL_STEP, "config": {"runtime": "fast", "model": "no-such"}}]
    with pytest.raises(CompilationError) as e:
        box.wire(step, inputs=[PROMPT])
    (found,) = e.value.problems
    assert found.path == "components.rate.config" and "no-such" in found.message
    assert e.value.exit_code == 3 and "q" in (found.fix or "")
    ok = [{"name": "rate", "type": MODEL_STEP, "config": {"runtime": "fast", "model": "q"}}]
    box.wire(ok, inputs=[PROMPT])


@pytest.mark.parametrize(
    ("prompt", "said"),
    [("Rate:\n{claim}", "'claim'"), ("Rate them", "'claims'"), ("Rate {}", "positional")],
)
def test_a_prompt_template_is_checked_against_its_inputs_when_the_step_is_built(
    prompt: str, said: str
) -> None:
    with pytest.raises(ConfigError, match=said):
        ModelStep(
            ModelStepConfig(runtime="m", inputs={"claims": CLAIMS}, prompt=prompt),
            model_runtimes={"m": MockModelRuntime()},
        )


def test_an_unknown_model_runtime_names_the_composed_ones() -> None:
    with pytest.raises(NotFoundError, match="Composed: m"):
        ModelStep(ModelStepConfig(runtime="ghost"), model_runtimes={"m": MockModelRuntime()})


def test_several_inputs_need_a_prompt_template() -> None:
    with pytest.raises(ConfigError, match="no prompt template"):
        ModelStep(
            ModelStepConfig(runtime="m", inputs={"a": CLAIMS, "b": RATING}),
            model_runtimes={"m": MockModelRuntime()},
        )


async def test_a_single_input_is_the_prompt_as_it_stands() -> None:
    mock = MockModelRuntime(MockModelRuntimeConfig(responses={"hello": "hi"}))
    step = ModelStep(ModelStepConfig(runtime="m"), model_runtimes={"m": mock})
    out = await step.run(Inputs({PROMPT: ("hello",)}), RunContext("r", "s"))
    assert out[MODEL_CALL].text == "hi" and step.requires == {PROMPT}


def test_an_agent_bound_to_a_model_runtime_is_refused_with_the_step_that_fits() -> None:
    """A model call has no loop and no tools: an agent cannot run on one."""
    with pytest.raises(ConfigError) as e:
        container(agents={"a": {"runtime": "m", "system_prompt": "x"}})
    (found,) = e.value.problems
    assert found.path == "matrix.agents.a.runtime" and "a model runtime" in found.message
    assert "model-step" in (found.fix or "")


def test_two_model_steps_each_name_the_kind_of_their_reply() -> None:
    box = container()
    step = {"type": MODEL_STEP, "config": {"runtime": "m"}}
    with pytest.raises(CompilationError, match="a kind has one provider") as e:
        box.wire([{"name": "one", **step}, {"name": "two", **step}], inputs=[PROMPT])
    (clash,) = e.value.problems
    assert (clash.path, clash.got) == ("components.two.config.response", MODEL_CALL)


def test_a_model_step_carries_its_runtime_s_effects_and_the_mock_is_a_stand_in() -> None:
    box = container()
    wiring = box.wire(_step({}), inputs=[PROMPT])
    assert wiring.effects == {"rate": frozenset()}
    assert box.registry.entry("model-runtime", "matrix.v1.model-runtime.mock").stand_in
    assert not box.registry.entry("model-runtime", "matrix.v1.model-runtime.hardline").stand_in


def test_a_model_step_s_fingerprint_follows_its_runtime_s_config() -> None:
    one = container(reply="a").wire(_step({}), inputs=[PROMPT]).fingerprints["rate"]
    two = container(reply="b").wire(_step({}), inputs=[PROMPT]).fingerprints["rate"]
    assert one != two
    assert one == container(reply="a").wire(_step({}), inputs=[PROMPT]).fingerprints["rate"]


def test_a_model_step_closes_its_runtime_s_connections_in_the_loop_that_made_them(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regression (W6.8): hardline keeps a model client per event loop and can close it only
    in that loop, while it runs. Nothing in matrix called hardline's ``aclose()``, so each
    run left its clients open, and the next loop to use the runtime logged that the last one
    had ended with them open. A model step now closes its runtime's connections when the run
    that made the calls ends, in that run's loop."""
    hardline = pytest.importorskip("hardline")
    httpx2 = pytest.importorskip("httpx2")
    from hardline.adapters._out.openai_compat import OpenAICompatBackend

    from matrix import Executor, wire
    from matrix.adapters._out.runtime.model import HardlineModelRuntime

    def reply(request: Any) -> Any:
        return httpx2.Response(
            200,
            json={
                "id": "r-1",
                "model": "q-1",
                "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
        )

    row = {"backend": "openai-compat", "model": "q-1", "family": "q", "base_url": "http://q/v1"}
    runtime = HardlineModelRuntime(
        hardline.build_runtime(
            config={"default": "q", "models": {"q": row}},
            backends={"openai-compat": OpenAICompatBackend(transport=httpx2.MockTransport(reply))},
        )
    )
    step = ModelStep(ModelStepConfig(runtime="m"), model_runtimes={"m": runtime})
    wiring = wire({"rate": step}, inputs=[PROMPT])
    caplog.set_level(logging.WARNING, logger="hardline")
    for _ in range(2):  # two event loops, as two `asyncio.run`s of a host make
        run = asyncio.run(Executor().run(wiring, {PROMPT: "rate it"}))
        assert run.rows(MODEL_CALL)[0].text == "ok"
    assert not [r for r in caplog.records if "left open" in r.getMessage()]


class _Replies:
    """A model runtime that answers every call with one record, as given."""

    def __init__(self, call: ModelCall) -> None:
        self._call = call

    def check(self, model: str | None) -> None:
        pass

    async def call(self, request: Any) -> ModelCall:
        return self._call


@pytest.mark.parametrize(
    ("reply", "reason", "said", "fix"),
    [
        (
            ModelCall(text="The answer is", finish_reason="length"),
            "failed",
            "cut off at max_tokens",
            "max_tokens",
        ),
        (
            ModelCall(text="I can't help.", finish_reason="refusal", refusal="I can't help."),
            "refused",
            'refused: "I can\'t help."',
            "prompt",
        ),
        (ModelCall(text="", finish_reason="content_filter"), "refused", "withheld", "prompt"),
        (ModelCall(text="", finish_reason="stop"), "failed", "empty", "prompt"),
        (ModelCall(text="{}", finish_reason="tool_call"), "failed", "asked for a tool", "tool"),
    ],
)
async def test_a_reply_that_is_not_a_whole_answer_fails_the_step_saying_why(
    reply: ModelCall, reason: str, said: str, fix: str
) -> None:
    """W3.3: a reply cut at max_tokens, refused, withheld or empty never stands on the board
    as an answer, for a consumer to take as one. The step fails with the runtime's
    ``refused`` or ``failed`` reason and says which."""
    step = ModelStep(ModelStepConfig(runtime="m"), model_runtimes={"m": _Replies(reply)})
    with pytest.raises(ModelRuntimeError) as e:
        await step.run(Inputs({PROMPT: ("rate it",)}), RunContext("r", "rate"))
    assert e.value.reason == reason and e.value.kind == "component"
    assert said in str(e.value) and fix in (e.value.fix or "")
    assert e.value.details["finish_reason"] == reply.finish_reason


async def test_a_whole_answer_or_one_whose_runtime_does_not_say_is_the_reply() -> None:
    """A runtime that reports no finish reason (``None``) is not taken to have cut the
    reply; the row keeps the ``None`` for a reader to see."""
    for finish in ("stop", None):
        runtime = _Replies(ModelCall(text="4", finish_reason=finish))
        step = ModelStep(ModelStepConfig(runtime="m"), model_runtimes={"m": runtime})
        out = await step.run(Inputs({PROMPT: ("rate it",)}), RunContext("r", "rate"))
        assert (out[MODEL_CALL].text, out[MODEL_CALL].finish_reason) == ("4", finish)

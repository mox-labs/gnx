"""The hardline and mock runtimes: what each port's runtimes honour, refuse and record."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from matrix import (
    AgentDefinition,
    AgentRuntime,
    ConfigError,
    ModelCall,
    ModelRequest,
    ModelRuntime,
    ModelRuntimeError,
    TokenUsage,
)
from matrix.adapters._out.runtime.mock import (
    MockAgentRuntime,
    MockAgentRuntimeConfig,
    MockModelRuntime,
    MockModelRuntimeConfig,
)

MODELS = {
    "default": "q",
    "models": {
        "q": {"backend": "mock", "model": "qwen-8b", "family": "qwen", "local": True},
        "h": {"backend": "mock", "model": "haiku", "family": "claude"},
        "down": {
            "backend": "mock",
            "model": "flaky-1",
            "family": "flaky",
            "retries": 0,
            "fallbacks": ["h"],
            "options": {"script": [{"error": "unavailable"}]},
        },
        "fixer": {
            "backend": "mock",
            "model": "fix-1",
            "family": "qwen",
            "options": {"script": ["not json", '{"verdict": "ship"}']},
        },
    },
}


class Review(BaseModel):
    verdict: str


def _hardline(**kw: object):  # type: ignore[no-untyped-def]
    pytest.importorskip("hardline")
    from matrix.adapters._out.runtime.model import (
        HardlineModelRuntime,
        HardlineModelRuntimeConfig,
    )

    return HardlineModelRuntime.from_config(HardlineModelRuntimeConfig(models=MODELS, **kw))  # type: ignore[arg-type]


# --- the hardline model runtime ------------------------------------------------------------


async def test_a_call_records_the_model_that_answered_as_hardline_reported_it() -> None:
    """Regression (mx-tests 4): the model runtime stamped ``num_turns=1`` and
    ``stop="completed"`` as constants, and ``model`` was the registry name, so a truncation,
    a refusal or the provider's id could never show. A call now carries what hardline
    reported, and a fact it does not report is None."""
    runtime = _hardline()
    assert isinstance(runtime, ModelRuntime)
    call = await runtime.call(ModelRequest(prompt="hello", system="be brief"))
    assert call.text == "mock:qwen-8b:hello"
    assert (call.name, call.model, call.family, call.backend) == ("q", "qwen-8b", "qwen", "mock")
    assert call.usage == TokenUsage(input_tokens=3, output_tokens=1)
    assert (call.request_id, call.attempts, call.retries) == ("mock-q-1", 1, 0)
    assert (call.fallback_from, call.finish_reason, call.refusal) == (None, None, None)
    assert not hasattr(call, "num_turns") and not hasattr(call, "stop")


async def test_a_fallback_is_recorded_as_the_model_that_answered() -> None:
    call = await _hardline().call(ModelRequest(prompt="hello", model="down"))
    assert (call.name, call.model, call.family, call.fallback_from) == (
        "h",
        "haiku",
        "claude",
        "down",
    )


async def test_the_default_model_is_the_runtime_s_then_the_registry_s() -> None:
    assert (await _hardline(default_model="h").call(ModelRequest(prompt="x"))).name == "h"
    assert (await _hardline().call(ModelRequest(prompt="x"))).name == "q"


async def test_a_structured_reply_is_validated_and_repaired_by_hardline() -> None:
    call = await _hardline().call(ModelRequest(prompt="review", model="fixer", output=Review))
    assert call.value == {"verdict": "ship"}
    assert call.attempts == 2  # the first reply was not JSON, and hardline asked again


def test_an_unknown_model_is_refused_by_check_naming_the_known_ones() -> None:
    """Regression (mx-src F12): an unknown name composed, checked and planned, and failed at
    run time as ``component``. ``check`` resolves it, as a ``config`` problem."""
    runtime = _hardline()
    with pytest.raises(ConfigError, match="no-such-model") as e:
        runtime.check("no-such-model")
    assert e.value.exit_code == 3 and "q" in (e.value.fix or "")
    runtime.check(None)  # the registry has a default
    runtime.check("h")


def test_hardline_failures_keep_whose_problem_they_are() -> None:
    pytest.importorskip("hardline")
    from hardline import BackendError, SecretError, UnknownModelError

    from matrix.adapters._out.runtime.model import _reason

    assert _reason(BackendError("x", reason="rate_limit")) == "rate_limited"
    assert _reason(BackendError("x", reason="auth")) == "auth"
    assert _reason(BackendError("x", reason="bad_request")) == "incapable"
    assert _reason(SecretError("x")) == "auth"
    assert _reason(UnknownModelError("x")) == "incapable"
    assert _reason(RuntimeError("x")) == "failed"


async def test_a_failed_call_is_a_classified_model_runtime_error() -> None:
    runtime = _hardline()
    with pytest.raises(ModelRuntimeError) as e:
        await runtime.call(ModelRequest(prompt="x", model="ghost"))
    assert (e.value.reason, e.value.kind) == ("incapable", "config")


# --- the mocks: stand-ins for each port ---------------------------------------------------


async def test_the_mock_agent_is_the_agent_port_not_a_lookalike() -> None:
    runtime = MockAgentRuntime(MockAgentRuntimeConfig(responses={"ping": "pong"}))
    assert isinstance(runtime, AgentRuntime)
    d = AgentDefinition(name="a", model="m")
    assert (await runtime.run(d, "ping")).content == "pong"
    assert (await runtime.run(d, "other")).content == "mock:a:other"
    assert runtime.calls == [("a", "ping"), ("a", "other")]


async def test_the_mock_agent_reports_no_cost_because_no_provider_reported_one() -> None:
    """A reported cost is provenance: what a provider said. A mock is not a provider."""
    response = await MockAgentRuntime().run(AgentDefinition(name="a"), "hello")
    assert response.cost_usd is None


async def test_the_mock_model_replies_by_prompt_then_by_model_then_by_default() -> None:
    mock = MockModelRuntime(
        MockModelRuntimeConfig(
            responses={"exact": "by prompt"}, by_model={"m": "by model"}, default="default"
        )
    )
    assert isinstance(mock, ModelRuntime)
    assert (await mock.call(ModelRequest(prompt="exact", model="m"))).text == "by prompt"
    assert (await mock.call(ModelRequest(prompt="other", model="m"))).text == "by model"
    assert (await mock.call(ModelRequest(prompt="other", model="n"))).text == "default"
    echoed = await MockModelRuntime().call(ModelRequest(prompt="p", model="m"))
    assert (echoed.text, echoed.name, echoed.family) == ("mock:m:p", "m", "mock")
    assert mock.calls == [("m", "exact"), ("m", "other"), ("n", "other")]


async def test_the_mock_model_holds_a_structured_reply_to_its_type() -> None:
    mock = MockModelRuntime(MockModelRuntimeConfig(default='```json\n{"verdict": "hold"}\n```'))
    call = await mock.call(ModelRequest(prompt="x", output=Review))
    assert isinstance(call, ModelCall) and call.value == {"verdict": "hold"}
    with pytest.raises(ModelRuntimeError, match="not a Review") as e:
        await MockModelRuntime().call(ModelRequest(prompt="x", output=Review))
    assert "one JSON document" in (e.value.fix or "")

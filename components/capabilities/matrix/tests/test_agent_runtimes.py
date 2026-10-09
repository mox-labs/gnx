"""The model and mock runtimes: what they honour, what they refuse, what they stamp."""

from __future__ import annotations

import pytest

from matrix import AgentDefinition, AgentRuntime, ConfigError
from matrix.adapters._out.runtime.mock import MockRuntime, MockRuntimeConfig
from matrix.adapters._out.runtime.model import ModelAgentRuntime, ModelAgentRuntimeConfig

MODELS = {
    "default": "q",
    "models": {
        "q": {"backend": "mock", "model": "qwen-8b", "family": "qwen", "local": True},
        "h": {"backend": "mock", "model": "haiku", "family": "claude"},
    },
}


def _model_runtime(**kw: object) -> ModelAgentRuntime:
    return ModelAgentRuntime.from_config(ModelAgentRuntimeConfig(models=MODELS, **kw))  # type: ignore[arg-type]


async def test_model_runtime_stamps_family_from_the_model_that_answered() -> None:
    runtime = _model_runtime()
    assert isinstance(runtime, AgentRuntime)
    local = await runtime.run(AgentDefinition(name="a", system_prompt="be brief"), "hello")
    judge = await runtime.run(AgentDefinition(name="j", model="h"), "hello")
    assert (local.family, local.model, local.num_turns) == ("qwen", "q", 1)
    assert local.content == "mock:qwen-8b:hello"
    assert (judge.family, judge.model) == ("claude", "h")


async def test_model_runtime_default_model_config() -> None:
    response = await _model_runtime(default_model="h").run(AgentDefinition(name="a"), "x")
    assert response.family == "claude"


async def test_model_runtime_refuses_a_definition_that_declares_tools() -> None:
    with pytest.raises(ConfigError, match=r"declares tools \['Read'\].*no tool loop"):
        await _model_runtime().run(AgentDefinition(name="a", tools=("Read",)), "x")


async def test_model_runtime_accepts_explicit_no_tools() -> None:
    response = await _model_runtime().run(AgentDefinition(name="a", tools=()), "x")
    assert response.content


async def test_mock_runtime_is_the_port_not_a_lookalike() -> None:
    runtime = MockRuntime(MockRuntimeConfig(responses={"ping": "pong"}))
    assert isinstance(runtime, AgentRuntime)
    d = AgentDefinition(name="a", model="m")
    assert (await runtime.run(d, "ping")).content == "pong"
    assert (await runtime.run(d, "other")).content == "mock:a:other"
    assert runtime.calls == [("a", "ping"), ("a", "other")]

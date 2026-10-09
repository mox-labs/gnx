"""compose(): config in, runtimes + definitions + bound agents out. Errors name key paths."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from matrix import (
    AgentDefinition,
    AgentRuntime,
    BoundAgent,
    ConfigError,
    MatrixConfig,
    Registry,
    compose,
    default_registry,
)
from matrix.adapters._out.runtime.mock import MockRuntime

if TYPE_CHECKING:
    from pathlib import Path


def _agents_dir(tmp_path: Path) -> Path:
    d = tmp_path / "agents"
    d.mkdir()
    (d / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Reviews diffs.\ntools: Read, Grep\n"
        "model: sonnet\ncolor: blue\n---\nYou review carefully.\n"
    )
    return d


def _config(**kw: object) -> MatrixConfig:
    return MatrixConfig.model_validate(kw)


async def test_definition_bound_to_runtime_runs(tmp_path: Path) -> None:
    _agents_dir(tmp_path)
    container = compose(
        _config(
            definitions=["agents"],
            runtimes={"offline": {"type": "mock", "default": "ok"}},
            agents={"reviewer": {"runtime": "offline"}},
        ),
        base_dir=tmp_path,
    )
    agent = container.agent("reviewer")
    assert isinstance(agent, BoundAgent)
    assert agent.definition.system_prompt == "You review carefully."
    assert agent.definition.tools == ("Read", "Grep")
    assert agent.definition.metadata == {"color": "blue"}
    response = await agent.run("look at this")
    assert (response.content, response.family, response.model) == ("ok", "mock", "sonnet")


async def test_one_definition_on_two_runtimes_with_overrides(tmp_path: Path) -> None:
    _agents_dir(tmp_path)
    container = compose(
        _config(
            definitions=["agents"],
            runtimes={"a": {"type": "mock"}, "b": {"type": "mock", "family": "qwen"}},
            agents={
                "reviewer": {"runtime": "a"},
                "reviewer-local": {
                    "runtime": "b",
                    "definition": "reviewer",
                    "model": "q",
                    "tools": [],
                },
            },
        ),
        base_dir=tmp_path,
    )
    local = container.agent("reviewer-local")
    assert local.definition.name == "reviewer-local"
    assert (local.definition.model, local.definition.tools) == ("q", ())
    assert local.definition.system_prompt == "You review carefully."  # inherited
    assert (await local.run("x")).family == "qwen"
    assert container.agent("reviewer").definition.tools == ("Read", "Grep")  # base untouched


def test_inline_agent_needs_no_file() -> None:
    container = compose(
        _config(
            runtimes={"m": {"type": "mock"}},
            agents={"greeter": {"runtime": "m", "system_prompt": "Hi."}},
        )
    )
    assert container.agent("greeter").definition.system_prompt == "Hi."


def test_unknown_runtime_type_lists_registered() -> None:
    with pytest.raises(ConfigError) as e:
        compose(
            _config(runtimes={"x": {"type": "strands"}}), registry=default_registry(discover=False)
        )
    msg = str(e.value)
    assert "runtimes.x.type: 'strands'" in msg and "claude-sdk" in msg and "mock" in msg


def test_runtime_option_typo_names_runtime_and_key() -> None:
    with pytest.raises(ConfigError) as e:
        compose(_config(runtimes={"m": {"type": "mock", "defualt": "x"}}), source="ix.yaml")
    assert "ix.yaml: runtimes.m" in str(e.value) and "defualt" in str(e.value)


def test_agent_naming_an_unconfigured_runtime() -> None:
    with pytest.raises(
        ConfigError, match=r"agents\.a\.runtime: 'sdk' is not a configured runtime. Configured: m"
    ):
        compose(_config(runtimes={"m": {"type": "mock"}}, agents={"a": {"runtime": "sdk"}}))


def test_agent_with_no_definition_and_no_inline_prompt() -> None:
    with pytest.raises(ConfigError, match=r"agents\.ghost: no definition named 'ghost'"):
        compose(_config(runtimes={"m": {"type": "mock"}}, agents={"ghost": {"runtime": "m"}}))


def test_named_definition_that_was_not_loaded_is_an_error_even_with_inline_prompt() -> None:
    with pytest.raises(ConfigError, match="no definition named 'reviewer'"):
        compose(
            _config(
                runtimes={"m": {"type": "mock"}},
                agents={"a": {"runtime": "m", "definition": "reviewer", "system_prompt": "x"}},
            )
        )


def test_missing_definitions_directory(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="does not exist"):
        compose(_config(definitions=["nope"]), base_dir=tmp_path)


def test_duplicate_definition_across_sources(tmp_path: Path) -> None:
    _agents_dir(tmp_path)
    other = tmp_path / "more"
    other.mkdir()
    (other / "reviewer.md").write_text("again")
    with pytest.raises(ConfigError, match="'reviewer' is defined in both"):
        compose(_config(definitions=["agents", "more"]), base_dir=tmp_path)


def test_top_level_models_reach_model_runtimes() -> None:
    container = compose(
        _config(
            models={"models": {"q": {"backend": "mock", "model": "m", "family": "qwen"}}},
            runtimes={"local": {"type": "model"}},
        )
    )
    runtime = container.runtime("local")
    assert runtime.models.registry.names() == ("q",)  # type: ignore[attr-defined]


def test_extension_runtime_registers_by_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    """A third party's runtime arrives the way matrix's own do, under its own namespace."""

    class _Dist:
        name = "acme-strands"

    class _EP:
        name, value, dist = "strands", "pkg:register", _Dist()

        @staticmethod
        def load():  # type: ignore[no-untyped-def]
            def register(registry: Registry) -> None:
                registry.register("runtime", "acme.v1.runtime.strands", lambda: MockRuntime())

            return register

    monkeypatch.setattr("matrix.domain.registry.entry_points", lambda group: [_EP()])
    container = compose(_config(runtimes={"s": {"type": "acme.v1.runtime.strands"}}))
    assert isinstance(container.runtime("s"), MockRuntime)
    entry = container.registry.entry("runtime", "acme.v1.runtime.strands")
    assert entry.origin == "acme-strands"


def test_every_builtin_runtime_satisfies_the_port() -> None:
    from matrix.adapters._out.runtime.claude_sdk import ClaudeSdkRuntime
    from matrix.adapters._out.runtime.model import ModelAgentRuntime

    for cls in (ClaudeSdkRuntime, ModelAgentRuntime, MockRuntime):
        assert callable(getattr(cls, "run", None)), cls
        assert callable(getattr(cls, "check", None)), cls  # check is required by the port
    assert isinstance(MockRuntime(), AgentRuntime)


def test_bound_agent_keeps_definition_immutable() -> None:
    d = AgentDefinition(name="a", system_prompt="p")
    agent = BoundAgent(d, MockRuntime())
    assert agent.definition is d and agent.name == "a"

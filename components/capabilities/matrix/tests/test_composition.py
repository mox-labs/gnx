"""compose(): config in, runtimes + definitions + bound agents out. Errors name key paths."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from importlib.metadata import version
from typing import TYPE_CHECKING

import pytest

from matrix import (
    AgentDefinition,
    BoundAgent,
    ConfigError,
    MatrixConfig,
    NotFoundError,
    Registry,
    compose,
    default_registry,
)
from matrix.adapters._out.runtime.mock import MockAgentRuntime

if TYPE_CHECKING:
    from collections.abc import Mapping
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
            agent_runtimes={"offline": {"type": "mock", "default": "ok"}},
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
            agent_runtimes={"a": {"type": "mock"}, "b": {"type": "mock", "family": "qwen"}},
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
            agent_runtimes={"m": {"type": "mock"}},
            agents={"greeter": {"runtime": "m", "system_prompt": "Hi."}},
        )
    )
    assert container.agent("greeter").definition.system_prompt == "Hi."


def test_unknown_runtime_type_lists_registered() -> None:
    with pytest.raises(ConfigError) as e:
        compose(
            _config(agent_runtimes={"x": {"type": "strands"}}),
            registry=default_registry(discover=False),
        )
    (found,) = e.value.problems
    assert (found.path, found.kind, found.got) == (
        "matrix.agent_runtimes.x.type",
        "name_unknown",
        "strands",
    )
    assert {"claude-sdk", "mock"} <= set(found.allowed)
    assert "hardline" not in found.allowed  # a model runtime is no agent runtime


def test_unknown_model_runtime_type_lists_the_registered_model_runtimes() -> None:
    with pytest.raises(ConfigError) as e:
        compose(
            _config(model_runtimes={"x": {"type": "claude-sdk"}}),
            registry=default_registry(discover=False),
        )
    (found,) = e.value.problems
    assert (found.path, found.kind, found.got) == (
        "matrix.model_runtimes.x.type",
        "name_unknown",
        "claude-sdk",
    )
    assert set(found.allowed) == {"hardline", "mock"}


def test_runtime_option_typo_names_runtime_and_key() -> None:
    with pytest.raises(ConfigError) as e:
        compose(_config(agent_runtimes={"m": {"type": "mock", "defualt": "x"}}), source="ix.matrix")
    assert [(p.path, p.kind) for p in e.value.problems] == [
        ("ix.matrix.agent_runtimes.m.defualt", "unknown_key")
    ]


def test_agent_naming_an_unconfigured_runtime() -> None:
    with pytest.raises(ConfigError) as e:
        compose(
            _config(
                agent_runtimes={"m": {"type": "mock"}},
                agents={"a": {"runtime": "sdk", "system_prompt": "x"}},
            )
        )
    (found,) = e.value.problems
    assert (found.path, found.kind, found.got, found.allowed) == (
        "matrix.agents.a.runtime",
        "name_unknown",
        "sdk",
        ("m",),
    )


def test_agent_with_no_definition_and_no_inline_prompt() -> None:
    with pytest.raises(ConfigError, match=r"no definition named 'ghost'") as e:
        compose(_config(agent_runtimes={"m": {"type": "mock"}}, agents={"ghost": {"runtime": "m"}}))
    assert [p.path for p in e.value.problems] == ["matrix.agents.ghost.definition"]


def test_every_composition_problem_is_reported_at_once() -> None:
    """Regression (S14): a bad runtime type hid the agent naming a runtime that is not there."""
    with pytest.raises(ConfigError) as e:
        compose(
            _config(
                agent_runtimes={"m": {"type": "nope"}},
                agents={"a": {"runtime": "zz", "system_prompt": "x"}},
            )
        )
    assert [p.path for p in e.value.problems] == [
        "matrix.agent_runtimes.m.type",
        "matrix.agents.a.runtime",
    ]
    assert e.value.complete


def test_an_agent_whose_runtime_could_not_be_built_is_reported_as_unchecked() -> None:
    with pytest.raises(ConfigError) as e:
        compose(
            _config(
                agent_runtimes={"m": {"type": "nope"}},
                agents={"a": {"runtime": "m", "system_prompt": "x"}},
            )
        )
    assert [p.path for p in e.value.problems] == ["matrix.agent_runtimes.m.type"]
    assert e.value.complete is False  # the runtime's check of the agent was never made


def test_named_definition_that_was_not_loaded_is_an_error_even_with_inline_prompt() -> None:
    with pytest.raises(ConfigError, match="no definition named 'reviewer'"):
        compose(
            _config(
                agent_runtimes={"m": {"type": "mock"}},
                agents={"a": {"runtime": "m", "definition": "reviewer", "system_prompt": "x"}},
            )
        )


def test_missing_definitions_directory(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no folder or file") as e:
        compose(_config(definitions=["nope"]), base_dir=tmp_path)
    assert [p.path for p in e.value.problems] == ["matrix.definitions"]


def test_duplicate_definition_across_sources(tmp_path: Path) -> None:
    _agents_dir(tmp_path)
    other = tmp_path / "more"
    other.mkdir()
    (other / "reviewer.md").write_text("again")
    with pytest.raises(ConfigError) as e:
        compose(_config(definitions=["agents", "more"]), base_dir=tmp_path)
    (found,) = e.value.problems
    assert (found.kind, found.got, found.source) == (
        "name_taken",
        "reviewer",
        str(other / "reviewer.md"),
    )
    assert str(tmp_path / "agents" / "reviewer.md") in found.message


def test_a_definition_in_two_tiers_is_one_problem_at_the_key_that_read_both(
    tmp_path: Path,
) -> None:
    """Regression: a second config's definitions accumulated onto the first's, and the clash
    came back at the path ``name`` with no fix, followed by a misleading "no definition named
    'claimer' was loaded" for the agent that used it."""
    from matrix import load_config

    for tier in ("first", "second"):
        (tmp_path / tier / "agents").mkdir(parents=True)
        (tmp_path / tier / "agents" / "reviewer.md").write_text("---\nname: reviewer\n---\nHi.\n")
        (tmp_path / tier / "matrix.yaml").write_text(
            "matrix:\n  definitions: [agents]\n  agent_runtimes: {m: {type: mock}}\n"
            "  agents: {reviewer: {runtime: m}}\n"
        )
    loaded = load_config(sources=[tmp_path / "first/matrix.yaml", tmp_path / "second/matrix.yaml"])
    with pytest.raises(ConfigError) as e:
        compose(loaded)
    (found,) = e.value.problems
    assert (found.path, found.kind, found.got) == ("matrix.definitions", "name_taken", "reviewer")
    assert "read together" in found.message and "--only" in found.fix
    assert e.value.complete is False  # the agent that uses it was not checked


def test_top_level_models_reach_model_runtimes() -> None:
    container = compose(
        _config(
            models={"models": {"q": {"backend": "mock", "model": "m", "family": "qwen"}}},
            model_runtimes={"local": {"type": "hardline"}},
        )
    )
    runtime = container.model_runtime("local")
    assert runtime.models.registry.names() == ("q",)  # type: ignore[attr-defined]


# --- the reuse fingerprint covers every need a component is handed ------------------------


def _step_print(models: object = None) -> str:
    """The fingerprint of a model step on a hardline model runtime."""
    container = compose(
        _config(
            models=models,
            model_runtimes={"m": {"type": "hardline"}},
            components=[{"name": "s", "type": "matrix.v1.model-step", "config": {"runtime": "m"}}],
            inputs=["matrix.v1.prompt"],
        ),
        registry=default_registry(discover=False),
    )
    return container.wire().fingerprints["s"]


def _agent_step_print(runtime: object, **context: object) -> str:
    container = compose(
        _config(
            agent_runtimes={"m": runtime},
            agents={"a": {"runtime": "m", "system_prompt": "x"}},
            components=[{"name": "s", "type": "matrix.v1.agent-step", "config": {"agent": "a"}}],
            inputs=["matrix.v1.task"],
        ),
        registry=default_registry(discover=False),
        context=context or None,
    )
    return container.wire().fingerprints["s"]


def _row(model: str, base_url: str) -> dict[str, object]:
    return {"backend": "openai-compat", "model": model, "base_url": base_url, "family": "q"}


def test_a_model_step_s_fingerprint_follows_matrix_models() -> None:
    one = _step_print({"default": "x", "models": {"x": _row("m1", "http://a/v1")}})
    two = _step_print({"default": "x", "models": {"x": _row("m2", "http://b/v1")}})
    assert one != two
    assert one == _step_print({"default": "x", "models": {"x": _row("m1", "http://a/v1")}})


def test_a_model_step_s_fingerprint_follows_the_tiers_hardline_discovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no ``matrix.models``, hardline reads its own tiers; they decide the model too."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    tier = tmp_path / "tier.yaml"
    monkeypatch.setenv("HARDLINE_CONFIG", str(tier))
    tier.write_text(
        "default: x\nmodels:\n  x: {backend: openai-compat, model: m1, family: q,"
        " base_url: 'http://a/v1'}\n"
    )
    one = _step_print()
    tier.write_text(
        "default: x\nmodels:\n  x: {backend: openai-compat, model: m2, family: q,"
        " base_url: 'http://b/v1'}\n"
    )
    assert _step_print() != one


def test_an_agent_step_s_fingerprint_follows_the_working_directory_it_is_handed() -> None:
    sdk = {"type": "claude-sdk"}
    assert _agent_step_print(sdk, cwd="/srv/a") != _agent_step_print(sdk, cwd="/srv/b")


def test_a_component_handed_the_agents_is_fingerprinted_by_them() -> None:
    class UsesAgents:
        requires = ("acme.v1.in",)
        provides = ("acme.v1.out",)

        def __init__(self, *, agents: object) -> None:
            self.agents = agents

        async def run(self, inputs: object, ctx: object) -> None:
            return None

    def fingerprint(prompt: str) -> str:
        registry = default_registry(discover=False)
        registry.register("component", "acme.v1.uses", UsesAgents, needs={"agents"})
        container = compose(
            _config(
                agent_runtimes={"m": {"type": "mock"}},
                agents={"a": {"runtime": "m", "system_prompt": prompt}},
                components=[{"name": "u", "type": "acme.v1.uses"}],
                inputs=["acme.v1.in"],
            ),
            registry=registry,
        )
        return container.wire().fingerprints["u"]

    assert fingerprint("judge strictly") != fingerprint("judge kindly")


def test_extension_runtime_registers_by_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    """A third party's runtime arrives the way matrix's own do, under its own namespace."""

    class _Dist:
        name = "acme-strands"

    class _EP:
        name, value, dist = "strands", "pkg:register", _Dist()

        @staticmethod
        def load():  # type: ignore[no-untyped-def]
            def register(registry: Registry) -> None:
                registry.register(
                    "agent-runtime", "acme.v1.agent-runtime.strands", lambda: MockAgentRuntime()
                )

            return register

    monkeypatch.setattr("matrix.composition.container.entry_points", lambda group: [_EP()])
    container = compose(_config(agent_runtimes={"s": {"type": "acme.v1.agent-runtime.strands"}}))
    assert isinstance(container.agent_runtime("s"), MockAgentRuntime)
    entry = container.registry.entry("agent-runtime", "acme.v1.agent-runtime.strands")
    assert entry.origin == "acme-strands"


def test_bind_puts_a_held_definition_on_a_configured_runtime_with_the_observers(
    tmp_path: Path,
) -> None:
    """Container.bind is public and nothing inside matrix calls it: held here to what the
    API reference says it does."""
    events = tmp_path / "events.jsonl"
    container = compose(
        _config(
            agent_runtimes={"offline": {"type": "mock", "default": "ok"}},
            observers=[{"type": "jsonl", "path": str(events)}],
        )
    )
    agent = container.bind(AgentDefinition(name="held", system_prompt="p"), "offline")
    assert agent.runtime is container.agent_runtime("offline")
    assert asyncio.run(agent.run("t")).content == "ok"
    assert '"matrix.agent.end"' in events.read_text()  # the container's observers saw it
    with pytest.raises(NotFoundError):
        container.bind(AgentDefinition(name="held", system_prompt="p"), "nope")


def test_bound_agent_keeps_definition_immutable() -> None:
    d = AgentDefinition(name="a", system_prompt="p")
    agent = BoundAgent(d, MockAgentRuntime())
    assert agent.definition is d and agent.name == "a"


@pytest.mark.parametrize(
    ("module", "config", "extra"),
    [
        ("claude_agent_sdk", {"agent_runtimes": {"r": {"type": "claude-sdk"}}}, "claude"),
        ("hardline", {"model_runtimes": {"r": {"type": "hardline"}}}, "models"),
    ],
)
def test_selecting_what_a_missing_extra_provides_is_a_config_error_naming_the_extra(
    monkeypatch: pytest.MonkeyPatch, module: str, config: dict[str, object], extra: str
) -> None:
    """Regression: a missing extra raised a bare ImportError, with no kind and no fix."""
    monkeypatch.setitem(sys.modules, module, None)
    with pytest.raises(ConfigError) as raised:
        compose(_config(**config))
    error = raised.value.payload()["error"]
    assert error["kind"] == "config"
    (found,) = error["problems"]
    assert (found["kind"], found["got"]) == ("extra_missing", module)
    tag = f"matrix-v{version('matrix')}"  # the release the installed matrix is
    requirement = f'"matrix[{extra}] @ git+https://github.com/mox-labs/gnx@{tag}#subdirectory='
    assert f"uv add {requirement}" in error["fix"]  # never `pip install matrix`, which is
    assert f"uv tool install --with {requirement}" in error["fix"]  # another package
    assert "pip install" not in error["fix"] and found["fix"] == error["fix"]


def test_matrix_imports_and_composes_with_no_extra_installed() -> None:
    script = (
        "import sys\n"
        "for name in ('claude_agent_sdk', 'hardline', 'opentelemetry'):\n"
        "    sys.modules[name] = None\n"
        "import matrix\n"
        "container = matrix.compose(matrix.MatrixConfig.model_validate(\n"
        "    {'agent_runtimes': {'r': {'type': 'mock'}}}))\n"
        "assert container.agent_runtime('r') is not None\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_an_agent_step_s_fingerprint_follows_its_agent_s_definition_and_runtime() -> None:
    """The reuse fingerprint covers what an agent is and where it runs: its prompt, model and
    tools, and its runtime's options (data-model.md; mx-tests 11)."""

    def printed(agent: Mapping[str, object], runtime: Mapping[str, object]) -> str:
        container = compose(
            _config(
                agent_runtimes={"m": {"type": "mock", **runtime}},
                agents={"a": {"runtime": "m", **agent}},
                components=[
                    {"name": "s", "type": "matrix.v1.agent-step", "config": {"agent": "a"}}
                ],
                inputs=["matrix.v1.task"],
            ),
            registry=default_registry(discover=False),
        )
        return container.wire().fingerprints["s"]

    base = printed({"system_prompt": "x"}, {})
    assert base == printed({"system_prompt": "x"}, {})
    for agent, runtime in (
        ({"system_prompt": "y"}, {}),
        ({"system_prompt": "x", "model": "big"}, {}),
        ({"system_prompt": "x", "tools": []}, {}),
        ({"system_prompt": "x"}, {"default": "canned"}),
    ):
        assert printed(agent, runtime) != base, (agent, runtime)

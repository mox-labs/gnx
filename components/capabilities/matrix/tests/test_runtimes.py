"""The mock runtimes, and what an agent runtime declares: what each port's runtimes honour,
refuse and record. The model runtime that calls a model is the bridge's, and its tests are
``matrix-hardline/tests``."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from matrix import (
    AgentDefinition,
    AgentRuntime,
    ModelCall,
    ModelRequest,
    ModelRuntime,
    ModelRuntimeError,
)
from matrix.adapters._out.runtime.mock import (
    MockAgentRuntime,
    MockAgentRuntimeConfig,
    MockModelRuntime,
    MockModelRuntimeConfig,
)


class Review(BaseModel):
    verdict: str


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
    assert (await mock.call(ModelRequest.from_prompt("exact", model="m"))).text == "by prompt"
    assert (await mock.call(ModelRequest.from_prompt("other", model="m"))).text == "by model"
    assert (await mock.call(ModelRequest.from_prompt("other", model="n"))).text == "default"
    echoed = await MockModelRuntime().call(ModelRequest.from_prompt("p", model="m"))
    assert (echoed.text, echoed.name, echoed.family) == ("mock:m:p", "m", "mock")
    assert mock.calls == [("m", "exact"), ("m", "other"), ("n", "other")]


async def test_the_mock_model_holds_a_structured_reply_to_its_type() -> None:
    mock = MockModelRuntime(MockModelRuntimeConfig(default='```json\n{"verdict": "hold"}\n```'))
    call = await mock.call(ModelRequest.from_prompt("x", output=Review))
    assert isinstance(call, ModelCall) and call.value == {"verdict": "hold"}
    with pytest.raises(ModelRuntimeError, match="not a Review") as e:
        await MockModelRuntime().call(ModelRequest.from_prompt("x", output=Review))
    assert "one JSON document" in (e.value.fix or "")


def test_an_agent_runtime_declares_what_its_sessions_do_as_data() -> None:
    """Whether a session loads skills from settings or from its working directory,
    which tool fires a skill and where in its working directory its skills must be placed,
    whether its tools can write files, and the config that keeps it from reading the machine
    it runs on, are the runtime's to declare when it registers (``capabilities=``, asked
    through ``Entry.capabilities``), so a caller asks the runtime instead of reading option
    names it would have to know."""
    from matrix import AgentCapabilities, default_registry

    registry = default_registry(discover=False)
    claude = registry.entry("agent-runtime", "matrix.v1.agent-runtime.claude-sdk")
    unset = claude.capabilities({})
    assert unset is not None
    # Unset, the CLI's own default decides, which the runtime does not claim to know.
    assert (unset.settings_skills, unset.project_skills, unset.writes_files) == (None,) * 3
    assert unset.hermetic == {"setting_sources": []}
    # Claude Code's answers, whatever the config: a skill fires through its Skill tool, and a
    # session finds the skills it is given in its working directory's .claude/skills.
    assert (unset.skill_tool, unset.skills_dir) == ("Skill", ".claude/skills")
    assert unset.set_by == {
        "settings_skills": "setting_sources",
        "project_skills": "setting_sources",
        "writes_files": "permission_mode",
    }
    hermetic = claude.capabilities({"setting_sources": []})
    assert hermetic is not None
    assert (hermetic.settings_skills, hermetic.project_skills) == (False, False)
    user = claude.capabilities({"setting_sources": ["user"]})
    assert user is not None and (user.settings_skills, user.project_skills) == (True, False)
    planning = claude.capabilities({"permission_mode": "plan"})
    assert planning is not None and planning.writes_files is False
    editing = claude.capabilities({"permission_mode": "acceptEdits"})
    assert editing is not None and editing.writes_files is None
    # A config the runtime refuses has no answers: building it says why.
    assert claude.capabilities({"permission_mode": "sometimes"}) is None
    pydantic_ai = registry.entry("agent-runtime", "matrix.v1.agent-runtime.pydantic-ai")
    # pydantic-ai loads no skills: no tool fires one, and nowhere is one placed.
    assert pydantic_ai.capabilities({"model_runtime": "m"}) == AgentCapabilities(
        settings_skills=False, project_skills=False, skill_tool=None, skills_dir=None
    )
    # The stand-in declares nothing: a dry run is reviewed as the session it stands in for.
    assert registry.entry("agent-runtime", "matrix.v1.agent-runtime.mock").capabilities({}) is None


def test_capabilities_are_declared_at_registration_and_nowhere_else() -> None:
    """A config class with a method named ``capabilities`` declares nothing unless its
    registration says so; and capabilities are an agent runtime's, with a config."""
    from matrix import AgentCapabilities, ConfigError, Registry

    class Named(BaseModel):
        def capabilities(self) -> AgentCapabilities:
            return AgentCapabilities(writes_files=False)

    def build(config: Named) -> MockAgentRuntime:
        return MockAgentRuntime()

    registry = Registry()
    registry.register("agent-runtime", "acme.v1.agent-runtime.named", build, config=Named)
    registry.register(
        "agent-runtime",
        "acme.v1.agent-runtime.declared",
        build,
        config=Named,
        capabilities=Named.capabilities,
    )
    assert registry.entry("agent-runtime", "acme.v1.agent-runtime.named").capabilities({}) is None
    declared = registry.entry("agent-runtime", "acme.v1.agent-runtime.declared")
    assert declared.capabilities({}) == AgentCapabilities(writes_files=False)
    with pytest.raises(ConfigError, match="agent runtime's config"):
        registry.register("component", "acme.v1.thing", build, capabilities=Named.capabilities)
    with pytest.raises(ConfigError, match="agent runtime's config"):
        registry.register(
            "agent-runtime", "acme.v1.agent-runtime.bare", build, capabilities=Named.capabilities
        )

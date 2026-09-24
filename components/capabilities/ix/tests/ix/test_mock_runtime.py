"""Tests for SimulatedRuntime — ix's dry-run AgentRuntime.

Verifies deterministic and stochastic behaviour, conformance to matrix's AgentRuntime port
(the old MockAgent was an Agent lookalike, not a runtime), and response structure.
Source: ix/adapters/_out/mock_runtime.py.
"""

from matrix import AgentDefinition, AgentResponse, AgentRuntime

from ix.adapters._out.mock_runtime import SimulatedRuntime

D = AgentDefinition(name="subject")


class TestProtocol:
    async def test_satisfies_agent_runtime_port(self):
        runtime = SimulatedRuntime()
        assert isinstance(runtime, AgentRuntime)
        result = await runtime.run(D, "test")
        assert isinstance(result, AgentResponse)
        assert result.family == "mock"


class TestDeterministicMode:
    """Without expectations, the simulator always activates the expected skill."""

    async def test_always_activates(self):
        result = await SimulatedRuntime(expected_skill="build-eval").run(D, "anything")
        assert result.tool_calls == ({"name": "Skill", "input": {"skill": "build-eval"}},)

    async def test_content_mentions_skill(self):
        result = await SimulatedRuntime(expected_skill="build-eval").run(D, "How do evals work?")
        assert "build-eval" in result.content

    async def test_canned_response_wins(self):
        runtime = SimulatedRuntime(responses={"q": "def f(): return 1"})
        result = await runtime.run(D, "q")
        assert (result.content, result.tool_calls) == ("def f(): return 1", ())


class TestStochasticMode:
    """With expectations and seed, activation is deterministic per seed."""

    async def test_same_seed_same_output(self):
        r1 = await SimulatedRuntime(seed=42, expectations={"q": True}).run(D, "q")
        r2 = await SimulatedRuntime(seed=42, expectations={"q": True}).run(D, "q")
        assert r1 == r2

    async def test_no_tool_calls_when_not_activated(self):
        """seed=7 with should_not_trigger (10% rate) does not activate."""
        runtime = SimulatedRuntime(seed=7, expectations={"hello": False})
        assert (await runtime.run(D, "hello")).tool_calls == ()

    async def test_activation_rates_match_expectations(self):
        """must_trigger ~90%, should_not ~10% over many trials."""
        runtime = SimulatedRuntime(seed=42, expectations={"must": True, "not": False})
        must = not_ = 0
        for _ in range(100):
            must += bool((await runtime.run(D, "must")).tool_calls)
            not_ += bool((await runtime.run(D, "not")).tool_calls)
        assert must > 70, f"Expected >70 must_trigger activations, got {must}"
        assert not_ < 30, f"Expected <30 should_not activations, got {not_}"

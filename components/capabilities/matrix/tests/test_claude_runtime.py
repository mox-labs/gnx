"""Tests for ClaudeSdkRuntime — the Claude Agent SDK as an AgentRuntime."""

import sys
import types

import pytest

from matrix import AgentDefinition
from matrix.domain.agent import AgentResponse

# --- SDK integration tests (mocked) ---


class _FakeTextBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeToolUseBlock:
    def __init__(self, name: str, input: dict):
        self.name = name
        self.input = input


class _FakeAssistantMessage:
    def __init__(self, content: list):
        self.content = content


class _FakeResultMessage:
    def __init__(
        self,
        duration_ms: int = 100,
        total_cost_usd: float = 0.01,
        usage: dict | None = None,
        num_turns: int = 1,
        subtype: str = "success",
        is_error: bool = False,
        errors: list[str] | None = None,
        api_error_status: int | None = None,
    ):
        self.duration_ms = duration_ms
        self.total_cost_usd = total_cost_usd
        self.usage = usage or {"input_tokens": 50, "output_tokens": 30}
        self.num_turns = num_turns
        self.subtype = subtype
        self.is_error = is_error
        self.errors = errors
        self.api_error_status = api_error_status


@pytest.fixture
def fake_sdk(monkeypatch):
    """Install a fake claude_agent_sdk module into sys.modules."""
    captured: dict = {"calls": []}

    async def fake_query(prompt, options=None):
        captured["calls"].append({"prompt": prompt, "options": options})
        yield _FakeAssistantMessage([_FakeTextBlock(f"echo: {prompt}")])
        yield _FakeResultMessage()

    sdk = types.ModuleType("claude_agent_sdk")
    sdk.query = fake_query  # type: ignore[attr-defined]
    sdk.ClaudeAgentOptions = lambda **kw: kw  # type: ignore[attr-defined]
    sdk.AssistantMessage = _FakeAssistantMessage  # type: ignore[attr-defined]
    sdk.TextBlock = _FakeTextBlock  # type: ignore[attr-defined]
    sdk.ToolUseBlock = _FakeToolUseBlock  # type: ignore[attr-defined]
    sdk.ResultMessage = _FakeResultMessage  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    return captured


@pytest.fixture
def fake_sdk_with_tools(monkeypatch):
    """SDK mock that returns tool use blocks."""
    captured: dict = {"calls": []}

    async def fake_query(prompt, options=None):
        captured["calls"].append({"prompt": prompt, "options": options})
        yield _FakeAssistantMessage(
            [
                _FakeTextBlock("Let me help with that."),
                _FakeToolUseBlock("Skill", {"skill": "build-eval"}),
            ]
        )
        yield _FakeResultMessage(
            duration_ms=250,
            total_cost_usd=0.05,
            usage={"input_tokens": 100, "output_tokens": 80},
            num_turns=2,
        )

    sdk = types.ModuleType("claude_agent_sdk")
    sdk.query = fake_query  # type: ignore[attr-defined]
    sdk.ClaudeAgentOptions = lambda **kw: kw  # type: ignore[attr-defined]
    sdk.AssistantMessage = _FakeAssistantMessage  # type: ignore[attr-defined]
    sdk.TextBlock = _FakeTextBlock  # type: ignore[attr-defined]
    sdk.ToolUseBlock = _FakeToolUseBlock  # type: ignore[attr-defined]
    sdk.ResultMessage = _FakeResultMessage  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    return captured


def _runtime(**config):
    from matrix.adapters._out.runtime.claude_sdk import ClaudeSdkRuntime, ClaudeSdkRuntimeConfig

    return ClaudeSdkRuntime(ClaudeSdkRuntimeConfig(**config))


def _defn(**kw):
    return AgentDefinition(**{"name": "a", **kw})


class TestClaudeSdkRuntime:
    @pytest.mark.anyio
    async def test_run_returns_agent_response(self, fake_sdk):
        response = await _runtime().run(_defn(system_prompt="You are helpful."), "hello")

        assert isinstance(response, AgentResponse)
        assert response.content == "echo: hello"
        assert response.tool_calls == ()
        assert (response.tokens_input, response.tokens_output) == (50, 30)
        assert (response.duration_ms, response.cost_usd, response.num_turns) == (100, 0.01, 1)
        assert response.family == "claude"

    @pytest.mark.anyio
    async def test_run_captures_tool_calls(self, fake_sdk_with_tools):
        response = await _runtime().run(_defn(system_prompt="test"), "write evals")

        assert response.content == "Let me help with that."
        assert response.tool_calls == ({"name": "Skill", "input": {"skill": "build-eval"}},)
        assert (response.duration_ms, response.cost_usd) == (250, 0.05)
        assert (response.tokens_input, response.tokens_output, response.num_turns) == (100, 80, 2)

    @pytest.mark.anyio
    async def test_definition_and_deployment_reach_the_sdk(self, fake_sdk):
        runtime = _runtime(plugins=[{"name": "test"}], setting_sources=[])
        await runtime.run(
            _defn(system_prompt="Be concise.", max_turns=3, tools=("Read",), model="sonnet"), "hi"
        )
        opts = fake_sdk["calls"][0]["options"]
        assert opts["system_prompt"] == "Be concise."
        assert opts["max_turns"] == 3
        assert opts["tools"] == ["Read"]
        assert opts["model"] == "sonnet"
        assert opts["plugins"] == [{"name": "test"}]
        assert opts["setting_sources"] == []

    @pytest.mark.anyio
    async def test_empty_prompt(self, fake_sdk):
        assert (await _runtime().run(_defn(), "")).content == "echo: "

    def test_one_runtime_serves_many_definitions(self, fake_sdk):
        runtime = _runtime()
        a = runtime.options(_defn(name="a", system_prompt="A", max_turns=1))
        b = runtime.options(_defn(name="b", system_prompt="B", max_turns=4))
        assert (a["system_prompt"], a["max_turns"]) == ("A", 1)
        assert (b["system_prompt"], b["max_turns"]) == ("B", 4)

    def test_no_system_prompt_is_sent_as_none(self, fake_sdk):
        assert _runtime().options(_defn())["system_prompt"] is None


class TestToolsNeverFailOpen:
    """Regression: the old adapter passed ``tools=self._allowed_tools or None``.

    ``[]`` — the value meaning *no tools* — is falsy, so it collapsed to ``None``, which the
    SDK reads as *the default toolset*. An agent configured with no tools got all of them.
    The installed SDK documents ``tools=[]`` as "disable all built-in tools".
    """

    def test_empty_tools_are_passed_as_empty_not_dropped(self, fake_sdk):
        opts = _runtime().options(_defn(tools=()))
        assert "tools" in opts and opts["tools"] == []

    def test_unspecified_tools_mean_the_runtime_default(self, fake_sdk):
        assert "tools" not in _runtime().options(_defn(tools=None))


class TestPluginPaths:
    def test_relative_plugin_path_resolves_against_cwd(self, fake_sdk, tmp_path):
        opts = _runtime(cwd=str(tmp_path), plugins=[{"type": "local", "path": "../p"}]).options(
            _defn()
        )
        assert opts["plugins"][0]["path"] == str((tmp_path / "../p").resolve())

    def test_absolute_plugin_path_untouched(self, fake_sdk, tmp_path):
        opts = _runtime(cwd=str(tmp_path), plugins=[{"type": "local", "path": "/abs/p"}]).options(
            _defn()
        )
        assert opts["plugins"][0]["path"] == "/abs/p"


class TestPermissionMode:
    """permission_mode is a permission control, so its default and validation are tested.

    This adapter once defaulted to "bypassPermissions" and nothing in the tree overrode it —
    every agent-backed component ran with permissions bypassed, decided by a constant rather
    than by anyone's configuration. The value arrives from config, so it is validated by the
    runtime's typed config and fails naming the legal set.
    """

    def test_default_is_safe_not_bypass(self, fake_sdk):
        assert _runtime().config.permission_mode == "default", (
            "the default must not grant unconfirmed tool access; bypass has to be asked for"
        )

    def test_bypass_is_still_available_when_asked_for(self, fake_sdk):
        assert _runtime(permission_mode="bypassPermissions").config.permission_mode == (
            "bypassPermissions"
        )

    def test_unknown_mode_fails_through_the_registry_naming_the_legal_set(self):
        from matrix import ConfigError, default_registry, runtime_type_url

        with pytest.raises(ConfigError) as exc:
            default_registry(discover=False).create(
                "runtime",
                runtime_type_url("claude-sdk"),
                {"permission_mode": "bypassPermission"},
                needs={"cwd": None},
                where="ix.yaml",
            )
        msg = str(exc.value)
        assert "ix.yaml" in msg and "permission_mode" in msg
        assert "bypassPermissions" in msg, "the error must name the legal values"

    def test_a_permissive_mode_is_logged_so_the_choice_is_visible(self, fake_sdk, caplog):
        with caplog.at_level("WARNING"):
            _runtime(permission_mode="bypassPermissions")
        assert any("bypassPermissions" in r.getMessage() for r in caplog.records), (
            "selecting a permissive mode must appear in the run record, not only the config"
        )

    def test_safe_mode_is_not_logged(self, fake_sdk, caplog):
        with caplog.at_level("WARNING"):
            _runtime()
        assert not caplog.records, "the safe default should be quiet"


# --- how a session ended ------------------------------------------------------------------


def _install(monkeypatch, *messages):
    async def fake_query(prompt, options=None):
        for m in messages:
            yield m

    sdk = types.ModuleType("claude_agent_sdk")
    sdk.query = fake_query  # type: ignore[attr-defined]
    sdk.ClaudeAgentOptions = lambda **kw: kw  # type: ignore[attr-defined]
    sdk.AssistantMessage = _FakeAssistantMessage  # type: ignore[attr-defined]
    sdk.TextBlock = _FakeTextBlock  # type: ignore[attr-defined]
    sdk.ToolUseBlock = _FakeToolUseBlock  # type: ignore[attr-defined]
    sdk.ResultMessage = _FakeResultMessage  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)


class TestSessionEnding:
    async def test_a_completed_session_says_so(self, fake_sdk):
        assert (await _runtime().run(_defn(), "hi")).stop == "completed"

    async def test_a_turn_limit_is_reported_not_raised(self, monkeypatch):
        """A one-turn routing eval ends at max_turns by design; the Skill call is the result."""
        _install(
            monkeypatch,
            _FakeAssistantMessage([_FakeToolUseBlock("Skill", {"skill": "aces"})]),
            _FakeResultMessage(subtype="error_max_turns", is_error=True),
        )
        response = await _runtime().run(_defn(), "hi")
        assert response.stop == "max_turns"
        assert response.tool_calls[0]["name"] == "Skill"

    async def test_a_failed_session_raises_instead_of_answering(self, monkeypatch):
        from matrix import AgentRuntimeError

        _install(
            monkeypatch,
            _FakeAssistantMessage([_FakeTextBlock("partial")]),
            _FakeResultMessage(
                subtype="error_during_execution", is_error=True, errors=["tool crashed"]
            ),
        )
        with pytest.raises(AgentRuntimeError, match="error_during_execution.*tool crashed"):
            await _runtime().run(_defn(), "hi")

    async def test_an_api_error_names_the_status(self, monkeypatch):
        from matrix import AgentRuntimeError

        _install(monkeypatch, _FakeResultMessage(is_error=True, api_error_status=529))
        with pytest.raises(AgentRuntimeError, match="HTTP 529"):
            await _runtime().run(_defn(), "hi")

    async def test_a_stream_with_no_result_raises(self, monkeypatch):
        from matrix import AgentRuntimeError

        _install(monkeypatch, _FakeAssistantMessage([_FakeTextBlock("cut off")]))
        with pytest.raises(AgentRuntimeError, match="without a result"):
            await _runtime().run(_defn(), "hi")

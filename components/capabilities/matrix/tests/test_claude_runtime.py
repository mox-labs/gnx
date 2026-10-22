"""Tests for ClaudeSdkRuntime — the Claude Agent SDK as an AgentRuntime.

The messages a session streams are the SDK's own dataclasses and the options it is handed
are the SDK's own ``ClaudeAgentOptions``: only ``query`` is replaced, so it starts no CLI. A
fake narrower than the SDK could not tell the runtime it reads a field the SDK lacks, or
hands it an option the SDK refuses (mx-tests 5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

import pytest

import matrix
from matrix import AgentDefinition
from matrix.domain.agent import AgentResponse

pytest.importorskip("claude_agent_sdk")

import claude_agent_sdk as sdk  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, AsyncIterator, Callable
    from pathlib import Path

    from matrix.adapters._out.runtime.claude_sdk import ClaudeSdkRuntime


class _Call(TypedDict):
    """One call of the SDK's ``query``, as the fake recorded it."""

    prompt: str | AsyncIterable[dict[str, Any]]
    options: sdk.ClaudeAgentOptions | None


class _Captured(TypedDict):
    calls: list[_Call]


def _options(captured: _Captured, call: int = 0) -> sdk.ClaudeAgentOptions:
    """The options a session was started with: the runtime always hands the SDK some."""
    options = captured["calls"][call]["options"]
    assert options is not None
    return options


def _assistant(*blocks: sdk.ContentBlock) -> sdk.AssistantMessage:
    return sdk.AssistantMessage(content=list(blocks), model="claude-x")


def _result(**fields: Any) -> sdk.ResultMessage:
    """A result message: a one-turn session that succeeded, with ``fields`` in place."""
    defaults = {
        "subtype": "success",
        "duration_ms": 100,
        "duration_api_ms": 90,
        "is_error": False,
        "num_turns": 1,
        "session_id": "session-1",
        "total_cost_usd": 0.01,
        "usage": {"input_tokens": 50, "output_tokens": 30},
    }
    return sdk.ResultMessage(**{**defaults, **fields})


def _install(
    monkeypatch: pytest.MonkeyPatch, *messages: sdk.Message, echo: bool = False
) -> _Captured:
    """Replace the SDK's ``query`` with one that streams ``messages`` (with ``echo``, the
    prompt back and a success); returns what it was called with, each ``{prompt, options}``."""
    captured: _Captured = {"calls": []}

    async def fake_query(
        *,
        prompt: str | AsyncIterable[dict[str, Any]],
        options: sdk.ClaudeAgentOptions | None = None,
        transport: object | None = None,
    ) -> AsyncIterator[sdk.Message]:
        captured["calls"].append({"prompt": prompt, "options": options})
        echoed: list[sdk.Message] = (
            [_assistant(sdk.TextBlock(f"echo: {prompt}")), _result(result=f"echo: {prompt}")]
            if echo
            else []
        )
        for message in echoed or messages:
            yield message

    monkeypatch.setattr(sdk, "query", fake_query)
    return captured


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> _Captured:
    """A session that echoes its prompt and succeeds."""
    return _install(monkeypatch, echo=True)


@pytest.fixture
def fake_sdk_with_tools(monkeypatch: pytest.MonkeyPatch) -> _Captured:
    """A session that calls a tool."""
    return _install(
        monkeypatch,
        _assistant(
            sdk.TextBlock("Let me help with that."),
            sdk.ToolUseBlock(id="toolu_0", name="Skill", input={"skill": "build-eval"}),
        ),
        _result(
            duration_ms=250,
            total_cost_usd=0.05,
            usage={"input_tokens": 100, "output_tokens": 80},
            num_turns=2,
            result="Let me help with that.",
        ),
    )


def _runtime(**config: Any) -> ClaudeSdkRuntime:
    from matrix.adapters._out.runtime.claude_sdk import ClaudeSdkRuntime, ClaudeSdkRuntimeConfig

    return ClaudeSdkRuntime(ClaudeSdkRuntimeConfig(**config))


def _defn(**kw: Any) -> AgentDefinition:
    return AgentDefinition(**{"name": "a", **kw})


async def test_a_session_reaches_the_workspace_guard(
    refused_live_calls: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The workspace guard refuses the SDK's ``query``. This holds that a session calls that
    very name, so the guard is in its path (ix-tests F12): a ``query`` bound in the runtime's
    module at import would be the real one, beyond the guard's reach. In case one ever is, the
    test first points the CLI at a closed port, away from the user's config and keys."""
    from matrix.adapters._out.runtime import claude_sdk

    assert "query" not in vars(claude_sdk), "the runtime binds the SDK's query past the guard"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(AssertionError, match="a live model session"):
        await _runtime(setting_sources=[]).run(_defn(), "hi")
    assert refused_live_calls == ["claude_agent_sdk.query"]
    refused_live_calls.clear()


class TestClaudeSdkRuntime:
    async def test_run_returns_agent_response(self, fake_sdk: _Captured) -> None:
        response = await _runtime().run(_defn(system_prompt="You are helpful."), "hello")

        assert isinstance(response, AgentResponse)
        assert response.content == "echo: hello"
        assert response.tool_calls == ()
        assert (response.tokens_input, response.tokens_output) == (50, 30)
        assert (response.duration_ms, response.cost_usd, response.num_turns) == (100, 0.01, 1)
        assert response.family == "claude"

    async def test_run_captures_tool_calls(self, fake_sdk_with_tools: _Captured) -> None:
        response = await _runtime().run(_defn(system_prompt="test"), "write evals")

        assert response.content == "Let me help with that."
        (call,) = response.tool_calls
        assert (call["name"], call["input"], call["id"]) == (
            "Skill",
            {"skill": "build-eval"},
            "toolu_0",
        )
        assert (response.duration_ms, response.cost_usd) == (250, 0.05)
        assert (response.tokens_input, response.tokens_output, response.num_turns) == (100, 80, 2)

    async def test_definition_and_deployment_reach_the_sdk(self, fake_sdk: _Captured) -> None:
        runtime = _runtime(plugins=[{"type": "local", "path": "/p"}], setting_sources=[])
        await runtime.run(
            _defn(system_prompt="Be concise.", max_turns=3, tools=("Read",), model="sonnet"), "hi"
        )
        opts = _options(fake_sdk)
        assert isinstance(opts, sdk.ClaudeAgentOptions)
        assert opts.system_prompt == "Be concise."
        assert opts.max_turns == 3
        assert opts.tools == ["Read"]
        assert opts.model == "sonnet"
        assert opts.plugins == [{"type": "local", "path": "/p"}]
        assert opts.setting_sources == []

    async def test_empty_prompt(self, fake_sdk: _Captured) -> None:
        assert (await _runtime().run(_defn(), "")).content == "echo: "

    def test_one_runtime_serves_many_definitions(self, fake_sdk: _Captured) -> None:
        runtime = _runtime()
        a = runtime.options(_defn(name="a", system_prompt="A", max_turns=1))
        b = runtime.options(_defn(name="b", system_prompt="B", max_turns=4))
        assert (a["system_prompt"], a["max_turns"]) == ("A", 1)
        assert (b["system_prompt"], b["max_turns"]) == ("B", 4)

    def test_no_system_prompt_is_sent_as_none(self, fake_sdk: _Captured) -> None:
        assert _runtime().options(_defn())["system_prompt"] is None


class TestToolsNeverFailOpen:
    """Regression: the old adapter passed ``tools=self._allowed_tools or None``.

    ``[]`` — the value meaning *no tools* — is falsy, so it collapsed to ``None``, which the
    SDK reads as *the default toolset*. An agent configured with no tools got all of them.
    The installed SDK documents ``tools=[]`` as "disable all built-in tools".
    """

    def test_empty_tools_are_passed_as_empty_not_dropped(self, fake_sdk: _Captured) -> None:
        opts = _runtime().options(_defn(tools=()))
        assert "tools" in opts and opts["tools"] == []

    def test_unspecified_tools_mean_the_runtime_default(self, fake_sdk: _Captured) -> None:
        assert "tools" not in _runtime().options(_defn(tools=None))


class TestPluginPaths:
    def test_relative_plugin_path_resolves_against_cwd(
        self, fake_sdk: _Captured, tmp_path: Path
    ) -> None:
        opts = _runtime(cwd=str(tmp_path), plugins=[{"type": "local", "path": "../p"}]).options(
            _defn()
        )
        assert opts["plugins"][0]["path"] == str((tmp_path / "../p").resolve())

    def test_absolute_plugin_path_untouched(self, fake_sdk: _Captured, tmp_path: Path) -> None:
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

    def test_default_is_safe_not_bypass(self, fake_sdk: _Captured) -> None:
        assert _runtime().config.permission_mode == "default", (
            "the default must not grant unconfirmed tool access; bypass has to be asked for"
        )

    def test_bypass_is_still_available_when_asked_for(self, fake_sdk: _Captured) -> None:
        assert _runtime(permission_mode="bypassPermissions").config.permission_mode == (
            "bypassPermissions"
        )

    def test_unknown_mode_fails_through_the_registry_naming_the_legal_set(self) -> None:
        from matrix import ConfigError, agent_runtime_type_url, default_registry

        with pytest.raises(ConfigError) as exc:
            default_registry(discover=False).create(
                "agent-runtime",
                agent_runtime_type_url("claude-sdk"),
                {"permission_mode": "bypassPermission"},
                needs={"cwd": None},
                where="ix.yaml",
            )
        msg = str(exc.value)
        assert "ix.yaml" in msg and "permission_mode" in msg
        assert "bypassPermissions" in msg, "the error must name the legal values"

    def test_a_permissive_mode_is_logged_so_the_choice_is_visible(
        self, fake_sdk: _Captured, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from matrix.adapters._out.runtime import claude_sdk

        monkeypatch.setattr(claude_sdk, "_LOGGED", set())
        with caplog.at_level("WARNING"):
            _runtime(permission_mode="bypassPermissions")
        assert any("bypassPermissions" in r.getMessage() for r in caplog.records), (
            "selecting a permissive mode must appear in the run record, not only the config"
        )
        assert {r.name for r in caplog.records} == {matrix.RUNTIME_LOGGER}

    def test_a_permissive_mode_is_logged_once_per_process_not_once_per_runtime(
        self, fake_sdk: _Captured, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: ix builds a runtime per session, and a 20-trial pilot printed the
        warning 20 times. Each runtime still carries it in its ``warnings``."""
        from matrix.adapters._out.runtime import claude_sdk

        monkeypatch.setattr(claude_sdk, "_LOGGED", set())
        with caplog.at_level("WARNING"):
            built = [_runtime(permission_mode="bypassPermissions") for _ in range(20)]
            _runtime(permission_mode="dontAsk")
        logged = [r.getMessage() for r in caplog.records]
        assert len(logged) == 2, logged
        assert sum("bypassPermissions" in line for line in logged) == 1
        assert all(len(runtime.warnings) == 1 for runtime in built)

    def test_safe_mode_is_not_logged(
        self, fake_sdk: _Captured, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            _runtime()
        assert not caplog.records, "the safe default should be quiet"


# --- how a session ended ------------------------------------------------------------------


class TestSessionEnding:
    async def test_a_completed_session_says_so(self, fake_sdk: _Captured) -> None:
        assert (await _runtime().run(_defn(), "hi")).stop == "completed"

    async def test_a_turn_limit_is_reported_not_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A one-turn routing eval ends at max_turns by design; the Skill call is the result."""
        _install(
            monkeypatch,
            _assistant(sdk.ToolUseBlock(id="toolu_1", name="Skill", input={"skill": "aces"})),
            _result(subtype="error_max_turns", is_error=True),
        )
        response = await _runtime().run(_defn(), "hi")
        assert response.stop == "max_turns"
        assert response.tool_calls[0]["name"] == "Skill"

    async def test_a_failed_session_raises_instead_of_answering(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from matrix import AgentRuntimeError

        _install(
            monkeypatch,
            _assistant(sdk.TextBlock("partial")),
            _result(subtype="error_during_execution", is_error=True, errors=["tool crashed"]),
        )
        with pytest.raises(AgentRuntimeError, match="error_during_execution.*tool crashed"):
            await _runtime().run(_defn(), "hi")

    async def test_a_failed_session_s_error_carries_what_its_result_says(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The session failed, but it ran and spent tokens: its id, usage and cost reach the
        session table through the error (``response``)."""
        from matrix import AgentRuntimeError

        _install(
            monkeypatch,
            _assistant(sdk.TextBlock("partial")),
            _result(
                subtype="error_during_execution",
                is_error=True,
                session_id="s-9",
                usage={"input_tokens": 4, "output_tokens": 2, "cache_read_input_tokens": 60},
            ),
        )
        with pytest.raises(AgentRuntimeError) as e:
            await _runtime().run(_defn(), "hi")
        response = e.value.response
        assert response is not None and response.stop is None
        assert (response.session_id, response.tokens_input, response.cost_usd) == (
            "s-9",
            64,
            0.01,
        )

    async def test_a_budget_limit_is_reported_not_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``max_budget_usd`` is the SDK's own stop; matrix records it as the reason the
        session ended and counts nothing against it."""
        _install(
            monkeypatch,
            _assistant(sdk.TextBlock("partial")),
            _result(subtype="error_max_budget_usd", is_error=True, total_cost_usd=0.5),
        )
        response = await _runtime(max_budget_usd=0.5).run(_defn(), "hi")
        assert (response.stop, response.cost_usd) == ("max_budget_usd", 0.5)
        # A session stopped at a limit has no result: what it said is in its transcript.
        assert (response.content, response.transcript) == ("", ("partial",))

    async def test_an_api_error_names_the_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from matrix import AgentRuntimeError

        _install(monkeypatch, _result(subtype="success", is_error=True, api_error_status=529))
        with pytest.raises(AgentRuntimeError, match="HTTP 529"):
            await _runtime().run(_defn(), "hi")

    async def test_a_stream_with_no_result_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from matrix import AgentRuntimeError

        _install(monkeypatch, _assistant(sdk.TextBlock("cut off")))
        with pytest.raises(AgentRuntimeError, match="without a result"):
            await _runtime().run(_defn(), "hi")


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (429, "rate_limited"),
        (401, "auth"),
        (403, "auth"),
        (529, "unavailable"),
        (504, "timeout"),
        (400, "failed"),
        (None, "failed"),
    ],
)
async def test_a_failed_session_s_http_status_is_its_reason(
    status: int | None, reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from matrix import AgentRuntimeError

    _install(monkeypatch, _result(subtype="success", is_error=True, api_error_status=status))
    with pytest.raises(AgentRuntimeError) as raised:
        await _runtime().run(_defn(), "hi")
    assert raised.value.reason == reason


async def test_usage_the_result_does_not_report_is_none_not_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a result without usage was recorded as 0 input and 0 output tokens, a
    constant standing in for a fact. What the result does not report is None."""
    _install(monkeypatch, _result(usage=None, result="done"))
    response = await _runtime().run(_defn(), "hi")
    assert (response.tokens_input, response.tokens_output) == (None, None)
    assert (response.cache_read_tokens, response.cache_write_tokens) == (None, None)
    _install(monkeypatch, _result(usage={"input_tokens": 50, "output_tokens": 30}, result="ok"))
    response = await _runtime().run(_defn(), "hi")
    assert (response.tokens_input, response.tokens_output) == (50, 30)
    assert (response.cache_read_tokens, response.cache_write_tokens) == (None, None)


async def test_a_tool_call_is_timed_from_its_request_to_its_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What lets matrix report each call as an ``execute_tool`` span."""
    _install(
        monkeypatch,
        _assistant(sdk.ToolUseBlock(id="toolu_1", name="Read", input={"path": "x"})),
        sdk.UserMessage(content=[sdk.ToolResultBlock(tool_use_id="toolu_1", is_error=True)]),
        sdk.UserMessage(content="plain text is no tool result"),
        _result(),
    )
    response = await _runtime().run(_defn(), "go")
    (call,) = response.tool_calls
    assert (call["name"], call["id"], call["error"]) == ("Read", "toolu_1", True)
    assert call["started"] <= call["ended"]
    assert response.provider == "anthropic"


def test_a_permissive_mode_is_a_warning_composition_carries(fake_sdk: _Captured) -> None:
    (warning,) = _runtime(permission_mode="bypassPermissions").warnings
    assert (warning.path, warning.kind, warning.got) == (
        "permission_mode",
        "permissive",
        "bypassPermissions",
    )
    assert _runtime().warnings == ()


def test_max_budget_usd_reaches_the_real_sdk_options() -> None:
    """The SDK's own options carry ``max_budget_usd``, checked against the installed SDK, and
    it is left out when unset."""
    from matrix.adapters._out.runtime.claude_sdk import ClaudeSdkRuntime, ClaudeSdkRuntimeConfig

    runtime = ClaudeSdkRuntime(ClaudeSdkRuntimeConfig(max_budget_usd=0.5))
    assert sdk.ClaudeAgentOptions(**runtime.options(_defn())).max_budget_usd == 0.5
    assert "max_budget_usd" not in ClaudeSdkRuntime().options(_defn())


# --- what the runtime accepts, declared in its config -------------------------------------


async def test_inline_subagents_reach_the_sdk_as_its_agent_definitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (mx-src F11): the ``agents`` option was forwarded as plain dicts, and the
    SDK converts each with ``dataclasses.asdict`` once the CLI has started, so every session
    with inline subagents failed. The fake ``query`` does what the SDK's client does."""
    import dataclasses

    seen = _install(monkeypatch, _result())
    runtime = _runtime(
        agents={
            "critic": {
                "description": "Finds faults.",
                "prompt": "Be harsh.",
                "tools": ["Read"],
                "model": "haiku",
                "max_turns": 2,
            }
        }
    )
    await runtime.run(_defn(), "go")
    agents = _options(seen).agents
    assert agents is not None
    (critic,) = agents.values()
    sent = {k: v for k, v in dataclasses.asdict(critic).items() if v is not None}
    assert sent == {
        "description": "Finds faults.",
        "prompt": "Be harsh.",
        "tools": ["Read"],
        "model": "haiku",
        "maxTurns": 2,
    }


def _create(options: dict[str, Any]) -> object:
    from matrix import agent_runtime_type_url, default_registry

    return default_registry(discover=False).create(
        "agent-runtime",
        agent_runtime_type_url("claude-sdk"),
        options,
        needs={"cwd": None},
        where="r",
    )


@pytest.mark.parametrize(
    ("options", "path"),
    [
        (
            {"agents": {"critic": {"description": "d", "prompt": "p", "colour": "red"}}},
            "agents.critic.colour",
        ),
        ({"agents": {"critic": {"prompt": "p"}}}, "agents.critic.description"),
        ({"setting_sources": ["usr"]}, "setting_sources.0"),
        ({"plugins": [{"name": "recon"}]}, "plugins.0"),
        ({"skills": "every"}, "skills"),
    ],
)
def test_what_the_runtime_cannot_play_is_refused_where_it_is_configured(
    options: dict[str, Any], path: str
) -> None:
    """The config schema is the declaration of what a session can be given: an option
    outside it fails at composition, at its key, never inside the SDK after the CLI started."""
    with pytest.raises(matrix.ConfigError) as e:
        _create(options)
    assert any(p.path.startswith(f"r.{path}") for p in e.value.problems), e.value.problems
    assert "matrix describe" in (e.value.fix or "")


def test_skills_reach_the_sdk_options(fake_sdk: _Captured) -> None:
    assert _runtime(skills=["review"]).sdk_options(_defn()).skills == ["review"]
    assert _runtime(skills="all").sdk_options(_defn()).skills == "all"
    assert _runtime().sdk_options(_defn()).skills is None


def test_skills_an_agent_s_tools_leave_no_way_to_use_are_refused_at_binding(
    fake_sdk: _Captured,
) -> None:
    runtime = _runtime(skills=["review"])
    with pytest.raises(matrix.ConfigError, match="only through the Skill tool") as e:
        matrix.BoundAgent(_defn(tools=("Read",)), runtime)
    assert "add Skill" in (e.value.fix or "")
    matrix.BoundAgent(_defn(tools=("Read", "Skill")), runtime)
    matrix.BoundAgent(_defn(tools=None), runtime)  # the SDK's default toolset has it
    matrix.BoundAgent(_defn(tools=("Read",)), _runtime(skills=[]))  # no skills to use


def test_an_option_the_installed_sdk_lacks_is_refused_at_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the SDK accepts is its own dataclasses' fields: an SDK older than an option is a
    config problem with the remedy, not a TypeError inside the session."""
    import dataclasses

    older = dataclasses.make_dataclass(
        "ClaudeAgentOptions",
        [
            (f.name, f.type, dataclasses.field(default=None))
            for f in dataclasses.fields(sdk.ClaudeAgentOptions)
            if f.name != "skills"
        ],
    )
    monkeypatch.setattr(sdk, "ClaudeAgentOptions", older)
    with pytest.raises(matrix.ConfigError, match="has no skills") as e:
        matrix.BoundAgent(_defn(), _runtime(skills=["review"]))
    assert "upgrade" in (e.value.fix or "")
    matrix.BoundAgent(_defn(), _runtime())


# --- what a session reports of itself (W3.4) ----------------------------------------------


async def test_the_reply_is_the_session_s_result_and_its_transcript_is_kept_apart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (mx-src F9): ``content`` glued every assistant text block of every turn
    together and ignored the result, so a multi-turn agent whose final answer was bare JSON
    replied ``'Let me read the diff.{"score": 4}'`` and its step's ``output`` failed."""
    from matrix import AGENT_STEP, Inputs, RunContext
    from matrix.domain.agent_step import AgentStep, AgentStepConfig

    _install(
        monkeypatch,
        _assistant(
            sdk.TextBlock("Let me read the diff."),
            sdk.ToolUseBlock(id="toolu_1", name="Read", input={"path": "d"}),
        ),
        sdk.UserMessage(content=[sdk.ToolResultBlock(tool_use_id="toolu_1")]),
        _assistant(sdk.TextBlock('{"score": 4}')),
        _result(num_turns=2, result='{"score": 4}'),
    )
    agent = matrix.BoundAgent(_defn(), _runtime())
    response = await agent.run("review")
    assert response.content == '{"score": 4}'
    assert response.transcript == ("Let me read the diff.", '{"score": 4}')
    step = AgentStep(AgentStepConfig(agent="a", output="acme.v1.score"), agents={"a": agent})
    out = await step.run(Inputs({matrix.TASK: ("review",)}), RunContext("r", AGENT_STEP))
    assert out["acme.v1.score"] == ({"score": 4},)


def _usage(model: str, output: int) -> dict[str, Any]:
    return {
        "inputTokens": 1,
        "outputTokens": output,
        "cacheReadInputTokens": 0,
        "cacheCreationInputTokens": 0,
        "webSearchRequests": 0,
        "costUSD": 0.0,
        "contextWindow": 200000,
        "maxOutputTokens": 32000,
    }


async def test_the_model_recorded_is_the_one_that_answered_not_the_one_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (review area 3): ``model`` was the definition's, the model *asked for*,
    and None when the CLI's default answered. It is the model the SDK says answered
    (``model_usage``); when several took part, the one that wrote the final reply."""
    _install(
        monkeypatch,
        sdk.AssistantMessage(content=[sdk.TextBlock("done")], model="claude-sonnet-4-5-x"),
        _result(result="done", model_usage={"claude-sonnet-4-5-x": _usage("s", 9)}),
    )
    assert (await _runtime().run(_defn(model="sonnet"), "go")).model == "claude-sonnet-4-5-x"
    _install(
        monkeypatch,
        sdk.AssistantMessage(content=[sdk.TextBlock("done")], model="claude-opus-4-1-x"),
        _result(
            result="done",
            model_usage={
                "claude-haiku-4-5-x": _usage("h", 40),  # the CLI's own small calls
                "claude-opus-4-1-x": _usage("o", 9),
            },
        ),
    )
    assert (await _runtime().run(_defn(model="opus"), "go")).model == "claude-opus-4-1-x"
    _install(monkeypatch, _result(result="done"))
    assert (await _runtime().run(_defn(model="opus"), "go")).model is None  # nobody said


async def test_the_session_id_why_it_stopped_and_the_cache_tokens_are_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (mx-src F10): ``tokens_input`` was ``usage["input_tokens"]`` alone, which
    for Anthropic excludes the tokens read from and written to the prompt cache: 12 against
    90,000 in the probe. It counts every input token now, and the cache's are kept beside
    it. The session id and the last reply's stop reason were dropped."""
    _install(
        monkeypatch,
        _result(
            result="ok",
            session_id="sess-9",
            stop_reason="max_tokens",
            usage={
                "input_tokens": 12,
                "cache_read_input_tokens": 90000,
                "cache_creation_input_tokens": 300,
                "output_tokens": 5,
            },
        ),
    )
    response = await _runtime().run(_defn(), "go")
    assert response.session_id == "sess-9"
    assert (response.finish_reason, response.native_finish_reason) == ("length", "max_tokens")
    assert (response.tokens_input, response.tokens_output) == (90312, 5)
    assert (response.cache_read_tokens, response.cache_write_tokens) == (90000, 300)


@pytest.mark.parametrize(
    ("native", "finish"),
    [
        ("end_turn", "stop"),
        ("stop_sequence", "stop"),
        ("max_tokens", "length"),
        ("refusal", "refusal"),
        ("tool_use", "tool_call"),
        ("pause_turn", "other"),
        (None, None),
    ],
)
async def test_the_sdk_s_stop_reason_is_read_in_one_vocabulary(
    monkeypatch: pytest.MonkeyPatch, native: str | None, finish: str | None
) -> None:
    _install(monkeypatch, _result(result="ok", stop_reason=native))
    response = await _runtime().run(_defn(), "go")
    assert (response.finish_reason, response.native_finish_reason) == (finish, native)


async def test_the_stream_is_closed_when_reading_it_stops_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (mx-src F30): the SDK's ``query()`` generator was read without
    ``aclosing``, so when reading stopped before its end, closing it (and with it the CLI)
    was left to asyncio's garbage-collection hook, some time later."""
    closed: list[bool] = []

    class Unreadable(sdk.UserMessage):
        """A user message whose content raises when read."""

        @property
        def content(self) -> str | list[sdk.ContentBlock]:
            raise RuntimeError("a message the runtime cannot read")

        @content.setter
        def content(self, value: str | list[sdk.ContentBlock]) -> None:
            """The dataclass constructor assigns it; there is nothing to keep."""

    async def fake_query(
        *,
        prompt: str | AsyncIterable[dict[str, Any]],
        options: sdk.ClaudeAgentOptions | None = None,
        transport: object | None = None,
    ) -> AsyncIterator[sdk.Message]:
        try:
            yield Unreadable(content="")
            yield _result(result="never read")
        finally:
            closed.append(True)

    monkeypatch.setattr(sdk, "query", fake_query)
    with pytest.raises(matrix.AgentRuntimeError, match="a message the runtime cannot read"):
        await _runtime().run(_defn(), "go")
    assert closed == [True]


async def test_a_missing_cli_is_incapable_and_a_timeout_is_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The error mapping the runtime promises (mx-tests 11): no test held it."""

    def failing(error: Exception) -> Callable[..., AsyncIterator[sdk.Message]]:
        async def fake_query(
            *,
            prompt: str | AsyncIterable[dict[str, Any]],
            options: sdk.ClaudeAgentOptions | None = None,
            transport: object | None = None,
        ) -> AsyncIterator[sdk.Message]:
            raise error
            yield  # an async generator, as the SDK's query is

        return fake_query

    monkeypatch.setattr(sdk, "query", failing(sdk.CLINotFoundError("no claude")))
    with pytest.raises(matrix.AgentRuntimeError) as e:
        await _runtime().run(_defn(), "go")
    assert (e.value.reason, e.value.kind) == ("incapable", "config")
    assert "npm install" in (e.value.fix or "")
    monkeypatch.setattr(sdk, "query", failing(TimeoutError("slow")))
    with pytest.raises(matrix.AgentRuntimeError) as e:
        await _runtime().run(_defn(), "go")
    assert (e.value.reason, e.value.kind, e.value.retryable) == ("timeout", "transient", True)


async def test_an_sdk_error_after_the_result_is_logged_and_the_result_kept(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def fake_query(
        *,
        prompt: str | AsyncIterable[dict[str, Any]],
        options: sdk.ClaudeAgentOptions | None = None,
        transport: object | None = None,
    ) -> AsyncIterator[sdk.Message]:
        yield _result(result="the answer")
        raise sdk.CLIConnectionError("the CLI went away while closing")

    monkeypatch.setattr(sdk, "query", fake_query)
    with caplog.at_level("DEBUG", logger="matrix.adapters._out.runtime.claude_sdk"):
        response = await _runtime().run(_defn(), "go")
    assert (response.content, response.stop) == ("the answer", "completed")
    assert any("went away" in r.getMessage() for r in caplog.records)


async def test_the_cli_is_handed_the_trace_context_of_its_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With OpenTelemetry active, the ``claude`` CLI the session starts is given the context of
    the session's own span (``TRACEPARENT``), so the CLI's spans join the run's trace; with no
    span current it is given none (W4.3)."""
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from matrix.adapters._out.observers.otel import OtelObserver

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    captured = _install(monkeypatch, echo=True)
    runtime = matrix.default_registry(discover=False).create(
        "agent-runtime", "matrix.v1.agent-runtime.claude-sdk", needs={"cwd": None}
    )
    agent = matrix.BoundAgent(
        _defn(name="rev"), runtime, observers=[OtelObserver(tracer_provider=provider)]
    )
    await agent.run("go")
    (span,) = [s for s in exporter.get_finished_spans() if s.name == "invoke_agent rev"]
    context = span.get_span_context()
    assert context is not None
    ids = f"{trace.format_trace_id(context.trace_id)}-{trace.format_span_id(context.span_id)}"
    expected = f"00-{ids}-{context.trace_flags:02x}"
    assert _options(captured).env["TRACEPARENT"] == expected

    await runtime.run(_defn(), "no span is current here")
    assert "TRACEPARENT" not in _options(captured, 1).env


async def test_the_fallback_model_reaches_the_sdk(fake_sdk: _Captured) -> None:
    await _runtime(fallback_model="haiku").run(_defn(model="sonnet"), "go")
    options = _options(fake_sdk)
    assert (options.model, options.fallback_model) == ("sonnet", "haiku")


async def test_the_runtime_never_touches_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M-3 (fixed): the runtime used to pop ``CLAUDECODE`` from ``os.environ`` for a call,
    so concurrent sessions raced on it. The SDK strips it from the child's own environment;
    the process's is left as it was, during the session and after."""
    import os

    monkeypatch.setenv("CLAUDECODE", "1")
    before = dict(os.environ)
    seen: list[dict[str, str]] = []

    async def fake_query(
        *,
        prompt: str | AsyncIterable[dict[str, Any]],
        options: sdk.ClaudeAgentOptions | None = None,
        transport: object | None = None,
    ) -> AsyncIterator[sdk.Message]:
        seen.append(dict(os.environ))
        yield _result(result="ok")

    monkeypatch.setattr(sdk, "query", fake_query)
    await _runtime().run(_defn(), "go")
    assert seen == [before] and dict(os.environ) == before

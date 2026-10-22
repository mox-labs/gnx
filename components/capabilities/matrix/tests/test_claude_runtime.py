"""Tests for ClaudeSdkRuntime — the Claude Agent SDK as an AgentRuntime.

The messages a session streams are the SDK's own dataclasses and the options it is handed
are the SDK's own ``ClaudeAgentOptions``: only ``query`` is replaced, so it starts no CLI. A
fake narrower than the SDK could not tell the runtime it reads a field the SDK lacks, or
hands it an option the SDK refuses (mx-tests 5).
"""

import pytest

import matrix
from matrix import AgentDefinition
from matrix.domain.agent import AgentResponse

sdk = pytest.importorskip("claude_agent_sdk")


def _assistant(*blocks):
    return sdk.AssistantMessage(content=list(blocks), model="claude-x")


def _result(**fields):
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


def _install(monkeypatch, *messages, echo=False):
    """Replace the SDK's ``query`` with one that streams ``messages`` (with ``echo``, the
    prompt back and a success); returns what it was called with, each ``{prompt, options}``."""
    captured: dict = {"calls": []}

    async def fake_query(*, prompt, options=None, transport=None):
        captured["calls"].append({"prompt": prompt, "options": options})
        echoed = [_assistant(sdk.TextBlock(f"echo: {prompt}")), _result()] if echo else []
        for message in echoed or messages:
            yield message

    monkeypatch.setattr(sdk, "query", fake_query)
    return captured


@pytest.fixture
def fake_sdk(monkeypatch):
    """A session that echoes its prompt and succeeds."""
    return _install(monkeypatch, echo=True)


@pytest.fixture
def fake_sdk_with_tools(monkeypatch):
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
        ),
    )


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
        (call,) = response.tool_calls
        assert (call["name"], call["input"], call["id"]) == (
            "Skill",
            {"skill": "build-eval"},
            "toolu_0",
        )
        assert (response.duration_ms, response.cost_usd) == (250, 0.05)
        assert (response.tokens_input, response.tokens_output, response.num_turns) == (100, 80, 2)

    @pytest.mark.anyio
    async def test_definition_and_deployment_reach_the_sdk(self, fake_sdk):
        runtime = _runtime(plugins=[{"type": "local", "path": "/p"}], setting_sources=[])
        await runtime.run(
            _defn(system_prompt="Be concise.", max_turns=3, tools=("Read",), model="sonnet"), "hi"
        )
        opts = fake_sdk["calls"][0]["options"]
        assert isinstance(opts, sdk.ClaudeAgentOptions)
        assert opts.system_prompt == "Be concise."
        assert opts.max_turns == 3
        assert opts.tools == ["Read"]
        assert opts.model == "sonnet"
        assert opts.plugins == [{"type": "local", "path": "/p"}]
        assert opts.setting_sources == []

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

    def test_a_permissive_mode_is_logged_so_the_choice_is_visible(
        self, fake_sdk, caplog, monkeypatch
    ):
        from matrix.adapters._out.runtime import claude_sdk

        monkeypatch.setattr(claude_sdk, "_LOGGED", set())
        with caplog.at_level("WARNING"):
            _runtime(permission_mode="bypassPermissions")
        assert any("bypassPermissions" in r.getMessage() for r in caplog.records), (
            "selecting a permissive mode must appear in the run record, not only the config"
        )
        assert {r.name for r in caplog.records} == {matrix.RUNTIME_LOGGER}

    def test_a_permissive_mode_is_logged_once_per_process_not_once_per_runtime(
        self, fake_sdk, caplog, monkeypatch
    ):
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

    def test_safe_mode_is_not_logged(self, fake_sdk, caplog):
        with caplog.at_level("WARNING"):
            _runtime()
        assert not caplog.records, "the safe default should be quiet"


# --- how a session ended ------------------------------------------------------------------


class TestSessionEnding:
    async def test_a_completed_session_says_so(self, fake_sdk):
        assert (await _runtime().run(_defn(), "hi")).stop == "completed"

    async def test_a_turn_limit_is_reported_not_raised(self, monkeypatch):
        """A one-turn routing eval ends at max_turns by design; the Skill call is the result."""
        _install(
            monkeypatch,
            _assistant(sdk.ToolUseBlock(id="toolu_1", name="Skill", input={"skill": "aces"})),
            _result(subtype="error_max_turns", is_error=True),
        )
        response = await _runtime().run(_defn(), "hi")
        assert response.stop == "max_turns"
        assert response.tool_calls[0]["name"] == "Skill"

    async def test_a_failed_session_raises_instead_of_answering(self, monkeypatch):
        from matrix import AgentRuntimeError

        _install(
            monkeypatch,
            _assistant(sdk.TextBlock("partial")),
            _result(subtype="error_during_execution", is_error=True, errors=["tool crashed"]),
        )
        with pytest.raises(AgentRuntimeError, match="error_during_execution.*tool crashed"):
            await _runtime().run(_defn(), "hi")

    async def test_a_budget_limit_is_reported_not_raised(self, monkeypatch):
        """``max_budget_usd`` is the SDK's own stop; matrix records it as the reason the
        session ended and counts nothing against it."""
        _install(
            monkeypatch,
            _assistant(sdk.TextBlock("partial")),
            _result(subtype="error_max_budget_usd", is_error=True, total_cost_usd=0.5),
        )
        response = await _runtime(max_budget_usd=0.5).run(_defn(), "hi")
        assert (response.stop, response.cost_usd, response.content) == (
            "max_budget_usd",
            0.5,
            "partial",
        )

    async def test_an_api_error_names_the_status(self, monkeypatch):
        from matrix import AgentRuntimeError

        _install(monkeypatch, _result(subtype="success", is_error=True, api_error_status=529))
        with pytest.raises(AgentRuntimeError, match="HTTP 529"):
            await _runtime().run(_defn(), "hi")

    async def test_a_stream_with_no_result_raises(self, monkeypatch):
        from matrix import AgentRuntimeError

        _install(monkeypatch, _assistant(sdk.TextBlock("cut off")))
        with pytest.raises(AgentRuntimeError, match="without a result"):
            await _runtime().run(_defn(), "hi")


@pytest.mark.anyio
async def test_a_tool_call_is_timed_from_its_request_to_its_result(monkeypatch) -> None:
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


def test_a_permissive_mode_is_a_warning_composition_carries(fake_sdk) -> None:
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


async def test_inline_subagents_reach_the_sdk_as_its_agent_definitions(monkeypatch) -> None:
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
    (critic,) = seen["calls"][0]["options"].agents.values()
    sent = {k: v for k, v in dataclasses.asdict(critic).items() if v is not None}
    assert sent == {
        "description": "Finds faults.",
        "prompt": "Be harsh.",
        "tools": ["Read"],
        "model": "haiku",
        "maxTurns": 2,
    }


def _create(options: dict):
    from matrix import default_registry, runtime_type_url

    return default_registry(discover=False).create(
        "runtime", runtime_type_url("claude-sdk"), options, needs={"cwd": None}, where="r"
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
    options: dict, path: str
) -> None:
    """The config schema is the declaration of what a session can be given: an option
    outside it fails at composition, at its key, never inside the SDK after the CLI started."""
    with pytest.raises(matrix.ConfigError) as e:
        _create(options)
    assert any(p.path.startswith(f"r.{path}") for p in e.value.problems), e.value.problems
    assert "matrix describe" in (e.value.fix or "")


def test_skills_reach_the_sdk_options(fake_sdk) -> None:
    assert _runtime(skills=["review"]).sdk_options(_defn()).skills == ["review"]
    assert _runtime(skills="all").sdk_options(_defn()).skills == "all"
    assert _runtime().sdk_options(_defn()).skills is None


def test_skills_an_agent_s_tools_leave_no_way_to_use_are_refused_at_binding(fake_sdk) -> None:
    runtime = _runtime(skills=["review"])
    with pytest.raises(matrix.ConfigError, match="only through the Skill tool") as e:
        matrix.BoundAgent(_defn(tools=("Read",)), runtime)
    assert "add Skill" in (e.value.fix or "")
    matrix.BoundAgent(_defn(tools=("Read", "Skill")), runtime)
    matrix.BoundAgent(_defn(tools=None), runtime)  # the SDK's default toolset has it
    matrix.BoundAgent(_defn(tools=("Read",)), _runtime(skills=[]))  # no skills to use


def test_an_option_the_installed_sdk_lacks_is_refused_at_binding(monkeypatch) -> None:
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

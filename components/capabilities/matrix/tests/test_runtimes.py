"""The hardline and mock runtimes: what each port's runtimes honour, refuse and record."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from matrix import (
    MODEL_STEP,
    PROMPT,
    SESSION,
    AgentDefinition,
    AgentRuntime,
    ConfigError,
    MatrixConfig,
    ModelCall,
    ModelRequest,
    ModelRuntime,
    ModelRuntimeError,
    RequestParams,
    TokenUsage,
    compose,
    default_registry,
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
        "served": {"backend": "mock", "model": "llama-3", "family": "llama", "provider": "ollama"},
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
        "cut": {
            "backend": "mock",
            "model": "cut-1",
            "family": "qwen",
            "options": {"default": "The answer is", "finish_reason": "length"},
        },
        "no": {
            "backend": "mock",
            "model": "no-1",
            "family": "qwen",
            "options": {"default": "I can't help.", "refusal": "I can't help."},
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


async def test_a_call_carries_hardline_s_whole_record() -> None:
    """Regression (mx-tests 4, W3.3): the model runtime stamped ``num_turns=1`` and
    ``stop="completed"`` as constants, then kept only part of what hardline reported, so a
    truncation, a refusal or the provider's id could never show. A call now carries
    hardline's whole record: why the reply ended, what was sent and to which registry row,
    every attempt, and the usage summed over them."""
    runtime = _hardline()
    assert isinstance(runtime, ModelRuntime)
    call = await runtime.call(ModelRequest(prompt="hello", system="be brief"))
    assert call.text == "mock:qwen-8b:hello"
    assert (call.name, call.model, call.family, call.backend) == ("q", "qwen-8b", "qwen", "mock")
    assert call.usage == TokenUsage(input_tokens=3, output_tokens=1)
    assert (call.request_id, call.attempts, call.retries) == ("mock-q-1", 1, 0)
    assert (call.finish_reason, call.native_finish_reason, call.refusal) == ("stop", "stop", None)
    assert call.fallback_from is None
    assert call.params == RequestParams(max_tokens=1024, temperature=None, structured=None)
    (attempt,) = call.attempt_log or ()
    assert (attempt.name, attempt.reason, attempt.usage) == ("q", None, call.usage)
    assert call.turns_digest is not None and call.turns_digest.startswith("sha256:")
    assert call.row is not None and call.row.digest.startswith("sha256:")
    assert call.row.rungs["model"] == "matrix.models"
    assert not hasattr(call, "num_turns") and not hasattr(call, "stop")


async def test_the_provider_is_the_row_s_or_else_its_backend_s_on_the_call_and_its_row() -> None:
    """Regression (W7.11): ``provider`` was set for the ``anthropic`` backend alone, so a call
    served by ollama, or any row of an OpenAI-compatible backend, said nobody served it. It is
    now the registry row's ``provider:``, and the backend's name for a row that names none, as
    hardline's own ``gen_ai.provider.name`` is; the session row a model step records says the
    same."""
    assert (await _hardline().call(ModelRequest(prompt="x", model="served"))).provider == "ollama"
    assert (await _hardline().call(ModelRequest(prompt="x"))).provider == "mock"
    box = compose(
        MatrixConfig.model_validate(
            {"models": MODELS, "model_runtimes": {"m": {"type": "hardline"}}}
        ),
        registry=default_registry(discover=False),
    )
    for model, provider in [("served", "ollama"), ("q", "mock")]:
        step = {"runtime": "m", "model": model}
        wiring = box.wire([{"name": "ask", "type": MODEL_STEP, "config": step}], inputs=[PROMPT])
        run = await box.executor().run(wiring, {PROMPT: "hello"})
        (session,) = run.rows(SESSION)
        assert (session.name, session.provider) == (model, provider)


async def test_a_reply_that_is_not_a_whole_answer_says_why() -> None:
    """A reply cut at max_tokens, or a refusal, is reported for what it is (hardline's
    promise), never as a finished answer."""
    cut = await _hardline().call(ModelRequest(prompt="x", model="cut"))
    assert (cut.text, cut.finish_reason, cut.native_finish_reason) == (
        "The answer is",
        "length",
        "length",
    )
    refused = await _hardline().call(ModelRequest(prompt="x", model="no"))
    assert (refused.finish_reason, refused.refusal) == ("refusal", "I can't help.")


async def test_a_fallback_is_recorded_as_the_model_that_answered() -> None:
    call = await _hardline().call(ModelRequest(prompt="hello", model="down"))
    assert (call.name, call.model, call.family, call.fallback_from) == (
        "h",
        "haiku",
        "claude",
        "down",
    )
    assert [(a.name, a.reason) for a in call.attempt_log or ()] == [
        ("down", "unavailable"),
        ("h", None),
    ]


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


class _Raises:
    """hardline's runtime, as the model runtime sees it, raising what it is given."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def complete(self, *args: object, **kwargs: object) -> object:
        raise self._error


async def test_on_the_run_path_a_rate_limit_keeps_its_hint_and_anything_else_fails() -> None:
    """What ``call`` raises, not a private helper: hardline's ``retry_after`` reaches the
    retry policy as ``retry_after_s``, and an exception hardline did not classify is the
    port's ``failed``, never an unclassified one (mx-tests 11)."""
    pytest.importorskip("hardline")
    from hardline import BackendError

    from matrix.adapters._out.runtime.model import HardlineModelRuntime

    limited = BackendError("slow down", reason="rate_limit", retry_after=3.0)
    with pytest.raises(ModelRuntimeError) as e:
        await HardlineModelRuntime(_Raises(limited)).call(ModelRequest(prompt="x"))  # type: ignore[arg-type]
    assert (e.value.reason, e.value.kind, e.value.retry_after_s) == (
        "rate_limited",
        "transient",
        3.0,
    )
    with pytest.raises(ModelRuntimeError) as e:
        await HardlineModelRuntime(_Raises(ValueError("odd"))).call(ModelRequest(prompt="x"))  # type: ignore[arg-type]
    assert (e.value.reason, e.value.kind) == ("failed", "component")
    assert isinstance(e.value.__cause__, ValueError)


def test_an_agent_runtime_declares_what_its_sessions_do_as_data() -> None:
    """W2.9: whether a session loads skills from settings or from its working directory,
    which tool fires a skill and where in its working directory its skills must be placed,
    whether its tools can write files, and the config that keeps it from reading the machine
    it runs on, are the runtime's to declare (its config's ``capabilities()``, asked through
    ``Entry.capabilities``), so a caller asks the runtime instead of reading option names it
    would have to know."""
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
    assert pydantic_ai.capabilities({}) == AgentCapabilities(
        settings_skills=False, project_skills=False, skill_tool=None, skills_dir=None
    )
    # The stand-in declares nothing: a dry run is reviewed as the session it stands in for.
    assert registry.entry("agent-runtime", "matrix.v1.agent-runtime.mock").capabilities({}) is None

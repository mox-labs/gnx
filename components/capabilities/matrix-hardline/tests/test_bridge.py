"""The bridge: hardline as a matrix model runtime, through matrix's port and its registry.

Every model is a hardline row on its mock backend, or on its OpenAI-compatible backend handed
an httpx2 ``MockTransport`` that answers as a server would. Nothing leaves the process. These
are the tests matrix's own suite held of its hardline adapter before matrix 0.10 (its design
record's "rearrangements a caller must not notice"), now read through the neutral reply and
hardline's record in ``provenance``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

import hardline
import httpx2
import pytest
from hardline import CALL_TYPE_URL, BackendError, CallRecord
from hardline.adapters._out.mock import MockBackend
from hardline.adapters._out.openai_compat import OpenAICompatBackend
from matrix import (
    MODEL_CALL,
    MODEL_STEP,
    PROMPT,
    SESSION,
    AgentDefinition,
    ConfigError,
    Executor,
    MatrixConfig,
    ModelRequest,
    ModelRuntime,
    ModelRuntimeError,
    ModelStep,
    ModelStepConfig,
    Registry,
    RunError,
    Session,
    TokenUsage,
    compose,
    default_registry,
    plan_config,
    pydantic_ai_model,
    wire,
)
from matrix.adapters._out.observers.otel import OtelObserver
from matrix.adapters._out.runtime.pydantic_ai import PydanticAiRuntime, PydanticAiRuntimeConfig
from matrix.domain.agent import BoundAgent
from matrix.domain.session import CURRENT_SESSIONS
from matrix.testing import ModelRuntimeConformance
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from pydantic import BaseModel

from matrix_hardline import (
    FAILED_CALL_TYPE_URL,
    TYPE_URL,
    FailedCall,
    HardlineModelRuntime,
    HardlineModelRuntimeConfig,
    register,
)
from matrix_hardline.runtime import REGISTRY_LABEL

if TYPE_CHECKING:
    from pathlib import Path

MODELS: dict[str, Any] = {
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


def _bridge(**config: Any) -> HardlineModelRuntime:
    return HardlineModelRuntime.from_config(HardlineModelRuntimeConfig(models=MODELS, **config))


def _record(call: Any) -> CallRecord:
    """hardline's record of ``call``, read back from its provenance with hardline's own schema."""
    return CallRecord.model_validate(call.provenance[CALL_TYPE_URL])


def _registry() -> Registry:
    """matrix's built-ins and this bridge, registered as its entry point registers it."""
    registry = default_registry(discover=False)
    register(registry)
    return registry


class TestTheBridgeKeepsThePort(ModelRuntimeConformance):
    def runtime(self) -> ModelRuntime:
        return _bridge()


async def test_a_reply_is_the_neutral_record_and_hardline_s_record_travels_whole() -> None:
    """Regression (matrix 0.7): the model runtime stamped constants, then kept part of what
    hardline reported. The reply is matrix's neutral record, and everything only hardline
    knows (its backend, attempts, retries, what it sent, the row and where each of its values
    came from) is hardline's own record, whole, under ``hardline.v1.call``."""
    call = await _bridge().call(ModelRequest.from_prompt("hello", system="be brief"))
    assert call.text == "mock:qwen-8b:hello"
    assert (call.name, call.model, call.family, call.provider) == ("q", "qwen-8b", "qwen", "mock")
    assert call.usage == TokenUsage(input_tokens=3, output_tokens=1)
    assert call.request_id == "mock-q-1"
    assert (call.finish_reason, call.native_finish_reason, call.refusal) == ("stop", "stop", None)
    record = _record(call)
    assert (record.backend, record.attempts, record.retries, record.fallback_from) == (
        "mock",
        1,
        0,
        None,
    )
    assert (record.params.max_tokens, record.params.temperature) == (1024, "provider_default")
    (attempt,) = record.attempt_log
    assert (attempt.name, attempt.reason) == ("q", None)
    assert record.turns_digest.startswith("sha256:") and record.row.digest.startswith("sha256:")
    assert record.row.rungs["model"] == REGISTRY_LABEL


async def test_a_fallback_is_recorded_as_the_model_that_answered() -> None:
    call = await _bridge().call(ModelRequest.from_prompt("hello", model="down"))
    assert (call.name, call.model, call.family) == ("h", "haiku", "claude")
    record = _record(call)
    assert record.fallback_from == "down"
    assert [(a.name, a.reason) for a in record.attempt_log] == [
        ("down", "unavailable"),
        ("h", None),
    ]


async def test_the_provider_is_the_row_s_or_else_its_backend_s_on_the_call_and_its_row() -> None:
    """Regression: ``provider`` was set for the ``anthropic`` backend alone. It is the row's
    ``provider:``, else the backend's name, as hardline names it; the session row a model step
    records says the same, under this runtime's type URL and what it drives."""
    box = compose(
        MatrixConfig.model_validate(
            {"model_runtimes": {"m": {"type": TYPE_URL, "models": MODELS}}}
        ),
        registry=_registry(),
    )
    for model, provider in [("served", "ollama"), ("q", "mock")]:
        step = {"runtime": "m", "model": model}
        wiring = box.wire([{"name": "ask", "type": MODEL_STEP, "config": step}], inputs=[PROMPT])
        run = await box.executor().run(wiring, {PROMPT: "hello"})
        (session,) = run.rows(SESSION)
        assert (session.name, session.provider) == (model, provider)
        assert (session.runtime, session.drives) == (TYPE_URL, ("hardline",))
        assert _record(session).backend == "mock"


async def test_a_reply_that_is_not_a_whole_answer_says_why() -> None:
    """A reply cut at max_tokens, or a refusal, is reported for what it is (hardline's
    promise), never as a finished answer."""
    cut = await _bridge().call(ModelRequest.from_prompt("x", model="cut"))
    assert (cut.text, cut.finish_reason, cut.native_finish_reason) == (
        "The answer is",
        "length",
        "length",
    )
    refused = await _bridge().call(ModelRequest.from_prompt("x", model="no"))
    assert (refused.finish_reason, refused.refusal) == ("refusal", "I can't help.")


async def test_the_default_model_is_the_runtime_s_then_the_registry_s() -> None:
    assert (await _bridge(default_model="h").call(ModelRequest.from_prompt("x"))).name == "h"
    assert (await _bridge().call(ModelRequest.from_prompt("x"))).name == "q"


async def test_the_overrides_are_the_request_s_then_the_runtime_s_then_the_row_s() -> None:
    plain = ModelRequest.from_prompt("x")
    assert _record(await _bridge().call(plain)).params.max_tokens == 1024
    assert _record(await _bridge(max_tokens=64).call(plain)).params.max_tokens == 64
    given = ModelRequest.from_prompt("x", max_tokens=8, temperature=0.5)
    params = _record(await _bridge(max_tokens=64, temperature=0.0).call(given)).params
    assert (params.max_tokens, params.temperature) == (8, 0.5)


async def test_a_structured_reply_is_validated_and_repaired_by_hardline() -> None:
    call = await _bridge().call(ModelRequest.from_prompt("review", model="fixer", output=Review))
    assert call.value == {"verdict": "ship"}
    assert _record(call).attempts == 2  # the first reply was not JSON, and hardline asked again


def test_check_refuses_what_a_call_could_not_carry() -> None:
    """M-6: an unknown model (naming the known ones), tools on a row whose backend cannot call
    them, and tools with an output structure, which hardline's ``extract`` cannot offer, are
    each a ``config`` problem at binding, not a failure mid-run."""
    runtime = _bridge()
    with pytest.raises(ConfigError, match="no-such-model") as unknown:
        runtime.check("no-such-model")
    assert unknown.value.exit_code == 3 and "q" in (unknown.value.fix or "")
    runtime.check(None)  # the registry has a default
    runtime.check("h", uses=frozenset({"tools"}))
    runtime.check("h", uses=frozenset({"output"}))
    with pytest.raises(ConfigError, match="tools and ask for an output structure"):
        runtime.check("h", uses=frozenset({"tools", "output"}))

    class Textual:
        structured_modes = frozenset({"prompt"})

        async def complete(self, spec: Any, request: Any, api_key: str | None) -> Any:
            raise AssertionError("never sent")

    textual = HardlineModelRuntime(
        hardline.build_runtime(
            {"models": {"t": {"backend": "textual", "model": "t", "family": "x"}}},
            backends={"textual": Textual()},
        )
    )
    with pytest.raises(ConfigError, match="cannot call tools"):
        textual.check("t", uses=frozenset({"tools"}))
    textual.check("t")  # with no tools offered, any row serves


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ({"options": {"script": [{"error": "rate_limit"}]}}, "rate_limited"),
        ({"options": {"script": [{"error": "auth"}]}}, "auth"),
        ({"options": {"script": [{"error": "bad_request"}]}}, "incapable"),
        ({"api_key": "env:MATRIX_HARDLINE_TEST_UNSET_KEY"}, "auth"),
        ({"backend": "broken"}, "failed"),
    ],
)
async def test_one_mapping_says_whose_problem_each_hardline_failure_is(
    row: dict[str, object], reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-2: each failure hardline reports reaches the caller with the reason the port names,
    from one mapping, where matrix 0.9 had two that disagreed."""

    class Broken(MockBackend):
        async def complete(self, spec: Any, request: Any, api_key: str | None) -> Any:
            raise RuntimeError("not a failure hardline knows")

    monkeypatch.delenv("MATRIX_HARDLINE_TEST_UNSET_KEY", raising=False)
    spec = {"backend": "mock", "model": "x-1", "family": "x", "retries": 0, **row}
    models = hardline.build_runtime(
        {"models": {"x": spec}}, backends={"mock": MockBackend(), "broken": Broken()}
    )
    with pytest.raises(ModelRuntimeError) as e:
        await HardlineModelRuntime(models).call(ModelRequest.from_prompt("hi", model="x"))
    assert e.value.reason == reason


class _Raises:
    """hardline's runtime, as the bridge sees it, raising what it is given."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def complete(self, *args: object, **kwargs: object) -> object:
        raise self._error


async def test_a_rate_limit_keeps_its_hint_and_anything_unclassified_fails() -> None:
    """hardline's ``retry_after`` reaches the retry policy as ``retry_after_s``; an unknown
    name is ``incapable``; an exception hardline did not classify is the port's ``failed``."""
    limited = BackendError("slow down", reason="rate_limit", retry_after=3.0)
    with pytest.raises(ModelRuntimeError) as e:
        await HardlineModelRuntime(_Raises(limited)).call(ModelRequest.from_prompt("x"))  # type: ignore[arg-type]
    assert (e.value.reason, e.value.kind, e.value.retry_after_s) == (
        "rate_limited",
        "transient",
        3.0,
    )
    with pytest.raises(ModelRuntimeError) as e:
        await _bridge().call(ModelRequest.from_prompt("x", model="ghost"))
    assert (e.value.reason, e.value.kind) == ("incapable", "config")
    with pytest.raises(ModelRuntimeError) as e:
        await HardlineModelRuntime(_Raises(ValueError("odd"))).call(ModelRequest.from_prompt("x"))  # type: ignore[arg-type]
    assert (e.value.reason, e.value.kind) == ("failed", "component")
    assert isinstance(e.value.__cause__, ValueError)


async def test_a_failed_call_is_a_session_row_with_what_each_attempt_cost() -> None:
    """A call that failed (its repair rate limited, no retries left) spent tokens and made
    attempts: its row says it failed and why, its usage is the call's, and what hardline's
    error said of the attempts is its provenance, valid against the schema registered."""
    row = {
        "backend": "mock",
        "model": "q-1",
        "family": "q",
        "retries": 0,
        "options": {"script": ["not a rating", {"error": "rate_limit"}]},
    }
    runtime = HardlineModelRuntime(
        hardline.build_runtime(
            config={"default": "q", "models": {"q": row}}, backends={"mock": MockBackend()}
        )
    )
    rating = "acme.v1.review"
    step = ModelStep(
        ModelStepConfig(runtime="m", output=rating),
        model_runtimes={"m": runtime},
        schemas={rating: Review},
    )
    schemas: dict[str, type[BaseModel]] = {**_registry().payload_schemas(), rating: Review}
    with pytest.raises(RunError) as e:
        await Executor().run(wire({"rate": step}, inputs=[PROMPT], schemas=schemas), {PROMPT: "x"})
    (session,) = e.value.run.rows(SESSION)
    assert (session.status, session.reason) == ("failed", "rate_limited")
    failed = FailedCall.model_validate(session.provenance[FAILED_CALL_TYPE_URL])
    assert [(a.name, a.reason) for a in failed.attempt_log] == [("q", None), ("q", "rate_limit")]
    assert session.usage is not None and session.usage.input_tokens > 0


def test_it_registers_its_runtime_and_the_schemas_of_what_it_records() -> None:
    """The bridge registers as a third party does: its runtime under its own namespace, no
    stand-in, driving hardline, and the schemas of the documents it puts in ``provenance``, so
    matrix validates each when a row is recorded and ``matrix describe`` prints it."""
    registry = _registry()
    entry = registry.entry("model-runtime", TYPE_URL)
    assert (entry.drives, entry.stand_in, entry.config) == (
        ("hardline",),
        False,
        HardlineModelRuntimeConfig,
    )
    schemas = registry.payload_schemas()
    assert (schemas[CALL_TYPE_URL], schemas[FAILED_CALL_TYPE_URL]) == (CallRecord, FailedCall)
    assert registry.failures == []


def test_hardline_s_warnings_are_in_the_plan(tmp_path: Path) -> None:
    """M-4: a fallback on the same backend and location as its row fails with it. hardline
    warns of it; matrix 0.9 only logged it at build. The runtime's ``warnings`` are now the
    plan's, at the runtime's key."""
    shared = {
        "default": "a",
        "models": {
            "a": {
                "backend": "openai-compat",
                "base_url": "http://127.0.0.1:9/v1",
                "model": "a",
                "family": "x",
                "fallbacks": ["b"],
            },
            "b": {
                "backend": "openai-compat",
                "base_url": "http://127.0.0.1:9/v1",
                "model": "b",
                "family": "x",
            },
        },
    }
    config = tmp_path / "matrix.yaml"
    config.write_text(
        json.dumps({"matrix": {"model_runtimes": {"m": {"type": TYPE_URL, "models": shared}}}})
    )
    plan = plan_config("matrix", str(config), only=True)  # the bridge as installed: discovered
    (warning,) = [w for w in plan.warnings if w.kind == "shared_fate"]
    assert warning.path == "matrix.model_runtimes.m.models.models.a.fallbacks"


def test_a_registry_in_the_runtime_s_config_that_hardline_refuses_is_a_problem_at_its_key() -> None:
    """Regression (matrix 0.7): hardline's ConfigError escaped as kind ``unknown``. A registry
    the runtime's own config gives, which hardline refuses, is a ``config`` problem at the key
    that was written."""
    config = MatrixConfig.model_validate(
        {
            "model_runtimes": {
                "h": {"type": TYPE_URL, "models": {"models": {"m": {"backend": "mock"}}}}
            }
        }
    )
    with pytest.raises(ConfigError) as raised:
        compose(config, registry=_registry())
    paths = {p.path for p in raised.value.problems}
    assert "matrix.model_runtimes.h.models.models.m.family" in paths
    assert raised.value.exit_code == 3


def _reply(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "id": "r-1",
            "model": "q-1",
            "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        },
    )


def test_a_model_step_closes_hardline_s_connections_in_the_loop_that_made_them(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regression (matrix 0.7): hardline keeps a client per event loop and closes it only in
    that loop. Nothing closed it, so the next loop to use the runtime logged that the last one
    had ended with it open. The bridge closes hardline's clients (``SupportsAclose``), and a
    model step defers that to the end of the run that made the calls."""
    row = {"backend": "openai-compat", "model": "q-1", "family": "q", "base_url": "http://q/v1"}
    runtime = HardlineModelRuntime(
        hardline.build_runtime(
            config={"default": "q", "models": {"q": row}},
            backends={"openai-compat": OpenAICompatBackend(transport=httpx2.MockTransport(_reply))},
        )
    )
    step = ModelStep(ModelStepConfig(runtime="m"), model_runtimes={"m": runtime})
    wiring = wire({"rate": step}, inputs=[PROMPT])
    caplog.set_level(logging.WARNING, logger="hardline")
    for _ in range(2):  # two event loops, as two `asyncio.run`s of a host make
        run = asyncio.run(Executor().run(wiring, {PROMPT: "rate it"}))
        assert run.rows(MODEL_CALL)[0].text == "ok"
    assert not [r for r in caplog.records if "left open" in r.getMessage()]


# --- pydantic-ai on hardline, through the port ------------------------------------------------

LOOKED_UP: list[str] = []


def lookup(word: str) -> str:
    """Find a word in the dictionary."""
    LOOKED_UP.append(word)
    if not word:
        from pydantic_ai import ModelRetry

        raise ModelRetry("give a word")
    return {"ossify": "to turn into bone"}.get(word, "not found")


def _agent(**fields: Any) -> AgentDefinition:
    return AgentDefinition.model_validate(
        {"name": "lexicographer", "system_prompt": "Define words.", **fields}
    )


class Server:
    """An OpenAI-compatible server on a MockTransport: records each body, answers in order."""

    def __init__(self, *replies: dict[str, Any]) -> None:
        self.bodies: list[dict[str, Any]] = []
        self._replies = list(replies)
        self.transport = httpx2.MockTransport(self._handle)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        return httpx2.Response(200, json=self._replies.pop(0))


def _chat(rid: str, prompt: int, completion: int, **message: Any) -> dict[str, Any]:
    finish = "tool_calls" if message.get("tool_calls") else "stop"
    return {
        "id": rid,
        "model": "qwen-served",
        "choices": [{"index": 0, "finish_reason": finish,
                     "message": {"role": "assistant", "content": None, **message}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }  # fmt: skip


def _asks(rid: str, *words: str) -> dict[str, Any]:
    calls = [
        {"id": f"{rid}-{i}", "type": "function",
         "function": {"name": "lookup", "arguments": json.dumps({"word": word})}}
        for i, word in enumerate(words)
    ]  # fmt: skip
    return _chat(rid, 40, 8, tool_calls=calls)


async def test_an_agent_calls_its_tool_through_hardline_end_to_end_on_the_wire() -> None:
    """A pydantic-ai session on hardline: every model call is one call on the port, sent by
    hardline's OpenAI-compatible backend, tools offered and asked for, results sent back; a
    tool that failed is sent as a failed tool's result, marked as hardline 0.3.1 marks it."""
    LOOKED_UP.clear()
    server = Server(
        _asks("c", "ossify", ""), _chat("r2", 60, 9, content="Ossify means to turn into bone.")
    )
    models = hardline.build_runtime(
        {"models": {"q": {"backend": "openai-compat", "base_url": "http://127.0.0.1:9/v1",
                          "model": "qwen", "family": "qwen"}}},
        backends={"openai-compat": OpenAICompatBackend(server.transport)},
    )  # fmt: skip
    runtime = PydanticAiRuntime(
        HardlineModelRuntime(models),
        PydanticAiRuntimeConfig(model_runtime="m", default_model="q"),
        tools={"lookup": lookup},
    )
    definition = _agent(max_turns=4)
    runtime.check(definition)

    response = await runtime.run(definition, "What does ossify mean?")

    assert LOOKED_UP == ["ossify", ""]
    assert response.content == "Ossify means to turn into bone."
    assert (response.model, response.family, response.provider) == (
        "qwen-served", "qwen", "openai-compat",
    )  # fmt: skip
    assert (response.tokens_input, response.tokens_output, response.num_turns) == (100, 17, 2)
    first, second = server.bodies
    assert first["messages"] == [
        {"role": "system", "content": "Define words."},
        {"role": "user", "content": "What does ossify mean?"},
    ]
    assert first["tools"][0]["function"]["name"] == "lookup"
    answered = [m for m in second["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in answered] == ["c-0", "c-1"]
    assert answered[0]["content"] == "to turn into bone"
    assert answered[1]["content"].startswith("[tool error] ")


async def test_a_row_that_fails_over_is_answered_by_its_fallback_and_says_so() -> None:
    """A fallback is the registry row's, not matrix's: a session on a row whose model is down
    is answered by the fallback and names the model that answered, and each call the
    pydantic-ai model makes is a row naming the fallback and the row it stood in for."""
    runtime = HardlineModelRuntime.from_config(
        HardlineModelRuntimeConfig(models=MODELS, default_model="down")
    )
    session = PydanticAiRuntime(runtime, PydanticAiRuntimeConfig(model_runtime="m"))
    response = await session.run(_agent(), "Define ossify.")
    assert (response.model, response.family) == ("haiku", "claude")
    rows: list[Session] = []

    def record(row: Session) -> int:
        rows.append(row)
        return len(rows) - 1

    from pydantic_ai import Agent

    agent: Agent[None, str] = Agent(pydantic_ai_model(runtime, runtime_url=TYPE_URL))
    agent.instrument = False
    recording = CURRENT_SESSIONS.set(record)
    try:
        assert (await agent.run("Define ossify.")).output.startswith("mock:haiku:")
    finally:
        CURRENT_SESSIONS.reset(recording)
    (row,) = rows
    assert (row.name, row.model, row.runtime) == ("h", "haiku", TYPE_URL)
    assert _record(row).fallback_from == "down"


def _counted(reader: InMemoryMetricReader, name: str) -> int:
    data = reader.get_metrics_data()
    points: list[Any] = [
        point
        for resource in (data.resource_metrics if data else ())
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]
    return sum(point.value for point in points)


async def test_a_session_s_tokens_on_hardline_are_counted_once() -> None:
    """hardline counts ``gen_ai.client.inference.usage.*`` for every call it makes; a session
    whose calls went through a model runtime is counted by none of matrix's counters, so each
    token is counted once."""
    reader = InMemoryMetricReader()
    meters = MeterProvider(metric_readers=[reader])
    asks = {"tool_calls": [{"name": "lookup", "arguments": '{"word": "ossify"}'}]}
    models = hardline.build_runtime(
        {"models": {"q": {"backend": "mock", "model": "m", "family": "x",
                          "options": {"script": [asks, "bone"]}}}},
        backends={"mock": MockBackend()},
        meter_provider=meters,
    )  # fmt: skip
    runtime = PydanticAiRuntime(
        HardlineModelRuntime(models),
        PydanticAiRuntimeConfig(model_runtime="m", default_model="q"),
        tools={"lookup": lookup},
    )
    observer = OtelObserver(tracer_provider=TracerProvider(), meter_provider=meters)
    agent = BoundAgent(_agent(), runtime, observers=[observer], through_model_runtime=True)
    response = await agent.run("Define ossify.")
    assert response.tokens_input and response.tokens_output
    assert _counted(reader, "gen_ai.client.inference.usage.input_tokens") == response.tokens_input
    assert _counted(reader, "gen_ai.client.inference.usage.output_tokens") == response.tokens_output

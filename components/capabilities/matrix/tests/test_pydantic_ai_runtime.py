"""The ``pydantic-ai`` agent runtime: a pydantic-ai ``Agent`` whose model calls go through hardline.

Every model here is a hardline row: the mock backend, or the OpenAI-compatible backend handed
an httpx2 ``MockTransport`` that answers as a server would. Nothing leaves the process.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from typing import Any

import httpx2
import pytest
from hardline import build_runtime
from hardline.adapters._out.mock import MockBackend
from hardline.adapters._out.openai_compat import OpenAICompatBackend
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from matrix import (
    AGENT_RESPONSE,
    SESSION,
    TASK,
    AgentDefinition,
    AgentRuntime,
    AgentRuntimeError,
    BoundAgent,
    ConfigError,
    MatrixConfig,
    compose,
    default_registry,
)
from matrix.adapters._out.observers.otel import OtelObserver
from matrix.adapters._out.runtime.model import HardlineModelRuntime, HardlineModelRuntimeConfig
from matrix.adapters._out.runtime.pydantic_ai import PydanticAiRuntime, PydanticAiRuntimeConfig

#: Every word looked up, by every session in this module.
LOOKED_UP: list[str] = []


def lookup(word: str) -> str:
    """Find a word in the dictionary."""
    LOOKED_UP.append(word)
    return {"ossify": "to turn into bone"}.get(word, "not found")


def shout(text: str) -> str:
    """Say it louder."""
    return text.upper()


def broken(word: str) -> str:
    """A tool that fails."""
    raise RuntimeError("the dictionary is on fire")


ASKS = {"tool_calls": [{"name": "lookup", "arguments": '{"word": "ossify"}'}]}


def _models(*script: Any, **row: Any) -> Any:
    """A hardline runtime with one mock row, ``q``, answering from ``script``."""
    options = {"script": list(script)} if script else {}
    return build_runtime(
        {
            "models": {
                "q": {"backend": "mock", "model": "m-1", "family": "x", "options": options, **row}
            }
        },  # fmt: skip
        backends={"mock": MockBackend()},
    )


def _runtime(*script: Any, tools: Any = None, **config: Any) -> PydanticAiRuntime:
    return PydanticAiRuntime(
        _models(*script),
        PydanticAiRuntimeConfig(default_model="q", **config),
        tools={"lookup": lookup} if tools is None else tools,
    )


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


async def test_an_agent_calls_its_tool_through_hardline_end_to_end_on_the_wire() -> None:
    LOOKED_UP.clear()
    server = Server(
        _chat(
            "r1",
            40,
            8,
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"word": "ossify"}'},
                }
            ],
        ),  # fmt: skip
        _chat("r2", 60, 9, content="Ossify means to turn into bone."),
    )
    models = build_runtime(
        {
            "models": {
                "q": {
                    "backend": "openai-compat",
                    "base_url": "http://127.0.0.1:9/v1",
                    "model": "qwen",
                    "family": "qwen",
                }
            }
        },  # fmt: skip
        backends={"openai-compat": OpenAICompatBackend(server.transport)},
    )
    runtime = PydanticAiRuntime(
        models, PydanticAiRuntimeConfig(default_model="q"), tools={"lookup": lookup}
    )
    definition = _agent(max_turns=4)
    runtime.check(definition)

    response = await runtime.run(definition, "What does ossify mean?")

    assert LOOKED_UP == ["ossify"]
    assert response.content == "Ossify means to turn into bone."
    assert response.transcript == ("Ossify means to turn into bone.",)
    (call,) = response.tool_calls
    assert (call["name"], call["input"], call["id"], call["error"]) == (
        "lookup", {"word": "ossify"}, "c1", False,
    )  # fmt: skip
    assert call["started"] <= call["ended"]
    assert (response.stop, response.finish_reason, response.native_finish_reason) == (
        "completed", "stop", "stop",
    )  # fmt: skip
    assert (response.model, response.family, response.provider) == (
        "qwen-served", "qwen", "openai-compat",
    )  # fmt: skip
    assert (response.tokens_input, response.tokens_output, response.num_turns) == (100, 17, 2)
    assert response.cost_usd is None  # hardline reports tokens; nothing estimates a cost
    assert response.session_id
    first, second = server.bodies
    assert first["messages"] == [
        {"role": "system", "content": "Define words."},
        {"role": "user", "content": "What does ossify mean?"},
    ]
    (offered,) = first["tools"]
    assert offered["function"]["name"] == "lookup"
    assert offered["function"]["description"] == "Find a word in the dictionary."
    assert second["messages"][2:] == [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
         "function": {"name": "lookup", "arguments": '{"word": "ossify"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "to turn into bone"},
    ]  # fmt: skip


class _Spy(MockBackend):
    """The mock backend, keeping the tools each request offered."""

    def __init__(self) -> None:
        super().__init__()
        self.offered: list[tuple[str, ...]] = []

    async def complete(self, spec: Any, request: Any, api_key: str | None) -> Any:
        self.offered.append(tuple(tool.name for tool in request.tools))
        return await super().complete(spec, request, api_key)


@pytest.mark.parametrize(
    ("tools", "offered"),
    [(None, ("lookup", "shout")), ((), ()), (("shout",), ("shout",))],
)
async def test_a_definition_s_tools_choose_from_the_runtime_s_and_none_means_none(
    tools: tuple[str, ...] | None, offered: tuple[str, ...]
) -> None:
    """``None`` is the runtime's default toolset, ``()`` no tools at all: never collapsed into
    the default (SECURITY.md M-2)."""
    spy = _Spy()
    models = build_runtime(
        {"models": {"q": {"backend": "mock", "model": "m", "family": "x"}}},
        backends={"mock": spy},
    )
    runtime = PydanticAiRuntime(
        models, PydanticAiRuntimeConfig(default_model="q"), tools={"lookup": lookup, "shout": shout}
    )
    definition = _agent(tools=tools)
    runtime.check(definition)
    await runtime.run(definition, "hi")
    assert spy.offered == [offered]


def test_check_refuses_what_a_session_could_not_play() -> None:
    runtime = _runtime()
    with pytest.raises(ConfigError, match="'spell'") as unknown_tool:
        runtime.check(_agent(tools=["lookup", "spell"]))
    assert "lookup" in (unknown_tool.value.fix or "")
    with pytest.raises(ConfigError, match="cannot resolve the model"):
        runtime.check(_agent(model="nowhere"))

    class Textual:
        structured_modes = frozenset({"prompt"})

        async def complete(self, spec: Any, request: Any, api_key: str | None) -> Any:
            raise AssertionError("never sent")

    models = build_runtime(
        {"models": {"t": {"backend": "textual", "model": "t", "family": "x"}}},
        backends={"textual": Textual()},
    )
    textual = PydanticAiRuntime(models, PydanticAiRuntimeConfig(default_model="t"),
                                tools={"lookup": lookup})  # fmt: skip
    with pytest.raises(ConfigError, match="cannot call tools"):
        textual.check(_agent())
    textual.check(_agent(tools=[]))  # with no tools offered, any row serves


async def test_reaching_max_turns_ends_the_session_as_a_limit_not_a_failure() -> None:
    LOOKED_UP.clear()
    response = await _runtime(ASKS).run(_agent(max_turns=2), "Define ossify, forever.")
    assert (response.stop, response.num_turns, response.content) == ("max_turns", 2, "")
    assert LOOKED_UP == ["ossify", "ossify"]
    assert [call["id"] for call in response.tool_calls] == ["mock-q-1-0", "mock-q-2-0"]
    assert response.finish_reason == "tool_call"


async def test_a_failed_call_is_an_agent_runtime_error_carrying_what_the_session_spent() -> None:
    runtime = PydanticAiRuntime(
        _models(ASKS, {"error": "rate_limit"}, retries=0),
        PydanticAiRuntimeConfig(default_model="q"),
        tools={"lookup": lookup},
    )
    with pytest.raises(AgentRuntimeError) as raised:
        await runtime.run(_agent(), "Define ossify.")
    error = raised.value
    assert (error.reason, error.retryable) == ("rate_limited", True)
    spent = error.response
    assert spent is not None
    assert (spent.num_turns, spent.model, spent.family) == (1, "m-1", "x")
    assert spent.tokens_input and spent.tokens_input > 0
    assert [call["name"] for call in spent.tool_calls] == ["lookup"]


async def test_a_tool_that_raises_fails_the_session_saying_so() -> None:
    runtime = _runtime(ASKS, tools={"lookup": broken})
    with pytest.raises(AgentRuntimeError, match="dictionary is on fire") as raised:
        await runtime.run(_agent(), "Define ossify.")
    assert raised.value.reason == "failed"


def test_it_is_registered_saying_what_it_drives_and_accepts() -> None:
    registry = default_registry(discover=False)
    entry = registry.entry("agent-runtime", "matrix.v1.agent-runtime.pydantic-ai")
    assert entry.drives == ("pydantic-ai-slim", "hardline")
    assert not entry.stand_in and entry.needs == frozenset({"models"})
    assert entry.config is PydanticAiRuntimeConfig
    assert set(PydanticAiRuntimeConfig.model_fields) == {
        "models", "default_model", "tools", "temperature", "max_tokens",
    }  # fmt: skip
    assert isinstance(_runtime(), AgentRuntime)
    assert _runtime().usage_counted_by == "hardline"


def test_tools_named_in_config_are_imported_when_the_runtime_is_built() -> None:
    config: dict[str, Any] = {"agent_runtimes": {"p": {
        "type": "pydantic-ai", "default_model": "q",
        "models": {"models": {"q": {"backend": "mock", "model": "m", "family": "x"}}},
        "tools": {"lookup": f"{__name__}:lookup", "gone": f"{__name__}:no_such_tool"},
    }}}  # fmt: skip
    with pytest.raises(ConfigError) as raised:
        compose(MatrixConfig.model_validate(config), registry=default_registry(discover=False))
    assert "tools.gone" in str(raised.value)
    # a problem the runtime raises is placed under the runtime's key, as a config one is
    assert [p.path for p in raised.value.problems] == ["matrix.agent_runtimes.p.tools.gone"]
    del config["agent_runtimes"]["p"]["tools"]["gone"]
    box = compose(MatrixConfig.model_validate(config), registry=default_registry(discover=False))
    runtime = box.agent_runtime("p")
    assert isinstance(runtime, PydanticAiRuntime)
    assert runtime.resolved["tools"] == {"lookup": f"{__name__}:lookup"}


async def test_its_session_row_names_the_model_that_answered_its_usage_and_ids() -> None:
    box = compose(
        MatrixConfig.model_validate(
            {
                "models": {
                    "models": {
                        "q": {
                            "backend": "mock",
                            "model": "m-1",
                            "family": "x",
                            "options": {"script": [ASKS, "bone, made"]},
                        }
                    }
                },
                "agent_runtimes": {
                    "p": {
                        "type": "pydantic-ai",
                        "default_model": "q",
                        "tools": {"lookup": f"{__name__}:lookup"},
                    }
                },
                "agents": {"lex": {"runtime": "p", "system_prompt": "Define words."}},
            }
        ),  # fmt: skip
        registry=default_registry(discover=False),
    )
    wiring = box.wire(
        [{"name": "define", "type": "matrix.v1.agent-step", "config": {"agent": "lex"}}],
        inputs=[TASK],
    )
    run = await box.executor().run(wiring, {TASK: "Define ossify."})
    (session,) = run.rows(SESSION)
    assert (session.port, session.runtime, session.drives) == (
        "agent-runtime", "matrix.v1.agent-runtime.pydantic-ai", ("pydantic-ai-slim", "hardline"),
    )  # fmt: skip
    assert (session.status, session.stop, session.turns) == ("completed", "completed", 2)
    assert (session.model, session.family, session.finish_reason) == ("m-1", "x", "stop")
    assert session.usage is not None and session.usage.input_tokens > 0
    assert session.session_id
    (reply,) = run.rows(AGENT_RESPONSE)
    assert (reply.content, reply.session) == ("bone, made", 0)


def _points(reader: InMemoryMetricReader, name: str) -> list[Any]:
    data = reader.get_metrics_data()
    return [
        point
        for resource in (data.resource_metrics if data else ())
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


def _counted(reader: InMemoryMetricReader, name: str) -> int:
    return sum(point.value for point in _points(reader, name))


async def test_one_agent_run_counts_each_token_once() -> None:
    """hardline counts ``gen_ai.client.inference.usage.*`` for every model call it makes, so
    matrix's agent-session counters leave this runtime's sessions alone (W7.12): the runtime
    declares it (``usage_counted_by``), and nothing reads its name."""
    reader = InMemoryMetricReader()
    meters = MeterProvider(metric_readers=[reader])
    models = build_runtime(
        {
            "models": {
                "q": {
                    "backend": "mock",
                    "model": "m",
                    "family": "x",
                    "options": {"script": [ASKS, "bone"]},
                }
            }
        },  # fmt: skip
        backends={"mock": MockBackend()},
        meter_provider=meters,
    )
    runtime = PydanticAiRuntime(models, PydanticAiRuntimeConfig(default_model="q"),
                                tools={"lookup": lookup})  # fmt: skip
    observer = OtelObserver(tracer_provider=TracerProvider(), meter_provider=meters)
    response = await BoundAgent(_agent(), runtime, observers=[observer]).run("Define ossify.")
    assert response.tokens_input and response.tokens_output
    assert _counted(reader, "gen_ai.client.inference.usage.input_tokens") == response.tokens_input
    assert _counted(reader, "gen_ai.client.inference.usage.output_tokens") == response.tokens_output
    (timed,) = _points(reader, "gen_ai.invoke_agent.duration")  # the session is still timed
    assert timed.count == 1


async def test_pydantic_ai_records_no_content_even_when_instrumented_process_wide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pydantic-ai's instrumentation is off unless something turns it on for every agent
    (``Agent.instrument_all``), and then records prompts and replies by default
    (``InstrumentationSettings.include_content``). This runtime's agents switch it off, since
    hardline and matrix already trace, without content (P1.1)."""
    from pydantic_ai import Agent
    from pydantic_ai.models.instrumented import InstrumentationSettings

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        Agent, "_instrument_default", InstrumentationSettings(tracer_provider=provider)
    )
    await _runtime(ASKS, "secret answer").run(_agent(), "a secret question")
    assert exporter.get_finished_spans() == ()


class _Closing(MockBackend):
    def __init__(self) -> None:
        super().__init__()
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1


async def test_connections_close_once_the_last_session_in_a_loop_ends() -> None:
    """Sessions share the runtime's connections within an event loop: they are closed when
    the last session running in that loop ends, never under one still running."""
    backend = _Closing()
    models = build_runtime(
        {"models": {"q": {"backend": "mock", "model": "m", "family": "x"}}},
        backends={"mock": backend},
    )
    runtime = PydanticAiRuntime(models, PydanticAiRuntimeConfig(default_model="q"))
    await asyncio.gather(*(runtime.run(_agent(tools=[]), f"ask {i}") for i in range(3)))
    assert backend.closed == 1
    await runtime.run(_agent(tools=[]), "again")
    assert backend.closed == 2


def test_importing_matrix_does_not_import_pydantic_ai() -> None:
    script = (
        "import sys\n"
        "import matrix\n"
        "matrix.compose(matrix.MatrixConfig.model_validate(\n"
        "    {'agent_runtimes': {'r': {'type': 'mock'}}}))\n"
        "assert 'pydantic_ai' not in sys.modules, 'pydantic_ai was imported'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


async def test_a_hardline_model_runtime_gives_a_pydantic_ai_model_on_a_row_it_names() -> None:
    """W11.2b: a caller that runs pydantic-ai itself (ix's evaluator judge) asks a model
    runtime for a pydantic-ai ``Model`` (``pydantic_ai_model``, optional on the port), so the
    model comes from the runtime's config and nowhere else: its row, and its ``max_tokens`` and
    ``temperature``. Each request is one hardline call; the connections close with ``aclose``."""
    from pydantic_ai import Agent

    runtime = HardlineModelRuntime(
        _models("four"), HardlineModelRuntimeConfig(default_model="q", max_tokens=32)
    )
    model = runtime.pydantic_ai_model(None)
    assert model.model_name == "q"
    agent: Agent[None, str] = Agent(model)
    agent.instrument = False
    assert (await agent.run("What is 2 + 2?")).output == "four"
    (call,) = model.session.calls
    assert call.name == "q" and call.params is not None and call.params.max_tokens == 32
    await model.aclose()
    with pytest.raises(ConfigError, match="cannot resolve the model"):
        runtime.pydantic_ai_model("unknown")


async def test_a_session_names_the_provider_its_row_names() -> None:
    """W7.11: the row's ``provider:`` is who served the session; without one, its backend's
    name stands in (above)."""
    runtime = PydanticAiRuntime(
        _models("Bone.", provider="ollama"), PydanticAiRuntimeConfig(default_model="q")
    )
    assert (await runtime.run(_agent(), "Define ossify.")).provider == "ollama"


async def test_each_call_the_pydantic_ai_model_makes_is_a_session_row_as_a_steps_is() -> None:
    """Regression: a judge evaluator's model calls left no session row. pydantic-ai makes them
    through the Model a model runtime hands out (``pydantic_ai_model``), never through
    ``call_model``, so nothing recorded them. Each is now a row of the run's session table, with
    the fields a model step's call has, under the runtime's type URL; a call that failed is a
    row too, saying why. Outside a run nothing is recorded, as for any call."""
    from hardline import BackendError
    from pydantic_ai import Agent

    from matrix import ModelRequest, Session, call_model
    from matrix.domain.session import CURRENT_SESSIONS

    models = build_runtime(
        {
            "models": {
                "q": {
                    "backend": "mock",
                    "model": "m-1",
                    "family": "x",
                    "provider": "ollama",
                    "options": {"default": "four"},
                },
                "down": {
                    "backend": "mock",
                    "model": "m-2",
                    "family": "x",
                    "retries": 0,
                    "options": {"script": [{"error": "unavailable"}]},
                },
            }
        },  # fmt: skip
        backends={"mock": MockBackend()},
    )
    url, drives = "matrix.v1.model-runtime.hardline", ("hardline",)
    runtime = HardlineModelRuntime(
        models, HardlineModelRuntimeConfig(default_model="q"), runtime_url=url, drives=drives
    )
    rows: list[Session] = []

    def record(row: Session) -> int:
        rows.append(row)
        return len(rows) - 1

    def ask(model: Any) -> Agent[None, str]:
        agent: Agent[None, str] = Agent(model)
        agent.instrument = False
        return agent

    await ask(runtime.pydantic_ai_model("q")).run("What is 2 + 2?")  # outside a run
    assert rows == []
    recording = CURRENT_SESSIONS.set(record)
    try:
        request = ModelRequest(prompt="What is 2 + 2?")
        await call_model(runtime, request, name="hardline", runtime_url=url, drives=drives)
        assert (await ask(runtime.pydantic_ai_model("q")).run("What is 2 + 2?")).output == "four"
        with pytest.raises(BackendError, match="unavailable"):
            await ask(runtime.pydantic_ai_model("down")).run("What is 2 + 2?")
    finally:
        CURRENT_SESSIONS.reset(recording)
    stepped, judged, failed = rows
    shared = (
        "port", "runtime", "drives", "status", "name", "model", "family", "provider", "backend",
        "finish_reason", "native_finish_reason", "attempts", "retries", "row",
    )  # fmt: skip
    assert {k: getattr(judged, k) for k in shared} == {k: getattr(stepped, k) for k in shared}
    assert (judged.runtime, judged.drives, judged.provider) == (url, drives, "ollama")
    assert judged.usage is not None and judged.request_id and judged.attempt_log
    assert judged.sent.startswith("sha256:") and judged.sent != stepped.sent
    assert (failed.status, failed.reason, failed.name, failed.runtime) == (
        "failed", "unavailable", None, url,
    )  # fmt: skip
    assert [(a.name, a.reason) for a in failed.attempt_log or ()] == [("down", "unavailable")]

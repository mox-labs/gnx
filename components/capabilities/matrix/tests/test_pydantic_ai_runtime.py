"""The ``pydantic-ai`` agent runtime and :func:`matrix.pydantic_ai_model`, over the model-runtime
port.

Every model here is :class:`Scripted`, a model runtime written against the port alone: it
answers from a script, asks for tools, fails as told, and keeps each request it was sent. That
pydantic-ai's whole loop runs on it, tools and refusals included, is the port's claim that an
agent framework needs no model runtime but the port (hardline's, through the bridge, is
``matrix-hardline/tests``). Nothing leaves the process.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
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
    ConfigError,
    MatrixConfig,
    ModelCall,
    ModelRequest,
    ModelRuntime,
    ModelRuntimeError,
    Registry,
    Session,
    SupportsAclose,
    TokenUsage,
    ToolCall,
    Turn,
    Use,
    call_model,
    compose,
    default_registry,
    pydantic_ai_model,
)
from matrix.adapters._in.cli import main
from matrix.adapters._out.observers.otel import OtelObserver
from matrix.adapters._out.runtime.pydantic_ai import PydanticAiRuntime, PydanticAiRuntimeConfig
from matrix.domain.session import CURRENT_SESSIONS
from matrix.testing import ModelRuntimeConformance

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


#: A script entry that asks for ``lookup("ossify")``.
ASKS = [("lookup", '{"word": "ossify"}')]


class Scripted:
    """A model runtime that answers from ``script``, in order, then ``"done"``: a text, a list
    of ``(tool, arguments)`` the model asks for, or an exception to raise. It resolves the
    model names ``known`` (and none, its default), refuses tools when ``tools`` is False, and
    keeps each request and how often it was closed."""

    def __init__(
        self,
        *script: Any,
        known: tuple[str, ...] = ("q",),
        tools: bool = True,
        provider: str | None = "acme",
    ) -> None:
        self._script = list(script)
        self._known = known
        self._tools = tools
        self._provider = provider
        self.requests: list[ModelRequest] = []
        self.closed = 0

    def check(self, model: str | None, *, uses: frozenset[Use] = frozenset()) -> None:
        if model is not None and model not in self._known:
            raise ConfigError(f"cannot resolve the model {model!r}", fix="name q")
        if "tools" in uses and not self._tools:
            raise ConfigError(f"model {model!r} cannot call tools", fix="offer no tools")

    async def call(self, request: ModelRequest) -> ModelCall:
        self.requests.append(request)
        n = len(self.requests)
        entry = self._script.pop(0) if self._script else "done"
        if isinstance(entry, Exception):
            raise entry
        facts: dict[str, Any] = {
            "name": request.model or "q",
            "model": "m-1",
            "family": "x",
            "provider": self._provider,
            "usage": TokenUsage(input_tokens=10 * n, output_tokens=2),
            "request_id": f"r{n}",
        }
        if isinstance(entry, list):
            asked = tuple(
                ToolCall(id=f"c{n}-{i}", name=tool, arguments=arguments)
                for i, (tool, arguments) in enumerate(entry)
            )
            return ModelCall(tool_calls=asked, finish_reason="tool_call", **facts)
        return ModelCall(text=entry, finish_reason="stop", native_finish_reason="end", **facts)

    async def aclose(self) -> None:
        self.closed += 1


class TestTheScriptedStandIn(ModelRuntimeConformance):
    """The stand-in keeps the port it stands in for."""

    def runtime(self) -> ModelRuntime:
        return Scripted("hello")


def _runtime(*script: Any, tools: Any = None, **config: Any) -> PydanticAiRuntime:
    return PydanticAiRuntime(
        Scripted(*script),
        PydanticAiRuntimeConfig(model_runtime="m", default_model="q", **config),
        tools={"lookup": lookup} if tools is None else tools,
    )


def _agent(**fields: Any) -> AgentDefinition:
    return AgentDefinition.model_validate(
        {"name": "lexicographer", "system_prompt": "Define words.", **fields}
    )


async def test_an_agent_s_loop_runs_on_a_model_runtime_through_the_port() -> None:
    """Each model call of a session is one call on the port: the conversation so far as
    turns, the tools offered as specs, the overrides from the runtime's config; the reply's
    text and tool calls are pydantic-ai's response, and the session's facts are its calls'."""
    LOOKED_UP.clear()
    model = Scripted(ASKS, "Ossify means to turn into bone.")
    runtime = PydanticAiRuntime(
        model,
        PydanticAiRuntimeConfig(model_runtime="m", default_model="q", max_tokens=64),
        tools={"lookup": lookup},
    )
    assert isinstance(model, ModelRuntime) and isinstance(runtime, AgentRuntime)
    definition = _agent(max_turns=4)
    runtime.check(definition)

    response = await runtime.run(definition, "What does ossify mean?")

    assert LOOKED_UP == ["ossify"]
    assert response.content == "Ossify means to turn into bone."
    assert response.transcript == ("Ossify means to turn into bone.",)
    (call,) = response.tool_calls
    assert (call["name"], call["input"], call["id"], call["error"]) == (
        "lookup", {"word": "ossify"}, "c1-0", False,
    )  # fmt: skip
    assert call["started"] <= call["ended"]
    assert (response.stop, response.finish_reason, response.native_finish_reason) == (
        "completed", "stop", "end",
    )  # fmt: skip
    assert (response.model, response.family, response.provider) == ("m-1", "x", "acme")
    assert (response.tokens_input, response.tokens_output, response.num_turns) == (30, 4, 2)
    assert response.cost_usd is None  # tokens are reported; nothing estimates a cost
    assert response.session_id
    first, second = model.requests
    assert first.turns == (
        Turn(role="system", content="Define words."),
        Turn(role="user", content="What does ossify mean?"),
    )
    assert (first.model, first.max_tokens, first.temperature) == ("q", 64, None)
    (offered,) = first.tools
    assert (offered.name, offered.description) == ("lookup", "Find a word in the dictionary.")
    assert second.turns[2:] == (
        Turn(
            role="assistant",
            tool_calls=(ToolCall(id="c1-0", name="lookup", arguments='{"word": "ossify"}'),),
        ),
        Turn(role="tool", tool_call_id="c1-0", content="to turn into bone"),
    )


async def test_a_tool_that_failed_is_sent_back_as_an_error_turn() -> None:
    """A tool's failure reaches the model runtime as a tool turn marked ``is_error``, which
    each runtime carries to its provider (hardline 0.3.1's ``openai-compat`` marks it in the
    content), so the model is never told a failed tool answered."""
    model = Scripted(ASKS, "Sorry.")
    runtime = PydanticAiRuntime(
        model, PydanticAiRuntimeConfig(model_runtime="m"), tools={"lookup": _retrying}
    )
    await runtime.run(_agent(), "Define ossify.")
    (answered,) = [turn for turn in model.requests[1].turns if turn.role == "tool"]
    assert answered.is_error and "not in this dictionary" in answered.content


def _retrying(word: str) -> str:
    """Find a word in the dictionary."""
    from pydantic_ai import ModelRetry

    raise ModelRetry(f"{word!r} is not in this dictionary")


@pytest.mark.parametrize(
    ("tools", "offered"),
    [(None, ("lookup", "shout")), ((), ()), (("shout",), ("shout",))],
)
async def test_a_definition_s_tools_choose_from_the_runtime_s_and_none_means_none(
    tools: tuple[str, ...] | None, offered: tuple[str, ...]
) -> None:
    """``None`` is the runtime's default toolset, ``()`` no tools at all: never collapsed into
    the default (SECURITY.md M-2)."""
    model = Scripted("hi")
    runtime = PydanticAiRuntime(
        model, PydanticAiRuntimeConfig(model_runtime="m"), tools={"lookup": lookup, "shout": shout}
    )
    definition = _agent(tools=tools)
    runtime.check(definition)
    await runtime.run(definition, "hi")
    assert [tuple(tool.name for tool in request.tools) for request in model.requests] == [offered]


def test_check_refuses_what_a_session_could_not_play() -> None:
    """A tool the runtime does not have, a model its model runtime cannot resolve, and tools
    on a model its model runtime cannot offer them to, are refused at binding."""
    runtime = _runtime()
    with pytest.raises(ConfigError, match="'spell'") as unknown_tool:
        runtime.check(_agent(tools=["lookup", "spell"]))
    assert "lookup" in (unknown_tool.value.fix or "")
    with pytest.raises(ConfigError, match="cannot resolve the model 'nowhere'"):
        runtime.check(_agent(model="nowhere"))
    textual = PydanticAiRuntime(
        Scripted(tools=False), PydanticAiRuntimeConfig(model_runtime="m"), tools={"lookup": lookup}
    )
    with pytest.raises(ConfigError, match="cannot call tools"):
        textual.check(_agent())
    textual.check(_agent(tools=[]))  # with no tools offered, any model serves


def test_a_model_runtime_it_names_must_be_composed() -> None:
    """``model_runtime`` names one of ``matrix.model_runtimes``; one that names none is a
    problem at its key, saying which there are."""
    config = {
        "model_runtimes": {"m": {"type": "mock"}},
        "agent_runtimes": {"p": {"type": "pydantic-ai", "model_runtime": "gone"}},
    }
    with pytest.raises(ConfigError) as raised:
        compose(MatrixConfig.model_validate(config), registry=default_registry(discover=False))
    (found,) = raised.value.problems
    assert (found.path, found.kind, found.allowed) == (
        "matrix.agent_runtimes.p.model_runtime", "name_unknown", ("m",),
    )  # fmt: skip


def test_a_runtime_that_names_no_model_runtime_is_on_the_only_one_composed() -> None:
    """Ruling M-RT: unset, ``model_runtime`` is the one model runtime the config composes,
    resolved when the runtime is built, and its config says which. With none or several
    composed it is refused (``test_plan_and_run``)."""
    config = {
        "model_runtimes": {"m": {"type": "mock"}},
        "agent_runtimes": {"p": {"type": "pydantic-ai"}},
    }
    box = compose(MatrixConfig.model_validate(config), registry=default_registry(discover=False))
    runtime = box.agent_runtime("p")
    assert isinstance(runtime, PydanticAiRuntime)
    assert runtime.config.model_runtime == "m"
    assert runtime.model_runtime is box.model_runtimes["m"]


async def test_reaching_max_turns_ends_the_session_as_a_limit_not_a_failure() -> None:
    LOOKED_UP.clear()
    response = await _runtime(ASKS, ASKS).run(_agent(max_turns=2), "Define ossify, forever.")
    assert (response.stop, response.num_turns, response.content) == ("max_turns", 2, "")
    assert LOOKED_UP == ["ossify", "ossify"]
    assert [call["id"] for call in response.tool_calls] == ["c1-0", "c2-0"]
    assert response.finish_reason == "tool_call"


async def test_a_failed_call_is_an_agent_runtime_error_carrying_what_the_session_spent() -> None:
    """The model runtime's classification is the session's, with its Retry-After, and what
    the failed call reported is counted with what the session spent before it."""
    failed = ModelRuntimeError(
        "slow down",
        reason="rate_limited",
        retry_after_s=3.0,
        call=ModelCall(usage=TokenUsage(input_tokens=5)),
    )
    with pytest.raises(AgentRuntimeError) as raised:
        await _runtime(ASKS, failed).run(_agent(), "Define ossify.")
    error = raised.value
    assert (error.reason, error.retryable, error.retry_after_s) == ("rate_limited", True, 3.0)
    spent = error.response
    assert spent is not None
    assert (spent.num_turns, spent.model, spent.family, spent.tokens_input) == (1, "m-1", "x", 15)
    assert [call["name"] for call in spent.tool_calls] == ["lookup"]


async def test_a_tool_that_raises_fails_the_session_saying_so() -> None:
    runtime = _runtime(ASKS, tools={"lookup": broken})
    with pytest.raises(AgentRuntimeError, match="dictionary is on fire") as raised:
        await runtime.run(_agent(), "Define ossify.")
    assert raised.value.reason == "failed"


def test_it_is_registered_saying_what_it_drives_and_needs() -> None:
    """It drives pydantic-ai and names no model runtime: its calls are made on the model
    runtimes composition hands it, which it declares it needs."""
    registry = default_registry(discover=False)
    entry = registry.entry("agent-runtime", "matrix.v1.agent-runtime.pydantic-ai")
    assert entry.drives == ("pydantic-ai-slim",)
    assert not entry.stand_in and entry.needs == frozenset({"model_runtimes"})
    assert entry.config is PydanticAiRuntimeConfig
    assert set(PydanticAiRuntimeConfig.model_fields) == {
        "model_runtime", "default_model", "tools", "temperature", "max_tokens",
    }  # fmt: skip


def _tool_package(root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A package on the path whose ``__init__`` and ``tools`` module each leave a file behind
    when they run, and whose ``tools.define`` is a tool. Returns the package's name."""
    name = f"tools_{uuid.uuid4().hex}"
    package = root / name
    package.mkdir()
    (package / "__init__.py").write_text(f"open({str(root / 'PACKAGE_RAN')!r}, 'w').close()\n")
    (package / "tools.py").write_text(
        f"open({str(root / 'MODULE_RAN')!r}, 'w').close()\n\n"
        "def define(word: str) -> str:\n"
        '    """Find a word in the dictionary."""\n'
        "    return 'to turn into bone'\n"
    )
    monkeypatch.syspath_prepend(str(root))
    return name


def _tooled(package: str, **tools: str) -> dict[str, Any]:
    return {
        "model_runtimes": {"m": {"type": "mock"}},
        "agent_runtimes": {"p": {
            "type": "pydantic-ai", "model_runtime": "m",
            "tools": {"define": f"{package}.tools:define", **tools},
        }},
    }  # fmt: skip


async def test_a_tool_named_in_config_is_located_when_built_and_run_only_by_a_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: every configured tool was imported when the runtime was built, so
    ``matrix check`` and ``plan`` ran its module, and its package's, while saying nothing was
    called. Building only locates the module; a session imports what it offers."""
    package = _tool_package(tmp_path, monkeypatch)
    box = compose(
        MatrixConfig.model_validate(_tooled(package)), registry=default_registry(discover=False)
    )
    runtime = box.agent_runtime("p")
    assert isinstance(runtime, PydanticAiRuntime)
    assert runtime.resolved["tools"] == {"define": f"{package}.tools:define"}
    assert not (tmp_path / "PACKAGE_RAN").exists() and not (tmp_path / "MODULE_RAN").exists()
    assert package not in sys.modules

    asks = [("define", '{"word": "ossify"}')]
    scripted = PydanticAiRuntime(Scripted(asks, "to turn into bone"), runtime.config)
    assert not (tmp_path / "MODULE_RAN").exists()
    response = await scripted.run(_agent(), "define ossify")
    assert response.content == "to turn into bone"
    assert [call["name"] for call in response.tool_calls] == ["define"]
    assert (tmp_path / "MODULE_RAN").exists()


def test_check_and_plan_call_nothing_a_tool_s_module_included(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``matrix check`` and ``matrix plan`` say nothing was called: a tool's module is not run
    either, so a checkout's config cannot run code by being checked."""
    package = _tool_package(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / "matrix.yaml").write_text(json.dumps({"matrix": {
        **_tooled(package),
        "agents": {"a": {"runtime": "p", "system_prompt": "Define words."}},
        "inputs": ["matrix.v1.task"],
        "components": [{"name": "ask", "type": "matrix.v1.agent-step", "config": {"agent": "a"}}],
    }}))  # fmt: skip
    assert main(["check"]) == 0, capsys.readouterr()
    assert "nothing was called" in capsys.readouterr().out
    assert main(["plan"]) == 0, capsys.readouterr()
    assert not (tmp_path / "PACKAGE_RAN").exists() and not (tmp_path / "MODULE_RAN").exists()


def test_a_tool_whose_module_cannot_be_found_is_refused_when_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _tool_package(tmp_path, monkeypatch)
    config = _tooled(package, gone=f"{package}.nowhere:define", bare="not-a-reference")
    with pytest.raises(ConfigError) as raised:
        compose(MatrixConfig.model_validate(config), registry=default_registry(discover=False))
    # a problem the runtime raises is placed under the runtime's key, as a config one is
    assert sorted(p.path for p in raised.value.problems) == [
        "matrix.agent_runtimes.p.tools.bare",
        "matrix.agent_runtimes.p.tools.gone",
    ]
    assert not (tmp_path / "PACKAGE_RAN").exists()


async def test_a_tool_that_is_not_in_the_module_it_names_fails_the_session_as_a_config_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The module is found when the runtime is built; what it holds is known only once it
    runs, at the session that offers it."""
    package = _tool_package(tmp_path, monkeypatch)
    runtime = PydanticAiRuntime(
        Scripted("never asked"),
        PydanticAiRuntimeConfig(model_runtime="m", tools={"absent": f"{package}.tools:absent"}),
    )
    with pytest.raises(AgentRuntimeError) as raised:
        await runtime.run(_agent(), "define ossify")
    assert (raised.value.reason, raised.value.kind) == ("incapable", "config")
    assert "tools.absent" in str(raised.value)


@pytest.mark.parametrize(
    ("body", "error"),
    [("raise RuntimeError('boom at import')\n", "RuntimeError"), ("def (\n", "SyntaxError")],
)
async def test_a_tool_whose_module_raises_on_import_fails_the_session_as_a_config_fault(
    body: str, error: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: only an ImportError or AttributeError from importing a tool's module was
    read as the module not importing; one that raised anything else (a RuntimeError, a
    SyntaxError) failed the session as a component fault with no reason. Any error its
    import raises fails the session as ``incapable``, naming the tool and the error."""
    (tmp_path / "boom_at_import.py").write_text(body)
    monkeypatch.syspath_prepend(str(tmp_path))
    runtime = PydanticAiRuntime(
        Scripted("never asked"),
        PydanticAiRuntimeConfig(model_runtime="m", tools={"boom": "boom_at_import:define"}),
    )
    with pytest.raises(AgentRuntimeError) as raised:
        await runtime.run(_agent(), "define ossify")
    assert (raised.value.reason, raised.value.kind) == ("incapable", "config")
    assert "tools.boom" in str(raised.value) and error in str(raised.value)


async def test_a_tool_whose_import_failed_is_not_imported_again_by_a_later_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A module whose import raised is left out of ``sys.modules``, so importing it again
    runs its top-level code again. The runtime remembers the failure: every later session
    that offers the tool fails the same way, and the module's import runs once."""
    ran = tmp_path / "ran"
    (tmp_path / "counted_at_import.py").write_text(
        f"with open({str(ran)!r}, 'a') as f:\n"
        "    f.write('x')\n"
        "raise RuntimeError('boom at import')\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    runtime = PydanticAiRuntime(
        Scripted("never asked"),
        PydanticAiRuntimeConfig(model_runtime="m", tools={"boom": "counted_at_import:define"}),
    )
    failures = []
    for _ in range(2):
        with pytest.raises(AgentRuntimeError) as raised:
            await runtime.run(_agent(), "define ossify")
        failures.append((raised.value.reason, raised.value.kind, str(raised.value)))
    assert failures[0] == failures[1]
    assert failures[0][:2] == ("incapable", "config") and "RuntimeError" in failures[0][2]
    assert ran.read_text() == "x"


def test_a_tool_in_a_module_of_a_module_is_found_when_built() -> None:
    """Regression: a module whose parent is loaded but is no package (``os.path``) was read
    as not found, though it imports, so ``tools: {join: "os.path:join"}`` was refused."""
    config = _tooled("os")
    config["agent_runtimes"]["p"]["tools"] = {"join": "os.path:join"}
    box = compose(MatrixConfig.model_validate(config), registry=default_registry(discover=False))
    runtime = box.agent_runtime("p")
    assert isinstance(runtime, PydanticAiRuntime)
    assert runtime.resolved["tools"] == {"join": "os.path:join"}


def test_a_tool_in_a_module_whose_import_is_blocked_is_refused_when_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A module set to None in ``sys.modules`` cannot be imported, so it is not found."""
    monkeypatch.setitem(sys.modules, "blocked_tools", None)
    config = _tooled("os")
    config["agent_runtimes"]["p"]["tools"] = {"t": "blocked_tools:t"}
    with pytest.raises(ConfigError) as raised:
        compose(MatrixConfig.model_validate(config), registry=default_registry(discover=False))
    assert [p.path for p in raised.value.problems] == ["matrix.agent_runtimes.p.tools.t"]


def _scripted_registry(model: Scripted) -> Registry:
    """matrix's built-ins, and ``model`` at the model-runtime point, as an extension's would
    be."""
    registry = default_registry(discover=False)
    registry.register(
        "model-runtime", "acme.v1.model-runtime.scripted", lambda: model, drives=("acme",)
    )
    return registry


def _lexicon(model: Scripted) -> Any:
    return compose(
        MatrixConfig.model_validate(
            {
                "model_runtimes": {"m": {"type": "acme.v1.model-runtime.scripted"}},
                "agent_runtimes": {
                    "p": {
                        "type": "pydantic-ai",
                        "model_runtime": "m",
                        "tools": {"lookup": f"{__name__}:lookup"},
                    }
                },
                "agents": {"lex": {"runtime": "p", "system_prompt": "Define words."}},
            }
        ),  # fmt: skip
        registry=_scripted_registry(model),
    )


async def test_its_session_row_names_the_model_that_answered_its_usage_and_ids() -> None:
    box = _lexicon(Scripted(ASKS, "bone, made"))
    wiring = box.wire(
        [{"name": "define", "type": "matrix.v1.agent-step", "config": {"agent": "lex"}}],
        inputs=[TASK],
    )
    run = await box.executor().run(wiring, {TASK: "Define ossify."})
    (session,) = run.rows(SESSION)
    assert (session.port, session.runtime, session.drives) == (
        "agent-runtime", "matrix.v1.agent-runtime.pydantic-ai", ("pydantic-ai-slim",),
    )  # fmt: skip
    assert (session.status, session.stop, session.turns) == ("completed", "completed", 2)
    assert (session.model, session.family, session.provider) == ("m-1", "x", "acme")
    assert session.finish_reason == "stop"
    assert session.usage is not None and session.usage.input_tokens == 30
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


async def test_matrix_counts_no_token_of_a_session_made_on_a_model_runtime() -> None:
    """Changed requirement: the runtime declared ``usage_counted_by = "hardline"``, read by
    name. Its calls are now made on a matrix model runtime, which counts its own; composition
    knows (the runtime needs ``model_runtimes``), so matrix's agent-session counters count
    none of the session's tokens, and still time it. The bridge's tests show hardline counting
    them, once."""
    reader = InMemoryMetricReader()
    observer = OtelObserver(
        tracer_provider=TracerProvider(), meter_provider=MeterProvider(metric_readers=[reader])
    )
    box = _lexicon(Scripted(ASKS, "bone"))
    wiring = box.wire(
        [{"name": "define", "type": "matrix.v1.agent-step", "config": {"agent": "lex"}}],
        inputs=[TASK],
    )
    run = await box.executor(observers=[observer]).run(wiring, {TASK: "Define ossify."})
    (session,) = run.rows(SESSION)
    assert session.usage is not None and session.usage.input_tokens == 30  # kept on the row
    assert _points(reader, "gen_ai.client.inference.usage.input_tokens") == []
    (timed,) = _points(reader, "gen_ai.invoke_agent.duration")
    assert timed.count == 1


async def test_pydantic_ai_records_no_content_even_when_instrumented_process_wide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pydantic-ai's instrumentation is off unless something turns it on for every agent
    (``Agent.instrument_all``), and then records prompts and replies by default
    (``InstrumentationSettings.include_content``). This runtime's agents switch it off, since
    the model runtime and matrix already trace, without content."""
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


async def test_a_session_turns_pydantic_ais_banner_off() -> None:
    """Regression: pydantic-ai 2.6x prints a first-run banner on a terminal's stderr for a run
    it cannot attribute to an installed package, which a run through matrix's executor is
    not, so `matrix run --json` carried it. Under pytest pydantic-ai suppresses it itself, so
    what is checked is the flag it reads."""
    import pydantic_ai

    pydantic_ai.BANNER_ENABLED = True  # type: ignore[attr-defined,unused-ignore]
    await _runtime(ASKS, "an answer").run(_agent(), "a question")
    assert pydantic_ai.BANNER_ENABLED is False  # type: ignore[attr-defined,unused-ignore]


async def test_connections_close_once_the_last_session_in_a_loop_ends() -> None:
    """Sessions share the model runtime's connections within an event loop: they are closed
    when the last session running in that loop ends, never under one still running."""
    model = Scripted()
    runtime = PydanticAiRuntime(model, PydanticAiRuntimeConfig(model_runtime="m"))
    await asyncio.gather(*(runtime.run(_agent(tools=[]), f"ask {i}") for i in range(3)))
    assert model.closed == 1
    await runtime.run(_agent(tools=[]), "again")
    assert model.closed == 2


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


def _ask(model: Any) -> Any:
    from pydantic_ai import Agent

    agent: Agent[None, str] = Agent(model)
    agent.instrument = False
    return agent


async def test_pydantic_ai_model_is_a_model_over_any_model_runtime() -> None:
    """A caller that runs pydantic-ai itself (an evaluator's judge) gets a ``Model`` over any
    model runtime, on a name the runtime resolves, its settings sent as the call's overrides;
    its ``aclose`` closes the runtime's connections. A name the runtime does not resolve is
    refused when the Model is built."""
    runtime = Scripted("four")
    model = pydantic_ai_model(runtime, "q", settings={"max_tokens": 32})
    assert model.model_name == "q"
    assert (await _ask(model).run("What is 2 + 2?")).output == "four"
    (request,) = runtime.requests
    assert (request.model, request.max_tokens, request.prompt) == ("q", 32, "What is 2 + 2?")
    assert isinstance(model, SupportsAclose)  # the declared protocol, not a name
    await model.aclose()
    assert runtime.closed == 1
    with pytest.raises(ConfigError, match="cannot resolve the model 'unknown'"):
        pydantic_ai_model(runtime, "unknown")


async def test_a_setting_a_model_call_cannot_carry_is_refused_never_dropped() -> None:
    """M-5: the port carries ``max_tokens`` and ``temperature``. Any other pydantic-ai setting
    is a ConfigError, when the Model is built with it, else at the request that carries it,
    before anything is sent."""
    runtime = Scripted("four")
    with pytest.raises(ConfigError, match="top_p") as at_binding:
        pydantic_ai_model(runtime, settings={"temperature": 0.0, "top_p": 0.5})
    assert "max_tokens and temperature" in at_binding.value.message
    with pytest.raises(ConfigError, match="seed"):
        await _ask(pydantic_ai_model(runtime)).run("What is 2 + 2?", model_settings={"seed": 7})
    assert runtime.requests == []


async def test_each_call_the_pydantic_ai_model_makes_is_a_session_row_as_a_steps_is() -> None:
    """Regression: a judge evaluator's model calls left no session row. pydantic-ai makes them
    through the Model ``pydantic_ai_model`` gives, never through ``call_model``, so nothing
    recorded them. Each is now a row of the run's session table, with the fields a model
    step's call has, under the runtime its caller names; a call that failed is a row too,
    saying why. Outside a run nothing is recorded, as for any call."""
    unavailable = ModelRuntimeError(
        "down", reason="unavailable", call=ModelCall(usage=TokenUsage(input_tokens=1))
    )
    runtime = Scripted("four", "four", "four", unavailable)
    url, drives = "acme.v1.model-runtime.scripted", ("acme",)
    rows: list[Session] = []

    def record(row: Session) -> int:
        rows.append(row)
        return len(rows) - 1

    def model() -> Any:
        return pydantic_ai_model(runtime, "q", runtime_url=url, drives=drives)

    await _ask(model()).run("What is 2 + 2?")  # outside a run
    assert rows == []
    recording = CURRENT_SESSIONS.set(record)
    try:
        request = ModelRequest.from_prompt("What is 2 + 2?", model="q")
        await call_model(runtime, request, name="scripted", runtime_url=url, drives=drives)
        assert (await _ask(model()).run("What is 2 + 2?")).output == "four"
        with pytest.raises(ModelRuntimeError, match="down"):
            await _ask(model()).run("What is 2 + 2?")
    finally:
        CURRENT_SESSIONS.reset(recording)
    stepped, judged, failed = rows
    shared = ("port", "runtime", "drives", "status", "name", "model", "family", "provider",
              "finish_reason", "native_finish_reason")  # fmt: skip
    assert {k: getattr(judged, k) for k in shared} == {k: getattr(stepped, k) for k in shared}
    assert (judged.runtime, judged.drives, judged.provider) == (url, drives, "acme")
    assert judged.usage is not None and judged.request_id
    # Changed requirement: the judge's call digested its row, turns and tools by a rule of its
    # own, since it had no ModelRequest. It has one now, and one rule digests every call, so
    # equal requests (the same prompt to the same model) have equal digests whoever made them.
    assert judged.sent.startswith("sha256:") and judged.sent == stepped.sent
    assert (failed.status, failed.reason, failed.name, failed.runtime) == (
        "failed", "unavailable", None, url,
    )  # fmt: skip
    assert failed.usage == TokenUsage(input_tokens=1)

"""PydanticAiRuntime — a pydantic-ai ``Agent`` as an AgentRuntime, its model calls made on a
matrix model runtime.

An agent session here is pydantic-ai's loop: the model asks for tools, pydantic-ai validates
the arguments and runs them, and sends the results back, until the model answers or the
definition's ``max_turns`` is reached. Every model call is one call on the
:class:`~matrix.domain.ports._out.model_runtime.ModelRuntime` its config names
(``model_runtime``), so a session runs on whatever that runtime resolves a model name to, with
its retries, fallbacks, record and telemetry. Nothing here knows which runtime that is.

**The model.** pydantic-ai reaches a model through its ``Model`` class; the one here is backed
by a model runtime and a model name it resolves. It is the adapter between pydantic-ai's
shapes and the port's: pydantic-ai's requests and responses to turns and a ``ModelCall``,
its tool definitions to ``ToolSpec``s, and its model settings to the request's two overrides.
It lives in this file, the one that imports pydantic-ai; the port knows no agent framework.

**Settings it cannot carry are refused.** The port carries ``max_tokens`` and ``temperature``
and nothing else. Any other pydantic-ai setting (``top_p``, ``seed``, ``stop_sequences``, ...)
is a ``ConfigError``: when the ``Model`` is built with it (:func:`pydantic_ai_model`), else when
a request carries it, before anything is sent. It is never dropped. A provider's native tool,
or a prompt that is not text, is refused the same way.

**What a session can be given** is declared by :class:`PydanticAiRuntimeConfig` and nowhere
else (``matrix describe`` prints it): the model runtime (``model_runtime``), the model used when
a definition names none (``default_model``), the tools a definition may choose from (``tools``),
and the model settings. ``check`` refuses, at binding, a definition that names a tool the
runtime does not have, or a model, or tools on a model, that its model runtime refuses. A
definition's ``tools`` choose from the runtime's: ``None`` is all of them, ``()`` none.

**What a response carries.** The reply (``content``) is the run's output; every text the model
wrote along the way is the ``transcript``; each tool call is timed from the response that asked
for it to the request that carried its result, and ``error`` when the tool failed or its
arguments were refused. ``model``, ``family``, ``provider``, ``finish_reason`` and
``native_finish_reason`` are those of the call that answered last, as the model runtime reported
it; usage is summed over every call of the session; ``num_turns`` counts the calls;
``session_id`` is pydantic-ai's run id. No cost: pydantic-ai's own price estimate is not a
provider's report (owner ruling 2026-10-22: a cost is kept only as the provider's own report).

**Counted once.** The runtime registers with the need ``model_runtimes``, so composition marks
its sessions as made through a model runtime, which counts their calls' tokens; matrix's
agent-session counters leave them alone, and the session row keeps the usage.

**No content in telemetry.** pydantic-ai's instrumentation is off by default
(``Agent._instrument_default = False``, ``pydantic_ai/agent/__init__.py:549``, read by
``_resolve_instrumentation_settings``, ``:3416-3421``, which leaves the run on a ``NoOpTracer``,
``:4924-4928``). But ``Agent.instrument_all()`` (``:1190-1192``) turns it on for every agent that
has not chosen, and its ``InstrumentationSettings`` record prompts, replies and tool arguments
unless told not to (``include_content: bool = True``, ``pydantic_ai/models/instrumented.py:85``,
``:96``). Each agent here sets ``instrument = False`` (``:1199-1201``), so nothing in the process
can turn it on: the model runtime's spans and matrix's ``invoke_agent`` span trace a session,
without content.

**Connections.** A model runtime that holds connections (``SupportsAclose``) holds them per
event loop. Sessions in one loop share them, and they are closed when the last session running
in that loop ends.

**The model alone.** :func:`pydantic_ai_model` is the same ``Model`` for a caller that runs
pydantic-ai itself (an evaluator's judge), over any model runtime. Each call it makes is a row
of the run's session table, as a model step's call is (replied, failed or cancelled), under the
``runtime_url`` and ``drives`` its caller gives. Inside an agent session nothing records the
calls one by one: the session is the row.

``pydantic_ai`` is imported inside the functions that use it, so importing matrix loads none of
it. Type URL: ``matrix.v1.agent-runtime.pydantic-ai``.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import time
from dataclasses import dataclass, field
from functools import cache
from typing import TYPE_CHECKING, Annotated, Any, cast

from pydantic import BaseModel, ConfigDict, Field

from matrix.domain.agent import AgentCapabilities, AgentResponse
from matrix.domain.errors import AgentRuntimeError, ConfigError, ModelRuntimeError, invalid
from matrix.domain.model_call import ModelRequest, TokenUsage, ToolCall, ToolSpec, Turn
from matrix.domain.needs import ONLY_COMPOSED, model_runtime_named
from matrix.domain.optional import SupportsAclose
from matrix.domain.problems import Problem, problem
from matrix.domain.session import call_sent, call_session, record_cancelled, record_session

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
    from pydantic_ai.models import Model, ModelRequestParameters
    from pydantic_ai.settings import ModelSettings

    from matrix.domain.agent import AgentDefinition
    from matrix.domain.model_call import ModelCall, Use
    from matrix.domain.ports._out.model_runtime import ModelRuntime
    from matrix.domain.session import Session

#: The pydantic-ai settings a model call carries, as the port's overrides: no other.
CARRIED = frozenset({"max_tokens", "temperature"})


class PydanticAiRuntimeConfig(BaseModel):
    """A pydantic-ai agent loop whose model calls are made on a matrix model runtime (a config
    that reaches this is a capability grant, and naming a tool a grant to run its code, owner
    ruling 2026-10-22: see the package's SECURITY.md). `model_runtime` names one of
    `matrix.model_runtimes`; unset, it is the only one the config composes, and the runtime is
    refused when it composes none or several, so adding a second never changes which answers.
    An agent's `model` on this runtime is a name that model runtime resolves; unset,
    `default_model`, then the model runtime's own default. `tools` are the tools an agent may be
    given, each a Python function named `module:function`: its module is found when the runtime
    is built, without running it, and imported when a session first offers the tool (a module
    whose import fails is not imported again: every session that offers the tool fails); an
    agent's `tools` choose from them (unset: all; `[]`: none). The model runtime must be able
    to call tools on an agent's model when the agent is given any. `max_turns` bounds the model
    calls of a session. Calls report tokens, not cost."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The name, under ``matrix.model_runtimes``, of the model runtime every call is made on;
    #: unset, the only one composed, resolved when the runtime is built.
    model_runtime: Annotated[str | None, ONLY_COMPOSED, Field(min_length=1)] = None
    #: Used when a definition names no model. ``None`` = the model runtime's own default.
    default_model: str | None = None
    #: Tool name → ``module:function``, a Python function pydantic-ai runs when the model asks.
    #: Its parameters' types and its docstring are what the model is told of it.
    tools: dict[str, str] = Field(default_factory=dict)
    temperature: float | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, gt=0)

    def capabilities(self) -> AgentCapabilities:
        """A session loads no skill, from settings or its working directory: pydantic-ai has
        none, so no tool fires one and nowhere is one placed. It reads nothing of the machine
        beyond its config, so nothing keeps it out. Whether its tools write files is the
        functions' own to say."""
        return AgentCapabilities(settings_skills=False, project_skills=False)


class PydanticAiRuntime:
    """Runs AgentDefinitions as pydantic-ai agent sessions on a model runtime. ``tools`` adds
    tools given as functions in code to those the config names."""

    def __init__(
        self,
        model_runtime: ModelRuntime,
        config: PydanticAiRuntimeConfig,
        *,
        tools: Mapping[str, Callable[..., Any]] | None = None,
    ) -> None:
        self._model_runtime = model_runtime
        self._config = config
        unfound = [
            found
            for name, ref in self._config.tools.items()
            if (found := _unfound(name, ref)) is not None
        ]
        if unfound:
            raise invalid("the pydantic-ai runtime's tools cannot be found", unfound)
        #: Tool name → its function: those given in code, and each named one once a session
        #: has imported it.
        self._functions: dict[str, Callable[..., Any]] = dict(tools or {})
        clash = sorted(set(self._config.tools) & set(self._functions))
        if clash:
            raise ConfigError(
                f"tools.{clash[0]}: named in the runtime's config and given in code",
                fix="give each tool one name, in one place",
            )
        self._tools = sorted({*self._config.tools, *self._functions})
        #: Tool name → why its import failed: a later session fails the same way, and the
        #: module's top-level code does not run again.
        self._unimportable: dict[str, ConfigError] = {}
        self._sessions: dict[asyncio.AbstractEventLoop, int] = {}

    @classmethod
    def from_config(
        cls, config: PydanticAiRuntimeConfig, *, model_runtimes: Mapping[str, ModelRuntime]
    ) -> PydanticAiRuntime:
        """The runtime on the model runtime ``config.model_runtime`` names among
        ``model_runtimes``, or on the only one of them when it names none; a ConfigError at
        ``model_runtime`` when it names none of them, or names none and there are not exactly
        one. Its ``config`` names the one it is on."""
        name = model_runtime_named(config.model_runtime, model_runtimes, key="model_runtime")
        found = model_runtimes.get(name)
        if found is None:
            known = sorted(model_runtimes)
            raise invalid(
                "the pydantic-ai runtime's model runtime is not composed",
                [
                    problem(
                        "model_runtime",
                        "name_unknown",
                        f"{name!r} is not a composed model runtime",
                        fix="name one of matrix.model_runtimes, or define it there",
                        allowed=known,
                        got=name,
                    )
                ],
            )
        return cls(found, config.model_copy(update={"model_runtime": name}))

    @property
    def config(self) -> PydanticAiRuntimeConfig:
        return self._config

    @property
    def model_runtime(self) -> ModelRuntime:
        return self._model_runtime

    @property
    def resolved(self) -> dict[str, Any]:
        """What decides how a session answers, beyond the config and its needs (the model
        runtimes, which the fingerprint holds already): each tool's function, as the config
        names it or, for one given in code, as ``module:qualname``. Nothing is imported for
        it."""
        return {
            "tools": {
                name: self._config.tools.get(name)
                or f"{self._functions[name].__module__}:{self._functions[name].__qualname__}"
                for name in self._tools
            },
        }

    def check(self, definition: AgentDefinition) -> None:
        """Refuse, at binding, what a session could not play: a tool the runtime does not
        have, and a model, or tools on it, that the model runtime refuses."""
        unknown = [name for name in definition.tools or () if name not in self._tools]
        if unknown:
            known = ", ".join(self._tools) or "(none)"
            raise ConfigError(
                f"agent {definition.name!r} names tool {unknown[0]!r}, which the pydantic-ai "
                "runtime does not have",
                fix=f"name one of its tools ({known}), or add it to the runtime's `tools`",
                got=unknown[0],
            )
        model = definition.model or self._config.default_model
        uses: frozenset[Use] = frozenset({"tools"}) if self._offered(definition) else frozenset()
        try:
            self._model_runtime.check(model, uses=uses)
        except ConfigError as e:
            raise ConfigError(
                f"agent {definition.name!r} on the pydantic-ai runtime: {e.message}",
                fix=e.fix,
                got=model,
            ) from e

    def _offered(self, definition: AgentDefinition) -> list[str]:
        return list(self._tools) if definition.tools is None else list(definition.tools)

    def _function(self, name: str, definition: AgentDefinition) -> Callable[..., Any]:
        """The tool ``name``, imported the first time a session offers it. A named tool whose
        module was found but does not import (whatever its import raises), or does not hold
        the function, fails the session as ``incapable``: the runtime cannot honour its
        config. The failure is remembered, so every later session that offers the tool fails
        the same way without importing its module again."""
        function = self._functions.get(name)
        if function is not None:
            return function
        failed = self._unimportable.get(name)
        if failed is None:
            try:
                function = self._functions[name] = _imported(name, self._config.tools[name])
            except ConfigError as e:
                failed = self._unimportable[name] = e
            else:
                return function
        raise AgentRuntimeError(
            f"agent {definition.name!r}: {failed}",
            reason="incapable",
            fix=failed.fix,
            agent=definition.name,
        ) from failed

    async def aclose(self) -> None:
        """Close the connections the model runtime opened in the running event loop."""
        if isinstance(self._model_runtime, SupportsAclose):
            await self._model_runtime.aclose()

    async def run(self, definition: AgentDefinition, task: str) -> AgentResponse:
        """One pydantic-ai session, its model calls on the model runtime. A limit reached
        (``max_turns``) ends it; anything else that stops it is an AgentRuntimeError carrying
        what the session had done (``response``)."""
        from pydantic_ai import Agent, Tool, UsageLimitExceeded, capture_run_messages
        from pydantic_ai.usage import UsageLimits

        offered = {name: self._function(name, definition) for name in self._offered(definition)}
        model = _model_class()(
            self._model_runtime,
            definition.model or self._config.default_model,
            _Session(),
            None,
            None,
        )
        agent: Agent[None, str] = Agent(
            model,
            instructions=definition.system_prompt or None,
            name=definition.name,
            tools=[Tool(function, name=name) for name, function in offered.items()],
            model_settings=self._settings(),
        )
        agent.instrument = False  # see the module docstring: no content leaves by telemetry
        turns = definition.max_turns
        limits = UsageLimits() if turns is None else UsageLimits(request_limit=turns)
        session = model.session
        started = time.monotonic()
        loop = asyncio.get_running_loop()
        self._sessions[loop] = self._sessions.get(loop, 0) + 1
        try:
            with capture_run_messages() as messages:
                try:
                    result = await agent.run(task, usage_limits=limits)
                except UsageLimitExceeded:
                    return session.response(messages, started, stop="max_turns")
                except ModelRuntimeError as e:
                    raise AgentRuntimeError(
                        f"agent {definition.name!r}: {e.message}",
                        reason=e.reason,
                        fix=e.fix,
                        retry_after_s=e.retry_after_s,
                        response=session.response(messages, started, failed=e),
                        agent=definition.name,
                    ) from e
                except ConfigError as e:  # a setting or a part the port cannot carry
                    raise AgentRuntimeError(
                        f"agent {definition.name!r}: {e.message}",
                        reason="incapable",
                        fix=e.fix,
                        response=session.response(messages, started),
                        agent=definition.name,
                    ) from e
                except Exception as e:  # the port promises a response or a classified error
                    raise AgentRuntimeError(
                        f"agent {definition.name!r}: {type(e).__name__}: {e}",
                        reason="failed",
                        response=session.response(messages, started),
                        agent=definition.name,
                    ) from e
            return session.response(
                result.all_messages(),
                started,
                stop="completed",
                output=result.output,
                run_id=result.run_id,
            )
        finally:
            self._sessions[loop] -= 1
            if not self._sessions[loop]:
                del self._sessions[loop]
                await self.aclose()

    def _settings(self) -> ModelSettings | None:
        settings: dict[str, Any] = {}
        if self._config.max_tokens is not None:
            settings["max_tokens"] = self._config.max_tokens
        if self._config.temperature is not None:
            settings["temperature"] = self._config.temperature
        return cast("ModelSettings", settings) if settings else None


def pydantic_ai_model(
    runtime: ModelRuntime,
    model: str | None = None,
    *,
    settings: Mapping[str, Any] | None = None,
    runtime_url: str | None = None,
    drives: tuple[str, ...] = (),
) -> Model:
    """pydantic-ai's ``Model`` over any model runtime: each request it makes is one
    ``runtime.call`` on ``model`` (None: the runtime's default), for a caller that runs
    pydantic-ai itself (an evaluator's judge).

    ``settings`` are its default model settings, which a request's own override; only
    ``max_tokens`` and ``temperature`` are carried, and any other key is a ConfigError, here,
    or at the request that carries it. Inside a run, each call is a row of the run's session
    table, replied, failed or cancelled, under ``runtime_url`` and the distributions the
    runtime ``drives`` (its registry entry's). Its ``aclose()`` closes the connections its
    calls opened in the running event loop, when the runtime holds any. Raises ConfigError for
    a model the runtime does not resolve, as its ``check`` does."""
    given = dict(settings or {})
    _carried(given, where="the settings given")
    runtime.check(model)
    recording = _Recording(runtime_url, drives)
    return _model_class()(runtime, model, _Session(), given or None, recording)


def _carried(settings: Mapping[str, Any], *, where: str) -> None:
    """Raise ConfigError for each setting in ``settings`` a model call cannot carry."""
    refused = sorted(set(settings) - CARRIED)
    if refused:
        raise ConfigError(
            f"{where} hold {', '.join(refused)}, which a matrix model runtime's call cannot "
            "carry: it carries max_tokens and temperature, and no other setting",
            fix=f"drop {', '.join(refused)}: a setting is refused rather than ignored",
            got=refused[0],
        )


def _unfound(name: str, reference: str) -> Problem | None:
    """The problem at ``tools.<name>`` when ``reference`` is not ``module:function`` or its
    module cannot be found; None when it can. Nothing is imported to tell."""
    module, _, attribute = reference.partition(":")
    if not (module and attribute):
        why = "is not `module:function`"
    elif not _findable(module):
        why = f"names module {module!r}, which cannot be found"
    else:
        return None
    return problem(
        f"tools.{name}",
        "invalid_value",
        f"{reference!r} {why}",
        fix="name an importable function as `module:function`",
        got=reference,
    )


def _findable(module: str) -> bool:
    """Whether ``module`` can be found on the import path, without running it or the packages
    it is in: each package is searched by the locations its spec names, not by importing it."""
    if sys.modules.get(module) is not None:  # already run, whatever holds it (``os.path``)
        return True
    path: Sequence[str] | None = None
    parts = module.split(".")
    for end in range(1, len(parts) + 1):
        name = ".".join(parts[:end])
        loaded = sys.modules.get(name)
        if loaded is not None:  # already run: reading it runs nothing
            path = getattr(loaded, "__path__", None)
        else:
            spec = next(
                (
                    found
                    for finder in sys.meta_path
                    if (find := getattr(finder, "find_spec", None)) is not None
                    and (found := find(name, path)) is not None
                ),
                None,
            )
            if spec is None:
                return False
            path = spec.submodule_search_locations
        if end < len(parts) and path is None:
            return False  # a module, not a package: it holds no module
    return True


def _imported(name: str, reference: str) -> Callable[..., Any]:
    """The function ``module:function`` names: a ConfigError at ``tools.<name>`` when it does
    not import (whatever its import raises: a module is the tool author's code) or is not
    callable."""
    module, _, attribute = reference.partition(":")
    found: Any = None
    why = "is not `module:function`"
    if module and attribute:
        try:
            found = importlib.import_module(module)
            for part in attribute.split("."):
                found = getattr(found, part)
        except Exception as e:
            found, why = None, f"cannot be imported: {type(e).__name__}: {e}"
        else:
            why = "is not a function"
    if callable(found):
        return cast("Callable[..., Any]", found)
    fix = "name an importable function as `module:function`"
    raise ConfigError(
        f"tools.{name}: {reference!r} {why}",
        fix=fix,
        problems=[
            problem(
                f"tools.{name}", "invalid_value", f"{reference!r} {why}", fix=fix, got=reference
            )
        ],
    )


@dataclass(frozen=True)
class _Recording:
    """Where a handed-out ``Model``'s calls are recorded: the session rows' runtime and the
    distributions it drives."""

    runtime_url: str | None
    drives: tuple[str, ...]


@dataclass
class _Session:
    """What one session's model calls answered, in order."""

    calls: list[ModelCall] = field(default_factory=list)

    def response(
        self,
        messages: Sequence[ModelMessage],
        started: float,
        *,
        stop: str | None = None,
        output: Any = None,
        run_id: str | None = None,
        failed: ModelRuntimeError | None = None,
    ) -> AgentResponse:
        """The session as an AgentResponse: its reply, transcript and tool calls from the
        messages, its facts from the model runtime's calls (and the usage a failed call
        reported)."""
        from pydantic_ai.messages import ModelResponse, TextPart

        spent = [call.usage for call in self.calls if call.usage is not None]
        if failed is not None and failed.call is not None and failed.call.usage is not None:
            spent.append(failed.call.usage)
        usage = TokenUsage(
            input_tokens=sum(u.input_tokens for u in spent),
            output_tokens=sum(u.output_tokens for u in spent),
            cache_read_tokens=sum(u.cache_read_tokens for u in spent),
            cache_write_tokens=sum(u.cache_write_tokens for u in spent),
        )
        last = self.calls[-1] if self.calls else None
        texts = tuple(
            part.content
            for message in messages
            if isinstance(message, ModelResponse)
            for part in message.parts
            if isinstance(part, TextPart) and part.content
        )
        if run_id is None:
            run_id = next((m.run_id for m in messages if isinstance(m, ModelResponse)), None)
        return AgentResponse(
            content=output if isinstance(output, str) else "",
            transcript=texts,
            tool_calls=_tool_calls(messages),
            stop=stop,
            finish_reason=None if last is None else last.finish_reason,
            native_finish_reason=None if last is None else last.native_finish_reason,
            family=None if last is None else last.family,
            model=None if last is None else last.model,
            provider=None if last is None else last.provider,
            tokens_input=usage.input_tokens,
            tokens_output=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            num_turns=len(self.calls),
            duration_ms=int((time.monotonic() - started) * 1000),
            session_id=run_id,
        )


def _tool_calls(messages: Sequence[ModelMessage]) -> tuple[dict[str, Any], ...]:
    """Each tool call the model asked for, ``{name, input, id, started}``, with ``ended`` and
    ``error`` once a request carried its result: a return that failed, or a retry prompt (its
    arguments refused, or the tool asked for another try)."""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        RetryPromptPart,
        ToolCallPart,
        ToolReturnPart,
    )

    calls: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for message in messages:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, ToolCallPart):
                    call: dict[str, Any] = {
                        "name": part.tool_name,
                        "input": part.args_as_dict(),
                        "id": part.tool_call_id,
                        "started": message.timestamp.isoformat(),
                    }
                    by_id[part.tool_call_id] = call
                    calls.append(call)
        elif isinstance(message, ModelRequest):
            for answer in message.parts:
                if isinstance(answer, ToolReturnPart | RetryPromptPart):
                    asked = by_id.get(answer.tool_call_id)
                    if asked is not None:
                        failed = isinstance(answer, RetryPromptPart) or answer.outcome != "success"
                        asked.update(ended=answer.timestamp.isoformat(), error=failed)
    return tuple(calls)


#: The port's finish reasons in pydantic-ai's vocabulary. A refusal is the provider withholding
#: an answer; ``other`` has no name there.
_FINISH: dict[str, str] = {
    "stop": "stop",
    "length": "length",
    "content_filter": "content_filter",
    "refusal": "content_filter",
    "tool_call": "tool_call",
}


@cache
def _model_class() -> Callable[
    [ModelRuntime, str | None, _Session, dict[str, Any] | None, _Recording | None], _PortModel
]:
    """pydantic-ai's ``Model`` backed by a model runtime and one model name: defined when a
    session first needs it, so importing this module imports no pydantic-ai."""
    from pydantic_ai.messages import (
        ModelRequest as Request,
    )
    from pydantic_ai.messages import (
        ModelResponse,
        RetryPromptPart,
        SystemPromptPart,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )
    from pydantic_ai.models import Model
    from pydantic_ai.usage import RequestUsage

    class PortModel(Model):
        """One model name on a model runtime, as a pydantic-ai model. Each response it gives
        is one call on the port; the call is kept on ``session``, and, handed out, recorded
        as a session row."""

        def __init__(
            self,
            runtime: ModelRuntime,
            model: str | None,
            session: _Session,
            settings: dict[str, Any] | None,
            recording: _Recording | None,
        ) -> None:
            super().__init__(settings=cast("ModelSettings | None", settings))
            self._runtime = runtime
            self._model = model
            self._recording = recording
            self.session = session

        async def aclose(self) -> None:
            """Close the connections this model's calls opened in the running event loop."""
            if isinstance(self._runtime, SupportsAclose):
                await self._runtime.aclose()

        @property
        def model_name(self) -> str:
            return self._model or "default"

        @property
        def system(self) -> str:
            return "matrix"

        async def request(
            self,
            messages: list[ModelMessage],
            model_settings: ModelSettings | None,
            model_request_parameters: ModelRequestParameters,
        ) -> ModelResponse:
            settings, params = self.prepare_request(model_settings, model_request_parameters)
            given: dict[str, Any] = dict(settings or {})
            _carried(given, where="the model settings of this request")
            if params.native_tools:
                raise ConfigError(
                    "a matrix model runtime offers function tools only, not a provider's "
                    "native tools",
                    fix="give the agent function tools",
                )
            instructions = self._get_instruction_parts(messages, params) or []
            turns: list[Turn] = [Turn(role="system", content=i.content) for i in instructions]
            for message in messages:
                turns.extend(_turns(message))
            request = ModelRequest(
                turns=tuple(turns),
                model=self._model,
                tools=tuple(
                    ToolSpec(
                        name=tool.name,
                        description=tool.description or "",
                        parameters=dict(tool.parameters_json_schema),
                    )
                    for tool in (*params.declared_function_tools, *params.output_tools)
                ),
                max_tokens=given.get("max_tokens"),
                temperature=given.get("temperature"),
            )
            call = await self._call(request)
            self.session.calls.append(call)
            parts: list[Any] = [TextPart(call.text)] if call.text else []
            parts.extend(
                ToolCallPart(asked.name, asked.arguments, tool_call_id=asked.id)
                for asked in call.tool_calls
            )
            usage = call.usage or TokenUsage()
            return ModelResponse(
                parts=parts,
                usage=RequestUsage(
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                    cache_write_tokens=usage.cache_write_tokens,
                ),
                model_name=call.model or call.name,
                provider_name=call.provider,
                provider_response_id=call.request_id,
                finish_reason=cast("Any", _FINISH.get(call.finish_reason or "")),
            )

        async def _call(self, request: ModelRequest) -> ModelCall:
            """The call on the port; handed out, recorded as a model step's call is."""
            if self._recording is None:
                return await self._runtime.call(request)
            recording = self._recording

            def row(call: ModelCall | None, error: BaseException | None = None) -> Session:
                return call_session(
                    call,
                    sent=call_sent(request),
                    runtime=recording.runtime_url,
                    drives=recording.drives,
                    error=error,
                    prompt=request.prompt,
                )

            try:
                call = await self._runtime.call(request)
            except asyncio.CancelledError as e:
                record_cancelled(row(None, e))
                raise
            except Exception as e:
                record_session(row(e.call if isinstance(e, ModelRuntimeError) else None, e))
                raise
            session = record_session(row(call))
            return call if session is None else call.model_copy(update={"session": session})

    def _turns(message: ModelMessage) -> list[Turn]:
        """One pydantic-ai message as the port's turns."""
        if isinstance(message, ModelResponse):
            text = "".join(p.content for p in message.parts if isinstance(p, TextPart))
            calls = tuple(
                ToolCall(id=p.tool_call_id, name=p.tool_name, arguments=_arguments(p))
                for p in message.parts
                if isinstance(p, ToolCallPart)
            )
            return [Turn(role="assistant", content=text, tool_calls=calls)]
        assert isinstance(message, Request)
        turns: list[Turn] = []
        for part in message.parts:
            if isinstance(part, SystemPromptPart):
                turns.append(Turn(role="system", content=part.content))
            elif isinstance(part, UserPromptPart):
                if not isinstance(part.content, str):
                    raise ConfigError(
                        "a matrix model runtime takes text prompts only",
                        fix="give the agent its prompt as text",
                    )
                turns.append(Turn(role="user", content=part.content))
            elif isinstance(part, ToolReturnPart):
                turns.append(
                    Turn(
                        role="tool",
                        tool_call_id=part.tool_call_id,
                        content=part.model_response_str(),
                        is_error=part.outcome != "success",
                    )
                )
            elif isinstance(part, RetryPromptPart):
                if part.tool_name is None:
                    turns.append(Turn(role="user", content=part.model_response()))
                else:
                    turns.append(
                        Turn(
                            role="tool",
                            tool_call_id=part.tool_call_id,
                            content=part.model_response(),
                            is_error=True,
                        )
                    )
            else:
                raise ConfigError(
                    f"a matrix model runtime cannot send a {type(part).__name__}",
                    fix="give the agent text and function tools only",
                )
        return turns

    return cast(
        "Callable[[ModelRuntime, str | None, _Session, dict[str, Any] | None, "
        "_Recording | None], _PortModel]",
        PortModel,
    )


def _arguments(call: ToolCallPart) -> str:
    """A call's arguments as the JSON text the port carries: as the model wrote them, when it
    wrote text."""
    if call.args is None:
        return "{}"
    return call.args if isinstance(call.args, str) else call.args_as_json_str()


if TYPE_CHECKING:

    class _PortModel(Model):
        session: _Session

        async def aclose(self) -> None: ...

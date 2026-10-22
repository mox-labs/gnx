"""PydanticAiRuntime — a pydantic-ai ``Agent`` as an AgentRuntime, its model calls made by hardline.

An agent session here is pydantic-ai's loop: the model asks for tools, pydantic-ai validates
the arguments and runs them, and sends the results back, until the model answers or the
definition's ``max_turns`` is reached. Every model call goes through hardline, so a session
runs on hardline's registry rows, retries, fallbacks, call limits, record and telemetry, in any
model family hardline has a row for.

**The model.** pydantic-ai reaches a model through its ``Model`` class; the one here is backed
by a hardline runtime and one registry row. It is the adapter between the two message shapes:
pydantic-ai's requests and responses to hardline's turns and ``Completion``, and pydantic-ai's
tool definitions to hardline's ``ToolSpec``. It lives in this file, not in hardline: hardline
depends on pydantic, pyyaml and httpx2 only (Q13) and knows no agent framework, and pydantic-ai
knows no hardline, so the one module that depends on both, matrix's adapter, translates
between them. Nothing points back: hardline stays one layer down.

**What a session can be given** is declared by :class:`PydanticAiRuntimeConfig` and nowhere
else (``matrix describe`` prints it): the hardline registry (``models``), the row used when a
definition names none (``default_model``), the tools a definition may choose from (``tools``),
and the model settings. ``check`` refuses, at binding, a definition that names a tool the
runtime does not have, a model the registry cannot resolve, or tools for a row whose backend
cannot call them. A definition's ``tools`` choose from the runtime's: ``None`` is all of them,
``()`` none.

**What a response carries.** The reply (``content``) is the run's output; every text the model
wrote along the way is the ``transcript``; each tool call is timed from the response that asked
for it to the request that carried its result, and ``error`` when the tool failed or its
arguments were refused. ``model``, ``family``, ``finish_reason`` and ``native_finish_reason`` are
those of the call that answered last, hardline's ``Completion``; usage is summed over every call
of the session, each already summed over its attempts; ``num_turns`` counts the calls;
``session_id`` is pydantic-ai's run id. No cost: hardline reports tokens, and pydantic-ai's own
price estimate is not a provider's report (Q1).

**Counted once.** hardline counts the inference tokens of every call it makes. So this runtime
declares ``usage_counted_by = "hardline"``, and matrix's agent-session counters leave its
sessions to hardline; the session row keeps the usage.

**No content in telemetry.** pydantic-ai's instrumentation is off by default
(``Agent._instrument_default = False``, ``pydantic_ai/agent/__init__.py:549``, read by
``_resolve_instrumentation_settings``, ``:3416-3421``, which leaves the run on a ``NoOpTracer``,
``:4924-4928``). But ``Agent.instrument_all()`` (``:1190-1192``) turns it on for every agent that
has not chosen, and its ``InstrumentationSettings`` record prompts, replies and tool arguments
unless told not to (``include_content: bool = True``, ``pydantic_ai/models/instrumented.py:85``,
``:96``). Each agent here sets ``instrument = False`` (``:1199-1201``), so nothing in the process
can turn it on: hardline's ``chat`` spans and matrix's ``invoke_agent`` span trace a session,
without content (P1.1).

**Connections.** hardline's connections belong to the event loop that opened them. Sessions in
one loop share them, and they are closed when the last session running in that loop ends.

**The model alone.** :func:`hardline_model` is the same ``Model`` for a caller that runs
pydantic-ai itself: the ``hardline`` model runtime hands it out (``pydantic_ai_model``), so an
evaluator's judge calls its model on a row its config names, and nothing else.

``pydantic_ai`` and ``hardline`` are imported inside the methods that use them, so importing
matrix loads neither. Type URL: ``matrix.v1.agent-runtime.pydantic-ai``.
"""

from __future__ import annotations

import asyncio
import importlib
import time
from dataclasses import dataclass, field
from functools import cache
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, ConfigDict, Field

from matrix.domain.agent import AgentResponse
from matrix.domain.errors import AgentRuntimeError, ConfigError, RuntimeReason
from matrix.domain.problems import problem

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from hardline import Completion, HardlineError
    from hardline import Message as Turn
    from hardline import ModelRuntime as Hardline
    from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
    from pydantic_ai.models import Model, ModelRequestParameters
    from pydantic_ai.settings import ModelSettings

    from matrix.domain.agent import AgentDefinition


class PydanticAiRuntimeConfig(BaseModel):
    """A pydantic-ai agent loop whose model calls go through hardline (a config that reaches
    this is a capability grant: see the package's SECURITY.md). An agent's `model` on this
    runtime is a model name in the hardline registry (`models`, `matrix.models`, or hardline's
    own tiers); unset, `default_model`, then the registry's default. `tools` are the tools an
    agent may be given, each a Python function named `module:function`, imported when the
    runtime is built; an agent's `tools` choose from them (unset: all; `[]`: none). The row an
    agent runs on must be served by a hardline backend that can call tools when it is given
    any. `max_turns` bounds the model calls of a session. Calls report tokens, not cost."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: A hardline registry section. Composition fills this from the top-level ``matrix.models``
    #: when absent; ``None`` all the way down means hardline discovers its own tiers.
    models: dict[str, Any] | None = None
    #: Used when a definition names no model. ``None`` = the registry's own default.
    default_model: str | None = None
    #: Tool name → ``module:function``, a Python function pydantic-ai runs when the model asks.
    #: Its parameters' types and its docstring are what the model is told of it.
    tools: dict[str, str] = Field(default_factory=dict)
    temperature: float | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, gt=0)


class PydanticAiRuntime:
    """Runs AgentDefinitions as pydantic-ai agent sessions on hardline. ``tools`` adds tools
    given as functions in code to those the config names."""

    #: Whose counters count this runtime's model calls: hardline's, per call. matrix's
    #: agent-session counters leave its sessions alone, so no token is counted twice.
    usage_counted_by = "hardline"

    def __init__(
        self,
        models: Hardline,
        config: PydanticAiRuntimeConfig | None = None,
        *,
        tools: Mapping[str, Callable[..., Any]] | None = None,
    ) -> None:
        self._models = models
        self._config = config or PydanticAiRuntimeConfig()
        named = {name: _imported(name, ref) for name, ref in self._config.tools.items()}
        given = dict(tools or {})
        clash = sorted(set(named) & set(given))
        if clash:
            raise ConfigError(
                f"tools.{clash[0]}: named in the runtime's config and given in code",
                fix="give each tool one name, in one place",
            )
        self._tools = {**named, **given}
        self._sessions: dict[asyncio.AbstractEventLoop, int] = {}

    @classmethod
    def from_config(cls, config: PydanticAiRuntimeConfig) -> PydanticAiRuntime:
        from hardline import build_runtime

        models = (
            build_runtime(config=config.models, label="matrix.models")
            if config.models is not None
            else build_runtime()
        )
        return cls(models, config)

    @property
    def config(self) -> PydanticAiRuntimeConfig:
        return self._config

    @property
    def models(self) -> Hardline:
        return self._models

    @property
    def resolved(self) -> dict[str, Any]:
        """What decides how a session answers, beyond the config: the registry resolved, from
        ``models`` or hardline's own tiers (keys left out), and each tool's function."""
        registry = self._models.registry
        return {
            "default": registry.default,
            "models": {
                name: registry.get(name).model_dump(mode="json", exclude={"api_key"})
                for name in registry.names()
            },
            "tools": {
                name: f"{function.__module__}:{function.__qualname__}"
                for name, function in sorted(self._tools.items())
            },
        }

    def check(self, definition: AgentDefinition) -> None:
        """Refuse, at binding, what a session could not play: a tool the runtime does not
        have, a model the registry cannot resolve, and tools for a row that cannot call them."""
        from hardline import HardlineError

        unknown = [name for name in definition.tools or () if name not in self._tools]
        if unknown:
            known = ", ".join(sorted(self._tools)) or "(none)"
            raise ConfigError(
                f"agent {definition.name!r} names tool {unknown[0]!r}, which the pydantic-ai "
                "runtime does not have",
                fix=f"name one of its tools ({known}), or add it to the runtime's `tools`",
                got=unknown[0],
            )
        model = definition.model or self._config.default_model
        try:
            self._models.registry.get(model)
        except HardlineError as e:
            known = ", ".join(self._models.registry.names()) or "(none)"
            raise ConfigError(
                f"agent {definition.name!r}: the pydantic-ai runtime cannot resolve the model: {e}",
                fix=f"name one of its models ({known}), or add a row for it to `matrix.models` "
                "or a hardline config tier",
                got=model,
            ) from e
        if self._offered(definition):
            try:
                self._models.check_tools(model)
            except HardlineError as e:
                raise ConfigError(
                    f"agent {definition.name!r} is given tools, and {e}", fix=e.fix, got=model
                ) from e

    def _offered(self, definition: AgentDefinition) -> dict[str, Callable[..., Any]]:
        if definition.tools is None:
            return dict(self._tools)
        return {name: self._tools[name] for name in definition.tools}

    async def aclose(self) -> None:
        """Close the connections hardline opened in the running event loop."""
        await self._models.aclose()

    async def run(self, definition: AgentDefinition, task: str) -> AgentResponse:
        """One pydantic-ai session, its model calls through hardline. A limit reached
        (``max_turns``) ends it; anything else that stops it is an AgentRuntimeError carrying
        what the session had done (``response``)."""
        from hardline import HardlineError
        from pydantic_ai import Agent, Tool, UsageLimitExceeded, capture_run_messages
        from pydantic_ai.usage import UsageLimits

        model = _model_class()(
            self._models, definition.model or self._config.default_model, _Session(), None
        )
        agent: Agent[None, str] = Agent(
            model,
            instructions=definition.system_prompt or None,
            name=definition.name,
            tools=[
                Tool(function, name=name) for name, function in self._offered(definition).items()
            ],
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
                except HardlineError as e:
                    raise AgentRuntimeError(
                        f"agent {definition.name!r}: {e}",
                        reason=_reason(e),
                        retry_after_s=getattr(e, "retry_after", None),
                        response=session.response(messages, started, failed=e),
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
                await self._models.aclose()

    def _settings(self) -> ModelSettings | None:
        settings: dict[str, Any] = {}
        if self._config.max_tokens is not None:
            settings["max_tokens"] = self._config.max_tokens
        if self._config.temperature is not None:
            settings["temperature"] = self._config.temperature
        return cast("ModelSettings", settings) if settings else None


def hardline_model(
    models: Hardline, row: str | None, *, settings: Mapping[str, Any] | None = None
) -> _HardlineModel:
    """pydantic-ai's ``Model`` over ``row`` of the hardline runtime ``models`` (None: its
    default), each request one hardline call, for a caller that runs pydantic-ai itself.
    ``settings`` are its default model settings (``max_tokens``, ``temperature``), which a
    request's own override. Its ``aclose()`` closes the connections its calls opened in the
    running event loop. Raises hardline's ``UnknownModelError`` for a row it cannot resolve."""
    return _model_class()(models, row, _Session(), dict(settings or {}) or None)


def _imported(name: str, reference: str) -> Callable[..., Any]:
    """The function ``module:function`` names: a ConfigError at ``tools.<name>`` when it does
    not import or is not callable."""
    module, _, attribute = reference.partition(":")
    found: Any = None
    why = "is not `module:function`"
    if module and attribute:
        try:
            found = importlib.import_module(module)
            for part in attribute.split("."):
                found = getattr(found, part)
        except (ImportError, AttributeError) as e:
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


@dataclass
class _Session:
    """What one session's model calls answered, in order: hardline's Completions."""

    calls: list[Completion] = field(default_factory=list)

    def response(
        self,
        messages: Sequence[ModelMessage],
        started: float,
        *,
        stop: str | None = None,
        output: Any = None,
        run_id: str | None = None,
        failed: HardlineError | None = None,
    ) -> AgentResponse:
        """The session as an AgentResponse: its reply, transcript and tool calls from the
        messages, its facts from hardline's calls (and the usage a failed call reported)."""
        from hardline import Usage
        from pydantic_ai.messages import ModelResponse, TextPart

        usage = Usage()
        for call in self.calls:
            usage = usage + call.usage
        spent = getattr(failed, "usage", None)
        if spent is not None:
            usage = usage + spent
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
            provider=None if last is None else _PROVIDERS.get(last.backend),
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


#: hardline backends that speak to one provider, by the GenAI conventions' provider name. An
#: OpenAI-compatible backend may front any provider, so it names none.
_PROVIDERS: dict[str, str] = {"anthropic": "anthropic"}

_BACKEND_REASONS: dict[str, RuntimeReason] = {
    "rate_limit": "rate_limited",
    "timeout": "timeout",
    "unavailable": "unavailable",
    "auth": "auth",
    "bad_request": "incapable",
}


def _reason(error: HardlineError) -> RuntimeReason:
    """hardline's classification, in the runtime port's vocabulary."""
    from hardline import BackendError, ConfigError, SecretError, UnknownModelError

    if isinstance(error, BackendError):
        return _BACKEND_REASONS.get(error.reason, "failed")
    if isinstance(error, SecretError):
        return "auth"
    if isinstance(error, UnknownModelError | ConfigError):  # what check() would have refused
        return "incapable"
    return "failed"


#: hardline's finish reasons in pydantic-ai's vocabulary. A refusal is the provider withholding
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
    [Hardline, str | None, _Session, dict[str, Any] | None], _HardlineModel
]:
    """pydantic-ai's ``Model`` backed by a hardline runtime and one registry row: defined when
    a session first needs it, so importing this module imports no pydantic-ai."""
    from hardline import Message, ToolCall, ToolSpec
    from pydantic_ai.exceptions import UserError
    from pydantic_ai.messages import (
        ModelRequest,
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

    class HardlineModel(Model):
        """One hardline registry row as a pydantic-ai model. Each response it gives is one
        hardline call; the call's Completion is kept on ``session``."""

        def __init__(
            self,
            models: Hardline,
            row: str | None,
            session: _Session,
            settings: dict[str, Any] | None,
        ) -> None:
            super().__init__(settings=cast("ModelSettings | None", settings))
            self._hardline = models
            self._row = models.registry.get(row).name
            self.session = session

        async def aclose(self) -> None:
            """Close the connections this model's calls opened in the running event loop."""
            await self._hardline.aclose()

        @property
        def model_name(self) -> str:
            return self._row

        @property
        def system(self) -> str:
            return self._hardline.registry.get(self._row).backend

        async def request(
            self,
            messages: list[ModelMessage],
            model_settings: ModelSettings | None,
            model_request_parameters: ModelRequestParameters,
        ) -> ModelResponse:
            settings, params = self.prepare_request(model_settings, model_request_parameters)
            if params.native_tools:
                raise UserError(
                    "a hardline model offers function tools only, not a provider's native tools"
                )
            instructions = self._get_instruction_parts(messages, params) or []
            turns: list[Turn] = [Message(role="system", content=i.content) for i in instructions]
            for message in messages:
                turns.extend(_turns(message))
            tools = [
                ToolSpec(
                    name=tool.name,
                    description=tool.description or "",
                    parameters=dict(tool.parameters_json_schema),
                )
                for tool in (*params.declared_function_tools, *params.output_tools)
            ]
            given = settings or {}
            completion = await self._hardline.complete(
                self._row,
                turns,
                max_tokens=given.get("max_tokens"),
                temperature=given.get("temperature"),
                tools=tools,
            )
            self.session.calls.append(completion)
            parts: list[Any] = [TextPart(completion.text)] if completion.text else []
            parts.extend(
                ToolCallPart(call.name, call.arguments, tool_call_id=call.id)
                for call in completion.tool_calls
            )
            usage = completion.usage
            return ModelResponse(
                parts=parts,
                usage=RequestUsage(
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                    cache_write_tokens=usage.cache_write_tokens,
                ),
                model_name=completion.model,
                provider_name=completion.backend,
                provider_response_id=completion.request_id,
                finish_reason=cast("Any", _FINISH.get(completion.finish_reason or "")),
            )

    def _turns(message: ModelMessage) -> list[Turn]:
        """One pydantic-ai message as hardline turns."""
        if isinstance(message, ModelResponse):
            text = "".join(p.content for p in message.parts if isinstance(p, TextPart))
            calls = tuple(
                ToolCall(id=p.tool_call_id, name=p.tool_name, arguments=_arguments(p))
                for p in message.parts
                if isinstance(p, ToolCallPart)
            )
            return [Message(role="assistant", content=text, tool_calls=calls)]
        assert isinstance(message, ModelRequest)
        turns: list[Turn] = []
        for part in message.parts:
            if isinstance(part, SystemPromptPart):
                turns.append(Message(role="system", content=part.content))
            elif isinstance(part, UserPromptPart):
                if not isinstance(part.content, str):
                    raise UserError("a hardline model takes text prompts only")
                turns.append(Message(role="user", content=part.content))
            elif isinstance(part, ToolReturnPart):
                turns.append(
                    Message(
                        role="tool",
                        tool_call_id=part.tool_call_id,
                        content=part.model_response_str(),
                        is_error=part.outcome != "success",
                    )
                )
            elif isinstance(part, RetryPromptPart):
                if part.tool_name is None:
                    turns.append(Message(role="user", content=part.model_response()))
                else:
                    turns.append(
                        Message(
                            role="tool",
                            tool_call_id=part.tool_call_id,
                            content=part.model_response(),
                            is_error=True,
                        )
                    )
            else:
                raise UserError(f"a hardline model cannot send a {type(part).__name__}")
        return turns

    return cast(
        "Callable[[Hardline, str | None, _Session, dict[str, Any] | None], _HardlineModel]",
        HardlineModel,
    )


def _arguments(call: ToolCallPart) -> str:
    """A call's arguments as the JSON text hardline carries: as the model wrote them, when it
    wrote text."""
    if call.args is None:
        return "{}"
    return call.args if isinstance(call.args, str) else call.args_as_json_str()


if TYPE_CHECKING:

    class _HardlineModel(Model):
        session: _Session

        async def aclose(self) -> None: ...

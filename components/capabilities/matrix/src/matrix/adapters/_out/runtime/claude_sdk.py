"""ClaudeSdkRuntime — the Claude Agent SDK as an AgentRuntime.

Deployment config (permission mode, working directory, which settings load, which plugins
mount) lives on the runtime. What the agent *is* — its prompt, tools, model, turn budget —
arrives per call as an :class:`AgentDefinition`. One runtime serves every definition bound
to it.

What a session can be given is declared by :class:`ClaudeSdkRuntimeConfig` and nowhere else
(``matrix describe`` prints it), so an option outside it fails at its key when the runtime is
composed. ``check`` refuses, at binding, what a session could not play: skills an agent's
tools leave no way to use, and an option the installed SDK's own dataclasses do not have.
Inline subagents reach the SDK as its ``AgentDefinition``.

An agent's ``model`` is passed to the SDK as it stands: an alias the Claude Code CLI knows
(``haiku``, ``sonnet``, ``opus``) or a full model id (the SDK's own examples:
``claude-sonnet-4-5``, ``claude-opus-4-5``); unset, the CLI's default model.
``max_budget_usd`` is the SDK's own option of that name, passed to it as it stands: the SDK
ends a session once it has spent that, and the response says so (``stop: max_budget_usd``).
matrix counts nothing against it. ``cost_usd`` on a response is the SDK's own report
(``total_cost_usd``), kept as a fact of the session.

**What a response carries.** The reply (``content``) is the session's result
(``ResultMessage.result``); every text block the agent wrote along the way is its
``transcript``, so a multi-turn agent's final JSON parses on its own. ``model`` is the model
that answered: the one the SDK's ``model_usage`` names, or, when it names several (the CLI
makes small calls of its own), the one that wrote the final reply. ``tokens_input`` counts
every input token, those read from and written to the prompt cache included, which
Anthropic's ``input_tokens`` leaves out; ``cache_read_tokens`` and ``cache_write_tokens`` say
how many. The session id and the SDK's ``stop_reason`` (as ``native_finish_reason``, and
mapped to ``finish_reason``) are kept.

**The CLI joins the run's trace.** Given ``trace_environment`` (composition gives it one that
reads OpenTelemetry), each session hands the ``claude`` CLI it starts the trace context
current when the session starts (``TRACEPARENT``, ``TRACESTATE``) through the SDK's ``env``
option, so the spans the CLI exports are children of the session's ``invoke_agent`` span. With
no span current it hands nothing. The SDK lets an explicit ``env`` win over its own injection,
so this does not depend on which SDK version does that.

Type URL: ``matrix.v1.agent-runtime.claude-sdk``. Requires the ``claude`` extra.
"""

from __future__ import annotations

import dataclasses
import logging
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from matrix.domain.agent import AgentCapabilities, AgentResponse
from matrix.domain.errors import AgentRuntimeError, ConfigError, RuntimeReason, missing_extra
from matrix.domain.ports._out.agent_runtime import RUNTIME_LOGGER
from matrix.domain.problems import Problem, problem

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping

    from matrix.domain.agent import AgentDefinition
    from matrix.domain.model_call import FinishReason

logger = logging.getLogger(__name__)

#: The SDK's permission modes, validated at config time so a typo fails naming the legal set.
PermissionMode = Literal["default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"]

#: Modes that grant tool access without per-call confirmation. Selecting one is logged at
#: WARNING on :data:`RUNTIME_LOGGER` so a permissive run is visible in the output, not only in
#: the config: once per process for each mode, since a caller may build a runtime per session.
_PERMISSIVE_MODES = frozenset({"bypassPermissions", "dontAsk"})
#: The permissive modes already logged in this process.
_LOGGED: set[str] = set()

#: The tool through which a session uses a skill.
SKILL_TOOL = "Skill"

FAMILY = "claude"
#: ``gen_ai.provider.name``: the provider the SDK's sessions are served by.
PROVIDER = "anthropic"


#: Where a session may read settings from (the SDK's ``setting_sources``).
SettingSource = Literal["user", "project", "local"]


class PluginConfig(BaseModel):
    """One plugin a session mounts: a local directory (the only kind the SDK loads)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["local"] = "local"
    #: Relative to the runtime's ``cwd`` when it has one.
    path: str = Field(min_length=1)


class SubagentConfig(BaseModel):
    """One inline subagent the session may hand work to: the SDK's ``AgentDefinition``."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    description: str
    prompt: str
    #: ``None``: the session's tools. ``[]``: none.
    tools: list[str] | None = None
    disallowed_tools: list[str] | None = Field(
        default=None, validation_alias=AliasChoices("disallowed_tools", "disallowedTools")
    )
    #: An alias (``haiku``, ``sonnet``, ``opus``, ``inherit``) or a full model id.
    model: str | None = None
    skills: list[str] | None = None
    max_turns: int | None = Field(
        default=None, ge=1, validation_alias=AliasChoices("max_turns", "maxTurns")
    )

    def sdk_fields(self) -> dict[str, Any]:
        """The SDK's ``AgentDefinition`` keyword arguments, unset ones left out."""
        named = {"disallowed_tools": "disallowedTools", "max_turns": "maxTurns"}
        return {
            named.get(key, key): value
            for key, value in self.model_dump().items()
            if value is not None
        }


class ClaudeSdkRuntimeConfig(BaseModel):
    """Claude Agent SDK sessions (a config that reaches this is a capability grant: see the
    package's SECURITY.md). What a session can be given is declared here and nowhere else:
    `permission_mode`, `setting_sources`, `plugins`, `skills` and inline subagents (`agents`);
    an agent's own tools, turns and model come from its definition. An agent's `model` on this
    runtime is an alias the Claude Code CLI knows (`haiku`, `sonnet`, `opus`) or a full model
    id (as `claude-sonnet-4-5`); unset, the CLI's default. `max_budget_usd` is the SDK's own
    option, passed as it stands: the SDK ends a session once it has spent that many USD
    (`stop: max_budget_usd`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: SAFE BY DEFAULT. Unattended harnesses may need ``bypassPermissions`` but must ask for
    #: it, so the choice is in the config and the run log, never inherited from a constant.
    permission_mode: PermissionMode = "default"
    cwd: str | None = None
    #: ``[]`` = hermetic: no ambient ~/.claude or project settings leak into the session —
    #: what makes a routing measurement reproducible. ``None`` = the SDK's default.
    setting_sources: list[SettingSource] | None = None
    #: Plugins to mount, e.g. ``{type: local, path: ../../plugins/recon}``. A relative
    #: ``path`` is resolved against ``cwd`` here, so the session never depends on the
    #: process's working directory.
    plugins: list[PluginConfig] = []
    #: The skills a session may use (the SDK's ``skills``): names, or ``all``. ``None``: the
    #: CLI's own default; ``[]``: none listed. The SDK turns on the ``Skill`` tool for them, so
    #: an agent whose ``tools`` leave ``Skill`` out is refused.
    skills: list[str] | Literal["all"] | None = None
    #: A model alias or id the SDK falls back to when the agent's model is unavailable.
    fallback_model: str | None = None
    #: The SDK's ``max_budget_usd``, passed as it stands: the SDK ends a session once it has
    #: spent that many USD. ``None``: not passed, so the SDK's default applies.
    max_budget_usd: float | None = Field(default=None, gt=0)
    #: Inline subagents by name, each the SDK's ``AgentDefinition``.
    agents: dict[str, SubagentConfig] | None = None

    def capabilities(self) -> AgentCapabilities:
        """What a session does under this config. ``setting_sources`` decides which skills
        it loads: from settings when it lists any source, from its working directory's
        ``.claude/skills`` when it lists ``project``; unset, the CLI's own default decides,
        which is not claimed. ``permission_mode: plan`` writes nothing. ``setting_sources:
        []`` keeps this machine's settings, CLAUDE.md and hooks out."""
        sources = self.setting_sources
        return AgentCapabilities(
            settings_skills=None if sources is None else bool(sources),
            project_skills=None if sources is None else "project" in sources,
            writes_files=False if self.permission_mode == "plan" else None,
            hermetic={"setting_sources": []},
            set_by={
                "settings_skills": "setting_sources",
                "project_skills": "setting_sources",
                "writes_files": "permission_mode",
            },
        )


class ClaudeSdkRuntime:
    """Runs AgentDefinitions as Claude Agent SDK sessions. ``trace_environment`` returns the
    environment variables that carry the current trace context to a child process (see the
    module docstring); without it the CLI is handed none."""

    def __init__(
        self,
        config: ClaudeSdkRuntimeConfig | None = None,
        *,
        trace_environment: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError as e:
            raise missing_extra(
                "claude", needed_by="the claude-sdk runtime", module="claude_agent_sdk"
            ) from e
        self._config = config or ClaudeSdkRuntimeConfig()
        self._trace_environment = trace_environment
        mode = self._config.permission_mode
        if mode in _PERMISSIVE_MODES and mode not in _LOGGED:
            _LOGGED.add(mode)
            logging.getLogger(RUNTIME_LOGGER).warning(
                "claude-sdk runtime with permission_mode=%r — agents may use tools without "
                "per-call confirmation. This was requested explicitly.",
                mode,
            )

    @property
    def config(self) -> ClaudeSdkRuntimeConfig:
        return self._config

    @property
    def warnings(self) -> tuple[Problem, ...]:
        """What a reader of this runtime's config should see: a permissive mode, which
        composition carries into every document the ``matrix`` command prints."""
        mode = self._config.permission_mode
        if mode not in _PERMISSIVE_MODES:
            return ()
        return (
            problem(
                "permission_mode",
                "permissive",
                f"permission_mode {mode!r}: agents may use tools without per-call confirmation",
                fix="keep it only for an unattended run in a sandbox; `default` asks per call",
                got=mode,
            ),
        )

    def check(self, definition: AgentDefinition) -> None:
        """Refuse, at binding, what a session could not play: skills an agent's tools leave no
        way to use, and an option the installed SDK does not have."""
        skills = self._config.skills
        tools = definition.tools
        if skills and tools is not None and SKILL_TOOL not in tools:
            raise ConfigError(
                f"agent {definition.name!r} may call {', '.join(tools) or 'no tool'}, and the "
                f"runtime gives it skills, which run only through the {SKILL_TOOL} tool",
                fix=f"add {SKILL_TOOL} to the agent's tools, or drop the runtime's skills",
            )
        from claude_agent_sdk import AgentDefinition as SdkAgent
        from claude_agent_sdk import ClaudeAgentOptions

        options = self.options(definition)
        lacking = sorted(set(options) - _fields(ClaudeAgentOptions))
        for name, subagent in (self._config.agents or {}).items():
            lacking += [
                f"agents.{name}.{key}"
                for key in sorted(set(subagent.sdk_fields()) - _fields(SdkAgent))
            ]
        if lacking:
            raise ConfigError(
                f"the installed claude-agent-sdk ({_sdk_version()}) has no {', '.join(lacking)}",
                fix="upgrade it (`uv lock --upgrade-package claude-agent-sdk`) or drop "
                + ("that option" if len(lacking) == 1 else "those options"),
            )

    def _plugins(self) -> list[dict[str, Any]]:
        resolved = []
        for plugin in self._config.plugins:
            entry = plugin.model_dump()
            path = plugin.path
            if self._config.cwd and not Path(path).is_absolute():
                entry["path"] = str((Path(self._config.cwd) / path).resolve())
            resolved.append(entry)
        return resolved

    def options(self, definition: AgentDefinition) -> dict[str, Any]:
        """The ClaudeAgentOptions keyword arguments for one definition, each inline subagent as
        the keyword arguments of the SDK's ``AgentDefinition``. Pure."""
        opts: dict[str, Any] = {
            "system_prompt": definition.system_prompt or None,
            "plugins": self._plugins(),
            "permission_mode": self._config.permission_mode,
            "cwd": self._config.cwd,
        }
        # None → the SDK's default toolset. An empty tuple → no tools: passed as [], which the
        # SDK documents as "disable all built-in tools". Never collapse one into the other.
        if definition.tools is not None:
            opts["tools"] = list(definition.tools)
        if definition.max_turns is not None:
            opts["max_turns"] = definition.max_turns
        if definition.model is not None:
            opts["model"] = definition.model
        if self._config.fallback_model is not None:
            opts["fallback_model"] = self._config.fallback_model
        if self._config.max_budget_usd is not None:
            opts["max_budget_usd"] = self._config.max_budget_usd
        if self._config.setting_sources is not None:
            opts["setting_sources"] = list(self._config.setting_sources)
        if self._config.skills is not None:
            skills = self._config.skills
            opts["skills"] = skills if isinstance(skills, str) else list(skills)
        if self._config.agents is not None:
            opts["agents"] = {
                name: subagent.sdk_fields() for name, subagent in self._config.agents.items()
            }
        return opts

    def sdk_options(self, definition: AgentDefinition) -> Any:
        """The SDK's ``ClaudeAgentOptions`` for one definition: :meth:`options`, with each
        inline subagent as the SDK's ``AgentDefinition``."""
        from claude_agent_sdk import AgentDefinition as SdkAgent
        from claude_agent_sdk import ClaudeAgentOptions

        opts = self.options(definition)
        if "agents" in opts:
            opts["agents"] = {name: SdkAgent(**fields) for name, fields in opts["agents"].items()}
        return ClaudeAgentOptions(**opts)

    async def run(self, definition: AgentDefinition, task: str) -> AgentResponse:
        """One SDK session. Streams messages; captures text, tool calls, usage and cost.

        An error from the stream once the result message has arrived is logged and the
        result kept. Any other failure is an AgentRuntimeError with the SDK's exception as
        its cause. Nothing here touches process state, so sessions may run concurrently.
        """
        from claude_agent_sdk import query

        options = self.sdk_options(definition)
        carried = self._trace_environment() if self._trace_environment is not None else {}
        if carried:  # the CLI's spans join the trace of the span current here, the session's
            options = dataclasses.replace(options, env={**options.env, **carried})
        # Closed here however reading ends, not by the garbage collector: closing the stream
        # is what ends the CLI process. The SDK types it as an iterator; it is a generator.
        stream = cast("AsyncGenerator[Any, None]", query(prompt=task, options=options))
        async with aclosing(stream) as messages:
            transcript = await _collect(messages, definition.name)
        return _response(definition, transcript)


@dataclass(frozen=True)
class _Transcript:
    """What one session said: its text blocks, the tools it called, the model that wrote its
    last reply, and its result message."""

    texts: tuple[str, ...]
    tool_calls: tuple[dict[str, Any], ...]
    last_model: str | None
    result: Any


async def _collect(messages: AsyncIterator[Any], agent: str) -> _Transcript:
    """Read a session's message stream to its result. Raises :class:`AgentRuntimeError` when
    the stream fails before a result arrives, or ends without one.

    Each tool call is timed as the stream reports it: from the message that asked for it to
    the one that carried its result (``started``, ``ended``, ``error``), when one did.
    """
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

    texts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    last_model: str | None = None
    result = None
    try:
        async for message in messages:
            now = datetime.now(UTC).isoformat()
            if isinstance(message, AssistantMessage):
                if getattr(message, "parent_tool_use_id", None) is None:  # not a subagent's
                    last_model = message.model or last_model
                for block in message.content:
                    if isinstance(block, TextBlock):
                        texts.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        call: dict[str, Any] = {"name": block.name, "input": block.input}
                        call_id = getattr(block, "id", None)
                        if isinstance(call_id, str):
                            call.update(id=call_id, started=now)
                            by_id[call_id] = call
                        tool_calls.append(call)
            elif isinstance(message, ResultMessage):
                result = message
            else:  # a user message carries the results of the tools the assistant called
                content = getattr(message, "content", None)
                for block in content if isinstance(content, list) else ():
                    answered = by_id.get(str(getattr(block, "tool_use_id", "")))
                    if answered is not None:
                        answered.update(ended=now, error=bool(getattr(block, "is_error", False)))
    except Exception as e:
        if result is None:
            raise _stream_error(e, agent) from e
        logger.debug("SDK cleanup error (non-fatal, result received): %s", e)
    if result is None:
        raise AgentRuntimeError(
            f"agent {agent!r}: the session ended without a result message",
            reason="failed",
            agent=agent,
        )
    return _Transcript(
        texts=tuple(texts), tool_calls=tuple(tool_calls), last_model=last_model, result=result
    )


def _fields(cls: type) -> set[str]:
    """The fields of one of the SDK's dataclasses: what the installed SDK accepts."""
    return {field.name for field in dataclasses.fields(cls)}


def _sdk_version() -> str:
    try:
        return version("claude-agent-sdk")
    except PackageNotFoundError:
        return "version unknown"


def _stream_error(error: Exception, agent: str) -> AgentRuntimeError:
    if type(error).__name__ == "CLINotFoundError":
        return AgentRuntimeError(
            f"agent {agent!r}: Claude Code CLI not found",
            reason="incapable",
            fix="install it: npm install -g @anthropic-ai/claude-code",
            agent=agent,
        )
    return AgentRuntimeError(
        f"agent {agent!r}: Claude SDK error: {type(error).__name__}: {error}",
        reason="timeout" if isinstance(error, TimeoutError) else "failed",
        agent=agent,
    )


def _response(definition: AgentDefinition, transcript: _Transcript) -> AgentResponse:
    """The response of a session that has a result. Raises :class:`AgentRuntimeError` when
    the result says the session failed, carrying what the result says of it (``response``):
    its id, usage, cost, turns and the model that answered."""
    result = transcript.result
    stop = _stop(result)
    response = _facts(transcript, stop)
    if stop is None:
        detail = "; ".join(getattr(result, "errors", None) or []) or "no detail"
        status = getattr(result, "api_error_status", None)
        raise AgentRuntimeError(
            f"agent {definition.name!r}: session failed ({result.subtype}"
            + (f", HTTP {status}" if status else "")
            + f"): {detail}",
            reason=_http_reason(status),
            response=response,
            agent=definition.name,
            status=status,
        )
    return response


def _facts(transcript: _Transcript, stop: str | None) -> AgentResponse:
    """What a session's result message says, whether the session ended or failed."""
    result = transcript.result
    usage = getattr(result, "usage", None)
    usage = usage if isinstance(usage, dict) else {}
    cache_read = int(usage.get("cache_read_input_tokens") or 0)
    cache_write = int(usage.get("cache_creation_input_tokens") or 0)
    native = getattr(result, "stop_reason", None)
    reply = getattr(result, "result", None)
    return AgentResponse(
        content=reply if isinstance(reply, str) else "",
        transcript=transcript.texts,
        tool_calls=transcript.tool_calls,
        duration_ms=getattr(result, "duration_ms", 0) or 0,
        cost_usd=getattr(result, "total_cost_usd", None),
        tokens_input=int(usage.get("input_tokens") or 0) + cache_read + cache_write,
        tokens_output=int(usage.get("output_tokens") or 0),
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        num_turns=getattr(result, "num_turns", 0) or 0,
        family=FAMILY,
        provider=PROVIDER,
        model=_answered(getattr(result, "model_usage", None), transcript.last_model),
        stop=stop,
        finish_reason=None if native is None else _FINISH.get(native, "other"),
        native_finish_reason=native,
        session_id=getattr(result, "session_id", None),
    )


def _answered(model_usage: Any, last: str | None) -> str | None:
    """The model that answered: the one ``model_usage`` names, or, when it names several,
    the one that wrote the final reply."""
    named = list(model_usage) if isinstance(model_usage, dict) else []
    return named[0] if len(named) == 1 else last


#: The Messages API's stop reasons, which a result's ``stop_reason`` carries, in the model
#: call's vocabulary. A reason not named here is ``other``.
_FINISH: dict[str, FinishReason] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "refusal": "refusal",
    "tool_use": "tool_call",
}


#: Result subtypes that mean "a configured limit was reached" — an ending, not a failure.
_LIMITS = {
    "error_max_turns": "max_turns",
    "error_max_budget_usd": "max_budget_usd",
    "error_max_structured_output_retries": "max_structured_output_retries",
}


def _stop(result: Any) -> str | None:
    """Why the session ended, or None if it failed (execution or API error)."""
    if result.subtype in _LIMITS:
        return _LIMITS[result.subtype]
    if result.is_error:
        return None
    return "completed"


def _http_reason(status: Any) -> RuntimeReason:
    """A failed session's HTTP status, in the runtime port's vocabulary."""
    if not isinstance(status, int):
        return "failed"
    if status == 429:
        return "rate_limited"
    if status in (401, 403):
        return "auth"
    if status in (408, 504):
        return "timeout"
    if status >= 500:
        return "unavailable"
    return "failed"

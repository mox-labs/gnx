"""ClaudeSdkRuntime — the Claude Agent SDK as an AgentRuntime.

Deployment config (permission mode, working directory, which settings load, which plugins
mount) lives on the runtime. What the agent *is* — its prompt, tools, model, turn budget —
arrives per call as an :class:`AgentDefinition`. One runtime serves every definition bound
to it.

An agent's ``model`` is passed to the SDK as it stands: an alias the Claude Code CLI knows
(``haiku``, ``sonnet``, ``opus``) or a full model id (the SDK's own examples:
``claude-sonnet-4-5``, ``claude-opus-4-5``); unset, the CLI's default model.
``max_budget_usd`` bounds what one session may spend: the SDK's own option of that name,
which ends the session with ``stop: max_budget_usd`` once it is passed, and the per-session
bound a run's spend ceiling (``matrix.limits.spend_usd``) holds for each session.

Type URL: ``matrix.v1.runtime.claude-sdk``. Requires the ``claude`` extra.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from matrix.domain.agent import AgentResponse
from matrix.domain.errors import AgentRuntimeError, RuntimeReason, missing_extra
from matrix.domain.ports._out.agent_runtime import RUNTIME_LOGGER
from matrix.domain.problems import Problem, problem

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from matrix.domain.agent import AgentDefinition

logger = logging.getLogger(__name__)

#: The SDK's permission modes, validated at config time so a typo fails naming the legal set.
PermissionMode = Literal["default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"]

#: Modes that grant tool access without per-call confirmation. Selecting one is logged at
#: WARNING on :data:`RUNTIME_LOGGER` so a permissive run is visible in the output, not only in
#: the config: once per process for each mode, since a caller may build a runtime per session.
_PERMISSIVE_MODES = frozenset({"bypassPermissions", "dontAsk"})
#: The permissive modes already logged in this process.
_LOGGED: set[str] = set()

FAMILY = "claude"
#: ``gen_ai.provider.name``: the provider the SDK's sessions are served by.
PROVIDER = "anthropic"


class ClaudeSdkRuntimeConfig(BaseModel):
    """Claude Agent SDK sessions (a config that reaches this is a capability grant: see the
    package's SECURITY.md). An agent's `model` on this runtime is an alias the Claude Code CLI
    knows (`haiku`, `sonnet`, `opus`) or a full model id (as `claude-sonnet-4-5`); unset, the
    CLI's default. `max_budget_usd` is the most one session may spend, in USD: the SDK ends
    the session there (`stop: max_budget_usd`), and `matrix.limits.spend_usd` counts it as
    each session's bound."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: SAFE BY DEFAULT. Unattended harnesses may need ``bypassPermissions`` but must ask for
    #: it, so the choice is in the config and the run log, never inherited from a constant.
    permission_mode: PermissionMode = "default"
    cwd: str | None = None
    #: ``[]`` = hermetic: no ambient ~/.claude or project settings leak into the session —
    #: what makes a routing measurement reproducible. ``None`` = the SDK's default.
    setting_sources: list[str] | None = None
    #: SDK plugin configs, e.g. ``{type: local, path: ../../plugins/recon}``. A relative
    #: ``path`` is resolved against ``cwd`` here, so the session never depends on the
    #: process's working directory.
    plugins: list[dict[str, Any]] = []
    #: A model alias or id the SDK falls back to when the agent's model is unavailable.
    fallback_model: str | None = None
    #: The most one session may spend, in USD: the SDK's ``max_budget_usd``. ``None``: no
    #: bound per session, and a run's spend ceiling cannot hold a session to one.
    max_budget_usd: float | None = Field(default=None, gt=0)
    #: Inline subagents, forwarded as the SDK's ``agents`` option.
    agents: dict[str, Any] | None = None


class ClaudeSdkRuntime:
    """Runs AgentDefinitions as Claude Agent SDK sessions."""

    def __init__(self, config: ClaudeSdkRuntimeConfig | None = None) -> None:
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError as e:
            raise missing_extra(
                "claude", needed_by="the claude-sdk runtime", module="claude_agent_sdk"
            ) from e
        self._config = config or ClaudeSdkRuntimeConfig()
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
    def budget_usd(self) -> float | None:
        """The most one session may spend, in USD (``max_budget_usd``), or None."""
        return self._config.max_budget_usd

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
        """The SDK honours every field of a definition; nothing to refuse."""

    def _plugins(self) -> list[dict[str, Any]]:
        resolved = []
        for plugin in self._config.plugins:
            entry = dict(plugin)
            path = entry.get("path")
            if isinstance(path, str) and self._config.cwd and not Path(path).is_absolute():
                entry["path"] = str((Path(self._config.cwd) / path).resolve())
            resolved.append(entry)
        return resolved

    def options(self, definition: AgentDefinition) -> dict[str, Any]:
        """The ClaudeAgentOptions keyword arguments for one definition. Pure."""
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
            opts["setting_sources"] = self._config.setting_sources
        if self._config.agents is not None:
            opts["agents"] = self._config.agents
        return opts

    async def run(self, definition: AgentDefinition, task: str) -> AgentResponse:
        """One SDK session. Streams messages; captures text, tool calls, usage and cost.

        An error from the stream once the result message has arrived is logged and the
        result kept. Any other failure is an AgentRuntimeError with the SDK's exception as
        its cause. Nothing here touches process state, so sessions may run concurrently.
        """
        from claude_agent_sdk import ClaudeAgentOptions, query

        options = ClaudeAgentOptions(**self.options(definition))
        transcript = await _collect(query(prompt=task, options=options), definition.name)
        return _response(definition, transcript)


@dataclass(frozen=True)
class _Transcript:
    """What one session said: its text, the tools it called, and its result message."""

    content: str
    tool_calls: tuple[dict[str, Any], ...]
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
    result = None
    try:
        async for message in messages:
            now = datetime.now(UTC).isoformat()
            if isinstance(message, AssistantMessage):
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
    return _Transcript(content="".join(texts), tool_calls=tuple(tool_calls), result=result)


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
    the result says the session failed."""
    result = transcript.result
    stop = _stop(result)
    if stop is None:
        detail = "; ".join(getattr(result, "errors", None) or []) or "no detail"
        status = getattr(result, "api_error_status", None)
        raise AgentRuntimeError(
            f"agent {definition.name!r}: session failed ({result.subtype}"
            + (f", HTTP {status}" if status else "")
            + f"): {detail}",
            reason=_http_reason(status),
            agent=definition.name,
            status=status,
        )
    usage = getattr(result, "usage", None)
    return AgentResponse(
        content=transcript.content,
        tool_calls=transcript.tool_calls,
        duration_ms=getattr(result, "duration_ms", 0) or 0,
        cost_usd=getattr(result, "total_cost_usd", None),
        tokens_input=usage.get("input_tokens", 0) if isinstance(usage, dict) else 0,
        tokens_output=usage.get("output_tokens", 0) if isinstance(usage, dict) else 0,
        num_turns=getattr(result, "num_turns", 0) or 0,
        family=FAMILY,
        provider=PROVIDER,
        model=definition.model,
        stop=stop,
    )


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

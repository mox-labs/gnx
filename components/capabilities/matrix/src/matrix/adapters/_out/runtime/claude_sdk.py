"""ClaudeSdkRuntime — the Claude Agent SDK as an AgentRuntime.

Deployment config (permission mode, working directory, which settings load, which plugins
mount) lives on the runtime. What the agent *is* — its prompt, tools, model, turn budget —
arrives per call as an :class:`AgentDefinition`. One runtime serves every definition bound
to it.

Type URL: ``matrix.v1/runtime.claude-sdk``. Requires the ``claude`` extra.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

from matrix.domain.errors import AgentRuntimeError
from matrix.domain.types import AgentResponse

if TYPE_CHECKING:
    from matrix.domain.agent import AgentDefinition

logger = logging.getLogger(__name__)

#: The SDK's permission modes, validated at config time so a typo fails naming the legal set.
PermissionMode = Literal["default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"]

#: Modes that grant tool access without per-call confirmation. Selecting one is logged at
#: WARNING so a permissive run is visible in the output, not only in the config.
_PERMISSIVE_MODES = frozenset({"bypassPermissions", "dontAsk"})

FAMILY = "claude"


class ClaudeSdkRuntimeConfig(BaseModel):
    """Deployment settings. A config that reaches this is a capability grant — see SECURITY.md."""

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
    fallback_model: str | None = None
    #: Inline subagents, forwarded as the SDK's ``agents`` option.
    agents: dict[str, Any] | None = None


class ClaudeSdkRuntime:
    """Runs AgentDefinitions as Claude Agent SDK sessions."""

    def __init__(self, config: ClaudeSdkRuntimeConfig | None = None) -> None:
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "the claude-sdk runtime requires 'claude-agent-sdk'. "
                "Install with: uv add 'matrix[claude]'"
            ) from e
        self._config = config or ClaudeSdkRuntimeConfig()
        if self._config.permission_mode in _PERMISSIVE_MODES:
            logger.warning(
                "claude-sdk runtime with permission_mode=%r — agents may use tools without "
                "per-call confirmation. This was requested explicitly.",
                self._config.permission_mode,
            )

    @property
    def config(self) -> ClaudeSdkRuntimeConfig:
        return self._config

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
        """The ClaudeAgentOptions keyword arguments for one definition. Pure; tested directly."""
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
        if self._config.setting_sources is not None:
            opts["setting_sources"] = self._config.setting_sources
        if self._config.agents is not None:
            opts["agents"] = self._config.agents
        return opts

    async def run(self, definition: AgentDefinition, task: str) -> AgentResponse:
        """One SDK session. Streams messages; captures text, tool calls, usage and cost.

        The SDK may raise during generator cleanup after the ResultMessage has arrived; once
        the result is in hand that error is non-fatal. Any other failure is an
        AgentRuntimeError with the SDK's exception as its cause.

        Concurrency-safe: nothing here touches process state. (The CLI refuses to start under
        a parent Claude Code session; the SDK strips ``CLAUDECODE`` from the child's
        environment itself, so this runtime no longer edits ``os.environ``.)
        """
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            ToolUseBlock,
            query,
        )

        options = ClaudeAgentOptions(**self.options(definition))

        content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        result_msg = None
        try:
            async for message in query(prompt=task, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            content_parts.append(block.text)
                        elif isinstance(block, ToolUseBlock):
                            tool_calls.append({"name": block.name, "input": block.input})
                elif isinstance(message, ResultMessage):
                    result_msg = message
        except Exception as e:
            if result_msg is not None:
                logger.debug("SDK cleanup error (non-fatal, result received): %s", e)
            else:
                if type(e).__name__ == "CLINotFoundError":
                    raise AgentRuntimeError(
                        f"agent {definition.name!r}: Claude Code CLI not found. "
                        "Install: npm install -g @anthropic-ai/claude-code"
                    ) from e
                raise AgentRuntimeError(
                    f"agent {definition.name!r}: Claude SDK error: {type(e).__name__}: {e}"
                ) from e

        if result_msg is None:
            raise AgentRuntimeError(
                f"agent {definition.name!r}: the session ended without a result message"
            )
        stop = _stop(result_msg)
        if stop is None:
            detail = "; ".join(getattr(result_msg, "errors", None) or []) or "no detail"
            status = getattr(result_msg, "api_error_status", None)
            raise AgentRuntimeError(
                f"agent {definition.name!r}: session failed ({result_msg.subtype}"
                + (f", HTTP {status}" if status else "")
                + f"): {detail}"
            )

        usage = getattr(result_msg, "usage", None)
        return AgentResponse(
            content="".join(content_parts),
            tool_calls=tuple(tool_calls),
            duration_ms=getattr(result_msg, "duration_ms", 0) or 0,
            cost_usd=getattr(result_msg, "total_cost_usd", None),
            tokens_input=usage.get("input_tokens", 0) if isinstance(usage, dict) else 0,
            tokens_output=usage.get("output_tokens", 0) if isinstance(usage, dict) else 0,
            num_turns=getattr(result_msg, "num_turns", 0) or 0,
            family=FAMILY,
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

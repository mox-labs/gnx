"""ClaudeAgent — Agent SDK adapter for the Agent protocol.

Uses claude-agent-sdk to provide full agentic capabilities:
tool use, multi-turn reasoning, and multi-agent orchestration.

Matrix's runtime layer IS an Agent SDK application. The DAG scheduler
sequences component execution; each component gets agent capabilities
through this adapter.

Requires: uv add claude-agent-sdk
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Literal, get_args

from matrix.domain.types import AgentResponse

if TYPE_CHECKING:  # the SDK is an optional extra; never a runtime import here
    from claude_agent_sdk import SdkPluginConfig

logger = logging.getLogger(__name__)

# The SDK's permission modes. Typed as a Literal so a typo is a static error rather than a
# string the SDK may silently not recognise — and enumerated at runtime (get_args) so a
# value arriving from a YAML config fails loudly, naming the legal set.
PermissionMode = Literal[
    "default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"
]

#: The mode used when a caller does not choose one.
#:
#: SAFE BY DEFAULT, deliberately. This previously defaulted to "bypassPermissions", and
#: nothing in the tree overrode it — so every agent-backed component ran with permissions
#: bypassed, decided by a constant in this file rather than by anyone's configuration.
#: permission_mode is a governed surface: an unattended harness may legitimately need
#: bypass, but it has to ASK for it, so the choice appears in the config and in the run
#: record instead of being inherited silently.
DEFAULT_PERMISSION_MODE: PermissionMode = "default"

#: Modes that grant the agent tool access without per-call confirmation. Selecting one is
#: logged at WARNING so the decision is visible in the run output, not just the config.
_PERMISSIVE_MODES = frozenset({"bypassPermissions", "dontAsk"})


class ClaudeAgent:
    """Implements Agent using the Claude Agent SDK.

    Each run() launches a Claude agent session. The agent already knows
    its system prompt — callers just provide the task prompt.

    Usage::

        agent = ClaudeAgent(system_prompt="You are an expert evaluator.")
        response = await agent.run("Evaluate this code...")
        # response.content, response.tool_calls, response.tokens_input, etc.
    """

    def __init__(
        self,
        system_prompt: str | None = None,
        max_turns: int = 1,
        allowed_tools: list[str] | None = None,
        plugins: list[SdkPluginConfig] | None = None,
        permission_mode: PermissionMode = DEFAULT_PERMISSION_MODE,
        cwd: str | None = None,
        model: str | None = None,
        fallback_model: str | None = None,
        setting_sources: list[str] | None = None,
        agents: dict[str, Any] | None = None,
    ) -> None:
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "ClaudeAgent requires 'claude-agent-sdk'. Install with: uv add claude-agent-sdk"
            ) from e
        self._system_prompt = system_prompt
        self._max_turns = max_turns
        self._allowed_tools = allowed_tools or []
        self._plugins = plugins or []
        # Validate here, not at the SDK boundary. This value routinely arrives from a
        # config file via ComponentRegistry.create(**config), which does no validation of
        # its own — so an unrecognised mode must fail at construction with the legal set
        # named, rather than reaching the SDK as an unknown string.
        legal = get_args(PermissionMode)
        if permission_mode not in legal:
            raise ValueError(
                f"permission_mode {permission_mode!r} is not a Claude Agent SDK mode. "
                f"Legal values: {', '.join(legal)}"
            )
        if permission_mode in _PERMISSIVE_MODES:
            logger.warning(
                "ClaudeAgent running with permission_mode=%r — the agent may use tools "
                "without per-call confirmation. This was requested explicitly.",
                permission_mode,
            )
        self._permission_mode = permission_mode
        self._cwd = cwd
        # Reproducibility knobs (forwarded to ClaudeAgentOptions when set):
        #   model / fallback_model — pin the model so a run is not at the mercy
        #     of the local CLI default (an eval harness MUST pin this).
        #   setting_sources — control which settings load; [] = hermetic (no
        #     ambient ~/.claude or project plugins leak into the subprocess),
        #     which is what makes an activation/routing measurement reproducible.
        #   agents — define subagents inline (e.g. expose eval subjects as real
        #     agents rather than relying on ambient registration).
        self._model = model
        self._fallback_model = fallback_model
        self._setting_sources = setting_sources
        self._agents = agents

    async def run(self, prompt: str) -> AgentResponse:
        """Execute a prompt via the Claude Agent SDK.

        Streams messages, extracts TextBlock + ToolUseBlock content,
        and captures usage/cost from ResultMessage.

        The SDK may raise a ProcessError during generator cleanup even
        after all messages have been received. If we already got the
        ResultMessage, the query succeeded — treat the error as non-fatal.
        """
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            ToolUseBlock,
            query,
        )

        # Strip CLAUDECODE so the subprocess doesn't refuse to start
        # ("cannot be launched inside another Claude Code session").
        stashed = os.environ.pop("CLAUDECODE", None)

        # Only pass reproducibility knobs when explicitly set, so existing
        # callers that omit them keep the SDK's own defaults.
        _extra: dict[str, Any] = {}
        if self._model is not None:
            _extra["model"] = self._model
        if self._fallback_model is not None:
            _extra["fallback_model"] = self._fallback_model
        if self._setting_sources is not None:
            _extra["setting_sources"] = self._setting_sources
        if self._agents is not None:
            _extra["agents"] = self._agents

        options = ClaudeAgentOptions(
            system_prompt=self._system_prompt,
            max_turns=self._max_turns,
            tools=self._allowed_tools or None,
            plugins=self._plugins,
            permission_mode=self._permission_mode,
            cwd=self._cwd,
            **_extra,
        )

        content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        result_msg = None

        try:
            async for message in query(prompt=prompt, options=options):
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
                # Query completed — subprocess cleanup error is non-fatal.
                logger.debug("SDK cleanup error (non-fatal, result received): %s", e)
            else:
                err_name = type(e).__name__
                if err_name == "CLINotFoundError":
                    raise RuntimeError(
                        "Claude Code CLI not found. "
                        "Install: npm install -g @anthropic-ai/claude-code"
                    ) from e
                raise RuntimeError(f"Claude SDK error: {e}") from e
        finally:
            if stashed is not None:
                os.environ["CLAUDECODE"] = stashed

        usage = result_msg.usage if result_msg and hasattr(result_msg, "usage") else None

        return AgentResponse(
            content="".join(content_parts),
            tool_calls=tuple(tool_calls),
            duration_ms=getattr(result_msg, "duration_ms", 0) or 0,
            cost_usd=getattr(result_msg, "total_cost_usd", None),
            tokens_input=usage.get("input_tokens", 0) if isinstance(usage, dict) else 0,
            tokens_output=usage.get("output_tokens", 0) if isinstance(usage, dict) else 0,
            num_turns=getattr(result_msg, "num_turns", 0) or 0,
        )

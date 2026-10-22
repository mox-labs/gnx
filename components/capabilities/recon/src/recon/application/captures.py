"""``recon capture``: one Claude Code ``PostToolUse`` hook payload into a capture log."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from recon.domain.errors import ConfigError

if TYPE_CHECKING:
    from recon.domain.capture import CaptureLog
    from recon.domain.documents import Capture

#: The hook payload fields recon relies on (code.claude.com/docs/en/hooks, PostToolUse).
HOOK_REQUIRED_FIELDS = ("tool_name", "tool_response")


def record_hook_payload(log: CaptureLog, payload: Any) -> Capture:
    """Append one Claude Code ``PostToolUse`` hook payload to ``log`` as an external capture.

    - ``collector`` is the tool name (``mcp__exa__web_search_exa``)
    - ``source`` is the MCP server: ``mcp_server.name`` when present, else the middle of
      an ``mcp__<server>__<tool>`` name, else None
    - ``body`` is ``tool_response``: JSON-encoded unless it is already a string
    - ``request`` is the rest of the payload (``tool_input``, ``tool_use_id``,
      ``session_id``, ...), so the capture records what was asked as well as the answer;
      it is kept as given, a credential the tool took as an argument included

    Raises ConfigError when the payload is not a JSON object carrying the required fields.
    """
    if not isinstance(payload, dict):
        raise ConfigError(f"hook payload must be a JSON object, got {type(payload).__name__}")
    missing = [f for f in HOOK_REQUIRED_FIELDS if f not in payload]
    if missing:
        raise ConfigError(
            f"hook payload lacks {', '.join(missing)}: recon capture reads a PostToolUse "
            "payload (tool_name, tool_input, tool_response)"
        )
    tool_name = str(payload["tool_name"])
    response = payload["tool_response"]
    if isinstance(response, str):
        body, content_type = response.encode(), "text/plain"
    else:
        body = json.dumps(response, ensure_ascii=False, default=str).encode()
        content_type = "application/json"
    return log.record(
        collector=tool_name,
        source=_mcp_server(payload, tool_name),
        kind="external",
        request={k: v for k, v in payload.items() if k != "tool_response"},
        status=None,
        content_type=content_type,
        headers={},
        body=body,
    )


def _mcp_server(payload: dict[str, Any], tool_name: str) -> str | None:
    server = payload.get("mcp_server")
    if isinstance(server, dict) and isinstance(server.get("name"), str):
        return str(server["name"])
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__")
        if len(parts) >= 3 and parts[1]:
            return parts[1]
    return None

"""The capture log port.

A capture is one fetched response kept verbatim: an HTTP body, a command's stdout, or a
result some other process obtained (an MCP tool call, via a Claude Code hook). Captures
are appended to a log, ``captures.jsonl``, one JSON object per line, with each body stored
content-addressed beside it under ``raw/<sha256>``. The line format is an interchange
format other tools write to; it is specified in docs/reference/capture-format.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from pathlib import Path

CAPTURE_SCHEMA = "recon.v1.capture"
CAPTURE_LOG_NAME = "captures.jsonl"

CaptureKind = Literal["http", "cli", "web", "external"]


class CaptureLog(Protocol):
    """Appends captures to one log. Collectors receive one when the mission preserves raw."""

    def record(
        self,
        *,
        collector: str,
        source: str | None,
        kind: CaptureKind,
        request: dict[str, Any],
        status: int | None,
        content_type: str,
        headers: dict[str, str],
        body: bytes,
    ) -> dict[str, Any]:
        """Store ``body`` and append one capture line; return the line as written."""
        ...

    def stream_path(self, collector: str) -> Path:
        """A file the collector may tee a body into as it streams (CLI stdout).

        Call ``finalize_stream`` with the same collector name once writing is done; the
        file is then hashed, moved under ``raw/`` and logged.
        """
        ...

    def finalize_stream(
        self,
        collector: str,
        *,
        source: str | None,
        kind: CaptureKind,
        request: dict[str, Any],
        status: int | None,
        content_type: str,
    ) -> dict[str, Any] | None:
        """Log the streamed body; None when nothing was streamed for ``collector``."""
        ...

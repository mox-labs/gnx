"""Capture logs — writing and reading ``captures.jsonl`` (format: recon.v1.capture).

A capture log is a directory holding::

    captures.jsonl          append-only, one capture per line
    raw/<sha256>            each body, content-addressed (identical bodies stored once)

An archive with ``preserve_raw: true`` is one (``archive/<ts>/``); a mission's external
log, fed by ``recon capture``, is another (``.recon/<mission>/captures/``). The line
format is specified in docs/reference/capture-format.md and is written by tools other
than recon, so the reader here is strict about what it trusts (the body path) and lenient
about what it does not need (unknown fields are ignored).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from recon.domain.capture import CAPTURE_LOG_NAME, CAPTURE_SCHEMA, CaptureKind, CaptureLog
from recon.domain.exceptions import CollectionError, ConfigError

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

#: Response headers whose *value* is a credential rather than metadata.
#:
#: A capture exists to prove what the server said, so the key is kept and only the value
#: is replaced — a reader can still see that the server set a cookie, without the archive
#: carrying a live session token. This matters because an archive is a thing people
#: commit, attach to a ticket, or hand to a colleague.
#:
#: Matched case-insensitively: HTTP header names are case-insensitive and different clients
#: normalise them differently.
SENSITIVE_RESPONSE_HEADERS = frozenset(
    {
        "set-cookie",
        "set-cookie2",
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "x-auth-token",
        "x-amz-security-token",
    }
)

REDACTED = "«redacted by recon»"

_BODY_PATH = re.compile(r"^raw/[0-9a-f]{64}$")
_SCHEMA = re.compile(r"^recon\.v([0-9]+)\.capture$")


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Replace credential-bearing header values, preserving key presence and order."""
    return {
        k: (REDACTED if k.lower() in SENSITIVE_RESPONSE_HEADERS else v) for k, v in headers.items()
    }


def redact_query_params(url: str, names: Iterable[str]) -> str:
    """Replace the values of the named query parameters (an API key sent as ``?api_key=``)."""
    secret = {n for n in names if n}
    if not secret:
        return url
    parts = urlsplit(url)
    if not parts.query:
        return url
    pairs = [
        (k, REDACTED if k in secret else v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit(parts._replace(query=urlencode(pairs)))


def _now() -> str:
    return datetime.now(UTC).isoformat()


class FilesystemCaptureLog:
    """Appends captures under ``root`` (``root/captures.jsonl`` + ``root/raw/``).

    Appends take an exclusive ``flock`` on POSIX and write each line with one ``write``
    call, so concurrent ``recon capture`` processes (parallel tool calls firing hooks)
    never interleave lines.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.log_path = root / CAPTURE_LOG_NAME
        self._streams: dict[str, Path] = {}

    # --- writing ---

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
        sha = hashlib.sha256(body).hexdigest()
        body_path = self._raw_dir() / sha
        if not body_path.exists():
            tmp = body_path.with_name(f".{sha}.{uuid.uuid4().hex}.tmp")
            tmp.write_bytes(body)
            os.replace(tmp, body_path)
        return self._append(
            collector=collector,
            source=source,
            kind=kind,
            request=request,
            status=status,
            content_type=content_type,
            headers=headers,
            sha=sha,
            size=len(body),
        )

    def stream_path(self, collector: str) -> Path:
        path = self._streams.get(collector)
        if path is None:
            path = self._raw_dir() / f".stream-{uuid.uuid4().hex}.tmp"
            self._streams[collector] = path
        return path

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
        path = self._streams.pop(collector, None)
        if path is None:
            return None
        if not path.exists():
            path.write_bytes(b"")  # the command printed nothing: that is still evidence
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        sha = digest.hexdigest()
        size = path.stat().st_size
        target = self._raw_dir() / sha
        if target.exists():
            path.unlink()
        else:
            os.replace(path, target)
        return self._append(
            collector=collector,
            source=source,
            kind=kind,
            request=request,
            status=status,
            content_type=content_type,
            headers={},
            sha=sha,
            size=size,
        )

    def _raw_dir(self) -> Path:
        raw = self.root / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        return raw

    def _append(
        self,
        *,
        collector: str,
        source: str | None,
        kind: CaptureKind,
        request: dict[str, Any],
        status: int | None,
        content_type: str,
        headers: dict[str, str],
        sha: str,
        size: int,
    ) -> dict[str, Any]:
        line = {
            "schema": CAPTURE_SCHEMA,
            "id": uuid.uuid4().hex,
            "source": source,
            "collector": collector,
            "kind": kind,
            "request": request,
            "status": status,
            "content_type": content_type,
            "headers": redact_headers(headers),
            "body": f"raw/{sha}",
            "sha256": sha,
            "bytes": size,
            "captured_at": _now(),
        }
        data = (json.dumps(line, default=str, ensure_ascii=False) + "\n").encode()
        self.root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            _lock(fd)
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        return line


def _lock(fd: int) -> None:
    with contextlib.suppress(ImportError, OSError):
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)  # released by close()


# --- Claude Code hook payloads ---

#: The hook payload fields recon relies on (code.claude.com/docs/en/hooks, PostToolUse).
HOOK_REQUIRED_FIELDS = ("tool_name", "tool_response")


def record_hook_payload(log: CaptureLog, payload: Any) -> dict[str, Any]:
    """Append one Claude Code ``PostToolUse`` hook payload to ``log`` as an external capture.

    - ``collector`` is the tool name (``mcp__exa__web_search_exa``)
    - ``source`` is the MCP server: ``mcp_server.name`` when present, else the middle of
      an ``mcp__<server>__<tool>`` name, else None
    - ``body`` is ``tool_response``: JSON-encoded unless it is already a string
    - ``request`` is the rest of the payload (``tool_input``, ``tool_use_id``,
      ``session_id``, ...), so the capture records what was asked as well as the answer

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


# --- reading ---


def read_captures(log_path: Path) -> Iterator[tuple[dict[str, Any], bytes]]:
    """Yield (capture line, body bytes) for each capture in ``log_path``, in order.

    Raises CollectionError naming the file and line for anything that cannot be trusted:
    unparseable JSON, a schema of another major version, a body path outside ``raw/``,
    a missing body, or a body whose sha256 does not match. Unknown fields are ignored.
    """
    root = log_path.parent
    with open(log_path, encoding="utf-8") as f:
        for number, text in enumerate(f, start=1):
            if not text.strip():
                continue
            where = f"{log_path}:{number}"
            try:
                line = json.loads(text)
            except json.JSONDecodeError as exc:
                raise CollectionError(f"{where}: not JSON ({exc})") from exc
            if not isinstance(line, dict):
                raise CollectionError(f"{where}: a capture must be a JSON object")
            schema = line.get("schema")
            match = _SCHEMA.match(schema) if isinstance(schema, str) else None
            if match is None:
                raise CollectionError(f"{where}: schema {schema!r} is not {CAPTURE_SCHEMA}")
            if match.group(1) != "1":
                msg = f"{where}: schema {schema} is a major version this recon cannot read"
                raise CollectionError(msg)
            body_ref = line.get("body")
            if not isinstance(body_ref, str) or not _BODY_PATH.match(body_ref):
                msg = f"{where}: body must be 'raw/<sha256>', got {body_ref!r}"
                raise CollectionError(msg)
            body_path = root / body_ref
            try:
                body = body_path.read_bytes()
            except OSError as exc:
                raise CollectionError(f"{where}: body {body_ref} unreadable ({exc})") from exc
            actual = hashlib.sha256(body).hexdigest()
            if actual != body_ref.removeprefix("raw/") or actual != line.get("sha256", actual):
                raise CollectionError(f"{where}: body {body_ref} does not match its sha256")
            yield line, body

"""The capture log port.

A capture is one fetched response kept verbatim: an HTTP body, a command's stdout, an MCP
tool's or resource's result, or a result some other process obtained (an MCP tool call, via
a Claude Code hook). Captures
are appended to a log, ``captures.jsonl``, one JSON object per line, with each body stored
content-addressed beside it under ``raw/<sha256>``. The line format is an interchange
format other tools write to; it is specified by `recon --skill -r capture-format`.

A capture proves what a server said, and an archive is something people commit and pass
on, so a credential recon itself sends or receives never reaches the log: the value of a
credential-bearing response header, and of a query parameter that carries the mission's key,
is replaced. What another process hands recon (a hook's ``tool_input``) is kept as given,
credentials included: recon cannot tell one from any other argument (SECURITY.md, boundary 5).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from recon.domain.documents import Capture

CAPTURE_SCHEMA: Final = "recon.v1.capture"
CAPTURE_LOG_NAME = "captures.jsonl"

#: Response headers whose value is a credential rather than metadata. Matched
#: case-insensitively: clients normalise header names differently.
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


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Replace credential-bearing header values, keeping each key and the order."""
    return {
        k: (REDACTED if k.lower() in SENSITIVE_RESPONSE_HEADERS else v) for k, v in headers.items()
    }


#: A request header whose name holds one of these carries a credential (``Authorization``,
#: ``X-Goog-Api-Key``, ``Private-Token``, ``Cookie``). Any other is metadata.
_CREDENTIAL_NAME = re.compile(
    r"auth|key|token|secret|cookie|session|signature|password|credential", re.IGNORECASE
)


#: An auth scheme and the credential after it (``Bearer t0k``, ``Basic dXNl...``).
_SCHEMED = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\s+(\S+)")


def credential_values(headers: Mapping[str, str]) -> list[str]:
    """The credentials among request ``headers`` recon sends, to redact where they stand
    whole (:func:`redact_credentials`): the value of each header whose name says it carries
    one; for a value that is an auth scheme and a credential (``Bearer t0k``), the credential
    alone too; and for a cookie header, each cookie's value. A header whose name does not
    (``X-Org``, ``Accept``) is not one, so its value stays in what is captured."""
    found: list[str] = []
    for name, value in headers.items():
        if not (name.lower() in SENSITIVE_RESPONSE_HEADERS or _CREDENTIAL_NAME.search(name)):
            continue
        found.append(value)
        if "cookie" in name.lower():
            found += [v.strip() for _, _, v in (c.partition("=") for c in value.split(";"))]
        elif schemed := _SCHEMED.fullmatch(value.strip()):
            found.append(schemed.group(1))
    return [value for value in found if value]


def redact_credentials(text: str, credentials: Iterable[str]) -> str:
    """``text`` with each of ``credentials`` replaced where it stands whole: not run into a
    letter, digit, ``_`` or ``-`` on either side. A credential's value is redacted as a JSON
    string, a header's value or a query parameter's, never as a piece of other text (a
    credential ``2`` leaves ``2026`` and ``v2`` as they are)."""
    for secret in sorted(set(credentials), key=len, reverse=True):
        text = re.sub(rf"(?<![\w-]){re.escape(secret)}(?![\w-])", REDACTED, text)
    return text


def redact_query_params(url: str, names: Iterable[str], replacement: str = REDACTED) -> str:
    """Replace the values of the named query parameters (an API key sent as ``?api_key=``)."""
    secret = {n for n in names if n}
    parts = urlsplit(url)
    if not secret or not parts.query:
        return url
    pairs = [
        (k, replacement if k in secret else v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit(parts._replace(query=urlencode(pairs)))


CaptureKind = Literal["http", "cli", "web", "mcp", "external"]


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
    ) -> Capture:
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
    ) -> Capture | None:
        """Log the streamed body; None when nothing was streamed for ``collector``."""
        ...

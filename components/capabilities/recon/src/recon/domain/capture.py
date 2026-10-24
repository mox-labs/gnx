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
from urllib.parse import parse_qsl, quote_plus, unquote_plus, urlencode, urlsplit, urlunsplit

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
    """The credentials among request ``headers`` recon sends, to redact in what it captures
    (:func:`redact_credentials`): the value of each header whose name says it carries one;
    for a value that is an auth scheme and a credential (``Bearer t0k``), the credential
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


#: A credential this many characters long or longer is redacted wherever it appears, inside
#: other text too: a server that echoes one puts it where it likes (after a newline, in a URL,
#: run into other letters), and text that holds a real token, which is long, holds the token.
#: A shorter one is as likely to be a word or a number of its own (a cookie's ``consent=true``,
#: a stdio server's ``DEBUG=1``, ``X-Api-Key-Version: 2``), so it is redacted only where it
#: is a whole value.
LONG_CREDENTIAL: Final = 8


def redact_credentials(value: Any, credentials: Iterable[str]) -> Any:
    """``value``, decoded JSON (a captured result, or the request that got it), with each of
    ``credentials`` replaced by :data:`REDACTED`. A credential of :data:`LONG_CREDENTIAL`
    characters or more is replaced wherever it appears in a string or a key. A shorter one is
    replaced only where it is a whole value: a whole string (a header's value, in a mapping
    of headers), or a whole query parameter's value in a string that is a URL; never inside
    longer text. A number, a boolean or null is never touched, so what is redacted is still
    JSON of the same shape (a credential ``2`` leaves ``2``, ``2026`` and ``v2`` as they
    are)."""
    given = {credential for credential in credentials if credential}
    long = sorted((c for c in given if len(c) >= LONG_CREDENTIAL), key=len, reverse=True)
    return _redacted(value, long, given.difference(long))


def _redacted(value: Any, long: list[str], short: set[str]) -> Any:
    if isinstance(value, str):
        return _redacted_text(value, long, short)
    if isinstance(value, list):
        return [_redacted(item, long, short) for item in value]
    if isinstance(value, dict):
        return {
            _redacted_text(key, long, set()): _redacted(item, long, short)
            for key, item in value.items()
        }
    return value


def _redacted_text(text: str, long: list[str], short: set[str]) -> str:
    for secret in long:
        text = text.replace(secret, REDACTED)
    if text in short:
        return REDACTED
    return _redacted_query_values(text, short) if short else text


def _redacted_query_values(text: str, short: set[str]) -> str:
    """``text`` with each query parameter whose whole value, decoded, is one of ``short``
    replaced, when ``text`` is a URL (it has a ``?`` and no whitespace); any other text as it
    is. The rest of the URL is kept as written."""
    base, mark, rest = text.partition("?")
    if not mark or any(char.isspace() for char in text):
        return text
    query, hash_mark, fragment = rest.partition("#")
    pairs = []
    for pair in query.split("&"):
        name, equals, written = pair.partition("=")
        whole = bool(equals) and unquote_plus(written) in short
        pairs.append(f"{name}={quote_plus(REDACTED)}" if whole else pair)
    return f"{base}?{'&'.join(pairs)}{hash_mark}{fragment}"


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


#: ``stream``: one window's arrivals, each with its order and its offset in time from the
#: window's open, from which the window replays (:mod:`recon.domain.stream`).
CaptureKind = Literal["http", "cli", "web", "mcp", "external", "stream"]


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

    def tee(self, collector: str, data: bytes) -> None:
        """Append ``data`` to the body streaming for ``collector`` (its ``stream_path``), for
        a caller that writes no file itself."""
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

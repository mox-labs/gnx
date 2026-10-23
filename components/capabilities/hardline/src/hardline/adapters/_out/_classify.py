"""Map a provider's HTTP status to a BackendError reason.

The OpenAI-compatible protocol and Anthropic's Messages API share their status semantics, so
one rule serves every backend built on HTTP. Transport failures (no status) are classified
where they are caught, in ``_http``: a timeout is ``timeout``, any other ``unavailable``.

A 5xx is transient, and so retried and then fallen back from, except two that no retry can
change: 501 (the server does not implement the request) and 505 (it refuses the HTTP version)
say the row points somewhere that cannot serve it, which is ``bad_request``. A 500 stays
transient although a given prompt may reproduce it every time: a server's fault is more often
passing than not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from hardline.domain.errors import Reason


#: Server errors that say the request can never succeed at this location.
_CANNOT_SERVE = frozenset({501, 505})


def classify(status: int, headers: Mapping[str, str]) -> tuple[Reason, float | None]:
    """``(reason, retry_after_seconds)`` for a response that is not a success."""
    retry_after = _retry_after(headers)
    if status in (401, 403):
        return "auth", None
    if status == 429:
        return "rate_limit", retry_after
    if status == 408:
        return "timeout", retry_after
    if status in _CANNOT_SERVE:
        return "bad_request", None
    if status >= 500:  # includes Anthropic's 529 overloaded
        return "unavailable", retry_after
    if 400 <= status < 500:
        return "bad_request", None
    return "unknown", None


def _retry_after(headers: Mapping[str, str]) -> float | None:
    value = headers.get("retry-after")
    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:
        return None  # an HTTP-date form; the runtime's own backoff applies

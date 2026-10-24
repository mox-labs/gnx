"""How a request to an HTTP catalog source is addressed and carries its credential, the one
rule the ``api`` and ``sse`` collectors share.

The credential is the variable the source's ``auth.env`` names, with its ``auth.prefix``,
sent as the ``auth.header`` header, the ``auth.param`` query parameter, or both. An unset
variable sends the request without it (optional auth, e.g. a higher rate limit), which the
plan warns about before anything is sent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from recon.domain.substitution import substitute

if TYPE_CHECKING:
    from collections.abc import Mapping

    from recon.domain.models import SourceEntry


def build_url(base_url: str, endpoint: str, values: Mapping[str, str]) -> str:
    """The source's url and the endpoint, its ``{placeholders}`` filled from ``values``."""
    path = substitute(endpoint, dict(values))
    return f"{base_url.rstrip('/')}{path}"


def credential(source: SourceEntry, env: Mapping[str, str]) -> str | None:
    """The prefixed credential, or None when no variable is named or it is unset."""
    auth = source.auth
    if not auth.env:
        return None
    value = env.get(auth.env, "")
    if not value:
        return None
    return f"{auth.prefix}{value}"


def auth_headers(source: SourceEntry, env: Mapping[str, str], user_agent: str) -> dict[str, str]:
    """The headers every request to ``source`` carries: its User-Agent (the source's own, or
    ``user_agent``) and its credential header."""
    headers = {"User-Agent": source.user_agent or user_agent}
    secret = credential(source, env)
    if secret is not None and source.auth.header:
        headers[source.auth.header] = secret
    return headers


def auth_params(source: SourceEntry, env: Mapping[str, str]) -> dict[str, str]:
    """The query parameter that carries ``source``'s credential, when it is sent so."""
    secret = credential(source, env)
    if secret is None or not source.auth.param:
        return {}
    return {source.auth.param: secret}

"""How a request to an HTTP catalog source is addressed and carries its credential, the one
rule the ``api`` and ``sse`` collectors share.

The credential is what the source's ``auth.token`` names (``env:NAME`` or ``file:PATH``), with
its ``auth.prefix``, sent as the ``auth.header`` header, the ``auth.param`` query parameter, or
both; or the headers its ``auth.headers_helper`` prints, sent as they are. An unset variable
sends the request without it (optional auth, e.g. a higher rate limit), which the plan warns
about before anything is sent. Each is resolved by :class:`~recon.adapters._out.credentials.
Credentials`, once a survey.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from recon.domain.http import REDACTED
from recon.domain.substitution import substitute

if TYPE_CHECKING:
    from collections.abc import Mapping

    from recon.adapters._out.credentials import Credentials
    from recon.domain.models import SourceEntry


def build_url(base_url: str, endpoint: str, values: Mapping[str, str]) -> str:
    """The source's url and the endpoint, its ``{placeholders}`` filled from ``values``."""
    path = substitute(endpoint, dict(values))
    return f"{base_url.rstrip('/')}{path}"


def auth_headers(source: SourceEntry, credentials: Credentials, user_agent: str) -> dict[str, str]:
    """The headers every request to ``source`` carries: its User-Agent (the source's own, or
    ``user_agent``), and its credential header or the headers its helper prints."""
    headers = {"User-Agent": source.user_agent or user_agent}
    secret = credentials.token(source)
    if secret is not None and source.auth.header:
        headers[source.auth.header] = secret
    headers.update(credentials.helped(source))
    return headers


def auth_params(source: SourceEntry, credentials: Credentials) -> dict[str, str]:
    """The query parameter that carries ``source``'s credential, when it is sent so."""
    secret = credentials.token(source)
    if secret is None or not source.auth.param:
        return {}
    return {source.auth.param: secret}


def described_credential(source: SourceEntry) -> tuple[dict[str, str], dict[str, str]]:
    """The query parameter and the header that carry ``source``'s ``auth.token``, as a plan
    shows a request: :data:`~recon.domain.http.REDACTED`, read from nowhere. A helper's headers
    are not known until it runs, so a plan names the helper instead."""
    if not source.auth.token:
        return {}, {}
    params = {source.auth.param: REDACTED} if source.auth.param else {}
    headers = {source.auth.header: REDACTED} if source.auth.header else {}
    return params, headers

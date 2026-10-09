"""ApiCollector — delegates HTTP to an injected Requester; yields records.

No direct httpx import. No rate limiter (that's inside the Requester).
Constructor-injected dependencies are explicit: tests pass a fake Requester
without monkeypatching anything.

Registered as the built-in collector type ``api`` (entry-point group ``recon.collectors``).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from recon import DEFAULT_USER_AGENT
from recon.adapters._out.parsing import parse_body, to_records
from recon.application.captures import redact_query_params
from recon.application.transforms import BUILTIN_TRANSFORMS, apply_normalize
from recon.domain.collector import CollectorType
from recon.domain.exceptions import CollectionError, Problem
from recon.domain.substitution import find_unresolved, substitute

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from recon.domain.capture import CaptureLog
    from recon.domain.collector import Transform
    from recon.domain.http import Requester
    from recon.domain.models import CollectorEntry, SourceEntry


class ApiCollector:
    """Fetches structured data from an HTTP API and yields records.

    Returns an Iterator so large responses don't force list materialization
    at the call site. (True streaming into normalize/sink requires the
    requester to return a streaming body; current Requester buffers.)
    """

    def __init__(
        self,
        requester: Requester,
        env: Mapping[str, str] | None = None,
        transforms: Mapping[str, Transform] = BUILTIN_TRANSFORMS,
    ) -> None:
        self._requester = requester
        self._env = env if env is not None else os.environ
        self._transforms = transforms

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
    ) -> Iterator[dict[str, Any]]:
        if not source:
            msg = f"API collector '{entry.name}' requires a source"
            raise CollectionError(msg, kind="config")
        if not entry.endpoint:
            msg = f"API collector '{entry.name}' has no endpoint"
            raise CollectionError(msg, kind="config")

        url = build_url(source.url, entry.endpoint, entry.params or {})
        query_params = {**_resolved_params(entry.params or {}), **self._auth_params(source)}
        headers = self._build_headers(source)
        body = _substituted_body(entry)

        resp = self._requester.request(
            source,
            entry.method,
            url,
            params=query_params,
            headers=headers,
            json_body=body,
        )

        if captures is not None:
            request: dict[str, Any] = {
                "method": entry.method,
                # The response URL carries the query string, and with auth.param the
                # query string carries the key: it never reaches the capture log.
                "url": redact_query_params(resp.url, [source.auth.param]),
            }
            if body is not None:
                request["body"] = body
            captures.record(
                collector=entry.name,
                source=source.name,
                kind="http",
                request=request,
                status=resp.status_code,
                content_type=resp.content_type,
                headers=resp.headers,
                body=resp.body,
            )

        data = parse_body(resp.text, entry.response_format, origin=resp.url)
        for record in to_records(data, entry.extract):
            yield (
                apply_normalize(record, entry.normalize, self._transforms)
                if entry.normalize
                else record
            )

    # --- Private ---

    def _build_headers(self, source: SourceEntry) -> dict[str, str]:
        ua = source.user_agent or DEFAULT_USER_AGENT
        headers = {"User-Agent": ua}
        secret = self._secret(source)
        if secret is not None and source.auth.header:
            headers[source.auth.header] = secret
        return headers

    def _auth_params(self, source: SourceEntry) -> dict[str, str]:
        secret = self._secret(source)
        if secret is None or not source.auth.param:
            return {}
        return {source.auth.param: secret}

    def _secret(self, source: SourceEntry) -> str | None:
        """The prefixed credential, or None when no env var is named or it is unset.

        An unset variable sends the request without credentials (optional auth, e.g. a
        higher rate limit); ``survey --dry-run`` warns about it before anything is sent.
        """
        auth = source.auth
        if not auth.env:
            return None
        value = self._env.get(auth.env, "")
        if not value:
            return None
        return f"{auth.prefix}{value}"


def build_url(base_url: str, endpoint: str, params: dict[str, str]) -> str:
    path = substitute(endpoint, params)
    return f"{base_url.rstrip('/')}{path}"


def _resolved_params(params: dict[str, str]) -> dict[str, str]:
    """Drop entries whose values still contain {placeholder}."""
    return {k: v for k, v in params.items() if not (isinstance(v, str) and "{" in v)}


def _substituted_body(entry: CollectorEntry) -> dict[str, Any] | None:
    if not entry.body:
        return None
    body: dict[str, Any] = substitute(entry.body, entry.params or {})
    unresolved = find_unresolved(body)
    if unresolved:
        missing = ", ".join(sorted(set(unresolved)))
        msg = (
            f"API collector '{entry.name}' body has unresolved placeholder(s): "
            f"{{{missing}}}. Add them to params: — sending this literally to the "
            f"server will silently fail or return empty results."
        )
        raise CollectionError(msg, kind="config")
    return body


# --- Registration ---


def _check(entry: CollectorEntry) -> list[Problem]:
    problems = []
    if not entry.endpoint:
        problems.append(Problem("endpoint", "an api collector needs an endpoint (e.g. /search)"))
    else:
        unresolved = find_unresolved(substitute(entry.endpoint, entry.params or {}))
        if unresolved:
            missing = ", ".join(f"{{{u}}}" for u in sorted(set(unresolved)))
            problems.append(
                Problem("endpoint", f"unresolved placeholder(s) {missing}: set them in params:")
            )
    if entry.body:
        unresolved = find_unresolved(substitute(entry.body, entry.params or {}))
        if unresolved:
            missing = ", ".join(sorted(set(unresolved)))
            problems.append(
                Problem("body", f"unresolved placeholder(s) {{{missing}}}: add to params:")
            )
    return problems


def _describe(entry: CollectorEntry, source: SourceEntry | None) -> dict[str, Any]:
    if source is None or not entry.endpoint:
        return {}
    return {
        "method": entry.method,
        "url": build_url(source.url, entry.endpoint, entry.params or {}),
    }


collector_type = CollectorType(
    create=lambda ctx: ApiCollector(ctx.requester, ctx.env, ctx.transforms),
    effects=frozenset({"network"}),
    summary="HTTP API request → JSON/XML → records",
    requires_source=True,
    check=_check,
    describe=_describe,
)

"""ApiCollector — delegates HTTP to an injected Requester; yields records.

No direct httpx2 import. No rate limiter (that's inside the Requester).
Constructor-injected dependencies are explicit: tests pass a fake Requester
without monkeypatching anything.

Its own keys (:class:`ApiSettings`): ``endpoint``, appended to the source's url; ``params``,
the query parameters, sent as written; ``vars``, the values for the ``{placeholders}`` in
``endpoint`` and in ``body``'s strings, which are sent nowhere else; ``method``, ``body`` and
``response_format``.

Registered as the built-in collector type ``api`` (entry-point group ``recon.collectors``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict

from recon.adapters._out.parsing import parse_body
from recon.adapters._out.user_agent import DEFAULT_USER_AGENT
from recon.domain.capture import redact_query_params
from recon.domain.collector import CollectorType
from recon.domain.errors import Problem
from recon.domain.http import REDACTED, reported_url
from recon.domain.normalize import BUILTIN_TRANSFORMS, apply_normalize, to_records
from recon.domain.substitution import find_unresolved, substitute

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from recon.domain.capture import CaptureLog
    from recon.domain.collector import Transform
    from recon.domain.http import Requester
    from recon.domain.models import CollectorEntry, SourceEntry


class ApiSettings(BaseModel):
    """An ``api`` collector's own keys."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: str | None = None
    params: dict[str, str] = {}
    vars: dict[str, str] = {}
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    body: dict[str, Any] | None = None
    response_format: Literal["json", "xml", "html", "text"] = "json"


def _settings(entry: CollectorEntry) -> ApiSettings:
    return ApiSettings.model_validate(entry.settings)


class ApiCollector:
    """Fetches structured data from an HTTP API and yields records.

    Returns an Iterator so large responses don't force list materialization
    at the call site. (True streaming into normalize/sink requires the
    requester to return a streaming body; current Requester buffers.)
    """

    def __init__(
        self,
        requester: Requester,
        env: Mapping[str, str],
        user_agent: str,
        transforms: Mapping[str, Transform] = BUILTIN_TRANSFORMS,
    ) -> None:
        self._requester = requester
        self._env = env
        self._user_agent = user_agent
        self._transforms = transforms

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
    ) -> Iterator[dict[str, Any]]:
        settings = _settings(entry)
        # The plan holds both: the type requires a source, and _check an endpoint.
        assert source is not None and settings.endpoint is not None
        url = build_url(source.url, settings.endpoint, settings.vars)
        body = substitute(settings.body, settings.vars) if settings.body else None

        resp = self._requester.request(
            source,
            settings.method,
            url,
            params={**settings.params, **self._auth_params(source)},
            headers=self._build_headers(source),
            json_body=body,
        )

        if captures is not None:
            request: dict[str, Any] = {
                "method": settings.method,
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

        data = parse_body(resp.text, settings.response_format, origin=resp.url)
        for record in to_records(data, entry.extract):
            yield (
                apply_normalize(record, entry.normalize, self._transforms)
                if entry.normalize
                else record
            )

    # --- Private ---

    def _build_headers(self, source: SourceEntry) -> dict[str, str]:
        headers = {"User-Agent": source.user_agent or self._user_agent}
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


def build_url(base_url: str, endpoint: str, values: Mapping[str, str]) -> str:
    """The source's url and the endpoint, its ``{placeholders}`` filled from ``values``."""
    path = substitute(endpoint, dict(values))
    return f"{base_url.rstrip('/')}{path}"


# --- Registration ---


def _check(entry: CollectorEntry) -> list[Problem]:
    settings = _settings(entry)
    problems = []
    if not settings.endpoint:
        problems.append(
            Problem(
                "endpoint",
                "an api collector needs an endpoint",
                kind="missing_key",
                fix="endpoint: /search (the path after the source's url)",
            )
        )
    elif unresolved := _placeholders(substitute(settings.endpoint, settings.vars)):
        problems.append(
            Problem(
                "endpoint",
                f"unresolved placeholder(s) {unresolved}",
                kind="unresolved",
                fix=f"set {unresolved} under vars:",
                got=settings.endpoint,
            )
        )
    if settings.response_format not in ("json", "xml"):
        problems.append(
            Problem(
                "response_format",
                f"an api collector parses json or xml; {settings.response_format} would give a "
                "table with no records",
                fix="response_format: json (or xml); to convert a page, use a web collector",
                got=settings.response_format,
                allowed=("json", "xml"),
            )
        )
    if settings.body and (unresolved := _placeholders(substitute(settings.body, settings.vars))):
        problems.append(
            Problem(
                "body",
                f"unresolved placeholder(s) {unresolved}",
                kind="unresolved",
                fix=f"set {unresolved} under vars:",
            )
        )
    for key, value in settings.params.items():
        if unresolved := _placeholders(value):
            problems.append(
                Problem(
                    f"params.{key}",
                    f"unresolved placeholder(s) {unresolved}: a params value is a query "
                    "parameter, sent as written",
                    kind="unresolved",
                    fix="write the value to send; a value to fill an endpoint or body "
                    "placeholder goes under vars:",
                    got=value,
                )
            )
    return problems


def _placeholders(value: Any) -> str:
    """The ``{name}`` placeholders left in ``value``, as written, or "" when none are."""
    return ", ".join(f"{{{name}}}" for name in sorted(set(find_unresolved(value))))


def _describe(entry: CollectorEntry, source: SourceEntry | None) -> dict[str, Any]:
    """The request a run sends: its method, its URL with every query parameter, the headers
    the mission decides, and its body. The credential is :data:`REDACTED` wherever it goes,
    as in telemetry; it is sent only when its variable is set, which the plan warns about."""
    settings = _settings(entry)
    if source is None or not settings.endpoint:
        return {}
    params = dict(settings.params)
    if source.auth.param and source.auth.env:
        params[source.auth.param] = REDACTED
    url = _with_query(build_url(source.url, settings.endpoint, settings.vars), params)
    headers = {"User-Agent": source.user_agent or DEFAULT_USER_AGENT}
    if source.auth.header and source.auth.env:
        headers[source.auth.header] = REDACTED
    request: dict[str, Any] = {
        "method": settings.method,
        "url": reported_url(url, source.auth.param),
        "headers": headers,
    }
    if settings.body:
        request["body"] = substitute(settings.body, settings.vars)
    return request


def _with_query(url: str, params: Mapping[str, str]) -> str:
    """``url`` with ``params`` merged into its query, as the requester sends it: a parameter
    already in the URL keeps its place and takes the new value."""
    parts = urlsplit(url)
    merged = dict(parse_qsl(parts.query, keep_blank_values=True)) | dict(params)
    return urlunsplit(parts._replace(query=urlencode(merged))) if merged else url


collector_type = CollectorType(
    create=lambda ctx: ApiCollector(ctx.requester, ctx.env, ctx.user_agent, ctx.transforms),
    effects=frozenset({"network"}),
    summary="HTTP API request → JSON/XML → records",
    settings=ApiSettings,
    source_kinds=frozenset({"api"}),
    requires_source=True,
    normalizes=True,
    extracts=True,
    check=_check,
    describe=_describe,
)

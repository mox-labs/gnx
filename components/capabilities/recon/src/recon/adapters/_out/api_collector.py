"""ApiCollector: an HTTP API through the injected Requester; the built-in collector type
``api`` and the source kind ``api`` (an HTTP endpoint).

No direct httpx2 import. No rate limiter (that's inside the Requester). Built with the needs it
names (``requester``, ``credentials``, ``user_agent``), so a test hands it a fake Requester
without monkeypatching anything.

Its own keys (:class:`ApiSettings`): ``endpoint``, appended to the source's url; ``params``,
the query parameters, sent as written; ``vars``, the values for the ``{placeholders}`` in
``endpoint`` and in ``body``'s strings, which are sent nowhere else; ``method``, ``body`` and
``response_format``: how recon converts the answer, ``json`` (the default), ``xml``, ``text``
(line by line) or ``document`` (converted to markdown by its content type: HTML, PDF, DOCX,
..., one record ``{url, title, content, content_type}``).

Each answer is one :class:`~recon.core.domain.collector.Body`: recon captures it (the source's
credentials kept out), converts it, finds its records (``extract:``) and shapes them
(``normalize:``).

With ``paginate:`` it is :class:`~recon.core.domain.collector.Paged`: recon runs the crawl, and
asks this type for each page by its token, each a request through the Requester, so each draws
a rate-limit token and each is captured, each with the entry's method and body. A ``link`` or
``header`` crawl asks for the URL the page named, resolved against the page's own and held to
the source's origin (a link elsewhere fails the table: the source's credential would go with
it), with the source's credential parameter and no other query; a ``cursor``, ``offset`` or
``page`` crawl sends ``params`` and the token as ``paginate.param`` (a ``page`` crawl its first
page's number too). The next token a page names is the type's to find: the ``Link`` header's
``rel="next"``, or what is at ``paginate.next`` in its body, parsed for it here as recon parses
it for its records.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict

from recon.adapters._out.http_source import (
    auth_headers,
    auth_params,
    build_url,
    described_credential,
)
from recon.adapters._out.parsing import parse_body
from recon.adapters._out.user_agent import DEFAULT_USER_AGENT
from recon.core.domain.capture import redact_query_params
from recon.core.domain.collector import Body, Page, PlainKind, source_needed
from recon.core.domain.errors import CollectionError, Problem
from recon.core.domain.http import reported_url
from recon.core.domain.normalize import resolve_path
from recon.core.domain.paginate import COUNTED, next_link
from recon.core.domain.substitution import find_unresolved, substitute

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from recon.core.domain.collector import Effect, Intent, Item, Position, RecordsKind, Run
    from recon.core.domain.credentials import Credentials
    from recon.core.domain.http import HttpResponse, Requester
    from recon.core.domain.models import SourceEntry
    from recon.core.domain.paginate import PaginateSpec

#: The converter each ``response_format`` names.
CONVERTERS = {"json": "json", "xml": "xml", "text": "lines", "document": "document"}

#: The formats a page's next token can be found in the body of.
_PARSED = ("json", "xml")


class ApiSettings(BaseModel):
    """An ``api`` collector's own keys."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: str | None = None
    params: dict[str, str] = {}
    vars: dict[str, str] = {}
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    body: dict[str, Any] | None = None
    response_format: Literal["json", "xml", "text", "document"] = "json"


class ApiCollector:
    """Asks an HTTP API, and gives each answer as a body."""

    effects: frozenset[Effect] | None = frozenset({"network"})
    records: RecordsKind = "finite"
    accepts: frozenset[str] | None = frozenset({"api"})

    def __init__(self, *, requester: Requester, credentials: Credentials, user_agent: str) -> None:
        self._requester = requester
        self._credentials = credentials
        self._user_agent = user_agent

    def describe(self, run: Run) -> Intent:
        return _describe(run)

    def check(self, run: Run) -> list[Problem]:
        return source_needed(run, "api") + _check(run)

    def collect(self, run: Run, resume: Position | None) -> Iterator[Item]:
        """The source's answer: one request, one body (an entry with no ``paginate:``, which
        has nowhere to go on from)."""
        settings, source, url, body = self._asked(run)
        yield self._fetch(settings, source, (url, _params(settings)), body)[0]

    def page(self, run: Run, token: Any) -> Page:
        """The page ``token`` names (None: the first), and the next token it names."""
        settings, source, url, body = self._asked(run)
        spec = run.entry.paginate
        assert spec is not None  # recon crawls only an entry with paginate:
        target = _target(spec, source, settings, url, token)
        given, resp = self._fetch(settings, source, target, body)
        named = url if token is None and spec.type in ("link", "header") else None
        return Page(given, _found(spec, settings, resp), named)

    def close(self) -> None:
        return None  # the act closes its Requester

    # --- Private ---

    def _asked(self, run: Run) -> tuple[ApiSettings, SourceEntry, str, dict[str, Any] | None]:
        settings = run.settings_as(ApiSettings)
        source = run.source
        # The plan holds both: check needs a source, and an endpoint.
        assert source is not None and settings.endpoint is not None
        url = build_url(source.url, settings.endpoint, settings.vars)
        body = substitute(settings.body, settings.vars) if settings.body else None
        return settings, source, url, body

    def _fetch(
        self,
        settings: ApiSettings,
        source: SourceEntry,
        target: tuple[str, dict[str, str]],
        body: dict[str, Any] | None,
    ) -> tuple[Body, HttpResponse]:
        """One request, its answer as a body: what a capture keeps of the request (its URL's
        credential parameter redacted), and the source's credentials as its secrets."""
        url, params = target
        try:
            resp = self._requester.request(
                source,
                settings.method,
                url,
                params={**params, **auth_params(source, self._credentials)},
                headers=auth_headers(source, self._credentials, self._user_agent),
                json_body=body,
            )
        except CollectionError as exc:
            redacted = self._credentials.redacted(source, exc)
            if redacted is exc:
                raise
            raise redacted from None  # the original quotes the credential
        request: dict[str, Any] = {
            "method": settings.method,
            # The response URL carries the query string, and with auth.param the query string
            # carries the key: it never reaches the capture log.
            "url": redact_query_params(resp.url, [source.auth.param]),
        }
        if body is not None:
            request["body"] = body
        header = next((v for k, v in resp.headers.items() if k.lower() == "content-type"), "")
        given = Body(
            media_type=header or resp.content_type,
            data=resp.body,
            request=request,
            converter=CONVERTERS[settings.response_format],
            kind="http",
            status=resp.status_code,
            headers=resp.headers,
            secrets=tuple(self._credentials.secrets(source)),
            origin=resp.url,
        )
        return given, resp


class ApiSource(PlainKind):
    """The ``api`` source kind: an HTTP endpoint, its ``url``, ``auth`` and ``rate_limit``."""


def _target(
    spec: PaginateSpec, source: SourceEntry, settings: ApiSettings, url: str, token: Any
) -> tuple[str, dict[str, str]]:
    """The URL and query a page is asked for with: the first page's (no token) with
    ``params``; a ``link`` or ``header`` crawl's next at its own URL, on the source's origin;
    the others with ``params`` and the token as ``paginate.param``."""
    if token is None:
        return url, _params(settings)
    if spec.type in ("link", "header"):
        return _on_the_source(source, token), {}
    assert spec.param is not None  # PaginateSpec holds it for the others
    return url, {**_params(settings), spec.param: str(token)}


def _found(spec: PaginateSpec, settings: ApiSettings, resp: HttpResponse) -> Any:
    """The next page's token a page gave: the ``Link`` header's next URL, or what is at
    ``paginate.next`` in its body (parsed as its ``response_format`` says); None when it gave
    none. A URL is resolved against the page's own. A token that is not one (an object, a
    list) fails the table."""
    if spec.type in COUNTED:
        return None
    found: Any
    if spec.type == "header":
        link = next((value for name, value in resp.headers.items() if name.lower() == "link"), "")
        found = next_link(link)
    else:
        assert spec.next is not None  # PaginateSpec holds it for link and cursor
        fmt = settings.response_format
        data = parse_body(resp.text, fmt, origin=resp.url) if fmt in _PARSED else None
        found = resolve_path(data, spec.next) if data is not None else None
    kinds: tuple[type, ...] = (str, int, float) if spec.type == "cursor" else (str,)
    if found is not None and (isinstance(found, bool) or not isinstance(found, kinds)):
        what = "cursor (a string or a number)" if spec.type == "cursor" else "URL"
        raise CollectionError(
            f"paginate.next ({spec.next}) holds a {type(found).__name__}, not a {what}",
            kind="run_failed",
            fix=f"point paginate.next at the next page's {what} in a page's body",
        )
    if isinstance(found, str) and found and spec.type != "cursor":
        return urljoin(resp.url, found)
    return found


#: The port of each scheme a next page's URL may have, when it names none.
_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    return parts.scheme, (parts.hostname or "").lower(), parts.port or _PORTS.get(parts.scheme)


def _on_the_source(source: SourceEntry, link: str) -> str:
    """``link``, when it is at the source's origin (its scheme, host and port), where the
    source's credential may go; else the table fails."""
    if _origin(link)[0] not in _PORTS or _origin(link) != _origin(source.url):
        raise CollectionError(
            f"the next page is at {reported_url(link, source.auth.param)}, off source "
            f"{source.name!r}'s origin ({reported_url(source.url)}): its credential would go "
            "with the request, so it is not followed",
            kind="run_failed",
            fix="check that the source's url: has the scheme, host and port the API links its "
            "pages at; an API that pages on another host needs a source of its own",
        )
    return link


# --- the plan ---


def _check(run: Run) -> list[Problem]:
    settings = run.settings_as(ApiSettings)
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
    elif unresolved := _placeholders(settings.endpoint, settings.vars):
        problems.append(
            Problem(
                "endpoint",
                f"unresolved placeholder(s) {unresolved}",
                kind="unresolved",
                fix=f"set {unresolved} under vars:",
                got=settings.endpoint,
            )
        )
    if settings.body and (unresolved := _placeholders(settings.body, settings.vars)):
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
    spec = run.entry.paginate
    if spec is not None and spec.param is not None and spec.param in settings.params:
        problems.append(
            Problem(
                f"params.{spec.param}",
                f"paginate.param sends {spec.param} with every page after the first, so the "
                "mission does not send it too",
                kind="name_taken",
                fix=f"remove params.{spec.param}: the crawl sets it",
                got=settings.params[spec.param],
            )
        )
    return problems


def _placeholders(value: Any, variables: Mapping[str, str] | None = None) -> str:
    """The ``{name}`` placeholders in ``value`` that ``variables`` leaves unfilled, as
    written, or "" when none are."""
    return ", ".join(f"{{{name}}}" for name in sorted(set(find_unresolved(value, variables))))


def _params(settings: ApiSettings) -> dict[str, str]:
    """The query string as sent: each value as written, an escaped brace made one."""
    return {key: substitute(value, {}) for key, value in settings.params.items()}


def _describe(run: Run) -> dict[str, Any]:
    """The request a run sends: its method, its URL with every query parameter, the headers
    the mission decides, and its body; with ``paginate:``, how it crawls. The credential is
    :data:`REDACTED` wherever it goes, as in telemetry; it is sent only when its variable is
    set, which the plan warns about."""
    settings = run.settings_as(ApiSettings)
    entry, source = run.entry, run.source
    if source is None or not settings.endpoint:
        return {}
    secret_params, secret_headers = described_credential(source)
    params = _params(settings) | secret_params
    url = _with_query(build_url(source.url, settings.endpoint, settings.vars), params)
    headers = {"User-Agent": source.user_agent or DEFAULT_USER_AGENT} | secret_headers
    request: dict[str, Any] = {
        "method": settings.method,
        "url": reported_url(url, source.auth.param),
        "headers": headers,
    }
    if source.auth.headers_helper is not None:
        request["headers_helper"] = source.auth.headers_helper
    if settings.body:
        request["body"] = substitute(settings.body, settings.vars)
    if entry.paginate is not None:
        request["paginate"] = entry.paginate.model_dump(exclude_none=True)
    return request


def _with_query(url: str, params: Mapping[str, str]) -> str:
    """``url`` with ``params`` merged into its query, as the requester sends it: a parameter
    already in the URL keeps its place and takes the new value."""
    parts = urlsplit(url)
    merged = dict(parse_qsl(parts.query, keep_blank_values=True)) | dict(params)
    return urlunsplit(parts._replace(query=urlencode(merged))) if merged else url


def register(registry: Any) -> None:
    """The ``api`` collector type and the ``api`` source kind."""
    registry.register(
        "recon.collector",
        "recon.v1.collector.api",
        ApiCollector,
        config=ApiSettings,
        needs={"requester", "credentials", "user_agent"},
        summary="HTTP API request → JSON, XML, text or a document → records",
    )
    registry.register(
        "recon.source", "recon.v1.source.api", ApiSource, summary="an HTTP endpoint: url, auth"
    )

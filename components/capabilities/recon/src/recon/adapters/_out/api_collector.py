"""ApiCollector — delegates HTTP to an injected Requester; yields records.

No direct httpx2 import. No rate limiter (that's inside the Requester).
Constructor-injected dependencies are explicit: tests pass a fake Requester
without monkeypatching anything.

Its own keys (:class:`ApiSettings`): ``endpoint``, appended to the source's url; ``params``,
the query parameters, sent as written; ``vars``, the values for the ``{placeholders}`` in
``endpoint`` and in ``body``'s strings, which are sent nowhere else; ``method``, ``body`` and
``response_format``.

With ``paginate:`` (the type declares ``paginates``) it reads its source page by page, each
page a request through the Requester, so each draws a rate-limit token and each is captured,
each with the entry's method and body. A ``link`` or ``header`` crawl asks for the URL the
page named, resolved against the page's own and held to the source's origin (a link elsewhere
fails the table: the source's credential would go with it), with the source's credential
parameter and no other query; a ``cursor``, ``offset`` or ``page`` crawl sends ``params`` and
the token as ``paginate.param`` (a ``page`` crawl its first page's number too). How it stops
is :class:`~recon.domain.paginate.Crawl`'s. Each record carries ``_position``, ``{page, i}``;
each page, with its token (a URL as recon reports one, its credential parameter redacted), and
a crawl cut short are said through the page log (``CollectorContext.pages``).

Registered as the built-in collector type ``api`` (entry-point group ``recon.collectors``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict

from recon.adapters._out.credentials import Credentials
from recon.adapters._out.http_source import (
    auth_headers,
    auth_params,
    build_url,
    described_credential,
)
from recon.adapters._out.parsing import parse_body
from recon.adapters._out.user_agent import DEFAULT_USER_AGENT
from recon.domain.capture import redact_query_params
from recon.domain.collector import CollectorType
from recon.domain.errors import CollectionError, Problem
from recon.domain.http import reported_url
from recon.domain.normalize import BUILTIN_TRANSFORMS, apply_normalize, resolve_path, to_records
from recon.domain.paginate import COUNTED, FIRST_PAGE, Crawl, NoPages, Stop, next_link, position
from recon.domain.stream import POSITION_FIELD
from recon.domain.substitution import find_unresolved, substitute

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from recon.domain.capture import CaptureLog
    from recon.domain.collector import Transform
    from recon.domain.http import HttpResponse, Requester
    from recon.domain.models import CollectorEntry, SourceEntry
    from recon.domain.paginate import PageLog, PaginateSpec


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
        credentials: Credentials,
        user_agent: str,
        transforms: Mapping[str, Transform] = BUILTIN_TRANSFORMS,
        *,
        pages: PageLog | None = None,
    ) -> None:
        self._requester = requester
        self._credentials = credentials
        self._user_agent = user_agent
        self._transforms = transforms
        self._pages = pages if pages is not None else NoPages()

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
        if entry.paginate is not None:
            yield from self._crawl(entry, source, settings, (url, body), captures)
            return
        data, _ = self._fetch(entry, source, settings, (url, _params(settings)), body, captures)
        for record in to_records(data, entry.extract):
            yield self._shaped(entry, record)

    # --- Private ---

    def _crawl(
        self,
        entry: CollectorEntry,
        source: SourceEntry,
        settings: ApiSettings,
        first: tuple[str, dict[str, Any] | None],
        captures: CaptureLog | None,
    ) -> Iterator[dict[str, Any]]:
        """Every page, until the crawl stops; each record with its ``_position``."""
        spec = entry.paginate
        assert spec is not None
        url, body = first
        linked = spec.type in ("link", "header")
        crawl = Crawl(spec, first=url if linked else None)
        token: Any = None
        target = (url, _params(settings))
        if spec.type == "page":
            assert spec.param is not None  # PaginateSpec holds it for page
            token = FIRST_PAGE
            target = (url, {**_params(settings), spec.param: str(token)})
        page = 0
        while True:
            data, resp = self._fetch(entry, source, settings, target, body, captures, page=page)
            said = reported_url(token, source.auth.param) if linked and token else token
            self._pages.page(page, said)
            records = to_records(data, entry.extract)
            for index, record in enumerate(records):
                yield {**self._shaped(entry, record), POSITION_FIELD: position(page, index)}
            step = crawl.after(len(records), _found(spec, data, resp))
            if isinstance(step, Stop):
                if step.truncated_by is not None:
                    self._pages.truncated(step.truncated_by)
                return
            token, page = step.token, page + 1
            if linked:
                target = (_on_the_source(source, token), {})
            else:
                assert spec.param is not None  # PaginateSpec holds it for the others
                target = (url, {**_params(settings), spec.param: str(token)})

    def _fetch(
        self,
        entry: CollectorEntry,
        source: SourceEntry,
        settings: ApiSettings,
        target: tuple[str, dict[str, str]],
        body: dict[str, Any] | None,
        captures: CaptureLog | None,
        *,
        page: int | None = None,
    ) -> tuple[Any, HttpResponse]:
        """One request, captured when the mission keeps captures, and its body parsed. A
        page's capture names its ordinal (``page``), so a replay gives its records the same
        ``_position``."""
        url, params = target
        resp = self._requester.request(
            source,
            settings.method,
            url,
            params={**params, **self._auth_params(source)},
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
            if page is not None:
                request["page"] = page
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

        return parse_body(resp.text, settings.response_format, origin=resp.url), resp

    def _shaped(self, entry: CollectorEntry, record: dict[str, Any]) -> dict[str, Any]:
        if not entry.normalize:
            return record
        return apply_normalize(record, entry.normalize, self._transforms)

    def _build_headers(self, source: SourceEntry) -> dict[str, str]:
        return auth_headers(source, self._credentials, self._user_agent)

    def _auth_params(self, source: SourceEntry) -> dict[str, str]:
        return auth_params(source, self._credentials)


def _found(spec: PaginateSpec, data: Any, resp: HttpResponse) -> Any:
    """The next page's token a page gave: the ``Link`` header's next URL, or what is at
    ``paginate.next`` in its body; None when it gave none. A URL is resolved against the
    page's own. A token that is not one (an object, a list) fails the table."""
    if spec.type in COUNTED:
        return None
    found: Any
    if spec.type == "header":
        link = next((value for name, value in resp.headers.items() if name.lower() == "link"), "")
        found = next_link(link)
    else:
        assert spec.next is not None  # PaginateSpec holds it for link and cursor
        found = resolve_path(data, spec.next)
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
    spec = entry.paginate
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


def _describe(entry: CollectorEntry, source: SourceEntry | None) -> dict[str, Any]:
    """The request a run sends: its method, its URL with every query parameter, the headers
    the mission decides, and its body; with ``paginate:``, how it crawls. The credential is
    :data:`REDACTED` wherever it goes, as in telemetry; it is sent only when its variable is
    set, which the plan warns about."""
    settings = _settings(entry)
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


collector_type = CollectorType(
    create=lambda ctx: ApiCollector(
        ctx.requester,
        Credentials(ctx.env, ctx.mission_dir, ctx.shell, ctx.deadline),
        ctx.user_agent,
        ctx.transforms,
        pages=ctx.pages,
    ),
    effects=frozenset({"network"}),
    summary="HTTP API request → JSON/XML → records",
    settings=ApiSettings,
    source_kinds=frozenset({"api"}),
    accepts_kinds=frozenset({"api", "web"}),  # an HTTP endpoint, by either name
    requires_source=True,
    normalizes=True,
    extracts=True,
    paginates=True,
    check=_check,
    describe=_describe,
)

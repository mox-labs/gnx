"""McpCollector: calls a tool, or reads a resource, on an MCP server and yields records.

Registered as the built-in collector type ``mcp`` (entry-point group ``recon.collectors``),
with the source kind ``mcp`` it adds. The client is FastMCP's (``fastmcp-slim[client]``, the
one pydantic-ai's ``MCPToolset`` wraps), on the MCP SDK v2 and httpx2. This file is the only
one that imports ``fastmcp``, ``mcp`` or ``jsonschema``, and it imports them when a survey or a
dry run first reaches a server, so planning, status and the hook route never load them.

A source names its server:

* **stdio**: ``command``, ``args`` and ``env``. The server is started on the first table that
  uses it, serves every other table of the survey, and is stopped by ``close()`` when the
  survey ends. It runs in recon's working directory with the SDK's short list of inherited
  variables (``HOME``, ``LOGNAME``, ``PATH``, ``SHELL``, ``TERM``, ``USER``) and what ``env``
  gives: each value is written as is, or is ``env:NAME``, a variable of recon's environment
  the mission thereby declares (an unset one is left out). Its stderr goes to a temporary
  file, whose end an error quotes.
* **remote** (Streamable HTTP): ``url``, with ``token`` (``env:NAME``, or ``file:PATH``
  relative to the mission's directory), sent as ``Authorization: Bearer``, or
  ``headers_helper``, a command run once per survey whose stdout is a JSON object of headers.
  The client is the SDK's: it follows a redirect only within the server's origin, so no
  credential leaves it. An HTTP 401 or 403 makes the failure ``auth``.

A collector names a ``tool`` with its ``arguments``, or a ``resource``; ``vars`` fill their
``{placeholders}``. A tool's records come from its structured content when it declares an
output schema, else from the JSON in its text content; ``result:`` picks one explicitly. A
resource's come from the JSON in its text. ``extract`` and ``normalize`` then apply as for
``api``. A tool error fails the table; an answer with no content keeps it empty with a
``result_empty`` warning; text that is not JSON fails it.

Before each call the run's requirements are checked against the server's listing, taken
once per survey: the tool is listed and the arguments match its input schema, or the
resource is listed or matches a listed template. A dry run asks the same through
:meth:`McpCollector.requirements`; in a survey, one that does not hold fails the table as
``config``. Every call, a listing included, draws a token from the source's rate limit and
is one ``recon.mcp.call`` client span that names the method, the tool and the source, and
nothing the call carried. FastMCP's own spans are suppressed, since a failed one carries the
tool's error text; the SDK's, which carry the method and a request id, nest under recon's.
With ``preserve_raw``, each result is captured whole (kind ``mcp``), every credential value
the source resolved replaced.

Why a thread: the client is async and recon is not. One event loop runs on a thread of its
own while the collector lives; each call is handed to it, in the caller's context (so spans
nest), and waited for within the source's ``timeout`` and the survey's deadline.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from recon.domain.capture import REDACTED
from recon.domain.collector import CollectorType, Requirement
from recon.domain.errors import CollectionError, ErrorKind, NoRecordsError, Problem
from recon.domain.http import REDACTED as REPORTED_REDACTED
from recon.domain.http import reported_url
from recon.domain.normalize import apply_normalize, to_records
from recon.domain.substitution import find_unresolved, substitute
from recon.domain.telemetry import MCP_CALL

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterable, Iterator

    from recon.domain.capture import CaptureLog
    from recon.domain.collector import CollectorContext
    from recon.domain.models import CollectorEntry, SourceEntry

T = TypeVar("T")

#: The most of a stdio server's stderr an error quotes: the end, where the reason is.
STDERR_TAIL_CHARS = 2000

#: The most argument problems a requirement names.
_MAX_ARGUMENT_PROBLEMS = 5

#: How long ``close()`` waits for each server to stop, and for the loop's thread to end.
_CLOSE_SECONDS = 10.0

_REFERENCE = re.compile(r"^(env|file):(.+)$")


class McpSourceSettings(BaseModel):
    """An ``mcp`` source's own keys: a stdio server's ``command``, ``args`` and ``env``, or a
    remote's credential, ``token`` or ``headers_helper`` (the remote's ``url`` is the shared
    one)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    token: str | None = None
    headers_helper: str | None = None


class McpSettings(BaseModel):
    """An ``mcp`` collector's own keys: the ``tool`` to call with its ``arguments``, or the
    ``resource`` to read; ``vars`` for their placeholders; ``result``, which part of a tool's
    answer holds the records."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str | None = None
    arguments: dict[str, Any] = {}
    resource: str | None = None
    vars: dict[str, str] = {}
    result: Literal["structured", "text"] | None = None


def _settings(entry: CollectorEntry) -> McpSettings:
    return McpSettings.model_validate(entry.settings)


def _source_settings(source: SourceEntry) -> McpSourceSettings:
    return McpSourceSettings.model_validate(source.settings)


class _Loop:
    """An asyncio event loop on a thread of its own, for as long as the collector lives."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="recon-mcp")
        self._thread.daemon = True
        self._thread.start()

    def run(self, work: Coroutine[Any, Any, T], seconds: float) -> T:
        """``work``'s result, run on the loop in the caller's context (contextvars are copied
        when it is handed over, so a span current here is current there). Past ``seconds``
        it is cancelled and TimeoutError raised."""
        future = asyncio.run_coroutine_threadsafe(work, self._loop)
        try:
            return future.result(seconds)
        except BaseException:
            future.cancel()
            raise

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(_CLOSE_SECONDS)
        if not self._thread.is_alive():
            self._loop.close()


@dataclass
class _Server:
    """One source's server for the survey: its client, what it lists, and what to redact."""

    source: str
    #: FastMCP's ``Client``, whose ``__aenter__`` and ``close`` are not annotated.
    client: Any = None
    #: What the plan and a capture name it by: the command line, or the URL as reported.
    named: str = ""
    stdio: bool = False
    #: Every credential value the source resolved: never written to a capture.
    secrets: list[str] = field(default_factory=list)
    stderr: Any = None
    #: The HTTP statuses a remote answered with, so a 401 or 403 is told as ``auth``.
    statuses: list[int] = field(default_factory=list)
    #: Why it could not be reached, when it could not: every later table fails the same way.
    error: CollectionError | None = None
    tools: dict[str, Any] | None = None
    resources: tuple[set[str], list[str]] | None = None


class McpCollector:
    """Calls a tool or reads a resource on each source's MCP server, one server per source
    for the survey, and yields the records of its answer.

    ``http_client`` builds the httpx2 ``AsyncClient`` a remote server is reached through,
    given ``headers``, ``timeout`` and ``auth`` (the SDK's ``create_mcp_http_client`` unless a
    test hands it one over a transport of its own); whatever it builds, the server's statuses
    are recorded on it."""

    def __init__(
        self, ctx: CollectorContext, *, http_client: Callable[..., Any] | None = None
    ) -> None:
        self._ctx = ctx
        self._http_client = http_client
        self._loop: _Loop | None = None
        self._servers: dict[str, _Server] = {}

    # --- the Collector port ---

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
    ) -> Iterator[dict[str, Any]]:
        settings = _settings(entry)
        assert source is not None  # the type requires a source, which the plan holds
        server = self._reach(source)
        self._list(server, source, settings)
        for requirement in self._check(server, source, settings):
            if not requirement.holds:
                raise CollectionError(
                    f"{requirement.need}: {requirement.why}", kind="config", fix=requirement.fix
                )
        if settings.tool is not None:
            arguments = substitute(settings.arguments, settings.vars)
            result = self._call(server, source, "tools/call", settings.tool, arguments)
            raw: dict[str, Any] = result.model_dump(mode="json", by_alias=True, exclude_none=True)
            request: dict[str, Any] = {"tool": settings.tool, "arguments": arguments}
        else:
            assert settings.resource is not None  # _check holds one of the two
            uri = substitute(settings.resource, settings.vars)
            contents = self._call(server, source, "resources/read", uri, None)
            raw = {"contents": [c.model_dump(mode="json", by_alias=True, exclude_none=True)
                                for c in contents]}  # fmt: skip
            request = {"resource": uri}
        if captures is not None:
            captures.record(
                collector=entry.name,
                source=source.name,
                kind="mcp",
                request={"server": server.named, **request},
                status=None,
                content_type="application/json",
                headers={},
                body=_redacted(json.dumps(raw, ensure_ascii=False), server.secrets).encode(),
            )
        if settings.tool is not None:
            body = _tool_body(result, server.tools or {}, settings, source.name)
        else:
            body = _resource_body(raw["contents"], request["resource"], source.name)
        for record in to_records(body, entry.extract):
            yield (
                apply_normalize(record, entry.normalize, self._ctx.transforms)
                if entry.normalize
                else record
            )

    def requirements(self, entry: CollectorEntry, source: SourceEntry | None) -> list[Requirement]:
        """What the run needs of its server, each checked now: that it starts (stdio) or
        answers (remote), then that it lists the tool and the arguments match its input
        schema, or that it offers the resource."""
        assert source is not None  # the type requires a source, which the plan holds
        settings = _settings(entry)
        try:
            server = self._reach(source)
            self._list(server, source, settings)
        except CollectionError as exc:
            return [Requirement(_reach_need(source), holds=False, why=str(exc), fix=exc.fix or "")]
        return [
            Requirement(_reach_need(source), holds=True),
            *self._check(server, source, settings),
        ]

    def close(self) -> None:
        """Stop every server this survey started and end the loop's thread. A server that
        does not stop within its time is left to the SDK's own shutdown, which kills it."""
        if self._loop is None:
            return
        loop, self._loop = self._loop, None
        try:
            for server in self._servers.values():
                try:
                    if server.client is not None:
                        loop.run(server.client.close(), _CLOSE_SECONDS)
                except Exception:  # one server's stop must not keep the others running
                    pass
                finally:
                    if server.stderr is not None:
                        server.stderr.close()
        finally:
            self._servers.clear()
            loop.close()

    # --- reaching a server ---

    def _reach(self, source: SourceEntry) -> _Server:
        """The source's server, started or connected on first use; the error it failed with,
        raised again, when it could not be."""
        server = self._servers.get(source.name)
        if server is None:
            server = self._servers[source.name] = _Server(source.name)
            try:
                self._connect(server, source)
            except CollectionError as exc:
                server.error = exc
        if server.error is not None:
            raise server.error
        return server

    def _connect(self, server: _Server, source: SourceEntry) -> None:
        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport, StreamableHttpTransport

        settings = _source_settings(source)
        if settings.command is not None:
            env = self._stdio_env(settings, server)
            server.stdio = True
            server.named = " ".join([settings.command, *settings.args])
            # Open for the server's life, which ends in close().
            server.stderr = tempfile.TemporaryFile(  # noqa: SIM115
                "w+", encoding="utf-8", errors="replace"
            )
            transport: Any = StdioTransport(
                settings.command, list(settings.args), env=env, log_file=server.stderr
            )
        else:
            headers = self._remote_headers(settings, source, server)
            server.named = reported_url(source.url)
            transport = StreamableHttpTransport(
                source.url, headers=headers, httpx_client_factory=self._client_factory(server)
            )
        server.client = Client(transport, timeout=source.timeout, log_handler=_ignore_log)
        self._run(server, source, server.client.__aenter__(), "connect")

    def _stdio_env(self, settings: McpSourceSettings, server: _Server) -> dict[str, str]:
        env: dict[str, str] = {}
        for name, written in settings.env.items():
            value = self._resolve(written, f"env.{name}") if _REFERENCE.match(written) else written
            if value is None:
                continue
            if written != value:
                server.secrets.append(value)
            env[name] = value
        return env

    def _remote_headers(
        self, settings: McpSourceSettings, source: SourceEntry, server: _Server
    ) -> dict[str, str]:
        headers = {"User-Agent": source.user_agent or self._ctx.user_agent}
        if settings.token is not None:
            token = self._resolve(settings.token, "token")
            if token is not None:
                headers["Authorization"] = f"Bearer {token}"
                server.secrets.append(token)
        if settings.headers_helper is not None:
            given = self._helper_headers(settings.headers_helper, source)
            headers.update(given)
            server.secrets.extend(given.values())
        return headers

    def _resolve(self, written: str, where: str) -> str | None:
        """The value an ``env:NAME`` or ``file:PATH`` reference names; None for an unset
        variable (the plan warned about it)."""
        match = _REFERENCE.match(written)
        assert match is not None  # check_source holds the token to a reference
        scheme, name = match.groups()
        if scheme == "env":
            return self._ctx.env.get(name)
        path = Path(name).expanduser()
        path = path if path.is_absolute() else self._ctx.mission_dir / path
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise CollectionError(
                f"{where}: the file {path} could not be read: {type(exc).__name__}",
                kind="config",
                fix=f"put the credential in {path}, or correct the path (a relative one is "
                "relative to the mission's directory)",
            ) from exc

    def _helper_headers(self, command: str, source: SourceEntry) -> dict[str, str]:
        """The headers the source's ``headers_helper`` prints, as a JSON object of strings."""
        limit = self._ctx.deadline.limit(source.timeout)
        try:
            lines = list(self._ctx.shell.run_lines(command, None, limit))
        except CollectionError as exc:
            raise CollectionError(
                f"source {source.name!r}: its headers_helper failed, so no credential was "
                f"sent: {exc}",
                kind="auth",
                fix="run the headers_helper by hand to see why it failed, then fix it",
            ) from exc
        try:
            given = json.loads("\n".join(lines))
        except json.JSONDecodeError:
            given = None
        if not isinstance(given, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in given.items()
        ):
            raise CollectionError(
                f"source {source.name!r}: its headers_helper did not print a JSON object of "
                "header names to string values",
                kind="config",
                fix='make it print one object, e.g. {"Authorization": "Bearer ..."}',
            )
        return given

    def _client_factory(self, server: _Server) -> Callable[..., Any]:
        from mcp.shared._httpx_utils import create_mcp_http_client

        base = self._http_client or create_mcp_http_client

        def build(
            headers: dict[str, str] | None = None,
            timeout: Any = None,
            auth: Any = None,
            **_ignored: Any,
        ) -> Any:
            # FastMCP passes ``follow_redirects=True`` to a factory. It is not passed on: the
            # SDK's client follows no redirect itself, and its transport follows one only
            # within the server's origin, which is the rule a credential needs.
            client = base(headers=headers, timeout=timeout, auth=auth)

            async def seen(response: Any) -> None:
                server.statuses.append(response.status_code)

            client.event_hooks.setdefault("response", []).append(seen)
            return client

        return build

    # --- calls ---

    def _run(
        self, server: _Server, source: SourceEntry, work: Coroutine[Any, Any, T], what: str
    ) -> T:
        """``work`` on the loop, within the source's timeout and the survey's deadline, its
        failure classified."""
        from fastmcp.telemetry import suppress_fastmcp_telemetry

        if self._loop is None:
            self._loop = _Loop()
        deadline = self._ctx.deadline
        try:
            deadline.check()
        except CollectionError:
            work.close()
            raise
        limit = deadline.limit(source.timeout)
        server.statuses.clear()

        async def quietly() -> T:
            with suppress_fastmcp_telemetry():
                return await work

        try:
            return self._loop.run(quietly(), limit)
        except TimeoutError as exc:
            if limit < source.timeout and deadline.remaining() == 0:
                raise deadline.exceeded() from exc
            raise CollectionError(
                f"the MCP server of source {source.name!r} did not answer ({what}) within "
                f"{limit:g}s",
                kind="transient",
                fix="raise the source's timeout:, or survey again later",
            ) from exc
        except CollectionError:
            raise
        except Exception as exc:
            raise _classified(exc, server, source, what) from exc

    def _call(
        self, server: _Server, source: SourceEntry, method: str, name: str, arguments: Any
    ) -> Any:
        """One call to the server: a rate-limit token, then the call as one client span."""
        assert server.client is not None
        client = server.client
        self._ctx.rate_limiter.acquire(source, self._ctx.deadline)
        attributes: dict[str, str | None] = {
            "mcp.method.name": method,
            "gen_ai.tool.name": name if method == "tools/call" else None,
            "recon.source": source.name,
        }
        with self._ctx.telemetry.span(MCP_CALL, attributes, kind="client"):
            if method == "tools/call":
                timeout = self._ctx.deadline.limit(source.timeout)
                work: Any = client.call_tool_mcp(name, arguments, timeout=timeout)
            elif method == "resources/read":
                work = client.read_resource(name)
            elif method == "tools/list":
                work = client.list_tools()
            elif method == "resources/list":
                work = client.list_resources()
            else:
                work = client.list_resource_templates()
            return self._run(server, source, work, method)

    # --- requirements ---

    def _list(self, server: _Server, source: SourceEntry, settings: McpSettings) -> None:
        """Take the server's tools, or its resources and templates, once for the survey."""
        if settings.tool is not None and server.tools is None:
            listed = self._call(server, source, "tools/list", "", None)
            server.tools = {t.name: t for t in listed}
        elif settings.tool is None and server.resources is None:
            resources = self._call(server, source, "resources/list", "", None)
            templates = self._call(server, source, "resources/templates/list", "", None)
            server.resources = (
                {str(r.uri) for r in resources},
                [t.uri_template for t in templates],
            )

    def _check(
        self, server: _Server, source: SourceEntry, settings: McpSettings
    ) -> list[Requirement]:
        if settings.tool is not None:
            return self._tool_requirements(server, source, settings.tool, settings)
        assert settings.resource is not None  # _check (the plan's) holds one of the two
        return [self._resource_requirement(server, source, settings)]

    def _tool_requirements(
        self, server: _Server, source: SourceEntry, tool: str, settings: McpSettings
    ) -> list[Requirement]:
        need = f"the server of source {source.name!r} lists the tool {tool!r}"
        assert server.tools is not None  # _list took them
        if tool not in server.tools:
            names = ", ".join(sorted(server.tools)) or "(none)"
            return [
                Requirement(
                    need, holds=False, why=f"it lists: {names}", fix="set tool: to one it lists"
                )
            ]
        schema = server.tools[tool].input_schema
        valid = f"the input schema of {tool!r} (source {source.name!r}) is valid JSON Schema"
        try:
            found = _argument_problems(schema, substitute(settings.arguments, settings.vars))
        except _InvalidSchemaError as exc:
            return [
                Requirement(need, holds=True),
                Requirement(
                    valid,
                    holds=False,
                    why=str(exc),
                    fix="the server declares a schema no client can check arguments against; "
                    "report it to the server's maintainers, or use another tool",
                ),
            ]
        return [
            Requirement(need, holds=True),
            Requirement(
                f"the arguments given to {tool!r} (source {source.name!r}) match its input schema",
                holds=not found,
                why="; ".join(found),
                fix="" if not found else "correct arguments: as the tool's input schema says",
            ),
        ]

    def _resource_requirement(
        self, server: _Server, source: SourceEntry, settings: McpSettings
    ) -> Requirement:
        uri = substitute(settings.resource, settings.vars)
        need = f"the server of source {source.name!r} offers the resource {uri!r}"
        assert server.resources is not None  # _list took them
        uris, templates = server.resources
        if uri in uris or any(_matches(template, uri) for template in templates):
            return Requirement(need, holds=True)
        return Requirement(
            need,
            holds=False,
            why=f"it lists {len(uris)} resource(s) and {len(templates)} template(s), and none "
            "is or matches it",
            fix="set resource: to a URI the server lists, or one a template it lists matches",
        )


def _reach_need(source: SourceEntry) -> str:
    """The first requirement of every run: its server can be reached and lists what it has."""
    if _source_settings(source).command is not None:
        return f"the stdio server of source {source.name!r} starts and answers"
    return f"the server of source {source.name!r} answers"


def _ignore_log(message: Any) -> None:
    """A server's log notifications are the server's: recon prints none of them."""


class _InvalidSchemaError(Exception):
    """A tool's input schema that is not valid JSON Schema, or refers to what it does not hold."""


def _argument_problems(schema: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    """What ``schema`` refuses in ``arguments``, each at its path (``arguments.limit``).

    The schema is the server's, so it is checked before it is used, and its ``$ref``s
    resolve within it alone: the registry retrieves nothing, where jsonschema's default
    would fetch a ``$ref`` to another host itself, outside the requester. A schema that is
    not valid, or refers to what it does not hold, raises ``_InvalidSchemaError``.
    """
    from jsonschema.exceptions import SchemaError, UnknownType
    from jsonschema.validators import validator_for
    from referencing import Registry
    from referencing.exceptions import Unresolvable

    cls = validator_for(schema)
    try:
        cls.check_schema(schema)
        validator = cls(schema, registry=Registry())
        errors = sorted(validator.iter_errors(arguments), key=lambda e: list(map(str, e.path)))
    except SchemaError as exc:
        raise _InvalidSchemaError(f"at {_schema_path(exc.path)}: {exc.message}") from exc
    except UnknownType as exc:
        raise _InvalidSchemaError(f"it names a type no validator knows: {exc.type!r}") from exc
    except Unresolvable as exc:
        raise _InvalidSchemaError(f"it refers to {exc.ref!r}, which it does not hold") from exc
    found = [
        "arguments" + "".join(f".{part}" for part in error.path) + f": {error.message}"
        for error in errors
    ]
    return found[:_MAX_ARGUMENT_PROBLEMS]


def _schema_path(path: Iterable[Any]) -> str:
    """Where in a schema a problem is, as the dotted path the arguments' problems use."""
    return "".join(f".{part}" for part in path).lstrip(".") or "its root"


def _matches(template: str, uri: str) -> bool:
    """Whether ``uri`` fills ``template``'s ``{name}`` parts (RFC 6570's simple form)."""
    pattern = "".join(
        "[^/]+" if part.startswith("{") else re.escape(part)
        for part in re.split(r"(\{[^}]+\})", template)
        if part
    )
    return re.fullmatch(pattern, uri) is not None


def _redacted(text: str, secrets: list[str]) -> str:
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    return text


def _empty(where: str, key: str) -> NoRecordsError:
    return NoRecordsError(
        f"{where} answered with no content",
        problem_kind="result_empty",
        field=key,
        fix="check the arguments against what the server holds; a tool that has nothing "
        "to return should return an empty list",
    )


def _tool_body(result: Any, tools: dict[str, Any], settings: McpSettings, source: str) -> Any:
    """The parsed body a tool's answer holds its records in."""
    tool = settings.tool
    where = f"tool {tool!r} of source {source!r}"
    if result.is_error:
        text = " ".join(b.text for b in result.content if getattr(b, "type", "") == "text")
        raise CollectionError(
            f"{where} returned an error: {text or '(no text)'}",
            fix="read the tool's error; correct arguments:, or the server's setup",
        )
    declared = tools.get(tool or "")
    read = settings.result or (
        "structured" if declared is not None and declared.output_schema else "text"
    )
    texts = [b.text for b in result.content if getattr(b, "type", "") == "text"]
    if result.structured_content is None and not result.content:
        raise _empty(where, "tool")
    if read == "structured":
        if result.structured_content is None:
            raise CollectionError(
                f"{where} returned no structured content",
                kind="config" if settings.result else "run_failed",
                fix="set result: text to read the JSON in its text content",
            )
        return result.structured_content
    if not texts:
        kinds = ", ".join(sorted({getattr(b, "type", "?") for b in result.content}))
        raise CollectionError(
            f"{where} returned no text content (it returned: {kinds or 'structured content'})",
            kind="config" if settings.result == "text" else "run_failed",
            fix="set result: structured when the tool returns structured content; recon reads "
            "records from JSON, not from images or audio",
        )
    return _json_of(texts, where)


def _resource_body(contents: list[dict[str, Any]], uri: str, source: str) -> Any:
    where = f"resource {uri!r} of source {source!r}"
    if not contents:
        raise _empty(where, "resource")
    texts = [c["text"] for c in contents if "text" in c]
    if not texts:
        raise CollectionError(
            f"{where} is binary, not text",
            fix="read a resource whose text is JSON; recon makes records from JSON",
        )
    return _json_of(texts, where)


def _json_of(texts: list[str], where: str) -> Any:
    """The JSON the texts hold: one text's value, or a list of each's."""
    if not any(text.strip() for text in texts):
        raise _empty(where, "tool" if where.startswith("tool") else "resource")
    parsed = []
    for text in texts:
        try:
            parsed.append(json.loads(text))
        except json.JSONDecodeError as exc:
            raise CollectionError(
                f"{where} returned text that is not JSON (at line {exc.lineno}, column "
                f"{exc.colno})",
                fix="recon makes records from JSON; for a tool that declares an output schema, "
                "set result: structured",
            ) from exc
    return parsed[0] if len(parsed) == 1 else parsed


def _causes(exc: BaseException) -> Iterator[BaseException]:
    """``exc``, what it was raised from, and the members of any exception group among them."""
    seen: set[int] = set()
    pending = [exc]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        pending += [c for c in (current.__cause__, current.__context__) if c is not None]
        pending += list(getattr(current, "exceptions", ()))


def _is(exc: BaseException, module: str, name: str) -> bool:
    """Whether ``exc`` is a ``module``'s ``name``, or a subclass of it, without importing it."""
    return any(
        c.__name__ == name and c.__module__.split(".")[0] == module for c in type(exc).__mro__
    )


def _classified(exc: Exception, server: _Server, source: SourceEntry, what: str) -> CollectionError:
    """A failure to reach or call the server as recon's error: ``auth`` for a refused
    credential, ``transient`` for a server that could not be reached or was busy, ``config``
    for a command that could not start, else ``run_failed``; a stdio server's stderr quoted."""
    causes = list(_causes(exc))
    named = f"the MCP server of source {source.name!r}"
    kind: ErrorKind = "run_failed"
    fix = "read the server's error; correct the source, or the server's setup"
    if server.stdio and any(isinstance(c, FileNotFoundError | PermissionError) for c in causes):
        kind = "config"
        fix = "install the server's command, or correct command: (it is looked up on PATH)"
    elif any(status in (401, 403) for status in server.statuses):
        kind = "auth"
        fix = "check the credential the source's token: or headers_helper gives"
    elif any(status == 429 or status >= 500 for status in server.statuses) or any(
        _is(c, "httpx2", "TransportError") for c in causes
    ):
        kind = "transient"
        fix = "survey again later"
    message = f"{named} failed ({what}): {type(exc).__name__}: {exc}"
    if server.stdio and server.stderr is not None:
        message += _tail(server.stderr)
    return CollectionError(message, kind=kind, fix=fix)


def _tail(stderr: Any) -> str:
    try:
        stderr.flush()
        stderr.seek(0)
        text: str = stderr.read().strip()
    except (OSError, ValueError):
        return ""
    return f"\n{text[-STDERR_TAIL_CHARS:]}" if text else ""


# --- Registration ---


def _check(entry: CollectorEntry) -> list[Problem]:
    settings = _settings(entry)
    problems = []
    if settings.tool is None and settings.resource is None:
        problems.append(
            Problem(
                "tool",
                "an mcp collector needs tool: (a tool to call) or resource: (a URI to read)",
                kind="missing_key",
                fix="tool: search (and its arguments:), or resource: docs://index",
            )
        )
    elif settings.tool is not None and settings.resource is not None:
        problems.append(
            Problem(
                "resource",
                "an mcp collector calls a tool or reads a resource, not both",
                fix="keep tool: or resource:, and give the other its own collector",
                got=settings.resource,
            )
        )
    if settings.tool is None:
        for key in ("arguments", "result"):
            if key in settings.model_fields_set:
                problems.append(
                    Problem(
                        key,
                        f"{key}: is for a tool call; this collector reads a resource",
                        kind="unknown_key",
                        fix=f"remove {key}:, or call a tool with tool:",
                    )
                )
    for key, value in (("arguments", settings.arguments), ("resource", settings.resource)):
        if value and (unresolved := _placeholders(substitute(value, settings.vars))):
            problems.append(
                Problem(
                    key,
                    f"unresolved placeholder(s) {unresolved}",
                    kind="unresolved",
                    fix=f"set {unresolved} under vars:",
                )
            )
    return problems


def _placeholders(value: Any) -> str:
    return ", ".join(f"{{{name}}}" for name in sorted(set(find_unresolved(value))))


def _check_source(source: SourceEntry) -> list[Problem]:
    settings = _source_settings(source)
    problems = []
    remote_only = ("token", "headers_helper")
    stdio_only = ("args", "env")
    if settings.command is None and not source.url:
        problems.append(
            Problem(
                "command",
                "an mcp source names its server: command: (a stdio server recon starts) or "
                "url: (a remote one)",
                kind="missing_key",
                fix="command: npx (with args:), or url: https://example.com/mcp",
            )
        )
    elif settings.command is not None and source.url:
        problems.append(
            Problem(
                "url",
                "an mcp source is a stdio server (command:) or a remote one (url:), not both",
                fix="keep command: or url:, and give the other its own source",
                got=source.url,
            )
        )
    elif settings.command is not None:
        problems += [
            _not_for(key, "a stdio server", "it reads its credentials from env:")
            for key in (*remote_only, "user_agent")
            if key in settings.model_fields_set or key in source.model_fields_set
        ]
    else:
        scheme = urlsplit(source.url).scheme
        if scheme not in ("http", "https"):
            problems.append(
                Problem(
                    "url",
                    f"a remote mcp source's url is http or https, not {scheme or 'a path'}",
                    fix="url: https://example.com/mcp (for a server recon starts, use command:)",
                    got=source.url,
                )
            )
        problems += [
            _not_for(key, "a remote server", "name the variables on its own machine")
            for key in stdio_only
            if key in settings.model_fields_set
        ]
        if settings.token is not None and settings.headers_helper is not None:
            problems.append(
                Problem(
                    "headers_helper",
                    "an mcp source takes token: or headers_helper:, not both",
                    fix="keep one; headers_helper: may print an Authorization header itself",
                )
            )
    for key in ("auth", "max_bytes"):
        if key in source.model_fields_set:
            problems.append(
                _not_for(
                    key,
                    "an mcp source",
                    "give a remote's credential as token: or "
                    "headers_helper:, and a stdio server's under env:",
                )  # fmt: skip
            )
    if settings.token is not None and not _REFERENCE.match(settings.token):
        problems.append(
            Problem(
                "token",
                "a token is a reference, never the credential itself",
                fix="token: env:NAME (a variable recon reads) or token: file:PATH",
            )
        )
    return problems


def _not_for(key: str, what: str, instead: str) -> Problem:
    return Problem(
        key,
        f"{what} does not take {key}:",
        kind="unknown_key",
        fix=f"remove {key}: ({instead})",
    )


def _source_env(source: SourceEntry) -> dict[str, str]:
    """Each variable the source names as ``env:NAME``, with where."""
    settings = _source_settings(source)
    named = [("token", settings.token or "")]
    named += [(f"env.{name}", value) for name, value in settings.env.items()]
    found: dict[str, str] = {}
    for where, written in named:
        match = _REFERENCE.match(written)
        if match is not None and match.group(1) == "env":
            found.setdefault(match.group(2), where)
    return found


def _describe(entry: CollectorEntry, source: SourceEntry | None) -> dict[str, Any]:
    """The server the run reaches and the call it makes; credentials as REDACTED, the
    variables a stdio server is given by name only."""
    if source is None:
        return {}
    settings = _settings(entry)
    server = _source_settings(source)
    described: dict[str, Any] = {}
    if server.command is not None:
        described["server"] = " ".join([server.command, *server.args])
        described["env"] = sorted(server.env)
    else:
        described["server"] = reported_url(source.url)
        if server.token is not None:
            described["headers"] = {"Authorization": f"Bearer {REPORTED_REDACTED}"}
        if server.headers_helper is not None:
            described["headers_helper"] = server.headers_helper
    if settings.tool is not None:
        described["method"] = "tools/call"
        described["tool"] = settings.tool
        described["arguments"] = substitute(settings.arguments, settings.vars)
    elif settings.resource is not None:
        described["method"] = "resources/read"
        described["resource"] = substitute(settings.resource, settings.vars)
    return described


collector_type = CollectorType(
    create=McpCollector,
    effects=frozenset({"network", "subprocess"}),
    summary="MCP tool call or resource read → JSON → records",
    settings=McpSettings,
    source_kinds=frozenset({"mcp"}),
    accepts_kinds=frozenset({"mcp"}),
    source_settings=McpSourceSettings,
    requires_source=True,
    normalizes=True,
    extracts=True,
    check=_check,
    describe=_describe,
    check_source=_check_source,
    source_env=_source_env,
    preflight="starts each stdio server a run names, and connects to each remote one, to list "
    "its tools or resources and check each run against them; then stops and closes each",
)

"""The ``mcp`` collector type, against a real MCP server: the SDK's own, as a test fixture.

``tests/mcp_server.py`` is started over stdio, as a survey would start any stdio server, or
mounted in process behind a local HTTP server (uvicorn on 127.0.0.1) for a remote one. Where a
test needs an answer no server gives on purpose (a redirect, a 401), the remote client is
handed an httpx2 ``MockTransport`` through the collector's ``http_client`` seam. Nothing here
leaves the machine.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
import uvicorn
import yaml
from mcp.server.transport_security import TransportSecuritySettings

from recon.adapters._out.mcp_collector import McpCollector
from recon.composition import load_plugins, root
from tests.mcp_server import FIXTURE
from tests.recon_fakes import FixedClock, run_survey

if TYPE_CHECKING:
    from collections.abc import Callable

    from recon.domain.collector import Collector, CollectorContext

SERVER = str(Path(__file__).parent / "mcp_server.py")

#: The fixture as a catalog source recon starts over stdio.
STDIO = {"name": "fixture", "type": "mcp", "command": sys.executable, "args": [SERVER]}


def _survey(project: Path, collectors: list[dict[str, Any]], **kwargs: Any) -> Any:
    source = kwargs.pop("source", STDIO)
    extra = kwargs.pop("config", {})
    config = {"catalog": [source], "collectors": collectors, **extra}
    return run_survey(config, project, **kwargs)


def _rows(archive: Path, table: str) -> list[dict[str, Any]]:
    lines = (archive / f"{table}.jsonl").read_text().splitlines()
    return [row for row in map(json.loads, lines) if row != {"_empty": True}]


def _meta(archive: Path) -> dict[str, Any]:
    meta: dict[str, Any] = yaml.safe_load((archive / "meta.yaml").read_text())
    return meta


def _call(name: str, tool: str, **settings: Any) -> dict[str, Any]:
    return {"name": name, "type": "mcp", "source": "fixture", "tool": tool, **settings}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# --- the plan: what is checked without reaching a server ------------------------------------


def _problems(catalog: list[dict[str, Any]], collectors: list[dict[str, Any]]) -> Any:
    from recon.application.plan import build_plan

    plan = build_plan({"catalog": catalog, "collectors": collectors}, load_plugins(), {})
    return {p.path: p for p in plan.problems}, plan.warnings


@pytest.mark.parametrize(
    ("source", "path", "kind"),
    [
        ({"name": "s", "type": "mcp"}, "catalog.0.command", "missing_key"),
        ({**STDIO, "name": "s", "url": "https://x.test/mcp"}, "catalog.0.url", "invalid_value"),
        ({"name": "s", "type": "mcp", "url": "/srv/mcp"}, "catalog.0.url", "invalid_value"),
        ({**STDIO, "name": "s", "token": "env:T"}, "catalog.0.token", "unknown_key"),
        ({**STDIO, "name": "s", "user_agent": "me"}, "catalog.0.user_agent", "unknown_key"),
        ({"name": "s", "type": "mcp", "url": "https://x.test/mcp", "env": {"A": "b"}},
         "catalog.0.env", "unknown_key"),
        ({"name": "s", "type": "mcp", "url": "https://x.test/mcp", "token": "sk-live-123"},
         "catalog.0.token", "invalid_value"),
        ({"name": "s", "type": "mcp", "url": "https://x.test/mcp", "auth": {"env": "T"}},
         "catalog.0.auth", "unknown_key"),
        ({**STDIO, "name": "s", "cwd": "/tmp"}, "catalog.0.cwd", "unknown_key"),
    ],
)  # fmt: skip
def test_a_source_that_does_not_name_one_server_plainly_is_a_plan_problem(
    source: dict[str, Any], path: str, kind: str
) -> None:
    problems, _ = _problems([source], [{"name": "c", "type": "mcp", "source": "s", "tool": "t"}])
    assert path in problems, problems
    assert problems[path].kind == kind


@pytest.mark.parametrize(
    ("entry", "path", "kind"),
    [
        ({}, "collectors.0.tool", "missing_key"),
        ({"tool": "t", "resource": "r://x"}, "collectors.0.resource", "invalid_value"),
        ({"resource": "r://x", "arguments": {"q": 1}}, "collectors.0.arguments", "unknown_key"),
        ({"tool": "t", "arguments": {"q": "{topic}"}}, "collectors.0.arguments", "unresolved"),
        ({"resource": "r://{id}"}, "collectors.0.resource", "unresolved"),
        ({"tool": "t", "result": "html"}, "collectors.0.result", "invalid_value"),
    ],
)
def test_a_collector_that_does_not_name_one_call_plainly_is_a_plan_problem(
    entry: dict[str, Any], path: str, kind: str
) -> None:
    problems, _ = _problems(
        [{**STDIO, "name": "s"}], [{"name": "c", "type": "mcp", "source": "s", **entry}]
    )
    assert path in problems, problems
    assert problems[path].kind == kind


def test_each_variable_a_source_names_is_declared_and_warned_when_unset() -> None:
    sources: list[dict[str, Any]] = [
        {**STDIO, "name": "s", "env": {"GH": "env:GITHUB_TOKEN", "LEVEL": "debug"}},
        {"name": "r", "type": "mcp", "url": "https://x.test/mcp", "token": "env:REMOTE"},
    ]
    problems, warnings = _problems(sources, [{"name": "c", "type": "mcp", "tool": "t"}])
    assert problems == {}
    assert {(w.path, w.got) for w in warnings if w.kind == "env_unset"} == {
        ("catalog.0.env.GH", "GITHUB_TOKEN"),
        ("catalog.1.token", "REMOTE"),
    }


# --- records -------------------------------------------------------------------------------


def test_a_tool_with_an_output_schema_gives_its_structured_content(tmp_path: Path) -> None:
    collectors = [
        _call(
            "papers",
            "search",
            arguments={"query": "{topic}", "limit": 3},
            vars={"topic": "ssm"},
            extract="papers",
            normalize={"paper": "id", "title": "title"},
        )
    ]
    archive, [table] = _survey(tmp_path, collectors)
    assert table.status == "ok", table.error
    assert _rows(archive, "papers") == [{"paper": f"p{i}", "title": f"ssm {i}"} for i in range(3)]


def test_a_tool_without_one_gives_the_json_in_its_text(tmp_path: Path) -> None:
    collectors = [_call("listing", "listing", arguments={"query": "q"}, extract="items")]
    archive, [table] = _survey(tmp_path, collectors)
    assert table.status == "ok", table.error
    assert _rows(archive, "listing") == [{"id": "a", "q": "q"}, {"id": "b", "q": "q"}]


def test_result_text_reads_the_text_of_a_tool_that_wraps_it_in_its_schema(tmp_path: Path) -> None:
    """A server that declares an output schema of one string (``{"result": "<json>"}``) gives
    that string as the structured content: read as declared, it is one record; ``result:
    text`` reads the JSON."""
    collectors = [
        _call("as_declared", "wrapped", arguments={"query": "q"}),
        _call("as_text", "wrapped", arguments={"query": "q"}, result="text"),
    ]
    archive, tables = _survey(tmp_path, collectors)
    assert [t.status for t in tables] == ["ok", "ok"]
    assert _rows(archive, "as_declared") == [{"result": '[{"id": "w1", "q": "q"}]'}]
    assert _rows(archive, "as_text") == [{"id": "w1", "q": "q"}]


def test_a_resource_and_a_templated_resource_give_the_json_in_their_text(tmp_path: Path) -> None:
    collectors: list[dict[str, Any]] = [
        {"name": "all", "type": "mcp", "source": "fixture", "resource": "fixture://papers",
         "extract": "items"},
        {"name": "one", "type": "mcp", "source": "fixture", "resource": "fixture://papers/{id}",
         "vars": {"id": "42"}},
    ]  # fmt: skip
    archive, tables = _survey(tmp_path, collectors)
    assert [t.status for t in tables] == ["ok", "ok"], [t.error for t in tables]
    assert _rows(archive, "all") == [{"id": "r1"}, {"id": "r2"}]
    assert _rows(archive, "one") == [{"id": "42", "title": "paper 42"}]


# --- what is never "ok, 0 records" -----------------------------------------------------------


def test_a_tool_error_fails_the_table_with_the_tools_text(tmp_path: Path) -> None:
    archive, [table] = _survey(tmp_path, [_call("bad", "broken", arguments={"query": "x"})])
    assert table.status == "error"
    assert table.error is not None and table.error.kind == "run_failed"
    assert "tool 'broken' of source 'fixture' returned an error" in table.error.message


def test_an_answer_with_no_content_is_kept_empty_and_warned(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    archive, [table] = _survey(tmp_path, [_call("none", "nothing")], on_event=events.append)
    assert (table.status, table.records) == ("ok", 0)
    [warning] = [e for e in events if e["kind"] == "table_warning"]
    assert "answered with no content" in warning["reason"]
    assert warning["reason"].startswith("collectors.0.tool:")


def test_text_that_is_not_json_fails_the_table(tmp_path: Path) -> None:
    _, [table] = _survey(tmp_path, [_call("words", "prose")])
    assert table.status == "error"
    assert table.error is not None and "returned text that is not JSON" in table.error.message


def test_a_tool_the_server_does_not_list_fails_its_table_as_config(tmp_path: Path) -> None:
    _, tables = _survey(
        tmp_path,
        [_call("missing", "nope"), _call("wrong", "search", arguments={"limit": "many"})],
    )
    missing, wrong = tables
    assert missing.error is not None and missing.error.kind == "config"
    assert "it lists: broken, listing, nothing, prose, search, whoami, wrapped" in (
        missing.error.message
    )
    assert wrong.error is not None and wrong.error.kind == "config"
    assert "arguments: 'query' is a required property" in wrong.error.message
    assert "arguments.limit: 'many' is not of type 'integer'" in wrong.error.message


def test_a_command_that_does_not_exist_fails_as_config_and_once(tmp_path: Path) -> None:
    source = {**STDIO, "command": str(tmp_path / "no-such-server")}
    _, tables = _survey(tmp_path, [_call("a", "whoami"), _call("b", "whoami")], source=source)
    assert [t.error.kind if t.error else None for t in tables] == ["config", "config"]
    assert tables[0].error is not None and "install the server's command" in (
        tables[0].error.fix or ""
    )


# --- one server for the survey ---------------------------------------------------------------


def test_one_stdio_server_serves_every_table_and_is_stopped_once(tmp_path: Path) -> None:
    closed: list[McpCollector] = []

    class Counted(McpCollector):
        def close(self) -> None:
            closed.append(self)
            super().close()

    plugins = load_plugins()
    plugins.collector_types["mcp"] = replace(plugins.collector_types["mcp"], create=Counted)
    archive, tables = _survey(
        tmp_path, [_call("one", "whoami"), _call("two", "whoami")], plugins=plugins
    )
    assert [t.status for t in tables] == ["ok", "ok"]
    [first], [second] = _rows(archive, "one"), _rows(archive, "two")
    assert first["pid"] == second["pid"], "one server for both tables"
    assert len(closed) == 1
    deadline = time.monotonic() + 10
    while _alive(first["pid"]) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(first["pid"]), "the server was stopped when the survey ended"


def test_the_server_gets_its_env_references_and_the_capture_never_holds_them(
    tmp_path: Path,
) -> None:
    source = {**STDIO, "env": {"FIXTURE_TOKEN": "env:MY_TOKEN"}}
    archive, [table] = _survey(
        tmp_path,
        [_call("who", "whoami")],
        source=source,
        env={"MY_TOKEN": "s3cret-value", "OTHER": "never-passed"},
        config={"preserve_raw": True},
    )
    assert table.status == "ok", table.error
    [row] = _rows(archive, "who")
    assert row["token"] == "s3cret-value" and row["home"] is True
    [capture] = [json.loads(line) for line in (archive / "captures.jsonl").read_text().splitlines()]
    assert capture["kind"] == "mcp"
    assert capture["request"] == {"server": f"{sys.executable} {SERVER}", "tool": "whoami",
                                  "arguments": {}}  # fmt: skip
    body = (archive / capture["body"]).read_text()
    assert "s3cret-value" not in body and "«redacted by recon»" in body


# --- rate limit and telemetry ------------------------------------------------------------------


def test_every_call_draws_a_token_from_the_sources_rate_limit(tmp_path: Path) -> None:
    clock = FixedClock()
    source = {**STDIO, "rate_limit": {"rps": 1, "burst": 1}}
    _, tables = _survey(
        tmp_path, [_call("one", "whoami"), _call("two", "whoami")], source=source, clock=clock
    )
    assert [t.status for t in tables] == ["ok", "ok"]
    # tools/list takes the one token held; each tools/call then waits a second for its own.
    assert clock.slept == [1.0, 1.0]


def test_each_call_is_a_client_span_that_carries_no_content(tmp_path: Path) -> None:
    collectors = [_call("papers", "search", arguments={"query": "very-private-topic"})]
    archive, [table] = _survey(tmp_path, collectors)
    assert table.status == "ok", table.error
    log = (archive / "telemetry.jsonl").read_text()
    assert "very-private-topic" not in log
    calls = [e for e in map(json.loads, log.splitlines()) if e["name"] == "recon.mcp.call.start"]
    assert [e["attributes"] for e in calls] == [
        {"mcp.method.name": "tools/list", "recon.source": "fixture"},
        {"mcp.method.name": "tools/call", "gen_ai.tool.name": "search", "recon.source": "fixture"},
    ]
    assert {e["kind"] for e in calls} == {"client"}


def test_fastmcps_own_spans_which_carry_a_tools_error_text_are_not_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FastMCP sets a failed call's span status to the tool's error text; with an SDK
    installed, that text would leave by OTLP. Its spans are suppressed for recon's calls."""
    import fastmcp.telemetry
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        fastmcp.telemetry, "otel_get_tracer", lambda *args, **kwargs: provider.get_tracer("fastmcp")
    )
    _, [table] = _survey(tmp_path, [_call("bad", "broken", arguments={"query": "x"})])
    assert table.status == "error"
    assert exporter.get_finished_spans() == ()


# --- the dry run ---------------------------------------------------------------------------------


def _plan(project: Path, collectors: list[dict[str, Any]], **kwargs: Any) -> Any:
    mission = project / ".recon" / "m"
    mission.mkdir(parents=True)
    config = {"catalog": [kwargs.pop("source", STDIO)], "collectors": collectors}
    (mission / "config.yaml").write_text(yaml.safe_dump(config))
    return root({}, project=project, **kwargs).plan("m")


def test_the_dry_run_starts_the_server_and_checks_each_call_against_its_listing(
    tmp_path: Path,
) -> None:
    plan = _plan(
        tmp_path,
        [
            _call("good", "search", arguments={"query": "q"}),
            _call("missing", "nope"),
            _call("wrong", "search", arguments={"query": 3}),
            {"name": "gone", "type": "mcp", "source": "fixture", "resource": "fixture://other"},
        ],
    )
    assert plan.valid and not plan.runnable
    assert plan.preflight == [
        "mcp: starts each stdio server a run names, and connects to each remote one, to list "
        "its tools or resources and check each run against them; then stops and closes each"
    ]
    found = {(r.need, r.holds): r for r in plan.requirements}
    started = found["the stdio server of source 'fixture' starts and answers", True]
    assert started.needed_by == ["good", "missing", "wrong", "gone"]
    nope = found["the server of source 'fixture' lists the tool 'nope'", False]
    assert nope.why.startswith("it lists: broken, listing") and nope.needed_by == ["missing"]
    schema = found[
        "the arguments given to 'search' (source 'fixture') match its input schema", False
    ]
    assert schema.why == "arguments.query: 3 is not of type 'string'"
    assert ("the server of source 'fixture' offers the resource 'fixture://other'", False) in found


def test_a_dry_run_whose_server_cannot_start_says_so_and_stays_valid(tmp_path: Path) -> None:
    source = {**STDIO, "command": str(tmp_path / "no-such-server")}
    plan = _plan(tmp_path, [_call("a", "whoami")], source=source)
    [required] = plan.requirements
    assert (required.holds, required.needed_by) == (False, ["a"])
    assert "install the server's command" in required.fix
    assert plan.valid and not plan.runnable


def test_a_dry_run_builds_no_collector_whose_type_has_no_preflight(tmp_path: Path) -> None:
    built: list[str] = []
    plugins = load_plugins()

    def building(name: str) -> Callable[[CollectorContext], Collector]:
        def create(ctx: CollectorContext) -> Collector:
            built.append(name)
            raise AssertionError(f"a dry run built the collector {name!r}")

        return create

    for name, ct in list(plugins.collector_types.items()):
        if name != "mcp":
            plugins.collector_types[name] = replace(ct, create=building(name))
    plan = _plan(
        tmp_path,
        [_call("good", "whoami"), {"name": "c", "type": "cli", "run": "true"}],
        plugins=plugins,
    )
    assert built == []
    assert plan.runnable and plan.preflight


# --- remote servers ------------------------------------------------------------------------------


class _Seen:
    """An ASGI app around the fixture's that records each request's headers."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.headers: list[dict[str, str]] = []

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            self.headers.append({k.decode(): v.decode() for k, v in scope["headers"]})
        await self.app(scope, receive, send)


@pytest.fixture(scope="module")
def remote() -> Any:
    """The fixture served over Streamable HTTP on 127.0.0.1, by uvicorn on a thread."""
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    app = _Seen(FIXTURE.streamable_http_app(transport_security=security))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    yield app, f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"
    server.should_exit = True
    thread.join(5)


def test_a_remote_server_is_called_with_its_token(tmp_path: Path, remote: Any) -> None:
    seen, url = remote
    seen.headers.clear()
    source = {"name": "fixture", "type": "mcp", "url": url, "token": "env:REMOTE_TOKEN"}
    archive, [table] = _survey(
        tmp_path,
        [_call("papers", "search", arguments={"query": "q"}, extract="papers")],
        source=source,
        env={"REMOTE_TOKEN": "t0ken"},
    )
    assert table.status == "ok", table.error
    assert [r["id"] for r in _rows(archive, "papers")] == ["p0", "p1"]
    assert seen.headers and all(h["authorization"] == "Bearer t0ken" for h in seen.headers)


def test_a_headers_helper_gives_a_remote_its_headers(tmp_path: Path, remote: Any) -> None:
    seen, url = remote
    seen.headers.clear()
    helper = tmp_path / "helper.sh"
    helper.write_text('#!/bin/sh\necho \'{"X-Api-Key": "k3y"}\'\n')
    helper.chmod(0o755)
    source = {"name": "fixture", "type": "mcp", "url": url, "headers_helper": str(helper)}
    _, [table] = _survey(tmp_path, [_call("who", "whoami")], source=source)
    assert table.status == "ok", table.error
    assert seen.headers and all(h["x-api-key"] == "k3y" for h in seen.headers)


def _mock(answer: Any) -> Any:
    """The mcp type over a client whose every request ``answer`` answers."""

    def client(headers: Any = None, timeout: Any = None, auth: Any = None) -> Any:
        return httpx2.AsyncClient(
            headers=headers, timeout=timeout, auth=auth, transport=httpx2.MockTransport(answer)
        )

    plugins = load_plugins()
    plugins.collector_types["mcp"] = replace(
        plugins.collector_types["mcp"], create=lambda ctx: McpCollector(ctx, http_client=client)
    )
    return plugins


def test_a_remotes_token_never_follows_a_redirect_off_its_origin(tmp_path: Path) -> None:
    """P1.2's rule for an MCP server: the SDK's client follows no redirect, and its transport
    one only within the server's origin, so the token never reaches another host."""
    sent: list[tuple[str, str | None]] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        sent.append((request.url.host, request.headers.get("authorization")))
        return httpx2.Response(307, headers={"location": "https://elsewhere.test/mcp"})

    source = {"name": "fixture", "type": "mcp", "url": "https://mcp.test/mcp",
              "token": "env:REMOTE_TOKEN"}  # fmt: skip
    _, [table] = _survey(
        tmp_path,
        [_call("who", "whoami")],
        source=source,
        env={"REMOTE_TOKEN": "t0ken"},
        plugins=_mock(answer),
    )
    assert table.status == "error"
    assert sent and {host for host, _ in sent} == {"mcp.test"}


def test_the_sdks_client_follows_no_redirect_itself() -> None:
    """The premise of the test above for the client a survey really uses."""
    from mcp.shared._httpx_utils import create_mcp_http_client

    assert create_mcp_http_client().follow_redirects is False


def test_a_refused_credential_fails_the_table_as_auth(tmp_path: Path) -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, json={"error": "invalid_token"})

    source = {"name": "fixture", "type": "mcp", "url": "https://mcp.test/mcp",
              "token": "env:REMOTE_TOKEN"}  # fmt: skip
    _, [table] = _survey(
        tmp_path,
        [_call("who", "whoami")],
        source=source,
        env={"REMOTE_TOKEN": "expired"},
        plugins=_mock(answer),
    )
    assert table.error is not None and table.error.kind == "auth"

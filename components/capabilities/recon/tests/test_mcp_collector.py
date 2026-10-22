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
from typing import Any

import httpx2
import pytest
import uvicorn
import yaml
from mcp.server.transport_security import TransportSecuritySettings

from recon.adapters._out.mcp_collector import McpCollector
from recon.composition import load_plugins, root
from tests.mcp_server import FIXTURE
from tests.recon_fakes import FixedClock, building, run_survey

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
    assert "it lists: broken, echo, listing, nothing, prose, search, whoami, wrapped" in (
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


def test_a_stdio_server_runs_in_the_project_root_wherever_recon_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a stdio server ran in recon's working directory, so the template's
    filesystem server, given ".", read another tree when a survey was run from a
    subdirectory. It runs in the project root, against which a ``local`` source's relative
    url resolves too: the same directories wherever recon is run from."""
    project = tmp_path / "project"
    elsewhere = project / "src" / "deep"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    archive, [table] = _survey(project, [_call("who", "whoami")])
    assert table.status == "ok", table.error
    [row] = _rows(archive, "who")
    assert Path(row["cwd"]).resolve() == project.resolve()


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
    assert nope.why.startswith("it lists: broken, echo, listing") and nope.needed_by == ["missing"]
    schema = found[
        "the arguments given to 'search' (source 'fixture') match its input schema", False
    ]
    assert schema.why == "arguments.query: 3 is not of type 'string'"
    assert ("the server of source 'fixture' offers the resource 'fixture://other'", False) in found


@pytest.mark.parametrize(
    ("input_schema", "why"),
    [
        ({"type": "object", "properties": {"q": {"type": "strng"}}}, "'strng' is not valid"),
        (
            {"type": "object", "properties": {"q": {"$ref": "https://example.invalid/s.json"}}},
            "https://example.invalid/s.json",
        ),
        ({"type": "object", "properties": {"q": {"$ref": "#/nope"}}}, "/nope"),
    ],
)
def test_a_tool_whose_input_schema_is_not_valid_json_schema_is_a_requirement_not_a_crash(
    tmp_path: Path, input_schema: dict[str, Any], why: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the arguments were checked against a server's schema without checking the
    schema, so a schema with an unknown type or an unresolvable ``$ref`` crashed the dry run
    (exit 1, ``unknown``, "report this as a bug") and failed the survey's table as
    ``run_failed`` with jsonschema's raw text. It is the server's defect, and the dry run says
    so. And jsonschema fetched a ``$ref`` to another host itself, a request outside the
    requester to wherever a server pointed it: a ``$ref`` is never fetched."""
    looked_up: list[str] = []
    lookup = socket.getaddrinfo

    def recorded(host: Any, *args: Any, **kwargs: Any) -> Any:
        looked_up.append(str(host))
        return lookup(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", recorded)
    recording = tmp_path / "recording.json"
    result = {"content": [{"type": "text", "text": '[{"a": 1}]'}]}
    recording.write_text(
        json.dumps({"tool": "search", "input_schema": input_schema, "result": result})
    )
    source = {**STDIO, "args": [SERVER, "--replay", str(recording)]}
    collectors = [_call("t", "search", arguments={"q": "x"})]

    plan = _plan(tmp_path, collectors, source=source)
    assert plan.valid and not plan.runnable
    [refused] = [r for r in plan.requirements if not r.holds]
    assert refused.need == "the input schema of 'search' (source 'fixture') is valid JSON Schema"
    assert why in refused.why and refused.fix and refused.needed_by == ["t"]

    _, [table] = _survey(tmp_path, collectors, source=source)
    assert table.error is not None and table.error.kind == "config"
    assert refused.need in table.error.message
    assert "example.invalid" not in looked_up


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

    for name, ct in list(plugins.collector_types.items()):
        if name != "mcp":
            plugins.collector_types[name] = replace(ct, create=building(built, name))
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


def test_only_the_credentials_a_headers_helper_gives_are_redacted_in_a_capture(
    tmp_path: Path, remote: Any
) -> None:
    """Regression: every value a headers_helper printed counted as a secret, so a header such
    as ``X-Org: acme`` was replaced wherever ``acme`` appeared in a captured result, and the
    capture (the evidence, and the source to normalize again from) silently differed from
    the table. A header is a credential by its name; a bearer token is redacted alone too."""
    _seen, url = remote
    given = {"Authorization": "Bearer t0kenValue", "X-Goog-Api-Key": "g00gKeyValue",
             "X-Org": "acme"}  # fmt: skip
    query = "acme t0kenValue g00gKeyValue"
    archive, [table] = _survey(
        tmp_path,
        [_call("papers", "search", arguments={"query": query}, extract="papers")],
        source=_helped(tmp_path, url, given),
        config={"preserve_raw": True},
    )
    assert table.status == "ok", table.error
    [capture] = [json.loads(line) for line in (archive / "captures.jsonl").read_text().splitlines()]
    body = (archive / capture["body"]).read_text()
    assert "acme" in body, "a header that is not a credential is evidence, kept"
    assert "t0kenValue" not in body and "g00gKeyValue" not in body


def _helped(project: Path, url: str, headers: dict[str, str]) -> dict[str, Any]:
    """The remote fixture as a source whose ``headers_helper`` prints ``headers``."""
    helper = project / "helper.sh"
    helper.write_text(f"#!/bin/sh\necho '{json.dumps(headers)}'\n")
    helper.chmod(0o755)
    return {"name": "fixture", "type": "mcp", "url": url, "headers_helper": str(helper)}


def _captured(archive: Path) -> tuple[str, dict[str, Any]]:
    """The survey's one capture: its log line as written, and its body, which must be JSON."""
    [line] = (archive / "captures.jsonl").read_text().splitlines()
    body: dict[str, Any] = json.loads((archive / json.loads(line)["body"]).read_text())
    return line, body


def _replayed(project: Path, archive: Path, extract: str) -> list[dict[str, Any]]:
    """The records a ``capture`` collector reads back from the survey's capture log."""
    from recon.adapters._out.capture_collector import CaptureCollector
    from recon.domain.models import CollectorEntry

    log = str(archive / "captures.jsonl")
    entry = CollectorEntry(name="again", type="capture", path=log, extract=extract)
    return list(CaptureCollector(project).collect(entry, None))


def test_a_long_credential_a_server_echoes_is_never_in_the_capture(
    tmp_path: Path, remote: Any
) -> None:
    """Regression (gate B1): a credential was redacted only where it stood whole in the
    capture's JSON text, so a token the server echoed after a newline or a tab (written
    ``\\n``, ``\\t`` in JSON, joined to a letter), or after ``_``, ``-`` or a CJK letter, was
    written into the capture, in the structured content and in the JSON of the text. A
    credential of eight characters or more is redacted wherever it appears, the request
    included, and the capture is still JSON that a ``capture`` collector replays."""
    _seen, url = remote
    token = "t0kSecretValue"
    query = (
        f"intro\n{token}\tx; a\r\n{token}; NAME\t{token}; prefix_{token}; key-{token}; "
        f"トークン{token}"
    )
    given = {"Authorization": f"Bearer {token}", "X-Api-Key-Version": "1"}
    archive, [table] = _survey(
        tmp_path,
        [_call("papers", "search", arguments={"query": query}, extract="papers")],
        source=_helped(tmp_path, url, given),
        config={"preserve_raw": True},
    )
    assert table.status == "ok", table.error
    line, body = _captured(archive)
    assert token not in json.dumps(body["structuredContent"], ensure_ascii=False)
    [text] = body["content"]
    assert token not in text["text"] and "«redacted by recon»" in text["text"]
    assert token not in line, "nor does the request the capture records"
    replayed = _replayed(tmp_path, archive, "structuredContent.papers")
    assert [r["id"] for r in replayed] == ["p0", "p1"]
    assert all(r["title"].count("«redacted by recon»") == 6 for r in replayed)


@pytest.mark.parametrize("given", ["cookie", "env"])
def test_a_short_credential_blanks_no_number_boolean_or_longer_text(
    tmp_path: Path, remote: Any, given: str
) -> None:
    """Regression (gate B1-b): a short credential was redacted wherever it stood whole in
    the capture's text, so a cookie's ``consent=true`` blanked every ``true`` and a stdio
    server's ``DEBUG=1`` every ``1``, a JSON boolean or number included, and the capture was
    no longer JSON: replaying it failed. A credential under eight characters is redacted only
    where it is a whole value; the answer here holds none, so the capture is the answer."""
    answer = {"count": 1, "ok": True, "flags": [True, 1, 11], "note": "true to form, 1 of 11",
              "v": "v1"}  # fmt: skip
    if given == "cookie":
        _seen, url = remote
        source = _helped(tmp_path, url, {"Cookie": "sid=s3ss10nValue; consent=true"})
        env = None
    else:
        source = {**STDIO, "env": {"DEBUG": "env:DEBUG"}}
        env = {"DEBUG": "1"}
    archive, [table] = _survey(
        tmp_path,
        [_call("answer", "echo", arguments={"answer": answer})],
        source=source,
        env=env,
        config={"preserve_raw": True},
    )
    assert table.status == "ok", table.error
    _line, body = _captured(archive)
    assert body["structuredContent"] == answer
    [text] = body["content"]
    assert json.loads(text["text"]) == answer
    assert _replayed(tmp_path, archive, "structuredContent") == [answer]


def test_a_short_credential_is_redacted_where_it_is_a_whole_value(
    tmp_path: Path, remote: Any
) -> None:
    """A credential under eight characters is redacted where it is a whole JSON string or a
    whole query parameter's value, and nowhere inside longer text: ``the k3y is k3y`` is
    kept, and so is the text content, one string that holds the answer's JSON (SECURITY.md
    names this)."""
    _seen, url = remote
    answer = {"key": "k3y", "url": "https://x.test/q?api_key=k3y&n=1", "text": "the k3y is k3y",
              "n": 3}  # fmt: skip
    archive, [table] = _survey(
        tmp_path,
        [_call("answer", "echo", arguments={"answer": answer})],
        source=_helped(tmp_path, url, {"X-Api-Key": "k3y"}),
        config={"preserve_raw": True},
    )
    assert table.status == "ok", table.error
    _line, body = _captured(archive)
    redacted = {**answer, "key": "«redacted by recon»",
                "url": "https://x.test/q?api_key=%C2%ABredacted+by+recon%C2%BB&n=1"}  # fmt: skip
    assert body["structuredContent"] == redacted
    [text] = body["content"]
    assert json.loads(text["text"]) == answer
    assert _replayed(tmp_path, archive, "structuredContent") == [redacted]


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

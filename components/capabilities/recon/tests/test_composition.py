"""Collector types and transforms load from matrix's registry, at recon's points; a broken
plugin breaks nothing else."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, NoReturn

import pytest

from recon._matrix import COLLECTOR, TRANSFORM, Register, register, short, type_url
from recon.adapters._out.capture_files import MissionCaptureLogs
from recon.adapters._out.cli_collector import PopenRunner
from recon.adapters._out.credentials import Credentials
from recon.adapters._out.system_clock import SystemClock
from recon.adapters._out.user_agent import DEFAULT_USER_AGENT
from recon.composition import load_plugins
from recon.core.application.collectors import (
    ActCollectors,
    CollectorPorts,
    MissionPorts,
    ScopedEnv,
)
from recon.core.application.collectors import build_collectors as build
from recon.core.application.plan import build_plan
from recon.core.domain.collector import Collector, Effect, Item, Plugins, Position, Record, Run
from recon.core.domain.deadline import Deadline
from recon.core.domain.errors import CollectionError, UsageError
from recon.core.domain.http import HttpResponse
from recon.core.domain.models import ReconConfig, SourceEntry
from recon.core.domain.telemetry import Telemetry
from recon.testing import each_record, records
from tests.recon_fakes import (
    EmptyCollector,
    FakeCollector,
    ScriptedRequester,
    building,
    installed,
    run_for,
)
from tests.recon_fakes import run_survey as run

if TYPE_CHECKING:
    from pathlib import Path

    from recon.core.domain.documents import TableOutcome

#: Telemetry with no sinks, for building collectors outside a survey.
QUIET = Telemetry([], SystemClock())

#: The built-in collector types (ruling R10 folded ``web`` into ``api``).
BUILTINS = {"api", "cli", "capture", "mcp", "tail", "sse"}


#: Ports to build collectors over that reach nothing: no test here sends a request.
PORTS = CollectorPorts(
    requester=lambda telemetry, deadline, rate_limiter: ScriptedRequester({}),
    shell=PopenRunner(),
    user_agent=DEFAULT_USER_AGENT,
    clock=SystemClock(),
)


def build_collectors(
    registry: Plugins,
    mission_dir: Path,
    config: ReconConfig,
    telemetry: Telemetry,
    *,
    env: Mapping[str, str],
) -> ActCollectors:
    own = MissionPorts(
        credentials=lambda scoped, deadline: Credentials(
            scoped, mission_dir, PopenRunner(), deadline
        ),  # fmt: skip
        capture_logs=MissionCaptureLogs(mission_dir),
        roots=(mission_dir,),
    )
    return build(registry, PORTS, env, own, config, telemetry, Deadline(SystemClock()))


def test_builtins_register_at_recon_s_points_through_matrix_s_entry_point() -> None:
    """The installed package registers its own types and transforms as any extension would,
    at recon's points on matrix's registry, from its ``matrix.extensions`` entry point:
    ``mcp`` among them, the type added after the others with no edit to the plan or survey."""
    registry = load_plugins()
    assert set(registry.collector_types) >= BUILTINS
    assert "web" not in registry.collector_types, "folded into api (ruling R10)"
    assert set(registry.source_kinds) >= {"api", "file", "local", "mcp"}
    assert set(registry.converters) >= {"json", "xml", "lines", "document", "mcp_result"}
    assert {"html2text", "inverted_index", "join", "first", "markitdown"} <= set(
        registry.transforms
    )
    assert registry.broken == []


def test_a_built_in_keeps_its_short_name_and_any_other_its_type_url() -> None:
    """Ruling R3: ``type: api`` is ``recon.v1.collector.api``; a third party's type is named by
    its full type URL. A type URL's segments are kebab-case, a config's names as written."""
    assert type_url(COLLECTOR, "api") == "recon.v1.collector.api"
    assert type_url(TRANSFORM, "inverted_index") == "recon.v1.transform.inverted-index"
    assert short(TRANSFORM, "recon.v1.transform.inverted-index") == "inverted_index"
    assert short(COLLECTOR, "acme.v1.collector.sql") == "acme.v1.collector.sql"
    assert type_url(COLLECTOR, "acme.v1.collector.sql") == "acme.v1.collector.sql"


def test_each_collector_type_says_the_package_that_registered_it() -> None:
    """Polish R6: a warning about a type can name whose it is. recon's own types are
    registered by recon; one registered in-process is ``in_process``."""
    registry = load_plugins([_registers(COLLECTOR, "acme.v1.collector.good", _Good)])
    for name in BUILTINS:
        assert registry.collector_types[name].package == "recon", name
    assert registry.collector_types["acme.v1.collector.good"].package == "in_process"


class _Good(FakeCollector):
    effects = frozenset({"network"})


#: A collector type's build: what an entry at recon.collector registers.
GOOD = _Good


def _shout(value: Any) -> str:
    """Upper case."""
    return str(value).upper()


def _registers(point: str, url: str, value: object) -> Register:
    """An in-process extension registering ``value`` at ``point``: a collector type's class,
    built with no need, or what a transform's entry gives."""

    def register(registry: Any) -> None:
        build = value if isinstance(value, type) else (lambda: value)
        registry.register(point, url, build)

    return register


def test_an_extension_that_fails_to_load_is_quarantined_whole_and_others_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """matrix's rule: an extension whose registration raises is quarantined, every entry it
    registered with it, and the rest load; recon reports it, as a plugin that is broken."""
    from matrix import Extension, Registry

    def explodes(registry: Any) -> None:
        registry.register(COLLECTOR, "boom.v1.collector.half", GOOD)
        raise RuntimeError("boom on import")

    def installed() -> Registry:
        registry = Registry()
        registry.load([Extension(name="recon", origin="recon", register=register),
                       Extension(name="boom", origin="boom", register=explodes)])  # fmt: skip
        return registry

    monkeypatch.setattr("recon._matrix.default_registry", installed)
    plugins = load_plugins()
    assert (
        "api" in plugins.collector_types and "boom.v1.collector.half" not in plugins.collector_types
    )
    [broken] = plugins.broken
    assert (broken.name, broken.value) == ("boom", "boom")
    assert "RuntimeError: boom on import" in broken.error


def test_an_entry_that_builds_the_wrong_thing_is_reported_and_others_load() -> None:
    plugins = load_plugins([
        _registers(COLLECTOR, "acme.v1.collector.wrong", 42),
        _registers(TRANSFORM, "acme.v1.transform.nope", 42),
        _registers(COLLECTOR, "acme.v1.collector.good", GOOD),
    ])  # fmt: skip
    assert "acme.v1.collector.good" in plugins.collector_types
    by_name = {b.name: b.error for b in plugins.broken}
    assert set(by_name) == {"acme.v1.collector.wrong", "acme.v1.transform.nope"}
    assert "its build gave a int, which lacks" in by_name["acme.v1.collector.wrong"]


def test_a_plugin_type_declares_a_stream_through_the_point_a_built_in_uses() -> None:
    """ACES, extensible: a third party's type that never ends declares so in its
    ``records``, registered at ``recon.collector`` as ``tail`` and ``sse`` are, and the plan
    makes a stream of it with the defaults that declares."""

    class Feed(FakeCollector):
        effects = frozenset({"network"})
        records = "unbounded_source_paced"

    plugins = load_plugins([_registers(COLLECTOR, "acme.v1.collector.feed", Feed)])

    plan = build_plan({"collectors": [{"name": "ticks", "type": "acme.v1.collector.feed"}]},
                      plugins, {})  # fmt: skip

    assert plan.valid, plan.problems
    [stream] = plan.streams
    assert (stream.window.seconds, stream.overflow) == (300.0, "drop_newest")


def test_a_transform_registered_in_process_is_named_by_its_type_url() -> None:
    transforms = load_plugins([_registers(TRANSFORM, "acme.v1.transform.shout", _shout)]).transforms
    assert transforms["acme.v1.transform.shout"]("a") == "A"


def test_a_type_url_registered_twice_is_refused_naming_it() -> None:
    """matrix's rule: a type URL has one registration; an in-process one that takes an
    installed one's is refused, never silently shadowing it."""
    with pytest.raises(UsageError) as refused:
        load_plugins([_registers(COLLECTOR, "recon.v1.collector.api", GOOD)])
    assert "recon.v1.collector.api" in str(refused.value)


def _config(*collectors: dict[str, Any], catalog: Sequence[dict[str, Any]] = ()) -> ReconConfig:
    entries = list(collectors) or [{"name": "c", "type": "cli", "run": "true"}]
    return ReconConfig.model_validate({"catalog": list(catalog), "collectors": entries})


def test_a_factory_that_raises_is_reported_and_others_build(tmp_path: Path) -> None:
    from dataclasses import replace

    registry = load_plugins()

    def explode(**needs: Any) -> NoReturn:
        raise ValueError("no credentials file")

    registry.collector_types["fragile"] = replace(registry.collector_types["cli"], build=explode)
    config = _config(
        {"name": "f", "type": "fragile", "run": "true"}, {"name": "c", "type": "cli", "run": "true"}
    )
    built = build_collectors(registry, tmp_path, config, QUIET, env={})
    assert set(built.collectors) == {"cli"}
    assert any(b.name == "fragile" and "no credentials file" in b.error for b in built.failed)


def test_only_the_types_a_mission_uses_are_built(tmp_path: Path) -> None:
    """Regression: every installed type was built for every survey, so one plugin's broken
    factory was a warning in every survey, whether or not the mission used it."""
    registry = load_plugins()
    built: list[str] = []
    registry.collector_types["unused"] = installed(
        "unused", building(built, "unused"), declared=EmptyCollector()
    )
    survey = build_collectors(registry, tmp_path, _config(), QUIET, env={})
    assert set(survey.collectors) == {"cli"} and survey.failed == [] and built == []


# --- env: a collector type sees only the variables its sources declare ------------------

PROCESS_ENV = {
    "S2_KEY": "s2-secret",
    "DB_TOKEN": "db-secret",
    "AWS_SECRET_ACCESS_KEY": "not for any collector",
    "HOME": "/home/someone",
}


def _source(name: str, env: str) -> dict[str, Any]:
    return {"name": name, "url": f"https://{name}.example.test",
            "auth": {"param": "k", "token": f"env:{env}"}}  # fmt: skip


CATALOG = [_source("s2", "S2_KEY"), _source("db", "DB_TOKEN"), _source("soon", "NOT_SET")]


def _plugin_env(tmp_path: Path, *collectors: dict[str, Any]) -> Mapping[str, str]:
    """The ``env`` need a plugin type named ``probe`` is handed for this mission."""
    handed: dict[str, Mapping[str, str]] = {}

    def create(*, env: Mapping[str, str]) -> Collector:
        handed["env"] = env
        return EmptyCollector()

    registry = load_plugins()
    registry.collector_types["probe"] = installed(
        "probe", create, needs={"env"}, declared=EmptyCollector()
    )
    build_collectors(
        registry,
        tmp_path,
        _config(*collectors, catalog=CATALOG),
        QUIET,
        env=PROCESS_ENV,
    )
    return handed["env"]


def test_a_collector_type_is_handed_only_what_its_sources_declare(tmp_path: Path) -> None:
    """It used to be handed the whole process environment: every credential on the machine,
    whatever the mission's config named."""
    env = _plugin_env(
        tmp_path,
        {"name": "papers", "type": "api", "source": "s2", "endpoint": "/x"},
        {"name": "rows", "type": "probe", "source": "db"},
    )
    assert dict(env) == {"DB_TOKEN": "db-secret"}
    assert env["DB_TOKEN"] == "db-secret"


def test_a_collector_with_no_pinned_source_is_granted_every_catalog_source(tmp_path: Path) -> None:
    """No ``source:`` fans out over the catalog, so those are the sources it runs against."""
    env = _plugin_env(tmp_path, {"name": "rows", "type": "probe"})
    assert dict(env) == {"S2_KEY": "s2-secret", "DB_TOKEN": "db-secret"}


def test_a_declared_variable_that_is_unset_is_simply_absent(tmp_path: Path) -> None:
    env = _plugin_env(tmp_path, {"name": "rows", "type": "probe", "source": "soon"})
    assert env.get("NOT_SET") is None
    assert env.get("NOT_SET", "") == ""
    assert "NOT_SET" not in env


@pytest.mark.parametrize(
    "read",
    [
        lambda env: env["HOME"],
        lambda env: env.get("HOME"),
        lambda env: env.get("AWS_SECRET_ACCESS_KEY", ""),
        lambda env: "S2_KEY" in env,
    ],
    ids=["getitem", "get", "get-default", "contains"],
)
def test_reading_an_undeclared_variable_fails_loudly_with_a_fix(
    tmp_path: Path, read: Callable[[Mapping[str, str]], object]
) -> None:
    """Silence here would look like an unset variable: a request sent without its key."""
    env = _plugin_env(tmp_path, {"name": "rows", "type": "probe", "source": "db"})
    with pytest.raises(CollectionError) as excinfo:
        read(env)
    assert excinfo.value.kind == "config"
    assert "probe" in str(excinfo.value)
    assert "auth.token: env:" in (excinfo.value.fix or "")


def _reading_home(*, env: Mapping[str, str]) -> Collector:
    _ = env["HOME"]
    return EmptyCollector()


def test_a_factory_that_reads_an_undeclared_variable_is_reported_with_the_fix(
    tmp_path: Path,
) -> None:
    registry = load_plugins()
    registry.collector_types["eager"] = installed(
        "eager", _reading_home, needs={"env"}, declared=EmptyCollector()
    )
    config = _config({"name": "e", "type": "eager"})
    built = build_collectors(registry, tmp_path, config, QUIET, env=PROCESS_ENV)
    assert "eager" not in built.collectors
    [broken] = [b for b in built.failed if b.name == "eager"]
    assert "'HOME'" in broken.error and "auth.token: env:HOME" in broken.error


def test_the_api_collector_still_reads_its_source_s_key(tmp_path: Path) -> None:
    from recon.adapters._out.api_collector import ApiCollector

    class Requester:
        params: dict[str, str] | None = None

        def request(
            self,
            source: SourceEntry,
            method: str,
            url: str,
            *,
            params: dict[str, str] | None = None,
            headers: dict[str, str] | None = None,
            json_body: dict[str, Any] | None = None,
        ) -> HttpResponse:
            self.params = params
            return HttpResponse(
                status_code=200, headers={}, body=b"[]", text="[]", url=url, content_type=""
            )

        def get(
            self, source: SourceEntry, url: str, *, headers: dict[str, str] | None = None
        ) -> HttpResponse:
            return self.request(source, "GET", url, params={}, headers=headers)

    requester = Requester()
    entry = {"name": "p", "type": "api", "source": "s2", "endpoint": "/x"}
    config = _config(entry, catalog=CATALOG)
    collector = ApiCollector(
        requester=requester,
        credentials=Credentials(ScopedEnv(PROCESS_ENV, {"S2_KEY"}, "api")),
        user_agent=DEFAULT_USER_AGENT,
    )
    records(collector, run_for(config.collectors[0], SourceEntry.model_validate(CATALOG[0])))
    assert requester.params == {"k": "s2-secret"}


# --- $markitdown: reads only what the mission declares; fetches nothing ----------------


def _markitdown_table(
    tmp_path: Path, named: str, catalog: Sequence[dict[str, Any]] = ()
) -> tuple[TableOutcome, list[dict[str, Any]]]:
    """Survey one cli collector whose only record names ``named``, converted by $markitdown."""
    config = _config(
        {
            "name": "docs",
            "type": "cli",
            "run": f"echo '{named}'",
            "normalize": {"path": "line", "text": "line|$markitdown"},
        },
        catalog=catalog,
    )
    archive, [table] = run(config, tmp_path)
    path = archive / f"{table.name}.jsonl"
    lines = path.read_text().splitlines() if path.exists() else []
    return table, [json.loads(line) for line in lines]


def test_markitdown_converts_a_file_under_a_declared_local_source(tmp_path: Path) -> None:
    here = tmp_path / "here"
    here.mkdir()
    (here / "data.csv").write_text("name,city\nAlice,Paris\n")
    catalog = [{"name": "here", "type": "local", "url": str(here)}]

    table, rows = _markitdown_table(tmp_path, str(here / "data.csv"), catalog)

    assert table.status == "ok", table
    assert "Alice" in rows[0]["text"]


def test_markitdown_reads_a_path_relative_to_the_directory_its_command_ran_in(
    tmp_path: Path,
) -> None:
    """Regression: a relative path a ``cli`` command printed was resolved against recon's
    working directory, not the ``local`` source the command ran in, so it named another file
    or none, and was refused."""
    here = tmp_path / "here"
    here.mkdir()
    (here / "data.csv").write_text("name,city\nAlice,Paris\n")
    catalog = [{"name": "here", "type": "local", "url": str(here)}]

    table, rows = _markitdown_table(tmp_path, "data.csv", catalog)

    assert table.status == "ok", table
    assert "Alice" in rows[0]["text"]


def test_markitdown_does_not_read_a_file_the_mission_did_not_declare(tmp_path: Path) -> None:
    """A record names the path, and records are whatever a source returned: before this
    was confined, a collected `/etc/...` or `~/.ssh/...` was read into the table."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "secret.csv").write_text("key,value\ntoken,hunter2\n")

    table, rows = _markitdown_table(tmp_path, str(elsewhere / "secret.csv"))

    assert table.status == "error", rows
    assert table.error is not None
    assert table.error.kind == "config"
    assert "local" in (table.error.fix or "")
    assert rows == []


def test_markitdown_does_not_fetch_a_url_a_record_names(tmp_path: Path) -> None:
    """The fetch would bypass the requester, its rate limit and the capture log, and the
    plan showed no network effect for it. Served from localhost; nothing must arrive."""
    import http.server
    import threading

    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            body = b"<html><body><p>fetched</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/doc.html"
        table, rows = _markitdown_table(tmp_path, url)
    finally:
        server.shutdown()
        server.server_close()

    assert hits == []
    assert table.status == "error", rows
    assert table.error is not None
    assert table.error.kind == "config"
    assert "response_format: document" in (table.error.fix or "")


# --- normalize: is applied to every collector type's records ---------------------------


def _returning(collector: Collector) -> Callable[..., Collector]:
    def create(**needs: Any) -> Collector:
        return collector

    return create


class _RawCollector(FakeCollector):
    """A plugin collector that gives what it read and never looks at ``entry.normalize``:
    there is no way for it to (ruling R-NS)."""

    effects: frozenset[Effect] | None = frozenset()

    def __init__(self) -> None:
        self.closed = False

    def collect(self, run: Run, resume: Position | None) -> Iterator[Item]:
        try:
            yield Record({"Title": "A", "meta": {"year": 2020}})
            yield Record({"Title": "B", "meta": {"year": 2021}})
        finally:
            self.closed = True


def _registry_with(name: str, collector: Collector) -> Plugins:
    registry = load_plugins()
    registry.collector_types[name] = installed(name, _returning(collector))
    return registry


def _survey(
    registry: Plugins, project: Path, collector_entry: dict[str, Any]
) -> list[dict[str, Any]]:
    config = ReconConfig.model_validate({"collectors": [collector_entry]})
    archive, results = run(config, project, plugins=registry)
    assert [r.status for r in results] == ["ok"], results
    table = archive / f"{collector_entry['name']}.jsonl"
    return [json.loads(line) for line in table.read_text().splitlines()]


NORMALIZE = {"title": "Title", "year": "meta.year"}


def test_a_plugin_collector_that_ignores_normalize_still_has_it_applied(tmp_path: Path) -> None:
    """The plan validates ``normalize:`` for every type, so the survey must honour it for
    every type. A plugin that forgot to apply it used to write raw records, silently."""
    registry = _registry_with("raw", _RawCollector())
    rows = _survey(registry, tmp_path, {"name": "t", "type": "raw", "normalize": NORMALIZE})
    assert rows == [{"title": "A", "year": 2020}, {"title": "B", "year": 2021}]


def test_without_a_normalize_spec_a_plugin_collector_s_records_pass_through(tmp_path: Path) -> None:
    registry = _registry_with("raw", _RawCollector())
    rows = _survey(registry, tmp_path, {"name": "t", "type": "raw"})
    assert rows[0] == {"Title": "A", "meta": {"year": 2020}}


def test_the_built_in_types_are_not_normalized_twice(tmp_path: Path) -> None:
    entry = {
        "name": "t",
        "type": "cli",
        "run": """echo '{"Title": "A", "meta": {"year": 2020}}'""",
        "normalize": NORMALIZE,
    }
    assert _survey(load_plugins(), tmp_path, entry) == [{"title": "A", "year": 2020}]


def test_stopping_early_closes_the_plugin_s_generator(tmp_path: Path) -> None:
    """recon's normalize: must not stand between a plugin and its cleanup (a held
    connection)."""
    raw = _RawCollector()
    registry = _registry_with("raw", raw)
    config = ReconConfig.model_validate(
        {"collectors": [{"name": "t", "type": "raw", "normalize": NORMALIZE}]}
    )
    collector = build_collectors(registry, tmp_path, config, QUIET, env={}).collectors["raw"]
    run_ = run_for(config.collectors[0], plugins=registry)

    made = each_record(collector, run_, registry)
    assert isinstance(made, Generator)
    assert next(made) == {"title": "A", "year": 2020}
    made.close()

    assert raw.closed


# --- what a collector type is handed, and what a survey ends ---------------------------


def test_a_collector_type_s_spans_nest_under_its_table(tmp_path: Path) -> None:
    """The ``telemetry`` need is the act's, so a type that does its own I/O (an MCP call) is
    traced where its table is, not left out of the event log."""

    class Traced(FakeCollector):
        def __init__(self, *, telemetry: Telemetry) -> None:
            self._telemetry = telemetry

        def collect(self, run: Run, resume: Position | None) -> Iterator[Item]:
            with self._telemetry.span("acme.call", {"acme.op": "list"}):
                yield Record({"a": 1})

    registry = load_plugins()
    registry.collector_types["traced"] = installed("traced", Traced, needs={"telemetry"})
    archive, [table] = run({"collectors": [{"name": "t", "type": "traced"}]}, tmp_path,
                           plugins=registry)  # fmt: skip
    assert table.status == "ok"
    events = [json.loads(line) for line in (archive / "telemetry.jsonl").read_text().splitlines()]
    collect = next(e for e in events if e["name"] == "recon.collect.start")
    call = next(e for e in events if e["name"] == "acme.call.start")
    assert call["parent"] == collect["span"] and call["attributes"] == {"acme.op": "list"}


class _Server(FakeCollector):
    """A collector type that starts one stdio server (``cat``) on its first table, asks it
    for every table after, and stops it in ``close()``."""

    started: list[subprocess.Popen[str]] = []
    closed: list[subprocess.Popen[str] | None] = []

    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None

    def collect(self, run: Run, resume: Position | None) -> Iterator[Item]:
        entry = run.entry
        if self._process is None:
            self._process = subprocess.Popen(
                ["cat"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
            )
            _Server.started.append(self._process)
        assert self._process.stdin is not None and self._process.stdout is not None
        self._process.stdin.write(f"{entry.name}\n")
        self._process.stdin.flush()
        line = self._process.stdout.readline().strip()
        if line == "boom":
            raise KeyboardInterrupt
        yield Record({"echo": line, "pid": self._process.pid})

    def close(self) -> None:
        if self._process is None:
            return
        _Server.closed.append(self._process)
        self._process.terminate()
        self._process.wait()


def _server_registry() -> Plugins:
    _Server.started.clear()
    _Server.closed.clear()
    registry = load_plugins()
    registry.collector_types["stdio"] = installed("stdio", _Server, declared=EmptyCollector())
    return registry


def test_a_collector_keeps_a_stdio_server_across_its_tables_and_recon_ends_it(
    tmp_path: Path,
) -> None:
    """A collector may hold a session across its tables (the prerequisite of an MCP
    collector): one server serves every table, and recon closes the collector once, when
    the survey ends."""
    config = {"collectors": [{"name": "one", "type": "stdio"}, {"name": "two", "type": "stdio"},
                             {"name": "bad", "type": "cli", "run": "exit 3"}]}  # fmt: skip
    archive, tables = run(config, tmp_path, plugins=_server_registry())
    assert [t.status for t in tables] == ["ok", "ok", "error"]
    rows = [json.loads((archive / f"{t}.jsonl").read_text()) for t in ("one", "two")]
    assert [r["echo"] for r in rows] == ["one", "two"]
    assert rows[0]["pid"] == rows[1]["pid"], "one server for both tables"
    [process] = _Server.started
    assert _Server.closed == [process] and process.poll() is not None


def test_a_survey_cut_short_still_ends_its_collectors_sessions(tmp_path: Path) -> None:
    config = {"collectors": [{"name": "one", "type": "stdio"}, {"name": "boom", "type": "stdio"}]}
    with pytest.raises(KeyboardInterrupt):
        run(config, tmp_path, plugins=_server_registry())
    [process] = _Server.started
    assert _Server.closed == [process] and process.poll() is not None


def test_a_close_that_raises_is_a_warning_and_the_rest_are_closed(tmp_path: Path) -> None:
    class Fails(FakeCollector):
        def collect(self, run: Run, resume: Position | None) -> Iterator[Item]:
            yield Record({"a": 1})

        def close(self) -> None:
            raise OSError("socket already gone")

    registry = _server_registry()
    registry.collector_types["fails"] = installed(
        "fails", _returning(Fails()), declared=EmptyCollector()
    )
    config = {"collectors": [{"name": "f", "type": "fails"}, {"name": "s", "type": "stdio"}]}
    from recon.composition import root

    doc_root = tmp_path / ".recon" / "m"
    doc_root.mkdir(parents=True)
    import yaml

    (doc_root / "config.yaml").write_text(yaml.safe_dump(config))
    document = root({}, project=tmp_path, plugins=registry).observation.survey("m")
    [warning] = [w for w in document.warnings if w["kind"] == "plugin_broken"]
    assert "close() raised OSError: socket already gone" in warning["message"]
    assert len(_Server.closed) == 1

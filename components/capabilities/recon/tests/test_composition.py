"""Collector types and transforms load from entry points; a broken plugin breaks nothing else."""

import json
import subprocess
import sys
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Any, NoReturn

import pytest

from recon.adapters._out.cli_collector import PopenRunner
from recon.adapters._out.system_clock import SystemClock
from recon.adapters._out.user_agent import DEFAULT_USER_AGENT
from recon.application.collectors import (
    COLLECTORS_GROUP,
    CollectorPorts,
    ScopedEnv,
    SurveyCollectors,
)
from recon.application.collectors import build_collectors as build
from recon.application.plan import build_plan
from recon.composition import (
    TRANSFORMS_GROUP,
    Discover,
    load_collector_types,
    load_plugins,
    load_transforms,
)
from recon.domain.capture import CaptureLog
from recon.domain.collector import Collector, CollectorContext, CollectorType, Plugins
from recon.domain.deadline import Deadline
from recon.domain.documents import TableOutcome
from recon.domain.errors import CollectionError
from recon.domain.http import HttpResponse
from recon.domain.models import CollectorEntry, ReconConfig, SourceEntry
from recon.domain.telemetry import Telemetry
from tests.recon_fakes import EmptyCollector, NoConverter, ScriptedRequester, building
from tests.recon_fakes import run_survey as run

#: Telemetry with no sinks, for building collectors outside a survey.
QUIET = Telemetry([], SystemClock())


#: Ports to build collectors over that reach nothing: no test here sends a request.
PORTS = CollectorPorts(
    requester=lambda telemetry, deadline, rate_limiter: ScriptedRequester({}),
    converter=NoConverter(),
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
) -> SurveyCollectors:
    return build(registry, PORTS, env, mission_dir, config, telemetry, Deadline(SystemClock()))


def test_builtins_register_through_the_entry_point_groups() -> None:
    """The installed package declares its own types and transforms like any plugin would:
    ``mcp`` among them, the type added after the others with no edit to the plan or survey."""
    registry = load_plugins()
    assert {"api", "cli", "web", "capture", "mcp", "tail", "sse"} <= set(registry.collector_types)
    assert {"html2text", "inverted_index", "join", "first", "markitdown"} <= set(
        registry.transforms
    )
    assert registry.broken == []


def test_each_collector_type_says_the_package_that_registered_it(plugin_module: None) -> None:
    """Polish R6: a warning about a type can name whose it is. recon's own types are
    registered by recon; one whose entry point has no package found is absent."""
    registry = load_plugins()
    for name in ("api", "cli", "web", "capture", "mcp", "tail", "sse"):
        assert registry.collector_packages[name] == "recon", name

    unpackaged = load_plugins(_discover([("good", "recon_test_plugins:good", COLLECTORS_GROUP)]))
    assert "good" not in unpackaged.collector_packages


def test_the_types_that_read_extract_say_so() -> None:
    """A survey suggests a missing ``extract:`` only for a type that takes one."""
    types = load_plugins().collector_types
    assert {name for name in ("api", "cli", "web", "capture") if types[name].extracts} == {
        "api",
        "capture",
    }


@pytest.fixture
def plugin_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A throwaway module on sys.path holding a good type, a bad object, and a raiser."""
    (tmp_path / "recon_test_plugins.py").write_text(
        "from recon.domain.collector import CollectorType\n"
        "good = CollectorType(create=lambda ctx: object(), effects=frozenset({'network'}))\n"
        "not_a_type = 42\n"
        "def shout(v):\n    return str(v).upper()\n"
    )
    (tmp_path / "recon_test_explodes.py").write_text("raise RuntimeError('boom on import')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield
    for name in ("recon_test_plugins", "recon_test_explodes"):
        sys.modules.pop(name, None)


def _discover(entries: list[tuple[str, str, str]]) -> Discover:
    def discover(group: str) -> list[EntryPoint]:
        return [EntryPoint(name=n, value=v, group=g) for n, v, g in entries if g == group]

    return discover


def test_a_plugin_that_raises_on_import_is_reported_and_others_load(plugin_module: None) -> None:
    discover = _discover(
        [
            ("good", "recon_test_plugins:good", COLLECTORS_GROUP),
            ("boom", "recon_test_explodes:anything", COLLECTORS_GROUP),
            ("wrong", "recon_test_plugins:not_a_type", COLLECTORS_GROUP),
            ("missing", "no_such_module_anywhere:x", COLLECTORS_GROUP),
        ]
    )
    types, broken = load_collector_types(discover)

    assert set(types) == {"good"}
    assert isinstance(types["good"], CollectorType)
    by_name = {b.name: b for b in broken}
    assert set(by_name) == {"boom", "wrong", "missing"}
    assert "RuntimeError: boom on import" in by_name["boom"].error
    assert "expected a recon.domain.collector.CollectorType" in by_name["wrong"].error
    assert "ModuleNotFoundError" in by_name["missing"].error


def test_a_plugin_type_declares_a_stream_through_the_entry_point_a_built_in_uses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ACES, extensible: a third party's type that never ends declares ``unbounded`` and
    ``paced_by`` on its ``CollectorType``, registered under ``recon.collectors`` as ``tail``
    and ``sse`` are, and the plan makes a stream of it with the defaults those declare."""
    (tmp_path / "recon_test_feed.py").write_text(
        "from recon.domain.collector import CollectorType\n"
        "feed = CollectorType(create=lambda ctx: object(), effects=frozenset({'network'}),\n"
        "                     unbounded=True, paced_by='source')\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        types, broken = load_collector_types(
            _discover([("feed", "recon_test_feed:feed", COLLECTORS_GROUP)])
        )
    finally:
        sys.modules.pop("recon_test_feed", None)
    assert broken == []
    installed = load_plugins()
    plugins = Plugins({**installed.collector_types, **types}, installed.transforms, [])

    plan = build_plan({"collectors": [{"name": "ticks", "type": "feed"}]}, plugins, {})

    assert plan.valid, plan.problems
    [stream] = plan.streams
    assert (stream.window.seconds, stream.overflow) == (300.0, "drop_newest")


def test_transforms_load_by_name_and_reject_non_callables(plugin_module: None) -> None:
    discover = _discover(
        [
            ("shout", "recon_test_plugins:shout", TRANSFORMS_GROUP),
            ("nope", "recon_test_plugins:not_a_type", TRANSFORMS_GROUP),
        ]
    )
    transforms, broken = load_transforms(discover)
    assert transforms["shout"]("a") == "A"
    assert [b.name for b in broken] == ["nope"]


def test_duplicate_name_is_reported_not_silently_shadowed(plugin_module: None) -> None:
    discover = _discover(
        [
            ("dup", "recon_test_plugins:good", COLLECTORS_GROUP),
            ("dup", "recon_test_plugins:good", COLLECTORS_GROUP),
        ]
    )
    types, broken = load_collector_types(discover)
    assert set(types) == {"dup"}
    assert len(broken) == 1 and "already registered" in broken[0].error


def _config(*collectors: dict[str, Any], catalog: Sequence[dict[str, Any]] = ()) -> ReconConfig:
    entries = list(collectors) or [{"name": "c", "type": "cli", "run": "true"}]
    return ReconConfig.model_validate({"catalog": list(catalog), "collectors": entries})


def test_a_factory_that_raises_is_reported_and_others_build(tmp_path: Path) -> None:
    from dataclasses import replace

    registry = load_plugins()

    def explode(ctx: CollectorContext) -> NoReturn:
        raise ValueError("no credentials file")

    registry.collector_types["fragile"] = replace(registry.collector_types["cli"], create=explode)
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
    registry.collector_types["unused"] = CollectorType(
        create=building(built, "unused"), effects=None
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
    return {"name": name, "url": f"https://{name}.example.test", "auth": {"param": "k", "env": env}}


CATALOG = [_source("s2", "S2_KEY"), _source("db", "DB_TOKEN"), _source("soon", "NOT_SET")]


def _plugin_env(tmp_path: Path, *collectors: dict[str, Any]) -> Mapping[str, str]:
    """The ``ctx.env`` a plugin type named ``probe`` is handed for this mission."""
    handed: dict[str, Mapping[str, str]] = {}

    def create(ctx: CollectorContext) -> Collector:
        handed["env"] = ctx.env
        return EmptyCollector()

    registry = load_plugins()
    registry.collector_types["probe"] = CollectorType(create=create, effects=None)
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
    assert "auth.env" in (excinfo.value.fix or "")


def _reading_home(ctx: CollectorContext) -> Collector:
    _ = ctx.env["HOME"]
    return EmptyCollector()


def test_a_factory_that_reads_an_undeclared_variable_is_reported_with_the_fix(
    tmp_path: Path,
) -> None:
    registry = load_plugins()
    registry.collector_types["eager"] = CollectorType(create=_reading_home, effects=None)
    config = _config({"name": "e", "type": "eager"})
    built = build_collectors(registry, tmp_path, config, QUIET, env=PROCESS_ENV)
    assert "eager" not in built.collectors
    [broken] = [b for b in built.failed if b.name == "eager"]
    assert "'HOME'" in broken.error and "auth.env" in broken.error


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
        requester, ScopedEnv(PROCESS_ENV, {"S2_KEY"}, "api"), DEFAULT_USER_AGENT
    )
    list(collector.collect(config.collectors[0], SourceEntry.model_validate(CATALOG[0])))
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
    path = archive / table.file
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
    assert "web" in (table.error.fix or "")


# --- normalize: is applied to every collector type's records ---------------------------


def _returning(collector: Collector) -> Callable[[CollectorContext], Collector]:
    def create(ctx: CollectorContext) -> Collector:
        return collector

    return create


class _RawCollector:
    """A plugin collector that yields what it read and never looks at ``entry.normalize``."""

    def __init__(self) -> None:
        self.closed = False

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
    ) -> Iterator[dict[str, Any]]:
        try:
            yield {"Title": "A", "meta": {"year": 2020}}
            yield {"Title": "B", "meta": {"year": 2021}}
        finally:
            self.closed = True


def _registry_with(name: str, collector: Collector, **fields: Any) -> Plugins:
    registry = load_plugins()
    registry.collector_types[name] = CollectorType(
        create=_returning(collector), effects=frozenset(), **fields
    )
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


def test_a_type_that_normalizes_itself_is_not_normalized_twice(tmp_path: Path) -> None:
    """Applying the spec to already-normalized records would null every column."""

    class Own:
        def collect(
            self,
            entry: CollectorEntry,
            source: SourceEntry | None,
            *,
            captures: CaptureLog | None = None,
        ) -> Iterator[dict[str, Any]]:
            yield {"title": "already normalized", "year": 1999}

    registry = _registry_with("own", Own(), normalizes=True)
    rows = _survey(registry, tmp_path, {"name": "t", "type": "own", "normalize": NORMALIZE})
    assert rows == [{"title": "already normalized", "year": 1999}]


def test_the_built_in_types_are_not_normalized_twice(tmp_path: Path) -> None:
    entry = {
        "name": "t",
        "type": "cli",
        "run": """echo '{"Title": "A", "meta": {"year": 2020}}'""",
        "normalize": NORMALIZE,
    }
    assert _survey(load_plugins(), tmp_path, entry) == [{"title": "A", "year": 2020}]


def test_stopping_early_closes_the_plugin_s_generator(tmp_path: Path) -> None:
    """Normalizing must not stand between a plugin and its cleanup (a held connection)."""
    raw = _RawCollector()
    registry = _registry_with("raw", raw)
    config = ReconConfig.model_validate(
        {"collectors": [{"name": "t", "type": "raw", "normalize": NORMALIZE}]}
    )
    collector = build_collectors(registry, tmp_path, config, QUIET, env={}).collectors["raw"]
    entry = config.collectors[0]

    records = collector.collect(entry, None)
    assert isinstance(records, Generator)
    assert next(records) == {"title": "A", "year": 2020}
    records.close()

    assert raw.closed


# --- what a collector type is handed, and what a survey ends ---------------------------


def test_a_collector_type_s_spans_nest_under_its_table(tmp_path: Path) -> None:
    """``CollectorContext`` carries the survey's telemetry, so a type that does its own I/O
    (an MCP call) is traced where its table is, not left out of the event log."""

    class Traced:
        def __init__(self, ctx: CollectorContext) -> None:
            self._telemetry = ctx.telemetry

        def collect(
            self,
            entry: CollectorEntry,
            source: SourceEntry | None,
            *,
            captures: CaptureLog | None = None,
        ) -> Iterator[dict[str, Any]]:
            with self._telemetry.span("acme.call", {"acme.op": "list"}):
                yield {"a": 1}

    registry = load_plugins()
    registry.collector_types["traced"] = CollectorType(create=Traced, effects=None)
    archive, [table] = run({"collectors": [{"name": "t", "type": "traced"}]}, tmp_path,
                           plugins=registry)  # fmt: skip
    assert table.status == "ok"
    events = [json.loads(line) for line in (archive / "telemetry.jsonl").read_text().splitlines()]
    collect = next(e for e in events if e["name"] == "recon.collect.start")
    call = next(e for e in events if e["name"] == "acme.call.start")
    assert call["parent"] == collect["span"] and call["attributes"] == {"acme.op": "list"}


class _Server:
    """A collector type that starts one stdio server (``cat``) on its first table, asks it
    for every table after, and stops it in ``close()``."""

    started: list[subprocess.Popen[str]] = []
    closed: list[subprocess.Popen[str] | None] = []

    def __init__(self, ctx: CollectorContext) -> None:
        self._process: subprocess.Popen[str] | None = None

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
    ) -> Iterator[dict[str, Any]]:
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
        yield {"echo": line, "pid": self._process.pid}

    def close(self) -> None:
        assert self._process is not None
        _Server.closed.append(self._process)
        self._process.terminate()
        self._process.wait()


def _server_registry() -> Plugins:
    _Server.started.clear()
    _Server.closed.clear()
    registry = load_plugins()
    registry.collector_types["stdio"] = CollectorType(create=_Server, effects=None)
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
    class Fails:
        def collect(
            self,
            entry: CollectorEntry,
            source: SourceEntry | None,
            *,
            captures: CaptureLog | None = None,
        ) -> Iterator[dict[str, Any]]:
            yield {"a": 1}

        def close(self) -> None:
            raise OSError("socket already gone")

    registry = _server_registry()
    registry.collector_types["fails"] = CollectorType(create=_returning(Fails()), effects=None)
    config = {"collectors": [{"name": "f", "type": "fails"}, {"name": "s", "type": "stdio"}]}
    from recon.composition import root

    doc_root = tmp_path / ".recon" / "m"
    doc_root.mkdir(parents=True)
    import yaml

    (doc_root / "config.yaml").write_text(yaml.safe_dump(config))
    document = root({}, project=tmp_path, plugins=registry).survey("m")
    [warning] = [w for w in document.warnings if w["kind"] == "plugin_broken"]
    assert "close() raised OSError: socket already gone" in warning["message"]
    assert len(_Server.closed) == 1

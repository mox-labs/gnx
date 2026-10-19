"""Collector types and transforms load from entry points; a broken plugin breaks nothing else."""

import json
import sys
from importlib.metadata import EntryPoint

import pytest

from recon.adapters._out.system_clock import SystemClock
from recon.composition import (
    COLLECTORS_GROUP,
    DEFAULT_USER_AGENT,
    TRANSFORMS_GROUP,
    build_collectors,
    load_collector_types,
    load_plugins,
    load_transforms,
)
from recon.domain.collector import CollectorType
from recon.domain.errors import CollectionError
from recon.domain.models import ReconConfig
from recon.domain.telemetry import Telemetry
from tests.recon_fakes import run_survey as run

#: Telemetry with no sinks, for building collectors outside a survey.
QUIET = Telemetry([], SystemClock())


def test_builtins_register_through_the_entry_point_groups():
    """The installed package declares its own types and transforms like any plugin would."""
    registry = load_plugins()
    assert {"api", "cli", "web", "capture"} <= set(registry.collector_types)
    assert {"html2text", "inverted_index", "join", "first", "markitdown"} <= set(
        registry.transforms
    )
    assert registry.broken == []


def test_the_types_that_read_extract_say_so():
    """A survey suggests a missing ``extract:`` only for a type that takes one."""
    types = load_plugins().collector_types
    assert {name for name in ("api", "cli", "web", "capture") if types[name].extracts} == {
        "api",
        "capture",
    }


@pytest.fixture
def plugin_module(tmp_path, monkeypatch):
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


def _discover(entries):
    def discover(group):
        return [EntryPoint(name=n, value=v, group=g) for n, v, g in entries if g == group]

    return discover


def test_a_plugin_that_raises_on_import_is_reported_and_others_load(plugin_module):
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


def test_transforms_load_by_name_and_reject_non_callables(plugin_module):
    discover = _discover(
        [
            ("shout", "recon_test_plugins:shout", TRANSFORMS_GROUP),
            ("nope", "recon_test_plugins:not_a_type", TRANSFORMS_GROUP),
        ]
    )
    transforms, broken = load_transforms(discover)
    assert transforms["shout"]("a") == "A"
    assert [b.name for b in broken] == ["nope"]


def test_duplicate_name_is_reported_not_silently_shadowed(plugin_module):
    discover = _discover(
        [
            ("dup", "recon_test_plugins:good", COLLECTORS_GROUP),
            ("dup", "recon_test_plugins:good", COLLECTORS_GROUP),
        ]
    )
    types, broken = load_collector_types(discover)
    assert set(types) == {"dup"}
    assert len(broken) == 1 and "already registered" in broken[0].error


def _config(*collectors, catalog=()):
    entries = list(collectors) or [{"name": "c", "type": "cli", "run": "true"}]
    return ReconConfig.model_validate({"catalog": list(catalog), "collectors": entries})


def test_a_factory_that_raises_is_reported_and_others_build(tmp_path):
    from dataclasses import replace

    registry = load_plugins()

    def explode(ctx):
        raise ValueError("no credentials file")

    registry.collector_types["fragile"] = replace(registry.collector_types["cli"], create=explode)
    collectors, failed = build_collectors(
        registry, tmp_path, _config(), QUIET, env={}, clock=SystemClock()
    )
    assert "fragile" not in collectors
    assert {"api", "cli", "web", "capture"} <= set(collectors)
    assert any(b.name == "fragile" and "no credentials file" in b.error for b in failed)


# --- env: a collector type sees only the variables its sources declare ------------------

PROCESS_ENV = {
    "S2_KEY": "s2-secret",
    "DB_TOKEN": "db-secret",
    "AWS_SECRET_ACCESS_KEY": "not for any collector",
    "HOME": "/home/someone",
}


def _source(name, env):
    return {"name": name, "url": f"https://{name}.example.test", "auth": {"param": "k", "env": env}}


CATALOG = [_source("s2", "S2_KEY"), _source("db", "DB_TOKEN"), _source("soon", "NOT_SET")]


def _plugin_env(tmp_path, *collectors):
    """The ``ctx.env`` a plugin type named ``probe`` is handed for this mission."""
    handed = {}

    def create(ctx):
        handed["env"] = ctx.env
        return object()

    registry = load_plugins()
    registry.collector_types["probe"] = CollectorType(create=create, effects=None)
    build_collectors(
        registry,
        tmp_path,
        _config(*collectors, catalog=CATALOG),
        QUIET,
        env=PROCESS_ENV,
        clock=SystemClock(),
    )
    return handed["env"]


def test_a_collector_type_is_handed_only_what_its_sources_declare(tmp_path):
    """It used to be handed the whole process environment: every credential on the machine,
    whatever the mission's config named."""
    env = _plugin_env(
        tmp_path,
        {"name": "papers", "type": "api", "source": "s2", "endpoint": "/x"},
        {"name": "rows", "type": "probe", "source": "db"},
    )
    assert dict(env) == {"DB_TOKEN": "db-secret"}
    assert env["DB_TOKEN"] == "db-secret"


def test_a_collector_with_no_pinned_source_is_granted_every_catalog_source(tmp_path):
    """No ``source:`` fans out over the catalog, so those are the sources it runs against."""
    env = _plugin_env(tmp_path, {"name": "rows", "type": "probe"})
    assert dict(env) == {"S2_KEY": "s2-secret", "DB_TOKEN": "db-secret"}


def test_a_declared_variable_that_is_unset_is_simply_absent(tmp_path):
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
def test_reading_an_undeclared_variable_fails_loudly_with_a_fix(tmp_path, read):
    """Silence here would look like an unset variable: a request sent without its key."""
    env = _plugin_env(tmp_path, {"name": "rows", "type": "probe", "source": "db"})
    with pytest.raises(CollectionError) as excinfo:
        read(env)
    assert excinfo.value.kind == "config"
    assert "probe" in str(excinfo.value)
    assert "auth.env" in excinfo.value.fix


def test_a_factory_that_reads_an_undeclared_variable_is_reported_with_the_fix(tmp_path):
    registry = load_plugins()
    registry.collector_types["eager"] = CollectorType(
        create=lambda ctx: ctx.env["HOME"], effects=None
    )
    collectors, failed = build_collectors(
        registry, tmp_path, _config(), QUIET, env=PROCESS_ENV, clock=SystemClock()
    )
    assert "eager" not in collectors
    [broken] = [b for b in failed if b.name == "eager"]
    assert "'HOME'" in broken.error and "auth.env" in broken.error


def test_the_api_collector_still_reads_its_source_s_key(tmp_path):
    from recon.adapters._out.api_collector import ApiCollector
    from recon.composition import ScopedEnv
    from recon.domain.http import HttpResponse
    from recon.domain.models import SourceEntry

    class Requester:
        def request(self, source, method, url, *, params=None, headers=None, json_body=None):
            self.params = params
            return HttpResponse(
                status_code=200, headers={}, body=b"[]", text="[]", url=url, content_type=""
            )

    requester = Requester()
    entry = {"name": "p", "type": "api", "source": "s2", "endpoint": "/x"}
    config = _config(entry, catalog=CATALOG)
    collector = ApiCollector(
        requester, ScopedEnv(PROCESS_ENV, {"S2_KEY"}, "api"), DEFAULT_USER_AGENT
    )
    list(collector.collect(config.collectors[0], SourceEntry.model_validate(CATALOG[0])))
    assert requester.params == {"k": "s2-secret"}


# --- $markitdown: reads only what the mission declares; fetches nothing ----------------


def _markitdown_table(tmp_path, named: str, catalog=()):
    """Survey one cli collector whose only record names ``named``, converted by $markitdown."""
    mission = tmp_path / "mission"
    mission.mkdir(exist_ok=True)
    config = _config(
        {
            "name": "docs",
            "type": "cli",
            "run": f"echo '{named}'",
            "normalize": {"path": "line", "text": "line|$markitdown"},
        },
        catalog=catalog,
    )
    collectors, failed = build_collectors(
        load_plugins(), mission, config, QUIET, env={}, clock=SystemClock()
    )
    archive, [table] = run(config, collectors, mission)
    path = archive / table.file
    lines = path.read_text().splitlines() if path.exists() else []
    return table, [json.loads(line) for line in lines]


def test_markitdown_converts_a_file_under_a_declared_local_source(tmp_path):
    here = tmp_path / "here"
    here.mkdir()
    (here / "data.csv").write_text("name,city\nAlice,Paris\n")
    catalog = [{"name": "here", "type": "local", "url": str(here)}]

    table, rows = _markitdown_table(tmp_path, str(here / "data.csv"), catalog)

    assert table.status == "ok", table
    assert "Alice" in rows[0]["text"]


def test_markitdown_does_not_read_a_file_the_mission_did_not_declare(tmp_path):
    """A record names the path, and records are whatever a source returned: before this
    was confined, a collected `/etc/...` or `~/.ssh/...` was read into the table."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "secret.csv").write_text("key,value\ntoken,hunter2\n")

    table, rows = _markitdown_table(tmp_path, str(elsewhere / "secret.csv"))

    assert table.status == "error", rows
    assert table.error.kind == "config"
    assert "local" in table.error.fix
    assert rows == []


def test_markitdown_does_not_fetch_a_url_a_record_names(tmp_path):
    """The fetch would bypass the requester, its rate limit and the capture log, and the
    plan showed no network effect for it. Served from localhost; nothing must arrive."""
    import http.server
    import threading

    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            body = b"<html><body><p>fetched</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
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
    assert table.error.kind == "config"
    assert "web" in table.error.fix


# --- normalize: is applied to every collector type's records ---------------------------


class _RawCollector:
    """A plugin collector that yields what it read and never looks at ``entry.normalize``."""

    def __init__(self):
        self.closed = False

    def collect(self, entry, source, *, captures=None):
        try:
            yield {"Title": "A", "meta": {"year": 2020}}
            yield {"Title": "B", "meta": {"year": 2021}}
        finally:
            self.closed = True


def _registry_with(name, collector, **fields):
    registry = load_plugins()
    registry.collector_types[name] = CollectorType(
        create=lambda ctx: collector, effects=frozenset(), **fields
    )
    return registry


def _survey(registry, mission_dir, collector_entry):
    config = ReconConfig.model_validate({"collectors": [collector_entry]})
    collectors, failed = build_collectors(
        registry, mission_dir, config, QUIET, env={}, clock=SystemClock()
    )
    archive, results = run(config, collectors, mission_dir)
    assert [r.status for r in results] == ["ok"], results
    table = archive / f"{collector_entry['name']}.jsonl"
    return [json.loads(line) for line in table.read_text().splitlines()]


NORMALIZE = {"title": "Title", "year": "meta.year"}


def test_a_plugin_collector_that_ignores_normalize_still_has_it_applied(tmp_path):
    """The plan validates ``normalize:`` for every type, so the survey must honour it for
    every type. A plugin that forgot to apply it used to write raw records, silently."""
    registry = _registry_with("raw", _RawCollector())
    rows = _survey(registry, tmp_path, {"name": "t", "type": "raw", "normalize": NORMALIZE})
    assert rows == [{"title": "A", "year": 2020}, {"title": "B", "year": 2021}]


def test_without_a_normalize_spec_a_plugin_collector_s_records_pass_through(tmp_path):
    registry = _registry_with("raw", _RawCollector())
    rows = _survey(registry, tmp_path, {"name": "t", "type": "raw"})
    assert rows[0] == {"Title": "A", "meta": {"year": 2020}}


def test_a_type_that_normalizes_itself_is_not_normalized_twice(tmp_path):
    """Applying the spec to already-normalized records would null every column."""

    class Own:
        def collect(self, entry, source, *, captures=None):
            yield {"title": "already normalized", "year": 1999}

    registry = _registry_with("own", Own(), normalizes=True)
    rows = _survey(registry, tmp_path, {"name": "t", "type": "own", "normalize": NORMALIZE})
    assert rows == [{"title": "already normalized", "year": 1999}]


def test_the_built_in_types_are_not_normalized_twice(tmp_path):
    entry = {
        "name": "t",
        "type": "cli",
        "run": """echo '{"Title": "A", "meta": {"year": 2020}}'""",
        "normalize": NORMALIZE,
    }
    assert _survey(load_plugins(), tmp_path, entry) == [{"title": "A", "year": 2020}]


def test_stopping_early_closes_the_plugin_s_generator(tmp_path):
    """Normalizing must not stand between a plugin and its cleanup (a held connection)."""
    raw = _RawCollector()
    registry = _registry_with("raw", raw)
    config = ReconConfig.model_validate(
        {"collectors": [{"name": "t", "type": "raw", "normalize": NORMALIZE}]}
    )
    collector = build_collectors(registry, tmp_path, config, QUIET, clock=SystemClock(), env={})[0][
        "raw"
    ]
    entry = config.collectors[0]

    records = collector.collect(entry, None)
    assert next(records) == {"title": "A", "year": 2020}
    records.close()

    assert raw.closed

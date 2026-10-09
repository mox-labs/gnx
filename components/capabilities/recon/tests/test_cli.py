"""The CLI contract: exit codes, JSON documents, errors on stderr, and no effects on a plan.

Every test runs in a throwaway project (a tmp dir with a `.git`), drives the real click
group through CliRunner, and uses `cli` collectors running `echo`/`printf` — no network.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from recon.adapters._in import cli as cli_module
from recon.adapters._in.cli import main
from recon.application.plan import BrokenPlugin
from recon.composition import load_registry
from recon.domain.collector import CollectorType
from recon.domain.exceptions import CollectionError
from recon.domain.type_url import is_type_url

OK_CONFIG = {
    "collectors": [
        {"name": "hello", "type": "cli", "run": 'printf \'{"a": 1}\\n{"a": 2}\\n\''},
    ]
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _mission(project: Path, name: str, config: dict | str) -> Path:
    mission = project / ".recon" / name
    mission.mkdir(parents=True)
    text = config if isinstance(config, str) else yaml.safe_dump(config)
    (mission / "config.yaml").write_text(text)
    return mission


def _invoke(*args: str, stdin: str | None = None):
    return CliRunner().invoke(main, list(args), input=stdin, catch_exceptions=False)


def _doc(result) -> dict:
    doc = json.loads(result.stdout)
    assert is_type_url(doc["schema"]), doc["schema"]
    return doc


def _error(result) -> dict:
    lines = [line for line in result.stderr.splitlines() if line.startswith("{")]
    assert len(lines) == 1, result.stderr
    return json.loads(lines[0])["error"]


# --- survey ---------------------------------------------------------------------------


class TestSurvey:
    def test_ok_json_document(self, project):
        _mission(project, "m", OK_CONFIG)
        result = _invoke("survey", "m", "--json")

        assert result.exit_code == 0, result.stderr
        doc = _doc(result)
        assert doc["schema"] == "recon.v1.survey"
        assert doc["status"] == "ok"
        assert [(t["name"], t["status"], t["records"]) for t in doc["tables"]] == [
            ("hello", "ok", 2)
        ]
        assert doc["tables"][0]["type_url"] == "recon.v1.records"
        assert Path(doc["archive"]).parent == project / ".recon" / "m" / "archive"

    def test_human_mode_prints_the_archive_path_on_stdout(self, project):
        _mission(project, "m", OK_CONFIG)
        result = _invoke("survey", "m")
        assert result.exit_code == 0
        assert Path(result.stdout.strip()).is_dir()

    def test_mission_lives_under_the_git_root_not_the_cwd(self, project, monkeypatch):
        _mission(project, "m", OK_CONFIG)
        sub = project / "deep" / "er"
        sub.mkdir(parents=True)
        monkeypatch.chdir(sub)
        assert _invoke("survey", "m", "--json").exit_code == 0
        assert not (sub / ".recon").exists()

    def test_outside_a_repository_the_cwd_is_the_root(self, tmp_path, monkeypatch):
        bare = tmp_path / "no-git-here"
        bare.mkdir()
        monkeypatch.chdir(bare)
        # tmp_path itself is not inside a repo either, so .recon lands in the cwd.
        if any((p / ".git").exists() for p in bare.parents):
            pytest.skip("tmp dir is inside a git repository")
        _mission(bare, "m", OK_CONFIG)
        assert _invoke("survey", "m").exit_code == 0

    def test_partial_failure_exits_1_and_still_writes_the_archive(self, project):
        config = {
            "collectors": [
                {"name": "good", "type": "cli", "run": "echo '{\"a\": 1}'"},
                {"name": "bad", "type": "cli", "run": "exit 7"},
            ]
        }
        _mission(project, "m", config)
        result = _invoke("survey", "m", "--json")

        assert result.exit_code == 1
        doc = _doc(result)
        assert doc["status"] == "incomplete"
        statuses = {t["name"]: t["status"] for t in doc["tables"]}
        assert statuses == {"good": "ok", "bad": "error"}
        error = _error(result)
        assert error["kind"] == "collection"
        assert error["failed"] == ["bad"]
        assert error["fix"]
        archive = Path(error["archive"])
        assert (archive / ".incomplete").exists()
        assert (archive / "good.jsonl").exists()

    def test_human_partial_failure_is_non_zero_too(self, project):
        _mission(project, "m", {"collectors": [{"name": "bad", "type": "cli", "run": "exit 7"}]})
        result = _invoke("survey", "m")
        assert result.exit_code == 1
        assert "Error:" in result.stderr

    @pytest.mark.parametrize(
        ("kinds", "code", "kind"),
        [
            (["transient"], 5, "transient"),
            (["transient", "transient"], 5, "transient"),
            (["transient", "auth"], 6, "auth"),
            (["auth", "collection"], 6, "auth"),
            (["transient", "collection"], 1, "collection"),
        ],
    )
    def test_exit_code_follows_the_failures_classification(
        self, project, monkeypatch, kinds, code, kind
    ):
        registry = load_registry()
        for k in set(kinds):
            registry.collector_types[f"fails-{k}"] = _failing_type(k)
        monkeypatch.setattr(cli_module, "load_registry", lambda: registry)
        config = {
            "collectors": [
                {"name": "ok", "type": "cli", "run": "echo '{}'"},
                *({"name": f"t{i}", "type": f"fails-{k}"} for i, k in enumerate(kinds)),
            ]
        }
        _mission(project, "m", config)
        result = _invoke("survey", "m", "--json")

        assert result.exit_code == code
        assert _error(result)["kind"] == kind
        tables = {t["name"]: t for t in _doc(result)["tables"]}
        assert tables["ok"]["status"] == "ok", "every table's status is still reported"
        assert [tables[f"t{i}"]["error"]["kind"] for i in range(len(kinds))] == kinds

    def test_no_config_is_not_found(self, project):
        result = _invoke("survey", "nope", "--json")
        assert result.exit_code == 4
        error = _error(result)
        assert error["kind"] == "not_found"
        assert "recon init nope" in error["fix"]

    def test_bad_mission_name_is_a_usage_error(self, project):
        assert _invoke("survey", "../escape").exit_code == 2

    def test_invalid_yaml_is_a_config_error(self, project):
        _mission(project, "m", "collectors: [unclosed\n")
        result = _invoke("survey", "m", "--json")
        assert result.exit_code == 3
        assert _error(result)["kind"] == "config"


def _failing_type(kind: str) -> CollectorType:
    class Failing:
        def collect(self, entry, source, *, captures=None):
            raise CollectionError(f"simulated {kind} failure", kind=kind)

    return CollectorType(create=lambda ctx: Failing(), effects=frozenset({"network"}))


# --- survey -c ------------------------------------------------------------------------


class TestSurveyConfigOverride:
    def test_does_not_overwrite_a_different_mission_config(self, project):
        mission = _mission(project, "m", OK_CONFIG)
        before = (mission / "config.yaml").read_text()
        other = project / "other.yaml"
        other.write_text(
            yaml.safe_dump({"collectors": [{"name": "x", "type": "cli", "run": "true"}]})
        )

        result = _invoke("survey", "m", "-c", str(other), "--json")

        assert result.exit_code == 3
        error = _error(result)
        assert error["kind"] == "conflict"
        assert "--replace" in error["fix"]
        assert (mission / "config.yaml").read_text() == before
        assert not (mission / "archive").exists()

    def test_replace_overwrites_and_runs(self, project):
        mission = _mission(project, "m", OK_CONFIG)
        other = project / "other.yaml"
        other.write_text(
            yaml.safe_dump({"collectors": [{"name": "x", "type": "cli", "run": "true"}]})
        )

        result = _invoke("survey", "m", "-c", str(other), "--replace", "--json")

        assert result.exit_code == 0, result.stderr
        assert (mission / "config.yaml").read_text() == other.read_text()
        assert [t["name"] for t in _doc(result)["tables"]] == ["x"]

    def test_identical_config_is_not_a_conflict(self, project):
        mission = _mission(project, "m", OK_CONFIG)
        same = project / "same.yaml"
        same.write_text((mission / "config.yaml").read_text())
        assert _invoke("survey", "m", "-c", str(same)).exit_code == 0

    def test_new_mission_is_created_from_the_file(self, project):
        cfg = project / "c.yaml"
        cfg.write_text(yaml.safe_dump(OK_CONFIG))
        assert _invoke("survey", "fresh", "-c", str(cfg)).exit_code == 0
        assert (project / ".recon" / "fresh" / "config.yaml").read_text() == cfg.read_text()


# --- survey --dry-run -----------------------------------------------------------------


class TestDryRun:
    @pytest.mark.parametrize("flag", ["--dry-run", "-n"])
    def test_plans_without_running_or_writing(self, project, monkeypatch, flag):
        marker = project / "ran"
        config = {
            "catalog": [{"name": "api", "url": "http://192.0.2.1"}],  # TEST-NET: unroutable
            "collectors": [
                {"name": "touch", "type": "cli", "run": f"touch {marker}"},
                {"name": "remote", "type": "api", "source": "api", "endpoint": "/x"},
            ],
        }
        mission = _mission(project, "m", config)

        def no_collectors(*a, **k):
            raise AssertionError("a dry run must not build collectors")

        monkeypatch.setattr(cli_module, "build_collectors", no_collectors)
        result = _invoke("survey", "m", flag, "--json")

        assert result.exit_code == 0, result.stderr
        doc = _doc(result)
        assert doc["schema"] == "recon.v1.plan"
        assert doc["valid"] is True
        runs = {r["output"]: r for r in doc["runs"]}
        assert set(runs) == {"touch-api", "remote"}
        assert runs["touch-api"]["effects"] == ["subprocess"]
        assert runs["touch-api"]["commands"] == [f"touch {marker}"]
        assert runs["remote"]["effects"] == ["network"]
        assert runs["remote"]["url"] == "http://192.0.2.1/x"
        assert doc["effects"] == ["network", "subprocess"]
        assert not marker.exists()
        assert not (mission / "archive").exists()

    def test_with_c_writes_no_config(self, project):
        cfg = project / "c.yaml"
        cfg.write_text(yaml.safe_dump(OK_CONFIG))
        result = _invoke("survey", "new", "-c", str(cfg), "-n", "--json")
        assert result.exit_code == 0
        assert _doc(result)["writes"]["config"].endswith(".recon/new/config.yaml")
        assert not (project / ".recon").exists()

    def test_invalid_plan_exits_3_listing_every_problem(self, project):
        config = {
            "collectors": [
                {"name": "a", "type": "ftp"},
                {"name": "b", "type": "cli", "run": "true", "normalize": {"t": "x|$nope"}},
                {"name": "c", "type": "cli"},
            ]
        }
        mission = _mission(project, "m", config)
        result = _invoke("survey", "m", "--dry-run", "--json")

        assert result.exit_code == 3
        error = _error(result)
        assert error["kind"] == "config"
        paths = {p["path"] for p in error["problems"]}
        assert {"collectors[0].type", "collectors[1].normalize.t", "collectors[2].run"} <= paths
        assert "--dry-run" in error["fix"]
        assert not (mission / "archive").exists()

    def test_a_real_survey_validates_first_too(self, project):
        mission = _mission(project, "m", {"collectors": [{"name": "a", "type": "ftp"}]})
        assert _invoke("survey", "m").exit_code == 3
        assert not (mission / "archive").exists()

    def test_human_plan_goes_to_stdout(self, project):
        _mission(project, "m", OK_CONFIG)
        result = _invoke("survey", "m", "-n")
        assert result.exit_code == 0
        assert "hello" in result.stdout and "nothing was fetched" in result.stdout


# --- status ---------------------------------------------------------------------------


class TestStatus:
    def test_json_lists_missions_and_plugins(self, project):
        _mission(project, "m", OK_CONFIG)
        _invoke("survey", "m")
        result = _invoke("status", "--json")

        assert result.exit_code == 0
        doc = _doc(result)
        assert doc["schema"] == "recon.v1.status"
        [mission] = doc["missions"]
        assert mission["name"] == "m" and mission["archives"] == 1
        assert mission["latest"]["state"] == "ok"
        assert mission["latest"]["format_version"] == 2
        assert mission["latest"]["tables"][0]["type_url"] == "recon.v1.records"
        types = {c["type"] for c in doc["plugins"]["collectors"]}
        assert {"api", "cli", "web", "capture"} <= types
        assert "$html2text" in doc["plugins"]["transforms"]

    def test_empty_is_still_a_wrapped_document(self, project):
        doc = _doc(_invoke("status", "--json"))
        assert doc["missions"] == []

    def test_reports_a_broken_plugin(self, project, monkeypatch):
        registry = load_registry()
        registry.broken.append(BrokenPlugin("recon.collectors", "sql", "x:y", "ImportError: z"))
        monkeypatch.setattr(cli_module, "load_registry", lambda: registry)

        doc = _doc(_invoke("status", "--json"))
        assert doc["plugins"]["broken"][0]["name"] == "sql"
        human = _invoke("status")
        assert "sql" in human.stderr and "failed to load" in human.stderr

    def test_reads_a_format_1_archive(self, project):
        mission = _mission(project, "old", OK_CONFIG)
        archive = mission / "archive" / "2026-01-01-000000-000000"
        archive.mkdir(parents=True)
        (archive / "t.jsonl").write_text('{"a": 1}\n')
        (archive / ".incomplete").write_text("")
        (archive / "meta.yaml").write_text(
            yaml.safe_dump(
                {
                    "format_version": 1,
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "collectors": [
                        {"name": "t", "status": "ok", "file": "t.jsonl", "records": 1},
                        {"name": "u", "status": "error", "error": "boom"},
                    ],
                }
            )
        )
        latest = _doc(_invoke("status", "--json"))["missions"][0]["latest"]
        assert latest["format_version"] == 1
        assert latest["state"] == "incomplete"
        tables = {t["name"]: t for t in latest["tables"]}
        assert tables["t"]["type_url"] == "recon.v1.records"
        assert tables["u"]["error"] == {"kind": "unknown", "message": "boom"}

        rows = _doc(_invoke("query", "old", "SELECT a FROM t", "--json"))["rows"]
        assert rows == [{"a": 1}]


# --- query ----------------------------------------------------------------------------


class TestQuery:
    def test_json_rows_are_wrapped(self, project):
        _mission(project, "m", OK_CONFIG)
        _invoke("survey", "m")
        result = _invoke("query", "m", "SELECT a FROM hello ORDER BY a", "--json")

        assert result.exit_code == 0
        doc = _doc(result)
        assert doc["schema"] == "recon.v1.query-rows"
        assert doc["columns"] == ["a"]
        assert doc["rows"] == [{"a": 1}, {"a": 2}]
        assert doc["count"] == 2

    def test_zero_rows_is_still_a_document(self, project):
        _mission(project, "m", OK_CONFIG)
        _invoke("survey", "m")
        doc = _doc(_invoke("query", "m", "SELECT a FROM hello WHERE a > 9", "--json"))
        assert doc["rows"] == [] and doc["count"] == 0

    def test_bad_sql_exits_3_naming_the_tables(self, project):
        _mission(project, "m", OK_CONFIG)
        _invoke("survey", "m")
        result = _invoke("query", "m", "SELECT * FROM nope", "--json")
        assert result.exit_code == 3
        error = _error(result)
        assert error["kind"] == "query"
        assert "hello" in error["fix"]

    def test_no_archive_is_not_found(self, project):
        _mission(project, "m", OK_CONFIG)
        result = _invoke("query", "m", "SELECT 1", "--json")
        assert result.exit_code == 4
        assert _error(result)["fix"] == "recon survey m"

    def test_unknown_run_is_not_found(self, project):
        _mission(project, "m", OK_CONFIG)
        _invoke("survey", "m")
        result = _invoke("query", "m", "SELECT 1", "--run", "1999-01-01", "--json")
        assert result.exit_code == 4
        assert "have:" in _error(result)["message"]


# --- templates / init -----------------------------------------------------------------


class TestTemplatesAndInit:
    def test_templates_json(self, project):
        doc = _doc(_invoke("templates", "--json"))
        assert doc["schema"] == "recon.v1.templates"
        names = {t["name"] for t in doc["templates"]}
        assert {"code-forensics", "mcp-captures"} <= names
        assert all(t["description"] for t in doc["templates"])

    def test_every_template_plans_clean_but_for_its_placeholders(self, project):
        """A scaffold must not hand out a broken config. The only problems a template may
        have are the placeholders it asks the user to fill — and those must be caught by
        the plan, not discovered as a 404 against `/repos/{owner}/...`."""
        placeholder_templates = set()
        for item in _doc(_invoke("templates", "--json"))["templates"]:
            name = item["name"]
            assert _invoke("init", name, "-t", name).exit_code == 0
            result = _invoke("survey", name, "--dry-run", "--json")
            if result.exit_code == 0:
                continue
            problems = _error(result)["problems"]
            assert all("unresolved placeholder" in p["message"] for p in problems), (name, problems)
            placeholder_templates.add(name)
        assert placeholder_templates == {"github-audit"}

    def test_init_json_and_conflict(self, project):
        result = _invoke("init", "m", "-t", "code-forensics", "--json")
        assert result.exit_code == 0
        doc = _doc(result)
        assert doc["schema"] == "recon.v1.mission"
        assert Path(doc["config"]) == project / ".recon" / "m" / "config.yaml"

        again = _invoke("init", "m", "--json")
        assert again.exit_code == 3
        assert _error(again)["kind"] == "conflict"

    def test_unknown_template_is_not_found(self, project):
        result = _invoke("init", "m", "-t", "nope", "--json")
        assert result.exit_code == 4
        assert _error(result)["fix"] == "recon templates"


# --- capture --------------------------------------------------------------------------

HOOK_PAYLOAD = {
    "session_id": "abc123",
    "transcript_path": "/tmp/t.jsonl",
    "cwd": "/repo",
    "permission_mode": "default",
    "hook_event_name": "PostToolUse",
    "tool_name": "mcp__exa__web_search_exa",
    "tool_input": {"query": "state space models"},
    "tool_response": {"results": [{"title": "Mamba", "url": "https://arxiv.org/abs/2312.00752"}]},
    "tool_use_id": "toolu_01",
}


class TestCapture:
    def test_hook_payload_to_survey_end_to_end(self, project):
        _invoke("init", "mcp", "-t", "mcp-captures")
        result = _invoke("capture", "mcp", "--json", stdin=json.dumps(HOOK_PAYLOAD))

        assert result.exit_code == 0, result.stderr
        line = _doc(result)
        assert line["schema"] == "recon.v1.capture"
        assert line["kind"] == "external"
        log = project / ".recon" / "mcp" / "captures" / "captures.jsonl"
        assert json.loads(log.read_text())["id"] == line["id"]

        survey = _invoke("survey", "mcp", "--json")
        assert survey.exit_code == 0, survey.stderr
        rows = _doc(_invoke("query", "mcp", "SELECT * FROM mcp_results", "--json"))["rows"]
        assert rows[0]["tool"] == "mcp__exa__web_search_exa"

    def test_unknown_mission_is_not_found(self, project):
        result = _invoke("capture", "nope", "--json", stdin=json.dumps(HOOK_PAYLOAD))
        assert result.exit_code == 4
        assert "mcp-captures" in _error(result)["fix"]

    @pytest.mark.parametrize("stdin", ["not json", "[]", '{"tool_name": "x"}'])
    def test_bad_payload_is_a_config_error_and_writes_nothing(self, project, stdin):
        _invoke("init", "mcp", "-t", "mcp-captures")
        result = _invoke("capture", "mcp", "--json", stdin=stdin)
        assert result.exit_code == 3
        assert _error(result)["kind"] == "config"
        assert not (project / ".recon" / "mcp" / "captures" / "captures.jsonl").exists()


# --- errors that escape -----------------------------------------------------------------


def test_an_unexpected_exception_is_classified_unknown(project, monkeypatch):
    def boom():
        raise RuntimeError("wiring bug")

    monkeypatch.setattr(cli_module, "load_registry", boom)
    _mission(project, "m", OK_CONFIG)
    result = _invoke("survey", "m", "--json")
    assert result.exit_code == 1
    error = _error(result)
    assert error["kind"] == "unknown"
    assert "wiring bug" in error["message"]


def test_a_factory_failure_reaches_the_tables_not_a_traceback(project, monkeypatch):
    registry = load_registry()

    def explode(ctx):
        raise ValueError("no credentials file")

    registry.collector_types["fragile"] = replace(registry.collector_types["cli"], create=explode)
    monkeypatch.setattr(cli_module, "load_registry", lambda: registry)
    _mission(project, "m", {"collectors": [{"name": "f", "type": "fragile", "run": "true"}]})
    result = _invoke("survey", "m", "--json")
    assert result.exit_code == 1
    [table] = _doc(result)["tables"]
    assert table["error"]["kind"] == "config"
    assert any("no credentials file" in w for w in _doc(result)["warnings"])

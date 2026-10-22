"""The CLI contract: exit codes, JSON documents, errors on stderr, and no effects on a plan.

Every test runs in a throwaway project (a tmp dir with a `.git`), drives the real click
group through CliRunner, and uses `cli` collectors running `echo`/`printf` — no network.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from click import unstyle
from click.testing import CliRunner

import recon
from recon import composition
from recon.adapters._in.cli import main
from recon.composition import load_plugins
from recon.domain.collector import BrokenPlugin, CollectorType
from recon.domain.errors import CollectionError
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
    """The one ``recon.v1.error`` envelope on stderr, as one JSON line: the shape
    ``components/capabilities/error-envelope.json`` agrees, keys in its order.
    """
    lines = [line for line in result.stderr.splitlines() if line.startswith("{")]
    assert len(lines) == 1, result.stderr
    envelope = json.loads(lines[0])
    assert list(envelope) == ["schema", "error"] and envelope["schema"] == "recon.v1.error"
    error = envelope["error"]
    assert list(error) == ["kind", "message", "fix", "complete", "problems", "details"]
    assert error["message"] and error["fix"]
    return dict(error)


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

    def test_partial_failure_exits_run_failed_and_still_writes_the_archive(self, project):
        """Changed requirement: a survey whose failed tables are neither auth nor all
        transient exits ``run_failed`` (8), so it no longer shares 1 with ``unknown``."""
        config = {
            "collectors": [
                {"name": "good", "type": "cli", "run": "echo '{\"a\": 1}'"},
                {"name": "bad", "type": "cli", "run": "exit 7"},
            ]
        }
        _mission(project, "m", config)
        result = _invoke("survey", "m", "--json")

        assert result.exit_code == 8
        doc = _doc(result)
        assert doc["status"] == "incomplete"
        statuses = {t["name"]: t["status"] for t in doc["tables"]}
        assert statuses == {"good": "ok", "bad": "error"}
        error = _error(result)
        assert error["kind"] == "run_failed"
        assert error["details"]["failed"] == ["bad"]
        assert error["fix"]
        archive = Path(error["details"]["archive"])
        assert (archive / ".incomplete").exists()
        assert (archive / "good.jsonl").exists()

    def test_human_partial_failure_is_non_zero_too(self, project):
        _mission(project, "m", {"collectors": [{"name": "bad", "type": "cli", "run": "exit 7"}]})
        result = _invoke("survey", "m")
        assert result.exit_code == 8
        assert "error (run_failed):" in result.stderr

    @pytest.mark.parametrize(
        ("kinds", "code", "kind"),
        [
            (["transient"], 5, "transient"),
            (["transient", "transient"], 5, "transient"),
            (["transient", "auth"], 6, "auth"),
            (["auth", "run_failed"], 6, "auth"),
            (["transient", "run_failed"], 8, "run_failed"),
        ],
    )
    def test_exit_code_follows_the_failures_classification(
        self, project, monkeypatch, kinds, code, kind
    ):
        registry = load_plugins()
        for k in set(kinds):
            registry.collector_types[f"fails-{k}"] = _failing_type(k)
        monkeypatch.setattr(composition, "load_plugins", lambda: registry)
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

    @pytest.mark.parametrize("step", ["write_meta", "mark_complete"])
    def test_an_archive_that_could_not_be_finished_is_incomplete_and_fails(
        self, project, monkeypatch, step
    ):
        """Regression: a survey whose ``meta.yaml`` or completion step failed (a full disk)
        said ``status: ok`` and exited 0, and under --json the one word of it, an event,
        went nowhere; the archive stayed incomplete and only a later query said so."""

        def fails(self, archive, *args):
            raise OSError(28, "No space left on device")

        unfinished = type("Unfinished", (composition.ArchiveFiles,), {step: fails})
        monkeypatch.setattr(composition, "ArchiveFiles", unfinished)
        _mission(project, "m", OK_CONFIG)
        result = _invoke("survey", "m", "--json")

        assert result.exit_code == 8
        doc = _doc(result)
        assert doc["status"] == "incomplete"
        assert [t["status"] for t in doc["tables"]] == ["ok"]
        [warning] = [w for w in doc["warnings"] if w["kind"] == "archive_unfinished"]
        assert "No space left on device" in warning["message"]
        assert warning["kind"] in recon.PROBLEM_KINDS
        error = _error(result)
        assert error["kind"] == "run_failed"
        assert "could not be finished" in error["message"]
        assert error["details"] == {"archive": doc["archive"], "failed": []}
        assert (Path(doc["archive"]) / ".incomplete").exists()

        human = _invoke("survey", "m")
        assert human.exit_code == 8
        assert "No space left on device" in human.stderr
        assert "error (run_failed):" in human.stderr

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

        assert result.exit_code == 7 == recon.EXIT_CODES["conflict"]
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

        monkeypatch.setattr(composition, "build_collectors", no_collectors)
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
        assert {"collectors.0.type", "collectors.1.normalize.t", "collectors.2.run"} <= paths
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
        registry = load_plugins()
        registry.broken.append(BrokenPlugin("recon.collectors", "sql", "x:y", "ImportError: z"))
        monkeypatch.setattr(composition, "load_plugins", lambda: registry)

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
        # A table error is one record: fix is always present, null when there is none.
        assert tables["u"]["error"] == {"kind": "unknown", "message": "boom", "fix": None}

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

    def test_columns_that_share_a_name_are_all_kept(self, project):
        """Regression: ``SELECT h.a, g.a`` printed ``columns: ["a", "a"]`` but each row held
        one ``a``: a row is an object, and the second value overwrote the first. Repeated
        names are made unique as DuckDB makes them in a subquery (``a_1``, then ``a_1_1``
        on a further clash), in ``columns`` and in every row alike."""
        _mission(project, "m", OK_CONFIG)
        _invoke("survey", "m")
        sql = "SELECT h.a, g.a, 0 AS a_1 FROM hello h, hello g WHERE h.a = 1 AND g.a = 2"

        doc = _doc(_invoke("query", "m", sql, "--json"))

        assert doc["columns"] == ["a", "a_1", "a_1_1"]
        assert doc["rows"] == [{"a": 1, "a_1": 2, "a_1_1": 0}]
        human = unstyle(_invoke("query", "m", sql).stdout)
        assert "a_1_1" in human and "2" in human

    def test_an_empty_table_answers_a_query_of_its_columns(self, project):
        """Regression: a collector whose source had nothing wrote a table whose view had only
        ``_empty``, and ``SELECT title FROM <table>`` failed (exit 10) where the same query
        on a full archive ran."""
        mission = {
            "collectors": [
                {"name": "hits", "type": "cli", "run": "true", "normalize": {"title": "line"}}
            ]
        }
        _mission(project, "m", mission)
        assert _invoke("survey", "m").exit_code == 0
        result = _invoke("query", "m", "SELECT title FROM hits", "--json")
        assert result.exit_code == 0, result.stderr
        assert _doc(result)["rows"] == []

    def test_zero_rows_is_still_a_document(self, project):
        _mission(project, "m", OK_CONFIG)
        _invoke("survey", "m")
        doc = _doc(_invoke("query", "m", "SELECT a FROM hello WHERE a > 9", "--json"))
        assert doc["rows"] == [] and doc["count"] == 0

    def test_reading_outside_the_archive_is_refused_with_a_fix(self, project):
        _mission(project, "m", OK_CONFIG)
        _invoke("survey", "m")
        (project / "private.txt").write_text("hunter2")
        sql = f"SELECT * FROM read_text('{project / 'private.txt'}')"

        result = _invoke("query", "m", sql, "--json")

        assert result.exit_code == 10 == recon.EXIT_CODES["query"]
        assert "hunter2" not in result.stdout + result.stderr
        error = _error(result)
        assert error["kind"] == "query"
        assert str(project / ".recon" / "m" / "archive") in error["fix"]

    def test_bad_sql_exits_3_naming_the_tables(self, project):
        _mission(project, "m", OK_CONFIG)
        _invoke("survey", "m")
        result = _invoke("query", "m", "SELECT * FROM nope", "--json")
        assert result.exit_code == 10
        error = _error(result)
        assert error["kind"] == "query"
        assert "hello" in error["fix"]

    def test_no_archive_is_not_found(self, project):
        _mission(project, "m", OK_CONFIG)
        result = _invoke("query", "m", "SELECT 1", "--json")
        assert result.exit_code == 4
        assert _error(result)["fix"] == "recon survey m"

    def test_unknown_archive_is_not_found(self, project):
        """Changed requirement: the flag that names an archive is ``--archive``."""
        _mission(project, "m", OK_CONFIG)
        _invoke("survey", "m")
        result = _invoke("query", "m", "SELECT 1", "--archive", "1999-01-01", "--json")
        assert result.exit_code == 4
        assert "have:" in _error(result)["message"]


class TestQueryProvenance:
    def test_the_rows_name_the_archive_they_came_from(self, project):
        """Regression: query output did not say which archive it read."""
        _mission(project, "m", OK_CONFIG)
        _invoke("survey", "m")
        [archive] = (project / ".recon" / "m" / "archive").iterdir()

        doc = _doc(_invoke("query", "m", "SELECT a FROM hello", "--json"))
        assert doc["archive_id"] == archive.name and doc["archive"] == str(archive)
        human = _invoke("query", "m", "SELECT a FROM hello")
        assert f"archive {archive.name}" in human.stderr

    def test_help_says_where_archive_ids_come_from_and_how_to_read_older_or_raw_files(self):
        text = " ".join(unstyle(_invoke("query", "--help").stdout).split())
        assert "recon status <mission>" in text and "--archive" in text
        assert "read_json_auto" in text and "read_ndjson_objects" in text
        assert "WITH ORDINALITY" in text


class TestAnUnreadableTable:
    """Regression: one table DuckDB could not read (an OpenAlex record with its word-keyed
    ``abstract_inverted_index``, surveyed without normalize, as the skill's probe step says)
    made every query on every table fail as ``unknown``, "report this as a bug"."""

    @pytest.fixture
    def mission(self, project):
        record = {"id": "W1", "abstract_inverted_index": {"the": [0], "The": [2], "cat": [1]}}
        config = {"collectors": [
            {"name": "oa-works", "type": "cli", "run": f"echo '{json.dumps(record)}'"},
            {"name": "good", "type": "cli", "run": "echo '{\"a\": 1}'"},
        ]}  # fmt: skip
        _mission(project, "m", config)
        assert _invoke("survey", "m").exit_code == 0
        return "m"

    def test_another_table_is_queried_with_a_warning_naming_the_table_and_the_fix(self, mission):
        result = _invoke("query", mission, "SELECT a FROM good", "--json")

        assert result.exit_code == 0, result.stderr
        doc = _doc(result)
        assert doc["rows"] == [{"a": 1}]
        [warning] = doc["warnings"]
        assert warning["kind"] == "table_unreadable" and warning["got"] == "oa_works"
        assert "$inverted_index" in warning["fix"]
        human = _invoke("query", mission, "SELECT a FROM good")
        assert "oa_works cannot be queried" in human.stderr
        assert "fix:" in human.stderr and "$inverted_index" in human.stderr

    def test_the_survey_warns_before_any_query(self, project):
        """Regression: the survey exited 0 without a word; the first sign was a failed
        query."""
        record = {"id": "W1", "abstract_inverted_index": {"the": [0], "The": [2]}}
        config = {"collectors": [
            {"name": "oa-works", "type": "cli", "run": f"echo '{json.dumps(record)}'"},
            {"name": "good", "type": "cli", "run": "echo '{\"a\": 1}'"},
        ]}  # fmt: skip
        _mission(project, "m", config)

        result = _invoke("survey", "m", "--json")

        assert result.exit_code == 0, result.stderr
        [warning] = _doc(result)["warnings"]
        assert warning["kind"] == "table_unreadable" and warning["got"] == "oa_works"
        assert "$inverted_index" in warning["fix"] and "read_ndjson_objects" in warning["fix"]
        human = _invoke("survey", "m")
        assert "oa_works cannot be queried" in human.stderr and "$inverted_index" in human.stderr

    def test_querying_it_is_a_query_error_with_the_fix(self, mission):
        result = _invoke("query", mission, "SELECT * FROM oa_works", "--json")

        assert result.exit_code == 10
        error = _error(result)
        assert error["kind"] == "query"
        assert "$inverted_index" in error["fix"] and "read_ndjson_objects" in error["fix"]
        assert error["details"]["table"] == "oa_works"


class TestWhatCountsAsAnArchive:
    def test_a_directory_not_named_as_a_survey_names_one_is_not_an_archive(self, project):
        """Regression: any directory under ``archive/`` was an archive, so a stray
        ``notes/`` sorted after the timestamps, became the latest, and every query failed
        with "no tables"."""
        _mission(project, "m", OK_CONFIG)
        assert _invoke("survey", "m").exit_code == 0
        (project / ".recon" / "m" / "archive" / "notes").mkdir()

        [mission] = _doc(_invoke("status", "m", "--json"))["missions"]
        assert mission["archives"] == 1
        assert [a["id"] for a in mission["history"]] != ["notes"]
        result = _invoke("query", "m", "SELECT a FROM hello ORDER BY a", "--json")
        assert result.exit_code == 0, result.stderr
        assert _doc(result)["rows"] == [{"a": 1}, {"a": 2}]

    def test_a_newer_meta_yaml_is_read_for_what_this_recon_knows(self, project):
        """Regression: ``meta.yaml`` from a newer recon, with a field this one does not know
        in a table's error, made ``status`` fail as a defect (exit 1)."""
        mission = _mission(project, "m", OK_CONFIG)
        archive = mission / "archive" / "2027-01-01-000000-000000"
        archive.mkdir(parents=True)
        (archive / "t.jsonl").write_text('{"a": 1}\n')
        (archive / ".incomplete").write_text("")
        (archive / "meta.yaml").write_text(
            yaml.safe_dump(
                {
                    "format_version": 3,
                    "recon_version": "9.0.0",
                    "lineage": {"parent": "x"},
                    "tables": [
                        {
                            "name": "t",
                            "status": "error",
                            "records": 0,
                            "attempts": 2,
                            "error": {
                                "kind": "rate_limited",
                                "message": "slow down",
                                "fix": None,
                                "retry_after": 30,
                            },
                        }
                    ],
                }
            )
        )
        result = _invoke("status", "m", "--json")
        assert result.exit_code == 0, result.stderr
        [table] = _doc(result)["missions"][0]["latest"]["tables"]
        assert table["error"] == {"kind": "rate_limited", "message": "slow down", "fix": None}
        assert _doc(result)["missions"][0]["latest"]["format_version"] == 3


class TestArchiveHistory:
    def test_status_of_one_mission_lists_every_archive_with_its_time_and_outcome(self, project):
        """Regression: ``recon status`` said "Archives 3" and gave no id to query."""
        config = {"collectors": [
            {"name": "ok", "type": "cli", "run": "echo one"},
            {"name": "flaky", "type": "cli", "run": "test -e .recon/pass || exit 3"},
        ]}  # fmt: skip
        _mission(project, "m", config)
        _mission(project, "other", OK_CONFIG)
        assert _invoke("survey", "m").exit_code == 8
        (project / ".recon" / "pass").write_text("")
        assert _invoke("survey", "m").exit_code == 0

        doc = _doc(_invoke("status", "m", "--json"))

        [mission] = doc["missions"]
        assert mission["name"] == "m" and mission["archives"] == 2
        first, second = mission["history"]
        assert first["id"] < second["id"] == mission["latest"]["id"]
        assert (first["state"], second["state"]) == ("incomplete", "ok")
        assert first["started_at"] and first["finished_at"]
        assert [t["status"] for t in first["tables"]] == ["ok", "error"]
        rows = _doc(_invoke("query", "m", "SELECT * FROM ok", "--archive", first["id"], "--json"))
        assert rows["archive_id"] == first["id"]
        human = _invoke("status", "m")
        assert first["id"] in human.stdout and second["id"] in human.stdout

    def test_status_of_every_mission_carries_no_history(self, project):
        _mission(project, "m", OK_CONFIG)
        [mission] = _doc(_invoke("status", "--json"))["missions"]
        assert mission["history"] is None

    def test_an_unknown_mission_is_not_found(self, project):
        _mission(project, "m", OK_CONFIG)
        result = _invoke("status", "nope", "--json")
        assert result.exit_code == 4
        assert _error(result)["details"]["allowed"] == ["m"]

    def test_an_unknown_archive_points_at_the_listing(self, project):
        _mission(project, "m", OK_CONFIG)
        _invoke("survey", "m")
        result = _invoke("query", "m", "SELECT 1", "--archive", "1999-01-01", "--json")
        assert _error(result)["fix"] == "recon status m lists its archives"


class TestDryRunShowsTheRequest:
    def test_the_url_carries_its_query_and_the_secrets_are_redacted(self, project, monkeypatch):
        """Regression: the dry run showed ``https://api.openalex.org/works`` with no query
        string, so search, filter and per_page were invisible."""
        monkeypatch.setenv("OA_KEY", "sk-SECRET")
        monkeypatch.setenv("TOKEN", "tok-SECRET")
        config = {
            "catalog": [
                {"name": "oa", "url": "https://api.openalex.org", "user_agent": "me@example.org",
                 "auth": {"env": "OA_KEY", "param": "api_key"}},
                {"name": "gh", "url": "https://user:pw@api.github.test",
                 "auth": {"env": "TOKEN", "header": "Authorization", "prefix": "Bearer "}},
            ],
            "collectors": [
                {"name": "works", "type": "api", "source": "oa", "endpoint": "/works",
                 "params": {"search": "state space", "per_page": "1"}},
                {"name": "issues", "type": "api", "source": "gh", "method": "POST",
                 "endpoint": "/search?sort=new", "body": {"q": "{q}"}, "params": {"q": "bug"}},
            ],
        }  # fmt: skip
        _mission(project, "m", config)

        result = _invoke("survey", "m", "-n", "--json")

        assert "SECRET" not in result.stdout + result.stderr
        runs = {r["output"]: r for r in _doc(result)["runs"]}
        works, issues = runs["works"], runs["issues"]
        assert works["method"] == "GET"
        # In the config's order, which _mission's YAML dump sorts.
        assert works["url"] == (
            "https://api.openalex.org/works?per_page=1&search=state+space&api_key=REDACTED"
        )
        assert works["headers"] == {"User-Agent": "me@example.org"}
        assert issues["url"] == "https://REDACTED:REDACTED@api.github.test/search?sort=new&q=bug"
        assert issues["headers"]["Authorization"] == "REDACTED"
        assert issues["headers"]["User-Agent"].startswith(f"recon/{recon.__version__} ")
        assert issues["body"] == {"q": "bug"}
        human = _invoke("survey", "m", "-n").stdout
        assert "GET https://api.openalex.org/works?per_page=1&search=state+space" in human
        assert "Authorization: REDACTED" in human and '{"q": "bug"}' in human


class TestMixedFanOut:
    def test_an_unpinned_collector_over_sources_of_different_types_is_warned(self, project):
        """Regression: adding a source of another type to a mission silently fanned an
        unpinned collector out over it."""
        config = {
            "catalog": [
                {"name": "repo", "type": "local", "url": "."},
                {"name": "docs", "type": "web", "url": "https://docs.example.test"},
            ],
            "collectors": [
                {"name": "todos", "type": "cli", "run": "rg TODO ."},
                {"name": "page", "type": "web", "source": "docs"},
            ],
        }
        _mission(project, "m", config)

        result = _invoke("survey", "m", "-n", "--json")

        assert result.exit_code == 0
        [warning] = [w for w in _doc(result)["warnings"] if w["kind"] == "fan_out_mixed"]
        assert warning["path"] == "collectors.0.source"
        assert "repo (local)" in warning["message"] and "docs (web)" in warning["message"]
        assert "source:" in warning["fix"] and warning["allowed"] == ["repo", "docs"]
        human = _invoke("survey", "m", "-n")
        assert "collectors.0.source" in human.stderr and "fix: pin it" in human.stderr

    def test_a_survey_prints_the_warning_with_its_fix(self, project):
        config = {
            "catalog": [{"name": "repo", "type": "local", "url": "."},
                        {"name": "api", "url": "https://api.example.test"}],
            "collectors": [{"name": "hello", "type": "cli", "run": "echo hi"}],
        }  # fmt: skip
        _mission(project, "m", config)
        result = _invoke("survey", "m")
        assert result.exit_code == 0, result.stderr
        assert "collectors.0.source" in result.stderr and "fix: pin it" in result.stderr

    def test_sources_of_one_type_are_not_warned(self, project):
        config = {
            "catalog": [{"name": "a", "type": "local", "url": "a"},
                        {"name": "b", "type": "local", "url": "b"}],
            "collectors": [{"name": "todos", "type": "cli", "run": "rg TODO ."}],
        }  # fmt: skip
        (project / "a").mkdir()
        (project / "b").mkdir()
        _mission(project, "m", config)
        assert _doc(_invoke("survey", "m", "-n", "--json"))["warnings"] == []


class TestOneRecordHoldingTheRecords:
    def test_a_survey_suggests_the_extract_it_is_missing(self, project):
        """Regression: arXiv without ``extract: feed.entry`` gave "ok, 1 record", one
        ``feed`` object holding the whole response, without a word."""
        config = {"collectors": [
            {"name": "feed", "type": "capture", "match": {"kind": "external"}},
        ]}  # fmt: skip
        _mission(project, "m", config)
        entries = [{"id": f"2312.0075{i}", "title": f"paper {i}"} for i in range(3)]
        payload = {**HOOK_PAYLOAD, "tool_response": {"feed": {"title": "q", "entry": entries}}}
        assert _invoke("capture", "m", stdin=json.dumps(payload)).exit_code == 0

        result = _invoke("survey", "m", "--json")

        assert result.exit_code == 0, result.stderr
        doc = _doc(result)
        assert doc["tables"][0]["records"] == 1
        [warning] = doc["warnings"]
        assert warning["kind"] == "extract_missing" and warning["path"] == "collectors.0.extract"
        assert "3 objects at feed.entry" in warning["message"]
        assert "extract: feed.entry" in warning["fix"]
        assert "extract: feed.entry" in _invoke("survey", "m").stderr

        config["collectors"][0]["extract"] = "feed.entry"
        (project / ".recon" / "m" / "config.yaml").write_text(yaml.safe_dump(config))
        fixed = _doc(_invoke("survey", "m", "--json"))
        assert fixed["tables"][0]["records"] == 3 and fixed["warnings"] == []

    def test_a_shaped_record_or_a_type_that_does_not_extract_is_not_warned(self, project):
        nested = json.dumps({"feed": {"entry": [{"a": 1}, {"a": 2}]}})
        config = {"collectors": [
            {"name": "lines", "type": "cli", "run": f"echo '{nested}'"},
            {"name": "shaped", "type": "cli", "run": f"echo '{nested}'",
             "normalize": {"entries": "feed.entry"}},
        ]}  # fmt: skip
        _mission(project, "m", config)
        assert _doc(_invoke("survey", "m", "--json"))["warnings"] == []


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

    def test_init_json(self, project):
        result = _invoke("init", "m", "-t", "code-forensics", "--json")
        assert result.exit_code == 0
        doc = _doc(result)
        assert doc["schema"] == "recon.v1.mission"
        assert Path(doc["config"]) == project / ".recon" / "m" / "config.yaml"
        assert doc["outcome"] == "created" and doc["from"] == "template:code-forensics"

    def test_init_again_with_the_same_config_changes_nothing_and_succeeds(self, project):
        """Regression (S12): a second identical init exited 3 ``conflict``, so a retry or a
        script that runs init every time failed."""
        cfg = project / "mission.yaml"
        cfg.write_text(yaml.safe_dump(OK_CONFIG))
        assert _invoke("init", "m", "-c", str(cfg)).exit_code == 0
        config = project / ".recon" / "m" / "config.yaml"
        before = config.stat().st_mtime_ns

        again = _invoke("init", "m", "-c", str(cfg), "--json")

        assert again.exit_code == 0, again.stderr
        assert _doc(again)["outcome"] == "unchanged"
        assert config.stat().st_mtime_ns == before
        assert _invoke("init", "t", "-t", "mcp-captures").exit_code == 0
        assert _doc(_invoke("init", "t", "-t", "mcp-captures", "--json"))["outcome"] == "unchanged"

    def test_init_with_a_different_config_is_a_conflict_naming_replace(self, project):
        _invoke("init", "m", "-t", "code-forensics")
        config = project / ".recon" / "m" / "config.yaml"
        before = config.read_text()
        other = project / "other.yaml"
        other.write_text(yaml.safe_dump(OK_CONFIG))

        result = _invoke("init", "m", "-c", str(other), "--json")

        assert result.exit_code == 7
        error = _error(result)
        assert error["kind"] == "conflict"
        assert f"recon init m -c {other} --replace" in error["fix"]
        assert config.read_text() == before
        replaced = _invoke("init", "m", "-c", str(other), "--replace", "--json")
        # Changed requirement: `created: false` read as "nothing changed", which a replace
        # is not; the outcome says which of the three happened.
        assert replaced.exit_code == 0 and _doc(replaced)["outcome"] == "replaced"
        assert config.read_text() == other.read_text()
        third = project / "third.yaml"
        third.write_text(yaml.safe_dump({"collectors": [{"name": "x", "type": "cli", "run": ":"}]}))
        assert "Replaced:" in _invoke("init", "m", "-c", str(third), "--replace").stderr
        assert "Unchanged:" in _invoke("init", "m", "-c", str(third)).stderr

    def test_init_takes_a_template_or_a_config_not_both(self, project):
        cfg = project / "mission.yaml"
        cfg.write_text(yaml.safe_dump(OK_CONFIG))
        result = _invoke("init", "m", "-t", "code-forensics", "-c", str(cfg), "--json")
        assert result.exit_code == 2 and _error(result)["kind"] == "usage"
        assert not (project / ".recon").exists()

    @pytest.mark.parametrize("command", [["init", "m"], ["survey", "m", "-n"], ["survey", "m"]])
    def test_a_dash_reads_the_config_from_stdin(self, project, command):
        result = _invoke(*command, "-c", "-", "--json", stdin=yaml.safe_dump(OK_CONFIG))
        assert result.exit_code == 0, result.stderr
        if command[0] == "init":
            assert _doc(result)["from"] == "-"
            config = project / ".recon" / "m" / "config.yaml"
            assert yaml.safe_load(config.read_text()) == OK_CONFIG

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

    monkeypatch.setattr(composition, "load_plugins", boom)
    _mission(project, "m", OK_CONFIG)
    result = _invoke("survey", "m", "--json")
    assert result.exit_code == 1
    error = _error(result)
    assert error["kind"] == "unknown"
    assert "wiring bug" in error["message"]


def test_an_escaping_error_keeps_its_own_fix(project, monkeypatch):
    def refuses():
        raise CollectionError("cannot do that", kind="config", fix="do this instead")

    monkeypatch.setattr(composition, "load_plugins", refuses)
    _mission(project, "m", OK_CONFIG)
    result = _invoke("survey", "m", "--json")
    assert result.exit_code == 3
    assert _error(result)["fix"] == "do this instead"


def test_a_plugin_reading_an_undeclared_variable_fails_its_table_with_a_fix(project, monkeypatch):
    """The whole path: the table fails as a config error that names the variable and says
    where to declare it; nothing is read from the process environment on the quiet."""
    monkeypatch.setenv("RECON_TEST_UNDECLARED", "a secret the mission never named")

    class Greedy:
        def __init__(self, ctx):
            self._env = ctx.env

        def collect(self, entry, source, *, captures=None):
            yield {"leaked": self._env.get("RECON_TEST_UNDECLARED", "")}

    registry = load_plugins()
    registry.collector_types["greedy"] = CollectorType(create=Greedy, effects=None)
    monkeypatch.setattr(composition, "load_plugins", lambda: registry)
    mission = _mission(project, "m", {"collectors": [{"name": "g", "type": "greedy"}]})

    result = _invoke("survey", "m", "--json")

    [table] = _doc(result)["tables"]
    assert table["status"] == "error"
    assert table["error"]["kind"] == "config"
    assert "RECON_TEST_UNDECLARED" in table["error"]["message"]
    assert "auth.env" in table["error"]["fix"]
    [archive] = (mission / "archive").iterdir()
    assert not (archive / "g.jsonl").exists()


def test_a_factory_failure_reaches_the_tables_not_a_traceback(project, monkeypatch):
    registry = load_plugins()

    def explode(ctx):
        raise ValueError("no credentials file")

    registry.collector_types["fragile"] = replace(registry.collector_types["cli"], create=explode)
    monkeypatch.setattr(composition, "load_plugins", lambda: registry)
    _mission(project, "m", {"collectors": [{"name": "f", "type": "fragile", "run": "true"}]})
    result = _invoke("survey", "m", "--json")
    assert result.exit_code == 8
    [table] = _doc(result)["tables"]
    assert table["error"]["kind"] == "config"
    [warning] = _doc(result)["warnings"]
    assert warning["kind"] == "plugin_broken" and "no credentials file" in warning["message"]


# --- the shared exit codes, usage errors, Ctrl-C ------------------------------------------


CONTRACTS = Path(recon.__file__).parents[3]


def test_the_agreed_kinds_keep_their_agreed_codes():
    """``components/capabilities/exit-codes.json`` holds the codes every tool agrees on; a
    tool's own kinds take codes from 10 up, one kind per code."""
    agreed = json.loads((CONTRACTS / "exit-codes.json").read_text())["kinds"]
    shared = set(agreed) & set(recon.EXIT_CODES)
    assert shared == set(agreed) - {"store"}, "recon writes no separate store"
    assert {kind: recon.EXIT_CODES[kind] for kind in shared} == {k: agreed[k] for k in shared}
    own = set(recon.EXIT_CODES) - shared
    assert own == {"query"} and all(recon.EXIT_CODES[kind] >= 10 for kind in own)
    codes = list(recon.EXIT_CODES.values())
    assert len(codes) == len(set(codes)), "one kind per code"
    tables = (recon.EXIT_MEANINGS, recon.EXIT_NEXT)
    assert set(recon.ERROR_KINDS) <= set(recon.EXIT_CODES) == set(tables[0]) == set(tables[1])


def test_the_shared_problem_kinds_are_the_agreed_ones():
    """``components/capabilities/error-envelope.json`` names the problem kinds every tool
    shares; recon uses those names for those mistakes, and adds its own."""
    contract = json.loads((CONTRACTS / "error-envelope.json").read_text())
    shared = set(contract["problem_kinds"])
    assert shared - {"schema_unsupported"} <= set(recon.PROBLEM_KINDS)
    assert {"missing", "invalid", "unknown_name", "duplicate"}.isdisjoint(recon.PROBLEM_KINDS)
    assert list(recon.Problem("p", "m").document()) == list(contract["problem"])
    assert list(recon.error_document(recon.ReconError("m")).document()["error"]) == list(
        contract["envelope"]["error"]
    )


def test_config_and_lookup_errors_have_the_builtin_bases():
    assert issubclass(recon.ConfigError, ValueError)
    assert issubclass(recon.NotFoundError, KeyError)
    assert str(recon.NotFoundError("no mission 'm'")) == "no mission 'm'", "not repr-quoted"


@pytest.mark.parametrize(
    "args",
    [
        ("survey",),
        ("survey", "m", "--no-such-flag", "--json"),
        ("frobnicate", "--json"),
        ("survey", "../escape", "--json"),
        ("-r", "normalize-spec"),
        ("--skill", "-r", "no-such-reference"),
    ],
)
def test_a_usage_error_is_one_envelope_of_kind_usage_and_exits_2(project, args):
    result = _invoke(*args)
    assert result.exit_code == 2 == recon.EXIT_CODES["usage"], result.stderr
    if "--json" in args:
        assert result.stdout == ""
        assert _error(result)["kind"] == "usage"
    else:
        assert result.stderr.startswith("error (usage):")


def test_an_unknown_skill_reference_names_the_ones_there_are(project):
    with pytest.raises(recon.UsageError) as caught:
        recon.skill("nope")
    assert caught.value.details == {"value": "nope", "allowed": recon.skill_references()}
    stderr = " ".join(_invoke("--skill", "-r", "nope").stderr.split())
    assert ", ".join(recon.skill_references()) in stderr


def test_ctrl_c_is_one_envelope_of_kind_interrupted_and_exits_130(project, monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(recon, "status", interrupted)
    result = _invoke("status", "--json")
    assert result.exit_code == 130 == recon.EXIT_CODES["interrupted"]
    assert _error(result)["kind"] == "interrupted"
    assert result.stderr.startswith("{"), "regression (N2): no blank line before the envelope"
    for_a_person = _invoke("status")
    assert for_a_person.exit_code == 130
    assert "error (interrupted):" in for_a_person.stderr


def test_a_survey_cut_short_leaves_its_archive_marked_incomplete(project, monkeypatch):
    """Regression: Ctrl-C mid-survey left an archive with no meta.yaml and no marker, which
    status reported as ok and query read without a warning."""

    class Interrupts:
        def collect(self, entry, source, *, captures=None):
            yield {"a": 1}
            raise KeyboardInterrupt

    plugins = composition.load_plugins()
    plugins.collector_types["stops"] = CollectorType(create=lambda ctx: Interrupts(),
                                                     effects=None)  # fmt: skip
    monkeypatch.setattr(composition, "load_plugins", lambda: plugins)
    mission = _mission(project, "m", {"collectors": [{"name": "s", "type": "stops"}]})

    result = _invoke("survey", "m", "--json")

    assert result.exit_code == 130
    [archive] = (mission / "archive").iterdir()
    assert (archive / ".incomplete").exists()
    assert _doc(_invoke("status", "--json"))["missions"][0]["latest"]["state"] == "incomplete"


def test_a_query_on_an_incomplete_archive_says_so_in_its_document(project):
    """Regression: only the human output warned that rows may be partial; an agent reading
    --json could not tell."""
    _mission(project, "m", {"collectors": [
        {"name": "good", "type": "cli", "run": "echo '{\"a\": 1}'"},
        {"name": "bad", "type": "cli", "run": "exit 7"},
    ]})  # fmt: skip
    _invoke("survey", "m")
    document = _doc(_invoke("query", "m", "SELECT a FROM good", "--json"))
    assert document["state"] == "incomplete" and document["rows"] == [{"a": 1}]
    _mission(project, "n", OK_CONFIG)
    _invoke("survey", "n")
    assert _doc(_invoke("query", "n", "SELECT a FROM hello", "--json"))["state"] == "ok"


def test_a_plan_problem_carries_its_kind_fix_and_the_value_written(project):
    _mission(project, "m", {"collectors": [{"name": "a", "type": "ftp"}], "colectors": []})
    problems = {p["path"]: p for p in _error(_invoke("survey", "m", "-n", "--json"))["problems"]}
    installed = sorted(
        t["type"] for t in _doc(_invoke("status", "--json"))["plugins"]["collectors"]
    )
    assert problems["collectors.0.type"] == {
        "path": "collectors.0.type",
        "kind": "name_unknown",
        "message": problems["collectors.0.type"]["message"],
        "fix": problems["collectors.0.type"]["fix"],
        "allowed": installed,
        "got": "ftp",
        "source": "",
    }
    assert "cli" in problems["collectors.0.type"]["fix"]
    assert problems["colectors"]["kind"] == "unknown_key"
    assert {p["kind"] for p in problems.values()} <= set(recon.PROBLEM_KINDS)
    assert all(p["fix"] for p in problems.values())


def _survey_process(project: Path, marker: str) -> tuple[subprocess.Popen[str], int]:
    """``recon survey`` in its own process, stopped in its second table, whose command is
    still running; returns the process and the process group of that command."""
    _mission(project, "m", {"collectors": [
        {"name": "first", "type": "cli", "run": "echo one; echo two"},
        {"name": "slow", "type": "cli", "run": f"echo partial; sleep 30; : {marker}"},
    ]})  # fmt: skip
    recon_command = Path(sys.executable).with_name("recon")
    survey = subprocess.Popen(
        [str(recon_command), "survey", "m", "--json"],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    for _ in range(100):
        found = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
        if found.stdout.strip():
            return survey, int(found.stdout.split()[0])
        time.sleep(0.1)
    survey.kill()
    pytest.fail("the slow table's command never started")


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def test_a_terminated_survey_reads_incomplete_and_leaves_no_command_running(project):
    """Regression: only Ctrl-C marked an archive incomplete. A survey stopped by SIGTERM left
    an archive that status and query called ok, and its command kept running."""
    survey, command_group = _survey_process(project, f"recon-sigterm-{uuid.uuid4().hex}")
    survey.send_signal(signal.SIGTERM)
    _out, err = survey.communicate(timeout=10)
    time.sleep(0.2)
    alive = _group_alive(command_group)
    if alive:
        os.killpg(command_group, signal.SIGKILL)

    assert survey.returncode == recon.EXIT_CODES["interrupted"]
    assert json.loads(err.strip().splitlines()[-1])["error"]["kind"] == "interrupted"
    assert not alive, "the command of the table being collected outlived the survey"
    latest = _doc(_invoke("status", "--json"))["missions"][0]["latest"]
    assert latest["state"] == "incomplete"
    query = _doc(_invoke("query", "m", "SELECT * FROM first", "--json"))
    assert query["state"] == "incomplete"
    [archive] = (project / ".recon" / "m" / "archive").iterdir()
    assert not list(archive.glob("*.tmp")), "a half-written table was left behind"


def test_a_killed_survey_reads_incomplete(project):
    """SIGKILL runs no handler, so the archive is incomplete from the moment it is made."""
    survey, command_group = _survey_process(project, f"recon-sigkill-{uuid.uuid4().hex}")
    survey.kill()
    survey.communicate(timeout=10)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(command_group, signal.SIGKILL)  # nothing can reap it for a killed recon

    latest = _doc(_invoke("status", "--json"))["missions"][0]["latest"]
    assert latest["state"] == "incomplete" and latest["format_version"] is None


def test_ctrl_c_under_json_prints_the_envelope_and_nothing_before_it(project):
    """Regression (N2): click answered SIGINT with a blank line on stderr, so a reader that
    took the first line of stderr got "" instead of the envelope."""
    survey, command_group = _survey_process(project, f"recon-sigint-{uuid.uuid4().hex}")
    survey.send_signal(signal.SIGINT)
    _out, err = survey.communicate(timeout=10)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(command_group, signal.SIGKILL)
    assert survey.returncode == 130
    first = err.splitlines()[0]
    assert json.loads(first)["error"]["kind"] == "interrupted"


# --- output for people, and stdin ------------------------------------------------------------


def test_a_long_path_is_not_wrapped_off_a_terminal(tmp_path, monkeypatch):
    """Regression (N1): piped, human output was wrapped at 80 columns, splitting a path."""
    deep = tmp_path / ("a-directory-with-a-long-name-" * 3) / "project"
    (deep / ".git").mkdir(parents=True)
    monkeypatch.chdir(deep)
    monkeypatch.setenv("COLUMNS", "80")
    config = deep / ".recon" / "m" / "config.yaml"
    assert len(str(config)) > 80
    mission = {
        "catalog": [{"name": "repo", "type": "local", "url": "."}],
        "collectors": [{"name": "ls", "type": "cli", "source": "repo", "run": "ls {url}"}],
    }
    result = _invoke("init", "m", "-c", "-", stdin=yaml.safe_dump(mission))
    assert f"Created: {config}" in result.stderr
    plan = _invoke("survey", "m", "-n")
    assert f"ls {deep}" in plan.stdout, "a table cell is not wrapped either"


def test_capture_on_a_terminal_says_what_it_wants_instead_of_waiting(project):
    """Regression (N8): with nothing piped in, capture waited on the terminal in silence."""
    import pty

    _invoke("init", "mcp", "-t", "mcp-captures")
    leader, follower = pty.openpty()
    try:
        result = subprocess.run(
            [str(Path(sys.executable).with_name("recon")), "capture", "mcp", "--json"],
            cwd=project,
            stdin=follower,
            capture_output=True,
            text=True,
            timeout=20,
        )
    finally:
        os.close(leader)
        os.close(follower)
    assert result.returncode == 2
    error = json.loads(result.stderr)["error"]
    assert error["kind"] == "usage" and "pipe" in error["fix"]


def test_a_config_error_says_when_checking_stopped_early(project):
    _mission(project, "m", {"collectors": [{"name": "c", "type": "cli", "patterns": "x"}]})
    result = _invoke("survey", "m", "-n", "--json")
    assert result.exit_code == 3
    assert _error(result)["complete"] is False
    human = _invoke("survey", "m", "-n")
    assert "checking stopped early" in human.stderr
    _mission(project, "n", {"collectors": [{"name": "c", "type": "ftp"}]})
    assert _error(_invoke("survey", "n", "-n", "--json"))["complete"] is True


# --- the directory a command runs in ----------------------------------------------------------


class TestTheDirectoryACommandRunsIn:
    def test_a_local_source_whose_directory_is_missing_is_a_plan_problem(
        self, project, monkeypatch
    ):
        """Regression: with ``url: does-not-exist`` and ``run: pwd``, run from a
        subdirectory, the dry run named the missing directory as the cwd with no problem, and
        the survey said ok with one row: the subdirectory."""
        mission = {
            "catalog": [{"name": "repo", "type": "local", "url": "does-not-exist"}],
            "collectors": [{"name": "where", "type": "cli", "source": "repo", "run": "pwd"}],
        }
        _mission(project, "m", mission)
        sub = project / "sub"
        sub.mkdir()
        monkeypatch.chdir(sub)

        planned = _invoke("survey", "m", "-n", "--json")
        assert planned.exit_code == 3
        [problem] = _error(planned)["problems"]
        assert problem["path"] == "catalog.0.url"
        assert problem["got"] == str(project / "does-not-exist")
        surveyed = _invoke("survey", "m", "--json")
        assert surveyed.exit_code == 3
        assert not (project / ".recon" / "m" / "archive").exists(), "nothing ran"

    def test_the_dry_run_shows_the_directory_the_command_runs_in(self, project, monkeypatch):
        (project / "src").mkdir()
        mission = {
            "catalog": [{"name": "repo", "type": "local", "url": "src"}],
            "collectors": [
                {"name": "where", "type": "cli", "source": "repo", "run": "pwd"},
                {"name": "anywhere", "type": "cli", "run": "pwd"},
            ],
        }
        _mission(project, "m", mission)
        sub = project / "sub"
        sub.mkdir()
        monkeypatch.chdir(sub)

        runs = {r["output"]: r for r in _doc(_invoke("survey", "m", "-n", "--json"))["runs"]}
        assert runs["where"]["cwd"] == str(project / "src")
        assert runs["anywhere-repo"]["cwd"] == str(project / "src")
        human = unstyle(_invoke("survey", "m", "-n").stdout)
        assert f"pwd (in {project / 'src'})" in human
        rows = _doc(_invoke("survey", "m", "--json"))
        assert rows["status"] == "ok"
        lines = recon.query("m", 'SELECT line FROM "where"', root=project).rows
        assert [Path(row["line"]).resolve() for row in lines] == [(project / "src").resolve()]

    def test_a_command_with_no_local_source_is_shown_running_in_the_working_directory(
        self, project
    ):
        _mission(project, "m", {"collectors": [{"name": "where", "type": "cli", "run": "pwd"}]})
        [run] = _doc(_invoke("survey", "m", "-n", "--json"))["runs"]
        assert run["cwd"] is None
        assert "pwd (in the working directory)" in unstyle(_invoke("survey", "m", "-n").stdout)


# --- the library, pointed at a project -------------------------------------------------------


def test_the_library_works_on_a_project_without_changing_directory(tmp_path, monkeypatch):
    """S13: every operation takes ``root``; a ``local`` source's relative url is relative to
    it, so the default template scans the project, not the caller's directory."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    root = tmp_path / "project"
    root.mkdir()
    (root / "only-here.txt").write_text("x")
    mission = {
        "catalog": [{"name": "repo", "type": "local", "url": "."}],
        "collectors": [{"name": "files", "type": "cli", "source": "repo", "run": "ls"}],
    }
    cfg = tmp_path / "mission.yaml"
    cfg.write_text(yaml.safe_dump(mission))

    created = recon.init("m", config=cfg, root=root)
    assert Path(created.config) == root / ".recon" / "m" / "config.yaml"
    plan = recon.plan("m", root=root)
    assert plan.runs[0]["cwd"] == str(root)
    survey = recon.survey("m", root=root)
    assert survey.status == "ok"
    rows = recon.query("m", "SELECT line FROM files", root=root).rows
    assert {"line": "only-here.txt"} in rows
    assert [m.name for m in recon.status(root=root).missions] == ["m"]
    assert not (elsewhere / ".recon").exists()

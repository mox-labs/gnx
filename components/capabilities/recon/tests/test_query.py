"""Querying an archive: what a table's view holds."""

import datetime
import json
import subprocess
import sys
from importlib.metadata import requires

import pytest
from packaging.requirements import Requirement

from recon.adapters._out.duckdb_query import SQL_DOCS, UNNEST_DOCS, DuckdbQuery
from recon.domain.errors import QueryError


def execute(archive, sql):
    rows = DuckdbQuery().execute(archive, sql)
    return rows.columns, rows.rows


def _archive(tmp_path, tables: dict[str, list[dict]]):
    archive = tmp_path / "m" / "archive" / "2026-01-01-000000-000000"
    archive.mkdir(parents=True)
    for name, records in tables.items():
        (archive / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    return archive


class TestConfinement:
    """SQL is written by whoever read the tables, and the tables are collected text. A query
    reads this mission's archives and nothing else: no other file, no extension, no network.

    Before this, `SELECT * FROM read_text('/etc/hosts')` returned the file.
    """

    @pytest.fixture
    def archive(self, tmp_path):
        archive = _archive(tmp_path, {"t": [{"a": 1}]})
        earlier = archive.parent / "2025-12-31-000000-000000"
        earlier.mkdir()
        (earlier / "t.jsonl").write_text(json.dumps({"a": 0}) + "\n")
        (tmp_path / "m" / "config.yaml").write_text("collectors: []  # mission-secret\n")
        (tmp_path / "private.txt").write_text("hunter2\n")
        return archive

    def _refused(self, archive, sql):
        with pytest.raises(QueryError) as excinfo:
            execute(archive, sql)
        assert excinfo.value.kind == "query"
        assert "hunter2" not in str(excinfo.value) and "mission-secret" not in str(excinfo.value)
        return excinfo.value

    def test_the_archive_s_own_tables_are_readable(self, archive):
        assert execute(archive, "SELECT a FROM t")[1] == [(1,)]

    def test_another_archive_of_the_same_mission_is_readable(self, archive):
        """The documented cross-archive diff."""
        earlier = archive.parent / "2025-12-31-000000-000000" / "t.jsonl"
        sql = f"SELECT curr.a, prev.a FROM t curr, read_json_auto('{earlier}') prev"
        assert execute(archive, sql)[1] == [(1, 0)]

    @pytest.mark.parametrize("reader", ["read_text", "read_blob", "read_csv"])
    def test_a_file_outside_the_mission_s_archives_is_refused(self, archive, tmp_path, reader):
        error = self._refused(archive, f"SELECT * FROM {reader}('{tmp_path / 'private.txt'}')")
        assert str(archive.parent) in error.fix

    def test_the_mission_s_own_config_is_outside_its_archives(self, archive, tmp_path):
        self._refused(archive, f"SELECT * FROM read_text('{tmp_path / 'm' / 'config.yaml'}')")

    def test_dot_dot_does_not_escape(self, archive):
        self._refused(archive, f"SELECT * FROM read_text('{archive}/../../../private.txt')")

    def test_listing_a_directory_outside_is_refused(self, archive, tmp_path):
        self._refused(archive, f"SELECT * FROM glob('{tmp_path}/*')")

    def test_writing_outside_is_refused(self, archive, tmp_path):
        self._refused(archive, f"COPY (SELECT 1) TO '{tmp_path / 'written.csv'}'")
        assert not (tmp_path / "written.csv").exists()

    @pytest.mark.parametrize(
        "sql",
        [
            "SET enable_external_access=true",
            "SET allowed_directories=['/']",
            "RESET allowed_directories",
            "SET autoload_known_extensions=true",
            "SET autoinstall_known_extensions=true",
        ],
    )
    def test_the_restrictions_cannot_be_lifted_from_sql(self, archive, sql):
        self._refused(archive, sql)

    @pytest.mark.parametrize("sql", ["INSTALL httpfs", "LOAD httpfs"])
    def test_no_extension_is_installed_or_loaded(self, archive, sql):
        self._refused(archive, sql)

    def test_a_url_is_not_fetched(self, archive):
        self._refused(archive, "SELECT * FROM read_csv('http://127.0.0.1:9/x.csv')")
        self._refused(archive, "SELECT * FROM read_json_auto('https://127.0.0.1:9/x.json')")


class TestOnlyReads:
    """Regression: the confinement let SQL write wherever it may read, so ``COPY ... TO`` and
    ``ATTACH`` wrote files into the mission's archives, ``meta.yaml`` included, and a planted
    file became a table of the next query. A query runs SELECT statements and nothing else."""

    @pytest.fixture
    def archive(self, tmp_path):
        archive = _archive(tmp_path, {"t": [{"a": 1}]})
        (archive / "meta.yaml").write_text("status: ok\n")
        return archive

    @pytest.mark.parametrize(
        ("sql", "statement"),
        [
            ("COPY (SELECT 99 AS a) TO '{archive}/planted.jsonl'", "COPY"),
            ("COPY (SELECT 1 AS status) TO '{archive}/meta.yaml' (FORMAT csv)", "COPY"),
            ("ATTACH '{archive}/x.duckdb' AS x", "ATTACH"),
            ("CREATE TABLE planted AS SELECT 1 AS a", "CREATE"),
            ("CREATE VIEW t2 AS SELECT * FROM t", "CREATE"),
            ("EXPORT DATABASE '{archive}/dump'", "EXPORT"),
            ("SELECT a FROM t; COPY t TO '{archive}/planted.jsonl'", "COPY"),
        ],
    )
    def test_a_statement_that_is_not_a_select_is_refused_and_writes_nothing(
        self, archive, sql, statement
    ):
        before = sorted(p.name for p in archive.iterdir())
        with pytest.raises(QueryError) as excinfo:
            execute(archive, sql.format(archive=archive))
        error = excinfo.value
        assert error.kind == "query"
        assert error.details == {"statements": [statement]}
        assert statement in str(error) and "SELECT" in error.fix
        assert sorted(p.name for p in archive.iterdir()) == before
        assert (archive / "meta.yaml").read_text() == "status: ok\n"

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT a FROM t",
            "WITH x AS (SELECT a FROM t) SELECT a FROM x",
            "FROM t",
            "SELECT a FROM t UNION ALL SELECT a FROM t",
            "SELECT a FROM t EXCEPT SELECT 2",
            "DESCRIBE t",
            "SUMMARIZE t",
            "SELECT 1; SELECT a FROM t",
        ],
    )
    def test_a_select_in_any_of_its_forms_runs(self, archive, sql):
        assert execute(archive, sql)[1]

    def test_sql_that_does_not_parse_is_still_told_what_duckdb_said(self, archive):
        with pytest.raises(QueryError) as excinfo:
            execute(archive, "SELEC 1")
        assert 'syntax error at or near "SELEC"' in excinfo.value.fix
        assert "statements" not in excinfo.value.details


class TestTimestamps:
    """Regression: recon never declared ``pytz``, which DuckDB needs to return a TIMESTAMP
    WITH TIME ZONE, and only the workspace's dev group supplied it. Installed on its own,
    recon failed every such query as ``unknown``, "report this as a bug"."""

    @pytest.fixture
    def archive(self, tmp_path):
        # GitHub's published_at shape, as docs/how-to/first-survey.md collects it.
        return _archive(tmp_path, {"releases": [{"published": "2026-03-15T18:42:31Z"}]})

    def test_a_timestamp_table_can_be_queried(self, archive):
        sql = "SELECT published, published::TIMESTAMPTZ AS zoned FROM releases"
        [(published, zoned)] = execute(archive, sql)[1]
        assert published == datetime.datetime(2026, 3, 15, 18, 42, 31)
        assert zoned.tzinfo is not None

    def test_recon_declares_what_duckdb_needs_and_the_duckdb_it_is_tested_with(self):
        declared = {r.name: r for r in map(Requirement, requires("recon") or []) if not r.marker}
        assert "pytz" in declared
        assert "1.5.5" in declared["duckdb"].specifier
        assert "2.0.0" not in declared["duckdb"].specifier

    def test_without_pytz_a_zoned_timestamp_is_a_query_error_that_says_so(self, archive):
        """In a process of its own, where ``import pytz`` fails as it does when the package
        is missing: the error names it, and the cast that needs no time zone."""
        script = (
            "import sys; sys.modules['pytz'] = None\n"
            "from pathlib import Path\n"
            "from recon.adapters._out.duckdb_query import DuckdbQuery\n"
            "from recon.domain.errors import QueryError\n"
            "try:\n"
            "    DuckdbQuery().execute(Path(sys.argv[1]), sys.argv[2])\n"
            "except QueryError as error:\n"
            "    print(error.kind, error.fix, sep='\\n')\n"
        )
        sql = "SELECT published::TIMESTAMPTZ AS zoned FROM releases"
        result = subprocess.run(
            [sys.executable, "-c", script, str(archive), sql],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        kind, fix = result.stdout.split("\n", 1)
        assert kind == "query"
        assert "pytz" in fix and "::TIMESTAMP" in fix

    def test_an_import_error_raised_as_itself_is_a_query_error_that_says_so(
        self, archive, monkeypatch
    ):
        """DuckDB can also let the import error through rather than wrap it."""
        import duckdb

        class Result:
            def __init__(self, real):
                self.description = real.description

            def fetchall(self):
                raise ModuleNotFoundError("No module named 'pytz'", name="pytz")

        class Connection:
            def __init__(self, real):
                self._real = real

            def __getattr__(self, name):
                return getattr(self._real, name)

            def execute(self, sql):
                result = self._real.execute(sql)
                return Result(result) if "zoned" in sql else result

        connect = duckdb.connect
        monkeypatch.setattr(duckdb, "connect", lambda: Connection(connect()))
        with pytest.raises(QueryError) as excinfo:
            execute(archive, "SELECT published AS zoned FROM releases")
        assert excinfo.value.kind == "query"
        assert "pytz" in excinfo.value.fix and "::TIMESTAMP" in excinfo.value.fix


class TestEmptyTable:
    """An empty table's file holds one sentinel line so DuckDB can read it. The sentinel is
    not a row: `count(*)` on an empty table is 0."""

    def test_count_is_zero(self, tmp_path):
        archive = _archive(tmp_path, {"nothing": [{"_empty": True}], "two": [{"a": 1}, {"a": 2}]})

        assert execute(archive, "SELECT count(*) FROM nothing")[1] == [(0,)]
        assert execute(archive, "SELECT * FROM nothing")[1] == []
        assert execute(archive, "SELECT count(*) FROM two")[1] == [(2,)]

    def test_the_documented_filter_still_runs(self, tmp_path):
        """Queries written against the sentinel keep working."""
        archive = _archive(tmp_path, {"nothing": [{"_empty": True}]})
        sql = "SELECT count(*) FROM nothing WHERE NOT coalesce(_empty, false)"
        assert execute(archive, sql)[1] == [(0,)]

    def test_a_record_that_merely_has_the_field_is_kept(self, tmp_path):
        archive = _archive(tmp_path, {"t": [{"_empty": True, "a": 1}, {"_empty": True, "a": 2}]})
        assert execute(archive, "SELECT count(*) FROM t")[1] == [(2,)]


class TestOneUnreadableTable:
    """Regression: a table DuckDB could not make a view of (an OpenAlex record keeps its
    ``abstract_inverted_index``, keyed by word, where "the" and "The" are one struct field)
    failed every query on every table, as ``unknown`` "report a bug in recon"."""

    WORD_KEYED = {"id": 1, "abstract_inverted_index": {"the": [0], "The": [3], "cat": [1]}}

    @pytest.fixture
    def archive(self, tmp_path):
        return _archive(tmp_path, {"oa-works": [self.WORD_KEYED], "good": [{"a": 1}, {"a": 2}]})

    def test_a_query_of_another_table_runs_and_warns_naming_the_table_why_and_the_fix(
        self, archive
    ):
        rows = DuckdbQuery().execute(archive, "SELECT a FROM good ORDER BY a")

        assert rows.rows == [(1,), (2,)]
        [warning] = rows.warnings
        assert warning.kind == "table_unreadable" and warning.got == "oa_works"
        assert "Duplicate name" in warning.message
        assert "ignore_errors" not in warning.message, "DuckDB's hint, which a view cannot take"
        assert "$inverted_index" in warning.fix and "abstract_inverted_index" in warning.fix
        assert warning.source == str(archive / "oa-works.jsonl")

    def test_a_query_of_the_table_is_a_query_error_with_the_fix_not_an_unknown_one(self, archive):
        with pytest.raises(QueryError) as excinfo:
            execute(archive, "SELECT id FROM OA_Works")

        error = excinfo.value
        assert error.kind == "query"
        assert "oa_works cannot be queried" in error.message
        assert "$inverted_index" in error.fix
        assert error.details == {"table": "oa_works", "file": str(archive / "oa-works.jsonl")}

    def test_the_fix_s_raw_reading_works_inside_the_sandbox(self, archive):
        """The way round the fix names is one the confined connection allows."""
        file = archive / "oa-works.jsonl"
        sql = f"SELECT json->>'id' FROM read_ndjson_objects('{file}')"
        assert execute(archive, sql)[1] == [("1",)]

    def test_another_error_still_names_only_the_tables_that_can_be_queried(self, archive):
        with pytest.raises(QueryError) as excinfo:
            execute(archive, "SELECT * FROM nope")
        assert excinfo.value.fix == "query one of these tables: good"

    def test_a_readable_archive_has_no_warning(self, tmp_path):
        archive = _archive(tmp_path, {"t": [{"a": 1}]})
        assert DuckdbQuery().execute(archive, "SELECT a FROM t").warnings == []

    def test_the_survey_s_check_names_the_table_with_the_query_s_fix(self, archive):
        """Regression: a survey exited 0 with no warning; the first sign was a failed query.
        The survey asks this of its archive."""
        [warning] = DuckdbQuery().unreadable(archive)

        assert warning.kind == "table_unreadable" and warning.got == "oa_works"
        assert warning.message.startswith("table oa_works cannot be queried: ")
        assert warning.fix == DuckdbQuery().execute(archive, "SELECT 1").warnings[0].fix

    def test_a_readable_archive_or_one_with_no_tables_has_nothing_unreadable(self, tmp_path):
        assert DuckdbQuery().unreadable(_archive(tmp_path, {"t": [{"a": 1}]})) == []
        empty = tmp_path / "n" / "archive" / "a"
        empty.mkdir(parents=True)
        assert DuckdbQuery().unreadable(empty) == []


class TestTheFixIsAboutWhatFailed:
    """Regression: every SQL error's fix was "query one of these tables", so ``UNNEST not
    supported here`` sent an agent to look for another table."""

    @pytest.fixture
    def archive(self, tmp_path):
        papers = [{"title": "p", "authors": ["a", "b"]}, {"title": "q", "authors": ["a"]}]
        return _archive(tmp_path, {"papers": papers})

    def _fix(self, archive, sql):
        with pytest.raises(QueryError) as excinfo:
            execute(archive, sql)
        assert excinfo.value.kind == "query"
        return excinfo.value.fix

    def test_unnest_beside_an_aggregate_is_given_the_subquery_that_works(self, archive):
        fix = self._fix(archive, "SELECT unnest(authors) au, count(*) FROM papers GROUP BY au")

        assert "UNNEST not supported here" in fix and UNNEST_DOCS in fix
        assert "query one of these tables" not in fix
        pattern = (
            "SELECT item, count(*) AS n FROM (SELECT unnest(<list column>) AS item FROM "
            "<table>) GROUP BY item ORDER BY n DESC"
        )
        assert pattern in fix
        filled = pattern.replace("<list column>", "authors").replace("<table>", "papers")
        assert execute(archive, filled)[1] == [("a", 2), ("b", 1)]

    @pytest.mark.parametrize(
        ("sql", "says"),
        [
            ("SELEC 1", 'syntax error at or near "SELEC"'),
            ("SELECT nope FROM papers", 'Referenced column "nope" not found'),
            ("SELECT no_such_function(title) FROM papers", "no_such_function"),
        ],
    )
    def test_another_sql_error_quotes_duckdb_and_points_at_its_sql(self, archive, sql, says):
        fix = self._fix(archive, sql)

        assert fix.startswith("correct the SQL, which DuckDB refused (")
        assert says in fix and SQL_DOCS in fix
        assert "query one of these tables" not in fix

    def test_only_a_missing_table_is_told_the_tables_there_are(self, archive):
        assert self._fix(archive, "SELECT * FROM paper") == "query one of these tables: papers"

    def test_a_binder_error_over_a_glob_of_archives_says_the_archives_differ(self, tmp_path):
        """Regression: ``.recon/<m>/archive/*/gh-repos.jsonl`` failed with ``Referenced
        column "name" not found``, one archive raw and another normalized, and the fix said
        to query one of the tables."""
        raw = _archive(tmp_path, {"gh-repos": [{"full_name": "o/r", "stargazers_count": 3}]})
        normalized = raw.parent / "2026-01-02-000000-000000"
        normalized.mkdir()
        (normalized / "gh-repos.jsonl").write_text(json.dumps({"name": "o/r", "stars": 3}) + "\n")
        glob = f"{raw.parent}/*/gh-repos.jsonl"

        fix = self._fix(
            normalized, f"SELECT ordinality, owner FROM read_json_auto('{glob}') WITH ORDINALITY"
        )

        assert f"'{glob}' reads the table from every archive it matches" in fix
        assert f"'{normalized}/<table>.jsonl'" in fix and "recon status m --json" in fix
        assert "union_by_name=true" in fix and "query one of these tables" not in fix
        one_file = normalized / "gh-repos.jsonl"
        sql = f"SELECT ordinality, name FROM read_json_auto('{one_file}') WITH ORDINALITY"
        assert execute(normalized, sql)[1] == [(1, "o/r")]

    def test_a_binder_error_without_a_glob_is_not_blamed_on_archives(self, archive):
        fix = self._fix(archive, f"SELECT nope FROM read_json_auto('{archive}/papers.jsonl')")
        assert fix.startswith("correct the SQL") and "archive it matches" not in fix


class TestRowCitation:
    """The documented way to cite a row: its line in its table's file, by ``WITH
    ORDINALITY`` on the file. Each record is one line, so the ordinal is the line number."""

    def test_the_ordinal_is_the_line_number(self, tmp_path):
        archive = _archive(tmp_path, {"t": [{"a": "x"}, {"a": "y"}, {"a": "z"}]})
        sql = (
            f"SELECT ordinality AS line, a FROM read_json_auto('{archive / 't.jsonl'}') "
            "WITH ORDINALITY WHERE a = 'y'"
        )
        assert execute(archive, sql)[1] == [(2, "y")]
        lines = (archive / "t.jsonl").read_text().splitlines()
        assert json.loads(lines[2 - 1]) == {"a": "y"}

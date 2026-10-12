"""Querying an archive: what a table's view holds."""

import json

import pytest

from recon.application.query import execute
from recon.domain.exceptions import QueryError


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

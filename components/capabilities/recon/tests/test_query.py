"""Querying an archive: what a table's view holds."""

import json

from recon.application.query import execute


def _archive(tmp_path, tables: dict[str, list[dict]]):
    archive = tmp_path / "m" / "archive" / "2026-01-01-000000-000000"
    archive.mkdir(parents=True)
    for name, records in tables.items():
        (archive / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    return archive


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

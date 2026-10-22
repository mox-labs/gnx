"""SQL over an archive's tables, in DuckDB.

Every table is a view named after its file (non-identifier characters become ``_``). The
capture log is not a table. A table that collected nothing is a view with no rows: its one
sentinel line is filtered out.

A view is made per table, and one that cannot be made does not stop the others. DuckDB reads
a JSON object as a struct, so an object keyed by data rather than by field names (OpenAlex's
``abstract_inverted_index``, keyed by word) can hold two keys that differ only in case,
which no struct can: that table gets no view. A query that does not use it runs and is
warned; one that does fails as a ``query`` error that says how to read the table instead.
A survey asks the same question of every table it wrote (:meth:`DuckdbQuery.unreadable`),
so the warning comes before the first query.

A failed query's fix is about what failed: a missing table names the tables there are; any
other SQL error quotes DuckDB and points at its SQL documentation, or at the usual shape of
the query when DuckDB's message names one (``unnest`` beside an aggregate); a binder
error from a glob that spans archives says the archives' columns can differ.

The SQL is not trusted. Whoever writes it has usually just read the tables, and the tables
are collected text, so the connection is confined before any SQL runs: it reads files only
under the mission's ``archive/`` directory (every archive of the mission, which keeps
cross-archive diffs working), installs and loads no extension, and reaches no network. DuckDB
lets SQL write wherever it may read, so a query runs only SELECT statements (``WITH``,
``FROM``, ``DESCRIBE`` and ``SUMMARIZE`` are SELECTs to DuckDB): a ``COPY``, ``ATTACH``,
``CREATE`` or any other statement is refused before anything runs.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from recon.adapters._out.archive_files import is_empty_table, table_files
from recon.domain.archives import Rows
from recon.domain.errors import NotFoundError, Problem, QueryError

if TYPE_CHECKING:
    from pathlib import Path

_NOT_IDENTIFIER = re.compile(r"[^a-zA-Z0-9_]")


def table_name(stem: str) -> str:
    return _NOT_IDENTIFIER.sub("_", stem)


def _confine(conn: Any, readable: Path) -> None:
    """Restrict a fresh connection to reading under ``readable``. Order matters:
    ``allowed_directories`` can only be set while external access is still on, turning
    external access off cannot be undone, and the lock stops the extension settings from
    being switched back."""
    root = str(readable).replace("'", "''")
    conn.execute(f"SET allowed_directories=['{root}']")
    conn.execute("SET autoinstall_known_extensions=false")
    conn.execute("SET autoload_known_extensions=false")
    conn.execute("SET enable_external_access=false")
    conn.execute("SET lock_configuration=true")


def _reads_only(conn: Any, sql: str) -> None:
    """Refuse ``sql`` unless every statement in it is a SELECT, as DuckDB's parser types it."""
    import duckdb

    refused = [
        statement.type.name
        for statement in conn.extract_statements(sql)
        if statement.type != duckdb.StatementType.SELECT
    ]
    if refused:
        raise QueryError(
            f"recon query runs only SELECT statements; this SQL has {', '.join(refused)}",
            fix="ask with a SELECT (WITH, FROM, DESCRIBE and SUMMARIZE are SELECTs too): a "
            "query reads the archive and writes nothing, so COPY, ATTACH, CREATE, INSERT, SET "
            "and every other statement are refused",
            details={"statements": refused},
        )


class DuckdbQuery:
    def tables(self, archive: Path) -> list[str]:
        return [table_name(f.stem) for f in table_files(archive)]

    def execute(self, archive: Path, sql: str) -> Rows:
        import duckdb  # only a query needs it

        archive = archive.absolute()
        files = table_files(archive)
        if not files:
            raise NotFoundError(f"no tables in archive {archive}")
        conn = duckdb.connect()
        try:
            _confine(conn, archive.parent)
            unreadable = _views(conn, files)
            readable = [name for name in self.tables(archive) if name not in unreadable]
            try:
                _reads_only(conn, sql)
                result = conn.execute(sql)
                columns = [d[0] for d in result.description or []]
                rows = result.fetchall() if result.description else []
            except ImportError as exc:  # a module DuckDB needs for a result, raised as itself
                module = exc.name or "a module"
                raise QueryError(
                    f"DuckDB could not import {module}: {exc}", fix=_module_fix(module)
                ) from exc
            except duckdb.Error as exc:
                for name, (file, why) in unreadable.items():
                    if isinstance(exc, duckdb.CatalogException) and _names(sql, name):
                        raise QueryError(
                            f"table {name} cannot be queried: {why}",
                            fix=_unreadable_fix(str(file), why),
                            details={"table": name, "file": str(file)},
                        ) from exc
                raise QueryError(
                    f"SQL error: {exc}\nAvailable tables: {', '.join(readable)}",
                    fix=_fix(exc, archive, readable, sql),
                ) from exc
            warnings = [
                _unreadable(name, file, why, ", and this query did not use it")
                for name, (file, why) in unreadable.items()
            ]
            return Rows(columns=columns, rows=rows, warnings=warnings)
        finally:
            conn.close()

    def unreadable(self, archive: Path) -> list[Problem]:
        import duckdb

        files = table_files(archive.absolute())
        if not files:
            return []
        conn = duckdb.connect()
        try:
            _confine(conn, archive.absolute().parent)
            return [
                _unreadable(name, file, why) for name, (file, why) in _views(conn, files).items()
            ]
        finally:
            conn.close()


def _views(conn: Any, files: list[Path]) -> dict[str, tuple[Path, str]]:
    """Make a view of each table; return the tables that got none, with their file and why."""
    import duckdb

    unreadable: dict[str, tuple[Path, str]] = {}
    for file in files:
        quoted = str(file).replace("'", "''")
        where = " WHERE NOT _empty" if is_empty_table(file) else ""
        try:
            conn.execute(
                f'CREATE VIEW "{table_name(file.stem)}" AS '
                f"SELECT * FROM read_json_auto('{quoted}'){where}"
            )
        except duckdb.Error as exc:
            unreadable[table_name(file.stem)] = (file, _first_line(exc))
    return unreadable


#: DuckDB's own hint on a struct it cannot infer; a view cannot take it, and the fix says
#: what to do instead.
_DUCKDB_HINT = re.compile(r",?\s*try ignore_errors=true\s*$")


def _first_line(exc: Exception) -> str:
    return _DUCKDB_HINT.sub("", str(exc).split("\n", 1)[0])


def _names(sql: str, table: str) -> bool:
    """Whether ``sql`` names ``table``; DuckDB matches names without regard to case."""
    return re.search(rf"(?<![\w\"]){re.escape(table)}(?![\w\"])", sql, re.IGNORECASE) is not None


def _unreadable_fix(file: str, why: str) -> str:
    raw = f"SELECT json FROM read_ndjson_objects('{file}')"
    if "Duplicate name" in why:
        return (
            "a record holds an object keyed by data, not by field names (OpenAlex's "
            "abstract_inverted_index is keyed by word), whose keys DuckDB cannot make "
            "columns of: normalize that field in the collector (abstract: "
            '"abstract_inverted_index|$inverted_index"), or leave it out of normalize:, and '
            f"survey again; until then, read the records as JSON text: {raw}"
        )
    return (
        f"read the records as JSON text, {raw}, and normalize the field at fault in the "
        "collector before surveying again"
    )


def _unreadable(name: str, file: Path, why: str, context: str = "") -> Problem:
    return Problem(
        "",
        f"table {name} cannot be queried{context}: {why}",
        kind="table_unreadable",
        fix=_unreadable_fix(str(file), why),
        got=name,
        source=str(file),
    )


#: DuckDB's SQL documentation, and its page on ``unnest``.
SQL_DOCS = "https://duckdb.org/docs/current/sql/introduction"
UNNEST_DOCS = "https://duckdb.org/docs/current/sql/query_syntax/unnest"

#: A quoted string in the SQL: a path a ``read_*`` function is given.
_LITERAL = re.compile(r"'((?:[^']|'')*)'")
_MISSING_TABLE = re.compile(r"Table with name \S+ does not exist")
_REQUIRED_MODULE = re.compile(r"Required module '([\w.]+)' failed to import")


def _cross_archive_glob(sql: str) -> str | None:
    """The first quoted path in ``sql`` with a wildcard in a directory, which reads a table
    from more than one archive (``archive/*/t.jsonl``); None when there is none."""
    for match in _LITERAL.finditer(sql):
        directory = match.group(1).replace("''", "'").rpartition("/")[0]
        if any(wildcard in directory for wildcard in "*?["):
            return match.group(1)
    return None


def _module_fix(module: str) -> str:
    return (
        f"DuckDB needs the Python module {module} to return this result (pytz for a TIMESTAMP "
        "WITH TIME ZONE): reinstall recon, which declares it, or cast the column to a type "
        "that needs no module, as <column>::TIMESTAMP"
    )


def _fix(exc: Exception, archive: Path, tables: list[str], sql: str) -> str:
    import duckdb

    message = _first_line(exc)
    required = _REQUIRED_MODULE.search(message)
    if required is not None:
        return _module_fix(required.group(1))
    if isinstance(exc, duckdb.PermissionException):
        return (
            f"a query reads only files under {archive.parent}, loads no extension and makes "
            f"no network request: query this archive's tables, or another archive as "
            f"read_json_auto('{archive.parent}/<archive id>/<table>.jsonl')"
        )
    if "Duplicate name" in str(exc):
        return _unreadable_fix("<the .jsonl file>", str(exc))
    if isinstance(exc, duckdb.CatalogException) and _MISSING_TABLE.search(message):
        return f"query one of these tables: {', '.join(tables) or '(none)'}"
    glob = _cross_archive_glob(sql)
    if isinstance(exc, duckdb.BinderException) and glob is not None:
        return (
            f"'{glob}' reads the table from every archive it matches, and archives surveyed "
            "with different normalize: specs (or one without) have different columns, so a "
            f"column one has may not bind ({message}): read one archive's file by its path, "
            f"'{archive}/<table>.jsonl' for the archive this query read (`recon status "
            f"{archive.parent.parent.name} --json` lists every archive's path under "
            "missions[0].history); to read across archives on purpose, give read_json_auto "
            "union_by_name=true and filename=true, and select the columns every archive has"
        )
    if isinstance(exc, duckdb.BinderException) and "UNNEST" in message:
        return (
            f"{message}: DuckDB does not unnest a list in WHERE or GROUP BY, or beside an "
            "aggregate such as count(*); unnest it in a subquery and count or filter outside "
            "it, as SELECT item, count(*) AS n FROM (SELECT unnest(<list column>) "
            f"AS item FROM <table>) GROUP BY item ORDER BY n DESC; see {UNNEST_DOCS}"
        )
    return f"correct the SQL, which DuckDB refused ({message}); DuckDB's SQL: {SQL_DOCS}"

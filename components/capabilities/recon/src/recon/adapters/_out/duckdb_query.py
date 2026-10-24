"""SQL over an archive's tables, in DuckDB.

The tables are the ones the archive's store lists (``ArchiveStore.tables``): this adapter
reads each where the store says it is and knows nothing else of how archives are stored.
Every table is a view named by :func:`~recon.domain.archives.table_name` (non-identifier
characters become ``_``). A table that collected nothing is a view with no rows, whose
columns are the ones the store says it keeps (its collector's ``normalize:`` spec's),
null-typed, so a query of them binds as it would on a full table; ``_empty`` is a column of
it too, for queries written against the sentinel line an empty table's file holds. A query
across a stream's windows (:meth:`DuckdbQuery.execute_across`) makes each view of the
table's file in every window, read as one, by column name.

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
cross-archive diffs working), spills nothing to disk, installs and loads no extension, and
reaches no network. DuckDB
lets SQL write wherever it may read, so a query runs only SELECT statements (``WITH``,
``FROM``, ``DESCRIBE`` and ``SUMMARIZE`` are SELECTs to DuckDB): a ``COPY``, ``ATTACH``,
``CREATE`` or any other statement is refused before anything runs.
"""

from __future__ import annotations

import csv
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from recon.domain.archives import Rows, table_name
from recon.domain.errors import NotFoundError, Problem, QueryError

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from recon.domain.archives import ArchiveStore, StoredTable
    from recon.domain.changes import ChangeOp


def _confine(conn: Any, readable: Path) -> None:
    """Restrict a fresh connection to reading under ``readable``. Order matters:
    ``allowed_directories`` can only be set while external access is still on, turning
    external access off cannot be undone, and the lock stops the extension settings from
    being switched back. Spilling to disk is off: DuckDB adds its temp directory (``.tmp/``
    under the working directory) to the directories SQL may read, so a query could read
    whatever is there; a query that would spill fails for memory instead. The time zone is
    UTC: DuckDB reads ``…Z`` as a timestamp with no zone, and a cast to TIMESTAMPTZ would
    otherwise take the machine's zone for it; a time with an explicit offset keeps it."""
    root = str(readable).replace("'", "''")
    conn.execute("SET TimeZone='UTC'")
    conn.execute("SET temp_directory=''")
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
    """``archives`` is the store whose tables the SQL reads."""

    def __init__(self, archives: ArchiveStore) -> None:
        self._archives = archives

    def execute(self, archive: Path, sql: str) -> Rows:
        return self._execute([archive.absolute()], sql)

    def execute_across(self, archives: Sequence[Path], sql: str) -> Rows:
        return self._execute([archive.absolute() for archive in archives], sql)

    def _execute(self, archives: list[Path], sql: str) -> Rows:
        import duckdb  # only a query needs it

        archive = archives[-1]
        tables = _grouped([_sealed(self._archives.tables(a)) for a in archives])
        if not tables:
            raise NotFoundError(f"no tables in archive {archive}")
        conn = duckdb.connect()
        try:
            _confine(conn, archive.parent)
            unreadable = _views(conn, tables)
            names = list(tables)
            readable = [name for name in names if name not in unreadable]
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

        tables = _grouped([_sealed(self._archives.tables(archive.absolute()))])
        if not tables:
            return []
        conn = duckdb.connect()
        try:
            _confine(conn, archive.absolute().parent)
            return [
                _unreadable(name, file, why) for name, (file, why) in _views(conn, tables).items()
            ]
        finally:
            conn.close()


def _sealed(tables: list[StoredTable]) -> list[StoredTable]:
    """The tables SQL reads: a table not yet sealed holds provisional records, and its file
    is not where the store says it will be."""
    return [table for table in tables if table.sealed]


def _grouped(archives: list[list[StoredTable]]) -> dict[str, list[StoredTable]]:
    """Each table by the name SQL knows it by, with its part in each archive read, in the
    archives' order: one part for a single archive, a window's each for a stream's."""
    grouped: dict[str, list[StoredTable]] = {}
    for tables in archives:
        for table in tables:
            grouped.setdefault(table_name(table.output), []).append(table)
    return grouped


def _views(conn: Any, tables: dict[str, list[StoredTable]]) -> dict[str, tuple[Path, str]]:
    """Make a view of each table, of all its parts; return the tables that got none, with a
    file of theirs and why. Parts read together are joined by column name, so a column one
    part lacks is null in its rows."""
    import duckdb

    unreadable: dict[str, tuple[Path, str]] = {}
    for name, parts in tables.items():
        files = [part.file for part in parts if not part.empty]
        if not files:
            columns = list(dict.fromkeys(column for part in parts for column in part.columns))
            kept = [f"NULL AS {_identifier(column)}" for column in columns]
            if "_empty" not in columns:
                kept.append("NULL::BOOLEAN AS _empty")
            body = f"SELECT {', '.join(kept)} WHERE false"
        elif len(files) == 1:
            body = f"SELECT * FROM read_json_auto({_literal(files[0])})"
        else:
            listed = ", ".join(_literal(file) for file in files)
            body = f"SELECT * FROM read_json_auto([{listed}], union_by_name = true)"
        try:
            conn.execute(f'CREATE VIEW "{name}" AS {body}')
        except duckdb.Error as exc:
            unreadable[name] = ((files or [parts[0].file])[0], _first_line(exc))
    return unreadable


def _literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


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
    if isinstance(exc, duckdb.OutOfMemoryException):
        return (
            "the query needs more memory than DuckDB has, and a recon query never spills to "
            "disk: select fewer columns or rows, or aggregate before joining"
        )
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


# --- the join of a large told index (ruling C6) --------------------------------------------
#
# DuckdbJoin: the KeyJoin (``recon.domain.changes``) of a large told index, on a DuckDB
# connection of its own that runs no SQL but this module's. The told index and the table's keys
# are written, as they are read, to two CSV files in a temporary directory of the join's own,
# which DuckDB reads; every answer is read back a batch at a time. So the process holds a batch,
# and DuckDB at most ``memory_limit``, spilling the rest into the same directory, which
# ``close`` removes. The connection is confined before it reads anything, as the query's is:
# it reads and spills only under its directory, loads no extension and reaches no network.
# A key goes to DuckDB as the hex of its UTF-8 bytes (a lone surrogate passed through as its
# code point's bytes, which DuckDB's text would refuse) and comes back decoded: hex orders as
# its bytes do, and UTF-8's bytes as the code points the in-memory join sorts by.

#: By default, the most memory the join's DuckDB holds before it spills to disk.
MEMORY_LIMIT = "96MB"

#: The rows read back from DuckDB at a time.
_BATCH = 10_000

#: The table's keys, grouped: each key's first ordinal, its content, and whether it has two
#: (``amb``). A content (a hash of 16 bytes) is two 64-bit numbers, ``a`` and ``b``, and two
#: contents differ when the least and the greatest of either half do: DuckDB aggregates numbers
#: out of core, where text, or a count of distinct values, it holds in memory.
_KEYS = """
CREATE TABLE nk AS
SELECT k, min(i) AS i, min(a) AS a, min(b) AS b, min(a) <> max(a) OR min(b) <> max(b) AS amb
FROM new_keys GROUP BY k
"""


class DuckdbJoin:
    """Joins on DuckDB within ``memory_limit`` and ``threads``."""

    def __init__(self, *, memory_limit: str = MEMORY_LIMIT, threads: int = 1) -> None:
        self._memory_limit = memory_limit
        self._threads = threads

    def join(
        self, told: Iterable[tuple[str, str]], new: Iterable[tuple[int, str, str]]
    ) -> _DuckdbJoined:
        return _DuckdbJoined(told, new, self._memory_limit, self._threads)


class _DuckdbJoined:
    def __init__(
        self,
        told: Iterable[tuple[str, str]],
        new: Iterable[tuple[int, str, str]],
        memory_limit: str,
        threads: int,
    ) -> None:
        import duckdb  # only a large comparison needs it

        self._directory = tempfile.TemporaryDirectory(prefix="recon-join-")
        home = Path(self._directory.name)
        try:
            _write_csv(home / "told.csv", ((_hex(k), *_halves(h)) for k, h in told))
            _write_csv(home / "new.csv", ((i, _hex(k), *_halves(h)) for i, k, h in new))
            # A database file of the join's own, so its tables page to disk as its memory fills.
            self._conn: Any = duckdb.connect(str(home / "join.duckdb"))
            _confine_join(self._conn, home, memory_limit, threads)
            told_csv = _read_csv(home / "told.csv", "k", "a", "b")
            new_csv = _read_csv(home / "new.csv", "i", "k", "a", "b")
            self._conn.execute(f"CREATE TABLE told AS SELECT * FROM {told_csv}")
            self._conn.execute(f"CREATE TABLE new_keys AS SELECT * FROM {new_csv}")
            self._conn.execute(_KEYS)
            self.ambiguous: int = self._count("SELECT count(*) FROM nk WHERE amb")
        except BaseException:
            self.close()
            raise

    def ops(self) -> Iterator[tuple[int, ChangeOp]]:
        return self._rows(
            "SELECT nk.i, CASE WHEN t.k IS NULL THEN 'insert' ELSE 'update' END "
            "FROM nk LEFT JOIN told t ON t.k = nk.k "
            "WHERE NOT nk.amb AND (t.k IS NULL OR t.a <> nk.a OR t.b <> nk.b) ORDER BY nk.i"
        )

    def gone(self) -> Iterator[str]:
        return (
            _key(row[0])
            for row in self._rows("SELECT k FROM told ANTI JOIN nk USING (k) ORDER BY k")
        )

    def told(self, *, deletes: bool) -> Iterator[tuple[str, str]]:
        rows = self._rows(f"SELECT k, a, b FROM ({self._folded(deletes)}) ORDER BY k")
        return ((_key(k), f"{a:016x}{b:016x}") for k, a, b in rows)

    def told_count(self, *, deletes: bool) -> int:
        return self._count(f"SELECT count(*) FROM ({self._folded(deletes)})")

    def _count(self, sql: str) -> int:
        row = self._conn.execute(sql).fetchone()
        assert row is not None  # a count has a row
        return int(row[0])

    def close(self) -> None:
        conn = getattr(self, "_conn", None)
        if conn is not None:
            conn.close()
            self._conn = None
        self._directory.cleanup()

    def _folded(self, deletes: bool) -> str:
        """The told index once this table is told: its unambiguous keys, and, without deletes
        (told only of a table with no ambiguous key), every other key told before."""
        kept = "SELECT k, a, b FROM nk WHERE NOT amb"
        if deletes:
            return kept
        others = "SELECT k, a, b FROM told ANTI JOIN (SELECT k FROM nk WHERE NOT amb) USING (k)"
        return f"{kept} UNION ALL {others}"

    def _rows(self, sql: str) -> Iterator[Any]:
        cursor = self._conn.execute(sql)
        while rows := cursor.fetchmany(_BATCH):
            yield from (tuple(row) for row in rows)


def _hex(key: str) -> str:
    """A key as DuckDB holds it: the hex of its UTF-8 bytes, a lone surrogate passed through."""
    return key.encode("utf-8", "surrogatepass").hex()


def _key(held: str) -> str:
    """A key as DuckDB held it, back as its text."""
    return bytes.fromhex(held).decode("utf-8", "surrogatepass")


def _halves(content: str) -> tuple[int, int]:
    """A content, 32 hex digits, as two unsigned 64-bit numbers."""
    return int(content[:16], 16), int(content[16:], 16)


def _write_csv(path: Path, rows: Iterable[tuple[Any, ...]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator="\n")
        for row in rows:
            writer.writerow(row)


def _read_csv(path: Path, *columns: str) -> str:
    types = {"i": "BIGINT", "k": "VARCHAR", "a": "UBIGINT", "b": "UBIGINT"}
    named = ", ".join(f"'{column}': '{types[column]}'" for column in columns)
    where = str(path).replace("'", "''")
    return (
        f"read_csv('{where}', columns={{{named}}}, header=false, delim=',', quote='\"', "
        "escape='\"', auto_detect=false)"
    )


def _confine_join(conn: Any, home: Path, memory_limit: str, threads: int) -> None:
    """Hold a fresh connection to reading and spilling under ``home``, within its memory and
    threads, before it reads anything; no extension, no network, and its settings locked."""
    root = str(home).replace("'", "''")
    conn.execute(f"SET memory_limit='{memory_limit}'")
    conn.execute(f"SET threads={int(threads)}")
    conn.execute("SET preserve_insertion_order=false")
    conn.execute(f"SET temp_directory='{root}/spill'")
    conn.execute(f"SET allowed_directories=['{root}']")
    conn.execute("SET autoinstall_known_extensions=false")
    conn.execute("SET autoload_known_extensions=false")
    conn.execute("SET enable_external_access=false")
    conn.execute("SET lock_configuration=true")

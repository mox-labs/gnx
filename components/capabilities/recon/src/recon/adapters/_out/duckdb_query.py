"""SQL over archives' tables, in DuckDB.

The tables are the ones the archive adapter this is built with lists (:class:`TableFiles`):
this adapter reads each where that adapter says it is, and knows nothing else of how archives
are stored; no location reaches a caller, a document or an error. Every table is a view
named by :func:`~recon.core.domain.archives.table_name` (non-identifier characters become ``_``).
A table that collected nothing is a view with no rows, whose columns are the ones the adapter
says it keeps (its collector's ``normalize:`` spec's), null-typed, so a query of them binds as
it would on a full table; ``_empty`` is a column of it too, for queries written against the
sentinel line an empty table holds. A query across a stream's windows makes each view of the
table in every window, read as one, by column name.

Beside each table's view, in schemas of their own:

- ``json.<table>``: each record as its JSON text, the one column ``json``, for records SQL
  cannot make columns of (``json->>'id'``);
- ``lines.<table>``, over one archive: each record with ``line``, its place in the table
  counted from 1 in the order it was landed, before any ``WHERE`` or ``ORDER BY``: what a row
  is cited by;
- ``baseline.<table>``, given a baseline archive: that archive's table, so a query asks what is
  new since it (``... EXCEPT SELECT ... FROM baseline.<table>``).

A view is made per table, and one that cannot be made does not stop the others. DuckDB reads
a JSON object as a struct, so an object keyed by data rather than by field names (OpenAlex's
``abstract_inverted_index``, keyed by word) can hold two keys that differ only in case,
which no struct can: that table gets no view of columns, and its ``json`` view stands. A query
that does not use it runs and is warned; one that does fails as a ``query`` error that says
how to read the table instead. A survey asks the same question of every table it wrote
(:meth:`DuckdbQuery.unreadable`), so the warning comes before the first query.

A failed query's fix is about what failed: a missing table names the tables there are; any
other SQL error quotes DuckDB and points at its SQL documentation, or at the usual shape of
the query when DuckDB's message names one (``unnest`` beside an aggregate).

The SQL is not trusted. Whoever writes it has usually just read the tables, and the tables
are collected text, so the connection is confined before any SQL runs: it reads the files of
the tables of the archives the query names (the archive, a stream's windows, the baseline)
and no other file, not even another of those archives' own (their meta, event log, told
index), spills nothing to disk, installs and loads no extension, and reaches no network.
DuckDB lets SQL write wherever it may read, so a query runs only SELECT statements (``WITH``,
``FROM``, ``DESCRIBE`` and ``SUMMARIZE`` are SELECTs to DuckDB): a ``COPY``, ``ATTACH``,
``CREATE`` or any other statement is refused before anything runs.
"""

from __future__ import annotations

import csv
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from recon.adapters._out.private_files import make_dirs
from recon.core.domain.archives import Rows, table_name
from recon.core.domain.errors import NotFoundError, Problem, QueryError, ReconError

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from recon.core.domain.archives import ArchiveRef
    from recon.core.domain.changes import ChangeOp

#: The schemas recon makes beside the tables' own views.
JSON, LINES, BASELINE = "json", "lines", "baseline"


class TableFile(Protocol):
    """One of an archive's tables, as the archive adapter describes it to this one."""

    @property
    def output(self) -> str: ...
    @property
    def file(self) -> Path: ...
    @property
    def empty(self) -> bool: ...
    @property
    def columns(self) -> tuple[str, ...]: ...
    @property
    def sealed(self) -> bool: ...


class TableFiles(Protocol):
    """What this adapter needs of the archive adapter it is built with: each archive's tables,
    each with its file (raising QueryError for a layout it does not read). File locations pass
    between the two adapters alone."""

    def tables(self, ref: ArchiveRef) -> Sequence[TableFile]: ...


def _confine(conn: Any, readable: Sequence[Path]) -> None:
    """Restrict a fresh connection to reading the files ``readable``, and no other. Order
    matters: ``allowed_paths`` can only be set while external access is still on, turning
    external access off cannot be undone, and the lock stops the extension settings from
    being switched back. Spilling to disk is off: DuckDB adds its temp directory (``.tmp/``
    under the working directory) to the directories SQL may read, so a query could read
    whatever is there; a query that would spill fails for memory instead. The time zone is
    UTC: DuckDB reads ``…Z`` as a timestamp with no zone, and a cast to TIMESTAMPTZ would
    otherwise take the machine's zone for it; a time with an explicit offset keeps it."""
    allowed = ", ".join(_literal(path) for path in dict.fromkeys(readable))
    conn.execute("SET TimeZone='UTC'")
    conn.execute("SET temp_directory=''")
    conn.execute(f"SET allowed_paths=[{allowed}]")
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
    """The :class:`~recon.core.domain.archives.ArchiveQuery` over the archive adapter ``files``."""

    def __init__(self, files: TableFiles) -> None:
        self._files = files

    def run(
        self, archives: Sequence[ArchiveRef], sql: str, *, baseline: ArchiveRef | None = None
    ) -> Rows:
        import duckdb  # only a query needs it

        last = archives[-1]
        tables = _grouped([_sealed(self._files.tables(a)) for a in archives])
        if not tables:
            raise NotFoundError(f"no tables in archive {last.id} of {last.mission}")
        before: dict[str, list[TableFile]] = {}
        if baseline is not None:
            before = _grouped([_sealed(self._files.tables(baseline))])
        readable = _files(tables) + _files(before)
        conn = duckdb.connect()
        try:
            _confine(conn, readable)
            unreadable = _views(conn, tables, numbered=len(archives) == 1)
            if baseline is not None:
                _schema_views(conn, BASELINE, before)
            names = list(tables)
            readable_names = [name for name in names if name not in unreadable]
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
                for name, why in unreadable.items():
                    if isinstance(exc, duckdb.CatalogException) and _names(sql, name):
                        raise QueryError(
                            f"table {name} cannot be queried: {why}",
                            fix=_unreadable_fix(name, why),
                            details={"table": name},
                        ) from exc
                raise QueryError(
                    f"SQL error: {exc}\nAvailable tables: {', '.join(readable_names)}",
                    fix=_fix(exc, readable_names, sql, baseline is not None),
                ) from exc
            warnings = [
                _unreadable(name, why, ", and this query did not use it")
                for name, why in unreadable.items()
            ]
            return Rows(columns=columns, rows=rows, warnings=warnings)
        finally:
            conn.close()

    def unreadable(self, archive: ArchiveRef) -> list[Problem]:
        import duckdb

        tables = _grouped([_sealed(self._files.tables(archive))])
        if not tables:
            return []
        conn = duckdb.connect()
        try:
            _confine(conn, _files(tables))
            return [_unreadable(name, why) for name, why in _views(conn, tables).items()]
        finally:
            conn.close()


def _sealed(tables: Sequence[TableFile]) -> list[TableFile]:
    """The tables SQL reads: a table not yet sealed holds provisional records."""
    return [table for table in tables if table.sealed]


def _files(tables: dict[str, list[TableFile]]) -> list[Path]:
    """The files the views of ``tables`` read: all SQL over them may read."""
    return [part.file for parts in tables.values() for part in parts if not part.empty]


def _grouped(archives: list[list[TableFile]]) -> dict[str, list[TableFile]]:
    """Each table by the name SQL knows it by, with its part in each archive read, in the
    archives' order: one part for a single archive, a window's each for a stream's."""
    grouped: dict[str, list[TableFile]] = {}
    for tables in archives:
        for table in tables:
            grouped.setdefault(table_name(table.output), []).append(table)
    return grouped


def _views(
    conn: Any, tables: dict[str, list[TableFile]], *, numbered: bool = False
) -> dict[str, str]:
    """Make each table's view of all its parts, its ``json`` view and, ``numbered``, its
    ``lines`` view; return the tables that got no view of columns, with why. Parts read
    together are joined by column name, so a column one part lacks is null in its rows."""
    import duckdb

    conn.execute(f"CREATE SCHEMA {JSON}")
    if numbered:
        conn.execute(f"CREATE SCHEMA {LINES}")
    unreadable: dict[str, str] = {}
    for name, parts in tables.items():
        files = [part.file for part in parts if not part.empty]
        conn.execute(f"CREATE VIEW {JSON}.{_identifier(name)} AS {_json_body(files)}")
        try:
            conn.execute(f"CREATE VIEW {_identifier(name)} AS {_body(parts)}")
        except duckdb.Error as exc:
            unreadable[name] = _first_line(exc)
            continue
        if numbered:
            conn.execute(f"CREATE VIEW {LINES}.{_identifier(name)} AS {_numbered_body(parts)}")
    return unreadable


def _schema_views(conn: Any, schema: str, tables: dict[str, list[TableFile]]) -> None:
    """Each table's view in ``schema``; one that cannot be made is left out, as a query that
    names it then says."""
    import duckdb

    conn.execute(f"CREATE SCHEMA {schema}")
    for name, parts in tables.items():
        try:
            conn.execute(f"CREATE VIEW {schema}.{_identifier(name)} AS {_body(parts)}")
        except duckdb.Error:
            continue


def _body(parts: list[TableFile]) -> str:
    """A table's records as columns, from all its parts."""
    files = [part.file for part in parts if not part.empty]
    if not files:
        return _no_rows(parts)
    if len(files) == 1:
        return f"SELECT * FROM read_json_auto({_literal(files[0])})"
    listed = ", ".join(_literal(file) for file in files)
    return f"SELECT * FROM read_json_auto([{listed}], union_by_name = true)"


def _numbered_body(parts: list[TableFile]) -> str:
    """One archive's table, each record with its ``line``."""
    files = [part.file for part in parts if not part.empty]
    if not files:
        return _no_rows(parts, "NULL::BIGINT AS line")
    return (
        "SELECT ordinality AS line, * EXCLUDE (ordinality) "
        f"FROM read_json_auto({_literal(files[0])}) WITH ORDINALITY"
    )


def _json_body(files: list[Path]) -> str:
    """A table's records, each as its JSON text."""
    if not files:
        return "SELECT NULL::JSON AS json WHERE false"
    listed = ", ".join(_literal(file) for file in files)
    return f"SELECT json FROM read_ndjson_objects([{listed}])"


def _no_rows(parts: list[TableFile], *first: str) -> str:
    """An empty table's view: the columns it keeps, null-typed, and no row."""
    columns = list(dict.fromkeys(column for part in parts for column in part.columns))
    kept = [*first, *(f"NULL AS {_identifier(column)}" for column in columns)]
    if "_empty" not in columns:
        kept.append("NULL::BOOLEAN AS _empty")
    return f"SELECT {', '.join(kept)} WHERE false"


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


def _unreadable_fix(name: str, why: str) -> str:
    raw = f"SELECT json FROM {JSON}.{name}"
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


def _unreadable(name: str, why: str, context: str = "") -> Problem:
    return Problem(
        "",
        f"table {name} cannot be queried{context}: {why}",
        kind="table_unreadable",
        fix=_unreadable_fix(name, why),
        got=name,
    )


#: DuckDB's SQL documentation, and its page on ``unnest``.
SQL_DOCS = "https://duckdb.org/docs/current/sql/introduction"
UNNEST_DOCS = "https://duckdb.org/docs/current/sql/query_syntax/unnest"

_MISSING_TABLE = re.compile(r"Table with name \S+ does not exist")
_BASELINE = re.compile(rf"(?<![\w\"]){BASELINE}\s*\.", re.IGNORECASE)
_REQUIRED_MODULE = re.compile(r"Required module '([\w.]+)' failed to import")


def _module_fix(module: str) -> str:
    return (
        f"DuckDB needs the Python module {module} to return this result (pytz for a TIMESTAMP "
        "WITH TIME ZONE): reinstall recon, which declares it, or cast the column to a type "
        "that needs no module, as <column>::TIMESTAMP"
    )


def _fix(exc: Exception, tables: list[str], sql: str, baseline: bool) -> str:
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
            "a query reads only the tables of the archives it names, by their names, loads "
            "no extension and makes no network request: query the tables by name (json.<table> "
            "for each record's JSON text); for what is new since an earlier archive, name it with "
            "--baseline <archive id> and read its tables as baseline.<table>"
        )
    if "Duplicate name" in str(exc):
        return _unreadable_fix("<table>", str(exc))
    if isinstance(exc, duckdb.CatalogException) and not baseline and _BASELINE.search(sql):
        return (
            "baseline.<table> is an earlier archive's table: name that archive with "
            "--baseline <archive id> (`recon status <mission>` lists the ids)"
        )
    if isinstance(exc, duckdb.CatalogException) and _MISSING_TABLE.search(message):
        return f"query one of these tables: {', '.join(tables) or '(none)'}"
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
# DuckdbJoin: the KeyJoin (``recon.observe.domain.joins``) of a large told index, on a DuckDB
# connection of its own that runs no SQL but this module's. The told index and the table's keys
# are written, as they are read, to two CSV files in a directory of the join's own, which DuckDB
# reads; every answer is read back a batch at a time. So the process holds a batch, and DuckDB
# at most ``memory_limit``, spilling the rest into the same directory, at most
# ``max_temp_directory_size`` of it; past that the join fails, classified (``store``). The
# directory is made under the ``scratch`` the join is built with (its mission's, 0700), named
# for the process, and ``close`` removes it; one a process that died left (its CSV copies of keys
# and hashes, its spill) is removed when the next join starts there (Erlang, round-4 review F7).
# The connection is confined before it reads anything, as the query's is: it reads and spills
# only under its directory, loads no extension and reaches no network.
# A key goes to DuckDB as the hex of its UTF-8 bytes (a lone surrogate passed through as its
# code point's bytes, which DuckDB's text would refuse) and comes back decoded: hex orders as
# its bytes do, and UTF-8's bytes as the code points the in-memory join sorts by.

#: By default, the most memory the join's DuckDB holds before it spills to disk.
MEMORY_LIMIT = "96MB"

#: By default, the most the join's DuckDB spills to disk; past it the join fails.
MAX_TEMP_DIRECTORY_SIZE = "4GiB"

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
    """Joins on DuckDB within ``memory_limit``, ``threads`` and ``max_temp_directory_size``
    (DuckDB's own names for them), each join's files in a directory of its own under
    ``scratch`` (a mission's, private; None: the system's temporary directory)."""

    def __init__(
        self,
        *,
        scratch: Path | None = None,
        memory_limit: str = MEMORY_LIMIT,
        threads: int = 1,
        max_temp_directory_size: str = MAX_TEMP_DIRECTORY_SIZE,
    ) -> None:
        self._scratch = scratch
        self._memory_limit = memory_limit
        self._threads = threads
        self._max_temp = max_temp_directory_size

    def join(
        self, told: Iterable[tuple[str, str]], new: Iterable[tuple[int, str, str]]
    ) -> _DuckdbJoined:
        return _DuckdbJoined(told, new, self._memory_limit, self._threads, self._max_temp,
                             self._scratch)  # fmt: skip


class _DuckdbJoined:
    def __init__(
        self,
        told: Iterable[tuple[str, str]],
        new: Iterable[tuple[int, str, str]],
        memory_limit: str,
        threads: int,
        max_temp: str,
        scratch: Path | None,
    ) -> None:
        import duckdb  # only a large comparison needs it

        self._failed = duckdb.Error
        self._max_temp = max_temp
        if scratch is not None:
            _sweep(scratch)
        self._directory = tempfile.TemporaryDirectory(
            prefix=f"{os.getpid()}-" if scratch is not None else "recon-join-",
            dir=scratch,
        )
        home = Path(self._directory.name)
        try:
            _write_csv(home / "told.csv", ((_hex(k), *_halves(h)) for k, h in told))
            _write_csv(home / "new.csv", ((i, _hex(k), *_halves(h)) for i, k, h in new))
            # A database file of the join's own, so its tables page to disk as its memory fills.
            self._conn: Any = duckdb.connect(str(home / "join.duckdb"))
            _confine_join(self._conn, home, memory_limit, threads, max_temp)
            told_csv = _read_csv(home / "told.csv", "k", "a", "b")
            new_csv = _read_csv(home / "new.csv", "i", "k", "a", "b")
            self._conn.execute(f"CREATE TABLE told AS SELECT * FROM {told_csv}")
            self._conn.execute(f"CREATE TABLE new_keys AS SELECT * FROM {new_csv}")
            self._conn.execute(_KEYS)
            self.ambiguous: int = self._count("SELECT count(*) FROM nk WHERE amb")
        except duckdb.Error as exc:
            self.close()
            raise self._store_failure(exc) from exc
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
        try:
            cursor = self._conn.execute(sql)
            while rows := cursor.fetchmany(_BATCH):
                yield from (tuple(row) for row in rows)
        except self._failed as exc:
            raise self._store_failure(exc) from exc

    def _store_failure(self, exc: Exception) -> ReconError:
        """DuckDB's failure as the join's: past its memory and its spill's cap, or a disk
        that is full. The table is kept; its changes are told next time."""
        return ReconError(
            f"the join of a large told index failed: {exc}",
            kind="store",
            fix=f"give the join more room, a DuckdbJoin(memory_limit=..., max_temp_directory_size"
            f"=...) from root(key_join=), which builds each mission's join (its spill is capped "
            f"at {self._max_temp}), or free space under the mission; the next survey compares "
            "the table again",
        )


def _sweep(scratch: Path) -> None:
    """Make ``scratch`` (its owner's alone), and remove each join's directory in it whose
    process is gone: a join that died (killed, its host down) left its files there."""
    make_dirs(scratch)
    for leftover in scratch.iterdir():
        pid, dash, _ = leftover.name.partition("-")
        if dash and pid.isdigit() and not _alive(int(pid)):
            shutil.rmtree(leftover, ignore_errors=True)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # another user's: alive, and not ours to sweep
    return True


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


def _confine_join(conn: Any, home: Path, memory_limit: str, threads: int, max_temp: str) -> None:
    """Hold a fresh connection to reading and spilling under ``home``, within its memory,
    threads and spill, before it reads anything; no extension, no network, and its settings
    locked."""
    root = str(home).replace("'", "''")
    conn.execute(f"SET memory_limit='{memory_limit}'")
    conn.execute(f"SET max_temp_directory_size='{max_temp.replace(chr(39), '')}'")
    conn.execute(f"SET threads={int(threads)}")
    conn.execute("SET preserve_insertion_order=false")
    conn.execute(f"SET temp_directory='{root}/spill'")
    conn.execute(f"SET allowed_directories=['{root}']")
    conn.execute("SET autoinstall_known_extensions=false")
    conn.execute("SET autoload_known_extensions=false")
    conn.execute("SET enable_external_access=false")
    conn.execute("SET lock_configuration=true")

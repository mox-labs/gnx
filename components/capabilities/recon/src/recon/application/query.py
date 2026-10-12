"""Query an archive directory of JSONL files via DuckDB.

Pure application-layer logic: given an archive path and a SQL string, returns
columns and rows. No I/O beyond DuckDB reading JSONL. CLI formats the output.

Keeping this out of the CLI adapter means a future non-CLI frontend (MCP
server, Python API, HTTP endpoint) can reuse the same query path.

Every output table is a view named after its file (non-identifier characters become
``_``). The capture log is not a table. A table that collected nothing is a view with no
rows: the one sentinel line in its file is filtered out.

The SQL is not trusted. Whoever writes it has usually just read the tables, and the tables
are collected text, so the connection is confined before any SQL runs: it reads files only
under the mission's ``archive/`` directory (every archive of the mission, which keeps
cross-archive diffs working), installs and loads no extension, and reaches no network.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from recon.application.archives import is_empty_table, table_files
from recon.domain.exceptions import NotFoundError, QueryError

if TYPE_CHECKING:
    from pathlib import Path

_TABLE_NAME_SAFE = re.compile(r"[^a-zA-Z0-9_]")


def table_name(stem: str) -> str:
    return _TABLE_NAME_SAFE.sub("_", stem)


def available_tables(archive_dir: Path) -> list[str]:
    """List DuckDB-style table names derivable from the archive's JSONL files."""
    return [table_name(jf.stem) for jf in table_files(archive_dir)]


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


def execute(archive_dir: Path, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Execute SQL against every JSONL table in the archive as DuckDB views.

    ``archive_dir`` is one archive, ``<mission>/archive/<timestamp>``; its parent is the
    only directory the SQL can read.

    Returns (columns, rows). Raises NotFoundError when the archive has no tables and
    QueryError when the SQL fails or reaches for something outside that directory. Does
    not print; caller decides how to present.
    """
    import duckdb  # imported lazily — CLI-only dep used only when querying

    archive_dir = archive_dir.absolute()
    jsonl_files = table_files(archive_dir)
    if not jsonl_files:
        msg = f"No JSONL tables in archive: {archive_dir}"
        raise NotFoundError(msg)

    conn = duckdb.connect()
    try:
        _confine(conn, archive_dir.parent)
        for jf in jsonl_files:
            jf_str = str(jf).replace("'", "''")
            where = " WHERE NOT _empty" if is_empty_table(jf) else ""
            conn.execute(
                f'CREATE VIEW "{table_name(jf.stem)}" AS '
                f"SELECT * FROM read_json_auto('{jf_str}'){where}"
            )

        try:
            result = conn.execute(sql)
        except duckdb.Error as exc:
            tables = ", ".join(available_tables(archive_dir))
            msg = f"SQL error: {exc}\nAvailable tables: {tables}"
            fix = None
            if isinstance(exc, duckdb.PermissionException):
                fix = (
                    f"a query reads only files under {archive_dir.parent}, loads no "
                    "extension and makes no network request: query this archive's tables, "
                    f"or another archive as read_json_auto('{archive_dir.parent}"
                    "/<timestamp>/<table>.jsonl')"
                )
            raise QueryError(msg, fix=fix) from exc

        columns = [desc[0] for desc in result.description] if result.description else []
        rows = result.fetchall() if result.description else []
        return columns, rows
    finally:
        conn.close()

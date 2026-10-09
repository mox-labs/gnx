"""Query an archive directory of JSONL files via DuckDB.

Pure application-layer logic: given an archive path and a SQL string, returns
columns and rows. No I/O beyond DuckDB reading JSONL. CLI formats the output.

Keeping this out of the CLI adapter means a future non-CLI frontend (MCP
server, Python API, HTTP endpoint) can reuse the same query path.

Every output table is a view named after its file (non-identifier characters become
``_``). The capture log is not a table.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from recon.application.archives import table_files
from recon.domain.exceptions import NotFoundError, QueryError

if TYPE_CHECKING:
    from pathlib import Path

_TABLE_NAME_SAFE = re.compile(r"[^a-zA-Z0-9_]")


def table_name(stem: str) -> str:
    return _TABLE_NAME_SAFE.sub("_", stem)


def available_tables(archive_dir: Path) -> list[str]:
    """List DuckDB-style table names derivable from the archive's JSONL files."""
    return [table_name(jf.stem) for jf in table_files(archive_dir)]


def execute(archive_dir: Path, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Execute SQL against every JSONL table in the archive as DuckDB views.

    Returns (columns, rows). Raises NotFoundError when the archive has no tables and
    QueryError when the SQL fails. Does not print; caller decides how to present.
    """
    import duckdb  # imported lazily — CLI-only dep used only when querying

    jsonl_files = table_files(archive_dir)
    if not jsonl_files:
        msg = f"No JSONL tables in archive: {archive_dir}"
        raise NotFoundError(msg)

    conn = duckdb.connect()
    try:
        for jf in jsonl_files:
            jf_str = str(jf).replace("'", "''")
            conn.execute(
                f"CREATE VIEW \"{table_name(jf.stem)}\" AS SELECT * FROM read_json_auto('{jf_str}')"
            )

        try:
            result = conn.execute(sql)
        except duckdb.Error as exc:
            tables = ", ".join(available_tables(archive_dir))
            msg = f"SQL error: {exc}\nAvailable tables: {tables}"
            raise QueryError(msg) from exc

        columns = [desc[0] for desc in result.description] if result.description else []
        rows = result.fetchall() if result.description else []
        return columns, rows
    finally:
        conn.close()

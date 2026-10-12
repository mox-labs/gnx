"""Reading missions and archives back — both meta.yaml formats.

``format_version: 1`` (recon ≤ 0.8) listed ``collectors`` with an ``error`` string;
``format_version: 2`` lists ``tables`` with ``type_url`` and ``error: {kind, message}``.
Readers get the version-2 shape either way, so ``status`` and ``query`` work on old
archives without knowing which they are reading.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import yaml

from recon.domain.capture import CAPTURE_LOG_NAME
from recon.domain.exceptions import NotFoundError
from recon.domain.type_url import DEFAULT_RECORDS_TYPE_URL

if TYPE_CHECKING:
    from pathlib import Path

INCOMPLETE_MARKER = ".incomplete"

#: The only line of a table that collected nothing. It keeps the file readable by DuckDB;
#: it is not a record, so it is neither counted in meta.yaml nor a row in the table's view.
EMPTY_SENTINEL: dict[str, Any] = {"_empty": True}


def list_archives(mission_dir: Path) -> list[Path]:
    """Archive directories, oldest first (timestamps sort lexically)."""
    archive = mission_dir / "archive"
    if not archive.is_dir():
        return []
    return sorted(d for d in archive.iterdir() if d.is_dir())


def archive_state(archive_dir: Path) -> str:
    return "incomplete" if (archive_dir / INCOMPLETE_MARKER).exists() else "ok"


def resolve_archive(mission_dir: Path, mission: str, run_id: str | None) -> Path:
    """The named archive, or the latest. NotFoundError says what exists instead."""
    runs = list_archives(mission_dir)
    if not runs:
        raise NotFoundError(f"no archives for mission {mission!r}")
    if run_id is None:
        return runs[-1]
    for run in runs:
        if run.name == run_id:
            return run
    available = ", ".join(r.name for r in runs)
    raise NotFoundError(f"archive {run_id!r} not found for {mission!r}; have: {available}")


def table_files(archive_dir: Path) -> list[Path]:
    """The archive's output tables: every JSONL file except the capture log."""
    return [p for p in sorted(archive_dir.glob("*.jsonl")) if p.name != CAPTURE_LOG_NAME]


def is_empty_table(path: Path) -> bool:
    """True when the table file holds the empty sentinel and nothing else."""
    with open(path, encoding="utf-8") as f:
        first, second = f.readline(), f.readline()
    if second.strip():
        return False
    try:
        return bool(json.loads(first) == EMPTY_SENTINEL)
    except json.JSONDecodeError:
        return False


def read_meta(archive_dir: Path) -> dict[str, Any]:
    """meta.yaml in the version-2 shape; ``format_version`` is None when it is missing."""
    path = archive_dir / "meta.yaml"
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        raw = None
    if not isinstance(raw, dict):
        return {"format_version": None, "tables": []}
    version = raw.get("format_version", 1)
    if version == 1:
        return {
            "format_version": 1,
            "recon_version": None,
            "config_sha256": None,
            "timestamp": raw.get("timestamp"),
            "tables": [_v1_table(c) for c in raw.get("collectors") or [] if isinstance(c, dict)],
        }
    return {**raw, "tables": [t for t in raw.get("tables") or [] if isinstance(t, dict)]}


def _v1_table(entry: dict[str, Any]) -> dict[str, Any]:
    table: dict[str, Any] = {
        "name": entry.get("name"),
        "file": entry.get("file", f"{entry.get('name')}.jsonl"),
        "type_url": DEFAULT_RECORDS_TYPE_URL,
        "status": entry.get("status"),
        "records": entry.get("records", 0),
        "seconds": entry.get("seconds"),
    }
    if entry.get("status") == "error":
        table["error"] = {"kind": "unknown", "message": str(entry.get("error", ""))}
    return table

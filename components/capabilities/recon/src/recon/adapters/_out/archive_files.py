"""Archives as directories of JSONL files.

::

    <mission>/archive/<archive id>/
      <output>.jsonl          one table per collector and source, one record per line
      meta.yaml               recon version, config sha256, times, and per table: type_url,
                              records, status, seconds, error {kind, message[, fix]}
      telemetry.jsonl         the survey's recon.v1.event lines, as they happened
      .incomplete             written first; removed once meta.yaml is written and every
                              table succeeded
      captures.jsonl, raw/    the capture log, when the mission preserves raw

A table that collected nothing holds one sentinel line, ``{"_empty": true}``, so DuckDB can
still read the file; it is not a record and is not counted. When the table's columns are
known (its collector's ``normalize:`` spec), the line keeps them as ``_columns``, so the
table's view still has them. ``meta.yaml`` is read back in the current shape whether it was
written as ``format_version: 1`` (``collectors`` with an ``error`` string) or 2 (``tables``
with ``error: {kind, message}``).
"""

from __future__ import annotations

import contextlib
import json
import os
from datetime import datetime
from typing import TYPE_CHECKING, Any

import yaml

from recon.adapters._out.capture_files import FilesystemCaptureLog
from recon.adapters._out.event_log import EventLog
from recon.domain.archives import ARCHIVE_ID_FORMAT, EVENT_LOG_NAME
from recon.domain.capture import CAPTURE_LOG_NAME
from recon.domain.type_url import DEFAULT_RECORDS_TYPE_URL

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence
    from pathlib import Path

    from recon.domain.clock import Clock

INCOMPLETE_MARKER = ".incomplete"

#: The only line of a table that collected nothing.
EMPTY_SENTINEL: dict[str, Any] = {"_empty": True}

#: Records written between two fsyncs: the most a crash can lose from a table being written.
_FSYNC_EVERY = 1000


class ArchiveFiles:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def create(self, mission_dir: Path, archive_id: str) -> Path:
        archive = mission_dir / "archive" / archive_id
        archive.mkdir(parents=True, exist_ok=True)
        (archive / INCOMPLETE_MARKER).write_text("")
        return archive

    def write_table(
        self,
        archive: Path,
        output: str,
        records: Generator[dict[str, Any], None, None],
        *,
        columns: Sequence[str] = (),
    ) -> int:
        path = archive / f"{output}.jsonl"
        tmp = path.with_suffix(".jsonl.tmp")
        count = 0
        try:
            # closing() is what runs the collector's own cleanup (a subprocess killed, a
            # client closed) when writing stops early.
            with contextlib.closing(records), open(tmp, "w") as f:
                for record in records:
                    f.write(json.dumps(record, default=str) + "\n")
                    count += 1
                    if count % _FSYNC_EVERY == 0:
                        f.flush()
                        os.fsync(f.fileno())
                if count == 0:
                    empty = (
                        {**EMPTY_SENTINEL, "_columns": list(columns)} if columns else EMPTY_SENTINEL
                    )
                    f.write(json.dumps(empty) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise
        return count

    def capture_log(self, archive: Path) -> FilesystemCaptureLog:
        return FilesystemCaptureLog(archive, self._clock)

    def event_log(self, archive: Path) -> EventLog:
        return EventLog(archive / EVENT_LOG_NAME)

    def mark_complete(self, archive: Path) -> None:
        (archive / INCOMPLETE_MARKER).unlink(missing_ok=True)

    def write_meta(self, archive: Path, meta: dict[str, Any]) -> None:
        path = archive / "meta.yaml"
        tmp = path.with_suffix(".yaml.tmp")
        try:
            tmp.write_text(yaml.safe_dump(meta, default_flow_style=False, sort_keys=False))
            tmp.replace(path)
        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise

    def archives(self, mission_dir: Path) -> list[Path]:
        archive = mission_dir / "archive"
        if not archive.is_dir():
            return []
        return sorted(d for d in archive.iterdir() if d.is_dir() and _is_archive_id(d.name))

    def is_incomplete(self, archive: Path) -> bool:
        return (archive / INCOMPLETE_MARKER).exists()

    def read_meta(self, archive: Path) -> dict[str, Any]:
        try:
            raw = yaml.safe_load((archive / "meta.yaml").read_text())
        except (OSError, yaml.YAMLError):
            raw = None
        if not isinstance(raw, dict):
            return {"format_version": None, "tables": []}
        if raw.get("format_version", 1) == 1:
            return {
                "format_version": 1,
                "recon_version": None,
                "config_sha256": None,
                "timestamp": raw.get("timestamp"),
                "tables": [
                    _v1_table(c) for c in raw.get("collectors") or [] if isinstance(c, dict)
                ],
            }
        return {**raw, "tables": [t for t in raw.get("tables") or [] if isinstance(t, dict)]}


def _is_archive_id(name: str) -> bool:
    """Whether ``name`` is one a survey gives its archive: a stray ``notes/`` is not."""
    try:
        datetime.strptime(name, ARCHIVE_ID_FORMAT)
    except ValueError:
        return False
    return True


def table_files(archive: Path) -> list[Path]:
    """The archive's tables: every JSONL file but the capture log and the event log."""
    logs = {CAPTURE_LOG_NAME, EVENT_LOG_NAME}
    return [p for p in sorted(archive.glob("*.jsonl")) if p.name not in logs]


def empty_table_columns(path: Path) -> list[str] | None:
    """The columns an empty table keeps (none when they were not known); None when the file
    holds anything but the empty sentinel."""
    with open(path, encoding="utf-8") as f:
        first, second = f.readline(), f.readline()
    if second.strip():
        return None
    try:
        line = json.loads(first)
    except json.JSONDecodeError:
        return None
    if not isinstance(line, dict) or line.get("_empty") is not True:
        return None
    columns = line.get("_columns", [])
    if set(line) - {"_empty", "_columns"} or not isinstance(columns, list):
        return None
    return [str(column) for column in columns]


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

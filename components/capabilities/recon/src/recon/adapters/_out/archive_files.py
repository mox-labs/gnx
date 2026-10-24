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

A table is written to ``<output>.jsonl.tmp`` and renamed to ``<output>.jsonl`` when it
ends; a table that fails is retracted, its ``.tmp`` removed. While it is written, its lines
reach the ``.tmp`` whole, at most 64 KiB or 250 ms after they are given (:class:`_Lines`),
and every 1000 records are flushed to the disk.

A table that collected nothing holds one sentinel line, ``{"_empty": true}``, so a reader of
the file sees it was written; it is not a record and is not counted. When the table's
columns are known (its collector's ``normalize:`` spec), the line keeps them as
``_columns``. :meth:`ArchiveFiles.tables` describes each table, the sentinel read, so no
other adapter knows any of this. This is table layout :data:`TABLE_LAYOUT`, which
``meta.yaml`` names as ``table_layout`` (an archive whose ``meta.yaml`` does not name one is
in layout 1); a layout this recon does not know is refused rather than misread.
``meta.yaml`` is read back in the current shape whether it was written as ``format_version:
1`` (``collectors`` with an ``error`` string) or 2 (``tables`` with ``error: {kind,
message}``).
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any

import yaml

from recon.adapters._out.capture_files import FilesystemCaptureLog
from recon.adapters._out.event_log import EventLog
from recon.domain.archives import ARCHIVE_ID_FORMAT, EVENT_LOG_NAME, StoredTable
from recon.domain.capture import CAPTURE_LOG_NAME
from recon.domain.errors import QueryError
from recon.domain.type_url import DEFAULT_RECORDS_TYPE_URL

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence
    from pathlib import Path

    from recon.domain.clock import Clock

INCOMPLETE_MARKER = ".incomplete"

#: The layout of an archive's tables this store writes and reads, named in ``meta.yaml``.
TABLE_LAYOUT = 1

#: The only line of a table that collected nothing.
EMPTY_SENTINEL: dict[str, Any] = {"_empty": True}

#: Records written between two fsyncs: the most a crash can lose from a table being written.
_FSYNC_EVERY = 1000

#: A table's lines wait in memory until this many bytes wait, or :data:`_WRITE_SECONDS` have
#: passed, then are written together (ruling A2's fallback, measured: see :class:`_Lines`).
_WRITE_BYTES = 64 * 1024
_WRITE_SECONDS = 0.25


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
            with contextlib.closing(records), _Lines(tmp) as f:
                for record in records:
                    f.write(json.dumps(record, default=str) + "\n")
                    count += 1
                    if count % _FSYNC_EVERY == 0:
                        f.sync()
                if count == 0:
                    empty = (
                        {**EMPTY_SENTINEL, "_columns": list(columns)} if columns else EMPTY_SENTINEL
                    )
                    f.write(json.dumps(empty) + "\n")
                f.sync()
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
        stamped = {**meta, "table_layout": TABLE_LAYOUT}
        try:
            tmp.write_text(yaml.safe_dump(stamped, default_flow_style=False, sort_keys=False))
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

    def tables(self, archive: Path) -> list[StoredTable]:
        layout = self._table_layout(archive)
        if layout != TABLE_LAYOUT:
            raise QueryError(
                f"archive {archive.name} holds its tables in table layout {layout}, which this "
                f"recon does not read (it reads layout {TABLE_LAYOUT})",
                fix="upgrade recon to the version that surveyed this archive, or later",
                details={"archive": str(archive), "table_layout": layout},
            )
        tables = []
        for file in _table_files(archive):
            columns = _empty_table_columns(file)
            empty = columns is not None
            tables.append(StoredTable(file.stem, file, empty, tuple(columns or ())))
        return tables

    def _table_layout(self, archive: Path) -> Any:
        """The layout ``meta.yaml`` names; 1, the only one there was before it was named,
        when it names none or there is no readable ``meta.yaml`` yet (a survey running, or
        killed)."""
        try:
            raw = yaml.safe_load((archive / "meta.yaml").read_text())
        except (OSError, yaml.YAMLError):
            return TABLE_LAYOUT
        return raw.get("table_layout", 1) if isinstance(raw, dict) else TABLE_LAYOUT


class _Lines:
    """A file a table is written to, in whole lines: what is given waits in memory until 64
    KiB of it is waiting or 250 ms have passed, then goes to the file in one ``write`` of
    whole lines, so a reader of the file sees every line written by then, each whole, and no
    part of the next, at most 250 ms late however slowly the lines come. A write the disk
    cuts short is taken back before the error is raised. What reaches the disk itself is
    left to :meth:`sync`.

    Measured by ``scripts/bench_table_write.py`` (a 200,000-line ``cli`` survey, against the
    buffered writer before it): one ``write`` per line cost 84-89%, past ruling A2's 5%
    bound, so lines are batched as the ruling says to then, which cost 3.6-3.9%. A thread
    writes what waits on time, so a collector that is slow to give its next record does not
    hold back the last one."""

    def __init__(self, path: Path) -> None:
        self._descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        self._size = 0
        self._waiting: list[bytes] = []
        self._waiting_bytes = 0
        self._lock = threading.Lock()
        self._failed: OSError | None = None
        self._stop = threading.Event()
        self._timer = threading.Thread(target=self._write_on_time, name="recon-table-writer")
        self._timer.daemon = True
        self._timer.start()

    def __enter__(self) -> _Lines:
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._timer.join()
        os.close(self._descriptor)

    def write(self, line: str) -> None:
        data = line.encode()
        with self._lock:
            if self._failed is not None:
                raise self._failed
            self._waiting.append(data)
            self._waiting_bytes += len(data)
            if self._waiting_bytes >= _WRITE_BYTES:
                self._write_waiting()

    def sync(self) -> None:
        """Write what waits, then flush the file to the disk."""
        with self._lock:
            if self._failed is not None:
                raise self._failed
            self._write_waiting()
            os.fsync(self._descriptor)

    def _write_waiting(self) -> None:
        """Write every waiting line in one ``write``. The lock is held."""
        if not self._waiting:
            return
        data = b"".join(self._waiting)
        self._waiting.clear()
        self._waiting_bytes = 0
        written = os.write(self._descriptor, data)
        if written != len(data):
            with contextlib.suppress(OSError):  # the error below is the one to report
                os.ftruncate(self._descriptor, self._size)
            raise OSError(f"wrote {written} of {len(data)} bytes of lines to the table")
        self._size += written

    def _write_on_time(self) -> None:
        while not self._stop.wait(_WRITE_SECONDS):
            with self._lock:
                try:
                    self._write_waiting()
                except OSError as exc:  # raised to the writer at its next line
                    self._failed = exc
                    return


def _is_archive_id(name: str) -> bool:
    """Whether ``name`` is one a survey gives its archive: a stray ``notes/`` is not."""
    try:
        datetime.strptime(name, ARCHIVE_ID_FORMAT)
    except ValueError:
        return False
    return True


def _table_files(archive: Path) -> list[Path]:
    """The archive's tables: every JSONL file but the capture log and the event log."""
    logs = {CAPTURE_LOG_NAME, EVENT_LOG_NAME}
    return [p for p in sorted(archive.glob("*.jsonl")) if p.name not in logs]


def _empty_table_columns(path: Path) -> list[str] | None:
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

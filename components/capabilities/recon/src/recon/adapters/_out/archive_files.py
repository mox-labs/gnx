"""Archives as directories of JSONL files.

::

    <mission>/archive/<archive id>/
      <output>.jsonl          one table per collector and source, one record per line
      meta.yaml               recon version, config sha256, times, and per table: type_url,
                              records, status, seconds, error {kind, message[, fix]}
      telemetry.jsonl         the survey's recon.v1.event lines, as they happened
      .incomplete             written first; removed once meta.yaml is written and every
                              table succeeded
      writer.lock             locked by the writing process from create to release, then
                              removed; left unlocked by a writer that died
      captures.jsonl, raw/    the capture log, when the mission preserves raw
    <mission>/streams/<stream>/
      writer.lock             locked by the stream's one writer while it runs, holding its
                              pid (so a writer refused is told whose); kept, emptied
      <window id>/            each window, an archive as above, of one table
      retired.jsonl           a line for each window retain: removed: what, and why

**Writing, complete, incomplete.** The writer holds an ``flock`` on ``writer.lock`` from
:meth:`ArchiveFiles.create` until :meth:`ArchiveFiles.release` or its death, as matrix's
JSONL store does, so a pid reused by another process cannot make a dead survey read as one
running. An archive is made whole, its lock held and its marker written, under a name no
reader lists (``.<archive id>.<pid>.making``), then renamed into place, so no archive is
ever seen without them; a writer that dies while making one leaves only that hidden
directory. :meth:`ArchiveFiles.state` reads ``writing`` while the lock is held; else
``incomplete`` while the marker is there; else ``complete`` once ``meta.yaml`` is; else
``incomplete``. Whether a writer is alive is known on its own host only.

**Streams.** A stream's windows are archives in the stream's own directory, so listing one
stream reads that directory alone. A window's id starts with its index
(:func:`~recon.domain.stream.window_id`), and one writer at a time holds the stream (an
``flock`` on its ``writer.lock``, which stays when it is let go, so no two writers ever hold
two files of one name), so the index it gives each new window is one no other writer gives.
The stream is ``writing`` while that lock is held, and otherwise reads as its last window.

**Reading as it is written.** :meth:`ArchiveFiles.read_since` reads each table's file from a
byte offset to its last whole line, the live ``.tmp`` of a table being written included, and
hands back a cursor: the offset reached in each table, and which tables it has told sealed,
as base64url JSON naming the archive. Only this store reads it; the ``.tmp`` path never
leaves it. A table this cursor read from whose file is gone, with no sealed file in its
place, failed and was retracted.

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

import base64
import binascii
import contextlib
import fcntl
import json
import os
import shutil
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any, BinaryIO

import yaml

from recon.adapters._out.capture_files import FilesystemCaptureLog
from recon.adapters._out.event_log import EventLog
from recon.domain.archives import (
    ARCHIVE_ID_FORMAT,
    EVENT_LOG_NAME,
    ArchiveState,
    NewRecords,
    StoredTable,
    TableEnds,
    TableRecord,
)
from recon.domain.capture import CAPTURE_LOG_NAME
from recon.domain.errors import QueryError, ReconError, UsageError
from recon.domain.stream import window_index
from recon.domain.type_url import DEFAULT_RECORDS_TYPE_URL

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence
    from pathlib import Path

    from recon.domain.clock import Clock

INCOMPLETE_MARKER = ".incomplete"

#: Locked by the writing process while it writes the archive, or extends the stream.
WRITER_LOCK = "writer.lock"

#: The directory under a mission that holds its streams, each a directory of its windows.
STREAMS = "streams"

#: Where a table's torn last line is moved: a write a dying writer cut short.
TORN = ".torn"

#: A stream's tombstones, one line a retired window, appended beside the windows; and the
#: suffix of a window being removed, out of every listing.
RETIRED = "retired.jsonl"
RETIRING = ".retiring"

#: The layout of an archive's tables this store writes and reads, named in ``meta.yaml``.
TABLE_LAYOUT = 1

#: The only line of a table that collected nothing.
EMPTY_SENTINEL: dict[str, Any] = {"_empty": True}

#: Records written between two fsyncs: the most a crash can lose from a table being written.
_FSYNC_EVERY = 1000

#: By default, a table's lines wait in memory until this many bytes wait, or
#: :data:`FLUSH_S` seconds have passed, then are written together (ruling A2's fallback,
#: measured: see :class:`_Lines`). ``ArchiveFiles(flush_bytes=, flush_s=)`` sets others.
FLUSH_BYTES = 64 * 1024
FLUSH_S = 0.25


class ArchiveFiles:
    def __init__(
        self, clock: Clock, *, flush_bytes: int = FLUSH_BYTES, flush_s: float = FLUSH_S
    ) -> None:
        """``flush_bytes`` and ``flush_s`` bound how long a table's records wait before a
        reader of the table can see them: in bytes waiting, and in seconds."""
        if flush_bytes < 1 or flush_s <= 0:
            raise ValueError(f"flush_bytes must be >= 1 and flush_s > 0, got {flush_bytes}, "
                             f"{flush_s}")  # fmt: skip
        self._clock = clock
        self._flush_bytes = flush_bytes
        self._flush_s = flush_s
        #: The lock held on each archive this store is writing, by archive.
        self._held: dict[Path, int] = {}
        #: The lock held on each stream this store is extending, by its directory.
        self._streams: dict[Path, int] = {}

    def create(self, mission_dir: Path, archive_id: str, *, stream: str | None = None) -> Path:
        home = mission_dir / "archive" if stream is None else _stream_dir(mission_dir, stream)
        archive = home / archive_id
        archive.parent.mkdir(parents=True, exist_ok=True)
        # Made whole under a name no reader lists, then renamed into place: an archive is
        # never seen without its lock and marker, whenever its writer dies.
        making = archive.with_name(f".{archive_id}.{os.getpid()}.making")
        making.mkdir()
        descriptor = _hold(making / WRITER_LOCK)
        try:
            (making / INCOMPLETE_MARKER).write_text("")
            taken = ReconError(
                f"archive {archive_id} already exists in {archive.parent}; an archive is "
                "never written twice",
                kind="store",
                fix="survey again: a new survey's archive is named for the moment it starts",
                details={"archive": str(archive)},
            )
            if archive.exists():
                raise taken
            try:
                making.rename(archive)
            except OSError as exc:  # another survey took the id since it was checked
                raise taken from exc
        except BaseException:
            os.close(descriptor)
            for leftover in (making / WRITER_LOCK, making / INCOMPLETE_MARKER):
                with contextlib.suppress(OSError):
                    leftover.unlink()
            with contextlib.suppress(OSError):
                making.rmdir()
            raise
        self._held[archive] = descriptor
        return archive

    def release(self, archive: Path) -> None:
        descriptor = self._held.pop(archive, None)
        if descriptor is None:
            return
        # Removed while still locked, so no reader finds it unlocked beside a finished archive.
        with contextlib.suppress(OSError):
            (archive / WRITER_LOCK).unlink()
        os.close(descriptor)  # closing releases the lock

    def hold_stream(self, mission_dir: Path, stream: str) -> None:
        directory = _stream_dir(mission_dir, stream)
        directory.mkdir(parents=True, exist_ok=True)
        if directory in self._streams:
            return
        try:
            descriptor = _hold(directory / WRITER_LOCK)
        except OSError as exc:
            pid = _holder(directory / WRITER_LOCK)
            if pid == os.getpid():
                stop = "this process already writes it: stream it once the run there returns"
            elif pid is not None:
                stop = (f"let it run, or stop it: kill -TERM {pid} (its pid) seals the window "
                        "it is writing; then stream again")  # fmt: skip
            else:
                stop = (f"let it run, or stop it (`recon status {mission_dir.name}` shows the "
                        "stream writing); then stream again")  # fmt: skip
            raise ReconError(
                f"another recon is writing stream {stream} of {mission_dir.name}; a stream has "
                "one writer at a time",
                kind="busy",
                fix=stop,
                details={"stream": stream} | ({} if pid is None else {"pid": pid}),
            ) from exc
        # The holder's pid, so a writer refused can name it; cleared before it lets go.
        os.ftruncate(descriptor, 0)
        os.pwrite(descriptor, f"{os.getpid()}\n".encode(), 0)
        self._streams[directory] = descriptor

    def release_stream(self, mission_dir: Path, stream: str) -> None:
        descriptor = self._streams.pop(_stream_dir(mission_dir, stream), None)
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.ftruncate(descriptor, 0)  # no pid of a writer that has let go
            os.close(descriptor)  # closing releases the lock; the file stays

    def stream_state(self, mission_dir: Path, stream: str) -> ArchiveState | None:
        if _locked(_stream_dir(mission_dir, stream) / WRITER_LOCK):
            return "writing"
        windows = self.archives(mission_dir, stream=stream)
        return self.state(windows[-1]) if windows else None

    def streams(self, mission_dir: Path) -> list[str]:
        directory = mission_dir / STREAMS
        if not directory.is_dir():
            return []
        return sorted(d.name for d in directory.iterdir() if d.is_dir())

    def table_ends(self, archive: Path, output: str) -> TableEnds:
        sealed = archive / f"{output}.jsonl"
        path = sealed if sealed.exists() else archive / f"{output}{_LIVE}"
        if not path.exists():
            return TableEnds(0)
        count = 0
        first: bytes | None = None
        last: bytes | None = None
        with open(path, "rb") as f:
            for line in f:
                if not line.endswith(b"\n"):
                    break  # a line not yet whole is no record
                if count == 0 and _is_empty_sentinel(line):
                    return TableEnds(0)
                first = first or line
                last = line
                count += 1
        return TableEnds(count, _object(first), _object(last))

    def size(self, archive: Path) -> int:
        return sum(path.stat().st_size for path in archive.rglob("*") if path.is_file())

    def retire(self, archive: Path, tombstone: dict[str, Any]) -> None:
        home = archive.parent
        for leftover in home.glob(f".*{RETIRING}"):  # a retirement a dead writer began
            shutil.rmtree(leftover, ignore_errors=True)
        # The tombstone first, appended whole in one write (only the stream's writer retires):
        # a writer that dies after it leaves the window listed, and the next retirement
        # retires it again; a reader keeps the last tombstone of an id.
        line = (json.dumps(tombstone, default=str) + "\n").encode()
        descriptor = os.open(home / RETIRED, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(descriptor, line)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        # Out of every listing at once, then removed: a writer that dies between leaves a
        # hidden directory the next retirement removes, never half a window.
        going = home / f".{archive.name}.{os.getpid()}{RETIRING}"
        archive.rename(going)
        shutil.rmtree(going)

    def retired(self, mission_dir: Path, stream: str) -> list[dict[str, Any]]:
        path = _stream_dir(mission_dir, stream) / RETIRED
        if not path.is_file():
            return []
        stones: dict[Any, dict[str, Any]] = {}
        for line in path.read_bytes().split(b"\n"):
            if line.strip():
                stone = json.loads(line)
                stones[stone.get("id")] = stone
        return list(stones.values())

    def retired_count(self, mission_dir: Path, stream: str) -> int:
        path = _stream_dir(mission_dir, stream) / RETIRED
        return path.read_bytes().count(b"\n") if path.is_file() else 0

    def last_record(self, archive: Path, output: str) -> dict[str, Any] | None:
        sealed = archive / f"{output}.jsonl"
        path = sealed if sealed.exists() else archive / f"{output}{_LIVE}"
        if not path.exists():
            return None
        if path is not sealed:
            _move_torn_tail(path)
        line = _last_line(path)
        if line is None or _is_empty_sentinel(line):
            return None
        record = json.loads(line)
        return record if isinstance(record, dict) else None

    def state(self, archive: Path) -> ArchiveState:
        lock = archive / WRITER_LOCK
        if _locked(lock):
            return "writing"
        if (archive / INCOMPLETE_MARKER).exists():
            return "incomplete"
        if (archive / "meta.yaml").exists():
            return "complete"
        return "incomplete"  # a directory no survey made whole: nothing will finish it

    def write_table(
        self,
        archive: Path,
        output: str,
        records: Generator[dict[str, Any], None, None],
        *,
        columns: Sequence[str] = (),
        keep_on_error: bool = False,
    ) -> int:
        path = archive / f"{output}.jsonl"
        tmp = path.with_suffix(".jsonl.tmp")
        count = 0
        kept: BaseException | None = None
        try:
            # closing() is what runs the collector's own cleanup (a subprocess killed, a
            # client closed) when writing stops early.
            with contextlib.closing(records), _Lines(tmp, self._flush_bytes, self._flush_s) as f:
                try:
                    for record in records:
                        f.write(json.dumps(record, default=str) + "\n")
                        count += 1
                        if count % _FSYNC_EVERY == 0:
                            f.sync()
                except BaseException as exc:
                    if not keep_on_error:
                        raise
                    kept = exc  # raised once what was written is sealed
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
        if kept is not None:
            raise kept
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

    def abandoned(self, mission_dir: Path) -> list[Path]:
        homes = [mission_dir / "archive"]
        homes += [_stream_dir(mission_dir, stream) for stream in self.streams(mission_dir)]
        left = []
        for home in homes:
            if not home.is_dir():
                continue
            for path in sorted(home.iterdir()):
                parts = path.name.split(".")  # "", archive or window id, pid, "making"
                if (
                    path.is_dir()
                    and len(parts) == 4
                    and parts[0] == ""
                    and parts[3] == "making"
                    and (_is_archive_id(parts[1]) or window_index(parts[1]) is not None)
                    and parts[2].isdigit()
                    and not _alive(int(parts[2]))
                ):
                    left.append(path)
        return left

    def archives(self, mission_dir: Path, *, stream: str | None = None) -> list[Path]:
        if stream is not None:
            directory = _stream_dir(mission_dir, stream)
            if not directory.is_dir():
                return []
            indexed = [(window_index(d.name), d) for d in directory.iterdir()]
            windows = sorted((i, d) for i, d in indexed if i is not None)
            return [d for _, d in windows if d.is_dir()]
        archive = mission_dir / "archive"
        if not archive.is_dir():
            return []
        return sorted(d for d in archive.iterdir() if d.is_dir() and _is_archive_id(d.name))

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

    def read_since(
        self, archive: Path, cursor: str | None, *, limit: int, max_bytes: int
    ) -> NewRecords:
        if limit < 1:
            raise UsageError(
                f"a read's limit is the most records it returns, 1 or more; got {limit}",
                fix="read with a limit of 1 or more, or leave it at its default",
            )
        if max_bytes < 1:
            raise UsageError(
                f"a read's max_bytes is the most bytes of records it returns, 1 or more; got "
                f"{max_bytes}",
                fix="read with a max_bytes of 1 or more, or leave it at its default (1 MiB)",
            )
        positions = _decode(cursor, archive)
        files = dict(_table_files(archive))
        records: list[TableRecord] = []
        sealed: list[str] = []
        retracted = [n for n, (_, told) in positions.items() if not told and n not in files]
        budget = _Budget(limit, max_bytes)
        for name, path in files.items():
            offset, told = positions.get(name, (0, False))
            if told:
                continue
            if budget.spent():
                budget.cut = True  # a table left unread, which may have records
                continue
            read = _read_table(path, name, offset, budget)
            if read is None:  # its .tmp went between listing and opening, and no table came
                if name in positions:
                    retracted.append(name)
                continue
            lines, positions[name], ended = read
            records += [TableRecord(name, json.loads(line)) for line in lines]
            if ended:
                positions[name] = (positions[name][0], True)
                sealed.append(name)
        for name in retracted:
            positions.pop(name, None)
        cursor = _encode(archive, positions)
        return NewRecords(records, cursor, sealed, sorted(retracted), more=budget.cut)

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
        for output, file in _table_files(archive):
            if file.name.endswith(_LIVE):
                tables.append(StoredTable(output, file.with_name(f"{output}.jsonl"), sealed=False))
                continue
            columns = _empty_table_columns(file)
            empty = columns is not None
            tables.append(StoredTable(output, file, empty, tuple(columns or ())))
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
    """A file a table is written to, in whole lines: what is given waits in memory until
    ``flush_bytes`` of it wait or ``flush_s`` seconds have passed (64 KiB and 250 ms by
    default), then goes to the file in one ``write`` of whole lines, so a reader of the file
    sees every line written by then, each whole, and no part of the next, at most
    ``flush_s`` late however slowly the lines come. A write the disk
    cuts short is taken back before the error is raised. What reaches the disk itself is
    left to :meth:`sync`.

    Measured by ``scripts/bench_table_write.py`` (200,000-line ``cli`` surveys against the
    buffered writer before it): one ``write`` per line cost 82-111%, past ruling A2's 5%
    bound, so lines are batched as the ruling says to then. A thread writes what waits on
    time, so a collector that is slow to give its next record does not hold back the last
    one; adding a line takes no lock, which a lock per line cost on short lines."""

    def __init__(self, path: Path, flush_bytes: int, flush_s: float) -> None:
        self._descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        self._flush_bytes = flush_bytes
        self._flush_s = flush_s
        self._size = 0
        self._waiting: list[str] = []
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
        # The hot path takes no lock: lines are only ever appended here, and only taken from
        # the front, under the lock, by whoever writes them (a list's append and its slice
        # deletion are each atomic), so order is kept. A line is encoded when written.
        if self._failed is not None:
            raise self._failed
        self._waiting.append(line)
        self._waiting_bytes += len(line)  # a JSON line is ASCII: one byte a character
        if self._waiting_bytes >= self._flush_bytes:
            with self._lock:
                self._write_waiting()
            self._waiting_bytes = 0

    def sync(self) -> None:
        """Write what waits, then flush the file to the disk."""
        with self._lock:
            if self._failed is not None:
                raise self._failed
            self._write_waiting()
            os.fsync(self._descriptor)

    def _write_waiting(self) -> None:
        """Write every line waiting now in one ``write``, and take them from the front of
        the queue. The lock is held; the writer may append meanwhile."""
        waiting = len(self._waiting)
        if not waiting:
            return
        data = "".join(self._waiting[:waiting]).encode()
        written = os.write(self._descriptor, data)
        if written != len(data):
            with contextlib.suppress(OSError):  # the error below is the one to report
                os.ftruncate(self._descriptor, self._size)
            raise OSError(f"wrote {written} of {len(data)} bytes of lines to the table")
        del self._waiting[:waiting]
        self._size += written

    def _write_on_time(self) -> None:
        while not self._stop.wait(self._flush_s):
            with self._lock:
                try:
                    self._write_waiting()
                except OSError as exc:  # raised to the writer at its next line
                    self._failed = exc
                    return


def _stream_dir(mission_dir: Path, stream: str) -> Path:
    return mission_dir / STREAMS / stream


def _move_torn_tail(path: Path) -> None:
    """Move a cut-short last line of ``path`` aside to ``<path>.torn``, as matrix's store
    does; the whole lines before it are left as they are."""
    with open(path, "rb+") as f:
        size = f.seek(0, os.SEEK_END)
        whole = _end_of_last_line(f, size)
        if whole == size:
            return
        f.seek(whole)
        path.with_name(path.name + TORN).write_bytes(f.read())
        f.truncate(whole)


def _end_of_last_line(f: BinaryIO, size: int) -> int:
    """The offset just past the last newline in the first ``size`` bytes of the open file
    ``f`` (0 when there is none), read backwards a block at a time."""
    end = size
    while end > 0:
        start = max(0, end - 65536)
        f.seek(start)
        found = f.read(end - start).rfind(b"\n")
        if found != -1:
            return start + found + 1
        end = start
    return 0


def _last_line(path: Path) -> bytes | None:
    """The last whole line of ``path``; None when it has none."""
    with open(path, "rb") as f:
        end = _end_of_last_line(f, f.seek(0, os.SEEK_END))
        if end == 0:
            return None
        start = _end_of_last_line(f, end - 1)
        f.seek(start)
        return f.read(end - start)


def _is_archive_id(name: str) -> bool:
    """Whether ``name`` is one a survey gives its archive: a stray ``notes/`` is not."""
    try:
        datetime.strptime(name, ARCHIVE_ID_FORMAT)
    except ValueError:
        return False
    return True


#: The suffix of a table being written, or left unfinished by a writer that died.
_LIVE = ".jsonl.tmp"


def _table_files(archive: Path) -> list[tuple[str, Path]]:
    """The archive's tables by output name, each with its file: every JSONL file but the
    capture log and the event log, and each table being written (its ``.jsonl.tmp``) that has
    no sealed file yet."""
    logs = {CAPTURE_LOG_NAME, EVENT_LOG_NAME}
    found: dict[str, Path] = {}
    for path in archive.glob("*.jsonl*"):
        if path.name.endswith(".jsonl") and path.name not in logs:
            found[path.name.removesuffix(".jsonl")] = path
        elif path.name.endswith(_LIVE):
            found.setdefault(path.name.removesuffix(_LIVE), path)
    return sorted(found.items())


#: A cursor's format, which the cursor names: one this store does not know is refused.
_CURSOR_FORMAT = 1


def _encode(archive: Path, positions: dict[str, tuple[int, bool]]) -> str:
    """The cursor at ``positions``: each table's offset, and whether it was told sealed."""
    body = {
        "format": _CURSOR_FORMAT,
        "archive": archive.name,
        "tables": {name: [offset, told] for name, (offset, told) in sorted(positions.items())},
    }
    text = json.dumps(body, separators=(",", ":"))
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _decode(cursor: str | None, archive: Path) -> dict[str, tuple[int, bool]]:
    """The positions ``cursor`` holds; none for no cursor. Raises UsageError for a cursor
    this store did not give, or gave for another archive."""
    if cursor is None:
        return {}
    try:
        body = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, binascii.Error):
        body = None
    tables = body.get("tables") if isinstance(body, dict) else None
    if (
        not isinstance(body, dict)
        or body.get("format") != _CURSOR_FORMAT
        or not isinstance(body.get("archive"), str)
        or not isinstance(tables, dict)
        or not all(_is_offset(offset) for offset in tables.values())
    ):
        raise UsageError(
            "the cursor is not one recon gave: pass on the cursor the last read returned, "
            "unchanged",
            fix="read without a cursor to start from the archive's first record",
        )
    if body["archive"] != archive.name:
        raise UsageError(
            f"the cursor is for archive {body['archive']}, not {archive.name}",
            fix=f"read archive {body['archive']} with it (--archive {body['archive']}), or "
            f"read {archive.name} without a cursor",
            details={"archive": archive.name, "cursor_archive": body["archive"]},
        )
    return {str(name): (int(offset), bool(told)) for name, (offset, told) in tables.items()}


def _is_offset(offset: Any) -> bool:
    """A cursor's say of one table: ``[offset, told]``, the byte offset read to and whether
    the table's seal or retraction was told. Not a record's ``_position``."""
    return (
        isinstance(offset, list)
        and len(offset) == 2
        and isinstance(offset[0], int)
        and not isinstance(offset[0], bool)
        and offset[0] >= 0
        and isinstance(offset[1], bool)
    )


class _Budget:
    """What one read may still return: ``records`` and ``max_bytes`` of them, and always at
    least one record. ``cut`` says it stopped a read before what was there was read."""

    def __init__(self, records: int, max_bytes: int) -> None:
        self._records = records
        self._max_bytes = max_bytes
        self._taken = 0
        self._bytes = 0
        self.cut = False

    def spent(self) -> bool:
        return self._taken >= self._records or self._bytes >= self._max_bytes

    def take(self, line: bytes) -> bool:
        """Count ``line`` in, unless it would pass a bound: a first record always is."""
        if self._taken and (
            self._taken >= self._records or self._bytes + len(line) > self._max_bytes
        ):
            self.cut = True
            return False
        self._taken += 1
        self._bytes += len(line)
        return True


def _read_table(
    path: Path, output: str, offset: int, budget: _Budget
) -> tuple[list[bytes], tuple[int, bool], bool] | None:
    """The whole lines of the table ``output`` from ``offset`` that ``budget`` takes; the
    offset after them; and whether the table is sealed and read to its end. None when a
    table being written went before it was opened, with no sealed file in its place: it was
    retracted."""
    candidates = [path] if not path.name.endswith(_LIVE) else [path, path.with_suffix("")]
    for candidate in candidates:
        try:
            f = open(candidate, "rb")  # noqa: SIM115 - closed below, after a choice of files
        except FileNotFoundError:
            continue
        with f:
            sealed = not candidate.name.endswith(_LIVE)
            size = os.fstat(f.fileno()).st_size
            if offset > size and not sealed:
                # A write the disk cut short was taken back past what this reader read: the
                # table is about to fail and be retracted. Read nothing of it until then.
                return [], (offset, False), False
            if offset > size:
                raise UsageError(
                    f"the cursor is past the end of table {output}: it is not this archive's",
                    fix="read without a cursor to start from the archive's first record",
                )
            if sealed and offset == 0 and _empty_table_columns(candidate) is not None:
                return [], (size, False), True  # an empty table: its one line is no record
            f.seek(offset)
            lines: list[bytes] = []
            while True:
                line = f.readline()
                if not line.endswith(b"\n"):
                    break  # the end, or a line not yet whole
                if not sealed and offset == 0 and _is_empty_sentinel(line):
                    break  # the table is about to be sealed empty: read it once it is
                if not budget.take(line):
                    break
                lines.append(line)
                offset += len(line)
            return lines, (offset, False), sealed and offset == size
    return None


def _object(line: bytes | None) -> dict[str, Any] | None:
    """A record's line as its object; None for no line, or one that holds no object."""
    if line is None:
        return None
    parsed = json.loads(line)
    return parsed if isinstance(parsed, dict) else None


def _is_empty_sentinel(line: bytes) -> bool:
    try:
        parsed = json.loads(line)
    except ValueError:
        return False
    return isinstance(parsed, dict) and parsed.get("_empty") is True


def _hold(path: Path) -> int:
    """Hold an exclusive lock on ``path``, made if need be, until the descriptor is closed.
    It is made in an archive no reader lists yet, so none can test it as it is taken."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(descriptor)
        raise
    return descriptor


def _holder(path: Path) -> int | None:
    """The pid the holder of a stream's lock wrote in it, when it is a process alive on this
    host; None when it has not written it yet, or the file says something else."""
    try:
        said = path.read_text().strip()
    except OSError:
        return None
    if not said.isdigit():
        return None
    pid = int(said)
    return pid if _alive(pid) else None


def _alive(pid: int) -> bool:
    """Whether a process ``pid`` runs on this host. A pid reused since reads alive, so what
    it made is not reported: the error is on the side of saying nothing."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # another user's
        return True
    return True


def _locked(path: Path) -> bool:
    """True while some process, this one included, holds the lock on ``path``."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(descriptor)  # closing releases a lock this call took
    return False


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

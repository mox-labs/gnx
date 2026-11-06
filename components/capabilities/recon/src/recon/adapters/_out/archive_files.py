"""Archives as directories of JSONL files: the :class:`~recon.core.domain.archives.Landing` and
:class:`~recon.core.domain.archives.Archives` ports over the local filesystem. Every location below
is this module's decision; no port says any of it.

::

    <mission>/archive/<archive id>/
      <output>.jsonl          one table per collector and source, one record per line
      <output>.pages.jsonl    a paginated table's page log, each page's token: no table (a
                              window's is added to as its pages are read)
      <output>.keys.jsonl     a compared table's told index: no table
      meta.yaml               the archive's meta (recon version, config sha256, times, and
                              per table: type_url, records, status, seconds, error, range)
      telemetry.jsonl         the survey's recon.v1.event lines, as they happened
      .incomplete             written first; removed once the meta is recorded and every
                              table succeeded
      writer.lock             locked by the writing process from begin to its end, then
                              removed; left unlocked by a writer that died
      captures.jsonl, raw/    the capture log, when the mission preserves raw
    <mission>/archive/retired.jsonl  a line for each archive retain: removed: its tombstone
    <mission>/streams/<stream>/
      writer.lock             locked by the stream's one writer while it holds it, holding
                              its pid (so a writer refused is told whose); kept, emptied
      <window id>/            each window, an archive as above, of one table
      retired.jsonl           a line for each window retain: removed: its tombstone
    <mission>/writer.lock     locked by the mission's one survey while it holds it, holding
                              its pid, as a stream's is; kept, emptied
    <mission>/archive-index.jsonl  each archive's index, a line {id, index} as it is made

**Writing, complete, incomplete.** The writer holds an ``flock`` on ``writer.lock`` from
:meth:`_Hold.begin` until its block ends or the writer dies, as matrix's JSONL store does, so
a pid reused by another process cannot make a dead survey read as one running. An archive is
made whole, its lock held and its marker written, under a name no reader lists
(``.<archive id>.<pid>.making``), then renamed into place, so no archive is ever seen without
them; a writer that dies while making one leaves only that hidden directory
(:meth:`ArchiveFiles.abandoned`). :meth:`ArchiveFiles.state` reads ``writing`` while the lock
is held; else ``incomplete`` while the marker is there; else ``complete`` once ``meta.yaml``
is; else ``incomplete``. Whether a writer is alive is known on its own host only.

**Holds.** :meth:`ArchiveFiles.hold` takes the mission's own ``writer.lock`` (a survey's
hold) or its stream's (a stream's), an ``flock`` that stays when it is let go, so no two
writers ever hold two files of one name. A stream's window id starts with its index
(:func:`~recon.core.domain.stream.window_id`), so the index its one writer gives each new window is
one no other writer gives. The stream is ``writing`` while its lock is held, and otherwise
reads as its last window.

**Archives in the order they were made.** A survey's archive is given the mission's next
index as it is made, before it has its name, a line of ``archive-index.jsonl`` beside its
mission's ``archive/`` (so ``archive/`` holds archives, and SQL reads none of it); the survey
records it in its meta too. Archives are listed by index, so a clock set back between two
surveys never puts the later before the earlier. One with no index (made before indexes) is
listed first, by its id, which is when it began. While ``archive-index.jsonl`` is lost, the
indexes are read from each archive's ``meta.yaml`` instead (Dijkstra F1), and the next archive
made writes the record again from them.

**Reading as it is written.** :meth:`ArchiveFiles.read_since` reads each table's file from a
byte offset to its last whole line, the live ``.tmp`` of a table being written included, and
hands back a cursor: the offset reached in each table, and which tables it has told exhausted,
as base64url JSON naming the archive. Only this adapter reads it; no path leaves it. A table
this cursor read from whose file is gone, with no sealed file in its place, failed and was
retracted.

A table is written to ``<output>.jsonl.tmp`` and renamed to ``<output>.jsonl`` when it
ends; a table that fails is retracted, its ``.tmp`` removed. While it is written, its lines
reach the ``.tmp`` whole, at most 64 KiB or 250 ms after they are given (:class:`_Lines`),
and every 1000 records are flushed to the disk.

A table that collected nothing holds one sentinel line, ``{"_empty": true}``, so a reader of
the file sees it was written; it is not a record and is not counted. When the table's
columns are known (its collector's ``normalize:`` spec), the line keeps them as
``_columns``. :meth:`ArchiveFiles.tables` describes each table to the query engine, the
sentinel read, so no other adapter knows any of this. This is table layout
:data:`TABLE_LAYOUT`, which ``meta.yaml`` names as ``table_layout`` (an archive whose
``meta.yaml`` does not name one is in layout 1); a layout this recon does not know is refused
rather than misread. ``meta.yaml`` is read back in the current shape whether it was written as
``format_version: 1`` (``collectors`` with an ``error`` string) or 2 (``tables`` with
``error: {kind, message}``).
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
import os
import shutil
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, BinaryIO

import yaml

from recon.adapters._out.capture_files import FilesystemCaptureLog
from recon.adapters._out.event_log import EventLog
from recon.adapters._out.file_locks import alive, hold, hold_naming_pid, holder, let_go, locked
from recon.adapters._out.mission_layout import (
    STREAMS,
    archive_dir,
    home,
    is_archive_id,
    mission_dir,
    stream_dir,
)
from recon.adapters._out.private_files import DIR_MODE, make_dirs, open_file, write_file
from recon.core.domain.archives import (
    RESERVED_OUTPUTS,
    SIDECARS,
    Abandoned,
    ArchiveRef,
    ArchiveState,
    NewRecords,
    Sidecar,
    TableEnds,
    TableRecord,
)
from recon.core.domain.capture import CAPTURE_LOG_NAME
from recon.core.domain.changes import CHANGES_SUFFIX
from recon.core.domain.errors import NotFoundError, QueryError, ReconError, UsageError
from recon.core.domain.stream import window_index
from recon.core.domain.type_url import DEFAULT_RECORDS_TYPE_URL

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable, Iterator, Mapping, Sequence
    from pathlib import Path

    from recon.core.domain.archives import Hold, Landed
    from recon.core.domain.clock import Clock

INCOMPLETE_MARKER = ".incomplete"

#: Locked by the writing process while it writes the archive, or holds the mission or stream.
WRITER_LOCK = "writer.lock"

#: The archive's event log, beside its meta; never a table.
EVENT_LOG_NAME = "telemetry.jsonl"

#: Where a table's torn last line is moved: a write a dying writer cut short.
TORN = ".torn"

#: The tombstones of a mission's retired archives (or a stream's windows), one line each,
#: appended beside them; and the suffix of an archive being removed, out of every listing.
RETIRED = "retired.jsonl"
RETIRING = ".retiring"

#: Each of a mission's archives' index, a line ``{id, index}`` an archive, in the mission's
#: directory beside ``archive/``.
INDEX = "archive-index.jsonl"

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

assert {CAPTURE_LOG_NAME, EVENT_LOG_NAME} == {f"{name}.jsonl" for name in RESERVED_OUTPUTS}


@dataclass(frozen=True)
class StoredTable:
    """One of an archive's tables, as this adapter describes it to the query engine it is
    built with (:class:`~recon.adapters._out.duckdb_query.TableFiles`).

    ``output`` is the collector's output name. ``file`` holds its records, one JSON object per
    line. ``empty`` says it collected none: then the file is not to be read as records, and
    ``columns`` are the ones it keeps, its collector's ``normalize:`` spec's when that was
    known. ``sealed``: its writer renamed it into place as it ended; false for a table still
    being written, or one a writer that died was writing, whose records are provisional."""

    output: str
    file: Path
    empty: bool = False
    columns: tuple[str, ...] = ()
    sealed: bool = True


class ArchiveFiles:
    """The archives of every mission under ``root`` (the project's ``.recon`` directory)."""

    def __init__(
        self,
        root: Path,
        clock: Clock,
        *,
        flush_bytes: int = FLUSH_BYTES,
        flush_s: float = FLUSH_S,
    ) -> None:
        """``flush_bytes`` and ``flush_s`` bound how long a table's records wait before a
        reader of the table can see them: in bytes waiting, and in seconds."""
        if flush_bytes < 1 or flush_s <= 0:
            raise ValueError(f"flush_bytes must be >= 1 and flush_s > 0, got {flush_bytes}, "
                             f"{flush_s}")  # fmt: skip
        self._root = root.absolute()
        self._clock = clock
        self._flush_bytes = flush_bytes
        self._flush_s = flush_s
        #: The holds this store's writers have, by the lock file each holds.
        self._holding: set[Path] = set()

    # --- Landing --------------------------------------------------------------------------

    @contextlib.contextmanager
    def hold(self, mission: str, *, stream: str | None = None) -> Iterator[Hold]:
        lock = (
            mission_dir(self._root, mission) if stream is None
            else stream_dir(self._root, mission, stream)
        ) / WRITER_LOCK  # fmt: skip
        descriptor = self._take(lock, mission, stream)
        self._holding.add(lock)
        try:
            yield _Hold(self, mission, stream)
        finally:
            self._holding.discard(lock)
            let_go(descriptor)

    def _take(self, lock: Path, mission: str, stream: str | None) -> int:
        """The lock ``lock`` taken for this writer; ReconError (``busy``) while another
        writer, or another hold of this store, has it."""
        try:
            if lock in self._holding:  # a hold is its writer's alone
                raise BlockingIOError(lock)
            make_dirs(lock.parent)
            return hold_naming_pid(lock)
        except OSError as exc:
            raise _busy(lock, mission, stream) from exc

    def _begin(self, mission: str, stream: str | None, archive_id: str) -> _Landed:
        ref = ArchiveRef(mission, archive_id, stream)
        archive = archive_dir(self._root, ref)
        make_dirs(archive.parent)
        # Made whole under a name no reader lists, then renamed into place: an archive is
        # never seen without its lock and marker, whenever its writer dies.
        making = archive.with_name(f".{archive_id}.{os.getpid()}.making")
        making.mkdir(mode=DIR_MODE)
        descriptor = hold(making / WRITER_LOCK)
        index = window_index(archive_id) if stream is not None else None
        try:
            write_file(making / INCOMPLETE_MARKER, b"")
            taken = ReconError(
                f"archive {archive_id} of {mission} already exists; an archive is never "
                "written twice",
                kind="store",
                fix="survey again: a new survey's archive is named for the moment it starts",
                details={"archive": archive_id},
            )
            if archive.exists():
                raise taken
            if stream is None:  # its index first: one that never gets its archive names nothing
                index = self._give_index(mission, archive_id)
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
        return _Landed(self, ArchiveRef(mission, archive_id, stream, index), archive, descriptor)

    def _derive(self, ref: ArchiveRef) -> _Landed:
        """A sealed archive opened again under its own writer lock, so it reads ``writing``
        while what is derived from it is written."""
        archive = archive_dir(self._root, ref)
        if not (archive / "meta.yaml").exists():
            raise NotFoundError(
                f"archive {ref.id} of {ref.mission} was never sealed, so nothing is derived "
                "from it",
                fix=f"recon status {ref.mission} lists its archives",
                details={"archive": ref.id},
            )
        try:
            descriptor = hold(archive / WRITER_LOCK)
        except OSError as exc:
            raise ReconError(
                f"archive {ref.id} of {ref.mission} is held by another writer",
                kind="busy",
                fix="wait for it to end, then run again",
                details={"archive": ref.id},
            ) from exc
        return _Landed(self, ref, archive, descriptor)

    def _give_index(self, mission: str, archive_id: str) -> int:
        """Record ``archive_id`` as the mission's next archive and return its index: one more
        than the last index given (or than the archives' ``meta.yaml`` say, when the record is
        lost), and than every retired archive's tombstone holds (a lost record names only
        those kept)."""
        given = self._index_lines(mission)
        lines = b""
        if given is None:  # lost, or never made: made again from what the archives say
            given = self._meta_indexes(mission)
            lines = b"".join(
                _index_line(a, i) for a, i in sorted(given.items(), key=lambda x: x[1])
            )
        index = max([*given.values(), *self._retired_indexes(mission)], default=0) + 1
        lines += _index_line(archive_id, index)
        descriptor = open_file(mission_dir(self._root, mission) / INDEX,
                               os.O_WRONLY | os.O_APPEND)  # fmt: skip
        try:
            os.write(descriptor, lines)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return index

    def _given(self, mission: str) -> dict[str, int]:
        """The archives' indexes: the record's, or, while it is lost, what each archive's
        ``meta.yaml`` says (read, never written: the next archive made writes the record)."""
        given = self._index_lines(mission)
        return self._meta_indexes(mission) if given is None else given

    def _meta_indexes(self, mission: str) -> dict[str, int]:
        """The index each archive's ``meta.yaml`` holds, by id; none for one with none."""
        found: dict[str, int] = {}
        for archive in self._listed(home(self._root, mission, None)):
            index = _read_meta(archive).get("index")
            if type(index) is int:
                found[archive.name] = index
        return found

    def _retired_indexes(self, mission: str) -> list[int]:
        """The indexes the retired archives' tombstones hold; a torn line holds none."""
        try:
            raw = (home(self._root, mission, None) / RETIRED).read_bytes()
        except FileNotFoundError:
            return []
        found: list[int] = []
        for line in raw.splitlines():
            with contextlib.suppress(ValueError, AttributeError):
                index = json.loads(line).get("index")
                if type(index) is int:
                    found.append(index)
        return found

    def _index_lines(self, mission: str) -> dict[str, int] | None:
        """The indexes the mission's ``archive-index.jsonl`` gives, by archive id (a later line
        of an id is the one kept); None when it is not there."""
        try:
            raw = (mission_dir(self._root, mission) / INDEX).read_bytes()
        except FileNotFoundError:
            return None
        given: dict[str, int] = {}
        for line in raw.splitlines():
            with contextlib.suppress(ValueError, AttributeError):
                entry = json.loads(line)
                if isinstance(entry.get("id"), str) and type(entry.get("index")) is int:
                    given[entry["id"]] = entry["index"]
        return given

    def _retire(self, ref: ArchiveRef, tombstone: Mapping[str, Any]) -> None:
        archive = archive_dir(self._root, ref)
        where = archive.parent
        for leftover in where.glob(f".*{RETIRING}"):  # a retirement a dead writer began
            shutil.rmtree(leftover, ignore_errors=True)
        # The tombstone first, appended whole in one write (only the holder retires): a writer
        # that dies after it leaves the archive listed, and the next retirement retires it
        # again; a reader keeps the last tombstone of an id. A survey's archive's tombstone
        # keeps its index, which leaves the index record with it.
        stone = {**tombstone, "id": ref.id}
        if stone.get("index") is None:
            stone["index"] = ref.index if ref.index is not None else (
                self._given(ref.mission).get(ref.id) if ref.stream is None
                else window_index(ref.id)
            )  # fmt: skip
        line = (json.dumps(stone, default=str) + "\n").encode()
        descriptor = open_file(where / RETIRED, os.O_WRONLY | os.O_APPEND)
        try:
            os.write(descriptor, line)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        # Out of every listing at once, then removed: a writer that dies between leaves a
        # hidden directory the next retirement removes, never half an archive.
        going = where / f".{ref.id}.{os.getpid()}{RETIRING}"
        archive.rename(going)
        shutil.rmtree(going)
        if ref.stream is None:  # a survey's archive: its index line goes with it
            self._drop_index(ref.mission, ref.id)

    def _drop_index(self, mission: str, archive_id: str) -> None:
        """The index record without ``archive_id``'s line, replaced whole; one not there, or
        holding no such line, is left as it is."""
        given = self._index_lines(mission)
        if given is None or archive_id not in given:
            return
        kept = sorted(((a, i) for a, i in given.items() if a != archive_id), key=lambda x: x[1])
        where = mission_dir(self._root, mission)
        tmp = where / f".{INDEX}.{os.getpid()}.tmp"
        write_file(tmp, b"".join(_index_line(a, i) for a, i in kept))
        os.replace(tmp, where / INDEX)

    # --- Archives -------------------------------------------------------------------------

    def archives(self, mission: str, *, stream: str | None = None) -> list[ArchiveRef]:
        if stream is not None:
            directory = stream_dir(self._root, mission, stream)
            if not directory.is_dir():
                return []
            indexed = [(window_index(d.name), d) for d in directory.iterdir()]
            windows = sorted((i, d.name) for i, d in indexed if i is not None and d.is_dir())
            return [ArchiveRef(mission, name, stream, i) for i, name in windows]
        listed = self._listed(home(self._root, mission, None))
        given = self._given(mission)
        refs = [ArchiveRef(mission, a.name, None, given.get(a.name)) for a in listed]
        return sorted(refs, key=lambda ref: ref.rank)

    def _listed(self, where: Path) -> list[Path]:
        """The survey archives in ``where``, by id."""
        if not where.is_dir():
            return []
        return sorted(
            d for d in where.iterdir() if d.is_dir() and is_archive_id(d.name, stream=False)
        )

    def streams(self, mission: str) -> list[str]:
        directory = mission_dir(self._root, mission) / STREAMS
        if not directory.is_dir():
            return []
        return sorted(d.name for d in directory.iterdir() if d.is_dir())

    def state(self, ref: ArchiveRef) -> ArchiveState:
        archive = archive_dir(self._root, ref)
        if locked(archive / WRITER_LOCK):
            return "writing"
        if (archive / INCOMPLETE_MARKER).exists():
            return "incomplete"
        if (archive / "meta.yaml").exists():
            return "complete"
        return "incomplete"  # a directory no survey made whole: nothing will finish it

    def stream_state(self, mission: str, stream: str) -> ArchiveState | None:
        if locked(stream_dir(self._root, mission, stream) / WRITER_LOCK):
            return "writing"
        windows = self.archives(mission, stream=stream)
        return self.state(windows[-1]) if windows else None

    def meta(self, ref: ArchiveRef) -> dict[str, Any]:
        return _read_meta(archive_dir(self._root, ref))

    def records(
        self, ref: ArchiveRef, output: str, *, unsealed: bool = False
    ) -> Iterator[dict[str, Any]] | None:
        archive = archive_dir(self._root, ref)
        path = archive / f"{output}.jsonl"
        if path.is_file():
            return _table_records(path)
        live = archive / f"{output}{_LIVE}"
        if unsealed and live.is_file():
            return _table_records(live, whole=True)
        return None

    def sidecar(
        self, ref: ArchiveRef, output: str, sidecar: Sidecar
    ) -> Iterator[dict[str, Any]] | None:
        path = archive_dir(self._root, ref) / f"{output}.{sidecar}.jsonl"
        if not path.is_file():
            return None
        return _sidecar_lines(path)

    def last_record(self, ref: ArchiveRef, output: str) -> dict[str, Any] | None:
        archive = archive_dir(self._root, ref)
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

    def read_since(
        self,
        ref: ArchiveRef,
        cursor: str | None,
        *,
        max_records: int,
        max_bytes: int,
        tables: Sequence[str] | None = None,
    ) -> NewRecords:
        if max_records < 1:
            raise UsageError(
                f"a read's max_records is the most records it returns, 1 or more; got "
                f"{max_records}",
                fix="read with a max_records of 1 or more, or leave it at its default (1000)",
            )
        if max_bytes < 1:
            raise UsageError(
                f"a read's max_bytes is the most bytes of records it returns, 1 or more; got "
                f"{max_bytes}",
                fix="read with a max_bytes of 1 or more, or leave it at its default (1 MiB)",
            )
        archive = archive_dir(self._root, ref)
        positions = _decode(cursor, ref.id)
        files = dict(_table_files(archive))
        if tables is not None:
            files = {name: path for name, path in files.items() if name in tables}
        records: list[TableRecord] = []
        exhausted: list[str] = []
        named = positions if tables is None else {n: p for n, p in positions.items() if n in tables}
        retracted = [n for n, (_, told) in named.items() if not told and n not in files]
        budget = _Budget(max_records, max_bytes)
        for name, path in files.items():
            offset, told = positions.get(name, (0, False))
            if told:
                continue
            if path.name.endswith(_LIVE) and name.endswith(f".{CHANGES_SUFFIX}"):
                # A change table is read once sealed, never as it is written: one its writer
                # died writing told nothing, and the next survey tells its rows (review G2).
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
                exhausted.append(name)
        for name in retracted:
            positions.pop(name, None)
        return NewRecords(records, _encode(ref.id, positions), exhausted, sorted(retracted),
                          more=budget.cut)  # fmt: skip

    def size(self, ref: ArchiveRef) -> int:
        archive = archive_dir(self._root, ref)
        return sum(path.stat().st_size for path in archive.rglob("*") if path.is_file())

    def retired(self, mission: str, *, stream: str | None = None) -> list[dict[str, Any]]:
        path = home(self._root, mission, stream) / RETIRED
        if not path.is_file():
            return []
        stones: dict[Any, dict[str, Any]] = {}
        for line in path.read_bytes().split(b"\n"):
            if line.strip():
                with contextlib.suppress(ValueError):  # a torn last line is no tombstone
                    stone = json.loads(line)
                    if isinstance(stone, dict):
                        stones[stone.get("id")] = stone
        return list(stones.values())

    def retired_count(self, mission: str, stream: str) -> int:
        path = stream_dir(self._root, mission, stream) / RETIRED
        return path.read_bytes().count(b"\n") if path.is_file() else 0

    def abandoned(self, mission: str) -> list[Abandoned]:
        homes = [(home(self._root, mission, None), False)]
        homes += [(stream_dir(self._root, mission, s), True) for s in self.streams(mission)]
        left = []
        for where, windows in homes:
            if not where.is_dir():
                continue
            for path in sorted(where.iterdir()):
                parts = path.name.split(".")  # "", archive or window id, pid, "making"
                if (
                    path.is_dir()
                    and len(parts) == 4
                    and parts[0] == ""
                    and parts[3] == "making"
                    and is_archive_id(parts[1], stream=windows)
                    and parts[2].isdigit()
                    and not alive(int(parts[2]))
                ):
                    left.append(Abandoned(where=str(path), fix=f"rm -r '{path}'"))
        return left

    # --- what the query engine reads (duckdb_query.TableFiles) ----------------------------

    def tables(self, ref: ArchiveRef) -> list[StoredTable]:
        """The archive's tables, by output name, those not yet sealed included. Raises
        QueryError when they are in a layout this store does not read (one a newer recon
        wrote)."""
        archive = archive_dir(self._root, ref)
        layout = _table_layout(archive)
        if layout != TABLE_LAYOUT:
            raise QueryError(
                f"archive {ref.id} holds its tables in table layout {layout}, which this "
                f"recon does not read (it reads layout {TABLE_LAYOUT})",
                fix="upgrade recon to the version that surveyed this archive, or later",
                details={"archive": ref.id, "table_layout": layout},
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


def _busy(lock: Path, mission: str, stream: str | None) -> ReconError:
    """The refusal of a hold another writer has."""
    pid = holder(lock)
    if stream is not None:
        if pid == os.getpid():
            stop = "this process already writes it: stream it once the run there returns"
        elif pid is not None:
            stop = (f"let it run, or stop it: kill -TERM {pid} (its pid) seals the window it is "
                    "writing; then stream again")  # fmt: skip
        else:
            stop = (f"let it run, or stop it (`recon status {mission}` shows the stream "
                    "writing); then stream again")  # fmt: skip
        return ReconError(
            f"another recon is writing stream {stream} of {mission}; a stream has one writer "
            "at a time",
            kind="busy",
            fix=stop,
            details={"stream": stream} | ({} if pid is None else {"pid": pid}),
        )
    if pid == os.getpid():
        stop = "this process is surveying it now: survey again once that survey returns"
    elif pid is not None:
        stop = (f"let it run, or stop it: kill -TERM {pid} (its pid) stops it, its archive "
                "reading ended: stopped; then survey again")  # fmt: skip
    else:
        stop = (f"let it run (`recon status {mission}` shows its archive writing), then survey "
                "again")  # fmt: skip
    return ReconError(
        f"another recon is surveying mission {mission}; a mission has one survey at a time",
        kind="busy",
        fix=stop,
        details={"mission": mission} | ({} if pid is None else {"pid": pid}),
    )


class _Hold:
    """A writer's hold (:class:`~recon.core.domain.archives.Hold`) through one ArchiveFiles."""

    def __init__(self, store: ArchiveFiles, mission: str, stream: str | None) -> None:
        self._store = store
        self._mission = mission
        self._stream = stream

    @contextlib.contextmanager
    def begin(self, archive_id: str) -> Iterator[Landed]:
        landed = self._store._begin(self._mission, self._stream, archive_id)
        try:
            yield landed
        finally:
            landed.release()

    @contextlib.contextmanager
    def derive(self, ref: ArchiveRef) -> Iterator[Landed]:
        self._held(ref, "derive from")
        landed = self._store._derive(ref)
        try:
            yield landed
        finally:
            landed.release()

    def retire(self, ref: ArchiveRef, tombstone: Mapping[str, Any]) -> None:
        self._held(ref, "retire")
        self._store._retire(ref, tombstone)

    def _held(self, ref: ArchiveRef, act: str) -> None:
        if (ref.mission, ref.stream) != (self._mission, self._stream):
            raise UsageError(
                f"archive {ref.id} is not one of what this writer holds",
                fix=f"{act} an archive under the hold of its own mission or stream",
                details={"archive": ref.id},
            )


class _Landed:
    """One archive being written (:class:`~recon.core.domain.archives.Landed`)."""

    def __init__(
        self, store: ArchiveFiles, ref: ArchiveRef, archive: Path, descriptor: int
    ) -> None:
        self._store = store
        self._ref = ref
        self._archive = archive
        self._descriptor: int | None = descriptor
        #: The lines of each table being written, by output, so a line added to its sidecar
        #: is written after them.
        self._writing: dict[str, _Lines] = {}

    @property
    def ref(self) -> ArchiveRef:
        return self._ref

    def release(self) -> None:
        """Let go of the archive, however it ended: it reads ``complete`` or ``incomplete``."""
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        # Removed while still locked, so no reader finds it unlocked beside a finished archive.
        with contextlib.suppress(OSError):
            (self._archive / WRITER_LOCK).unlink()
        os.close(descriptor)  # closing releases the lock

    def write(
        self,
        output: str,
        records: Generator[dict[str, Any], None, None],
        *,
        columns: Sequence[str] = (),
        keep_on_error: bool = False,
    ) -> int:
        path = self._archive / f"{output}.jsonl"
        tmp = path.with_suffix(".jsonl.tmp")
        count = 0
        kept: BaseException | None = None
        try:
            # closing() is what runs the collector's own cleanup (a subprocess killed, a
            # client closed) when writing stops early.
            with (
                contextlib.closing(records),
                _Lines(tmp, self._store._flush_bytes, self._store._flush_s) as f,
            ):
                self._writing[output] = f
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
                finally:
                    self._writing.pop(output, None)
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

    def ends(self, output: str) -> TableEnds:
        sealed = self._archive / f"{output}.jsonl"
        path = sealed if sealed.exists() else self._archive / f"{output}{_LIVE}"
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

    def keep(self, output: str, sidecar: Sidecar, lines: Iterable[dict[str, Any]]) -> None:
        path = self._archive / f"{output}.{sidecar}.jsonl"
        tmp = path.with_suffix(".jsonl.tmp")
        try:
            with open(open_file(tmp, os.O_WRONLY | os.O_TRUNC), "w", encoding="utf-8") as f:
                for line in lines:
                    f.write(json.dumps(line, default=str) + "\n")
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise

    def mark(self, output: str, sidecar: Sidecar, line: dict[str, Any]) -> None:
        lines = self._writing.get(output)
        if lines is not None:
            lines.flush()  # the table's lines given before this one reach the file first
        data = (json.dumps(line, default=str) + "\n").encode()
        descriptor = open_file(
            self._archive / f"{output}.{sidecar}.jsonl", os.O_WRONLY | os.O_APPEND
        )
        try:
            if os.write(descriptor, data) != len(data):
                raise OSError(f"wrote part of a line to {output}'s {sidecar}")
        finally:
            os.close(descriptor)

    def captures(self) -> FilesystemCaptureLog:
        return FilesystemCaptureLog(self._archive, self._store._clock)

    def events(self) -> EventLog:
        return EventLog(self._archive / EVENT_LOG_NAME)

    def record(self, meta: Mapping[str, Any]) -> None:
        path = self._archive / "meta.yaml"
        tmp = path.with_suffix(".yaml.tmp")
        stamped = {**meta, "table_layout": TABLE_LAYOUT}
        try:
            text = yaml.safe_dump(stamped, default_flow_style=False, sort_keys=False)
            write_file(tmp, text.encode())
            tmp.replace(path)
        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise

    def complete(self) -> None:
        (self._archive / INCOMPLETE_MARKER).unlink(missing_ok=True)


def _read_meta(archive: Path) -> dict[str, Any]:
    """``meta.yaml`` in the current format, whichever format it was written in."""
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
            "tables": [_v1_table(c) for c in raw.get("collectors") or [] if isinstance(c, dict)],
        }
    return {**raw, "tables": [t for t in raw.get("tables") or [] if isinstance(t, dict)]}


def _table_layout(archive: Path) -> Any:
    """The layout ``meta.yaml`` names; 1, the only one there was before it was named, when it
    names none or there is no readable ``meta.yaml`` yet (a survey running, or killed)."""
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
        self._descriptor = open_file(path, os.O_WRONLY | os.O_TRUNC)
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

    def flush(self) -> None:
        """Write what waits, without flushing the file to the disk: what follows it in another
        file is then written after it."""
        with self._lock:
            if self._failed is not None:
                raise self._failed
            self._write_waiting()

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


def _index_line(archive: str, index: int) -> bytes:
    return (json.dumps({"id": archive, "index": index}) + "\n").encode()


def _move_torn_tail(path: Path) -> None:
    """Move a cut-short last line of ``path`` aside to ``<path>.torn``, as matrix's store
    does; the whole lines before it are left as they are."""
    with open(path, "rb+") as f:
        size = f.seek(0, os.SEEK_END)
        whole = _end_of_last_line(f, size)
        if whole == size:
            return
        f.seek(whole)
        write_file(path.with_name(path.name + TORN), f.read())
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


#: The suffix of a table being written, or left unfinished by a writer that died.
_LIVE = ".jsonl.tmp"


def _sidecar_lines(path: Path) -> Iterator[dict[str, Any]]:
    """A sidecar's lines; one a writer that died cut short (no newline) is none of them."""
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.endswith("\n"):
                return
            yield json.loads(line)


def _table_records(path: Path, *, whole: bool = False) -> Iterator[dict[str, Any]]:
    """A sealed table's records: each line, but a table's that collected nothing, whose one
    line is the empty sentinel. ``whole``: a table its writer died writing, whose last line
    may be cut short: each line that ends, and no other."""
    with path.open("rb") as f:
        for line in f:
            if whole and not line.endswith(b"\n"):
                break
            record = json.loads(line)
            if isinstance(record, dict) and record.get("_empty") is True:
                continue
            yield record


def _is_sidecar(name: str) -> bool:
    """Whether ``name`` is a table's sidecar (``<output>.pages.jsonl``), being written or not."""
    stem = name.removesuffix(".tmp").removesuffix(".jsonl")
    return any(stem.endswith(f".{sidecar}") for sidecar in SIDECARS)


def _table_files(archive: Path) -> list[tuple[str, Path]]:
    """The archive's tables by output name, each with its file: every JSONL file but the
    capture log, the event log and the tables' sidecars, and each table being written (its
    ``.jsonl.tmp``) that has no sealed file yet."""
    logs = {CAPTURE_LOG_NAME, EVENT_LOG_NAME}
    found: dict[str, Path] = {}
    for path in archive.glob("*.jsonl*"):
        if _is_sidecar(path.name):
            continue
        if path.name.endswith(".jsonl") and path.name not in logs:
            found[path.name.removesuffix(".jsonl")] = path
        elif path.name.endswith(_LIVE):
            found.setdefault(path.name.removesuffix(_LIVE), path)
    return sorted(found.items())


#: A cursor's format, which the cursor names: one this store does not know is refused.
_CURSOR_FORMAT = 1


def _encode(archive: str, positions: dict[str, tuple[int, bool]]) -> str:
    """The cursor at ``positions``: each table's offset, and whether it was told exhausted."""
    body = {
        "format": _CURSOR_FORMAT,
        "archive": archive,
        "tables": {name: [offset, told] for name, (offset, told) in sorted(positions.items())},
    }
    text = json.dumps(body, separators=(",", ":"))
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _decode(cursor: str | None, archive: str) -> dict[str, tuple[int, bool]]:
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
    if body["archive"] != archive:
        raise UsageError(
            f"the cursor is for archive {body['archive']}, not {archive}",
            fix=f"read archive {body['archive']} with it (--archive {body['archive']}), or "
            f"read {archive} without a cursor",
            details={"archive": archive, "cursor_archive": body["archive"]},
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

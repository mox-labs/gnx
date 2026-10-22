"""A directory of runs, each a set of typed JSONL tables written as the run goes.

    <root>/<run_id>/run.json                 {"schema": "matrix.v1.run", run_id, status, ...,
                                               "tables": {kind: {"file", "rows"}}}
    <root>/<run_id>/<kind>.jsonl             one row per line: the typed record itself
    <root>/<run_id>/matrix.v1.ledger.jsonl   one line per row landed: seq, kind, row,
                                               component, time
    <root>/<run_id>/writer.lock              locked by the writing process while it lives
    <root>/<run_id>/events.jsonl             the run's events, one ``matrix.v1.event`` a line

One kind per file, and the kind is its schema, so ``duckdb``'s ``read_json_auto`` or ``jq``
reads a table without matrix.

**While a run is going** ``run.json`` says ``running`` and every table file exists. Each row
is one ``write`` of one whole line to a file opened for appending, made before ``append``
returns: a process killed at any moment leaves every row it had appended. A write the disk
cuts short is taken back before the error is raised. (Pass ``fsync=True`` to also survive the
machine going down, at the cost of a disk flush per row; the run's directory is flushed too
once its files are made, and once ``run.json`` is renamed into place.) A line that was cut
short is the last line of its file and has no newline; matrix's readers ignore it.

**A row is JSON data.** Models, dataclasses, sets, tuples and datetimes inside it are written
as pydantic writes them (``to_jsonable_python``). A row holding anything else is refused with a
ContractError naming where in the row it is, before anything is written for it: a row's ledger
entry waits for the row, and the two are written together.

**When a run ends** ``run.json`` is replaced in one rename with the final document. A run
that never got that far still reads ``running``, until a matrix reader (``describe`` or
``load``) finds that the process that was writing it, on this host, is gone. That reader
**finishes the record as interrupted**: each file's cut-short last line, if it has one, is
moved aside to ``<file>.torn``, and ``run.json`` is replaced with ``status: interrupted``,
each table's row count and ``torn``. From then on every file reads whole with ``jq`` or
DuckDB, and ``interrupted`` is on disk. "Gone" is read from ``writer.lock``, which the writer
holds an ``flock`` on from ``open`` until ``finish`` or its death, so a pid reused by another
process cannot make a dead run read as running. The reader holds an ``flock`` on the run's
directory while it finishes the record, so two readers finish it once, and a second reader
waits for the first rather than read the run as running. Finishing the record is
best-effort: a reader who cannot write it (a read-only mount, another user's runs) logs that
on ``matrix.store`` and reads it as it stands, ``interrupted``, with each table's whole lines
counted, leaving the files as they are. A dead run on another host stays ``running``: matrix
does not guess.

**Events** are telemetry kept beside the record: lossy by design (``run.json``'s
``events_dropped`` counts what is missing), flushed line by line, and never read back as data.
Results are read from the tables.

Type URLs: ``matrix.v1.store.jsonl`` (config: ``root``, default ``.matrix/runs`` under the
working directory; ``fsync``) and ``matrix.v1.store.memory``.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import socket
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict
from pydantic_core import PydanticSerializationError, to_jsonable_python

from matrix.domain.component import LEDGER
from matrix.domain.construct import Construct
from matrix.domain.errors import ConfigError, ContractError, NotFoundError, StoreError

if TYPE_CHECKING:
    from matrix.domain.executor import Run
    from matrix.domain.observer import Event

#: The file a run's events are kept in, beside its tables.
EVENTS = "events.jsonl"
#: Beside a file, what was cut from it when an interrupted run's record was finished.
TORN = ".torn"

logger = logging.getLogger("matrix.store")


class JsonlStoreConfig(BaseModel):
    """``matrix.store`` for ``type: jsonl``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The directory runs are kept under; relative to the working directory.
    root: str = ".matrix/runs"
    #: Flush every row to disk before ``append`` returns: survives the machine going down.
    fsync: bool = False


_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _encode(kind: str, row: Any) -> bytes:
    """``row`` as one JSON line. Raises ContractError naming the part of it that is not data."""
    try:
        return json.dumps(to_jsonable_python(row)).encode() + b"\n"
    except (PydanticSerializationError, TypeError, ValueError) as e:
        raise ContractError(
            f"a row of {kind!r} is not JSON data at {_culprit(row)}: {e}",
            fix="hand over data (models, dicts, lists, strings, numbers), not live objects",
            table=kind,
        ) from None


def _culprit(value: Any, path: str = "") -> str:
    """Where in ``value`` the first part that cannot be written as JSON is, as a dotted path."""
    if isinstance(value, dict):
        items: Any = value.items()
    elif isinstance(value, list | tuple):
        items = enumerate(value)
    else:
        return path or "(root)"
    for key, item in items:
        try:
            json.dumps(to_jsonable_python(item))
        except (PydanticSerializationError, TypeError, ValueError):
            return _culprit(item, f"{path}.{key}" if path else str(key))
    return path or "(root)"


def _file(kind: str) -> str:
    return f"{_UNSAFE.sub('_', kind)}.jsonl"


def _lines(path: Path) -> list[bytes]:
    """The complete lines of a table file; a cut-short last line is not one."""
    if not path.exists():
        return []
    return path.read_bytes().split(b"\n")[:-1]


_LOCK = "writer.lock"


def _hold(path: Path) -> int:
    """Create ``path`` and hold an exclusive lock on it until the descriptor is closed."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return descriptor


def _flush_directory(directory: Path) -> None:
    """Make the entries of ``directory`` (files made, a file renamed in) durable: a file's own
    flush does not cover its name."""
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _cut_torn_tail(path: Path) -> int:
    """Move a cut-short last line of ``path`` aside to ``<path>.torn``; return the complete
    lines it holds."""
    if not path.exists():
        return 0
    data = path.read_bytes()
    whole = data.rfind(b"\n") + 1
    if whole < len(data):
        path.with_name(path.name + TORN).write_bytes(data[whole:])
        os.truncate(path, whole)
    return data[:whole].count(b"\n")


def _writer_alive(path: Path) -> bool:
    """True while some process holds the lock on ``path``."""
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


class _EventLog:
    """The run's events, one ``matrix.v1.event`` document a line, flushed as written."""

    def __init__(self, path: Path) -> None:
        self._file = path.open("a", encoding="utf-8")

    def on_event(self, event: Event) -> None:
        self._file.write(json.dumps(to_jsonable_python(event.document(), fallback=str)) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


class _JsonlRunWriter:
    def __init__(self, directory: Path, tables: dict[str, str], fsync: bool, lock: int) -> None:
        self._directory = directory
        self._tables = tables
        self._fsync = fsync
        self._lock = lock
        self._open: dict[str, int] = {}
        self._rows: dict[str, int] = dict.fromkeys(tables, 0)
        #: A ledger entry's line, waiting for its row's: a refused row leaves neither.
        self._entry: bytes | None = None

    def append(self, kind: str, row: Any) -> None:
        if kind not in self._tables:
            raise StoreError(
                f"run at {self._directory} has no table for kind {kind!r}. "
                f"Tables: {', '.join(self._tables)}",
                table=kind,
            )
        try:
            line = _encode(kind, row)
        except ContractError:
            if kind != LEDGER:
                self._entry = None  # the refused row's entry goes with it
            raise
        if kind == LEDGER and self._entry is None:
            self._entry = line
            return
        entry, self._entry = self._entry, None
        if entry is not None:
            self._write(LEDGER, entry)
        if kind == LEDGER:
            self._entry = line
        else:
            self._write(kind, line)

    def _write(self, kind: str, line: bytes) -> None:
        name = self._tables[kind]
        descriptor: int | None = None
        size = 0
        try:
            descriptor = self._open.get(kind)
            if descriptor is None:
                descriptor = self._open[kind] = os.open(
                    self._directory / name, os.O_WRONLY | os.O_APPEND
                )
            size = os.fstat(descriptor).st_size
            written = os.write(descriptor, line)
            if written != len(line):
                raise OSError(f"wrote {written} of {len(line)} bytes")
            if self._fsync:
                os.fsync(descriptor)
        except OSError as e:
            if descriptor is not None:  # take back what part of the line was written
                try:
                    os.ftruncate(descriptor, size)
                except OSError:
                    logger.exception("a cut-short line in %s could not be taken back", name)
            raise StoreError(
                f"could not append a row of {kind!r} to {self._directory / name}: "
                f"{type(e).__name__}: {e}",
                table=kind,
            ) from e
        self._rows[kind] += 1

    def events(self) -> _EventLog:
        return _EventLog(self._directory / EVENTS)

    def finish(self, run: Run) -> str:
        if self._entry is not None:  # an entry whose row never came: torn, as a kill leaves it
            entry, self._entry = self._entry, None
            self._write(LEDGER, entry)
        try:
            for descriptor in self._open.values():
                os.close(descriptor)
            self._open.clear()
            document = run.document() | {
                "tables": {
                    kind: {"file": name, "rows": self._rows[kind]}
                    for kind, name in self._tables.items()
                }
            }
            _replace(self._directory / "run.json", document, self._fsync)
            if self._fsync:
                _flush_directory(self._directory)
            os.close(self._lock)
        except OSError as e:
            raise StoreError(
                f"could not finish run {run.run_id!r} at {self._directory}: {type(e).__name__}: {e}"
            ) from e
        return str(self._directory)


def _replace(path: Path, document: dict[str, Any], fsync: bool) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as handle:
        handle.write(json.dumps(document, indent=2, default=str) + "\n")
        if fsync:
            handle.flush()
            os.fsync(handle.fileno())
    os.replace(tmp, path)


class JsonlConstructStore:
    def __init__(self, root: Path | str, *, fsync: bool = False) -> None:
        self._root = Path(root)
        self._fsync = fsync

    @classmethod
    def from_config(cls, config: JsonlStoreConfig) -> JsonlConstructStore:
        return cls(Path(config.root).expanduser(), fsync=config.fsync)

    def location(self, run_id: str) -> str:
        return str(self._dir(run_id))

    def _dir(self, run_id: str) -> Path:
        if _UNSAFE.search(run_id) or run_id in {"", ".", ".."}:
            raise ConfigError(f"run id {run_id!r} is not a safe directory name")
        return self._root / run_id

    def open(self, run: Run) -> _JsonlRunWriter:
        directory = self._dir(run.run_id)
        tables = {kind: _file(kind) for kind in run.construct.kinds()}
        lock: int | None = None
        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except (FileExistsError, NotADirectoryError) as e:
            raise StoreError(
                f"the store's root {self._root} is not a directory: {type(e).__name__}: {e}",
                fix="point the store's `root` at a directory, or remove the file in its way",
                run_id=run.run_id,
            ) from e
        except OSError as e:
            raise StoreError(
                f"could not make the store's root {self._root}: {type(e).__name__}: {e}",
                run_id=run.run_id,
            ) from e
        try:
            directory.mkdir()
        except FileExistsError:
            raise StoreError(
                f"run {run.run_id!r} already exists at {directory}; a run's record is never "
                "reopened or rewritten",
                fix="run under a new run id",
                run_id=run.run_id,
            ) from None
        except OSError as e:
            raise StoreError(
                f"could not open run {run.run_id!r} at {directory}: {type(e).__name__}: {e}",
                run_id=run.run_id,
            ) from e
        try:
            for name in tables.values():
                (directory / name).touch()
            lock = _hold(directory / _LOCK)
            document = run.document() | {
                "writer": {"pid": os.getpid(), "host": socket.gethostname()},
                "tables": {kind: {"file": name} for kind, name in tables.items()},
            }
            _replace(directory / "run.json", document, self._fsync)
            if self._fsync:
                _flush_directory(directory)
                _flush_directory(self._root)
        except OSError as e:
            if lock is not None:
                os.close(lock)
            raise StoreError(
                f"could not open run {run.run_id!r} at {directory}: {type(e).__name__}: {e}",
                run_id=run.run_id,
            ) from e
        return _JsonlRunWriter(directory, tables, self._fsync, lock)

    def _record(self, run_id: str) -> dict[str, Any]:
        path = self._dir(run_id) / "run.json"
        if not path.exists():
            raise NotFoundError(
                f"no run {run_id!r} in {self._root}. Runs: " + (", ".join(self.runs()) or "(none)"),
                run_id=run_id,
            )
        try:
            record: dict[str, Any] = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            raise StoreError(f"could not read {path}: {type(e).__name__}: {e}") from e
        return record

    def describe(self, run_id: str) -> dict[str, Any]:
        record = self._finished(run_id)
        if record.get("status") != "running":
            return record
        # Still running, here or on another host: say what is on disk now.
        _count_rows(record, self._dir(run_id))
        return record

    def _finished(self, run_id: str) -> dict[str, Any]:
        """The run's record, once it is finished: a run whose writer, on this host, is gone
        is finished here as ``interrupted`` (see the module docstring). A reader who cannot
        write the record reads it as it stands, ``interrupted``, and leaves it as it is."""
        record = self._record(run_id)
        writer = record.get("writer") or {}
        directory = self._dir(run_id)
        if (
            record.get("status") != "running"
            or writer.get("host") != socket.gethostname()
            or _writer_alive(directory / _LOCK)
        ):
            return record
        try:
            return self._finish_interrupted(run_id, directory)
        except OSError as e:
            logger.warning(
                "read the interrupted run %r at %s as it stands; its record could not be "
                "finished: %s: %s",
                run_id,
                directory,
                type(e).__name__,
                e,
            )
        record = self._record(run_id)
        if record.get("status") == "running":
            _count_rows(record, directory)
            record["status"] = "interrupted"
        return record

    def _finish_interrupted(self, run_id: str, directory: Path) -> dict[str, Any]:
        """Finish the record of a run whose writer is gone. Raises OSError when it cannot."""
        # The run's directory is locked, not the writer's file, so a reader checking for a
        # live writer meanwhile is not misled; it waits here for this one instead.
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            record = self._record(run_id)
            if record.get("status") != "running":
                return record  # another reader finished it first
            for info in record["tables"].values():
                info["rows"] = _cut_torn_tail(directory / info["file"])
            _cut_torn_tail(directory / EVENTS)
            _count_torn(record)
            record["status"] = "interrupted"
            _replace(directory / "run.json", record, self._fsync)
            if self._fsync:
                _flush_directory(directory)
        finally:
            os.close(descriptor)  # closing releases the lock
        return record

    def load(self, run_id: str) -> Construct:
        record = self._finished(run_id)
        directory = self._dir(run_id)
        try:
            tables = {
                kind: [json.loads(line) for line in _lines(directory / info["file"])]
                for kind, info in record["tables"].items()
            }
        except (OSError, ValueError) as e:
            raise StoreError(
                f"could not read run {run_id!r} at {directory}: {type(e).__name__}: {e}",
                run_id=run_id,
            ) from e
        return Construct.restore(tables)

    def runs(self) -> list[str]:
        """The runs under the root, oldest first. A directory whose ``run.json`` cannot be
        read, or whose name is not a run id, is skipped, and logged on ``matrix.store``."""
        if not self._root.is_dir():
            return []
        started: dict[str, str] = {}
        for path in self._root.iterdir():
            if not (path / "run.json").exists():
                continue
            try:
                started[path.name] = str(self._record(path.name).get("started", ""))
            except (StoreError, ConfigError) as e:
                logger.warning("skipped %s, whose record is damaged: %s", path, e)
        return sorted(started, key=lambda name: (started[name], name))


def _count_rows(record: dict[str, Any], directory: Path) -> None:
    """Each table's rows as its whole lines on disk now, and ``torn`` from them."""
    for info in record["tables"].values():
        info["rows"] = len(_lines(directory / info["file"]))
    _count_torn(record)


def _count_torn(record: dict[str, Any]) -> None:
    """``torn``: the ledger entries whose row never landed, from the tables' row counts."""
    tables = record["tables"]
    landed = sum(info["rows"] for kind, info in tables.items() if kind != LEDGER)
    record["torn"] = max(0, tables[LEDGER]["rows"] - landed)


class _MemoryRunWriter:
    def __init__(self, record: dict[str, Any]) -> None:
        self._record = record

    def append(self, kind: str, row: Any) -> None:
        tables: dict[str, list[Any]] = self._record["tables"]
        if kind not in tables:
            raise StoreError(f"the run has no table for kind {kind!r}", table=kind)
        tables[kind].append(row)

    def finish(self, run: Run) -> str:
        self._record["document"] = run.document()
        return run.run_id

    def events(self) -> None:
        return None


class MemoryConstructStore:
    """Runs kept in a dict, rows as given. For tests and single-process callers."""

    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}

    def open(self, run: Run) -> _MemoryRunWriter:
        if run.run_id in self._runs:
            raise StoreError(
                f"run {run.run_id!r} already exists; a run's record is never reopened or rewritten",
                fix="run under a new run id",
                run_id=run.run_id,
            )
        record = self._runs[run.run_id] = {
            "document": run.document(),
            "tables": {kind: [] for kind in run.construct.kinds()},
        }
        return _MemoryRunWriter(record)

    def _get(self, run_id: str) -> dict[str, Any]:
        record = self._runs.get(run_id)
        if record is None:
            raise NotFoundError(
                f"no run {run_id!r}. Runs: {', '.join(self._runs) or '(none)'}",
                run_id=run_id,
            )
        return record

    def load(self, run_id: str) -> Construct:
        return Construct.restore(self._get(run_id)["tables"])

    def location(self, run_id: str) -> str:
        return run_id

    def describe(self, run_id: str) -> dict[str, Any]:
        record = self._get(run_id)
        tables = {kind: {"rows": len(rows)} for kind, rows in record["tables"].items()}
        return dict(record["document"]) | {"tables": tables}

    def runs(self) -> list[str]:
        return list(self._runs)

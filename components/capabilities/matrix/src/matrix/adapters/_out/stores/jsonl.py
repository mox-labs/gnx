"""A directory of runs, each a set of typed JSONL tables written as the run goes.

    <root>/<run_id>/run.json                 {"schema": "matrix.v1.run", run_id, status, ...,
                                               "tables": {kind: {"file", "rows"}}}
    <root>/<run_id>/<kind>.jsonl             one row per line: the typed record itself
    <root>/<run_id>/matrix.v1.ledger.jsonl   one line per row landed: seq, kind, row,
                                               component, time

One kind per file, and the kind is its schema, so ``duckdb``'s ``read_json_auto`` or ``jq``
reads a table without matrix.

**While a run is going** ``run.json`` says ``running`` and every table file exists. Each row
is one ``write`` of one whole line to a file opened for appending, made before ``append``
returns: a process killed at any moment leaves every row it had appended. (Pass
``fsync=True`` to also survive the machine going down, at the cost of a disk flush per row.)
A line that was cut short is the last line of its file and has no newline; readers ignore it.

**When a run ends** ``run.json`` is replaced in one rename with the final document. A run
that never got that far still reads ``running``; ``describe`` says ``interrupted`` instead
when the process that was writing it, on this host, is gone.
"""

from __future__ import annotations

import json
import os
import re
import socket
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from matrix.domain.component import LEDGER
from matrix.domain.construct import Construct
from matrix.domain.errors import ConfigError, NotFoundError, StoreError

if TYPE_CHECKING:
    from matrix.domain.executor import Run

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _json(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_json(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _file(kind: str) -> str:
    return f"{_UNSAFE.sub('_', kind)}.jsonl"


def _lines(path: Path) -> list[bytes]:
    """The complete lines of a table file; a cut-short last line is not one."""
    if not path.exists():
        return []
    return path.read_bytes().split(b"\n")[:-1]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # it exists, and is someone else's
    return True


class _JsonlRunWriter:
    def __init__(self, directory: Path, tables: dict[str, str], fsync: bool) -> None:
        self._directory = directory
        self._tables = tables
        self._fsync = fsync
        self._open: dict[str, int] = {}
        self._rows: dict[str, int] = dict.fromkeys(tables, 0)

    def append(self, kind: str, row: Any) -> None:
        name = self._tables.get(kind)
        if name is None:
            raise StoreError(
                f"run at {self._directory} has no table for kind {kind!r}. "
                f"Tables: {', '.join(self._tables)}",
                table=kind,
            )
        try:
            line = json.dumps(_json(row), default=str).encode() + b"\n"
            descriptor = self._open.get(kind)
            if descriptor is None:
                descriptor = self._open[kind] = os.open(
                    self._directory / name, os.O_WRONLY | os.O_APPEND
                )
            written = os.write(descriptor, line)
            if written != len(line):
                raise OSError(f"wrote {written} of {len(line)} bytes")
            if self._fsync:
                os.fsync(descriptor)
        except (OSError, TypeError, ValueError) as e:
            raise StoreError(
                f"could not append a row of {kind!r} to {self._directory / name}: "
                f"{type(e).__name__}: {e}",
                table=kind,
            ) from e
        self._rows[kind] += 1

    def finish(self, run: Run) -> str:
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

    def _dir(self, run_id: str) -> Path:
        if _UNSAFE.search(run_id) or run_id in {"", ".", ".."}:
            raise ConfigError(f"run id {run_id!r} is not a safe directory name")
        return self._root / run_id

    def open(self, run: Run) -> _JsonlRunWriter:
        directory = self._dir(run.run_id)
        tables = {kind: _file(kind) for kind in run.construct.kinds()}
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            directory.mkdir()
            for name in tables.values():
                (directory / name).touch()
            document = run.document() | {
                "writer": {"pid": os.getpid(), "host": socket.gethostname()},
                "tables": {kind: {"file": name} for kind, name in tables.items()},
            }
            _replace(directory / "run.json", document, self._fsync)
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
        return _JsonlRunWriter(directory, tables, self._fsync)

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
        record = self._record(run_id)
        if record.get("status") != "running":
            return record
        # Never finished: say what is on disk now, and whether anyone is still writing it.
        directory = self._dir(run_id)
        for info in record["tables"].values():
            info["rows"] = len(_lines(directory / info["file"]))
        landed = sum(i["rows"] for kind, i in record["tables"].items() if kind != LEDGER)
        record["torn"] = max(0, record["tables"][LEDGER]["rows"] - landed)
        writer = record.get("writer") or {}
        if writer.get("host") == socket.gethostname() and not _alive(int(writer.get("pid", 0))):
            record["status"] = "interrupted"
        return record

    def load(self, run_id: str) -> Construct:
        record = self._record(run_id)
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
        if not self._root.is_dir():
            return []
        started: dict[str, str] = {}
        for path in self._root.iterdir():
            if (path / "run.json").exists():
                started[path.name] = str(self._record(path.name).get("started", ""))
        return sorted(started, key=lambda name: (started[name], name))


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

    def describe(self, run_id: str) -> dict[str, Any]:
        record = self._get(run_id)
        tables = {kind: {"rows": len(rows)} for kind, rows in record["tables"].items()}
        return dict(record["document"]) | {"tables": tables}

    def runs(self) -> list[str]:
        return list(self._runs)

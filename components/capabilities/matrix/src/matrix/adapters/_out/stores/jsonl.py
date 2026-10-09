"""A directory of runs, each a set of typed JSONL tables.

    <root>/<run_id>/run.json          {"schema": "matrix.v1.run", run_id, flow, status, ...,
                                       "tables": {topic: {"type_url", "rows", "file"}}}
    <root>/<run_id>/<topic>.jsonl     one artifact per line: id, producer, port, episode,
                                       timestamp, value

One schema per file (a topic carries one type), so ``duckdb``'s ``read_json_auto`` or ``jq``
reads a table without matrix. Files are written to a temporary name and renamed, so a
crashed save never leaves a half-written table behind.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from matrix.domain.construct import Artifact, Construct
from matrix.domain.errors import ConfigError, NotFoundError

if TYPE_CHECKING:
    from matrix.domain.executor import Run

RUN_SCHEMA = "matrix.v1.run"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _json(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_json(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


class JsonlConstructStore:
    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    def _dir(self, run_id: str) -> Path:
        if _UNSAFE.search(run_id) or run_id in {"", ".", ".."}:
            raise ConfigError(f"run id {run_id!r} is not a safe directory name")
        return self._root / run_id

    def save(self, run: Run) -> str:
        directory = self._dir(run.run_id)
        if directory.exists():
            raise ConfigError(
                f"run {run.run_id!r} is already saved at {directory}; runs are never rewritten",
                fix="save the run under a new run id",
            )
        directory.mkdir(parents=True)
        tables: dict[str, dict[str, Any]] = {}
        seq = {a.id: i for i, a in enumerate(run.construct.ledger)}
        used: set[str] = set()
        for topic, type_url in run.construct.topics().items():
            rows = run.construct.rows(topic)
            stem = _UNSAFE.sub("_", topic)
            name, n = f"{stem}.jsonl", 1
            while name in used or name == "run.json":
                n += 1
                name = f"{stem}-{n}.jsonl"
            used.add(name)
            lines = (
                json.dumps(
                    {
                        "seq": seq[a.id],
                        "id": a.id,
                        "producer": a.producer,
                        "port": a.port,
                        "episode": a.episode,
                        "timestamp": a.timestamp.isoformat(),
                        "value": _json(a.value),
                    },
                    default=str,
                )
                for a in rows
            )
            _write(directory / name, "".join(f"{line}\n" for line in lines))
            tables[topic] = {"type_url": type_url, "rows": len(rows), "file": name}
        record = {
            "schema": RUN_SCHEMA,
            "run_id": run.run_id,
            "flow": run.flow,
            "status": run.status,
            "episode": run.episode,
            "started": run.started.isoformat(),
            "ended": run.ended.isoformat() if run.ended else None,
            "failures": run.failures,
            "tables": tables,
        }
        _write(directory / "run.json", json.dumps(record, indent=2, default=str) + "\n")
        return str(directory)

    def describe(self, run_id: str) -> dict[str, Any]:
        path = self._dir(run_id) / "run.json"
        if not path.exists():
            raise NotFoundError(
                f"no saved run {run_id!r} in {self._root}. Runs: "
                + (", ".join(self.runs()) or "(none)"),
                run_id=run_id,
            )
        record: dict[str, Any] = json.loads(path.read_text())
        return record

    def load(self, run_id: str) -> Construct:
        record = self.describe(run_id)
        construct = Construct({t: info["type_url"] for t, info in record["tables"].items()})
        rows: list[tuple[int, Artifact]] = []
        for topic, info in record["tables"].items():
            for line in (self._dir(run_id) / info["file"]).read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                rows.append(
                    (
                        row.get("seq", len(rows)),
                        Artifact(
                            id=row["id"],
                            run_id=run_id,
                            topic=topic,
                            type_url=info["type_url"],
                            producer=row["producer"],
                            port=row["port"],
                            value=row["value"],
                            episode=row.get("episode", 0),
                            timestamp=datetime.fromisoformat(row["timestamp"]),
                        ),
                    )
                )
        for _, artifact in sorted(rows, key=lambda pair: pair[0]):
            construct.append(artifact)
        return construct

    def runs(self) -> list[str]:
        if not self._root.is_dir():
            return []
        found = [p for p in self._root.iterdir() if (p / "run.json").exists()]
        return [p.name for p in sorted(found, key=lambda p: (p / "run.json").stat().st_mtime)]


class MemoryConstructStore:
    """Runs kept in a dict, values as given. For tests and single-process callers."""

    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}

    def save(self, run: Run) -> str:
        if run.run_id in self._runs:
            raise ConfigError(f"run {run.run_id!r} is already saved; runs are never rewritten")
        self._runs[run.run_id] = run
        return run.run_id

    def _get(self, run_id: str) -> Run:
        run = self._runs.get(run_id)
        if run is None:
            raise NotFoundError(
                f"no saved run {run_id!r}. Runs: {', '.join(self._runs) or '(none)'}",
                run_id=run_id,
            )
        return run

    def load(self, run_id: str) -> Construct:
        return self._get(run_id).construct

    def describe(self, run_id: str) -> dict[str, Any]:
        run = self._get(run_id)
        return {
            "schema": RUN_SCHEMA,
            "run_id": run.run_id,
            "flow": run.flow,
            "status": run.status,
            "failures": run.failures,
            "tables": {
                t: {"type_url": u, "rows": len(run.construct.rows(t))}
                for t, u in run.construct.topics().items()
            },
        }

    def runs(self) -> list[str]:
        return list(self._runs)

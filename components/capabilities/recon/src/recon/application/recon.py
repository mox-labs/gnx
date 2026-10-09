"""Recon — the dispatch loop (reactor).

Iterates collector entries, fans out across catalog sources, composes the
collect → shape → sink pipeline per target. Collectors return iterators;
sink streams records to JSONL line-by-line. Generator cleanup is guaranteed
via contextlib.closing — consumer-abandoned collectors always release
their resources.

The archive it writes (meta.yaml ``format_version: 2``)::

    archive/<timestamp>/
      <output>.jsonl          one table per collector × source, records as normalized
      meta.yaml               recon version, config sha256, and per table: type_url,
                              records, status, seconds, error {kind, message}
      .incomplete             present when any table failed
      captures.jsonl, raw/    the capture log, when preserve_raw is on

No direct adapter imports — hex arch clean.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable, Generator, Iterator, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import yaml

from recon import __version__
from recon.application.captures import FilesystemCaptureLog
from recon.application.plan import fan_out
from recon.domain.exceptions import CollectionError, ErrorKind, ReconError

if TYPE_CHECKING:
    from pathlib import Path

    from recon.domain.capture import CaptureLog
    from recon.domain.collector import Collector
    from recon.domain.models import CollectorEntry, ReconConfig, SourceEntry

EventCallback = Callable[[dict[str, Any]], None]

META_FORMAT_VERSION = 2


class CollectorError(CollectionError):
    """Wraps a collector's raised exception with the entry + source it came from.

    Raised at the run_collector boundary so downstream code (sink, dispatch)
    knows which collector failed without having to unpack the cause itself.
    The cause's ``kind`` carries through, so a 429 stays ``transient``.
    """

    def __init__(self, collector_name: str, source_name: str | None, cause: BaseException):
        self.collector_name = collector_name
        self.source_name = source_name
        self.cause = cause
        loc = f"{collector_name}" + (f" against '{source_name}'" if source_name else "")
        kind: ErrorKind = cause.kind if isinstance(cause, ReconError) else "unknown"
        super().__init__(f"[{loc}] {cause}", kind=kind)


def failure_kind(results: list[dict[str, Any]]) -> ErrorKind | None:
    """Classify a survey by its failed tables; None when every table succeeded.

    ``auth`` if any table failed on credentials (a new key fixes it, a retry does not);
    else ``transient`` if every failure was transient (a retry may fix all of them);
    else ``collection``.
    """
    kinds = [r["error"]["kind"] for r in results if r.get("status") == "error"]
    if not kinds:
        return None
    if "auth" in kinds:
        return "auth"
    if all(k == "transient" for k in kinds):
        return "transient"
    return "collection"


def _tag_errors(
    collector: Collector,
    entry: CollectorEntry,
    source: SourceEntry | None,
    captures: CaptureLog | None = None,
) -> Iterator[dict[str, Any]]:
    """Wrap a collector's iterator so any exception it raises (eagerly or
    mid-stream) surfaces as CollectorError with entry + source attribution.

    Without this, an exception raised deep inside `collector.collect()` would
    reach sink() with no information about *which* collector produced it.
    """
    source_name = source.name if source else None
    try:
        yield from collector.collect(entry, source, captures=captures)
    except CollectorError:
        raise  # already tagged
    except Exception as exc:
        raise CollectorError(entry.name, source_name, exc) from exc


def _sink(
    records: Generator[dict[str, Any], None, None], path: Path, *, fsync_every: int = 1000
) -> int:
    """Stream records to JSONL atomically; return the number written.

    Writes to <path>.tmp line-by-line; fsyncs every `fsync_every` records so
    durability loss on crash is bounded. On any error the partial .tmp is
    removed and the exception propagates. On success, atomic rename to final.

    Guarantees generator cleanup via contextlib.closing — if an exception
    interrupts iteration, the upstream collector's try/finally still runs
    (subprocess kill, httpx client close, rate-limit token release).
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    count = 0
    try:
        # closing() is load-bearing, not decoration — see the docstring. The parameter is
        # typed Generator (not Iterator) precisely so this type-checks: `_sink` genuinely
        # requires something it can close, and the signature now says so. An earlier pass
        # deleted this to silence a mypy type-var error, which silently dropped subprocess
        # kill / httpx close / rate-limit release on an interrupted iteration.
        with contextlib.closing(records), open(tmp, "w") as f:
            for record in records:
                f.write(json.dumps(record, default=str) + "\n")
                count += 1
                if count % fsync_every == 0:
                    f.flush()
                    os.fsync(f.fileno())
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return count


def _with_sentinel(r: Iterator[dict[str, Any]]) -> Generator[dict[str, Any], None, None]:
    """Empty-collector sentinel: a collector that yields nothing still produces one
    ``{"_empty": true}`` record, so DuckDB's read_json_auto has a file to read."""
    any_yielded = False
    for record in r:
        any_yielded = True
        yield record
    if not any_yielded:
        yield {"_empty": True}


def _check_collisions(config: ReconConfig) -> None:
    catalog = {s.name: s for s in config.catalog}
    seen: set[str] = set()
    collisions: set[str] = set()
    for entry in config.collectors:
        for _source, name in fan_out(entry, catalog):
            if name in seen:
                collisions.add(name)
            seen.add(name)
    if collisions:
        msg = (
            f"Output name collision: {', '.join(sorted(collisions))}. "
            "Two collectors would write to the same file. "
            "Rename one of the colliding collectors so each produces a unique JSONL."
        )
        raise ReconError(msg, kind="config")


def run(
    config: ReconConfig,
    collectors: Mapping[str, Collector],
    mission_dir: Path,
    on_event: EventCallback | None = None,
    *,
    config_sha256: str | None = None,
) -> tuple[Path, list[dict[str, Any]]]:
    """Execute all collectors, produce an archive directory.

    Fan-out: if a collector has no source, it runs against every catalog entry.
    If a collector names a source, it runs against that source only.
    If there's no catalog, collectors run once with no source.

    Outcome events are reported via `on_event` if provided. Each event is a
    dict with at least a `kind` field: "ok", "error", or "archive_warning".
    The caller formats output; this function does no console I/O.

    Returns (archive_dir, results) where results is the per-table list that also lands
    in meta.yaml under ``tables``. A failed table has ``error: {kind, message}``.
    """
    _check_collisions(config)

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S-%f")
    archive_dir = mission_dir / "archive" / timestamp
    archive_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)

    captures: CaptureLog | None = FilesystemCaptureLog(archive_dir) if config.preserve_raw else None
    catalog: dict[str, SourceEntry] = {s.name: s for s in config.catalog}

    def emit(event: dict[str, Any]) -> None:
        if on_event is not None:
            on_event(event)

    results: list[dict[str, Any]] = []

    def failed(base: dict[str, Any], kind: ErrorKind, message: str, seconds: float) -> None:
        results.append(
            {
                **base,
                "status": "error",
                "records": 0,
                "seconds": round(seconds, 2),
                "error": {"kind": kind, "message": message},
            }
        )
        emit({"kind": "error", "name": base["name"], "reason": message, "error_kind": kind})

    for entry in config.collectors:
        targets = fan_out(entry, catalog)
        if entry.source and not targets:
            base = _table(entry, entry.name, entry.source)
            available = ", ".join(sorted(catalog)) or "(none)"
            failed(
                base, "config", f"Source '{entry.source}' not in catalog. Available: {available}", 0
            )
            continue

        collector = collectors.get(entry.type)
        for source, output_name in targets:
            base = _table(entry, output_name, source.name if source else None)
            if collector is None:
                failed(base, "config", f"No collector registered for type: {entry.type}", 0)
                continue
            start = datetime.now(UTC)
            try:
                # Fan-out renames the entry so each target's captures and output share
                # the output name.
                per_target_entry = (
                    entry
                    if output_name == entry.name
                    else entry.model_copy(update={"name": output_name})
                )
                records = _tag_errors(collector, per_target_entry, source, captures=captures)
                count = _sink(_with_sentinel(records), archive_dir / f"{output_name}.jsonl")
            except Exception as exc:
                kind: ErrorKind = exc.kind if isinstance(exc, ReconError) else "unknown"
                failed(base, kind, str(exc), (datetime.now(UTC) - start).total_seconds())
                continue
            elapsed = (datetime.now(UTC) - start).total_seconds()
            results.append({**base, "status": "ok", "records": count, "seconds": round(elapsed, 2)})
            emit({"kind": "ok", "name": output_name, "records": count})

    # If any collector errored, leave a sentinel so `status` and `query` know
    # this archive is partial.
    if any(r["status"] == "error" for r in results):
        try:
            (archive_dir / ".incomplete").write_text("")
        except OSError as exc:
            emit({"kind": "archive_warning", "reason": f".incomplete write failed: {exc}"})

    meta = {
        "format_version": META_FORMAT_VERSION,
        "recon_version": __version__,
        "config_sha256": config_sha256,
        "started_at": started.isoformat(),
        "timestamp": datetime.now(UTC).isoformat(),
        "preserve_raw": config.preserve_raw,
        "tables": results,
    }
    meta_path = archive_dir / "meta.yaml"
    meta_tmp = meta_path.with_suffix(".yaml.tmp")
    try:
        meta_tmp.write_text(yaml.safe_dump(meta, default_flow_style=False, sort_keys=False))
        meta_tmp.replace(meta_path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            meta_tmp.unlink()
        with contextlib.suppress(OSError):
            (archive_dir / ".incomplete").write_text("")
        emit(
            {
                "kind": "archive_warning",
                "reason": f"meta.yaml write failed: {exc}",
                "results": results,
            }
        )

    return archive_dir, results


def _table(entry: CollectorEntry, output: str, source: str | None) -> dict[str, Any]:
    return {
        "name": output,
        "file": f"{output}.jsonl",
        "collector": entry.name,
        "type": entry.type,
        "source": source,
        "type_url": entry.type_url,
    }

"""The survey: run every planned collector against its sources and write one archive.

Determinism (the contract the tests in ``test_determinism.py`` hold):

    Given the same mission config, the same answers from the network (the ``Requester``
    port), the same output from shell commands (``ShellRunner``), the same documents from
    the converter (``DocumentConverter``) and the same files under the mission's declared
    directories, a survey writes the same records, in the same order, to the same tables.
    The time enters only through the ``Clock`` port. With the clock fixed too, the whole
    archive is the same: its name, every table, and ``meta.yaml``. The one exception is the
    capture log a mission with ``preserve_raw`` keeps: each capture has a unique ``id``.

Tables run one at a time, in config order, each collector against each of its sources in
catalog order. A table that fails is recorded with its error and the rest still run. The
archive is created incomplete and marked complete only after ``meta.yaml`` is written with
every table succeeded, so a survey that fails a table, is interrupted or is killed leaves an
archive that reads incomplete. A survey that cannot write ``meta.yaml`` or mark the archive
complete is not ``finished``, and says why in an ``archive_unfinished`` warning. Each table
is a ``recon.collect`` span.

A table of a type that reads ``extract:`` (``CollectorType.extracts``) that gets exactly one
record holding a list of objects, with neither ``extract:`` nor ``normalize:`` shaping it,
is most likely the whole response read as one record (arXiv's feed without ``extract:
feed.entry``): the survey keeps the table and warns, naming the list's path. A table with an
``extract:`` is not warned about its lists: its one record is a record, and they are its own.

A collector that finds nothing to keep for a reason it can name raises ``NoRecordsError``
before its first record (an ``extract:`` that resolves to nothing in a response that is not
empty, items that are not objects, captures no filter kept): the survey keeps the table,
empty, and warns with the reason, so an empty table never reads as a source that had
nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Generator, Mapping
from typing import TYPE_CHECKING, Any

from recon.application.plan import fan_out
from recon.domain.archives import META_FORMAT_VERSION
from recon.domain.documents import TableError, TableOutcome
from recon.domain.errors import CollectionError, ErrorKind, NoRecordsError, Problem, ReconError
from recon.domain.normalize import nested_records
from recon.domain.telemetry import COLLECT

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from recon.domain.archives import ArchiveStore
    from recon.domain.capture import CaptureLog
    from recon.domain.clock import Clock
    from recon.domain.collector import Collector
    from recon.domain.models import CollectorEntry, ReconConfig, SourceEntry
    from recon.domain.telemetry import Span, Telemetry

#: Receives each table's outcome as it lands: ``{"kind": "ok" | "error" | "table_warning" |
#: "archive_warning", ...}``. The survey prints nothing itself.
EventCallback = Callable[[dict[str, Any]], None]

#: An archive's name: when the survey started, to the microsecond, so names sort by time.
ARCHIVE_ID_FORMAT = "%Y-%m-%d-%H%M%S-%f"


class CollectorError(CollectionError):
    """A collector's exception, tagged with the entry and source it came from.

    The cause's ``kind`` and ``fix`` carry through, so a 429 stays ``transient``.
    """

    def __init__(self, collector_name: str, source_name: str | None, cause: BaseException):
        self.collector_name = collector_name
        self.source_name = source_name
        self.cause = cause
        where = collector_name + (f" against '{source_name}'" if source_name else "")
        kind: ErrorKind = cause.kind if isinstance(cause, ReconError) else "unknown"
        fix = cause.fix if isinstance(cause, ReconError) else None
        super().__init__(f"[{where}] {cause}", kind=kind, fix=fix)


class Survey:
    """One survey's run: the ports it writes through and the tables it has recorded.

    ``extracts`` names the collector types that read ``extract:``; ``warnings`` collects
    what the run found worth saying about tables that succeeded, and about an archive it
    could not finish. ``finished`` is false when ``meta.yaml`` could not be written or the
    archive could not be marked complete, so it reads incomplete whatever its tables did."""

    def __init__(
        self,
        archives: ArchiveStore,
        clock: Clock,
        recon_version: str,
        telemetry: Telemetry,
        on_event: EventCallback | None = None,
        *,
        extracts: Collection[str] = (),
    ) -> None:
        self._archives = archives
        self._clock = clock
        self._recon_version = recon_version
        self._telemetry = telemetry
        self._on_event = on_event
        self._extracts = frozenset(extracts)
        self.warnings: list[Problem] = []
        self.finished = True

    def run(
        self,
        config: ReconConfig,
        collectors: Mapping[str, Collector],
        archive: Path,
        *,
        started: datetime,
        config_sha256: str | None = None,
    ) -> list[TableOutcome]:
        """Run every table into ``archive``, a new one :meth:`ArchiveStore.create` made at
        ``started``, and write its ``meta.yaml``. Returns the tables, which also land in
        ``meta.yaml`` under ``tables``."""
        captures = self._archives.capture_log(archive) if config.preserve_raw else None
        tables: list[TableOutcome] = []
        for index, entry in enumerate(config.collectors):
            tables += self._run_entry(index, entry, config, collectors, archive, captures)
        meta = {
            "format_version": META_FORMAT_VERSION,
            "recon_version": self._recon_version,
            "config_sha256": config_sha256,
            "started_at": started.isoformat(),
            "timestamp": self._clock.now().isoformat(),
            "preserve_raw": config.preserve_raw,
            "tables": [table.document() for table in tables],
        }
        try:
            self._archives.write_meta(archive, meta)
            if all(table.status == "ok" for table in tables):
                self._archives.mark_complete(archive)
        except OSError as exc:
            self.finished = False
            warning = Problem(
                "",
                f"the archive could not be finished, so it reads incomplete: {exc}",
                kind="archive_unfinished",
                fix="free space in the archive's directory, or make it writable, then survey again",
            )
            self.warnings.append(warning)
            self._emit({"kind": "archive_warning", "reason": warning.message, "fix": warning.fix})
        return tables

    def _run_entry(
        self,
        index: int,
        entry: CollectorEntry,
        config: ReconConfig,
        collectors: Mapping[str, Collector],
        archive: Path,
        captures: CaptureLog | None,
    ) -> list[TableOutcome]:
        catalog = {s.name: s for s in config.catalog}
        targets = fan_out(entry, catalog)
        if entry.source and not targets:
            available = ", ".join(sorted(catalog)) or "(none)"
            message = f"Source '{entry.source}' not in catalog. Available: {available}"
            table = _table(entry, entry.name, entry.source)
            with self._telemetry.span(COLLECT, _attributes(table)) as span:
                return [self._failed(span, table, "config", message, 0)]
        collector = collectors.get(entry.type)
        tables = []
        for source, output in targets:
            table = _table(entry, output, source.name if source else None)
            with self._telemetry.span(COLLECT, _attributes(table)) as span:
                if collector is None:
                    message = f"No collector registered for type: {entry.type}"
                    tables.append(self._failed(span, table, "config", message, 0))
                    continue
                outcome = self._run_table(
                    span, table, collector, (index, entry), source, archive, captures
                )
                tables.append(outcome)
        return tables

    def _run_table(
        self,
        span: Span,
        table: TableOutcome,
        collector: Collector,
        at: tuple[int, CollectorEntry],
        source: SourceEntry | None,
        archive: Path,
        captures: CaptureLog | None,
    ) -> TableOutcome:
        index, entry = at
        output = table.name
        # Each fan-out target is its own entry, named for its output, so its captures and
        # its table share the name.
        target = entry if output == entry.name else entry.model_copy(update={"name": output})
        start = self._clock.monotonic()
        first: list[dict[str, Any]] = []
        nothing: NoRecordsError | None = None
        try:
            records = _keeping_first(_tagged(collector, target, source, captures), first)
            try:
                count = self._archives.write_table(archive, output, records)
            except NoRecordsError as said:
                if first:  # records came before it: an empty table would drop them
                    raise
                nothing = said
                count = self._archives.write_table(archive, output, _no_records())
        except Exception as exc:
            kind: ErrorKind = exc.kind if isinstance(exc, ReconError) else "unknown"
            fix = exc.fix if isinstance(exc, ReconError) else None
            seconds = self._clock.monotonic() - start
            return self._failed(span, table, kind, str(exc), seconds, fix)
        seconds = round(self._clock.monotonic() - start, 2)
        span.set("recon.records", count)
        self._emit({"kind": "ok", "name": output, "records": count})
        if nothing is not None:
            self._warn(
                output,
                Problem(
                    f"collectors.{index}.{nothing.field}",
                    f"{output}: {nothing.message}",
                    kind=nothing.problem_kind,
                    fix=nothing.fix or "",
                ),
            )
        if count == 1:
            self._check_extract(index, entry, output, first[0])
        return table.model_copy(update={"records": count, "seconds": seconds})

    def _check_extract(
        self, index: int, entry: CollectorEntry, output: str, record: dict[str, Any]
    ) -> None:
        # With an extract, the one record is one of the records the extract names, and a
        # list inside it is that record's own (a paper's authors), not the response's.
        if entry.type not in self._extracts or entry.normalize or entry.extract:
            return
        found = nested_records(record)
        if found is None:
            return
        path, count = found
        warning = Problem(
            f"collectors.{index}.extract",
            f"{output} got one record, which holds a list of {count} objects at "
            f"{path}: if those are the records, extract: is missing and the table should "
            f"have {count} rows",
            kind="extract_missing",
            fix=f"set extract: {path} on collector {entry.name!r}, then survey again",
        )
        self._warn(output, warning)

    def _warn(self, output: str, warning: Problem) -> None:
        self.warnings.append(warning)
        self._emit({"kind": "table_warning", "name": output, "reason": str(warning),
                    "fix": warning.fix})  # fmt: skip

    def _failed(
        self,
        span: Span,
        table: TableOutcome,
        kind: ErrorKind,
        message: str,
        seconds: float,
        fix: str | None = None,
    ) -> TableOutcome:
        # The message stays in meta.yaml: it can quote a command or a URL, and a span goes
        # wherever telemetry is sent.
        span.fail(kind, f"the table failed ({kind}); its error is in the archive's meta.yaml")
        self._emit(
            {
                "kind": "error",
                "name": table.name,
                "reason": message,
                "error_kind": kind,
                "fix": fix,
            }
        )
        return table.model_copy(
            update={
                "status": "error",
                "seconds": round(seconds, 2),
                "error": TableError(kind=kind, message=message, fix=fix),
            }
        )

    def _emit(self, event: dict[str, Any]) -> None:
        if self._on_event is not None:
            self._on_event(event)


def _tagged(
    collector: Collector,
    entry: CollectorEntry,
    source: SourceEntry | None,
    captures: CaptureLog | None,
) -> Generator[dict[str, Any], None, None]:
    """The collector's records; any exception it raises, eagerly or mid-stream, comes out
    as a CollectorError naming the entry and source."""
    try:
        yield from collector.collect(entry, source, captures=captures)
    except (CollectorError, NoRecordsError):
        raise
    except Exception as exc:
        raise CollectorError(entry.name, source.name if source else None, exc) from exc


def _no_records() -> Generator[dict[str, Any], None, None]:
    yield from ()


def _keeping_first(
    records: Generator[dict[str, Any], None, None], kept: list[dict[str, Any]]
) -> Generator[dict[str, Any], None, None]:
    """``records``, with the first appended to ``kept``. Closing this closes ``records``, so
    the collector's cleanup still runs when writing stops early."""
    try:
        for record in records:
            if not kept:
                kept.append(record)
            yield record
    finally:
        records.close()


def _attributes(table: TableOutcome) -> dict[str, str | None]:
    return {
        "recon.table": table.name,
        "recon.collector": table.collector,
        "recon.collector.type": table.type,
        "recon.source": table.source,
    }


def _table(entry: CollectorEntry, output: str, source: str | None) -> TableOutcome:
    return TableOutcome(
        name=output,
        file=f"{output}.jsonl",
        collector=entry.name,
        type=entry.type,
        source=source,
        type_url=entry.type_url,
    )

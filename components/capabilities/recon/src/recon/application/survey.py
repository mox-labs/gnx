"""The survey: run every planned collector against its sources and write one archive.

Determinism (the contract the tests in ``test_determinism.py`` hold):

    Given the same mission config, the same answers from the network (the ``Requester``
    port), the same output from shell commands (``ShellRunner``), the same documents from
    the converter (``DocumentConverter``) and the same files under the mission's declared
    directories, a survey writes the same records, in the same order, to the same tables.
    The time enters only through the ``Clock`` port. With the clock fixed too, the whole
    archive is the same: its name, every table, and ``meta.yaml``. The one exception is the
    capture log a mission with ``preserve_raw`` keeps: each capture has a unique ``id``.
    An entry with ``changes:`` adds its baseline (the earlier archive its tables are compared
    with) and the mission's change key to the inputs: the same baseline, tables, spec and key
    give the same change tables and key indexes.

    A stream's window keeps the same contract, window by window (``stream.py``): given the
    same records arriving at the same stamps on the clock port, each window holds the same
    records, in the same order, with the same ``meta.yaml`` and event log, whatever thread
    wrote it. Which window a record falls in is a pure function of its order of arrival, its
    stamp and the window spec, and a window kept with ``preserve_raw`` replays from its
    capture to the same table under any clock. What a full buffer drops depends on how fast
    the windows are written, so a window that dropped records says it cannot be replayed.

Tables run one at a time, in config order, each collector against each of its sources in
catalog order. A table that fails is recorded with its error and the rest still run. A
mission's ``deadline:`` bounds them all, and a collector's each of its tables
(:mod:`recon.domain.deadline`): a table cut short fails as ``transient``, and so does each
table not started when the survey's deadline has passed; the archive is still finished. The
archive is created incomplete and marked complete only after ``meta.yaml`` is written with
every table succeeded, so a survey that fails a table, is stopped or is killed leaves an
archive that reads incomplete. ``meta.yaml`` says how the survey ended (``ended``):
``finished``, or ``stopped`` when it was stopped on request (a ``KeyboardInterrupt``, which
the command line raises for Ctrl-C and SIGTERM), the tables that ended before it recorded. A
survey that cannot write ``meta.yaml`` or mark the archive complete is not ``finished``, and
says why in an ``archive_unfinished`` warning. Each table
is a ``recon.collect`` span, which says how many of its records are written, while it is
written, at most once a second by the clock (``recon.collect.progress``).

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

Once every table has run, each table sealed ``ok`` of an entry with ``changes:`` is compared
with its baseline (:mod:`recon.application.changes`): its change table (``<output>.changes``)
and its key index are written, and its outcome says what was found. A stop during that is
recorded as ``ended: stopped``, and what it wrote is then not to be read.

A table read page by page (``paginate:``) says each page and why its crawl stopped short
through the survey's page log (:class:`TablePages`, the collectors' ``CollectorContext.pages``).
Once the table is sealed its page log is kept beside it (``<output>.pages.jsonl``); a crawl
cut short seals the table ``ok`` and ``truncated``, with what cut it, and warns
(``page_cap``, ``paginate_loop``, ``paginate_empty``). A table that fails keeps no page log.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, Literal

from recon.application.plan import fan_out
from recon.domain.archives import META_FORMAT_VERSION
from recon.domain.deadline import Deadline, DeadlineExceededError
from recon.domain.documents import TableError, TableOutcome
from recon.domain.errors import CollectionError, ErrorKind, NoRecordsError, Problem, ReconError
from recon.domain.normalize import nested_records
from recon.domain.telemetry import COLLECT, EventCallback

if TYPE_CHECKING:
    from collections.abc import Collection, Generator, Mapping
    from datetime import datetime
    from pathlib import Path

    from recon.application.changes import ChangeDetector
    from recon.domain.archives import ArchiveStore
    from recon.domain.capture import CaptureLog
    from recon.domain.changes import DeletesWithheld
    from recon.domain.clock import Clock
    from recon.domain.collector import Collector
    from recon.domain.models import CollectorEntry, ReconConfig, SourceEntry
    from recon.domain.paginate import TruncatedBy
    from recon.domain.telemetry import Span, Telemetry

#: By default, the least seconds between two ``recon.collect.progress`` events of a table.
PROGRESS_S = 1.0


class CollectorError(CollectionError):
    """A collector's exception, tagged with the entry and source it came from.

    The cause's ``kind`` and ``fix`` carry through, so a 429 stays ``transient``; a cause
    that is not a recon error is ``run_failed``, the kind its survey has too.
    """

    def __init__(self, collector_name: str, source_name: str | None, cause: BaseException):
        self.collector_name = collector_name
        self.source_name = source_name
        self.cause = cause
        where = collector_name + (f" against '{source_name}'" if source_name else "")
        kind: ErrorKind = cause.kind if isinstance(cause, ReconError) else "run_failed"
        fix = cause.fix if isinstance(cause, ReconError) else None
        super().__init__(f"[{where}] {cause}", kind=kind, fix=fix)


class TablePages:
    """The page log of a survey's tables (``recon.domain.paginate.PageLog``), one table at a
    time, as tables run: :meth:`begin` starts a table's, and :meth:`end` hands it over. Each
    page is one entry, ``{"page", "token"}``, so it holds a crawl's ``pages`` entries at most."""

    def __init__(self) -> None:
        self.pages: list[dict[str, Any]] = []
        self.truncated_by: TruncatedBy | None = None

    def page(self, index: int, token: Any) -> None:
        self.pages.append({"page": index, "token": token})

    def truncated(self, by: TruncatedBy) -> None:
        self.truncated_by = by

    def begin(self) -> None:
        self.pages, self.truncated_by = [], None

    def end(self) -> tuple[list[dict[str, Any]], TruncatedBy | None]:
        taken = self.pages, self.truncated_by
        self.begin()
        return taken


class Survey:
    """One survey's run: the ports it writes through and the tables it has recorded.

    ``extracts`` names the collector types that read ``extract:``, and ``kinds`` the source
    kinds each type accepts (a type it does not name accepts every kind), so an unpinned
    collector runs against the sources the plan resolved for it; ``warnings`` collects
    what the run found worth saying about tables that succeeded, and about an archive it
    could not finish. ``finished`` is false when ``meta.yaml`` could not be written or the
    archive could not be marked complete, so it reads incomplete whatever its tables did.
    ``progress_s`` is the least time between two progress events of a table. ``pages`` is the
    page log its collectors were built with (``CollectorContext.pages``)."""

    def __init__(
        self,
        archives: ArchiveStore,
        clock: Clock,
        recon_version: str,
        telemetry: Telemetry,
        on_event: EventCallback | None = None,
        *,
        extracts: Collection[str] = (),
        kinds: Mapping[str, Collection[str]] | None = None,
        deadline: Deadline | None = None,
        progress_s: float = PROGRESS_S,
        pages: TablePages | None = None,
        changes: ChangeDetector | None = None,
    ) -> None:
        self._archives = archives
        self._pages = pages if pages is not None else TablePages()
        self._changes = changes
        self._progress_s = progress_s
        self._clock = clock
        self._deadline = deadline if deadline is not None else Deadline(clock)
        self._recon_version = recon_version
        self._telemetry = telemetry
        self._on_event = on_event
        self._extracts = frozenset(extracts)
        self._kinds = dict(kinds or {})
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
        resolved: Mapping[str, Any] | None = None,
        streams: Collection[int] = (),
    ) -> list[TableOutcome]:
        """Run every table into ``archive``, a new one :meth:`ArchiveStore.create` made at
        ``started``, and write its ``meta.yaml``. Returns the tables, which also land in
        ``meta.yaml`` under ``tables``. ``resolved`` says where what the survey resolved came
        from (how its project root was found, which declared variables were set), and is
        written as given. The entries at the positions in ``streams`` are streams, written
        window by window into their own archives, so they are no table here."""
        captures = self._archives.capture_log(archive) if config.preserve_raw else None
        tables: list[TableOutcome] = []
        what = f"the survey's deadline ({config.deadline:g}s)" if config.deadline else ""
        fix = "raise deadline: at the top of the mission, or survey fewer tables"
        meta: dict[str, Any] = {
            "format_version": META_FORMAT_VERSION,
            "recon_version": self._recon_version,
            "config_sha256": config_sha256,
            "started_at": started.isoformat(),
            "timestamp": None,  # when it ended: set by _meta, here so the key keeps its place
            "preserve_raw": config.preserve_raw,
            "resolved": dict(resolved or {}),
        }
        try:
            with self._deadline.bound(config.deadline, what, fix):
                for index, entry in enumerate(config.collectors):
                    if index in streams:
                        continue
                    tables += self._run_entry(index, entry, config, collectors, archive, captures)
            # Every table has run, so the survey reaches its end: what changed is told now.
            tables = self._compare(config, tables, archive)
        except KeyboardInterrupt:
            # Stopped on request (Ctrl-C, SIGTERM): the tables that ended are recorded, and
            # that it was stopped, so it never reads as a survey that died. One that cannot
            # write it reads crashed, as one that died does: it left no record of its end.
            with contextlib.suppress(OSError):
                self._archives.write_meta(archive, self._meta(meta, tables, "stopped"))
            raise
        meta = self._meta(meta, tables, "finished")
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

    def _compare(
        self, config: ReconConfig, tables: list[TableOutcome], archive: Path
    ) -> list[TableOutcome]:
        """Each table sealed ``ok`` of an entry with ``changes:``, compared with its baseline:
        its change table and key index written, and its outcome saying what was found. A
        table that failed is compared with nothing and is no baseline."""
        if self._changes is None:
            return tables
        at = {entry.name: (i, entry) for i, entry in enumerate(config.collectors)}
        compared = []
        for table in tables:
            index, entry = at[table.collector]
            if entry.changes is None or table.status != "ok":
                compared.append(table)
                continue
            try:
                table = self._changes.snapshot(archive, table, entry.changes, entry.normalize)
            except OSError as exc:
                self._unfinished(f"{table.name}'s change table could not be written", exc)
            compared.append(table)
            withheld = table.changes.deletes_withheld if table.changes else None
            if withheld is not None:
                self._warn(table.name, _withheld(index, entry, table.name, withheld))
        return compared

    def _unfinished(self, what: str, exc: OSError) -> None:
        """An archive that cannot keep what it was to keep cannot be finished: it reads
        incomplete, and a warning says why."""
        self.finished = False
        warning = Problem(
            "",
            f"{what}, so the archive reads incomplete: {exc}",
            kind="archive_unfinished",
            fix="free space in the archive's directory, or make it writable, then survey again",
        )
        self.warnings.append(warning)
        self._emit({"kind": "archive_warning", "reason": warning.message, "fix": warning.fix})

    def _meta(
        self,
        head: dict[str, Any],
        tables: list[TableOutcome],
        ended: Literal["finished", "stopped"],
    ) -> dict[str, Any]:
        """The archive's ``meta.yaml``: ``head``, when the survey ended, how (``ended``, the
        :data:`~recon.domain.archives.Ended` its writer can know: ``crashed`` is a reader's),
        and its tables."""
        return {
            **head,
            "timestamp": self._clock.now().isoformat(),
            "ended": ended,
            "tables": [table.document() for table in tables],
        }

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
        collector = collectors.get(entry.type)
        tables = []
        for source, output in fan_out(entry, catalog, self._kinds.get(entry.type)):
            table = _table(entry, output, source.name if source else None)
            with self._telemetry.span(COLLECT, _attributes(table)) as span:
                if collector is None:  # its type's factory raised: a plugin_broken warning
                    message = f"collector type {entry.type!r} could not be built"
                    fix = "see the plugin_broken warning for why; fix or reinstall its package"
                    tables.append(self._failed(span, table, "config", message, 0, fix))
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
        try:
            self._deadline.check()
        except DeadlineExceededError as passed:
            message = f"not started: {passed}"
            return self._failed(span, table, passed.kind, message, 0, passed.fix)
        # Each fan-out target is its own entry, named for its output, so its captures and
        # its table share the name.
        target = entry if output == entry.name else entry.model_copy(update={"name": output})
        start = self._clock.monotonic()
        self._pages.begin()
        first: list[dict[str, Any]] = []
        nothing: NoRecordsError | None = None
        what = f"table {output}'s deadline ({entry.deadline:g}s)" if entry.deadline else ""
        raise_it = f"raise deadline: on collector {entry.name!r}, or narrow what it asks for"
        try:
            with self._deadline.bound(entry.deadline, what, raise_it):
                records = _reporting(
                    self._telemetry,
                    span,
                    self._clock,
                    self._progress_s,
                    _within(
                        self._deadline,
                        _keeping_first(_tagged(collector, target, source, captures), first),
                    ),
                )
                columns = list(entry.normalize or ())
                try:
                    count = self._archives.write_table(archive, output, records, columns=columns)
                except NoRecordsError as said:
                    if first:  # records came before it: an empty table would drop them
                        raise
                    nothing = said
                    count = self._archives.write_table(
                        archive, output, _no_records(), columns=columns
                    )
        except Exception as exc:
            kind: ErrorKind = exc.kind if isinstance(exc, ReconError) else "run_failed"
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
        update: dict[str, Any] = {"records": count, "seconds": seconds}
        pages, truncated_by = self._pages.end()
        if pages:
            self._keep_pages(archive, output, pages)
        if truncated_by is not None:
            update |= {"truncated": True, "truncated_by": truncated_by}
            self._warn(output, _truncated(index, entry, output, truncated_by, len(pages)))
        return table.model_copy(update=update)

    def _keep_pages(self, archive: Path, output: str, pages: list[dict[str, Any]]) -> None:
        """Keep a sealed table's page log beside it; an archive that cannot keep it cannot be
        finished, as one that cannot keep its ``meta.yaml``."""
        try:
            self._archives.write_sidecar(archive, output, "pages", pages)
        except OSError as exc:
            self._unfinished(f"{output}'s page log could not be written", exc)

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


def _within(
    deadline: Deadline, records: Generator[dict[str, Any], None, None]
) -> Generator[dict[str, Any], None, None]:
    """``records`` while the deadline holds: past it, the next record fails the table.
    Closing this closes ``records``."""
    try:
        for record in records:
            deadline.check()
            yield record
    finally:
        records.close()


def _reporting(
    telemetry: Telemetry,
    span: Span,
    clock: Clock,
    progress_s: float,
    records: Generator[dict[str, Any], None, None],
) -> Generator[dict[str, Any], None, None]:
    """``records``, each counted once the store has it, and the count reported on ``span``
    when ``progress_s`` seconds have passed since the table began or it was last reported. Closing
    this closes ``records``."""
    written = 0
    reported = clock.monotonic()
    try:
        for record in records:
            yield record
            written += 1
            now = clock.monotonic()
            if now - reported >= progress_s:
                telemetry.progress(span, {"recon.records": written})
                reported = now
    finally:
        records.close()


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


#: Why a table's deletes were withheld, and what to do about it.
_WITHHELD: dict[str, tuple[str, str]] = {
    "truncated": ("its crawl was cut short, so a key it lacks may only be past where it "
                  "stopped", "read every page: raise paginate.pages, or narrow the request"),
    "keyless": ("it holds records with no key, which may be the ones that went",
                "name key fields every record has (changes.key), or shape the records so they "
                "have them"),
    "ambiguous": ("it holds a key with two contents, so which one went is not known",
                  "name key fields that tell its records apart (changes.key)"),
}  # fmt: skip


def _withheld(index: int, entry: CollectorEntry, output: str, why: DeletesWithheld) -> Problem:
    """The warning for a table whose deletes were asked for and withheld."""
    reason, fix = _WITHHELD[why]
    return Problem(
        f"collectors.{index}.changes.deletes",
        f"{output}'s deletes were not told: {reason}",
        kind="deletes_withheld",
        fix=f"{fix} on collector {entry.name!r}",
        got=why,
    )


def _truncated(
    index: int, entry: CollectorEntry, output: str, by: TruncatedBy, pages: int
) -> Problem:
    """The warning for a table whose crawl ``by`` cut short after ``pages`` pages."""
    at = f"collectors.{index}.paginate"
    whole = "so it is not a whole snapshot of its source"
    if by == "pages":
        return Problem(
            f"{at}.pages",
            f"{output} read its cap of {pages} page(s) with more to read: the table is "
            f"truncated, {whole}",
            kind="page_cap",
            fix=f"raise paginate.pages on collector {entry.name!r}, or narrow what it asks for",
            got=pages,
        )
    if by == "loop":
        return Problem(
            at,
            f"{output}'s page {pages - 1} named a next page it had read already, so the crawl "
            f"stopped there: the table is truncated, {whole}",
            kind="paginate_loop",
            fix=f"check that paginate on collector {entry.name!r} reads the API's next token "
            "(paginate.next, paginate.param), and that the API advances it",
            got=pages,
        )
    return Problem(
        at,
        f"{output} read empty pages in a row that still named a next one, so the crawl "
        f"stopped after {pages} page(s): the table is truncated, {whole}",
        kind="paginate_empty",
        fix=f"check that extract: on collector {entry.name!r} finds each page's records, and "
        "that the API ends its pages; or narrow what it asks for",
        got=pages,
    )


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

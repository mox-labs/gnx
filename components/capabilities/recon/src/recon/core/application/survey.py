"""The survey: run every planned collector against its sources and write one archive.

Determinism (the contract the tests in ``test_determinism.py`` hold):

    Given the same mission config, the same answers from the network (the ``Requester``
    port), the same output from shell commands (``ShellRunner``), the same documents from
    the converters and the same files under the mission's declared
    directories, a survey writes the same records, in the same order, to the same tables.
    The time enters only through the ``Clock`` port. With the clock fixed too, the whole
    archive is the same: its name, every table, and its meta. The one exception is the
    capture log a mission with ``preserve_raw`` keeps: each capture has a unique ``id``. A
    survey a scheduled tick runs also records when the tick was due, in the host's local time
    (``scheduled_at``, ``late_s``): the same clock in the same time zone, the same values.
    What changed since an earlier archive is derived after the archive is sealed, by the
    observe layer, and keeps the same contract there (``recon.observe.application.changes``).

    A stream's window keeps the same contract, window by window (``stream.py``): given the
    same records arriving at the same stamps on the clock port, each window holds the same
    records, in the same order, with the same meta and event log, whatever thread
    wrote it. Which window a record falls in is a pure function of its order of arrival, its
    stamp and the window spec, and a window kept with ``preserve_raw`` replays from its
    capture to the same table under any clock. What a full buffer drops depends on how fast
    the windows are written, so a window that dropped records says it cannot be replayed.

Tables run one at a time, in config order, each collector against each of its sources in
catalog order. A table that fails is recorded with its error and the rest still run. A
mission's ``deadline:`` bounds them all, and a collector's each of its tables
(:mod:`recon.core.domain.deadline`): a table cut short fails as ``transient``, and so does each
table not started when the survey's deadline has passed; the archive is still finished. The
archive is begun incomplete and marked complete only after its meta is recorded with every
table succeeded, so a survey that fails a table, is stopped or is killed leaves an archive
that reads incomplete. The meta says how the survey ended (``ended``): ``finished``, or
``stopped`` when it was stopped on request (a ``KeyboardInterrupt``, which the command line
raises for Ctrl-C and SIGTERM), the tables that ended before it recorded. A survey that
cannot record its meta or mark the archive complete is not ``finished``, and says why in an
``archive_unfinished`` warning. Each table
is a ``recon.collect`` span, which says how many of its records are written, while it is
written, at most once a second by the clock (``recon.collect.progress``).

Every table's records are what recon found in its collector's items
(:mod:`recon.core.application.items`): each body captured, converted and its records found
(``extract:``), each record shaped (``normalize:``). A table whose one record came from a body
and holds a list of objects, with neither ``extract:`` nor ``normalize:`` shaping it, is most
likely the whole response read as one record (arXiv's feed without ``extract: feed.entry``):
the survey keeps the table and warns, naming the list's path. A table with an ``extract:`` is
not warned about its lists: its one record is a record, and they are its own.

A table that finds nothing to keep for a reason recon or its type can name (an ``extract:``
that fits none of its bodies, items that are not objects, captures no filter kept) ends with
``NoRecordsError`` before its first record: the survey keeps the table, empty, and warns with
the reason, so an empty table never reads as a source that had nothing.

A table of an entry with ``range:`` asks its source for the run's span step by step
(:mod:`recon.core.domain.ranges`): each step is one call of its collector, the entry's settings
filled with the step's bounds, and every record it gives carries the step as ``_range``
(``{start, end}``); the steps land in the one table, all or nothing, and its outcome records
the span (``range``), which commits with the archive's meta.

A table read page by page (``paginate:``, or a type's own crawl that marks its pages) keeps
each page and why its crawl stopped short in the survey's page log (:class:`TablePages`).
Once the table is sealed its page log is kept beside it (its ``pages`` sidecar); a crawl
cut short seals the table ``ok`` and ``truncated``, with what cut it, and warns
(``paginate_pages``, ``paginate_loop``, ``paginate_empty``). A table that fails keeps no page log.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError

from recon.core.application.items import Raw, Unpacking, run_events
from recon.core.application.plan import fan_out
from recon.core.domain.archives import META_FORMAT_VERSION
from recon.core.domain.collector import PageMark, Run, Truncated
from recon.core.domain.deadline import Deadline, DeadlineExceededError
from recon.core.domain.documents import RangeOutcome, TableError, TableOutcome
from recon.core.domain.errors import CollectionError, ErrorKind, NoRecordsError, Problem, ReconError
from recon.core.domain.normalize import nested_records
from recon.core.domain.ranges import RANGE_FIELD, fill, outcome
from recon.core.domain.telemetry import COLLECT, EventCallback

if TYPE_CHECKING:
    from collections.abc import Collection, Generator, Mapping
    from datetime import datetime

    from recon.core.application.collectors import ActCollectors
    from recon.core.domain.archives import Landed
    from recon.core.domain.capture import CaptureLog
    from recon.core.domain.clock import Clock
    from recon.core.domain.collector import Collector, Plugins
    from recon.core.domain.models import CollectorEntry, ReconConfig, SourceEntry
    from recon.core.domain.paginate import TruncatedBy
    from recon.core.domain.ranges import RangeRun
    from recon.core.domain.telemetry import Span, Telemetry

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
    """The page log of a survey's tables, one table at a time, as tables run: :meth:`begin`
    starts a table's, and :meth:`end` hands it over. Each page is one entry, ``{"page",
    "token"}``, so it holds a crawl's ``pages`` entries at most. Every page is wanted, and a
    snapshot's crawl never resumes."""

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

    ``plugins`` is what is installed: each type's settings schema, and the source kinds it
    accepts, so an unpinned collector runs against the sources the plan resolved for it;
    ``warnings`` collects what the run found worth saying about tables that succeeded, and
    about an archive it could not finish. ``finished`` is false when its meta could not be
    recorded or the archive could not be marked complete, so it reads incomplete whatever its
    tables did. ``progress_s`` is the least time between two progress events of a table.
    ``pages`` is the survey's page log. ``ranges`` is the run of each ranged table, by output
    (:func:`recon.observe.application.ranges.range_runs`)."""

    def __init__(
        self,
        clock: Clock,
        recon_version: str,
        telemetry: Telemetry,
        on_event: EventCallback | None = None,
        *,
        plugins: Plugins,
        deadline: Deadline | None = None,
        progress_s: float = PROGRESS_S,
        pages: TablePages | None = None,
        ranges: Mapping[str, RangeRun] | None = None,
    ) -> None:
        self._pages = pages if pages is not None else TablePages()
        self._ranges = dict(ranges or {})
        self._progress_s = progress_s
        self._clock = clock
        self._deadline = deadline if deadline is not None else Deadline(clock)
        self._recon_version = recon_version
        self._telemetry = telemetry
        self._on_event = on_event
        self._plugins = plugins
        self.warnings: list[Problem] = []
        self.finished = True

    def run(
        self,
        config: ReconConfig,
        built: ActCollectors,
        archive: Landed,
        *,
        started: datetime,
        config_sha256: str | None = None,
        config_value: Mapping[str, Any] | None = None,
        resolved: Mapping[str, Any] | None = None,
        streams: Collection[int] = (),
        scheduled: Mapping[str, Any] | None = None,
    ) -> list[TableOutcome]:
        """Run every table into ``archive``, one begun at ``started``, and record its meta. Returns
        the tables, which are also the meta's ``tables``. ``config_value`` is the config the survey
        ran when it was given as a value, recorded as given, its credentials redacted. ``resolved``
        says where what the survey resolved came from (how its project root was found, which
        declared variables were set), and is recorded as given. The entries at the positions in
        ``streams`` are streams, written window by window into their own archives, so they are no
        table here. A survey a scheduled tick runs is given ``scheduled`` (``scheduled_at``,
        ``late_s``), recorded as given; one run by hand has neither key."""
        captures = archive.captures() if config.preserve_raw else None
        index = archive.ref.index
        tables: list[TableOutcome] = []
        what = f"the survey's deadline ({config.deadline:g}s)" if config.deadline else ""
        fix = "raise deadline: at the top of the mission, or survey fewer tables"
        meta: dict[str, Any] = {
            "format_version": META_FORMAT_VERSION,
            "recon_version": self._recon_version,
            "config_sha256": config_sha256,
            # A config given as a value has no file to read again: what it was, its URLs'
            # credentials redacted as everywhere recon reports one.
            **({} if config_value is None else {"config": dict(config_value)}),
            # The mission's archives in the order they were made, whatever the clock said.
            **({} if index is None else {"index": index}),
            "started_at": started.isoformat(),
            "timestamp": None,  # when it ended: set by _meta, here so the key keeps its place
            "preserve_raw": config.preserve_raw,
            "resolved": dict(resolved or {}),
            **dict(scheduled or {}),
        }
        try:
            with self._deadline.bound(config.deadline, what, fix):
                for index, entry in enumerate(config.collectors):
                    if index in streams:
                        continue
                    tables += self._run_entry(index, entry, config, built, archive, captures)
        except KeyboardInterrupt:
            # Stopped on request (Ctrl-C, SIGTERM): the tables that ended are recorded, and
            # that it was stopped, so it never reads as a survey that died. One that cannot
            # write it reads crashed, as one that died does: it left no record of its end.
            with contextlib.suppress(OSError):
                archive.record(self._meta(meta, tables, "stopped"))
            raise
        meta = self._meta(meta, tables, "finished")
        try:
            archive.record(meta)
            if all(table.status == "ok" for table in tables):
                archive.complete()
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
        """The archive's meta: ``head``, when the survey ended, how (``ended``, the
        :data:`~recon.core.domain.archives.Ended` its writer can know: ``crashed`` is a
        reader's), and its tables."""
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
        built: ActCollectors,
        archive: Landed,
        captures: CaptureLog | None,
    ) -> list[TableOutcome]:
        catalog = {s.name: s for s in config.catalog}
        collector = built.collectors.get(entry.type)
        tables = []
        for source, output in fan_out(entry, catalog, self._plugins.accepts(entry.type)):
            table = _table(entry, output, source.name if source else None)
            with self._telemetry.span(COLLECT, _attributes(table)) as span:
                if collector is None:  # its type's factory raised: a plugin_broken warning
                    message = f"collector type {entry.type!r} could not be built"
                    fix = "see the plugin_broken warning for why; fix or reinstall its package"
                    tables.append(self._failed(span, table, "config", message, 0, fix))
                    continue
                outcome = self._run_table(
                    span, table, collector, built, (index, entry), source, archive, captures
                )
                tables.append(outcome)
        return tables

    def _run_table(
        self,
        span: Span,
        table: TableOutcome,
        collector: Collector,
        built: ActCollectors,
        at: tuple[int, CollectorEntry],
        source: SourceEntry | None,
        archive: Landed,
        captures: CaptureLog | None,
    ) -> TableOutcome:
        index, entry = at
        output = table.name
        ranged = self._ranges.get(output)
        if ranged is not None:
            table = table.model_copy(update={"range": RangeOutcome.model_validate(outcome(ranged))})
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
        bodied: list[bool] = []
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
                        _keeping_first(
                            self._records(
                                collector, built, target, source, captures, ranged, bodied
                            ),
                            first,
                        ),  # fmt: skip
                    ),
                )
                columns = list(entry.normalize or ())
                try:
                    count = archive.write(output, records, columns=columns)
                except NoRecordsError as said:
                    if first:  # records came before it: an empty table would drop them
                        raise
                    nothing = said
                    count = archive.write(output, _no_records(), columns=columns)
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
        if count == 1 and bodied[:1] == [True]:
            self._check_extract(index, entry, output, first[0])
        update: dict[str, Any] = {"records": count, "seconds": seconds}
        pages, truncated_by = self._pages.end()
        if pages:
            self._keep_pages(archive, output, pages)
        if truncated_by is not None:
            update |= {"truncated": True, "truncated_by": truncated_by}
            entry_at = f"collectors.{index}"
            self._warn(output, crawl_truncated(entry_at, entry, output, truncated_by, len(pages)))
        return table.model_copy(update=update)

    def _records(
        self,
        collector: Collector,
        built: ActCollectors,
        entry: CollectorEntry,
        source: SourceEntry | None,
        captures: CaptureLog | None,
        ranged: RangeRun | None,
        bodied: list[bool],
    ) -> Generator[dict[str, Any], None, None]:
        """A table's records: each run's (a ranged table's step by step, its entry's settings
        filled with the step's bounds, each record carrying the step as ``_range``), found and
        shaped by recon; its pages kept in the page log. ``bodied`` is told whether its first
        record came from a body. Closing this closes the run being read, so its collector's
        cleanup runs."""
        unpacking = Unpacking(built.converters, captures)
        steps: list[Any] = [None] if ranged is None else list(ranged.steps)
        for step in steps:
            asked = entry if step is None else entry.model_copy(update=fill(entry.settings, step))
            run = run_of(self._plugins, asked, source)
            shaping = built.shaping(run)
            events = _tagged(run_events(collector, run, unpacking, shaping), run)
            try:
                for event in events:
                    if isinstance(event, Raw):
                        record = shaping.raw(event)
                        if step is not None:
                            record[RANGE_FIELD] = step.document()
                        if not bodied:
                            bodied.append(event.bodied)
                        yield record
                    elif isinstance(event, PageMark):
                        self._pages.page(event.index, event.token)
                    elif isinstance(event, Truncated):
                        self._pages.truncated(event.by)
            finally:
                events.close()
        unpacking.end()

    def _keep_pages(self, archive: Landed, output: str, pages: list[dict[str, Any]]) -> None:
        """Keep a sealed table's page log beside it; an archive that cannot keep it cannot be
        finished, as one that cannot record its meta."""
        try:
            archive.keep(output, "pages", pages)
        except OSError as exc:
            self._unfinished(f"{output}'s page log could not be written", exc)

    def _check_extract(
        self, index: int, entry: CollectorEntry, output: str, record: dict[str, Any]
    ) -> None:
        # With an extract, the one record is one of the records the extract names, and a
        # list inside it is that record's own (a paper's authors), not the response's.
        if entry.normalize or entry.extract:
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
        # The message stays in the archive's meta: it can quote a command or a URL, and a span
        # goes wherever telemetry is sent.
        span.fail(kind, f"the table failed ({kind}); recon status gives its error")
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


def _tagged(events: Generator[Any, None, None], run: Run) -> Generator[Any, None, None]:
    """``events``; any exception their collector (or recon's conversion of what it gave)
    raises, eagerly or mid-stream, comes out as a CollectorError naming the entry and source.
    Closing this closes ``events``."""
    try:
        yield from events
    except (CollectorError, NoRecordsError):
        raise
    except Exception as exc:
        source = run.source.name if run.source else None
        raise CollectorError(run.entry.name, source, exc) from exc


def run_of(plugins: Plugins, entry: CollectorEntry, source: SourceEntry | None) -> Run:
    """The run of ``entry`` against ``source``: its type's own keys validated by the type's
    schema. A value its schema refuses (a range's bound filled into a key that cannot take
    it) fails the table as ``config``."""
    schema = plugins.collector_types[entry.type].settings
    if schema is None:
        return Run(entry, source)
    try:
        return Run(entry, source, schema.model_validate(entry.settings))
    except ValidationError as exc:
        raise CollectionError(
            f"collector {entry.name!r}: its settings as filled for this run are refused: {exc}",
            kind="config",
            fix="check the values the run fills in (a range's placeholders) against the type's "
            "keys (recon types --json)",
        ) from exc


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


def crawl_truncated(
    entry_at: str, entry: CollectorEntry, output: str, by: TruncatedBy, pages: int
) -> Problem:
    """The warning for a table (or a stream's crawl) whose crawl ``by`` cut short after
    ``pages`` pages; ``entry_at`` is the entry's path (``collectors.<i>``)."""
    at = f"{entry_at}.paginate"
    whole = "so it is not a whole snapshot of its source"
    if by == "pages":
        return Problem(
            f"{at}.pages",
            f"{output} read its cap of {pages} page(s) with more to read: the table is "
            f"truncated, {whole}",
            kind="paginate_pages",
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
        collector=entry.name,
        type=entry.type,
        source=source,
        type_url=entry.type_url,
    )

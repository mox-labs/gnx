"""The versioned documents recon publishes: one record per ``recon.v1.<shape>``.

Each operation returns one of these, and ``--json`` prints its :meth:`Document.document`.
They are the one definition of each shape: ``meta.yaml`` holds :class:`TableOutcome`s and
the error envelope is :class:`ErrorDocument`. A field whose value is the description a
plugin wrote, or a row SQL returned, is plain JSON data; everything else is typed here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Pydantic reads the field types when the models are built, so they are imported to run.
from recon.domain.archives import ArchiveState, Ended  # noqa: TC001
from recon.domain.capture import CAPTURE_SCHEMA, CaptureKind
from recon.domain.errors import ERROR_KINDS, ErrorKind, ReconError
from recon.domain.stream import ClosedBy  # noqa: TC001

#: Whether a survey's archive holds every table it was asked for: :data:`ArchiveState`
#: once the survey has ended, so never ``writing``. ``incomplete``: a table failed, the
#: survey was stopped, or ``meta.yaml`` could not be written.
SurveyStatus = Literal["complete", "incomplete"]

#: What ``init`` did to the mission's config: wrote the first one, found this one already
#: there byte for byte and left it, or overwrote a different one (``--replace``).
InitOutcome = Literal["created", "unchanged", "replaced"]


class Document(BaseModel):
    """Base of every published record: frozen, unknown keys refused, ``schema`` by alias."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    def document(self) -> dict[str, Any]:
        """The record as plain JSON data, keyed as ``--json`` and ``meta.yaml`` show it."""
        data: dict[str, Any] = self.model_dump(mode="json", by_alias=True)
        return data


# --- init and plan --------------------------------------------------------------------------


class MissionDocument(Document):
    schema_: Literal["recon.v1.mission"] = Field(default="recon.v1.mission", alias="schema")
    mission: str
    config: str
    #: ``template:<name>``, or the path of the file the config was copied from (``-``:
    #: standard input).
    from_: str = Field(serialization_alias="from")
    outcome: InitOutcome


class PlanWrites(Document):
    #: Where ``-c`` would copy the config; None when the mission's own config is planned.
    config: str | None
    archive: str
    captures: bool
    #: The directory of each stream the mission has, where its windows are written.
    streams: list[str] = []


#: Where an effective setting came from: the mission's config, its collector type's
#: declaration (a stream's overflow, from the type's ``paced_by``), the command's own flag
#: (or the call's argument: ``recon stream --windows``), or recon's default.
Origin = Literal["mission", "type", "command", "recon"]


class SourcedValue(Document):
    """One effective setting the plan resolved, by its path in the config, and where it came
    from. ``note`` says why, when it is not the config's own."""

    path: str
    value: Any
    source: Origin
    note: str = ""


class Required(Document):
    """Something a planned run needs of this host, or of what it reaches, whether it holds
    here now, and the outputs of the runs that need it: what a collector type's preflight
    said, the same need from several runs said once."""

    need: str
    holds: bool
    #: What was found, when it does not hold.
    why: str = ""
    #: How to make it hold, when it does not.
    fix: str = ""
    needed_by: list[str]


class PlanDocument(Document):
    schema_: Literal["recon.v1.plan"] = Field(default="recon.v1.plan", alias="schema")
    mission: str
    valid: bool
    #: One per collector and source; the keys after ``effects`` are what the collector type
    #: describes (a URL, the commands, a path).
    runs: list[dict[str, Any]]
    effects: list[str]
    writes: PlanWrites
    problems: list[dict[str, Any]]
    #: Problem records (``recon.v1.error``'s shape) that do not stop the survey.
    warnings: list[dict[str, Any]]
    #: Each catalog source an unpinned collector does not run against, its type not
    #: accepting the source's kind: ``{"collector", "source", "kind"}``.
    skipped: list[dict[str, str]] = []
    #: What the runs need of this host, as the collector types that declare a preflight
    #: checked it during this dry run, each with whether it holds.
    requirements: list[Required] = []
    #: Whether every requirement holds. False is not an error: the plan is still valid, and a
    #: survey fails each table whose requirement does not hold, as ``config``.
    runnable: bool = True
    #: What this dry run did to check the requirements, by collector type (``mcp: started
    #: each stdio server a run names ...``); empty when it ran, fetched and wrote nothing.
    preflight: list[str] = []
    #: Each setting of a stream the survey would use, with where it came from: what the
    #: mission wrote, and every default recon filled in (a window, a buffer, retain).
    provenance: list[SourcedValue] = []


# --- survey ---------------------------------------------------------------------------------


class TableError(Document):
    #: An error kind; read back from an older ``meta.yaml`` it may be one this recon lacks.
    kind: str
    message: str
    fix: str | None = None


class TableOutcome(Document):
    """One table of a survey: what produced it and how it ended."""

    name: str
    file: str
    collector: str
    type: str
    source: str | None
    type_url: str
    status: Literal["ok", "error"] = "ok"
    records: int = 0
    seconds: float = 0.0
    error: TableError | None = None


class WindowOutcome(Document):
    """One window of a stream, sealed: which, where, the span it covers, how it was closed,
    what it dropped, and its one table (whose ``status`` is the window's: ``error`` when its
    source failed, its records kept)."""

    stream: str
    index: int
    #: The window's archive id, as ``recon status`` lists it, and its directory.
    id: str
    archive: str
    #: ISO 8601, from the clock port: the window covers ``[opened_at, closed_at)``.
    opened_at: str
    closed_at: str
    closed_by: ClosedBy
    #: Records that arrived in the window and were not written: a full buffer's overflow, or
    #: a record larger than ``max_record_bytes``.
    dropped: int
    #: The most records that waited in the buffer while the window was filled: at most the
    #: stream's ``buffer.records``. It depends on how fast the window was written, so it is
    #: here and not in the window's ``meta.yaml``, which the same arrivals keep the same.
    high_water: int
    table: TableOutcome


class SurveyDocument(Document):
    """A survey: its archive of snapshot tables and, for each entry that is a stream, the one
    window it ran of it (``windows``), in config order. ``status`` is ``incomplete`` when a
    table or a window failed."""

    schema_: Literal["recon.v1.survey"] = Field(default="recon.v1.survey", alias="schema")
    mission: str
    archive: str
    status: SurveyStatus
    tables: list[TableOutcome]
    warnings: list[dict[str, Any]]
    windows: list[WindowOutcome] = []

    @property
    def failed(self) -> list[TableOutcome]:
        every = self.tables + [window.table for window in self.windows]
        return [table for table in every if table.status == "error"]


class StreamDocument(Document):
    """One run of a stream: how many windows it sealed and what they held, the last of them
    (whose ``closed_by`` says why the run stopped), and the stream's ``state`` once it had.
    Each window is listed in ``recon status`` and followed by ``recon watch --stream``; the
    run's own list stays this size however long it runs."""

    schema_: Literal["recon.v1.stream"] = Field(default="recon.v1.stream", alias="schema")
    mission: str
    stream: str
    #: The stream's directory, holding its windows.
    path: str
    state: ArchiveState | None
    windows: int
    records: int
    dropped: int
    #: The first window this run sealed, by id; None when it sealed none.
    first: str | None
    last: WindowOutcome | None
    warnings: list[dict[str, Any]]


# --- status, query, templates ---------------------------------------------------------------


class TableStatus(Document):
    name: str | None
    type_url: str | None
    status: str | None
    records: int | None
    error: TableError | None = None


class ArchiveStatus(Document):
    id: str
    path: str
    state: ArchiveState
    #: Whether its survey ran to its end; None while ``writing``.
    ended: Ended | None
    #: ISO 8601, from ``meta.yaml``; None when it is missing or does not say (format 1 has
    #: only the finish). ``finished_at`` None: the survey did not finish.
    started_at: str | None
    finished_at: str | None
    #: None when ``meta.yaml`` is missing or unreadable: the survey did not finish.
    format_version: int | None
    recon_version: str | None
    config_sha256: str | None
    tables: list[TableStatus]


class StreamStatus(Document):
    """A stream of a mission: ``writing`` while a ``recon stream`` (or a survey) extends it,
    else its last window's state; its windows, the first and the latest."""

    name: str
    path: str
    state: ArchiveState | None
    windows: int
    #: The first window's id, which ``watch --stream --archive`` takes to read from the start.
    first: str | None
    latest: ArchiveStatus | None
    #: The windows ``retain:`` removed, each leaving a tombstone beside the windows.
    retired: int = 0


class MissionStatus(Document):
    name: str
    config: str
    archives: int
    latest: ArchiveStatus | None
    captures: int
    #: Every archive, oldest first, when ``status`` was asked about this mission alone;
    #: None otherwise. Each ``id`` is what ``query --archive`` takes.
    history: list[ArchiveStatus] | None = None
    streams: list[StreamStatus] = []


class CollectorTypeInfo(Document):
    type: str
    summary: str
    effects: list[str] | Literal["unknown"]


class BrokenPluginInfo(Document):
    group: str
    name: str
    value: str
    error: str


class InstalledPlugins(Document):
    collectors: list[CollectorTypeInfo]
    #: Each as ``$name``.
    transforms: list[str]
    broken: list[BrokenPluginInfo]


class StatusDocument(Document):
    schema_: Literal["recon.v1.status"] = Field(default="recon.v1.status", alias="schema")
    root: str
    missions: list[MissionStatus]
    plugins: InstalledPlugins
    #: Problem records that do not stop the command: ``archive_abandoned``, each half-made
    #: archive a dead survey left, with how to remove it.
    warnings: list[dict[str, Any]] = []


class WindowRange(Document):
    """A run of a stream's windows, by the ids of its first and last (null when they are no
    longer known: a retired window's tombstone gone), and how many there are: a range stays
    this size however many windows it spans."""

    first: str | None
    last: str | None
    count: int


class QueryRows(Document):
    schema_: Literal["recon.v1.query-rows"] = Field(default="recon.v1.query-rows", alias="schema")
    mission: str
    #: The archive the rows came from: its directory, and its id (``query --archive``).
    archive: str
    archive_id: str
    state: ArchiveState
    ended: Ended | None
    columns: list[str]
    rows: list[dict[str, Any]]
    count: int
    warnings: list[dict[str, Any]]
    #: A query of a stream: its name, and the range of windows read (``archive`` is then the
    #: stream's directory, ``archive_id`` its last window read, and ``state`` the stream's).
    stream: str | None = None
    windows: WindowRange | None = None


class RecordsSince(Document):
    """What one read of an archive found: the records written since the cursor it was given,
    and the cursor to read on from. A survey writing the archive is followed by reading again
    with ``cursor`` until ``state`` is no longer ``writing`` and a read has no ``more``:
    ``state`` is read before the records, so such a read holds the last of them."""

    schema_: Literal["recon.v1.records-since"] = Field(
        default="recon.v1.records-since", alias="schema"
    )
    mission: str
    #: The archive read: its directory, and its id (``--archive``), which a later read with
    #: ``cursor`` names again.
    archive: str
    archive_id: str
    state: ArchiveState
    ended: Ended | None
    #: ``{"table", "record"}`` each, table by table in output-name order, each table's in the
    #: order written. A table not yet ``sealed`` may still be retracted.
    records: list[dict[str, Any]]
    count: int
    #: Opaque: pass it, unchanged, to the next read of the same archive.
    cursor: str
    #: Tables whose last record is now read: they gain no more.
    sealed: list[str]
    #: Tables that failed after records of theirs were read: those records are void.
    retracted: list[str]
    #: A bound (``limit`` records, ``max_bytes`` bytes) cut this read before all that was
    #: written by then was read: read again at once.
    more: bool
    #: A read of a stream: its name. ``archive`` and ``archive_id`` are then the window read,
    #: ``state`` that window's, and ``cursor`` carries the window, so the next read goes on
    #: into the next window once this one is read to its end.
    stream: str | None = None


class TemplateInfo(Document):
    name: str
    description: str


class TemplatesDocument(Document):
    schema_: Literal["recon.v1.templates"] = Field(default="recon.v1.templates", alias="schema")
    templates: list[TemplateInfo]


# --- captures -------------------------------------------------------------------------------


class Capture(Document):
    """One line of a capture log, as recon writes it (``recon --skill -r capture-format``).

    Other tools write these lines too, so the reader does not parse them with this record:
    it trusts only what it checks and ignores fields it does not know.
    """

    schema_: Literal["recon.v1.capture"] = Field(default=CAPTURE_SCHEMA, alias="schema")
    id: str
    source: str | None
    collector: str
    kind: CaptureKind
    #: What was asked, as the collector or the hook payload gave it.
    request: dict[str, Any]
    #: The HTTP status, or the command's exit code; None when there is none.
    status: int | None
    content_type: str
    #: Credential headers redacted.
    headers: dict[str, str]
    #: ``raw/<sha256>``, relative to the log's directory.
    body: str
    sha256: str
    size: int = Field(serialization_alias="bytes")
    #: ISO 8601, from the clock port.
    captured_at: str


# --- the error envelope ---------------------------------------------------------------------


class ErrorBody(Document):
    kind: ErrorKind
    message: str
    fix: str | None
    #: False when checking stopped before every problem was found: fix these, check again.
    complete: bool
    problems: list[dict[str, Any]]
    details: dict[str, Any]


class ErrorDocument(Document):
    schema_: Literal["recon.v1.error"] = Field(default="recon.v1.error", alias="schema")
    error: ErrorBody


def error_document(error: ReconError) -> ErrorDocument:
    """The envelope a failing command prints, as ``components/capabilities/error-envelope.json``
    agrees it. ``problems`` and ``details`` are always present, and empty when the error has
    none."""
    return ErrorDocument(
        error=ErrorBody(
            kind=error.kind,
            message=error.message,
            fix=error.fix,
            complete=error.complete,
            problems=[problem.document() for problem in error.problems],
            details=error.details,
        )
    )


def failure_kind(tables: list[TableOutcome]) -> ErrorKind | None:
    """Classify a survey by its failed tables; None when every table succeeded.

    ``auth`` if any table failed on credentials (a new key fixes it, a retry does not);
    else ``transient`` if every failure was transient (a retry may fix all of them);
    else ``run_failed``.
    """
    kinds = [t.error.kind if t.error else "unknown" for t in tables if t.status == "error"]
    if not kinds:
        return None
    if "auth" in kinds:
        return "auth"
    if all(k == "transient" for k in kinds):
        return "transient"
    return "run_failed"


_SURVEY_FAILURE_FIX = {
    "auth": "check the credentials named by the failing sources' auth.env, then: recon survey {n}",
    "transient": "retry later: recon survey {n}",
    "run_failed": "read each failed table's error (meta.yaml, or --json), fix its cause, "
    "then: recon survey {n} --dry-run",
}


def survey_failure(document: SurveyDocument) -> ReconError | None:
    """The error a survey's failed tables add up to, or None when every table succeeded and
    the archive was finished. An archive that could not be finished is ``store`` (owner ruling
    2026-10-22), whatever its tables did: a failed table's error is recorded in the
    ``meta.yaml`` that could not be written, so the store is the failure to act on first, and
    its ``archive_unfinished`` warning says why. Otherwise the kind is ``auth``,
    ``transient`` or ``run_failed``. The details name the archive and the failed tables. A
    stream's window that failed counts as a failed table."""
    every = document.tables + [window.table for window in document.windows]
    kind = failure_kind(every)
    failed = [t.name for t in document.failed]
    if any(w.get("kind") == "archive_unfinished" for w in document.warnings):
        tables = (
            f"{len(failed)} of {len(every)} table(s) failed, and "
            if failed
            else "every table succeeded, but "
        )
        return ReconError(
            f"{tables}the archive could not be finished, so it reads incomplete: "
            f"{document.archive}",
            kind="store",
            fix="read the survey's archive_unfinished warning, free space in the archive's "
            f"directory or make it writable, then: recon survey {document.mission}",
            details={"archive": document.archive, "failed": failed},
        )
    if kind is None:
        return None
    return ReconError(
        f"{len(failed)} of {len(every)} table(s) failed; the archive is incomplete",
        kind=kind,
        fix=_SURVEY_FAILURE_FIX[kind].format(n=document.mission),
        details={"archive": document.archive, "failed": failed},
    )


def stream_failure(document: StreamDocument) -> ReconError | None:
    """The error a stream's run ended on, or None when its last window was sealed ``ok``. A
    source that fails ends the stream, so only its last window can have: that window keeps
    the records it got, and its error's kind is the run's."""
    last = document.last
    if last is None or last.table.error is None:
        return None
    error = last.table.error
    kind: ErrorKind = next((known for known in ERROR_KINDS if known == error.kind), "unknown")
    return ReconError(
        f"stream {document.stream} stopped: its source failed in window {last.id}, which keeps "
        f"the {last.table.records} record(s) it got: {error.message}",
        kind=kind,
        fix=error.fix
        or f"read the window's error, fix its cause, then: recon stream {document.mission} "
        f"--collector {document.stream}",
        details={"archive": last.archive, "window": last.id},
    )

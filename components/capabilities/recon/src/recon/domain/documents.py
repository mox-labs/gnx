"""The versioned documents recon publishes: one record per ``recon.v1.<shape>``.

Each operation returns one of these, and ``--json`` prints its :meth:`Document.document`.
They are the one definition of each shape: ``meta.yaml`` holds :class:`TableOutcome`s and
the error envelope is :class:`ErrorDocument`. A field whose value is the description a
plugin wrote, or a row SQL returned, is plain JSON data; everything else is typed here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from recon.domain.capture import CAPTURE_SCHEMA, CaptureKind
from recon.domain.errors import ErrorKind, ReconError

#: Whether a survey's archive holds every table it was asked for. ``incomplete``: a table
#: failed, the survey was stopped, or ``meta.yaml`` could not be written.
SurveyStatus = Literal["ok", "incomplete"]

#: Where an archive stands as ``status`` and ``query`` read it: ``ok`` and ``incomplete`` as
#: for :data:`SurveyStatus`, or ``writing``: a survey is writing it now, so its tables not
#: yet sealed are not queried, and reading it again later finds it ``ok`` or ``incomplete``.
ArchiveState = Literal["ok", "incomplete", "writing"]

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


class SurveyDocument(Document):
    schema_: Literal["recon.v1.survey"] = Field(default="recon.v1.survey", alias="schema")
    mission: str
    archive: str
    status: SurveyStatus
    tables: list[TableOutcome]
    warnings: list[dict[str, Any]]

    @property
    def failed(self) -> list[TableOutcome]:
        return [table for table in self.tables if table.status == "error"]


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
    #: ISO 8601, from ``meta.yaml``; None when it is missing or does not say (format 1 has
    #: only the finish). ``finished_at`` None: the survey did not finish.
    started_at: str | None
    finished_at: str | None
    #: None when ``meta.yaml`` is missing or unreadable: the survey did not finish.
    format_version: int | None
    recon_version: str | None
    config_sha256: str | None
    tables: list[TableStatus]


class MissionStatus(Document):
    name: str
    config: str
    archives: int
    latest: ArchiveStatus | None
    captures: int
    #: Every archive, oldest first, when ``status`` was asked about this mission alone;
    #: None otherwise. Each ``id`` is what ``query --archive`` takes.
    history: list[ArchiveStatus] | None = None


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


class QueryRows(Document):
    schema_: Literal["recon.v1.query-rows"] = Field(default="recon.v1.query-rows", alias="schema")
    mission: str
    #: The archive the rows came from: its directory, and its id (``query --archive``).
    archive: str
    archive_id: str
    state: ArchiveState
    columns: list[str]
    rows: list[dict[str, Any]]
    count: int
    warnings: list[dict[str, Any]]


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
    ``transient`` or ``run_failed``. The details name the archive and the failed tables."""
    kind = failure_kind(document.tables)
    failed = [t.name for t in document.failed]
    if any(w.get("kind") == "archive_unfinished" for w in document.warnings):
        tables = (
            f"{len(failed)} of {len(document.tables)} table(s) failed, and "
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
        f"{len(failed)} of {len(document.tables)} table(s) failed; the archive is incomplete",
        kind=kind,
        fix=_SURVEY_FAILURE_FIX[kind].format(n=document.mission),
        details={"archive": document.archive, "failed": failed},
    )

"""recon's errors, the problems a plan finds, and the one exit-code table.

Every error has a ``kind`` that says what the caller should do next, a message that names
what is wrong, a ``fix`` (the next command to run or the config line to change), and
``details``: structured fields such as the archive a failed survey left. :data:`EXIT_CODES`
gives the exit code of every kind, :data:`EXIT_MEANINGS` what each means and
:data:`EXIT_NEXT` what to do about it; the command line, the skill text and the tests read
these and no other. The kinds every capability CLI shares have the codes agreed in
``components/capabilities/exit-codes.json``, and the problem record and its shared kinds
are the ones ``components/capabilities/error-envelope.json`` agrees; the tests hold recon
to both. recon's own kinds take codes from 10 up, one kind per code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ErrorKind = Literal[
    "unknown",
    "usage",
    "config",
    "not_found",
    "transient",
    "auth",
    "conflict",
    "run_failed",
    "store",
    "query",
    "busy",
    "interrupted",
]

#: The kinds an error can have, as ``error.kind`` in the ``recon.v1.error`` envelope.
ERROR_KINDS: tuple[ErrorKind, ...] = (
    "unknown",
    "usage",
    "config",
    "not_found",
    "transient",
    "auth",
    "conflict",
    "run_failed",
    "store",
    "query",
    "busy",
    "interrupted",
)

#: The exit code of each outcome and error kind.
EXIT_CODES: dict[str, int] = {
    "ok": 0,
    "unknown": 1,
    "usage": 2,
    "config": 3,
    "not_found": 4,
    "transient": 5,
    "auth": 6,
    "conflict": 7,
    "run_failed": 8,
    "store": 9,
    "query": 10,
    "busy": 11,
    "interrupted": 130,
}

#: What each kind in :data:`EXIT_CODES` means.
EXIT_MEANINGS: dict[str, str] = {
    "ok": "done",
    "unknown": "a failure recon did not classify",
    "usage": "a bad command, flag or argument",
    "config": "the mission config or another input is invalid",
    "not_found": "no such mission, archive or template",
    "transient": "every failed table failed transiently (HTTP 429, 5xx, network, a "
    "deadline); the archive is kept, marked incomplete",
    "auth": "a table failed on credentials (HTTP 401, 403); the archive is kept, marked incomplete",
    "conflict": "init, or survey -c, would overwrite a different mission config, or a tick's "
    "config changed",
    "run_failed": "a table failed, and not only on credentials or transiently; the archive is "
    "kept, marked incomplete",
    "store": "the survey could not finish its archive; it is kept, marked incomplete",
    "query": "the SQL failed, or reached outside the mission's archives",
    "busy": "another recon is writing the stream, or surveying the mission",
    "interrupted": "Ctrl-C or SIGTERM stopped the command; a survey's archive reads `ended: "
    "stopped` (a stopped `recon stream` exits 0)",
}

#: What to do next on each kind in :data:`EXIT_CODES`.
EXIT_NEXT: dict[str, str] = {
    "ok": "go on: plan, survey, query, in that order",
    "unknown": "report it as a bug in recon, with the command that raised it",
    "usage": "correct the command; `recon <command> --help` lists its arguments",
    "config": "fix each problem at its `path` as its `fix` says, then plan again; when "
    "`complete` is false, more problems may follow the fix",
    "not_found": "run the command the error's `fix` names",
    "transient": "if a table's error names a `deadline:` or `timeout:`, raise it (a retry "
    "fails too); else survey again later",
    "auth": "set the variable the failing source's auth.env names to a valid key, then "
    "survey again",
    "conflict": "add --replace to overwrite it, or choose another mission name; for a tick, "
    "review the config, then install it again",
    "run_failed": "read each failed table's error and fix its cause, then plan and survey again",
    "store": "free space where the archive is, or make it writable, then survey again",
    "query": "correct the SQL as the error's `fix` says; for a missing table, it names the "
    "tables there are",
    "busy": "let it run, or stop it as the `fix` says (`kill -TERM <pid>`, the pid in "
    "`details`), then run the command again",
    "interrupted": "run the command again",
}

#: The kinds a problem or a warning can have, as ``problems[].kind`` and ``warnings[].kind``:
#: the shared vocabulary first, then recon's own. A consumer tolerates a kind it does not
#: know: the set grows.
PROBLEM_KINDS: tuple[str, ...] = (
    "yaml_syntax",
    "wrong_shape",
    "unknown_key",
    "missing_key",
    "invalid_value",
    "name_unknown",
    "name_taken",
    # a {placeholder} nothing fills
    "unresolved",
    # warnings
    "env_unset",
    "plugin_broken",
    "otel_unavailable",
    "telemetry_failed",
    # a table came back as one record holding the list of records
    "extract_missing",
    # a table's file that SQL cannot read as a table
    "table_unreadable",
    # the survey could not write meta.yaml or mark its archive complete
    "archive_unfinished",
    # a table kept empty because recon could not find or keep its records: an extract: that
    # resolves to nothing in a response that is not empty, items that are not objects,
    # captures no filter kept
    "extract_unresolved",
    "records_not_objects",
    "captures_unmatched",
    # a table kept empty because the MCP tool or resource it reads answered with no content
    "result_empty",
    # events OpenTelemetry or on_event never got: a receiver was slower than the survey
    "events_dropped",
    # a receiver of a survey's events had not returned from one when the survey ended
    "telemetry_stuck",
    # what a survey that died while making its archive left behind, unlisted
    "archive_abandoned",
    # records of a stream that arrived and were dropped, one warning a cause (a full buffer,
    # one too large, a stop at once), at the path of the key that governs it
    "records_dropped",
    # a stream's source had not returned its next record when the stream stopped
    "source_stuck",
    # a stream that keeps every window because its entry sets no retain:
    "retain_unset",
    # a query of a stream that read more windows than it is cheap to open
    "windows_many",
    # a mission's config changed since its schedule was installed: its ticks are refused
    "config_changed",
    # a scheduler that cannot keep the schedule's missed: (cron runs no missed tick)
    "missed_ignored",
    # ticks skipped in a row because the one before still ran: the survey outlasts every
    "schedule_overrun",
)


@dataclass(frozen=True)
class Problem:
    """One finding in a mission config, located by a path dotted from its root
    (``collectors.2.type``), or a warning about the run it describes.

    ``allowed`` lists the legal values where the set is closed; ``got`` is the value that was
    written, when it is a scalar worth quoting back; ``source`` names the file the problem is
    in when it is not the config the command was given.
    """

    path: str
    message: str
    kind: str = "invalid_value"
    fix: str = ""
    got: str | int | float | bool | None = None
    allowed: tuple[str, ...] = ()
    source: str = ""

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message

    def document(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "message": self.message,
            "fix": self.fix,
            "allowed": list(self.allowed),
            "got": self.got,
            "source": self.source,
        }


class ReconError(Exception):
    """Base for every error recon raises on purpose.

    ``complete`` is false when checking stopped before every problem was found.
    """

    kind: ErrorKind = "unknown"
    fix: str | None = None

    def __init__(
        self,
        message: str,
        *,
        kind: ErrorKind | None = None,
        fix: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if kind is not None:
            self.kind = kind
        if fix is not None:
            self.fix = fix
        self.details: dict[str, Any] = dict(details or {})
        self.problems: list[Problem] = []
        self.complete = True

    @property
    def message(self) -> str:
        return str(self.args[0]) if self.args else type(self).__name__

    def __str__(self) -> str:
        return self.message

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.kind]


class UsageError(ReconError):
    """A command was given a flag, an argument or a name it does not take."""

    kind: ErrorKind = "usage"


class ConfigError(ReconError, ValueError):
    """Invalid input. ``problems`` lists every finding, not just the first; ``complete`` is
    false when a finding stopped the checks that would have found more."""

    kind: ErrorKind = "config"

    def __init__(
        self,
        message: str,
        problems: list[Problem] | None = None,
        *,
        fix: str | None = None,
        complete: bool = True,
    ) -> None:
        super().__init__(message, fix=fix)
        self.problems = list(problems or [])
        self.complete = complete


class NotFoundError(ReconError, KeyError):
    """A named mission, archive or template does not exist."""

    kind: ErrorKind = "not_found"


class ConflictError(ReconError):
    """The operation would overwrite something that differs from what it would write."""

    kind: ErrorKind = "conflict"


class QueryError(ReconError):
    """SQL against an archive failed."""

    kind: ErrorKind = "query"


class CollectionError(ReconError):
    """Collection failed. ``kind`` says whether retrying (transient) or a new key (auth)
    helps; any other failure is ``run_failed``, or ``config`` when the mission must change."""

    kind: ErrorKind = "run_failed"


class NoRecordsError(CollectionError):
    """A collector found nothing to keep, for a reason worth saying: an ``extract:`` that
    resolves to nothing in a response that is not empty, items that are not objects,
    captures no filter kept, a tool that answered with no content. A source that had nothing
    says nothing; this is the other case.

    Raised before any record is yielded. The survey then keeps the table, empty, and warns
    with the message and ``fix`` as a ``problem_kind`` problem at ``collectors.<i>.<field>``;
    raised after a record, it fails the table as ``run_failed``, since keeping an empty
    table would drop what came first. A collector type of any package may raise it.
    """

    def __init__(self, message: str, *, problem_kind: str, field: str, fix: str) -> None:
        super().__init__(message, fix=fix)
        self.problem_kind = problem_kind
        self.field = field

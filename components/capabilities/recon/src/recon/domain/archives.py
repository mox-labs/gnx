"""The archive ports: where a survey's tables are written, and how they are queried.

An archive is one survey's output: a table per collector and source, a ``meta.yaml``, the
survey's event log and, unless the survey finished with every table, an incomplete marker. A
stream's window (:mod:`recon.domain.stream`) is an archive too, of one table, kept with the
other windows of its stream rather than with the survey's archives; one writer at a time
extends a stream, from :meth:`ArchiveStore.hold_stream` to :meth:`ArchiveStore.release_stream`,
and one survey at a time writes a mission's archives, from :meth:`ArchiveStore.hold_mission` to
:meth:`ArchiveStore.release_mission`.
The marker is written when the archive is created and removed only once the survey has
finished, so a survey that dies at any point, however it was stopped, leaves an archive
that reads incomplete. While a survey writes its archive it holds it, from :meth:`create`
to :meth:`ArchiveStore.release`, so a reader tells an archive being written from one whose
writer stopped without finishing it (:data:`ArchiveState`).
How it is stored (a directory of JSONL files) is :class:`ArchiveStore`'s decision; what
engine answers SQL over it, and the sandbox that engine runs in, is :class:`ArchiveQuery`'s.
The engine reads the tables where the store says they are (:meth:`ArchiveStore.tables`), and
nothing else of the store's layout. A reader that follows an archive while it is written
reads through :meth:`ArchiveStore.read_since`, which knows where a table being written is
and gives out only cursors, so no caller learns the store's files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable, Iterator, Sequence
    from pathlib import Path

    from recon.domain.capture import CaptureLog
    from recon.domain.errors import Problem
    from recon.domain.telemetry import EventSink

#: The ``format_version`` of the ``meta.yaml`` a survey writes.
META_FORMAT_VERSION = 2

#: An archive's name: when the survey started, to the microsecond, so names sort by time. A
#: directory under ``archive/`` that is not named so is not an archive.
ARCHIVE_ID_FORMAT = "%Y-%m-%d-%H%M%S-%f"

#: The survey's ``recon.v1.event`` lines, beside ``meta.yaml``; never a table.
EVENT_LOG_NAME = "telemetry.jsonl"

#: The most records one read of an archive returns unless told otherwise
#: (:meth:`ArchiveStore.read_since`): enough to keep up with a survey read once a second,
#: few enough that a read of a large archive's start stays small.
READ_MAX_RECORDS = 1000

#: The most bytes of records one read of an archive returns unless told otherwise: it stops
#: before the record that would pass it, and always returns at least one record.
READ_MAX_BYTES = 1024 * 1024

#: By default, the seconds a watcher waits between reads of an archive a survey is writing,
#: once it has read all there is so far (``progress_s``, the survey's own cadence, is 1.0).
WATCH_INTERVAL_S = 1.0

#: An archive's state, as the store and every document say it. ``writing``: a survey holds
#: it and is writing it. ``complete``: its survey finished it with every table.
#: ``incomplete``: no survey holds it and it was never completed (a table failed, or its
#: survey was stopped or crashed; :data:`Ended` says which). A reader who sees ``writing``
#: and reads again later sees it change; the other two never change. A state, where a
#: table's ``ok`` or ``error`` is an outcome.
ArchiveState = Literal["writing", "complete", "incomplete"]

#: How an archive's writer ended, once no writer holds it (None while it is ``writing``).
#: ``finished``: it ran to its end and wrote ``meta.yaml`` saying so, so an ``incomplete``
#: archive's tables failed, or it could not be marked complete. ``stopped``: it was stopped
#: on request (Ctrl-C, SIGTERM, a stream's stop) and wrote ``meta.yaml`` saying so. Both are
#: written by the writer, in ``meta.yaml``'s ``ended``. ``crashed``: it left no ``meta.yaml``,
#: so it died (killed, or its host went down) or could not write one (a full disk; such a
#: survey says so as it ends, ``archive_unfinished``): never written, read from that absence,
#: since the record that would tell the two apart is the one that is missing.
Ended = Literal["finished", "stopped", "crashed"]

#: What a table may keep beside it that is no table: ``pages``, its page log (each page's
#: token, once a page: :mod:`recon.domain.paginate`); ``keys``, its key index (each key's
#: content hash: :mod:`recon.domain.changes`). Written with the table once it is sealed, and
#: gone with its archive.
Sidecar = Literal["pages", "keys"]

#: Each sidecar's suffix to its table's output name: an output named so would be one.
SIDECARS: tuple[Sidecar, ...] = ("pages", "keys")

_NOT_IDENTIFIER = re.compile(r"[^a-zA-Z0-9_]")


def table_name(output: str) -> str:
    """The name SQL knows an output's table by: every character but an ASCII letter, a digit
    or ``_`` becomes ``_`` (``oa-works`` is ``oa_works``). SQL matches it without regard to
    case, so two outputs whose names agree here, ignoring case, would be one table."""
    return _NOT_IDENTIFIER.sub("_", output)


@dataclass(frozen=True)
class StoredTable:
    """One of an archive's tables, as its store describes it to whatever reads it.

    ``output`` is the collector's output name (SQL knows it as :func:`table_name`). ``file``
    holds its records, one JSON object per line. ``empty`` says it collected none: then the
    file is not to be read as records, and ``columns`` are the ones it keeps, its collector's
    ``normalize:`` spec's when that was known.

    ``sealed``: its writer renamed it into place as it ended. It is false for a table still
    being written, and stays false for one a survey that died was writing: its records are
    provisional, and ``file`` is where they will be
    once it is sealed (until then, where they are is the store's to know). A sealed table
    gains and loses no line. How it ended is the survey's to say (``meta.yaml``, and the
    ``recon.collect`` end event), not the table's.
    """

    output: str
    file: Path
    empty: bool = False
    columns: tuple[str, ...] = ()
    sealed: bool = True


@dataclass(frozen=True)
class TableEnds:
    """What a table holds, read from where its store keeps it: how many records, and its
    first and last (None when it has none)."""

    records: int
    first: dict[str, Any] | None = None
    last: dict[str, Any] | None = None


@dataclass(frozen=True)
class TableRecord:
    """One record of one of an archive's tables, by the table's output name."""

    table: str
    record: dict[str, Any]


@dataclass(frozen=True)
class NewRecords:
    """What :meth:`ArchiveStore.read_since` read: the records written since the cursor it
    was given, in whole, and the cursor to read on from.

    ``cursor`` is opaque: only the store that gave it reads it. ``exhausted`` names each
    table that is sealed and whose last record is now read, so this reader gets no more of it
    and what it read of it stands; ``retracted`` each table this cursor had read from that
    failed, so the records read from it are void. Each table is told exhausted or retracted
    once along a chain of cursors. (``sealed`` is a table's own state, :class:`StoredTable`'s:
    a table is exhausted for a reader once it is sealed and that reader has read it all.)
    ``more``: a bound (records or bytes) ended the read before all that was written by then
    was read; read again at once.
    """

    records: list[TableRecord]
    cursor: str
    exhausted: list[str] = field(default_factory=list)
    retracted: list[str] = field(default_factory=list)
    more: bool = False


class ArchiveStore(Protocol):
    def create(self, mission_dir: Path, archive_id: str, *, stream: str | None = None) -> Path:
        """A new archive of the mission, named ``archive_id``, holding only the incomplete
        marker, and held by this writer until :meth:`release`: its state is ``writing``. With
        ``stream``, a window of that stream (``archive_id`` is a window's id), which this
        writer holds (:meth:`hold_stream`). Raises ReconError (``store``) when the id is
        taken."""
        ...

    def hold_stream(self, mission_dir: Path, stream: str) -> None:
        """Hold the mission's ``stream`` for this writer until :meth:`release_stream` or its
        death: no other writer, in this process or another on this host, can hold it
        meanwhile, so the windows it adds take ids no other writer takes. Raises
        ReconError (``busy``) when another writer holds it."""
        ...

    def release_stream(self, mission_dir: Path, stream: str) -> None:
        """Let go of a stream :meth:`hold_stream` held; one this writer does not hold is left
        as it is."""
        ...

    def hold_mission(self, mission_dir: Path) -> None:
        """Hold the mission for one survey until :meth:`release_mission` or its death: no
        other survey, in this process or another on this host, can hold it meanwhile, so two
        surveys of a mission never overlap. Raises ReconError (``busy``), naming the holder's
        pid in ``details`` when it is known, while another holds it."""
        ...

    def release_mission(self, mission_dir: Path) -> None:
        """Let go of a mission :meth:`hold_mission` held; one this store does not hold is
        left as it is."""
        ...

    def stream_state(self, mission_dir: Path, stream: str) -> ArchiveState | None:
        """``writing`` while a writer holds the stream; else its last window's state; None
        for a stream with no window."""
        ...

    def streams(self, mission_dir: Path) -> list[str]:
        """The names of the mission's streams, sorted."""
        ...

    def table_ends(self, archive: Path, output: str) -> TableEnds:
        """What the table ``output`` holds as stored now, sealed or not: its records counted,
        its first and its last read from the file. A stream's window says what it holds from
        this, so it never claims a record its table lacks."""
        ...

    def size(self, archive: Path) -> int:
        """The bytes ``archive`` takes where it is stored."""
        ...

    def retire(self, archive: Path, tombstone: dict[str, Any]) -> None:
        """Remove a sealed window of a stream, as its stream's ``retain:`` says, and keep
        ``tombstone`` (what it was, and why it went) in its place, so its absence reads as
        retention. The tombstone is kept before the window goes. For a stream this writer
        holds."""
        ...

    def retired(self, mission_dir: Path, stream: str) -> list[dict[str, Any]]:
        """The tombstones of the stream's retired windows, oldest first, one per window."""
        ...

    def retired_count(self, mission_dir: Path, stream: str) -> int:
        """How many windows of the stream were retired, counted without reading each."""
        ...

    def last_record(self, archive: Path, output: str) -> dict[str, Any] | None:
        """The last whole record of the table ``output``, sealed or left unsealed by a writer
        that died; None when it has none. A torn last line, a write a dying writer cut short,
        is first moved aside (it is no record), and every whole line is left as it was. For an
        archive no writer holds."""
        ...

    def release(self, archive: Path) -> None:
        """Let go of an archive :meth:`create` made, once the survey is done writing it,
        however it ended: its state is then ``complete`` or ``incomplete``. An archive this
        store does not hold is left as it is. A writer that dies lets go of it too."""
        ...

    def state(self, archive: Path) -> ArchiveState:
        """Where the writing of ``archive`` stands, as any reader, in any process on this
        host, sees it now."""
        ...

    def write_table(
        self,
        archive: Path,
        output: str,
        records: Generator[dict[str, Any], None, None],
        *,
        columns: Sequence[str] = (),
        keep_on_error: bool = False,
    ) -> int:
        """Write every record to the table ``output`` and return how many there were.

        While the table is written, a record is written whole or not at all: a reader of the
        live table sees whole records, soon after each is given, and never part of one. The
        table is sealed when this returns, and the survey's ``recon.collect`` end event says
        how it ended. Until then a snapshot table's records are provisional: on any error,
        including one the records raise, the table is retracted (nothing of it is left, the
        records a reader already saw included) and the error propagates. A stream's window
        writes with ``keep_on_error``, since what its source sent cannot be fetched again: on
        an error the records raise, or an interrupt, the records written so far are sealed,
        then the error propagates. ``records`` is closed either way. ``columns`` are the
        table's columns when they are known (a ``normalize:`` spec's): a table that collected
        nothing keeps them, so a query of them still binds.
        """
        ...

    def write_sidecar(
        self, archive: Path, output: str, sidecar: Sidecar, lines: Iterable[dict[str, Any]]
    ) -> None:
        """Keep ``lines`` as the table ``output``'s ``sidecar``, whole or not at all, in place
        of any it had. A sidecar is never one of :meth:`tables`, and goes with its archive.
        Raises OSError when it cannot."""
        ...

    def read_sidecar(
        self, archive: Path, output: str, sidecar: Sidecar
    ) -> Iterator[dict[str, Any]] | None:
        """The lines of the table ``output``'s ``sidecar``, in the order written; None when it
        has none."""
        ...

    def read_table(self, archive: Path, output: str) -> Iterator[dict[str, Any]] | None:
        """The records of the sealed table ``output``, in the order written (none for a table
        that collected nothing); None when ``archive`` has no such sealed table."""
        ...

    def capture_log(self, archive: Path) -> CaptureLog: ...

    def event_log(self, archive: Path) -> EventSink:
        """The sink that keeps the survey's events in the archive."""
        ...

    def mark_complete(self, archive: Path) -> None:
        """Remove the incomplete marker. Raises OSError when it cannot."""
        ...

    def write_meta(self, archive: Path, meta: dict[str, Any]) -> None:
        """Raises OSError when ``meta.yaml`` cannot be written; nothing partial is left."""
        ...

    def abandoned(self, mission_dir: Path) -> list[Path]:
        """What a survey or a stream that died while making an archive or a window of the
        mission left behind, which :meth:`archives` never lists: each only once its maker is
        gone on this host. Nothing is removed."""
        ...

    def archives(self, mission_dir: Path, *, stream: str | None = None) -> list[Path]:
        """The mission's archives, oldest first: its directories named by
        :data:`ARCHIVE_ID_FORMAT`, and no other. With ``stream``, that stream's windows
        instead, by index, at the cost of that stream's windows alone."""
        ...

    def read_meta(self, archive: Path) -> dict[str, Any]:
        """``meta.yaml`` in the current format, whichever format it was written in; one
        from a newer recon as written, for the reader to take the fields it knows.
        ``format_version`` is None when it is missing or unreadable."""
        ...

    def read_since(
        self, archive: Path, cursor: str | None, *, max_records: int, max_bytes: int
    ) -> NewRecords:
        """The records of ``archive`` written since ``cursor`` (None: from the start), at
        most ``max_records`` of them and ``max_bytes`` of their lines, whole records only and at
        least one, from its sealed tables and the ones being written alike, table by table
        in output-name order; ``more`` when a bound cut it. Raises UsageError for a cursor
        this store did not give for this archive, and for a ``max_records`` or ``max_bytes``
        below 1."""
        ...

    def tables(self, archive: Path) -> list[StoredTable]:
        """The archive's tables, by output name, those not yet sealed included. Raises
        QueryError when the archive's tables are in a layout this store does not read (one a
        newer recon wrote)."""
        ...


@dataclass(frozen=True)
class Rows:
    """``warnings``: a ``table_unreadable`` problem for each table SQL could not read, which
    the query did not need."""

    columns: list[str]
    rows: list[tuple[Any, ...]]
    warnings: list[Problem] = field(default_factory=list)


class ArchiveQuery(Protocol):
    def execute(self, archive: Path, sql: str) -> Rows:
        """Run ``sql`` against the archive's sealed tables: a table not yet sealed is not one
        SQL knows.

        The SQL is not trusted: it reads nothing outside the mission's archives, loads no
        extension and reaches no network. A table that cannot be read as one does not stop
        the others: a query that does not use it runs, with a warning naming it. Raises
        NotFoundError when the archive has no tables, QueryError when the SQL fails, reaches
        outside or uses a table that cannot be read.
        """
        ...

    def execute_across(self, archives: Sequence[Path], sql: str) -> Rows:
        """Run ``sql`` as :meth:`execute` does, against the sealed tables of every one of
        ``archives`` (a stream's windows, in order, in one directory) at once: each table is
        the union of its records in each, by column name. Raises as :meth:`execute` does."""
        ...

    def unreadable(self, archive: Path) -> list[Problem]:
        """A ``table_unreadable`` problem for each of the archive's tables SQL cannot read,
        with the fix :meth:`execute` gives; none for an archive with no tables."""
        ...

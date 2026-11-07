"""The archive ports: how collected tables are landed, read back, and queried.

An archive is one survey's output: a table per collector and source, its meta (the survey's
record of itself: when, under which config, and how each table ended), the survey's event
log and, with ``preserve_raw``, its capture log. A stream's window (:mod:`recon.core.domain.stream`)
is an archive too, of one table, kept with the other windows of its stream. Every archive is
named by an :class:`ArchiveRef`, never by where it is: how archives are stored is the
adapter's decision alone (owner ruling G5), and no port here carries a location.

Three ports, each hiding one decision:

- :class:`Landing` (with :class:`Hold` and :class:`Landed`): how a write is made durable and
  exclusive. A writer holds a mission's surveys, or one stream, and lands archives under that
  hold; one survey of a mission at a time, one writer of a stream at a time.
- :class:`Archives`: how what was landed is laid out and read back.
- :class:`ArchiveQuery`: what engine answers SQL over archives, and the sandbox it runs in.

**The commit.** An archive's meta is recorded whole or not at all (:meth:`Landed.record`), and
it is the archive's commit point: a table counts as landed, with what its outcome carries (a
range's checkpoint among it, :mod:`recon.core.domain.ranges`), once a meta that names it is
recorded with ``ended`` set. A writer that dies before that leaves tables no reader counts
(``ended: crashed``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable, Iterator, Mapping, Sequence
    from contextlib import AbstractContextManager

    from recon.core.domain.capture import CaptureLog
    from recon.core.domain.errors import Problem
    from recon.core.domain.telemetry import EventSink

#: The ``format_version`` of the meta a survey writes.
META_FORMAT_VERSION = 2

#: An archive's id: when the survey started, to the microsecond, so ids sort by time.
ARCHIVE_ID_FORMAT = "%Y-%m-%d-%H%M%S-%f"

#: The output names an archive keeps for its own logs (its event log, and its capture log):
#: no collector's output may take one.
RESERVED_OUTPUTS = frozenset({"telemetry", "captures"})

#: The most records one read of an archive returns unless told otherwise
#: (:meth:`Archives.read_since`): enough to keep up with a survey read once a second, few
#: enough that a read of a large archive's start stays small.
READ_MAX_RECORDS = 1000

#: The most bytes of records one read of an archive returns unless told otherwise: it stops
#: before the record that would pass it, and always returns at least one record.
READ_MAX_BYTES = 1024 * 1024

#: By default, the seconds a watcher waits between reads of an archive a survey is writing,
#: once it has read all there is so far (``progress_s``, the survey's own cadence, is 1.0).
WATCH_INTERVAL_S = 1.0

#: An archive's state. ``writing``: a writer holds it. ``complete``: its writer finished it
#: with every table. ``incomplete``: no writer holds it and it was never completed (a table
#: failed, or its writer was stopped or crashed; :data:`Ended` says which). A reader who sees
#: ``writing`` and reads again later sees it change; the other two never change.
ArchiveState = Literal["writing", "complete", "incomplete"]

#: How an archive's writer ended, once no writer holds it (None while it is ``writing``).
#: ``finished``: it ran to its end and recorded its meta saying so. ``stopped``: it was stopped
#: on request (Ctrl-C, SIGTERM, a stream's stop) and recorded its meta saying so. ``crashed``:
#: it recorded no meta that says how it ended, so it died, or could not record one: never
#: written, read from that absence.
Ended = Literal["finished", "stopped", "crashed"]

#: What a table may keep beside it that is no table: ``pages``, its page log (each page's
#: token, once a page: :mod:`recon.core.domain.paginate`); ``keys``, its told index (each key's
#: content hash: :mod:`recon.core.domain.changes`). Kept with the table, and gone with its archive.
Sidecar = Literal["pages", "keys"]

#: Each sidecar's suffix to its table's output name: an output named so would be one.
SIDECARS: tuple[Sidecar, ...] = ("pages", "keys")

_NOT_IDENTIFIER = re.compile(r"[^a-zA-Z0-9_]")


@dataclass(frozen=True)
class ArchiveRef:
    """One archive, or one window of a stream, by reference: its mission, its stream (None for
    a survey's archive) and its id, which is what ``--archive`` takes. ``index`` is its place
    in the order its mission's archives (or its stream's windows) were made, whatever the
    clock said; None for an archive made before indexes. It is not part of its identity."""

    mission: str
    id: str
    stream: str | None = None
    index: int | None = field(default=None, compare=False)

    @property
    def rank(self) -> tuple[int, int, str]:
        """Where it stands in its mission's order: :func:`archive_rank`."""
        return archive_rank(self.id, self.index)


def archive_rank(archive: str | None, index: int | None) -> tuple[int, int, str]:
    """Where an archive (or a window) stands in its mission's order: an indexed one by its
    index, after every one made before indexes, which stand by their ids (when they began);
    none (before the first) before all."""
    if archive is None:
        return (-1, 0, "")
    return (0, 0, archive) if index is None else (1, index, "")


def table_name(output: str) -> str:
    """The name SQL knows an output's table by: every character but an ASCII letter, a digit
    or ``_`` becomes ``_`` (``oa-works`` is ``oa_works``). SQL matches it without regard to
    case, so two outputs whose names agree here, ignoring case, would be one table."""
    return _NOT_IDENTIFIER.sub("_", output)


@dataclass(frozen=True)
class TableEnds:
    """What a table holds as landed so far: how many records, and its first and last (None
    when it has none)."""

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
    """What :meth:`Archives.read_since` read: the records written since the cursor it was
    given, in whole, and the cursor to read on from.

    ``cursor`` is opaque: only the adapter that gave it reads it. ``exhausted`` names each
    table that is sealed and whose last record is now read, so this reader gets no more of it
    and what it read of it stands; ``retracted`` each table this cursor had read from that
    failed, so the records read from it are void. Each table is told exhausted or retracted
    once along a chain of cursors. ``more``: a bound (records or bytes) ended the read before
    all that was written by then was read; read again at once.
    """

    records: list[TableRecord]
    cursor: str
    exhausted: list[str] = field(default_factory=list)
    retracted: list[str] = field(default_factory=list)
    more: bool = False


@dataclass(frozen=True)
class Abandoned:
    """What a writer that died while making an archive left behind, which no listing shows
    and nothing reads: ``where`` it is and ``fix``, the command that removes it, both said for
    a person by the adapter that knows its medium. recon never reads either back."""

    where: str
    fix: str


class Landing(Protocol):
    """Where collected tables are made durable, one writer at a time."""

    def hold(self, mission: str, *, stream: str | None = None) -> AbstractContextManager[Hold]:
        """Hold the mission's surveys (``stream`` None), or the mission's stream ``stream``,
        for this writer alone until the block ends or the writer dies: no other writer, in
        this process or another on this host, holds the same meanwhile. A survey's hold and a
        stream's are apart: a survey and another writer's stream of one mission may run
        together. Raises ReconError (``busy``) on entering, naming the holder's pid in
        ``details`` when it is known, while another holds it; UsageError for a name that is
        not a mission name."""
        ...


class Hold(Protocol):
    """A writer's hold of a mission's surveys, or of one of its streams: what is landed, what
    is derived from it once sealed, and what is let go, under it."""

    def begin(self, archive_id: str) -> AbstractContextManager[Landed]:
        """A new archive of what is held, named ``archive_id``: a survey's archive (given the
        mission's next index, one more than the last given), or a window of the stream held
        (``archive_id`` its window id, its index in it). It reads ``writing`` from the moment
        it can be listed until the block ends, or its writer dies, and is then ``complete`` or
        ``incomplete``. Raises ReconError (``store``) when the id is taken."""
        ...

    def derive(self, ref: ArchiveRef) -> AbstractContextManager[Landed]:
        """A sealed archive of what is held, opened again to keep what is derived from its own
        tables: a table of its own (a change table), a table's sidecar (its told index), and
        its meta recorded again with what was found. It reads ``writing`` until the block ends,
        so no reader takes what it holds for whole before what is derived from it is kept; what
        it was marked (complete or not) is left as it was, and so is every table it sealed.
        Raises NotFoundError for an archive never sealed, UsageError for one that is not of
        what is held, ReconError (``busy``) while another writer holds it."""
        ...

    def retire(self, ref: ArchiveRef, tombstone: Mapping[str, Any]) -> None:
        """Let go of a sealed archive of what is held, as a ``retain:`` says: its tables and
        everything kept with them go, and ``tombstone`` (what it was, what its tables
        committed, and why it went) is kept in its place, before it goes, so its absence reads
        as retention and :meth:`Archives.retired` gives it. A writer that dies part way leaves
        it listed, and retiring it again finishes the work."""
        ...


class Landed(Protocol):
    """One archive being written, from :meth:`Hold.begin` until its block ends."""

    @property
    def ref(self) -> ArchiveRef:
        """The archive, with its index."""
        ...

    def write(
        self,
        output: str,
        records: Generator[dict[str, Any], None, None],
        *,
        columns: Sequence[str] = (),
        keep_on_error: bool = False,
    ) -> int:
        """Write every record to the table ``output`` and return how many there were.

        While the table is written, a record is written whole or not at all: a reader of the
        live table sees whole records, soon after each is given, and never part of one. The
        table is sealed when this returns. Until then a snapshot table's records are
        provisional: on any error, including one the records raise, the table is retracted
        (nothing of it is left, the records a reader already saw included) and the error
        propagates. A stream's window writes with ``keep_on_error``, since what its source
        sent cannot be fetched again: on an error the records raise, or an interrupt, the
        records written so far are sealed, then the error propagates. ``records`` is closed
        either way. ``columns`` are the table's columns when they are known (a
        ``normalize:`` spec's): a table that collected nothing keeps them, so a query of them
        still binds."""
        ...

    def ends(self, output: str) -> TableEnds:
        """What the table ``output`` holds as landed now, sealed or not: its records counted,
        its first and its last. A stream's window says what it holds from this, so it never
        claims a record its table lacks."""
        ...

    def keep(self, output: str, sidecar: Sidecar, lines: Iterable[dict[str, Any]]) -> None:
        """Keep ``lines`` as the table ``output``'s ``sidecar``, whole or not at all, in place
        of any it had. Raises OSError when it cannot."""
        ...

    def mark(self, output: str, sidecar: Sidecar, line: dict[str, Any]) -> None:
        """Add ``line`` to the table ``output``'s ``sidecar`` while the table is written (a
        stream's window keeps its page log so: :mod:`recon.core.domain.paginate`), in one write,
        once every record given to the table before it is landed: a sidecar line is never kept
        without the records before it, so a window its writer died in says no more than it
        holds. Raises OSError when it cannot."""
        ...

    def captures(self) -> CaptureLog:
        """The archive's capture log (``preserve_raw``)."""
        ...

    def events(self) -> EventSink:
        """The sink that keeps the archive's events in it."""
        ...

    def record(self, meta: Mapping[str, Any]) -> None:
        """Record ``meta`` as the archive's meta, whole or not at all, in place of the one
        recorded before: the archive's commit point (the module docstring). Raises OSError
        when it cannot; the one recorded before is then left as it was."""
        ...

    def complete(self) -> None:
        """Mark the archive complete: every table succeeded and its meta is recorded. Raises
        OSError when it cannot; the archive then reads ``incomplete``."""
        ...


class Archives(Protocol):
    """What was landed, read back: a mission's archives and streams, in order, and each
    archive's state, meta and tables. Readers take no hold: an archive a writer holds reads
    ``writing``, and what it has landed so far is readable."""

    def archives(self, mission: str, *, stream: str | None = None) -> list[ArchiveRef]:
        """The mission's archives, in the order they were made, each with its index; with
        ``stream``, that stream's windows instead, at the cost of that stream's windows alone.
        Neither lists what a writer that died while making one left (:meth:`abandoned`), nor
        what was retired (:meth:`retired`)."""
        ...

    def streams(self, mission: str) -> list[str]:
        """The names of the mission's streams, sorted."""
        ...

    def state(self, ref: ArchiveRef) -> ArchiveState:
        """Where the writing of ``ref`` stands, as any reader, in any process on this host,
        sees it now."""
        ...

    def stream_state(self, mission: str, stream: str) -> ArchiveState | None:
        """``writing`` while a writer holds the stream; else its last window's state; None for
        a stream with no window."""
        ...

    def meta(self, ref: ArchiveRef) -> dict[str, Any]:
        """The archive's meta, in the current format whichever format it was recorded in; one
        from a newer recon as recorded, for the reader to take the fields it knows.
        ``format_version`` is None when none is recorded or it cannot be read."""
        ...

    def records(
        self, ref: ArchiveRef, output: str, *, unsealed: bool = False
    ) -> Iterator[dict[str, Any]] | None:
        """The records of the sealed table ``output``, in the order written (none for a table
        that collected nothing); None when the archive has no such sealed table. With
        ``unsealed``, a table its writer died writing gives its whole records, and is left as
        it is."""
        ...

    def sidecar(
        self, ref: ArchiveRef, output: str, sidecar: Sidecar
    ) -> Iterator[dict[str, Any]] | None:
        """The lines of the table ``output``'s ``sidecar``, in the order written; None when it
        has none. A last line a writer that died cut short is no line of it."""
        ...

    def last_record(self, ref: ArchiveRef, output: str) -> dict[str, Any] | None:
        """The last whole record of the table ``output``, sealed or left unsealed by a writer
        that died; None when it has none. A torn last record, a write a dying writer cut
        short, is first set aside (it is no record), and every whole one is left as it was.
        For an archive no writer holds."""
        ...

    def read_since(
        self,
        ref: ArchiveRef,
        cursor: str | None,
        *,
        max_records: int,
        max_bytes: int,
        tables: Sequence[str] | None = None,
    ) -> NewRecords:
        """The records of ``ref`` written since ``cursor`` (None: from the start), at most
        ``max_records`` of them and ``max_bytes`` of their bytes, whole records only and at
        least one, from its sealed tables and the ones being written alike, table by table
        in output-name order; ``more`` when a bound cut it. With ``tables``, of the tables of
        those output names alone (a change table: what a subscription reads). Raises
        UsageError for a cursor this adapter did not give for this archive, and for a
        ``max_records`` or ``max_bytes`` below 1."""
        ...

    def size(self, ref: ArchiveRef) -> int:
        """The bytes the archive takes where it is kept: what ``retain: {bytes}`` counts."""
        ...

    def retired(self, mission: str, *, stream: str | None = None) -> list[dict[str, Any]]:
        """The tombstones of the mission's retired archives (with ``stream``, of that stream's
        retired windows), oldest first, one per archive: each as :meth:`Hold.retire` was given
        it, with its ``id`` and ``index``."""
        ...

    def retired_count(self, mission: str, stream: str) -> int:
        """How many windows of the stream were retired, counted without reading each."""
        ...

    def abandoned(self, mission: str) -> list[Abandoned]:
        """What a writer that died while making one of the mission's archives or windows left
        behind, each only once its maker is gone on this host. Nothing is removed."""
        ...


class Store(Landing, Archives, Protocol):
    """Where a session's acts land and are read back from: one adapter keeping both sides of
    the one storage decision (``recon.open(landing=...)``)."""


@dataclass(frozen=True)
class Rows:
    """``warnings``: a ``table_unreadable`` problem for each table SQL could not read, which
    the query did not need."""

    columns: list[str]
    rows: list[tuple[Any, ...]]
    warnings: list[Problem] = field(default_factory=list)


class ArchiveQuery(Protocol):
    def run(
        self, archives: Sequence[ArchiveRef], sql: str, *, baseline: ArchiveRef | None = None
    ) -> Rows:
        """Run ``sql`` against the sealed tables of ``archives`` (one archive, or a stream's
        windows, in order): each table is the union of its records in each, by column name,
        named by :func:`table_name`. A table not yet sealed is not one SQL knows. Each table's
        records are also ``json.<table>``, each as its JSON text (``json``), for records SQL
        cannot make columns of. Over one archive, each table's records are also
        ``lines.<table>``, numbered from 1 in the order they were landed (``line``), which is
        how a row is cited. With ``baseline``, that archive's tables are ``baseline.<table>``
        too, so a query can ask what changed since it.

        The SQL is not trusted: it reads nothing but the tables of the archives named, loads
        no extension, writes nothing and reaches no network. A table that cannot be read as
        one does not stop the others: a query that does not use it runs, with a warning
        naming it. Raises NotFoundError when the archives have no tables, QueryError when the
        SQL fails, reaches outside or uses a table that cannot be read.
        """
        ...

    def unreadable(self, archive: ArchiveRef) -> list[Problem]:
        """A ``table_unreadable`` problem for each of the archive's tables SQL cannot read,
        with the fix :meth:`run` gives; none for an archive with no tables."""
        ...

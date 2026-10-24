"""The archive ports: where a survey's tables are written, and how they are queried.

An archive is one survey's output: a table per collector and source, a ``meta.yaml``, the
survey's event log and, unless the survey finished with every table, an incomplete marker.
The marker is written when the archive is created and removed only once the survey has
finished, so a survey that dies at any point, however it was stopped, leaves an archive
that reads incomplete. While a survey writes its archive it holds it, from :meth:`create`
to :meth:`ArchiveStore.release`, so a reader tells an archive being written from one whose
writer stopped without finishing it (:data:`WriteState`).
How it is stored (a directory of JSONL files) is :class:`ArchiveStore`'s decision; what
engine answers SQL over it, and the sandbox that engine runs in, is :class:`ArchiveQuery`'s.
The engine reads the tables where the store says they are (:meth:`ArchiveStore.tables`), and
nothing else of the store's layout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence
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

#: Where the writing of an archive stands. ``writing``: a survey holds it and is writing it.
#: ``complete``: its survey finished it with every table. ``incomplete``: no survey holds it
#: and it was never completed (a table failed, or its survey was stopped or died). A reader
#: who sees ``writing`` and reads again later sees it change; the other two never change.
WriteState = Literal["writing", "complete", "incomplete"]

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

    ``sealed`` is false for a table still being written, or left unfinished by a survey that
    died while writing it: its records are provisional, and ``file`` is where they will be
    once it is sealed (until then, where they are is the store's to know). A sealed table
    gains and loses no line. How it ended is the survey's to say (``meta.yaml``, and the
    ``recon.collect`` end event), not the table's.
    """

    output: str
    file: Path
    empty: bool = False
    columns: tuple[str, ...] = ()
    sealed: bool = True


class ArchiveStore(Protocol):
    def create(self, mission_dir: Path, archive_id: str) -> Path:
        """A new archive of the mission, named ``archive_id``, holding only the incomplete
        marker, and held by this writer until :meth:`release`: its state is ``writing``."""
        ...

    def release(self, archive: Path) -> None:
        """Let go of an archive :meth:`create` made, once the survey is done writing it,
        however it ended: its state is then ``complete`` or ``incomplete``. An archive this
        store does not hold is left as it is. A writer that dies lets go of it too."""
        ...

    def state(self, archive: Path) -> WriteState:
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
    ) -> int:
        """Write every record to the table ``output`` and return how many there were.

        While the table is written, a record is written whole or not at all: a reader of the
        live table sees whole records, soon after each is given, and never part of one. The
        table is sealed when this returns, and the survey's ``recon.collect`` end event says
        how it ended. Until then its records are provisional: on any error, including one the
        records raise, the table is retracted (nothing of it is left, the records a reader
        already saw included) and the error propagates. ``records`` is closed either way.
        ``columns`` are the table's columns when they are known (a ``normalize:`` spec's): a
        table that collected nothing keeps them, so a query of them still binds.
        """
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

    def archives(self, mission_dir: Path) -> list[Path]:
        """The mission's archives, oldest first: its directories named by
        :data:`ARCHIVE_ID_FORMAT`, and no other."""
        ...

    def read_meta(self, archive: Path) -> dict[str, Any]:
        """``meta.yaml`` in the current format, whichever format it was written in; one
        from a newer recon as written, for the reader to take the fields it knows.
        ``format_version`` is None when it is missing or unreadable."""
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

    def unreadable(self, archive: Path) -> list[Problem]:
        """A ``table_unreadable`` problem for each of the archive's tables SQL cannot read,
        with the fix :meth:`execute` gives; none for an archive with no tables."""
        ...

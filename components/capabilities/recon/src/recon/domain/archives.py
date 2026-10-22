"""The archive ports: where a survey's tables are written, and how they are queried.

An archive is one survey's output: a table per collector and source, a ``meta.yaml``, the
survey's event log and, unless the survey finished with every table, an incomplete marker.
The marker is written when the archive is created and removed only once the survey has
finished, so a survey that dies at any point, however it was stopped, leaves an archive
that reads incomplete.
How it is stored (a directory of JSONL files) is :class:`ArchiveStore`'s decision; what
engine answers SQL over it, and the sandbox that engine runs in, is :class:`ArchiveQuery`'s.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from recon.domain.capture import CaptureLog
    from recon.domain.errors import Problem

#: The ``format_version`` of the ``meta.yaml`` a survey writes.
META_FORMAT_VERSION = 2

#: The survey's ``recon.v1.event`` lines, beside ``meta.yaml``; never a table.
EVENT_LOG_NAME = "telemetry.jsonl"

_NOT_IDENTIFIER = re.compile(r"[^a-zA-Z0-9_]")


def table_name(output: str) -> str:
    """The name SQL knows an output's table by: every character but an ASCII letter, a digit
    or ``_`` becomes ``_`` (``oa-works`` is ``oa_works``). SQL matches it without regard to
    case, so two outputs whose names agree here, ignoring case, would be one table."""
    return _NOT_IDENTIFIER.sub("_", output)


class ArchiveStore(Protocol):
    def create(self, mission_dir: Path, archive_id: str) -> Path:
        """A new archive of the mission, named ``archive_id``, holding only the incomplete
        marker."""
        ...

    def write_table(
        self, archive: Path, output: str, records: Generator[dict[str, Any], None, None]
    ) -> int:
        """Write every record to the table ``output`` and return how many there were.

        The table appears whole or not at all: on any error, including one the records
        raise, nothing is left and the error propagates. ``records`` is closed either way.
        """
        ...

    def capture_log(self, archive: Path) -> CaptureLog: ...

    def mark_complete(self, archive: Path) -> None:
        """Remove the incomplete marker. Raises OSError when it cannot."""
        ...

    def write_meta(self, archive: Path, meta: dict[str, Any]) -> None:
        """Raises OSError when ``meta.yaml`` cannot be written; nothing partial is left."""
        ...

    def archives(self, mission_dir: Path) -> list[Path]:
        """The mission's archives, oldest first."""
        ...

    def read_meta(self, archive: Path) -> dict[str, Any]:
        """``meta.yaml`` in the current format, whichever format it was written in;
        ``format_version`` is None when it is missing or unreadable."""
        ...

    def is_incomplete(self, archive: Path) -> bool: ...


@dataclass(frozen=True)
class Rows:
    """``warnings``: a ``table_unreadable`` problem for each table SQL could not read, which
    the query did not need."""

    columns: list[str]
    rows: list[tuple[Any, ...]]
    warnings: list[Problem] = field(default_factory=list)


class ArchiveQuery(Protocol):
    def tables(self, archive: Path) -> list[str]:
        """The names SQL can use for the archive's tables."""
        ...

    def execute(self, archive: Path, sql: str) -> Rows:
        """Run ``sql`` against the archive's tables.

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

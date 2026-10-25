"""Subscriptions (round-4 rulings R1-R4): what changed, read by whoever is told of it.

**Reading** (R1). An output's change rows are read across the mission's archives (a stream's,
across its windows), oldest first, each through the archive store's ``read_since`` of its
change table alone. A change table is read only once it is sealed (the store never gives a row
of one being written), and every row of a sealed change table was told, once: a table whose
writer died before it sealed told nothing, and the next survey or window tells its rows
(``recon.domain.changes``). So the rows of the sealed change tables, in archive order, are each
change once. The reader holds the cursor, as the end-to-end argument has it (SE:c11): recon
keeps no state of a reader's, and a reader that reads again from a cursor gets the same rows,
with the same ``id``s, so delivery is at least once and a reader drops what it has seen by its
``id``.

The cursor names the archive it is in, how many of its change table's rows were read, the
store's own cursor there, and whether that table is done: read to its end, or, its archive
no longer being written, never sealed. An archive is done when it is; the read goes on into
the next, so archives whose tables changed nothing cost a read nothing. ``retain:`` may remove
an archive before a reader reached it: the read that passes such archives says so
(``retired_range``), so none is passed silently.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from recon.application.changes import canonical
from recon.domain.archives import ARCHIVE_ID_FORMAT
from recon.domain.changes import changes_output
from recon.domain.documents import ChangeRow, WindowRange
from recon.domain.errors import UsageError
from recon.domain.stream import window_index

if TYPE_CHECKING:
    from pathlib import Path

    from recon.domain.archives import ArchiveState, ArchiveStore
    from recon.domain.changes import ChangeOp

#: A change cursor names its format first: one this recon does not know is refused.
_CURSOR = "c1"

#: What a row's ``id`` is keyed with, so it is never a digest recon makes for anything else.
_ID_PERSON = b"recon.row"


@dataclass(frozen=True)
class Position:
    """Where a reader of an output's change rows is: in ``archive`` (None: before the first),
    past ``row`` rows of its change table, at the store's cursor ``at`` there, and whether that
    table is ``done`` (no row of it is still to come). Positions order by archive, then row:
    an archive's id sorts by when it began (a window's, by its index)."""

    archive: str | None = None
    row: int = 0
    at: str | None = None
    done: bool = False

    def order(self) -> tuple[str, int]:
        return (self.archive or "", self.row)


@dataclass(frozen=True)
class ChangeRead:
    """What one read found: the rows, where the reader is after them, whether a bound cut the
    read (``more``), the state of the last archive read, and the archives retired before the
    reader reached them."""

    rows: list[ChangeRow]
    position: Position
    more: bool
    state: ArchiveState | None
    retired: WindowRange | None


class ChangeReader:
    """Reads the change rows of ``output`` in the mission at ``mission_dir`` (named
    ``mission``): across its archives, or across the windows of ``stream`` when the output is
    a stream's."""

    def __init__(
        self,
        archives: ArchiveStore,
        mission_dir: Path,
        mission: str,
        output: str,
        stream: str | None,
    ) -> None:
        self._archives = archives
        self._mission_dir = mission_dir
        self._mission = mission
        self._output = output
        self._stream = stream
        self._table = changes_output(output)

    def read(self, position: Position | None, *, max_records: int, max_bytes: int) -> ChangeRead:
        """The rows after ``position`` (None: from the first archive), at most
        ``max_records`` of them and about ``max_bytes`` of their lines, at least one when any
        is there. Raises UsageError for a position in no archive the mission has or had."""
        if max_records < 1 or max_bytes < 1:
            raise UsageError(
                f"a read's max_records and max_bytes are each 1 or more; got {max_records} and "
                f"{max_bytes}",
                fix="read with each of 1 or more, or leave them at their defaults (1000, 1 MiB)",
            )
        archives = self._archives.archives(self._mission_dir, stream=self._stream)
        names = [archive.name for archive in archives]
        stones: list[str] | None = None

        def retired_between(after: str | None, before: str | None) -> list[str]:
            nonlocal stones
            if stones is None:
                stones = sorted(
                    str(stone.get("id"))
                    for stone in self._archives.retired(self._mission_dir, self._stream)
                )
            return [
                s for s in stones if (after is None or s > after) and (before is None or s < before)
            ]

        at = position or Position()
        retired: WindowRange | None = None
        if at.archive is None:
            index = 0
            gone = retired_between(None, names[0] if names else None)
            retired = _range(gone)
        elif at.archive in names:
            index = names.index(at.archive)
        else:
            gone = retired_between(None, None)
            if at.archive not in gone:
                raise UsageError(
                    f"the cursor is in archive {at.archive}, which mission {self._mission} "
                    "neither has nor retired: it is not this mission's",
                    fix="pass on the cursor the last read of this output returned, unchanged, or "
                    "read without one to start from the first archive",
                    details={"archive": at.archive},
                )
            later = [i for i, name in enumerate(names) if name > at.archive]
            next_name = names[later[0]] if later else None
            retired = _range(retired_between(at.archive, next_name) + [at.archive])
            if not later:
                return ChangeRead([], Position(at.archive, at.row, at.at, True), False, None,
                                  retired)  # fmt: skip
            index, at = later[0], Position(names[later[0]])
        rows: list[ChangeRow] = []
        records, size = max_records, max_bytes
        more = False
        state: ArchiveState | None = None
        while index < len(archives):
            archive = archives[index]
            state = self._archives.state(archive)  # before its rows: see done, below
            if not at.done:
                read = self._archives.read_since(archive, at.at, max_records=records,
                                                 max_bytes=size, tables=[self._table])  # fmt: skip
                for offset, found in enumerate(read.records):
                    rows.append(self._row(archive.name, at.row + offset, found.record))
                    records -= 1
                    size -= len(json.dumps(found.record, default=str)) + 1
                # Done once read to its end; or, no more read and its archive no longer
                # written, never to be sealed: a writer that died told nothing (the next
                # survey or window tells its rows).
                done = self._table in read.exhausted or (not read.more and state != "writing")
                at = Position(archive.name, at.row + len(read.records), read.cursor, done)
                if read.more:
                    more = True
                    break
                if not done:
                    break  # its survey may yet seal its change table: nothing after it yet
            if index + 1 == len(archives):
                break
            following = archives[index + 1]
            gone = retired_between(archive.name, following.name)
            if gone and (rows or retired is not None):
                more = True  # the next read says the gap before any row after it
                break
            if gone:
                retired = _range(gone)
            index, at = index + 1, Position(following.name)
            if records < 1 or size < 1:
                more = True
                break
        return ChangeRead(rows, at, more, state, retired)

    def _row(self, archive: str, row: int, change: dict[str, Any]) -> ChangeRow:
        key = change.get("key")
        record = change.get("record")
        op = change.get("op")
        return ChangeRow(
            id=row_id(archive, self._output, row, str(op), key),
            archive=archive,
            op=cast("ChangeOp", op),  # recon wrote it: one of the ops
            key=key if isinstance(key, dict) else None,
            record=record if isinstance(record, dict) else None,
        )

    def encode(self, position: Position) -> str:
        """The opaque cursor at ``position``: only :meth:`decode` reads it."""
        body = {
            "mission": self._mission,
            "output": self._output,
            "archive": position.archive,
            "row": position.row,
            "at": position.at,
            "done": position.done,
        }
        return _CURSOR + json.dumps(body, separators=(",", ":")).encode().hex()

    def decode(self, cursor: str) -> Position:
        """The position ``cursor`` holds. Raises UsageError for a cursor recon did not give for
        a read of change rows, or gave for another mission or output."""
        body: Any = None
        if cursor.startswith(_CURSOR):
            try:
                body = json.loads(bytes.fromhex(cursor.removeprefix(_CURSOR)))
            except ValueError:
                body = None
        fresh = "read without a cursor to start from the first archive"
        if not _well_formed(body):
            raise UsageError(
                "the cursor is not one recon gave for a read of change rows: pass on the cursor "
                "the last read returned, unchanged",
                fix=fresh,
            )
        if (body["mission"], body["output"]) != (self._mission, self._output):
            raise UsageError(
                f"the cursor is for the change rows of {body['output']} in mission "
                f"{body['mission']}, not of {self._output} in {self._mission}",
                fix=f"read {body['output']} of {body['mission']} with it, or {fresh}",
                details={"mission": body["mission"], "output": body["output"]},
            )
        return Position(body["archive"], body["row"], body["at"], body["done"])


def row_id(archive: str, output: str, row: int, op: str, key: Any) -> str:
    """The ``id`` of a change row: a digest of its archive, its output, its place in its change
    table, its op and its key. Its place is in it because one change table can tell a key's
    update twice (a window telling an earlier one's records, ``untold``): op and key alone
    would name two rows. Never a digest of the record's content, whose hash never leaves
    recon."""
    named = canonical([archive, output, row, op, key])
    digest = hashlib.blake2b(digest_size=16, person=_ID_PERSON)
    digest.update(named.encode("utf-8", "surrogatepass"))
    return digest.hexdigest()


def _range(ids: list[str]) -> WindowRange | None:
    """The run of archive ids ``ids`` (sorted), or None for none."""
    if not ids:
        return None
    ordered = sorted(ids)
    return WindowRange(first=ordered[0], last=ordered[-1], count=len(ordered))


def _well_formed(body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    archive, row = body.get("archive"), body.get("row")
    return (
        isinstance(body.get("mission"), str)
        and isinstance(body.get("output"), str)
        and (archive is None or (isinstance(archive, str) and _is_archive_id(archive)))
        and isinstance(row, int)
        and not isinstance(row, bool)
        and row >= 0
        and isinstance(body.get("at"), str | None)
        and isinstance(body.get("done"), bool)
    )


def _is_archive_id(name: str) -> bool:
    """Whether ``name`` is an archive's id or a window's."""
    if window_index(name) is not None:
        return True
    try:
        datetime.strptime(name, ARCHIVE_ID_FORMAT)
    except ValueError:
        return False
    return True

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
from recon.domain.documents import (
    ChangeRow,
    ChangesSince,
    SubscriptionDocument,
    SubscriptionFailing,
    SubscriptionStatus,
    WindowRange,
)
from recon.domain.errors import NotFoundError, UsageError
from recon.domain.stream import window_index
from recon.domain.subscriptions import SubscriptionState, matches

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from recon.application.plan import Plan
    from recon.domain.archives import ArchiveState, ArchiveStore
    from recon.domain.changes import ChangeOp
    from recon.domain.clock import Clock
    from recon.domain.documents import SubscriptionOutcome
    from recon.domain.subscriptions import SubscriptionSpec, SubscriptionStore

#: A change cursor names its format first: one this recon does not know is refused.
_CURSOR = "c1"

#: What a row's ``id`` is keyed with, so it is never a digest recon makes for anything else.
_ID_PERSON = b"recon.row"


@dataclass(frozen=True)
class Position:
    """Where a reader of an output's change rows is: in ``archive`` (None: before the first),
    past ``row`` rows of its change table, at the store's cursor ``at`` there, and whether that
    table is ``done`` (no row of it is still to come). Positions order by archive, then row,
    then done: an archive's id sorts by when it began (a window's, by its index)."""

    archive: str | None = None
    row: int = 0
    at: str | None = None
    done: bool = False

    def order(self) -> tuple[str, int, bool]:
        return (self.archive or "", self.row, self.done)


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

    def known(self, position: Position) -> bool:
        """Whether ``position`` is in an archive the mission has, or had (retired), or before
        the first."""
        if position.archive is None:
            return True
        archives = self._archives.archives(self._mission_dir, stream=self._stream)
        if position.archive in {archive.name for archive in archives}:
            return True
        stones = self._archives.retired(self._mission_dir, self._stream)
        return position.archive in {str(stone.get("id")) for stone in stones}

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


class Subscriptions:
    """A mission's named subscriptions (ruling R3), a convenience for a subscriber that keeps
    no state of its own: recon keeps the cursor each acked, beside the mission
    (:class:`~recon.domain.subscriptions.SubscriptionStore`), and moves it only on an ``ack``,
    never back. One consumer at a time holds a subscription; a second is refused ``busy``.

    ``prepare`` is a mission's plan (raising as a survey's would); ``reader`` the reader of an
    output's change rows in it."""

    def __init__(
        self,
        *,
        store: SubscriptionStore,
        clock: Clock,
        mission_dir: Callable[[str], Path],
        prepare: Callable[[str], Plan],
        reader: Callable[[str, str, Plan], ChangeReader],
    ) -> None:
        self._store = store
        self._clock = clock
        self._mission_dir = mission_dir
        self._prepare = prepare
        self._reader = reader

    def read(
        self,
        name: str,
        subscription: str,
        cursor: str | None = None,
        *,
        max_records: int,
        max_bytes: int,
    ) -> ChangesSince:
        """The rows of the subscription's output that its ``match:`` keeps, from the cursor it
        acked last (or ``cursor``, when given: a subscriber that keeps its own), once. Moves
        nothing: what is acked moves only on :meth:`ack`. Raises NotFoundError for a
        subscription the config does not declare, ReconError ``busy`` while another consumer
        holds it."""
        plan = self._prepare(name)
        spec = _declared(plan, name, subscription)
        reader = self._reader(name, spec.output, plan)
        mission = self._mission_dir(name)
        self._store.hold(mission, subscription)
        try:
            if cursor is not None:
                position: Position | None = reader.decode(cursor)
            else:
                position = _acked(reader, self._store.state(mission, subscription), name, spec)
            read = reader.read(position, max_records=max_records, max_bytes=max_bytes)
        finally:
            self._store.release(mission, subscription)
        rows = [row for row in read.rows if matches(spec.match, _change(row))]
        return ChangesSince(
            mission=name,
            output=spec.output,
            subscription=subscription,
            rows=rows,
            count=len(rows),
            cursor=reader.encode(read.position),
            more=read.more,
            state=read.state,
            retired_range=read.retired,
        )

    def ack(self, name: str, subscription: str, cursor: str) -> SubscriptionDocument:
        """Keep ``cursor`` as what the subscription has acked: the rows before it are done
        with. An ack is monotone: a cursor before the one acked is refused, the same one changes
        nothing (``unchanged``). Raises UsageError for a cursor of another output or mission,
        or before the one acked; ReconError ``busy`` while another consumer holds it."""
        plan = self._prepare(name)
        spec = _declared(plan, name, subscription)
        reader = self._reader(name, spec.output, plan)
        position = reader.decode(cursor)
        if not reader.known(position):
            raise UsageError(
                f"the cursor is in archive {position.archive}, which mission {name} neither has "
                "nor retired: it is not this mission's",
                fix=f"ack the cursor the last `recon subscribe {name} {subscription}` returned",
                details={"subscription": subscription, "archive": position.archive},
            )
        mission = self._mission_dir(name)
        self._store.hold(mission, subscription)
        try:
            state = self._store.state(mission, subscription)
            acked = _acked(reader, state, name, spec)
            if acked is not None and position.order() < acked.order():
                raise UsageError(
                    f"the cursor is before the one subscription {subscription} acked: an ack "
                    "moves it on, never back",
                    fix=f"ack the cursor of a later read (`recon subscribe {name} "
                    f"{subscription}` reads from the one acked)",
                    details={
                        "subscription": subscription,
                        "acked": _where(acked),
                        "got": _where(position),
                    },
                )
            if acked is not None and position.order() == acked.order():
                return _document(name, subscription, "unchanged", state, self._dead(name, spec))
            kept = SubscriptionState(
                output=spec.output, cursor=cursor, acked_at=self._clock.now().isoformat()
            )
            self._store.keep(mission, subscription, kept)
        finally:
            self._store.release(mission, subscription)
        return _document(name, subscription, "acked", kept, self._dead(name, spec))

    def remove(self, name: str, subscription: str) -> SubscriptionDocument:
        """Remove what is kept of the subscription: its acked cursor and its dead letters
        (``removed``), or say nothing was (``absent``). Its declaration in the config stays,
        so it reads from the first archive again. Raises ReconError ``busy`` while a consumer
        holds it."""
        mission = self._mission_dir(name)
        self._store.hold(mission, subscription)
        try:
            state = self._store.state(mission, subscription)
            removed = self._store.remove(mission, subscription)
        finally:
            self._store.release(mission, subscription)
        output = state.output if state is not None else None
        return SubscriptionDocument(
            mission=name, subscription=subscription, outcome="removed" if removed else "absent",
            output=output, cursor=None, acked_at=None,
        )  # fmt: skip

    def statuses(self, name: str, plan: Plan | None) -> list[SubscriptionStatus]:
        """Each subscription ``plan`` declares, and each one something is kept of."""
        mission = self._mission_dir(name)
        declared = {s.name: s for s in plan.config.subscriptions} if plan and plan.config else {}
        listed = []
        for each in sorted(set(declared) | set(self._store.names(mission))):
            state = self._store.state(mission, each)
            spec = declared.get(each)
            listed.append(
                SubscriptionStatus(
                    name=each,
                    output=spec.output if spec else state.output if state else None,
                    declared=spec is not None,
                    cursor=state.cursor if state else None,
                    acked_at=state.acked_at if state else None,
                    failing=_failing(state),
                    dead_letters=self._store.dead_letters(mission, each),
                )
            )
        return listed

    def _dead(self, name: str, spec: SubscriptionSpec) -> int:
        return self._store.dead_letters(self._mission_dir(name), spec.name)


def _declared(plan: Plan, name: str, subscription: str) -> SubscriptionSpec:
    """The subscription the mission's config declares by that name; NotFoundError naming those
    it does declare."""
    assert plan.config is not None
    for spec in plan.config.subscriptions:
        if spec.name == subscription:
            return spec
    known = [spec.name for spec in plan.config.subscriptions]
    raise NotFoundError(
        f"mission {name!r} declares no subscription {subscription!r}; it declares: "
        f"{', '.join(known) or '(none)'}",
        fix="name one of them, or declare it under subscriptions: (recon --skill -r subscriptions)",
        details={"value": subscription, "allowed": known},
    )


def _acked(
    reader: ChangeReader, state: SubscriptionState | None, name: str, spec: SubscriptionSpec
) -> Position | None:
    """Where the subscription acked last; None when nothing is acked. Raises UsageError when
    what is kept is of another output than the config's: the subscription was redeclared."""
    if state is None or state.cursor is None:
        return None
    if state.output != spec.output:
        raise UsageError(
            f"subscription {spec.name} acked rows of {state.output}, and the config now has it "
            f"read {spec.output}",
            fix=f"`recon unsubscribe {name} {spec.name}` removes what it acked, so it reads "
            f"{spec.output} from its first archive; or name it as it was",
            details={"subscription": spec.name, "acked_output": state.output},
        )
    return reader.decode(state.cursor)


def _change(row: ChangeRow) -> dict[str, Any]:
    return {"op": row.op, "key": row.key, "record": row.record}


def _where(position: Position) -> dict[str, Any]:
    return {"archive": position.archive, "row": position.row}


def _failing(state: SubscriptionState | None) -> SubscriptionFailing | None:
    if state is None or state.failing is None:
        return None
    failing = state.failing
    return SubscriptionFailing(id=failing.id, attempts=failing.attempts, error=failing.error)


def _document(
    name: str,
    subscription: str,
    outcome: SubscriptionOutcome,
    state: SubscriptionState | None,
    dead_letters: int,
) -> SubscriptionDocument:
    return SubscriptionDocument(
        mission=name,
        subscription=subscription,
        outcome=outcome,
        output=state.output if state else None,
        cursor=state.cursor if state else None,
        acked_at=state.acked_at if state else None,
        failing=_failing(state),
        dead_letters=dead_letters,
    )

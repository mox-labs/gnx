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

import contextlib
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from recon.application.changes import canonical
from recon.domain.archives import ARCHIVE_ID_FORMAT, READ_MAX_BYTES, archive_rank
from recon.domain.changes import changes_output
from recon.domain.documents import (
    ChangeRow,
    ChangesSince,
    DeliverDocument,
    Delivery,
    DeliveryRun,
    SubscriptionDocument,
    SubscriptionFailing,
    SubscriptionStatus,
    WindowRange,
)
from recon.domain.errors import NotFoundError, Problem, ReconError, UsageError
from recon.domain.stream import window_index
from recon.domain.subscriptions import Failing, SubscriptionState, clean, matches

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from recon.application.plan import Plan
    from recon.domain.archives import ArchiveState, ArchiveStore
    from recon.domain.changes import ChangeOp
    from recon.domain.clock import Clock
    from recon.domain.documents import SubscriptionOutcome
    from recon.domain.subscriptions import (
        Delivered,
        Deliverer,
        SubscriptionSpec,
        SubscriptionStore,
    )

#: A change cursor names its format first: one this recon does not know is refused.
_CURSOR = "c1"

#: What a change cursor's signature is keyed with besides the mission's key.
_CURSOR_PERSON = b"recon.cursor"

#: What a row's ``id`` is keyed with, so it is never a digest recon makes for anything else.
_ID_PERSON = b"recon.row"


@dataclass(frozen=True)
class Position:
    """Where a reader of an output's change rows is: in ``archive`` (None: before the first),
    past ``row`` rows of its change table, at the store's cursor ``at`` there, and whether that
    table is ``done`` (no row of it is still to come). Positions order by archive, then row,
    then done: archives in the order they were made, by index, never by the clock."""

    archive: str | None = None
    row: int = 0
    at: str | None = None
    done: bool = False
    #: The archive's index (:meth:`~recon.domain.archives.ArchiveStore.indexes`); None for one
    #: made before indexes.
    index: int | None = None

    def order(self) -> tuple[tuple[int, int, str], int, bool]:
        return (archive_rank(self.archive, self.index), self.row, self.done)


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
    a stream's. Its cursors are signed with the mission's key (``secret``): one edited, however
    plausible (a real archive, a row past its table's end), is refused, never read on from."""

    def __init__(
        self,
        archives: ArchiveStore,
        mission_dir: Path,
        mission: str,
        output: str,
        stream: str | None,
        secret: bytes,
    ) -> None:
        self._secret = secret
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
        indexes = self._archives.indexes(self._mission_dir, stream=self._stream)
        ranks = [archive_rank(name, indexes.get(name)) for name in names]
        stones: list[tuple[tuple[int, int, str], str]] | None = None

        def retired_between(after: tuple[int, int, str] | None,
                            before: tuple[int, int, str] | None) -> list[str]:  # fmt: skip
            nonlocal stones
            if stones is None:
                found = self._archives.retired(self._mission_dir, self._stream)
                stones = sorted(_stone(stone, indexes) for stone in found)
            return [
                name for rank, name in stones
                if (after is None or rank > after) and (before is None or rank < before)
            ]  # fmt: skip

        at = position or Position()
        retired: WindowRange | None = None
        if at.archive is None:
            index = 0
            gone = retired_between(None, ranks[0] if ranks else None)
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
            mine = archive_rank(at.archive, at.index)
            later = [i for i, rank in enumerate(ranks) if rank > mine]
            next_rank = ranks[later[0]] if later else None
            retired = _range([at.archive, *retired_between(mine, next_rank)])
            if not later:
                return ChangeRead([], Position(at.archive, at.row, at.at, True, at.index), False,
                                  None, retired)  # fmt: skip
            index = later[0]
            at = Position(names[index], index=indexes.get(names[index]))
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
                at = Position(archive.name, at.row + len(read.records), read.cursor, done,
                              indexes.get(archive.name))  # fmt: skip
                if read.more:
                    more = True
                    break
                if not done:
                    break  # its survey may yet seal its change table: nothing after it yet
            if index + 1 == len(archives):
                break
            following = archives[index + 1]
            gone = retired_between(ranks[index], ranks[index + 1])
            if gone and (rows or retired is not None):
                more = True  # the next read says the gap before any row after it
                break
            if gone:
                retired = _range(gone)
            index, at = index + 1, Position(following.name, index=indexes.get(following.name))
            if records < 1 or size < 1:
                more = True
                break
        return ChangeRead(rows, at, more, state, retired)

    def unread(
        self, position: Position | None, *, within: float | None = None, now: datetime | None = None
    ) -> set[str]:
        """The archives (a stream's windows) whose sealed change table holds a row
        ``position`` has not passed (None: none passed): the one it is in, unless its table is
        done, and each after it whose change table is not empty. With ``within``, those
        written to their end no more than ``within`` seconds before ``now`` alone: one that does
        not say when is past it, so it never holds retention for ever (Erlang, re-check)."""
        archives = self._archives.archives(self._mission_dir, stream=self._stream)
        names = [archive.name for archive in archives]
        start = 0
        if position is not None and position.archive is not None:
            if position.archive in names:
                start = names.index(position.archive) + (1 if position.done else 0)
            else:  # retired: what is after it
                indexes = self._archives.indexes(self._mission_dir, stream=self._stream)
                mine = archive_rank(position.archive, position.index)
                ranks = [archive_rank(name, indexes.get(name)) for name in names]
                start = next((i for i, rank in enumerate(ranks) if rank > mine), len(names))
        unread = set()
        for archive in archives[start:]:
            rows = self._archives.read_table(archive, self._table)
            if rows is None or next(iter(rows), None) is None:
                continue
            if within is None or now is None:
                unread.add(archive.name)
                continue
            made = _made_at(self._archives.read_meta(archive))
            if made is not None and (now - made).total_seconds() <= within:
                unread.add(archive.name)
        return unread

    def row_id(self, archive: str, row: int, op: str, key: Any) -> str:
        """The ``id`` of the row at ``row`` of ``archive``'s change table (:func:`row_id`)."""
        return row_id(self._secret, self._mission, archive, self._output, row, op, key)

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
            id=self.row_id(archive, row, str(op), key),
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
            "index": position.index,
        }
        text = json.dumps(body, separators=(",", ":")).encode()
        return f"{_CURSOR}{text.hex()}.{self._sign(text)}"

    def _sign(self, text: bytes) -> str:
        digest = hashlib.blake2b(digest_size=16, key=self._secret, person=_CURSOR_PERSON)
        digest.update(text)
        return digest.hexdigest()

    def decode(self, cursor: str) -> Position:
        """The position ``cursor`` holds. Raises UsageError for a cursor recon did not give for
        a read of change rows, or gave for another mission or output."""
        body: Any = None
        hexed, _, signed = cursor.removeprefix(_CURSOR).partition(".")
        if cursor.startswith(_CURSOR):
            try:
                text = bytes.fromhex(hexed)
                if hmac.compare_digest(signed, self._sign(text)):
                    body = json.loads(text)
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
        return Position(body["archive"], body["row"], body["at"], body["done"],
                        body.get("index"))  # fmt: skip


def _made_at(meta: dict[str, Any]) -> datetime | None:
    """When an archive (a window) was written to its end, as its ``meta.yaml`` says; None when
    it does not say."""
    stream = meta.get("stream")
    stamp = stream.get("closed_at") if isinstance(stream, dict) else meta.get("timestamp")
    if isinstance(stamp, str):
        with contextlib.suppress(ValueError):
            stamp = datetime.fromisoformat(stamp)
    if isinstance(stamp, datetime):
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)
    return None


def row_id(
    secret: bytes, mission: str, archive: str, output: str, row: int, op: str, key: Any
) -> str:
    """The ``id`` of a change row: a digest, keyed with the mission's key (``secret``), of its
    mission, its archive, its output, its place in its change table, its op and its key. Keyed,
    because it travels with every delivery: a key of few values (a code) is not found from it
    by hashing guesses (Vector, review N1); its mission in it, so the same row of two missions
    is two ids (Dijkstra). Its place is in it because one change table can tell a key's update
    twice (a window telling an earlier one's records, ``untold``): op and key alone would name
    two rows. Never a digest of the record's content, whose hash never leaves recon."""
    named = canonical([mission, archive, output, row, op, key])
    digest = hashlib.blake2b(digest_size=16, key=secret, person=_ID_PERSON)
    digest.update(named.encode("utf-8", "surrogatepass"))
    return digest.hexdigest()


def _range(ids: list[str]) -> WindowRange | None:
    """The run of archive ids ``ids``, in the order they were made, or None for none."""
    if not ids:
        return None
    return WindowRange(first=ids[0], last=ids[-1], count=len(ids))


def _stone(stone: dict[str, Any], indexes: dict[str, int]) -> tuple[tuple[int, int, str], str]:
    """A tombstone's archive's place in the order, and its id: by the index it kept, or the
    one its mission gave it (``indexes``), or a window's in its id; else by its id (one made
    before indexes)."""
    name = str(stone.get("id"))
    index = stone.get("index")
    if type(index) is not int:
        index = indexes.get(name, window_index(name))
    return archive_rank(name, index), name


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
        and (body.get("index") is None or type(body.get("index")) is int)
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
    output's change rows in it; ``deliverer`` gives a delivery to a command, run in
    ``project``."""

    def __init__(
        self,
        *,
        store: SubscriptionStore,
        clock: Clock,
        mission_dir: Callable[[str], Path],
        prepare: Callable[[str], Plan],
        reader: Callable[[str, str, Plan], ChangeReader],
        deliverer: Deliverer,
        project: Path,
    ) -> None:
        self._store = store
        self._clock = clock
        self._mission_dir = mission_dir
        self._prepare = prepare
        self._reader = reader
        self._deliverer = deliverer
        self._project = project

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

    def held(self, name: str, plan: Plan, stream: str | None) -> dict[int, tuple[str, set[str]]]:
        """For each subscription ``plan`` declares on an output of ``stream`` (None: a snapshot
        entry's), by its position in ``subscriptions:``: its name, and the archives (windows)
        holding change rows it has not acked, which retention keeps. What it keeps that is not
        of this output, or does not read, holds everything: nothing it acked can be trusted."""
        assert plan.config is not None
        mission = self._mission_dir(name)
        held: dict[int, tuple[str, set[str]]] = {}
        for index, spec in enumerate(plan.config.subscriptions):
            if spec.output not in plan.changes or plan.changes[spec.output] != stream:
                continue
            reader = self._reader(name, spec.output, plan)
            try:
                position = _acked(reader, self._store.state(mission, spec.name), name, spec)
            except UsageError:
                position = None
            unread = reader.unread(position, within=spec.max_lag, now=self._clock.now())
            held[index] = (spec.name, unread)
        return held

    def _dead(self, name: str, spec: SubscriptionSpec) -> int:
        return self._store.dead_letters(self._mission_dir(name), spec.name)

    def deliver(
        self, name: str, subscription: str | None = None, *, plan: Plan | None = None
    ) -> DeliverDocument:
        """Give the rows each subscription with ``deliver:`` has not acked to its command, or
        the one named's alone (ruling R4): at most ``max_per_tick`` one at a time, each acked
        once its command exits 0, then the rest it can count before its ``deadline`` as one
        row counting them. A row its command fails waits, holding the rows after it, for the
        next delivery, and after ``attempts`` is set aside as a dead letter, so a poison row
        never holds the subscription for good. ``plan`` is the mission's, when the caller has
        planned it (a tick: the config it checked). Raises NotFoundError for a subscription the
        config does not declare, UsageError for one with no ``deliver:``."""
        plan = plan if plan is not None else self._prepare(name)
        assert plan.config is not None
        declared = list(enumerate(plan.config.subscriptions))
        if subscription is not None:
            spec = _declared(plan, name, subscription)
            if spec.deliver is None:
                raise UsageError(
                    f"subscription {subscription} has no deliver:, so there is nothing to give "
                    "its rows to: its subscriber reads them",
                    fix=f"recon subscribe {name} {subscription}, or give it deliver: {{command: "
                    "<program>, args: [...]}",
                    details={"subscription": subscription},
                )
            declared = [(i, s) for i, s in declared if s.name == subscription]
        runs: list[DeliveryRun] = []
        warnings: list[Problem] = []
        for index, spec in declared:
            if spec.deliver is None:
                continue
            run, found = self._deliver(name, index, spec, plan)
            runs.append(run)
            warnings += found
        return DeliverDocument(
            mission=name, deliveries=runs, warnings=[w.document() for w in warnings]
        )

    def _deliver(
        self, name: str, index: int, spec: SubscriptionSpec, plan: Plan
    ) -> tuple[DeliveryRun, list[Problem]]:
        at = f"subscriptions.{index}.deliver"
        mission = self._mission_dir(name)
        try:
            self._store.hold(mission, spec.name)
        except ReconError as busy:
            if busy.kind != "busy":
                raise
            fix = busy.fix or "let the other consumer finish, then deliver again"
            warning = Problem(at, str(busy), kind="subscription_busy", fix=fix, got=spec.name)
            return DeliveryRun(subscription=spec.name, output=spec.output,
                               skipped_by="busy"), [warning]  # fmt: skip
        try:
            return _Delivery(self, name, at, spec, plan).run()
        except (OSError, ReconError) as exc:  # said, never the delivery's (or a tick's) end
            fix = (exc.fix if isinstance(exc, ReconError) else None) or (
                "free space where the mission is, or make it writable, then: recon deliver "
                f"{name}"
            )  # fmt: skip
            warning = Problem(at, f"subscription {spec.name} was not delivered: {exc}",
                              kind="delivery_failed", fix=fix, got=spec.name)  # fmt: skip
            state = self._store.state(mission, spec.name)
            return DeliveryRun(subscription=spec.name, output=spec.output, more=True,
                               cursor=state.cursor if state else None), [warning]  # fmt: skip
        finally:
            self._store.release(mission, spec.name)


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


#: How many rows a delivery reads at a time while it looks for the next row its ``match:``
#: keeps, and while it counts the rows it gives as one.
_SCAN_ROWS = 100
_COUNT_ROWS = 1000

#: The most characters of what a command said when it failed that recon keeps.
_ERROR_CHARS = 500


@dataclass
class _Next:
    """The next row a ``match:`` keeps after a position: the positions before and after it;
    or none (``row`` None) as far as was read, ``before`` where reading stopped, and ``cut``
    when the deadline stopped it short."""

    row: ChangeRow | None
    before: Position | None
    after: Position | None
    retired: WindowRange | None
    cut: bool = False


class _Delivery:
    """One delivery of one subscription, held by its consumer lock while it runs."""

    def __init__(
        self, owner: Subscriptions, name: str, at: str, spec: SubscriptionSpec, plan: Plan
    ) -> None:
        assert spec.deliver is not None
        self._owner = owner
        self._store = owner._store
        self._name = name
        self._at = at
        self._spec = spec
        self._deliver = spec.deliver
        self._mission = owner._mission_dir(name)
        self._reader = owner._reader(name, spec.output, plan)
        self._clock = owner._clock
        self._started = owner._clock.monotonic()
        self._warnings: list[Problem] = []
        self._retired: WindowRange | None = None
        self._acked: str | None = None
        self._acked_at: str | None = None

    def _left(self) -> float:
        return self._deliver.deadline - (self._clock.monotonic() - self._started)

    def run(self) -> tuple[DeliveryRun, list[Problem]]:
        state = self._store.state(self._mission, self._spec.name)
        position = _acked(self._reader, state, self._name, self._spec)
        acked = position
        self._acked = state.cursor if state is not None else None
        self._acked_at = state.acked_at if state is not None else None
        failing = state.failing if state is not None else None
        delivered = dead = coalesced = 0
        more = False
        while delivered + dead < self._deliver.max_per_tick:
            if self._left() <= 0:
                more = True
                break
            found = self._next(position)
            if found.row is None or found.before is None or found.after is None:
                position = found.before or position
                more = found.cut
                break
            row, before, after = found.row, found.before, found.after
            done = self._give(self._change(row))
            if done.ok:
                delivered += 1
                position, failing = after, None
                self._keep(position, None)
                continue
            attempts = failing.attempts if failing is not None and failing.id == row.id else 0
            attempts += 1
            error = clean(done.error, _ERROR_CHARS)
            if attempts >= self._deliver.attempts:
                self._dead_letter(row, attempts, error)
                dead += 1
                position, failing = after, None
                self._keep(position, None)
                continue
            failing = Failing(id=row.id, attempts=attempts, error=error)
            position = before
            self._keep(position, failing)
            self._warnings.append(self._failed(row.id, attempts, error))
            more = True
            break
        else:
            position, coalesced, more = self._coalesce(position)
        if position is not None and (acked is None or position.order() != acked.order()):
            self._keep(position, failing)
        if self._retired is not None:
            self._warnings.append(self._gone(self._retired))
        return (
            DeliveryRun(
                subscription=self._spec.name,
                output=self._spec.output,
                delivered=delivered,
                coalesced=coalesced,
                dead_lettered=dead,
                cursor=None if position is None else self._reader.encode(position),
                more=more,
                failing=None
                if failing is None
                else SubscriptionFailing(id=failing.id, attempts=failing.attempts,
                                         error=failing.error),
                retired_range=self._retired,
            ),
            self._warnings,
        )  # fmt: skip

    def _read(self, position: Position | None, rows: int) -> ChangeRead:
        read = self._reader.read(position, max_records=rows, max_bytes=READ_MAX_BYTES)
        self._retired = self._retired or read.retired
        return read

    def _next(self, position: Position | None) -> _Next:
        """The next row after ``position`` (None: from the first archive) that the
        subscription's ``match:`` keeps."""
        at = position or Position()
        while True:
            if self._left() <= 0:
                return _Next(None, at, at, self._retired, cut=True)
            read = self._read(at, _SCAN_ROWS)
            for k, row in enumerate(read.rows):
                if not matches(self._spec.match, _change(row)):
                    continue
                before = at if k == 0 else self._read(at, k).position
                after = self._read(before, 1).position
                return _Next(row, before, after, self._retired)
            at = read.position
            if not read.more:
                return _Next(None, at, at, self._retired)

    def _coalesce(self, position: Position | None) -> tuple[Position | None, int, bool]:
        """The rows past ``max_per_tick``, counted as far as the deadline lets, given as one
        row counting them; the position after them once it is, how many, and whether rows are
        left for the next delivery."""
        at = position
        count = 0
        first: ChangeRow | None = None
        last: ChangeRow | None = None
        cut = False
        while True:
            if self._left() <= 0:
                cut = True
                break
            read = self._read(at, _COUNT_ROWS)
            for row in read.rows:
                if matches(self._spec.match, _change(row)):
                    count += 1
                    first = first or row
                    last = row
            at = read.position
            if not read.more:
                break
        if not count or first is None or last is None:
            return at, 0, cut
        summary = self._summary(first, last, count)
        done = self._give(summary)
        if not done.ok:
            self._warnings.append(
                Problem(
                    self._at,
                    f"subscription {self._spec.name}'s command failed the row counting {count} "
                    f"more change(s): {clean(done.error, _ERROR_CHARS)}",
                    kind="delivery_failed",
                    fix=f"they are counted again at the next delivery (recon deliver "
                    f"{self._name}, or the schedule's next tick); run the command by hand with "
                    "a delivery on its standard input to see why",
                    got=summary.id,
                )
            )
            return position, 0, True
        self._keep(at, None)
        return at, count, cut

    def _give(self, delivery: Delivery) -> Delivered:
        payload = (json.dumps(delivery.document(), ensure_ascii=True) + "\n").encode("ascii")
        timeout = max(0.001, min(self._deliver.timeout, self._left()))
        return self._owner._deliverer.deliver(
            self._deliver.argv, payload, timeout_s=timeout, cwd=self._owner._project
        )

    def _change(self, row: ChangeRow) -> Delivery:
        chars = self._deliver.max_chars
        record = row.record or {}
        return Delivery(
            kind="change",
            mission=self._name,
            subscription=self._spec.name,
            output=self._spec.output,
            id=row.id,
            archive=row.archive,
            op=row.op,
            key=None if row.key is None else {k: cleaned(v, chars) for k, v in row.key.items()},
            fields={field: cleaned(record.get(field), chars) for field in self._deliver.fields},
        )

    def _summary(self, first: ChangeRow, last: ChangeRow, count: int) -> Delivery:
        return Delivery(
            kind="summary",
            mission=self._name,
            subscription=self._spec.name,
            output=self._spec.output,
            id=_summary_id(first.id, last.id),
            archive=last.archive,
            count=count,
            first=first.id,
            last=last.id,
        )

    def _keep(self, position: Position | None, failing: Failing | None) -> None:
        """Keep ``position`` as acked, and ``failing``: ``acked_at`` moves only with the
        cursor."""
        cursor = None if position is None else self._reader.encode(position)
        if cursor != self._acked:
            self._acked, self._acked_at = cursor, self._clock.now().isoformat()
        self._store.keep(
            self._mission,
            self._spec.name,
            SubscriptionState(
                output=self._spec.output, cursor=cursor, acked_at=self._acked_at, failing=failing
            ),
        )

    def _dead_letter(self, row: ChangeRow, attempts: int, error: str) -> None:
        self._store.dead_letter(
            self._mission,
            self._spec.name,
            {
                "id": row.id,
                "archive": row.archive,
                "output": self._spec.output,
                "op": row.op,
                "attempts": attempts,
                "error": error,
                "set_aside_at": self._clock.now().isoformat(),
                "delivery": self._change(row).document(),
            },
        )
        self._warnings.append(
            Problem(
                self._at,
                f"subscription {self._spec.name}'s command failed row {row.id} {attempts} "
                f"time(s), so it is set aside as a dead letter and the rows after it go on: "
                f"{error}",
                kind="delivery_dead_lettered",
                fix="the row is kept with the subscription's dead letters (`recon status "
                f"{self._name}` counts them; recon --skill -r subscriptions says where); fix "
                "the command, then give it the row's delivery by hand",
                got=row.id,
            )
        )

    def _failed(self, row: str, attempts: int, error: str) -> Problem:
        return Problem(
            self._at,
            f"subscription {self._spec.name}'s command failed row {row} (attempt {attempts} of "
            f"{self._deliver.attempts}): {error}",
            kind="delivery_failed",
            fix=f"the row is given again at the next delivery (recon deliver {self._name}, or "
            f"the schedule's next tick) and set aside after {self._deliver.attempts} attempts; "
            "run the command by hand with a delivery on its standard input to see why",
            got=row,
        )

    def _gone(self, retired: WindowRange) -> Problem:
        return Problem(
            self._at,
            f"{retired.count} archive(s) from {retired.first} were retired by retain: before "
            f"subscription {self._spec.name} was delivered their rows, which are gone",
            kind="changes_retired",
            fix="keep archives longer than deliveries lag: raise the collector's retain:, or "
            "deliver more often",
            got=retired.count,
        )


def _summary_id(first: str, last: str) -> str:
    """A summary's ``id``: a digest of the ids of the first and last row it counts."""
    digest = hashlib.blake2b(digest_size=16, person=b"recon.summary")
    digest.update(f"{first}:{last}".encode())
    return digest.hexdigest()


def cleaned(value: Any, max_chars: int) -> Any:
    """A row's ``value`` as a delivery carries it: a string cleaned
    (:func:`~recon.domain.subscriptions.clean`); a number, a boolean or null as it is; a list
    or an object as its JSON text, cleaned, so no value grows past ``max_chars``."""
    if isinstance(value, str):
        return clean(value, max_chars)
    if value is None or isinstance(value, bool | int | float):
        return value
    return clean(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str), max_chars)

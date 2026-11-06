"""Change detection (rulings C1-C7, and the owner's of 2026-10-24): what changed in a table
since what its earlier tables told, as a table of its own (``<output>.changes``), and the told
index each table keeps beside it.

**The hash** (C2) is a keyed BLAKE2b of 16 bytes over the canonical JSON of the record as its
table holds it (``sort_keys``, compact separators, ``ensure_ascii=False``), without its
``volatile`` fields and recon's own (``_``). Its key is the mission's (``MissionStore.
change_key``): a file of mode 0600 made on first use, so a low-entropy record (a one-time
code in a subject) cannot be recovered by hashing guesses. It is an input to the determinism
contract: the same tables, spec and key give the same change tables, byte for byte. A hash
is kept only in the told index, never in a change row, a warning or telemetry.

**The told index** of a table, its ``keys`` sidecar, written with its change table, is the
fold of every change row told for its output and source up to it (:mod:`recon.core.domain.changes`):
a header (``spec``: the digest of the key and volatile fields and of the key's identity; the
table's record and keyless counts; the index's key count; ``mac``, a digest of all of it
keyed with the mission's key), then a line per key, sorted, with its content's hash. It is a
cache of a pure function: a table's told index is the one its comparison with the previous
table's gives, so one that is missing, whose header is not the spec's or does not count its
table, or whose ``mac`` does not hold (edited by hand), is computed again from the chain,
back to the first whose index holds. An earlier archive is only read, never written.

**The join** (:class:`~recon.core.domain.changes.KeyJoin`) of a table's keys with a told index is
:class:`MemoryJoin` for one of up to ``memory_keys`` keys, else the port's other adapter (DuckDB
on a connection of its own): the table is read three times (its keys and hashes into the join;
its records in order, for its inserts and updates; never all of it at once).

**A stream** (C7) is the same chain, window by window, through each window's ``previous``; a
window never tells a delete.

**After the seal** (ruling R9). A table's changes are derived once its archive (or window) is
sealed and let go, into the same archive, under the writer's hold (:class:`Derivation`, through
``Hold.derive``): the act's commit, its tables and their checkpoints, never waits on them, and
what is derived is kept beside the table it describes, so change rows, cursors and acks read
as before. An archive opened again to derive reads ``writing`` until what is derived is kept,
so no reader takes it for told. A writer that dies between the seal and the derivation leaves
the table **pending** (:func:`pending`): sealed under this config, with no change summary yet.
A reader waits on it, and the next run derives it, oldest first, before anything newer
(:meth:`Derivation.catch_up`); a table sealed under another config is no pending one (its
config may not have had ``changes:``), and is read as it was before.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from contextlib import suppress
from dataclasses import dataclass, field
from functools import cached_property, partial
from typing import TYPE_CHECKING, Any

from recon.core import (
    CHANGE_COLUMNS,
    CHANGES_TYPE_URL,
    ChangeSummary,
    Problem,
    ReconError,
    TableError,
    TableOutcome,
    changes_output,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Iterator, Mapping, Sequence

    from recon.core import (
        ArchiveRef,
        Archives,
        ChangeOp,
        ChangesSpec,
        CollectorEntry,
        DeletesWithheld,
        Hold,
        Landed,
        ReconConfig,
    )
    from recon.observe.domain.joins import Joined, KeyJoin

#: The bytes of a record's hash: enough that two records of one table never share one by
#: chance, few enough that an index of a million keys stays small.
HASH_BYTES = 16

#: By default, the most keys a comparison joins in memory; past it, the join is DuckDB's,
#: which holds a bounded amount and spills the rest to disk. Measured (design record): a
#: comparison of 200,000 keys in memory peaks at about 160 MB of the process.
MEMORY_KEYS = 200_000

#: What a record's hash is keyed with besides the mission's key, so a hash recon makes for
#: change detection is never one it makes for anything else.
_PERSON = b"recon.changes"

#: What a told index's ``mac`` is keyed with besides the mission's key.
_MAC_PERSON = b"recon.told"


def canonical(value: Any) -> str:
    """``value`` as the one JSON text it has: keys sorted, no spaces, not ASCII-escaped."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str)  # fmt: skip


@dataclass(frozen=True)
class Hasher:
    """A spec's view of a record: its key and its content's hash, under the mission's
    ``secret``."""

    spec: ChangesSpec
    secret: bytes

    def key(self, record: dict[str, Any]) -> str | None:
        """The record's key, as the text it is looked up by; None for a keyless record (a key
        field absent or null)."""
        values = [record.get(name) for name in self.spec.key]
        if any(value is None for value in values):
            return None
        return canonical(values)

    def values(self, key: str) -> dict[str, Any]:
        """A key's text as ``{field: value}``."""
        return dict(zip(self.spec.key, json.loads(key), strict=True))

    def content(self, record: dict[str, Any]) -> str:
        """The keyed hash of the record without its volatile fields and recon's own."""
        volatile = self._volatile
        kept = {k: v for k, v in record.items() if not k.startswith("_") and k not in volatile}
        digest = self._keyed.copy()  # keyed once: a copy costs less than a new key schedule
        # A lone surrogate (JSON "\ud800", which a stranger's text can carry) is not UTF-8:
        # passed through as its code point's bytes, so it hashes, and never stops the table.
        digest.update(canonical(kept).encode("utf-8", "surrogatepass"))
        return digest.hexdigest()

    @cached_property
    def _volatile(self) -> frozenset[str]:
        return frozenset(self.spec.volatile)

    @cached_property
    def _keyed(self) -> hashlib.blake2b:
        return hashlib.blake2b(digest_size=HASH_BYTES, key=self.secret, person=_PERSON)

    def mac(self, header: dict[str, Any], pairs: Iterable[tuple[str, str]]) -> str:
        """The keyed digest of a told index: its ``header``'s counts and spec, then each of its
        ``(key, content)`` pairs in order. Its header carries it, so an index edited by hand,
        however its counts are made to hold, is not what the mission's key says, and is
        computed again (Ixian F5)."""
        mac = hashlib.blake2b(digest_size=HASH_BYTES, key=self.secret, person=_MAC_PERSON)
        mac.update(canonical({k: header.get(k) for k in _MACED}).encode())
        for key, content in pairs:
            mac.update(canonical([key, content]).encode("utf-8", "surrogatepass") + b"\n")
        return mac.hexdigest()

    @property
    def digest(self) -> str:
        """The spec's digest: its key and volatile fields, and the identity of the key the
        hashes are made with (never the key), so an index made under another is out of date."""
        identity = hashlib.blake2b(b"identity", digest_size=8, key=self.secret).hexdigest()
        spec = {"key": self.spec.key, "volatile": sorted(self.spec.volatile), "secret": identity}
        return hashlib.blake2b(canonical(spec).encode(), digest_size=HASH_BYTES).hexdigest()


#: The header fields a told index's ``mac`` covers, with its body.
_MACED = ("spec", "records", "keyless", "keys")


def normalize_digest(normalize: dict[str, str] | None) -> str:
    """The digest of a ``normalize:`` spec, which says whether two tables' records have the
    same shape."""
    return hashlib.blake2b(canonical(normalize).encode(), digest_size=HASH_BYTES).hexdigest()


# --- the join, in memory ------------------------------------------------------------------


class MemoryJoin:
    """The :class:`~recon.core.domain.changes.KeyJoin` in memory: a map of the told index and one
    of the table's keys."""

    def join(
        self, told: Iterable[tuple[str, str]], new: Iterable[tuple[int, str, str]]
    ) -> _MemoryJoined:
        return _MemoryJoined(dict(told), new)


class _MemoryJoined:
    def __init__(self, told: dict[str, str], new: Iterable[tuple[int, str, str]]) -> None:
        self._told = told
        #: Each key of the table: the ordinal of its first record, and its content (None when
        #: it has two).
        self._new: dict[str, tuple[int, str | None]] = {}
        for ordinal, key, content in new:
            found = self._new.get(key)
            if found is None:
                self._new[key] = (ordinal, content)
            elif found[1] != content:
                self._new[key] = (found[0], None)
        self.ambiguous = sum(1 for _, content in self._new.values() if content is None)

    def ops(self) -> Iterator[tuple[int, ChangeOp]]:
        for key, (ordinal, content) in self._new.items():  # first appearance, table order
            if content is None:
                continue
            told = self._told.get(key)
            if told is None:
                yield ordinal, "insert"
            elif told != content:
                yield ordinal, "update"

    def gone(self) -> Iterator[str]:
        return iter(sorted(key for key in self._told if key not in self._new))

    def told(self, *, deletes: bool) -> Iterator[tuple[str, str]]:
        # Deletes are told only of a table with no ambiguous key, so without them every key
        # told before stays, an ambiguous one with what was told of it.
        folded = {} if deletes else dict(self._told)
        for key, (_, content) in self._new.items():
            if content is not None:
                folded[key] = content
        return iter(sorted(folded.items()))

    def told_count(self, *, deletes: bool) -> int:
        # The union of the keys told and the table's unambiguous ones, counted, never the
        # fold sorted to count it (round-4 review M6).
        kept = sum(1 for _, content in self._new.values() if content is not None)
        if deletes:
            return kept
        return kept + sum(
            1 for key in self._told if key not in self._new or self._new[key][1] is None
        )

    def close(self) -> None:
        self._told, self._new = {}, {}


# --- a comparison -------------------------------------------------------------------------


@dataclass
class Counts:
    """What a comparison found: its rows by op, the records it could not compare, and why its
    deletes were withheld."""

    inserts: int = 0
    updates: int = 0
    deletes: int = 0
    keyless: int = 0
    ambiguous: int = 0
    withheld: DeletesWithheld | None = None


class Comparison:
    """A table (``new``: its records in order, read again on each call) compared with a told
    index (``told``: None when nothing was told before, so every key is an insert and no
    delete is considered), through ``join``. ``whole``: the table sealed ``ok``, not
    truncated, its survey run to its end. Its deletes are told when the spec asks for them,
    the table is whole and holds no keyless and no ambiguous record; or, given ``deletes``,
    as that says: a table compared again folds as it was told when first compared."""

    def __init__(
        self,
        new: Callable[[], Iterable[dict[str, Any]]],
        told: Iterable[tuple[str, str]] | None,
        hasher: Hasher,
        join: KeyJoin,
        *,
        whole: bool,
        deletes: bool | None = None,
    ) -> None:
        self.counts = Counts()
        self._new = new
        self._hasher = hasher
        self._joined: Joined = join.join(told if told is not None else (), self._keys(new()))
        self.counts.ambiguous = self._joined.ambiguous
        self.deletes = False
        if deletes is not None:
            self.deletes = deletes and told is not None
        elif hasher.spec.deletes and told is not None:
            withheld: DeletesWithheld | None = None
            if not whole:
                withheld = "truncated"
            elif self.counts.keyless:
                withheld = "keyless"
            elif self.counts.ambiguous:
                withheld = "ambiguous"
            self.counts.withheld = withheld
            self.deletes = withheld is None

    def _keys(self, records: Iterable[dict[str, Any]]) -> Iterator[tuple[int, str, str]]:
        for ordinal, record in enumerate(records):
            key = self._hasher.key(record)
            if key is None:
                self.counts.keyless += 1
                continue
            yield ordinal, key, self._hasher.content(record)

    def rows(self) -> Iterator[dict[str, Any]]:
        """The change rows: inserts and updates in the table's order, then its deletes."""
        ops = self._joined.ops()
        next_op = next(ops, None)
        if next_op is not None:
            for ordinal, record in enumerate(self._new()):
                if ordinal != next_op[0]:
                    continue
                key = self._hasher.key(record)
                assert key is not None
                if next_op[1] == "insert":
                    self.counts.inserts += 1
                else:
                    self.counts.updates += 1
                yield {"op": next_op[1], "key": self._hasher.values(key), "record": record}
                next_op = next(ops, None)
                if next_op is None:
                    break
        if self.deletes:
            for key in self._joined.gone():
                self.counts.deletes += 1
                yield {"op": "delete", "key": self._hasher.values(key), "record": None}

    def told(self) -> Iterator[tuple[str, str]]:
        """The told index once this table's rows are told."""
        return self._joined.told(deletes=self.deletes)

    def told_count(self) -> int:
        """How many keys :meth:`told` gives."""
        return self._joined.told_count(deletes=self.deletes)

    def lines(self, records: int) -> Iterator[dict[str, Any]]:
        """The told index as its sidecar holds it, for a table of ``records`` records: its
        header, ``mac`` last, then a line a key. The index is listed twice, once for the
        ``mac``."""
        header: dict[str, Any] = {
            "spec": self._hasher.digest,
            "records": records,
            "keyless": self.counts.keyless,
            "keys": self.told_count(),
        }
        yield {**header, "mac": self._hasher.mac(header, self.told())}  # fmt: skip
        for key, content in self.told():
            yield {"key": key, "hash": content}

    def close(self) -> None:
        self._joined.close()


def told_lines(
    read: Callable[[], Iterator[dict[str, Any]] | None], hasher: Hasher, records: int
) -> tuple[int, Iterator[tuple[str, str]]] | None:
    """A kept told index, read by ``read`` (None: there is none), when its header is for
    ``hasher``'s spec and a table of ``records`` records and its ``mac`` is what the
    mission's key gives for it: its key count, and its ``(key, content)`` pairs; None when it
    is not. It is read twice: once to check it, once for its pairs."""
    lines = read()
    header = next(lines, None) if lines is not None else None
    if not isinstance(header, dict) or header.get("spec") != hasher.digest:
        return None
    if header.get("records") != records or not isinstance(header.get("keys"), int):
        return None
    try:
        held = hasher.mac(header, ((line["key"], line["hash"]) for line in lines))  # type: ignore[union-attr]
    except (KeyError, TypeError):
        return None
    again = read() if held == header.get("mac") else None
    if again is None or next(again, None) is None:
        return None
    return header["keys"], ((line["key"], line["hash"]) for line in again)


# --- the detector, over the store ---------------------------------------------------------


@dataclass(frozen=True)
class _Compared:
    """An earlier table that was compared: which archive, how many records it held, whether its
    deletes were told, whether it started the told index again, the one compared before it,
    and the windows that told nothing whose records it told before its own."""

    archive: ArchiveRef
    records: int
    deletes_told: bool
    rebaselined: bool
    previous: str | None
    normalize: str | None
    untold: tuple[str, ...] = ()


@dataclass
class _Told:
    """A told index to compare with (None: nothing told), its size, and what holds it open."""

    pairs: Iterable[tuple[str, str]] | None
    keys: int = 0
    holding: list[Comparison] = field(default_factory=list)

    def close(self) -> None:
        for comparison in self.holding:
            comparison.close()


class ChangeDetector:
    """Writes the change table and the told index of each table of ``mission`` whose entry
    has ``changes:``, under the mission's ``secret``: into the archive (or window) being
    written alone, while earlier ones are only read, through ``archives``. ``large`` joins a
    table or told index of more than ``memory_keys`` keys (None: every join is in memory)."""

    def __init__(
        self,
        archives: Archives,
        mission: str,
        secret: bytes,
        *,
        large: KeyJoin | None = None,
        memory_keys: int = MEMORY_KEYS,
    ) -> None:
        self._archives = archives
        self._mission = mission
        self._secret = secret
        self._large = large
        self._memory_keys = memory_keys
        self._unclosed: list[tuple[str, Exception]] = []

    def snapshot(
        self,
        archive: Landed,
        table: TableOutcome,
        spec: ChangesSpec,
        normalize: dict[str, str] | None,
        *,
        pending: Callable[[TableOutcome], object] | None = None,
    ) -> TableOutcome:
        """``table`` of ``archive``, sealed ``ok``, compared with the told index of the
        previous table compared: the latest earlier one of the same name and source. Run once
        every table of the survey has run, so the survey reaches its end. ``pending`` is
        given the table with its summary (its rows not yet counted) once its told index is
        written and before its change table is, so the summary is in the meta before any row
        can be read (Dijkstra F3)."""
        listed = self._archives.archives(self._mission)  # in the order they were made
        names = [a.id for a in listed]
        archives = listed[: names.index(archive.ref.id)] if archive.ref.id in names else listed
        return self._compare(archive, table, spec, normalize_digest(normalize), archives,
                             None, whole=not table.truncated, pending=pending)  # fmt: skip

    def window(
        self,
        archive: Landed,
        stream: str,
        previous: str | None,
        table: TableOutcome,
        spec: ChangesSpec,
        normalize: dict[str, str] | None,
        *,
        pending: Callable[[TableOutcome], object] | None = None,
    ) -> TableOutcome:
        """A stream's window, sealed ``ok``, compared with the told index of its chain up to
        the window ``previous``. Never a delete. ``pending`` as :meth:`snapshot`'s."""
        windows = self._archives.archives(self._mission, stream=stream)
        windowed = spec.model_copy(update={"deletes": False})
        return self._compare(archive, table, windowed, normalize_digest(normalize), windows,
                             previous or "", whole=True, pending=pending)  # fmt: skip

    def _compare(
        self,
        archive: Landed,
        table: TableOutcome,
        spec: ChangesSpec,
        shape: str,
        earlier: Sequence[ArchiveRef],
        chain: str | None,
        *,
        whole: bool,
        pending: Callable[[TableOutcome], object] | None,
    ) -> TableOutcome:
        """Compare ``table`` with the told index of the previous table compared among
        ``earlier``: the one ``chain`` names (a stream's previous window; "" for none), or
        else the latest. Written in the order that keeps every row a reader can see folded:
        the told index, then the summary (``pending``), then the change table."""
        hasher = Hasher(spec, self._secret)
        sha256 = self._archives.meta(archive.ref).get("config_sha256")
        by_name = {a.id: a for a in earlier}
        untold: list[ArchiveRef] = []
        if chain is None:
            previous = self._previous_snapshot(earlier, table)
            before = previous is None and self._sealed_before(earlier, table, sha256)
        else:
            previous, untold = self._chain(earlier, chain, table, sha256)
            before = False  # a window's records are its own: nothing to flood
        rebaselined = before or (previous is not None and previous.normalize != shape)
        if rebaselined:
            untold = []  # what an untold window held is in the told index started again here
        told = _Told(None) if rebaselined or previous is None else (
            self._told(previous, table, hasher, by_name)
        )  # fmt: skip
        dead: list[Comparison] = []
        try:
            # What windows that told nothing held, told here, oldest first (F1, G1).
            for window in untold:
                held = Comparison(
                    self._reader(window, table.name, unsealed=True),
                    told.pairs,
                    hasher,
                    self._join(told.keys),
                    whole=True,
                    deletes=False,
                )
                dead.append(held)
                told.close()
                told = _Told(held.told(), held.told_count())
            new = self._reader(archive.ref, table.name)
            comparison = Comparison(new, told.pairs, hasher, self._join(table.records, told.keys),
                                    whole=whole)  # fmt: skip
        except BaseException:
            for held in dead:
                held.close()
            raise
        finally:
            told.close()
        every = [*dead, comparison]

        def summary(written: int) -> ChangeSummary:
            return ChangeSummary(
                table=changes_output(table.name),
                type_url=CHANGES_TYPE_URL,
                previous=previous.archive.id if previous is not None else None,
                spec=hasher.digest,
                normalize=shape,
                rows=written,
                inserts=sum(c.counts.inserts for c in every),
                updates=sum(c.counts.updates for c in every),
                deletes=comparison.counts.deletes,
                rebaselined=rebaselined,
                keyless=sum(c.counts.keyless for c in every),
                ambiguous=sum(c.counts.ambiguous for c in every),
                deletes_withheld=None if rebaselined else comparison.counts.withheld,
                deletes_told=comparison.deletes,
                untold=[window.id for window in untold],
            )

        try:
            archive.keep(table.name, "keys", comparison.lines(table.records))
            if pending is not None:
                pending(table.model_copy(update={"changes": summary(0)}))
            rows: Iterable[dict[str, Any]] = (
                iter([{"op": "rebaselined", "key": None, "record": None}])
                if rebaselined
                else (row for c in every for row in c.rows())
            )
            written = archive.write(
                changes_output(table.name), _generator(rows), columns=CHANGE_COLUMNS
            )
        except BaseException:
            for c in every:
                with contextlib.suppress(Exception):  # the write's failure is the one said
                    c.close()
            raise
        # Sealed: its rows were told, whatever the cleanup after it does. A join that fails
        # as it closes is said, never the table's changes (Dijkstra G3); what it left is swept
        # by the next join.
        for c in every:
            try:
                c.close()
            except Exception as exc:
                self._unclosed.append((table.name, exc))
        return table.model_copy(update={"changes": summary(written)})

    def unclosed(self) -> list[tuple[str, Exception]]:
        """The joins that failed as they closed, after their change tables were sealed, by
        output, since this was last asked; each one's table told what it holds."""
        taken, self._unclosed = self._unclosed, []
        return taken

    def _join(self, *sizes: int) -> KeyJoin:
        if self._large is not None and max(sizes) > self._memory_keys:
            return self._large
        return MemoryJoin()

    # --- the chain ---

    def _told(
        self,
        last: _Compared,
        table: TableOutcome,
        hasher: Hasher,
        by_name: dict[str, ArchiveRef],
    ) -> _Told:
        """What was told up to ``last``: its kept told index when that holds, else computed
        again by comparing each table of the chain since the latest whose index holds (or
        since the chain's start) with the told index before it."""
        again: list[_Compared] = []
        told = _Told(None)
        current: _Compared | None = last
        while current is not None and current not in again:
            read = partial(self._archives.sidecar, current.archive, table.name, "keys")
            kept = told_lines(read, hasher, current.records)
            if kept is not None:
                told = _Told(kept[1], kept[0])
                break
            again.append(current)
            if current.rebaselined:
                break  # the told index started again here: from nothing told
            found = by_name.get(current.previous) if current.previous else None
            current = self._compared(found, table) if found is not None else None
        for compared in reversed(again):
            # A table is folded as it was told: after what the untold windows before it held.
            for name in () if compared.rebaselined else compared.untold:
                window = by_name.get(name)
                if window is None:
                    continue
                held = Comparison(
                    self._reader(window, table.name, unsealed=True),
                    told.pairs,
                    hasher,
                    self._join(told.keys),
                    whole=True,
                    deletes=False,
                )
                told.close()
                told = _Told(held.told(), held.told_count(), [held])
            comparison = Comparison(
                self._reader(compared.archive, table.name),
                None if compared.rebaselined else told.pairs,
                hasher,
                self._join(compared.records, told.keys),
                whole=True,
                deletes=compared.deletes_told,
            )
            told.close()
            told = _Told(comparison.told(), comparison.told_count(), [comparison])
        return told

    def _chain(
        self, windows: Sequence[ArchiveRef], chain: str, table: TableOutcome, sha256: str | None
    ) -> tuple[_Compared | None, list[ArchiveRef]]:
        """A window's previous compared window, walking back from ``chain`` (the window before
        it), and the windows passed on the way that told nothing, oldest first, whose records
        this window tells: one whose writer died before it compared them (Dijkstra F1), and
        one sealed with its changes failed (G1). A window sealed and never compared (before
        ``changes:`` was set) ends the walk with nothing told; one sealed under this config
        (``sha256``) and still pending (its deriving never ran) is untold, not such a one."""
        names = [window.id for window in windows]
        if chain not in names:
            return None, []
        untold: list[ArchiveRef] = []
        for window in reversed(windows[: names.index(chain) + 1]):
            compared = self._compared(window, table)
            if compared is not None:
                return compared, untold[::-1]
            meta = self._archives.meta(window)
            sealed = meta.get("format_version") is not None and meta.get("ended", "") is not None
            entry = self._entry(window, table)
            changes = entry.get("changes") if entry is not None else None
            failed = isinstance(changes, dict) and bool(changes.get("error"))
            if sealed and not failed and not pending(meta, table.name, sha256):
                break  # sealed, and never compared
            untold.append(window)
        return None, untold[::-1]

    def _previous_snapshot(
        self, earlier: Sequence[ArchiveRef], table: TableOutcome
    ) -> _Compared | None:
        """The latest earlier table of the same name and source that was compared, however
        its survey then ended: every table is compared once all have run, so one compared
        was whole when it was, and what it told a reader could read (Dijkstra F3)."""
        for candidate in reversed(earlier):
            compared = self._compared(candidate, table)
            if compared is not None:
                return compared
        return None

    def _sealed_before(
        self, earlier: Sequence[ArchiveRef], table: TableOutcome, sha256: str | None
    ) -> bool:
        """Whether an earlier survey that ran to its end sealed the table ``ok`` without
        comparing it (``changes:`` was set since): its records' shape is not known. One whose
        changes failed was under ``changes:``, and is not such a table; nor is one sealed under
        this config (``sha256``), whose changes are pending, not unknown."""
        for candidate in earlier:
            meta = self._archives.meta(candidate)
            entry = self._entry(candidate, table)
            if (
                meta.get("ended") == "finished"
                and entry is not None
                and not entry.get("changes")
                and not pending(meta, table.name, sha256)
            ):
                return True
        return False

    def _entry(self, archive: ArchiveRef, table: TableOutcome) -> dict[str, Any] | None:
        """The table's entry in ``archive``'s meta: sealed ``ok``, or, for a window, sealed
        however its source ended (its records are told whatever, F1)."""
        meta = self._archives.meta(archive)
        window = isinstance(meta.get("stream"), dict)
        for entry in meta.get("tables") or []:
            if (
                entry.get("name") == table.name
                and entry.get("source") == table.source
                and (entry.get("status") == "ok" or window)
            ):
                return dict(entry)
        return None

    def _compared(self, archive: ArchiveRef, table: TableOutcome) -> _Compared | None:
        entry = self._entry(archive, table)
        changes = entry.get("changes") if entry is not None else None
        if entry is None or not isinstance(changes, dict) or changes.get("error"):
            return None  # never compared, or its changes failed: it told nothing
        if self._archives.records(archive, changes_output(table.name)) is None:
            return None  # its summary was written, and it died before its rows were
        untold = changes.get("untold") or []
        return _Compared(archive, int(entry.get("records") or 0),
                         changes.get("deletes_told") is True, bool(changes.get("rebaselined")),
                         changes.get("previous"), changes.get("normalize"),
                         tuple(str(name) for name in untold))  # fmt: skip

    def _reader(
        self, archive: ArchiveRef, output: str, *, unsealed: bool = False
    ) -> Callable[[], Iterable[dict[str, Any]]]:
        def read() -> Iterable[dict[str, Any]]:
            records = self._archives.records(archive, output, unsealed=unsealed)
            return records if records is not None else ()

        return read


def failed(table: TableOutcome, exc: Exception) -> TableOutcome:
    """``table``, its changes not told for ``exc``: its outcome says so, classified (a store
    that could not write, ``store``; one recon did not classify, ``unknown``)."""
    kind = exc.kind if isinstance(exc, ReconError) else "store" if isinstance(exc, OSError) else (
        "unknown"
    )  # fmt: skip
    fix = exc.fix if isinstance(exc, ReconError) and exc.fix else None
    summary = ChangeSummary(
        table=changes_output(table.name), type_url=CHANGES_TYPE_URL, previous=None, spec="",
        normalize=None, rows=0, error=TableError(kind=kind, message=f"{type(exc).__name__}: "
                                                 f"{exc}", fix=fix),
    )  # fmt: skip
    return table.model_copy(update={"changes": summary})


def join_unclosed(at: str, output: str, exc: Exception) -> Problem:
    """The warning for a join that failed as it closed, once ``output``'s change table was
    sealed: its rows were told and stand; what the join left (a DuckDB connection's files)
    is removed when the next join starts."""
    return Problem(
        f"{at}.changes",
        f"{output}'s changes were told, and its join could not be closed: "
        f"{type(exc).__name__}: {exc}",
        kind="join_unclosed",
        fix="nothing to do for the table; what the join left is removed by the next large "
        "comparison of the mission; if this repeats, free space for the mission or report it",
    )


def changes_failed(at: str, output: str, exc: Exception) -> Problem:
    """The warning for a table whose changes could not be told (``at``: its entry's path):
    the table is kept, tells nothing and is no told index, so the next table of its output
    is compared with what was told before it, and tells this one's changes too (Vector F6:
    nothing in the change phase fails a survey or a window)."""
    fix = exc.fix if isinstance(exc, ReconError) and exc.fix else (
        "nothing to do for this table: it is kept, and the next survey or window tells its "
        "changes; if this repeats, report it as a bug in recon, with this message"
    )  # fmt: skip
    return Problem(
        f"{at}.changes",
        f"{output}'s changes could not be told, so it tells none this time: "
        f"{type(exc).__name__}: {exc}",
        kind="changes_failed",
        fix=fix,
    )


def _generator(rows: Iterable[dict[str, Any]]) -> Generator[dict[str, Any], None, None]:
    yield from rows


# --- after the seal -----------------------------------------------------------------------


def pending(meta: Mapping[str, Any], output: str, sha256: str | None) -> bool:
    """Whether the table ``output`` of the archive (or window) whose meta is ``meta`` is still
    to be told: sealed under the config whose digest is ``sha256`` (so its entry has
    ``changes:``), its writer ended (a survey's ``finished``; a window's, however it was
    sealed, as a window tells what its source sent), the table sealed ``ok`` (a window's, as
    it was sealed), and no change summary recorded for it. A table sealed under another config
    is never pending: that config may not have had ``changes:``."""
    if sha256 is None or meta.get("config_sha256") != sha256:
        return False
    window = isinstance(meta.get("stream"), dict)
    ended = meta.get("ended")
    if ended != "finished" and not (window and ended == "stopped"):
        return False
    for entry in meta.get("tables") or []:
        if entry.get("name") == output and (entry.get("status") == "ok" or window):
            return not entry.get("changes")
    return False


class Derivation:
    """What changed in a mission's tables, derived after each archive (or window) is sealed,
    into it, under the writer's hold: the change table and told index of each table of an entry
    of ``config`` with ``changes:``, each table's outcome then recorded again in the archive's
    meta with its change summary. ``sha256`` is the digest of ``config``'s bytes, which every
    archive it lands records: what tells a table pending (:func:`pending`).

    Nothing derived fails an act: a table whose changes cannot be told keeps its records, its
    outcome says why (``changes.error``), a ``changes_failed`` warning says what to do, and the
    next table of its output tells its changes too (Vector F6). Each warning is also told to
    ``warn``, with the output it is about, as it is found."""

    def __init__(
        self,
        archives: Archives,
        detector: ChangeDetector,
        config: ReconConfig,
        sha256: str,
        *,
        warn: Callable[[str, Problem], object] | None = None,
    ) -> None:
        self._archives = archives
        self._detector = detector
        self._sha256 = sha256
        self._warn = warn
        self._entries = {
            entry.name: (index, entry)
            for index, entry in enumerate(config.collectors)
            if entry.changes is not None
        }

    def snapshot(
        self, hold: Hold, ref: ArchiveRef, tables: Sequence[TableOutcome]
    ) -> tuple[list[TableOutcome], list[Problem]]:
        """Derive the changes of each of ``tables`` (``ref``'s, as its act sealed them) sealed
        ``ok`` of an entry with ``changes:``; the tables with what each found, and the
        warnings. A table that failed is compared with nothing and is no baseline, and one told
        already (an archive partly derived before its writer died) is told once."""
        tables = list(tables)
        todo = [
            position
            for position, table in enumerate(tables)
            if table.collector in self._entries and table.status == "ok" and table.changes is None
        ]
        if not todo:
            return tables, []
        warnings: list[Problem] = []
        meta = dict(self._archives.meta(ref))
        with hold.derive(ref) as landed:

            def record(position: int, told: TableOutcome) -> None:
                tables[position] = told
                landed.record({**meta, "tables": [table.document() for table in tables]})

            for position in todo:
                table = tables[position]
                index, entry = self._entries[table.collector]
                assert entry.changes is not None
                compare = partial(
                    self._detector.snapshot, landed, table, entry.changes, entry.normalize
                )
                told = self._told(compare, partial(record, position), table, index, warnings)
                withheld = told.changes.deletes_withheld if told.changes else None
                if withheld is not None:
                    self._said(table.name, _withheld(index, entry, table.name, withheld), warnings)
        return tables, warnings

    def window(
        self, hold: Hold, ref: ArchiveRef, table: TableOutcome
    ) -> tuple[TableOutcome, list[Problem]]:
        """Derive the changes of the table of the sealed window ``ref``, when its entry has
        ``changes:``: against what its chain told, never a delete (ruling C7). A window whose
        source failed holds records too, and tells them (Dijkstra F1)."""
        found = self._entries.get(table.collector)
        if found is None or ref.stream is None or table.changes is not None:
            return table, []
        index, entry = found
        assert entry.changes is not None
        meta = dict(self._archives.meta(ref))
        stream = meta.get("stream")
        previous = stream.get("previous") if isinstance(stream, dict) else None
        warnings: list[Problem] = []
        with hold.derive(ref) as landed:

            def record(told: TableOutcome) -> None:
                landed.record({**meta, "tables": [told.document()]})

            compare = partial(
                self._detector.window, landed, ref.stream, previous, table, entry.changes,
                entry.normalize,
            )  # fmt: skip
            told = self._told(compare, record, table, index, warnings)
        return told, warnings

    def catch_up(
        self, hold: Hold, mission: str, *, stream: str | None = None
    ) -> tuple[dict[str, list[TableOutcome]], list[Problem]]:
        """Derive, oldest first, every archive of what ``hold`` holds (with ``stream``, that
        stream's windows) whose tables are pending: a writer that died after its seal and
        before its deriving. Run before anything newer is derived, so each is told in its own
        archive, as a reader waiting on it expects. The tables told, by archive id, and the
        warnings."""
        told: dict[str, list[TableOutcome]] = {}
        warnings: list[Problem] = []
        for ref in self._archives.archives(mission, stream=stream):
            meta = self._archives.meta(ref)
            if not any(
                entry.get("collector") in self._entries
                and pending(meta, str(entry.get("name")), self._sha256)
                for entry in meta.get("tables") or []
            ):
                continue
            tables = [TableOutcome.model_validate(entry) for entry in meta.get("tables") or []]
            if stream is None:
                told[ref.id], found = self.snapshot(hold, ref, tables)
            else:
                table, found = self.window(hold, ref, tables[0])
                told[ref.id] = [table]
            warnings += found
        return told, warnings

    def _told(
        self,
        compare: Callable[..., TableOutcome],
        record: Callable[[TableOutcome], object],
        table: TableOutcome,
        index: int,
        warnings: list[Problem],
    ) -> TableOutcome:
        """``table`` (of the entry at ``collectors.<index>``) compared by ``compare``, handed
        as ``pending`` what records its summary before its rows can be read, its outcome then
        recorded by ``record``; a failure of its changes said, never raised."""
        at = f"collectors.{index}"
        try:
            told = compare(pending=record)
        except Exception as exc:  # the table's changes fail, never the act (Vector F6)
            told = failed(table, exc)
            self._said(table.name, changes_failed(at, table.name, exc), warnings)
            with suppress(OSError):  # unrecorded, it stays pending: the next run tells it
                record(told)
            return told
        for output, unclosed in self._detector.unclosed():
            self._said(output, join_unclosed(at, output, unclosed), warnings)
        with suppress(OSError):  # its counts; the change table is sealed, and reads whole
            record(told)
        return told

    def _said(self, output: str, warning: Problem, warnings: list[Problem]) -> None:
        warnings.append(warning)
        if self._warn is not None:
            self._warn(output, warning)


#: Why a table's deletes were withheld, and what to do about it.
_WITHHELD: dict[str, tuple[str, str]] = {
    "truncated": ("its crawl was cut short, so a key it lacks may only be past where it "
                  "stopped", "read every page: raise paginate.pages, or narrow the request"),
    "keyless": ("it holds records with no key, which may be the ones that went",
                "name key fields every record has (changes.key), or shape the records so they "
                "have them"),
    "ambiguous": ("it holds a key with two contents, so which one went is not known",
                  "name key fields that tell its records apart (changes.key)"),
}  # fmt: skip


def _withheld(index: int, entry: CollectorEntry, output: str, why: DeletesWithheld) -> Problem:
    """The warning for a table whose deletes were asked for and withheld."""
    reason, fix = _WITHHELD[why]
    return Problem(
        f"collectors.{index}.changes.deletes",
        f"{output}'s deletes were not told: {reason}",
        kind="deletes_withheld",
        fix=f"{fix} on collector {entry.name!r}",
        got=why,
    )

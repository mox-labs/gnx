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

**The told index** of a table, ``<output>.keys.jsonl``, written with its change table, is the
fold of every change row told for its output and source up to it (:mod:`recon.domain.changes`):
a header (``spec``: the digest of the key and volatile fields and of the key's identity; the
table's record and keyless counts; the index's key count), then a line per key, sorted, with
its content's hash. It is a cache of a pure function: a table's told index is the one its
comparison with the previous table's gives, so one that is missing, or whose header is not
the spec's or does not count its table, is computed again from the chain, back to the first
whose index holds. An earlier archive is only read, never written.

**The join** (:class:`~recon.domain.changes.KeyJoin`) of a table's keys with a told index is
:class:`MemoryJoin` for one of up to ``memory_keys`` keys, else the port's other adapter (DuckDB
on a connection of its own): the table is read three times (its keys and hashes into the join;
its records in order, for its inserts and updates; never all of it at once).

**A stream** (C7) is the same chain, window by window, through each window's ``previous``; a
window never tells a delete.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Any

from recon.domain.changes import CHANGE_COLUMNS, CHANGES_TYPE_URL, changes_output
from recon.domain.documents import ChangeSummary

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Iterator, Sequence
    from pathlib import Path

    from recon.domain.archives import ArchiveStore
    from recon.domain.changes import ChangeOp, ChangesSpec, DeletesWithheld, Joined, KeyJoin
    from recon.domain.documents import TableOutcome

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
        digest.update(canonical(kept).encode())
        return digest.hexdigest()

    @cached_property
    def _volatile(self) -> frozenset[str]:
        return frozenset(self.spec.volatile)

    @cached_property
    def _keyed(self) -> hashlib.blake2b:
        return hashlib.blake2b(digest_size=HASH_BYTES, key=self.secret, person=_PERSON)

    @property
    def digest(self) -> str:
        """The spec's digest: its key and volatile fields, and the identity of the key the
        hashes are made with (never the key), so an index made under another reads stale."""
        identity = hashlib.blake2b(b"identity", digest_size=8, key=self.secret).hexdigest()
        spec = {"key": self.spec.key, "volatile": sorted(self.spec.volatile), "secret": identity}
        return hashlib.blake2b(canonical(spec).encode(), digest_size=HASH_BYTES).hexdigest()


def normalize_digest(normalize: dict[str, str] | None) -> str:
    """The digest of a ``normalize:`` spec, which says whether two tables' records have the
    same shape."""
    return hashlib.blake2b(canonical(normalize).encode(), digest_size=HASH_BYTES).hexdigest()


# --- the join, in memory ------------------------------------------------------------------


class MemoryJoin:
    """The :class:`~recon.domain.changes.KeyJoin` in memory: a map of the told index and one
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
        return sum(1 for _ in self.told(deletes=deletes))

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
    the table is whole and holds no keyless and no ambiguous record."""

    def __init__(
        self,
        new: Callable[[], Iterable[dict[str, Any]]],
        told: Iterable[tuple[str, str]] | None,
        hasher: Hasher,
        join: KeyJoin,
        *,
        whole: bool,
    ) -> None:
        self.counts = Counts()
        self._new = new
        self._hasher = hasher
        self._joined: Joined = join.join(told if told is not None else (), self._keys(new()))
        self.counts.ambiguous = self._joined.ambiguous
        self.deletes = False
        if hasher.spec.deletes and told is not None:
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
        """The told index as its sidecar holds it, for a table of ``records`` records."""
        yield {"spec": self._hasher.digest, "records": records, "keyless": self.counts.keyless,
               "keys": self.told_count()}  # fmt: skip
        for key, content in self.told():
            yield {"key": key, "hash": content}

    def close(self) -> None:
        self._joined.close()


def told_lines(
    lines: Iterator[dict[str, Any]], spec: str, records: int
) -> tuple[int, Iterator[tuple[str, str]]] | None:
    """A kept told index, when its header is for ``spec`` and a table of ``records``
    records: its key count, and its ``(key, content)`` pairs; None when it is not."""
    header = next(lines, None)
    if not isinstance(header, dict) or header.get("spec") != spec:
        return None
    if header.get("records") != records or not isinstance(header.get("keys"), int):
        return None
    return header["keys"], ((line["key"], line["hash"]) for line in lines)


# --- the detector, over the store ---------------------------------------------------------


@dataclass(frozen=True)
class _Compared:
    """An earlier table that was compared: where, how many records it held, whether it was
    whole, whether it started the told index again, and the one compared before it."""

    archive: Path
    records: int
    whole: bool
    rebaselined: bool
    previous: str | None
    normalize: str | None


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
    """Writes the change table and the told index of each table of a mission whose entry has
    ``changes:``, through the archive store, under the mission's ``secret``. ``large`` joins
    a table or told index of more than ``memory_keys`` keys (None: every join is in memory).
    It writes into the archive (or window) being written alone: an earlier one is only
    read."""

    def __init__(
        self,
        archives: ArchiveStore,
        mission_dir: Path,
        secret: bytes,
        *,
        large: KeyJoin | None = None,
        memory_keys: int = MEMORY_KEYS,
    ) -> None:
        self._archives = archives
        self._mission_dir = mission_dir
        self._secret = secret
        self._large = large
        self._memory_keys = memory_keys

    def snapshot(
        self,
        archive: Path,
        table: TableOutcome,
        spec: ChangesSpec,
        normalize: dict[str, str] | None,
    ) -> TableOutcome:
        """``table`` of ``archive``, sealed ``ok``, compared with the told index of the
        previous table compared: the latest earlier one of the same name and source, in an
        archive whose survey ran to its end. Run once every table of the survey has run, so
        the survey reaches its end."""
        archives = [a for a in self._archives.archives(self._mission_dir) if a.name < archive.name]
        return self._compare(archive, table, spec, normalize_digest(normalize), archives,
                             None, whole=not table.truncated)  # fmt: skip

    def window(
        self,
        archive: Path,
        stream: str,
        previous: str | None,
        table: TableOutcome,
        spec: ChangesSpec,
        normalize: dict[str, str] | None,
    ) -> TableOutcome:
        """A stream's window, sealed ``ok``, compared with the told index of its chain up to
        the window ``previous``. Never a delete."""
        windows = self._archives.archives(self._mission_dir, stream=stream)
        windowed = spec.model_copy(update={"deletes": False})
        return self._compare(archive, table, windowed, normalize_digest(normalize), windows,
                             previous or "", whole=True)  # fmt: skip

    def _compare(
        self,
        archive: Path,
        table: TableOutcome,
        spec: ChangesSpec,
        shape: str,
        earlier: Sequence[Path],
        chain: str | None,
        *,
        whole: bool,
    ) -> TableOutcome:
        """Compare ``table`` with the told index of the previous table compared among
        ``earlier``: the one ``chain`` names (a stream's previous window; "" for none), or
        else the latest of a survey that ran to its end."""
        hasher = Hasher(spec, self._secret)
        by_name = {a.name: a for a in earlier}
        if chain is None:
            previous = self._previous_snapshot(earlier, table)
            seen = previous is not None or self._sealed_before(earlier, table)
        else:
            found = by_name.get(chain)
            previous = self._compared(found, table) if found is not None else None
            seen = found is not None
        rebaselined = seen and (previous is None or previous.normalize != shape)
        told = _Told(None) if rebaselined or previous is None else (
            self._told(previous, table, hasher, by_name)
        )  # fmt: skip
        new = self._reader(archive, table.name)
        try:
            comparison = Comparison(new, told.pairs, hasher, self._join(table.records, told.keys),
                                    whole=whole)  # fmt: skip
        finally:
            told.close()
        try:
            rows = (
                iter([{"op": "rebaselined", "key": None, "record": None}])
                if rebaselined
                else comparison.rows()
            )
            written = self._archives.write_table(
                archive, changes_output(table.name), _generator(rows), columns=CHANGE_COLUMNS
            )
            self._archives.write_sidecar(
                archive, table.name, "keys", comparison.lines(table.records)
            )
        finally:
            comparison.close()
        counts = comparison.counts
        summary = ChangeSummary(
            table=changes_output(table.name),
            type_url=CHANGES_TYPE_URL,
            previous=previous.archive.name if previous is not None else None,
            spec=hasher.digest,
            normalize=shape,
            rows=written,
            inserts=counts.inserts,
            updates=counts.updates,
            deletes=counts.deletes,
            rebaselined=rebaselined,
            keyless=counts.keyless,
            ambiguous=counts.ambiguous,
            deletes_withheld=None if rebaselined else counts.withheld,
        )
        return table.model_copy(update={"changes": summary})

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
        by_name: dict[str, Path],
    ) -> _Told:
        """What was told up to ``last``: its kept told index when that holds, else computed
        again by comparing each table of the chain since the latest whose index holds (or
        since the chain's start) with the told index before it."""
        again: list[_Compared] = []
        told = _Told(None)
        current: _Compared | None = last
        while current is not None and current not in again:
            lines = self._archives.read_sidecar(current.archive, table.name, "keys")
            kept = told_lines(iter(lines), hasher.digest, current.records) if lines else None
            if kept is not None:
                told = _Told(kept[1], kept[0])
                break
            again.append(current)
            if current.rebaselined:
                break  # the told index started again here: from nothing told
            found = by_name.get(current.previous) if current.previous else None
            current = self._compared(found, table) if found is not None else None
        for compared in reversed(again):
            comparison = Comparison(
                self._reader(compared.archive, table.name),
                None if compared.rebaselined else told.pairs,
                hasher,
                self._join(compared.records, told.keys),
                whole=compared.whole,
            )
            told.close()
            told = _Told(comparison.told(), comparison.told_count(), [comparison])
        return told

    def _previous_snapshot(self, earlier: Sequence[Path], table: TableOutcome) -> _Compared | None:
        """The latest earlier table of the same name and source that was compared, in an
        archive whose survey ran to its end."""
        for candidate in reversed(earlier):
            compared = self._compared(candidate, table)
            if compared is not None and self._ended(candidate) == "finished":
                return compared
        return None

    def _sealed_before(self, earlier: Sequence[Path], table: TableOutcome) -> bool:
        """Whether an earlier survey that ran to its end sealed the table ``ok`` without
        comparing it (``changes:`` was set since): its records' shape is not known."""
        return any(
            self._ended(candidate) == "finished" and self._entry(candidate, table) is not None
            for candidate in earlier
        )

    def _entry(self, archive: Path, table: TableOutcome) -> dict[str, Any] | None:
        for entry in self._archives.read_meta(archive).get("tables") or []:
            if (
                entry.get("name") == table.name
                and entry.get("source") == table.source
                and entry.get("status") == "ok"
            ):
                return dict(entry)
        return None

    def _compared(self, archive: Path, table: TableOutcome) -> _Compared | None:
        entry = self._entry(archive, table)
        changes = entry.get("changes") if entry is not None else None
        if entry is None or not isinstance(changes, dict):
            return None
        stream = self._archives.read_meta(archive).get("stream")
        whole = isinstance(stream, dict) or (
            not entry.get("truncated") and self._ended(archive) == "finished"
        )
        return _Compared(archive, int(entry.get("records") or 0), whole,
                         bool(changes.get("rebaselined")), changes.get("previous"),
                         changes.get("normalize"))  # fmt: skip

    def _ended(self, archive: Path) -> Any:
        return self._archives.read_meta(archive).get("ended")

    def _reader(self, archive: Path, output: str) -> Callable[[], Iterable[dict[str, Any]]]:
        def read() -> Iterable[dict[str, Any]]:
            records = self._archives.read_table(archive, output)
            return records if records is not None else ()

        return read


def _generator(rows: Iterable[dict[str, Any]]) -> Generator[dict[str, Any], None, None]:
    yield from rows

"""Change detection (rulings C1-C7): what changed in a table since its baseline, as a table of
its own (``<output>.changes``), and the key index each table keeps beside it.

**The hash** (C2) is a keyed BLAKE2b of 16 bytes over the canonical JSON of the record as its
table holds it (``sort_keys``, compact separators, ``ensure_ascii=False``), without its
``volatile`` fields and recon's own (``_``). Its key is the mission's (``MissionStore.
change_key``): a file of mode 0600 made on first use, so a low-entropy record (a one-time
code in a subject) cannot be recovered by hashing guesses. It is an input to the determinism
contract: the same baseline, table, spec and key give the same change table, byte for byte.
A hash is kept only in the key index, never in a change row, a warning or telemetry.

**The key index** (C6) of a table, ``<output>.keys.jsonl``, written when its changes are, is a
header (``spec``: the digest of the key fields, the volatile fields and the key's identity;
the table's record and keyless counts) and then a line per key, sorted by key, with its hash
(null for a key held with two contents). It is a cache: one that is missing, or whose header
does not match the spec and the table, is rebuilt from the table, which it never outranks.

**The diff** (C3) reads the baseline's index (a map, so each lookup is O(1) and the diff
O(B + N)), then the new table twice: once for its own index, which finds its ambiguous keys,
then in order, for its inserts and updates; then, when deletes are told, the baseline's table
in order, for its deletes. The rows are a pure function of (B, N, spec, key).

**A stream** (C7) compares each window with the index of every record its chain has told,
``index(k) = fold(index(k-1), window k)``, which each window keeps as its key index; a window
never yields a delete. A window's index is checked when it is read (its spec, and its own
table's count), and rebuilt by folding its chain back from its first window kept when it does
not hold.
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
    from recon.domain.changes import ChangesSpec, DeletesWithheld
    from recon.domain.documents import TableOutcome

#: The bytes of a record's hash: enough that two records of one table never share one by
#: chance, few enough that an index of a million keys stays small.
HASH_BYTES = 16

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

    def key(self, record: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        """The record's key, as the text it is looked up by and as ``{field: value}``; None
        for a keyless record (a key field absent or null)."""
        values = {name: record.get(name) for name in self.spec.key}
        if any(value is None for value in values.values()):
            return None
        return canonical(list(values.values())), values

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


@dataclass
class KeyIndex:
    """A table's keys, each with its content's hash (None: held with two contents, so
    ambiguous), and how many records it held and how many of them had no key."""

    hashes: dict[str, str | None] = field(default_factory=dict)
    records: int = 0
    keyless: int = 0

    @property
    def ambiguous(self) -> int:
        return sum(1 for value in self.hashes.values() if value is None)

    def add(self, text: str, content: str) -> None:
        """A record of key ``text`` and hash ``content``: the same content again is one
        record; another makes the key ambiguous."""
        if text in self.hashes and self.hashes[text] != content:
            self.hashes[text] = None
        else:
            self.hashes.setdefault(text, content)

    def lines(self, spec: str, fields: Sequence[str]) -> Iterator[dict[str, Any]]:
        """The index as its sidecar holds it: a header, then a line per key (``{field:
        value}`` of the key ``fields``), sorted."""
        yield {"spec": spec, "records": self.records, "keyless": self.keyless}
        for text in sorted(self.hashes):
            yield {"key": dict(zip(fields, json.loads(text), strict=True)),
                   "hash": self.hashes[text]}  # fmt: skip

    @classmethod
    def read(
        cls, lines: Iterator[dict[str, Any]], spec: str, records: int | None
    ) -> KeyIndex | None:
        """The index a sidecar holds, when its header is for ``spec`` and for a table of
        ``records`` records (None: not checked); None when it is not."""
        header = next(lines, None)
        if not isinstance(header, dict) or header.get("spec") != spec:
            return None
        if records is not None and header.get("records") != records:
            return None
        index = cls(records=int(header.get("records", 0)), keyless=int(header.get("keyless", 0)))
        for line in lines:
            index.hashes[canonical(list(line["key"].values()))] = line["hash"]
        return index


def index_of(records: Iterable[dict[str, Any]], hasher: Hasher) -> KeyIndex:
    """The key index of a table's records."""
    index = KeyIndex()
    for record in records:
        index.records += 1
        found = hasher.key(record)
        if found is None:
            index.keyless += 1
            continue
        index.add(found[0], hasher.content(record))
    return index


@dataclass
class Counts:
    """What a diff found: its rows by op, and the records it could not compare."""

    inserts: int = 0
    updates: int = 0
    deletes: int = 0
    withheld: DeletesWithheld | None = None


def diff(
    new: Callable[[], Iterable[dict[str, Any]]],
    new_index: KeyIndex,
    base: KeyIndex | None,
    base_records: Callable[[], Iterable[dict[str, Any]]] | None,
    hasher: Hasher,
    counts: Counts,
    *,
    whole: bool,
) -> Iterator[dict[str, Any]]:
    """The change rows of the table ``new`` (its records in order, read again on each call)
    against the baseline ``base`` (None: there is none, so every key is an insert): its
    inserts and updates in its order, then, when the spec tells deletes, ``new`` is ``whole``
    (sealed ok, not truncated, its survey finished) and it holds no keyless and no ambiguous
    record, the baseline's deletes in the order of ``base_records``. ``counts`` is filled as
    the rows are given."""
    told: set[str] = set()
    for record in new():
        found = hasher.key(record)
        if found is None:
            continue
        text, key = found
        if new_index.hashes.get(text) is None or text in told:
            continue  # ambiguous, or a second record of the same content: one row at most
        told.add(text)
        if base is None or text not in base.hashes:
            counts.inserts += 1
            yield {"op": "insert", "key": key, "record": record}
        elif base.hashes[text] != new_index.hashes[text]:
            counts.updates += 1  # its content changed, or the baseline's was ambiguous
            yield {"op": "update", "key": key, "record": record}
    if not hasher.spec.deletes or base is None or base_records is None:
        return
    withheld: DeletesWithheld | None = None
    if not whole:
        withheld = "truncated"
    elif new_index.keyless:
        withheld = "keyless"
    elif new_index.ambiguous:
        withheld = "ambiguous"
    if withheld is not None:
        counts.withheld = withheld
        return
    gone: set[str] = set()
    for record in base_records():
        found = hasher.key(record)
        if found is None:
            continue
        text, key = found
        if text in new_index.hashes or text in gone or base.hashes.get(text) is None:
            continue  # still there, told already, or ambiguous in the baseline
        gone.add(text)
        counts.deletes += 1
        yield {"op": "delete", "key": key, "record": record}


def fold(base: KeyIndex | None, window: KeyIndex) -> KeyIndex:
    """A stream's index after a window: every key its chain has told, each with its latest
    hash. A key a window holds ambiguous keeps what the chain told of it."""
    folded = KeyIndex(
        hashes=dict(base.hashes) if base else {}, records=window.records, keyless=window.keyless
    )
    for text, content in window.hashes.items():
        if content is not None:
            folded.hashes[text] = content
    return folded


class ChangeDetector:
    """Writes the change table and the key index of each table of a mission whose entry has
    ``changes:``, through the archive store, under the mission's ``secret``. It writes into
    the archive (or window) being written alone: an earlier one, sealed, is only read, its
    index rebuilt in memory when it does not hold."""

    def __init__(self, archives: ArchiveStore, mission_dir: Path, secret: bytes) -> None:
        self._archives = archives
        self._mission_dir = mission_dir
        self._secret = secret

    def snapshot(
        self,
        archive: Path,
        table: TableOutcome,
        spec: ChangesSpec,
        normalize: dict[str, str] | None,
    ) -> TableOutcome:
        """``table`` of ``archive``, sealed ``ok``, compared with its baseline: the latest
        earlier archive of the mission whose table of the same name and source sealed ``ok``.
        Its change table and key index are written, and the outcome says what was found. Run
        once every table of the survey has run, so the survey reaches its end."""
        hasher = Hasher(spec, self._secret)
        new = self._reader(archive, table.name)
        new_index = index_of(new(), hasher)
        self._archives.write_sidecar(
            archive, table.name, "keys", new_index.lines(hasher.digest, hasher.spec.key)
        )
        shape = normalize_digest(normalize)
        found = self._baseline(archive, table)
        counts = Counts()
        rebaselined = found is not None and found.normalize != shape
        rows: Iterable[dict[str, Any]]
        if rebaselined:
            rows = [{"op": "rebaselined", "key": None, "record": None}]
        else:
            base = base_records = None
            if found is not None:
                base = self._index(found.archive, table.name, hasher, found.records)
                base_records = self._reader(found.archive, table.name)
            rows = diff(new, new_index, base, base_records, hasher, counts,
                        whole=not table.truncated)  # fmt: skip
        written = self._archives.write_table(
            archive, changes_output(table.name), _generator(rows), columns=CHANGE_COLUMNS
        )
        summary = ChangeSummary(
            table=changes_output(table.name),
            type_url=CHANGES_TYPE_URL,
            baseline=found.archive.name if found is not None else None,
            spec=hasher.digest,
            normalize=shape,
            rows=written,
            inserts=counts.inserts,
            updates=counts.updates,
            deletes=counts.deletes,
            rebaselined=rebaselined,
            keyless=new_index.keyless,
            ambiguous=new_index.ambiguous,
            deletes_withheld=counts.withheld,
        )
        return table.model_copy(update={"changes": summary})

    def window(
        self,
        archive: Path,
        stream: str,
        previous: str | None,
        table: TableOutcome,
        spec: ChangesSpec,
    ) -> TableOutcome:
        """A stream's window, sealed ``ok``, compared with the index of everything its chain
        has told up to the window ``previous`` (None: the first window). Never a delete. Its
        index, the fold, is kept as its key index."""
        hasher = Hasher(spec.model_copy(update={"deletes": False}), self._secret)
        new = self._reader(archive, table.name)
        window_index = index_of(new(), hasher)
        windows = {w.name: w for w in self._archives.archives(self._mission_dir, stream=stream)}
        told = self._told(windows, previous, table.name, hasher)
        counts = Counts()
        rows = diff(new, window_index, told, None, hasher, counts, whole=True)
        written = self._archives.write_table(
            archive, changes_output(table.name), _generator(rows), columns=CHANGE_COLUMNS
        )
        folded = fold(told, window_index)
        self._archives.write_sidecar(
            archive, table.name, "keys", folded.lines(hasher.digest, hasher.spec.key)
        )
        summary = ChangeSummary(
            table=changes_output(table.name),
            type_url=CHANGES_TYPE_URL,
            baseline=previous,
            spec=hasher.digest,
            normalize=None,
            rows=written,
            inserts=counts.inserts,
            updates=counts.updates,
            keyless=window_index.keyless,
            ambiguous=window_index.ambiguous,
        )
        return table.model_copy(update={"changes": summary})

    # --- the store ---

    def _reader(self, archive: Path, output: str) -> Callable[[], Iterable[dict[str, Any]]]:
        def read() -> Iterable[dict[str, Any]]:
            records = self._archives.read_table(archive, output)
            return records if records is not None else ()

        return read

    def _index(self, archive: Path, output: str, hasher: Hasher, records: int) -> KeyIndex:
        """An earlier table's key index: its sidecar when that holds for the spec and the
        table, else rebuilt from the table, in memory."""
        lines = self._archives.read_sidecar(archive, output, "keys")
        if lines is not None and (kept := KeyIndex.read(iter(lines), hasher.digest, records)):
            return kept
        return index_of(self._reader(archive, output)(), hasher)

    def _baseline(self, archive: Path, table: TableOutcome) -> _Baseline | None:
        """The latest archive before ``archive`` whose table of the same name and source
        sealed ``ok``."""
        earlier = [a for a in self._archives.archives(self._mission_dir) if a.name < archive.name]
        for candidate in reversed(earlier):
            for entry in self._archives.read_meta(candidate).get("tables") or []:
                if (
                    entry.get("name") == table.name
                    and entry.get("source") == table.source
                    and entry.get("status") == "ok"
                ):
                    changes = entry.get("changes")
                    shape = changes.get("normalize") if isinstance(changes, dict) else None
                    return _Baseline(candidate, shape, int(entry.get("records") or 0))
        return None

    def _told(
        self, windows: dict[str, Path], previous: str | None, output: str, hasher: Hasher
    ) -> KeyIndex | None:
        """What a stream's chain told up to the window ``previous``: its key index when that
        holds for the spec and its table, else folded again, in memory, from the earliest
        window of the chain still kept."""
        chain: list[Path] = []
        kept: KeyIndex | None = None
        name = previous
        while name is not None and (window := windows.get(name)) is not None:
            lines = self._archives.read_sidecar(window, output, "keys")
            count = self._archives.table_ends(window, output).records
            if lines is not None and (kept := KeyIndex.read(iter(lines), hasher.digest, count)):
                break
            chain.append(window)
            stream = self._archives.read_meta(window).get("stream")
            name = stream.get("previous") if isinstance(stream, dict) else None
        for again in reversed(chain):
            kept = fold(kept, index_of(self._reader(again, output)(), hasher))
        return kept


@dataclass(frozen=True)
class _Baseline:
    """A table's baseline: its archive, the digest of the ``normalize:`` its table was made
    with (None when it does not say), and its record count."""

    archive: Path
    normalize: str | None
    records: int


def _generator(rows: Iterable[dict[str, Any]]) -> Generator[dict[str, Any], None, None]:
    yield from rows

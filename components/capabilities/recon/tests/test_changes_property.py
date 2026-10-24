"""Change detection, property-based (ixian's round-4 C1-C9, rulings C2-C6): random histories of
a small mailbox, checked against an oracle written independently of the code (sets of contents
per key, where the code keeps one hash per key).

``hypothesis`` is not a dev dependency, so each property runs over seeded ``random`` cases
(500 for the pure diff, fewer end to end), and a failure names its seed. The history is what a
mission's surveys see: each snapshot ``complete`` (sealed ok, its crawl whole), ``truncated``
(sealed ok, its crawl cut short) or ``failed`` (no table): a baseline is the latest earlier
snapshot that sealed ok.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from recon.adapters._out.archive_files import ArchiveFiles
from recon.application.changes import Counts, Hasher, KeyIndex, canonical, diff, index_of
from recon.composition import root
from recon.domain.changes import ChangesSpec
from recon.domain.errors import CollectionError
from recon.domain.http import HttpResponse
from tests.recon_fakes import FixedClock

if TYPE_CHECKING:
    from recon.domain.models import SourceEntry

CASES = 500
SECRET = b"k" * 32
KEYS = [f"m{n}" for n in range(6)]  # a small pool, so keys collide across snapshots

Snapshot = tuple[list[dict[str, Any]], str]  # records, and complete | truncated | failed


def _snapshot(
    rng: random.Random,
    *,
    keyless: bool = True,
    ambiguous: bool = True,
    statuses: tuple[str, ...] = ("complete", "truncated", "failed"),
) -> Snapshot:
    records = []
    for key in rng.sample(KEYS, rng.randint(0, len(KEYS))):
        record: dict[str, Any] = {"id": key, "subject": f"s{rng.randint(0, 2)}",
                                  "seen": rng.random(),
                                  "_position": {"page": 0, "i": rng.randint(0, 9)}}  # fmt: skip
        records.append(record)
        if rng.random() < 0.15:  # the same message twice
            records.append(dict(record))
        if ambiguous and rng.random() < 0.08:  # one key, two contents
            records.append({**record, "subject": record["subject"] + "!"})
    if keyless and rng.random() < 0.08:
        records.append({"id": None, "subject": "no id"})
    rng.shuffle(records)
    records = [dict(rng.sample(list(r.items()), len(r))) for r in records]  # field order
    return records, rng.choice(statuses)


def _history(rng: random.Random, **kwargs: Any) -> list[Snapshot]:
    return [_snapshot(rng, **kwargs) for _ in range(rng.randint(1, 6))]


# --- the oracle ----------------------------------------------------------------------------


def _content(record: dict[str, Any], volatile: set[str]) -> str:
    kept = {k: v for k, v in record.items() if not k.startswith("_") and k not in volatile}
    return json.dumps(kept, sort_keys=True)


def _keyed(records: list[dict[str, Any]], spec: ChangesSpec) -> tuple[dict[str, set[str]], int]:
    """Each key (in first appearance order) with the set of contents it has, and the keyless."""
    keyed: dict[str, set[str]] = {}
    keyless = 0
    for record in records:
        if any(record.get(f) is None for f in spec.key):
            keyless += 1
            continue
        key = json.dumps([record[f] for f in spec.key])
        keyed.setdefault(key, set()).add(_content(record, set(spec.volatile)))
    return keyed, keyless


def oracle(
    base: list[dict[str, Any]] | None, new: list[dict[str, Any]], whole: bool, spec: ChangesSpec
) -> list[tuple[str, str]]:
    """The ops the ruling gives, as (op, key), in order."""
    now, keyless = _keyed(new, spec)
    ambiguous = {k for k, contents in now.items() if len(contents) > 1}
    before = _keyed(base, spec)[0] if base is not None else None
    ops = []
    for key, contents in now.items():
        if key in ambiguous:
            continue
        if before is None or key not in before:
            ops.append(("insert", key))
        elif len(before[key]) > 1 or before[key] != contents:
            ops.append(("update", key))
    if spec.deletes and before is not None and whole and not keyless and not ambiguous:
        ops += [("delete", k) for k, c in before.items() if k not in now and len(c) == 1]
    return ops


# --- the code, over a history ----------------------------------------------------------


def _ops(rows: list[dict[str, Any]], spec: ChangesSpec) -> list[tuple[str, str]]:
    return [(r["op"], json.dumps([r["key"][f] for f in spec.key])) for r in rows]


def run_history(
    history: list[Snapshot], spec: ChangesSpec
) -> list[tuple[list[dict[str, Any]] | None, list[tuple[str, str]] | None]]:
    """Each snapshot's change rows (None for a failed one), and the oracle's ops for it."""
    hasher = Hasher(spec, SECRET)
    sealed: list[list[dict[str, Any]]] = []
    out: list[tuple[list[dict[str, Any]] | None, list[tuple[str, str]] | None]] = []
    for records, status in history:
        if status == "failed":
            out.append((None, None))
            continue
        base = sealed[-1] if sealed else None
        whole = status == "complete"
        new_index = index_of(records, hasher)
        base_index = index_of(base, hasher) if base is not None else None
        rows = list(diff(lambda r=records: r, new_index, base_index,  # type: ignore[misc]
                         (lambda b=base: b) if base is not None else None,  # type: ignore[misc]
                         hasher, Counts(), whole=whole))  # fmt: skip
        out.append((rows, oracle(base, records, whole, spec)))
        sealed.append(records)
    return out


def _seeds(n: int = CASES) -> list[int]:
    return list(range(n))


SPECS = [
    ChangesSpec(key=["id"], volatile=["seen"], deletes=True),
    ChangesSpec(key=["id"], volatile=["seen"]),
]


class TestTheDiffIsTheOracles:
    @pytest.mark.parametrize("spec", SPECS, ids=["deletes", "no-deletes"])
    def test_c1_the_ops_are_the_oracle_s(self, spec: ChangesSpec) -> None:
        for seed in _seeds():
            for step, (rows, expected) in enumerate(
                run_history(_history(random.Random(seed)), spec)
            ):
                if rows is not None:
                    assert _ops(rows, spec) == expected, f"seed {seed}, snapshot {step}"

    def test_c2_folding_the_ops_gives_the_latest_whole_snapshot(self) -> None:
        """The strongest: every snapshot complete or failed (no keyless or ambiguous record,
        which withhold deletes), deletes told; folding every row from empty gives the latest
        complete snapshot, key by key and content by content."""
        spec = SPECS[0]
        for seed in _seeds():
            history = _history(random.Random(seed), keyless=False, ambiguous=False,
                               statuses=("complete", "failed"))  # fmt: skip
            state = _fold(run_history(history, spec), spec)
            latest = [r for r, s in history if s == "complete"]
            if latest:
                assert state == _state(latest[-1], spec), f"seed {seed}"

    def test_c2_check_of_the_checker_without_deletes_the_fold_keeps_what_went(self) -> None:
        spec = SPECS[1]
        history: list[Snapshot] = [([{"id": "a"}, {"id": "b"}], "complete"),
                                   ([{"id": "a"}], "complete")]  # fmt: skip
        assert _fold(run_history(history, spec), spec) != _state(history[-1][0], spec)

    @pytest.mark.xfail(
        strict=True,
        reason="owner decision: a truncated table is a baseline (ruling C3: 'sealed ok'), so a "
        "key gone while a truncated snapshot intervened is never deleted; ixian's C5 diffs "
        "against the last complete snapshot instead",
    )
    def test_c2_holds_when_a_truncated_snapshot_intervenes(self) -> None:
        spec = SPECS[0]
        history: list[Snapshot] = [
            ([{"id": "a"}, {"id": "b"}, {"id": "c"}], "complete"),
            ([{"id": "a"}], "truncated"),
            ([{"id": "a"}, {"id": "b"}], "complete"),
        ]
        assert _fold(run_history(history, spec), spec) == _state(history[-1][0], spec)

    def test_c3_the_same_snapshot_twice_gives_no_ops(self) -> None:
        for seed in _seeds():
            rng = random.Random(seed)
            records, _ = _snapshot(rng)
            again = [dict(r) for r in records]
            [_, (rows, _)] = run_history([(records, "complete"), (again, "complete")], SPECS[0])
            assert rows == [], f"seed {seed}"

    def test_c4_a_change_to_a_volatile_field_alone_is_no_change(self) -> None:
        for seed in _seeds():
            rng = random.Random(seed)
            records, _ = _snapshot(rng, ambiguous=False)
            touched = [{**r, "seen": rng.random()} for r in records]
            [_, (rows, _)] = run_history([(records, "complete"), (touched, "complete")], SPECS[0])
            assert rows == [], f"seed {seed}"

    def test_c4_check_of_the_checker_undeclared_it_is_an_update(self) -> None:
        spec = ChangesSpec(key=["id"])
        records = [{"id": "a", "seen": 1}]
        [_, (rows, _)] = run_history(
            [(records, "complete"), ([{"id": "a", "seen": 2}], "complete")], spec
        )
        assert [r["op"] for r in rows or []] == ["update"]

    def test_c5_a_truncated_or_failed_snapshot_never_deletes(self) -> None:
        spec = SPECS[0]
        for seed in _seeds():
            history = _history(random.Random(seed))
            for (rows, _), (_, status) in zip(run_history(history, spec), history, strict=True):
                if status != "complete":
                    assert not any(r["op"] == "delete" for r in rows or []), f"seed {seed}"

    def test_c5_a_failed_snapshot_is_no_baseline(self) -> None:
        """A page-3 failure must not mark every later message deleted, nor be compared with."""
        spec = SPECS[0]
        history: list[Snapshot] = [([{"id": "a"}, {"id": "b"}], "complete"),
                                   ([{"id": "a"}], "failed"),
                                   ([{"id": "a"}, {"id": "b"}], "complete")]  # fmt: skip
        runs = run_history(history, spec)
        assert runs[1] == (None, None) and runs[2][0] == []

    def test_c6_the_order_of_fields_or_records_is_no_change(self) -> None:
        for seed in _seeds():
            rng = random.Random(seed)
            records, _ = _snapshot(rng)
            shuffled = [dict(rng.sample(list(r.items()), len(r))) for r in records]
            rng.shuffle(shuffled)
            [_, (rows, _)] = run_history([(records, "complete"), (shuffled, "complete")], SPECS[0])
            assert rows == [], f"seed {seed}"

    def test_c7_a_key_twice_is_one_record_or_ambiguous_and_ambiguity_holds_back_deletes(
        self,
    ) -> None:
        spec = SPECS[0]
        hasher = Hasher(spec, SECRET)
        base = [{"id": "a", "s": 1}, {"id": "gone", "s": 1}]
        same = [{"id": "a", "s": 2}, {"id": "a", "s": 2}]
        index = index_of(same, hasher)
        assert index.ambiguous == 0 and len(index.hashes) == 1
        rows = list(diff(lambda: same, index, index_of(base, hasher), lambda: base, hasher,
                         Counts(), whole=True))  # fmt: skip
        assert _ops(rows, spec) == [("update", '["a"]'), ("delete", '["gone"]')]

        twice = [{"id": "a", "s": 2}, {"id": "a", "s": 3}]
        counts = Counts()
        index = index_of(twice, hasher)
        rows = list(diff(lambda: twice, index, index_of(base, hasher), lambda: base, hasher,
                         counts, whole=True))  # fmt: skip
        assert rows == [] and index.ambiguous == 1 and counts.withheld == "ambiguous"

    def test_c7_a_key_ambiguous_in_the_baseline_blocks_its_own_delete(self) -> None:
        spec = SPECS[0]
        hasher = Hasher(spec, SECRET)
        base = [{"id": "x", "s": 1}, {"id": "x", "s": 2}, {"id": "y", "s": 1}]
        rows = list(diff(lambda: [], index_of([], hasher), index_of(base, hasher), lambda: base,
                         hasher, Counts(), whole=True))  # fmt: skip
        assert _ops(rows, spec) == [("delete", '["y"]')]

    def test_c7_keyless_records_are_counted_and_hold_back_deletes(self) -> None:
        spec = SPECS[0]
        hasher = Hasher(spec, SECRET)
        base: list[dict[str, Any]] = [{"id": "a"}, {"id": "b"}]
        new: list[dict[str, Any]] = [{"id": "a"}, {"id": None}, {"subject": "no id at all"}]
        counts = Counts()
        index = index_of(new, hasher)
        rows = list(diff(lambda: new, index, index_of(base, hasher), lambda: base, hasher, counts,
                         whole=True))  # fmt: skip
        assert rows == [] and index.keyless == 2 and counts.withheld == "keyless"

    def test_c8_the_first_snapshot_is_all_inserts(self) -> None:
        for seed in _seeds():
            records, _ = _snapshot(random.Random(seed))
            [(rows, expected)] = run_history([(records, "complete")], SPECS[0])
            assert rows is not None and all(r["op"] == "insert" for r in rows)
            assert _ops(rows, SPECS[0]) == expected, f"seed {seed}"

    def test_c9_an_index_read_back_from_its_lines_is_the_index(self) -> None:
        hasher = Hasher(SPECS[0], SECRET)
        for seed in _seeds():
            records, _ = _snapshot(random.Random(seed))
            index = index_of(records, hasher)
            back = KeyIndex.read(
                index.lines(hasher.digest, hasher.spec.key), hasher.digest, index.records
            )
            assert back is not None and back.hashes == index.hashes, f"seed {seed}"
            assert (back.records, back.keyless) == (index.records, index.keyless)
            assert (
                KeyIndex.read(index.lines(hasher.digest, hasher.spec.key), "another spec", None)
                is None
            )
            assert (
                KeyIndex.read(
                    index.lines(hasher.digest, hasher.spec.key), hasher.digest, index.records + 1
                )
                is None
            )


def _fold(
    runs: list[tuple[list[dict[str, Any]] | None, list[tuple[str, str]] | None]], spec: ChangesSpec
) -> dict[str, str]:
    state: dict[str, str] = {}
    for rows, _ in runs:
        for row in rows or []:
            key = json.dumps([row["key"][f] for f in spec.key])
            if row["op"] in ("insert", "update"):
                state[key] = _content(row["record"], set(spec.volatile))
            elif row["op"] == "delete":
                state.pop(key)
    return state


def _state(records: list[dict[str, Any]], spec: ChangesSpec) -> dict[str, str]:
    keyed = _keyed(records, spec)[0]
    return {k: next(iter(c)) for k, c in keyed.items()}


def test_the_hash_is_keyed_and_canonical() -> None:
    spec = ChangesSpec(key=["id"], volatile=["seen"])
    one, other = Hasher(spec, b"a" * 32), Hasher(spec, b"b" * 32)
    record = {"id": 1, "subject": "Your code is 482913", "seen": 1, "_position": {"page": 0}}
    assert one.content(record) != other.content(record), "keyed: another key, another hash"
    assert one.content(record) == one.content({"seen": 2, "subject": "Your code is 482913",
                                               "id": 1})  # fmt: skip
    assert len(one.content(record)) == 32, "16 bytes"
    assert one.digest != other.digest and "482913" not in canonical(one.digest)


# --- end to end, through recon's surveys ---------------------------------------------------


class _Mailbox:
    """The Requester port answering survey i with snapshot i: one page, which names a next
    one when the snapshot is truncated (the crawl's cap of one page then cuts it); a failed
    snapshot's request fails."""

    def __init__(self) -> None:
        self.snapshot: Snapshot = ([], "complete")

    def request(self, source: SourceEntry, method: str, url: str, **kw: Any) -> HttpResponse:
        records, status = self.snapshot
        if status == "failed":
            raise CollectionError("the mailbox is down", kind="transient")
        body = {"items": records, "next": "more" if status == "truncated" else None}
        text = json.dumps(body)
        return HttpResponse(status_code=200, headers={}, body=text.encode(), text=text, url=url,
                            content_type="application/json")  # fmt: skip

    def get(self, source: SourceEntry, url: str, **kw: Any) -> HttpResponse:
        return self.request(source, "GET", url)


def _mission(spec: ChangesSpec) -> dict[str, Any]:
    return {
        "catalog": [{"name": "mail", "url": "https://mail.test"}],
        "collectors": [
            {"name": "inbox", "type": "api", "source": "mail", "endpoint": "/messages",
             "extract": "items",
             "paginate": {"type": "cursor", "next": "next", "param": "cursor", "pages": 1},
             "changes": spec.model_dump()}
        ],
    }  # fmt: skip


@pytest.mark.parametrize("spec", SPECS, ids=["deletes", "no-deletes"])
def test_end_to_end_each_survey_s_change_table_is_the_oracle_s(
    tmp_path: Path, spec: ChangesSpec
) -> None:
    """C1, C5 and C9 through recon's root: the baseline each survey picks from its archives,
    a truncated crawl and a failed table as the survey makes them, and each key index equal
    to the one rebuilt from its table."""
    for seed in range(25):
        project = tmp_path / str(seed)
        (project / ".recon" / "m").mkdir(parents=True)
        (project / ".recon" / "m" / "config.yaml").write_text(yaml.safe_dump(_mission(spec)))
        mailbox = _Mailbox()
        clock = FixedClock()
        recon = root({}, project=project, clock=clock,
                     requester=lambda t, d, r, m=mailbox: m)  # type: ignore[misc]  # fmt: skip
        secret = project / ".recon" / "m" / "changes.key"
        history = _history(random.Random(seed))
        sealed: list[list[dict[str, Any]]] = []
        for step, (records, status) in enumerate(history):
            clock.advance(1)
            mailbox.snapshot = (records, status)
            document = recon.survey("m")
            archive = Path(document.path)
            [table] = document.tables
            where = f"seed {seed}, survey {step} ({status})"
            if status == "failed":
                assert table.status == "error" and table.changes is None, where
                assert not (archive / "inbox.changes.jsonl").exists(), where
                continue
            assert table.changes is not None, where
            rows = [
                json.loads(line)
                for line in (archive / "inbox.changes.jsonl").read_text().splitlines()
            ]
            rows = [] if rows == [{"_empty": True, "_columns": ["op", "key", "record"]}] else rows
            base = sealed[-1] if sealed else None
            # the records as the table holds them, _position included
            held = [json.loads(line) for line in (archive / "inbox.jsonl").read_text().splitlines()]
            held = [] if held and held[0].get("_empty") else held
            assert _ops(rows, spec) == oracle(base, held, status == "complete", spec), where
            hasher = Hasher(spec, secret.read_bytes())
            kept = ArchiveFiles(clock).read_sidecar(archive, "inbox", "keys")
            assert kept is not None, where
            assert list(kept) == list(
                index_of(held, hasher).lines(hasher.digest, hasher.spec.key)
            ), where
            sealed.append(held)

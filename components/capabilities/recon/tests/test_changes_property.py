"""Change detection, property-based (ixian's round-4 C1-C9, rulings C2-C6 and the owner's ruling
of 2026-10-24 on what was told): random histories of a small mailbox, checked against an
oracle written apart from the code. The oracle keeps what it has told as a map of key to
content and folds its own ops into it; the code keeps a told index of hashes, joined with each
table through a :class:`~recon.domain.changes.KeyJoin`.

``hypothesis`` is not a dev dependency, so each property runs over seeded ``random`` cases
(500 for the comparisons, 25 histories end to end), and a failure names its seed. A history is
what a mission's surveys see: each snapshot ``complete`` (sealed ok, its crawl whole),
``truncated`` (sealed ok, its crawl cut short) or ``failed`` (no table, so nothing told).
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from recon.adapters._out.archive_files import ArchiveFiles
from recon.application.changes import Comparison, Hasher, MemoryJoin, canonical, told_lines
from recon.composition import root
from recon.domain.changes import ChangesSpec
from recon.domain.errors import CollectionError
from recon.domain.http import HttpResponse
from tests.recon_fakes import FixedClock

if TYPE_CHECKING:
    from recon.domain.changes import KeyJoin
    from recon.domain.models import SourceEntry

CASES = 500
SECRET = b"k" * 32
KEYS = [f"m{n}" for n in range(6)]  # a small pool, so keys collide across snapshots

Records = list[dict[str, Any]]
Snapshot = tuple[Records, str]  # records, and complete | truncated | failed
Ops = list[tuple[str, str]]

#: The joins every comparison property runs over; the DuckDB one joins them when it exists.
JOINS: list[KeyJoin] = [MemoryJoin()]


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


def _keyed(records: Records, spec: ChangesSpec) -> tuple[dict[str, set[str]], int]:
    """Each key (in first appearance order) with the set of contents it has, and the keyless."""
    keyed: dict[str, set[str]] = {}
    keyless = 0
    for record in records:
        if any(record.get(f) is None for f in spec.key):
            keyless += 1
            continue
        key = json.dumps([record[f] for f in spec.key], separators=(",", ":"))
        keyed.setdefault(key, set()).add(_content(record, set(spec.volatile)))
    return keyed, keyless


class Oracle:
    """What the ruling tells, snapshot by snapshot: ``told`` is every key told, with the
    content told last."""

    def __init__(self, spec: ChangesSpec) -> None:
        self.spec = spec
        self.told: dict[str, str] | None = None  # None: nothing told yet

    def tell(self, records: Records, whole: bool) -> Ops:
        now, keyless = _keyed(records, self.spec)
        ambiguous = {k for k, contents in now.items() if len(contents) > 1}
        told = self.told if self.told is not None else {}
        ops: Ops = []
        for key, contents in now.items():
            if key in ambiguous:
                continue
            [content] = contents
            if key not in told:
                ops.append(("insert", key))
            elif told[key] != content:
                ops.append(("update", key))
            told[key] = content
        deleting = (self.spec.deletes and self.told is not None and whole and not keyless
                    and not ambiguous)  # fmt: skip
        if deleting:
            gone = sorted(k for k in told if k not in now)
            ops += [("delete", k) for k in gone]
            for key in gone:
                del told[key]
        self.told = told
        return ops


# --- the code, over a history ----------------------------------------------------------


def _ops(rows: list[dict[str, Any]], spec: ChangesSpec) -> Ops:
    return [(r["op"], json.dumps([r["key"][f] for f in spec.key], separators=(",", ":")))
            for r in rows]  # fmt: skip


def run_history(
    history: list[Snapshot], spec: ChangesSpec, join: KeyJoin
) -> list[tuple[list[dict[str, Any]] | None, Ops | None]]:
    """Each snapshot's change rows (None for a failed one) as the code tells them, chaining
    each comparison's told index into the next, and the oracle's ops for it."""
    hasher = Hasher(spec, SECRET)
    oracle = Oracle(spec)
    told: list[tuple[str, str]] | None = None
    out: list[tuple[list[dict[str, Any]] | None, Ops | None]] = []
    for records, status in history:
        if status == "failed":
            out.append((None, None))
            continue
        whole = status == "complete"
        comparison = Comparison(lambda r=records: r, told, hasher, join,  # type: ignore[misc]
                                whole=whole)  # fmt: skip
        rows = list(comparison.rows())
        told = list(comparison.told())
        comparison.close()
        out.append((rows, oracle.tell(records, whole)))
    return out


SPECS = [
    ChangesSpec(key=["id"], volatile=["seen"], deletes=True),
    ChangesSpec(key=["id"], volatile=["seen"]),
]


def _fold(runs: list[tuple[list[dict[str, Any]] | None, Ops | None]], spec: ChangesSpec,
          until: int | None = None) -> dict[str, str]:  # fmt: skip
    state: dict[str, str] = {}
    for rows, _ in runs[: None if until is None else until + 1]:
        for row in rows or []:
            key = json.dumps([row["key"][f] for f in spec.key])
            if row["op"] in ("insert", "update"):
                state[key] = _content(row["record"], set(spec.volatile))
            elif row["op"] == "delete":
                state.pop(key)
    return state


def _state(records: Records, spec: ChangesSpec) -> dict[str, str]:
    return {json.dumps([r[f] for f in spec.key]): _content(r, set(spec.volatile)) for r in records}


@pytest.mark.parametrize("join", JOINS, ids=lambda j: type(j).__name__)
class TestTheComparisonIsTheOracles:
    @pytest.mark.parametrize("spec", SPECS, ids=["deletes", "no-deletes"])
    def test_c1_the_ops_are_the_oracle_s(self, join: KeyJoin, spec: ChangesSpec) -> None:
        for seed in range(CASES):
            history = _history(random.Random(seed))
            for step, (rows, expected) in enumerate(run_history(history, spec, join)):
                if rows is not None:
                    assert _ops(rows, spec) == expected, f"seed {seed}, snapshot {step}"

    def test_c2_after_each_whole_snapshot_the_fold_of_every_op_is_that_snapshot(
        self, join: KeyJoin
    ) -> None:
        """The strongest: truncated and failed snapshots between, deletes told, no keyless or
        ambiguous record (which hold deletes back): after each whole snapshot, folding every
        row told so far from empty gives it, key by key and content by content."""
        spec = SPECS[0]
        for seed in range(CASES):
            history = _history(random.Random(seed), keyless=False, ambiguous=False)
            runs = run_history(history, spec, join)
            for step, (records, status) in enumerate(history):
                if status == "complete":
                    assert _fold(runs, spec, step) == _state(records, spec), f"seed {seed}"

    def test_c2_a_key_gone_while_a_truncated_snapshot_intervened_is_deleted(
        self, join: KeyJoin
    ) -> None:
        """{a,b,c} whole, {a} truncated, {a,b} whole: c is told deleted (the case a truncated
        baseline lost)."""
        spec = SPECS[0]
        history: list[Snapshot] = [
            ([{"id": "a"}, {"id": "b"}, {"id": "c"}], "complete"),
            ([{"id": "a"}], "truncated"),
            ([{"id": "a"}, {"id": "b"}], "complete"),
        ]
        runs = run_history(history, spec, join)
        assert [_ops(rows or [], spec) for rows, _ in runs] == [
            [("insert", '["a"]'), ("insert", '["b"]'), ("insert", '["c"]')],
            [],
            [("delete", '["c"]')],
        ]
        assert _fold(runs, spec) == _state(history[-1][0], spec)

    def test_an_update_a_truncated_run_told_is_not_told_again(self, join: KeyJoin) -> None:
        spec = SPECS[0]
        history: list[Snapshot] = [
            ([{"id": "a", "s": 1}, {"id": "b", "s": 1}], "complete"),
            ([{"id": "a", "s": 2}], "truncated"),
            ([{"id": "a", "s": 2}, {"id": "b", "s": 1}], "complete"),
        ]
        runs = run_history(history, spec, join)
        assert [_ops(rows or [], spec) for rows, _ in runs][1:] == [[("update", '["a"]')], []]

    def test_c2_check_of_the_checker_without_deletes_the_fold_keeps_what_went(
        self, join: KeyJoin
    ) -> None:
        spec = SPECS[1]
        history: list[Snapshot] = [([{"id": "a"}, {"id": "b"}], "complete"),
                                   ([{"id": "a"}], "complete")]  # fmt: skip
        assert _fold(run_history(history, spec, join), spec) != _state(history[-1][0], spec)

    def test_c3_the_same_snapshot_twice_gives_no_ops(self, join: KeyJoin) -> None:
        for seed in range(CASES):
            records, _ = _snapshot(random.Random(seed))
            again = [dict(r) for r in records]
            history: list[Snapshot] = [(records, "complete"), (again, "complete")]
            assert run_history(history, SPECS[0], join)[1][0] == [], f"seed {seed}"

    def test_c4_a_change_to_a_volatile_field_alone_is_no_change(self, join: KeyJoin) -> None:
        for seed in range(CASES):
            rng = random.Random(seed)
            records, _ = _snapshot(rng, ambiguous=False)
            touched = [{**r, "seen": rng.random()} for r in records]
            history: list[Snapshot] = [(records, "complete"), (touched, "complete")]
            assert run_history(history, SPECS[0], join)[1][0] == [], f"seed {seed}"

    def test_c4_check_of_the_checker_undeclared_it_is_an_update(self, join: KeyJoin) -> None:
        history: list[Snapshot] = [([{"id": "a", "seen": 1}], "complete"),
                                   ([{"id": "a", "seen": 2}], "complete")]  # fmt: skip
        rows = run_history(history, ChangesSpec(key=["id"]), join)[1][0]
        assert [r["op"] for r in rows or []] == ["update"]

    def test_c5_a_truncated_or_failed_snapshot_never_deletes(self, join: KeyJoin) -> None:
        spec = SPECS[0]
        for seed in range(CASES):
            history = _history(random.Random(seed))
            for (rows, _), (_, status) in zip(run_history(history, spec, join), history,
                                              strict=True):  # fmt: skip
                if status != "complete":
                    assert not any(r["op"] == "delete" for r in rows or []), f"seed {seed}"

    def test_c5_a_failed_snapshot_tells_nothing(self, join: KeyJoin) -> None:
        """A page-3 failure must not mark every later message deleted."""
        spec = SPECS[0]
        history: list[Snapshot] = [([{"id": "a"}, {"id": "b"}], "complete"),
                                   ([{"id": "a"}], "failed"),
                                   ([{"id": "a"}, {"id": "b"}], "complete")]  # fmt: skip
        runs = run_history(history, spec, join)
        assert runs[1] == (None, None) and runs[2][0] == []

    def test_c6_the_order_of_fields_or_records_is_no_change(self, join: KeyJoin) -> None:
        for seed in range(CASES):
            rng = random.Random(seed)
            records, _ = _snapshot(rng)
            shuffled = [dict(rng.sample(list(r.items()), len(r))) for r in records]
            rng.shuffle(shuffled)
            history: list[Snapshot] = [(records, "complete"), (shuffled, "complete")]
            assert run_history(history, SPECS[0], join)[1][0] == [], f"seed {seed}"

    def test_c7_duplicates_collapse_and_ambiguity_holds_deletes_back(self, join: KeyJoin) -> None:
        spec = SPECS[0]
        base: Records = [{"id": "a", "s": 1}, {"id": "gone", "s": 1}]
        same: Records = [{"id": "a", "s": 2}, {"id": "a", "s": 2}]
        runs = run_history([(base, "complete"), (same, "complete")], spec, join)
        assert _ops(runs[1][0] or [], spec) == [("update", '["a"]'), ("delete", '["gone"]')]

        twice: Records = [{"id": "a", "s": 2}, {"id": "a", "s": 3}]
        hasher = Hasher(spec, SECRET)
        told = Comparison(lambda: base, None, hasher, join, whole=True)
        comparison = Comparison(lambda: twice, list(told.told()), hasher, join, whole=True)
        assert list(comparison.rows()) == []
        assert comparison.counts.ambiguous == 1 and comparison.counts.withheld == "ambiguous"
        assert dict(comparison.told())[canonical(["a"])] == dict(told.told())[canonical(["a"])]

    def test_c7_keyless_records_are_counted_and_hold_deletes_back(self, join: KeyJoin) -> None:
        spec = SPECS[0]
        hasher = Hasher(spec, SECRET)
        base: Records = [{"id": "a"}, {"id": "b"}]
        new: Records = [{"id": "a"}, {"id": None}, {"subject": "no id at all"}]
        told = list(Comparison(lambda: base, None, hasher, join, whole=True).told())
        comparison = Comparison(lambda: new, told, hasher, join, whole=True)
        assert list(comparison.rows()) == []
        assert comparison.counts.keyless == 2 and comparison.counts.withheld == "keyless"

    def test_c8_the_first_snapshot_is_all_inserts(self, join: KeyJoin) -> None:
        for seed in range(CASES):
            records, _ = _snapshot(random.Random(seed))
            [(rows, expected)] = run_history([(records, "complete")], SPECS[0], join)
            assert rows is not None and all(r["op"] == "insert" for r in rows)
            assert _ops(rows, SPECS[0]) == expected, f"seed {seed}"

    def test_c9_a_told_index_read_back_from_its_lines_is_the_index(self, join: KeyJoin) -> None:
        hasher = Hasher(SPECS[0], SECRET)
        for seed in range(CASES):
            records, _ = _snapshot(random.Random(seed))
            comparison = Comparison(lambda r=records: r, None, hasher, join,  # type: ignore[misc]
                                    whole=True)  # fmt: skip
            lines = list(comparison.lines(len(records)))
            read = told_lines(iter(lines), hasher.digest, len(records))
            assert read is not None and read[0] == len(lines) - 1, f"seed {seed}"
            assert list(read[1]) == list(comparison.told()), f"seed {seed}"
            assert told_lines(iter(lines), "another spec", len(records)) is None
            assert told_lines(iter(lines), hasher.digest, len(records) + 1) is None


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


def _lines(path: Path) -> list[dict[str, Any]]:
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    return [] if lines and lines[0].get("_empty") else lines


@pytest.mark.parametrize("spec", SPECS, ids=["deletes", "no-deletes"])
def test_end_to_end_each_survey_s_change_table_is_the_oracle_s(
    tmp_path: Path, spec: ChangesSpec
) -> None:
    """C1, C5 and C9 through recon's root: the previous table each survey picks from its
    archives, a truncated crawl and a failed table as the survey makes them, and each told
    index equal to the one computed again from the chain once the kept ones are gone."""
    for seed in range(25):
        project = tmp_path / str(seed)
        (project / ".recon" / "m").mkdir(parents=True)
        (project / ".recon" / "m" / "config.yaml").write_text(yaml.safe_dump(_mission(spec)))
        mailbox = _Mailbox()
        clock = FixedClock()
        recon = root({}, project=project, clock=clock,
                     requester=lambda t, d, r, m=mailbox: m)  # type: ignore[misc]  # fmt: skip
        oracle = Oracle(spec)
        history = _history(random.Random(seed))
        kept: list[tuple[Path, list[dict[str, Any]]]] = []
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
            held = _lines(archive / "inbox.jsonl")  # as the table holds them, _position too
            expected = oracle.tell(held, status == "complete")
            assert _ops(_lines(archive / "inbox.changes.jsonl"), spec) == expected, where
            text = (archive / "inbox.keys.jsonl").read_text()
            kept.append((archive, [json.loads(line) for line in text.splitlines()]))
        # C9: every told index kept is the one the chain computes again without them
        for archive, _ in kept:
            (archive / "inbox.keys.jsonl").unlink()
        if not kept:
            continue
        last, index = kept[-1]
        # An empty truncated table tells nothing and keeps what was told, so its told index
        # is the one computed again from the chain.
        clock.advance(1)
        mailbox.snapshot = ([], "truncated")
        document = recon.survey("m")
        archive = Path(document.path)
        assert _lines(archive / "inbox.changes.jsonl") == [], f"seed {seed}"
        again = [
            json.loads(line) for line in (archive / "inbox.keys.jsonl").read_text().splitlines()
        ]
        assert again[1:] == index[1:], f"seed {seed}: computed again, it is the one kept"
        assert again[0]["keys"] == index[0]["keys"] == len(index) - 1
        store = ArchiveFiles(clock)
        assert store.read_sidecar(last, "inbox", "keys") is None, (
            "an earlier archive is not written"
        )

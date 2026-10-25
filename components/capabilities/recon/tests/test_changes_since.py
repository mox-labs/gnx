"""Reading change rows across a mission's archives (ruling R1): ``recon.changes_since`` and its
cursor, held by the reader. Ixian's round-4 section D: delivered rows are the change tables
concatenated, duplicates come only from a read the reader never kept, and the ``id``s drop
them (D-R1); a retired archive is said, never passed silently (D-R2); an unsealed change table
is never read (D-R3); a cursor of another mission or output is refused (D-R4)."""

from __future__ import annotations

import json
import random
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from recon.adapters._out.archive_files import ArchiveFiles
from recon.application.subscriptions import row_id
from recon.composition import root
from recon.domain.errors import NotFoundError, UsageError
from tests.recon_fakes import FixedClock, VirtualClock, plugins_with
from tests.test_changes import Surveys, _Feed, _mission, _rows, _stream_mission, _windows

if TYPE_CHECKING:
    from pathlib import Path

A, B, C = ({"id": "a", "subject": "hello"}, {"id": "b", "subject": "hi"},
           {"id": "c", "subject": "yo"})  # fmt: skip


def _archives(project: Path) -> list[Path]:
    home = project / ".recon" / "m" / "archive"
    return sorted(p for p in home.iterdir() if p.is_dir()) if home.is_dir() else []


def _told(project: Path) -> list[tuple[str, int, dict[str, Any]]]:
    """Every row of every sealed change table, in archive order: what was told."""
    told = []
    for archive in _archives(project):
        if (archive / "inbox.changes.jsonl").is_file():
            told += [(archive.name, n, row) for n, row in enumerate(_rows(archive))]
    return told


def _as_told(read: Any) -> list[tuple[str, dict[str, Any]]]:
    return [
        (row.archive, {"op": row.op, "key": row.key, "record": row.record}) for row in read.rows
    ]


class TestEveryRowOnceInOrder:
    def test_the_rows_of_each_sealed_change_table_in_archive_order(self, tmp_path: Path) -> None:
        _mission(tmp_path, {"key": ["id"], "deletes": True})
        surveys = Surveys(tmp_path)
        surveys.survey(A, B)
        surveys.survey(A)  # nothing new but b gone
        surveys.survey(A, C)
        read = surveys.recon.changes_since("m", "inbox")
        assert [(row.op, row.key) for row in read.rows] == [
            ("insert", {"id": "a"}), ("insert", {"id": "b"}), ("delete", {"id": "b"}),
            ("insert", {"id": "c"}),
        ]  # fmt: skip
        assert read.count == 4 and not read.more and read.state == "complete"
        assert read.rows[0].record == A and read.rows[2].record is None
        assert _as_told(read) == [(a, row) for a, _, row in _told(tmp_path)]

    def test_reading_on_from_the_cursor_gives_what_was_told_since_and_nothing_twice(
        self, tmp_path: Path
    ) -> None:
        _mission(tmp_path, {"key": ["id"]})
        surveys = Surveys(tmp_path)
        surveys.survey(A)
        first = surveys.recon.changes_since("m", "inbox")
        assert [row.key for row in first.rows] == [{"id": "a"}]
        caught_up = surveys.recon.changes_since("m", "inbox", first.cursor)
        assert caught_up.rows == [] and not caught_up.more
        surveys.survey(A, B)
        surveys.survey(A, B)  # an empty change table: the read goes past it
        surveys.survey(A, B, C)
        later = surveys.recon.changes_since("m", "inbox", caught_up.cursor)
        assert [row.key for row in later.rows] == [{"id": "b"}, {"id": "c"}]

    def test_a_bound_cuts_a_read_at_a_whole_row_and_says_more(self, tmp_path: Path) -> None:
        _mission(tmp_path, {"key": ["id"]})
        surveys = Surveys(tmp_path)
        surveys.survey(A, B)
        surveys.survey(A, B, C)
        seen, cursor, reads = [], None, 0
        while True:
            read = surveys.recon.changes_since("m", "inbox", cursor, max_records=1)
            reads += 1
            assert read.count <= 1
            seen += [row.key["id"] for row in read.rows if row.key]
            cursor = read.cursor
            if not read.more:
                break
        assert seen == ["a", "b", "c"] and reads <= 4

    def test_an_id_names_one_row_and_the_same_row_read_again_has_it(self, tmp_path: Path) -> None:
        _mission(tmp_path, {"key": ["id"]})
        surveys = Surveys(tmp_path)
        surveys.survey(A, B)
        once = surveys.recon.changes_since("m", "inbox")
        again = surveys.recon.changes_since("m", "inbox")
        assert [row.id for row in once.rows] == [row.id for row in again.rows]
        assert len({row.id for row in once.rows}) == 2
        archive = once.rows[0].archive
        assert once.rows[0].id == row_id(archive, "inbox", 0, "insert", {"id": "a"})
        assert row_id(archive, "inbox", 0, "update", {"id": "a"}) != once.rows[0].id
        assert row_id(archive, "inbox", 1, "insert", {"id": "a"}) != once.rows[0].id

    def test_no_hash_of_a_record_s_content_is_in_a_read(self, tmp_path: Path) -> None:
        """C2 holds through a read: a row's id is a digest of its place and key, never of its
        content, and the content hash is in no read."""
        _mission(tmp_path, {"key": ["id"]})
        surveys = Surveys(tmp_path)
        archive, _ = surveys.survey(A, B)
        index = (archive / "inbox.keys.jsonl").read_text().splitlines()[1:]
        hashes = [json.loads(line)["hash"] for line in index]
        text = json.dumps(surveys.recon.changes_since("m", "inbox").document())
        assert hashes and not any(h in text for h in hashes)

    def test_a_stream_s_change_rows_are_read_across_its_windows(self, tmp_path: Path) -> None:
        _stream_mission(tmp_path)
        records: list[dict[str, Any]] = [{"id": "a", "v": 1}, {"id": "b", "v": 1},
                                         {"id": "a", "v": 2}, {"id": "c"}]  # fmt: skip
        recon = root({}, project=tmp_path, clock=VirtualClock(),
                     plugins=plugins_with(feed=_Feed(records)))  # fmt: skip
        recon.stream("m", "s", windows=2)
        first, second = (w.name for w in _windows(tmp_path))
        read = recon.changes_since("m", "s")
        assert [(row.archive, row.op, row.key) for row in read.rows] == [
            (first, "insert", {"id": "a"}), (first, "insert", {"id": "b"}),
            (second, "update", {"id": "a"}), (second, "insert", {"id": "c"}),
        ]  # fmt: skip


class TestD1AtLeastOnceThroughACrash:
    """Ixian D-R1: surveys at random, reads between them, and a reader that crashes before it
    keeps its cursor (it acted on the rows, then died). What it got, its duplicates dropped by
    ``id``, is every change table concatenated in order; and a row comes twice only when the
    read that first gave it was never kept."""

    @pytest.mark.parametrize("seed", range(12))
    def test_rows_delivered_are_the_change_tables_concatenated(
        self, tmp_path: Path, seed: int
    ) -> None:
        rng = random.Random(seed)
        _mission(tmp_path, {"key": ["id"], "deletes": True})
        surveys = Surveys(tmp_path)
        pool = [{"id": k, "subject": s} for k in "abcdef" for s in ("x", "y")]
        kept: str | None = None  # the cursor the reader keeps, as it would on disk
        got: list[tuple[str, bool]] = []  # each row's id, and whether its read was kept
        for _ in range(rng.randint(4, 14)):
            if rng.random() < 0.5:
                inbox = {m["id"]: m for m in rng.sample(pool, rng.randint(0, 6))}
                surveys.survey(*inbox.values())
                continue
            read = surveys.recon.changes_since("m", "inbox", kept, max_records=rng.randint(1, 4))
            crashed = rng.random() < 0.35  # acted on the rows, then died before keeping it
            got += [(row.id, not crashed) for row in read.rows]
            if not crashed:
                kept = read.cursor
        while True:  # it comes back, and reads on from what it kept, to the end
            read = surveys.recon.changes_since("m", "inbox", kept)
            got += [(row.id, True) for row in read.rows]
            kept = read.cursor
            if not read.more:
                break
        told = [row_id(a, "inbox", n, row["op"], row["key"]) for a, n, row in _told(tmp_path)]
        seen: dict[str, bool] = {}
        for row, was_kept in got:
            if row in seen:
                assert not seen[row], "a row comes again only after a read never kept"
            seen[row] = seen.get(row, False) or was_kept
        assert list(dict.fromkeys(row for row, _ in got)) == told


class TestD2ARetiredArchiveIsSaid:
    def _retiring(self, tmp_path: Path) -> Surveys:
        _mission(tmp_path, {"key": ["id"]}, retain={"archives": 1})
        return Surveys(tmp_path)

    def test_a_reader_that_never_read_them_is_told_they_went(self, tmp_path: Path) -> None:
        surveys = self._retiring(tmp_path)
        first, _ = surveys.survey(A)
        surveys.survey(A, B)
        surveys.survey(A, B, C)
        assert not first.exists(), "retain: removed it"
        read = surveys.recon.changes_since("m", "inbox")
        assert read.retired_range is not None and read.retired_range.first == first.name
        assert read.retired_range.count == 2
        assert [row.key for row in read.rows] == [{"id": "c"}]

    def test_a_cursor_in_an_archive_retired_since_goes_on_and_says_so(self, tmp_path: Path) -> None:
        surveys = self._retiring(tmp_path)
        first, _ = surveys.survey(A, B)
        part = surveys.recon.changes_since("m", "inbox", max_records=1)
        assert [row.key for row in part.rows] == [{"id": "a"}] and part.more
        second, _ = surveys.survey(A, B, C)
        assert not first.exists()
        read = surveys.recon.changes_since("m", "inbox", part.cursor)
        assert read.retired_range is not None
        assert (read.retired_range.first, read.retired_range.count) == (first.name, 1)
        assert [(row.archive, row.key) for row in read.rows] == [(second.name, {"id": "c"})]

    def test_an_archive_retired_between_two_kept_is_said_before_the_rows_after_it(
        self, tmp_path: Path
    ) -> None:
        _mission(tmp_path, {"key": ["id"]})
        surveys = Surveys(tmp_path)
        first, _ = surveys.survey(A)
        middle, _ = surveys.survey(A, B)
        surveys.survey(A, B, C)
        ArchiveFiles(FixedClock()).retire(middle, {"id": middle.name, "rule": "test"})
        read = surveys.recon.changes_since("m", "inbox")
        assert [row.key for row in read.rows] == [{"id": "a"}] and read.more
        assert read.retired_range is None
        on = surveys.recon.changes_since("m", "inbox", read.cursor)
        assert on.retired_range is not None and on.retired_range.first == middle.name
        assert [row.key for row in on.rows] == [{"id": "c"}]


class TestD3AnUnsealedChangeTableIsNeverRead:
    def test_one_its_survey_died_writing_is_never_read_and_the_next_tells_its_rows(
        self, tmp_path: Path
    ) -> None:
        _mission(tmp_path, {"key": ["id"]})
        surveys = Surveys(tmp_path)
        died, _ = surveys.survey(A, B)
        # Killed while writing its change table: the rows on disk, never sealed, and its
        # meta.yaml the one written while it compared.
        (died / "inbox.changes.jsonl").rename(died / "inbox.changes.jsonl.tmp")
        meta = yaml.safe_load((died / "meta.yaml").read_text())
        (died / "meta.yaml").write_text(yaml.safe_dump({**meta, "ended": None}))
        read = surveys.recon.changes_since("m", "inbox")
        assert read.rows == [] and not read.more
        again, _ = surveys.survey(A, B)
        on = surveys.recon.changes_since("m", "inbox", read.cursor)
        assert [(row.archive, row.op, row.key) for row in on.rows] == [
            (again.name, "insert", {"id": "a"}), (again.name, "insert", {"id": "b"}),
        ]  # fmt: skip

    def test_one_being_written_holds_the_reader_until_it_is_sealed(self, tmp_path: Path) -> None:
        _mission(tmp_path, {"key": ["id"]})
        surveys = Surveys(tmp_path)
        surveys.survey(A)
        store = ArchiveFiles(FixedClock())
        mission = tmp_path / ".recon" / "m"
        writing = store.create(mission, "2030-01-01-000000-000000")
        row = {"op": "insert", "key": {"id": "z"}, "record": {"id": "z"}}
        (writing / "inbox.changes.jsonl.tmp").write_text(json.dumps(row) + "\n")
        read = surveys.recon.changes_since("m", "inbox")
        assert [r.key for r in read.rows] == [{"id": "a"}]
        assert read.state == "writing" and not read.more
        held = surveys.recon.changes_since("m", "inbox", read.cursor)
        assert held.rows == [] and held.state == "writing", "the reader waits on it"
        (writing / "inbox.changes.jsonl.tmp").rename(writing / "inbox.changes.jsonl")
        store.release(writing)
        sealed = surveys.recon.changes_since("m", "inbox", held.cursor)
        assert [r.key for r in sealed.rows] == [{"id": "z"}]


def _body(**changed: Any) -> str:
    """A change cursor of mission m's inbox, with ``changed`` in it."""
    body = {"mission": "m", "output": "inbox", "archive": None, "row": 0, "at": None,
            "done": False, **changed}  # fmt: skip
    return "c1" + json.dumps(body).encode().hex()


class TestD4AForeignCursorIsRefused:
    def _two(self, tmp_path: Path) -> Surveys:
        collectors = [{"name": name, "type": "api", "source": "mail", "endpoint": "/m",
                       "extract": "items", "changes": {"key": ["id"]}}
                      for name in ("inbox", "other")]  # fmt: skip
        mission = {"catalog": [{"name": "mail", "url": "https://mail.test"}],
                   "collectors": collectors}  # fmt: skip
        for name in ("m", "n"):
            (tmp_path / ".recon" / name).mkdir(parents=True, exist_ok=True)
            (tmp_path / ".recon" / name / "config.yaml").write_text(yaml.safe_dump(mission))
        surveys = Surveys(tmp_path)
        surveys.survey(A)
        return surveys

    def test_a_cursor_of_another_output_or_mission_is_refused(self, tmp_path: Path) -> None:
        surveys = self._two(tmp_path)
        other = surveys.recon.changes_since("m", "other").cursor
        with pytest.raises(UsageError) as refused:
            surveys.recon.changes_since("m", "inbox", other)
        assert refused.value.details == {"mission": "m", "output": "other"}
        assert refused.value.fix
        theirs = surveys.recon.changes_since("n", "inbox").cursor
        with pytest.raises(UsageError):
            surveys.recon.changes_since("m", "inbox", theirs)

    @pytest.mark.parametrize(
        "cursor",
        ["", "c1zz", "c1" + b"[]".hex(), "garbage", _body(archive="../x"), _body(row=-1),
         _body(done="no"), _body(at=3)],
    )  # fmt: skip
    def test_a_cursor_recon_did_not_give_is_refused(self, tmp_path: Path, cursor: str) -> None:
        surveys = self._two(tmp_path)
        with pytest.raises(UsageError):
            surveys.recon.changes_since("m", "inbox", cursor)

    def test_a_read_of_an_archive_s_records_gives_no_cursor_a_change_read_takes(
        self, tmp_path: Path
    ) -> None:
        surveys = self._two(tmp_path)
        records = surveys.recon.read_since("m").cursor
        with pytest.raises(UsageError):
            surveys.recon.changes_since("m", "inbox", records)

    def test_a_cursor_naming_an_archive_the_mission_never_had_is_refused(
        self, tmp_path: Path
    ) -> None:
        surveys = self._two(tmp_path)
        never = _body(archive="2001-01-01-000000-000000")
        with pytest.raises(UsageError) as refused:
            surveys.recon.changes_since("m", "inbox", never)
        assert "neither has nor retired" in str(refused.value)

    def test_an_output_without_changes_is_not_found_and_names_those_with(
        self, tmp_path: Path
    ) -> None:
        surveys = self._two(tmp_path)
        with pytest.raises(NotFoundError) as missing:
            surveys.recon.changes_since("m", "nope")
        assert missing.value.details["allowed"] == ["inbox", "other"]


def test_a_bound_below_one_is_refused(tmp_path: Path) -> None:
    _mission(tmp_path, {"key": ["id"]})
    surveys = Surveys(tmp_path)
    with pytest.raises(UsageError):
        surveys.recon.changes_since("m", "inbox", max_records=0)

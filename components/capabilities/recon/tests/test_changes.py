"""Change detection through recon's surveys and streams (rulings C1-C7, and the owner's of
2026-10-24 on what was told): what a spec change does, the rebaseline, the told index as a
cache, the mission's key, the change table as a table, the plan's refusals and reports,
deletes withheld, and a stream's windows told against their chain. The comparison's own
properties are ``test_changes_property.py``'s."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from recon.adapters._out.archive_files import ArchiveFiles
from recon.adapters._out.mission_files import MissionFiles
from recon.application.changes import Comparison, Hasher, MemoryJoin, canonical
from recon.application.plan import build_plan
from recon.composition import load_plugins, root
from recon.domain.changes import ChangesSpec
from recon.domain.http import HttpResponse
from tests.recon_fakes import FixedClock, VirtualClock, plugins_with
from tests.test_paginated_survey import PagedApi

if TYPE_CHECKING:
    from collections.abc import Iterator

    from recon.domain.capture import CaptureLog
    from recon.domain.collector import Plugins
    from recon.domain.models import CollectorEntry, SourceEntry


class Inbox:
    """The Requester port answering each survey with the messages it is given."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def request(self, source: SourceEntry, method: str, url: str, **kw: Any) -> HttpResponse:
        text = json.dumps({"items": self.messages})
        return HttpResponse(status_code=200, headers={}, body=text.encode(), text=text, url=url,
                            content_type="application/json")  # fmt: skip

    def get(self, source: SourceEntry, url: str, **kw: Any) -> HttpResponse:
        return self.request(source, "GET", url)


def _mission(project: Path, changes: dict[str, Any] | None, **entry: Any) -> None:
    collector = {"name": "inbox", "type": "api", "source": "mail", "endpoint": "/m",
                 "extract": "items", **entry}  # fmt: skip
    if changes is not None:
        collector["changes"] = changes
    mission = {"catalog": [{"name": "mail", "url": "https://mail.test"}], "collectors": [collector]}
    (project / ".recon" / "m").mkdir(parents=True, exist_ok=True)
    (project / ".recon" / "m" / "config.yaml").write_text(yaml.safe_dump(mission))


class Surveys:
    """A mission's surveys, each a second after the last, of the inbox as it is then."""

    def __init__(self, project: Path, requester: Any = None) -> None:
        self.inbox = Inbox()
        self.clock = FixedClock()
        requester = requester if requester is not None else self.inbox
        self.recon = root({}, project=project, clock=self.clock,
                          requester=lambda t, d, r: requester)  # fmt: skip

    def survey(self, *messages: dict[str, Any]) -> tuple[Path, Any]:
        self.clock.advance(1)
        self.inbox.messages = list(messages)
        document = self.recon.survey("m")
        return Path(document.path), document


def _rows(archive: Path, output: str = "inbox") -> list[dict[str, Any]]:
    lines = [
        json.loads(line) for line in (archive / f"{output}.changes.jsonl").read_text().splitlines()
    ]
    return [] if lines and lines[0].get("_empty") else lines


def _ops(archive: Path) -> list[tuple[str, Any]]:
    return [(row["op"], row["key"] and row["key"].get("id")) for row in _rows(archive)]


A, B, C = ({"id": "a", "subject": "hello", "seen": 1}, {"id": "b", "subject": "hi", "seen": 1},
           {"id": "c", "subject": "yo", "seen": 1})  # fmt: skip


class TestTheChangeTable:
    def test_its_rows_and_their_order(self, tmp_path: Path) -> None:
        """C5: rows {op, key, record}; inserts and updates in N's order, then deletes in B's."""
        _mission(tmp_path, {"key": ["id"], "volatile": ["seen"], "deletes": True})
        surveys = Surveys(tmp_path)
        first, document = surveys.survey(A, B, C)
        assert _ops(first) == [("insert", "a"), ("insert", "b"), ("insert", "c")]
        assert document.tables[0].changes.previous is None

        changed = {**C, "subject": "yo!"}
        new = {"id": "d", "subject": "new"}
        second, document = surveys.survey(changed, new, {**A, "seen": 9})
        assert _ops(second) == [("update", "c"), ("insert", "d"), ("delete", "b")]
        assert _rows(second)[0] == {"op": "update", "key": {"id": "c"}, "record": changed}
        assert _rows(second)[2] == {"op": "delete", "key": {"id": "b"}, "record": None}
        summary = document.tables[0].changes
        assert summary.previous == first.name and summary.table == "inbox.changes"
        assert (summary.inserts, summary.updates, summary.deletes, summary.rows) == (1, 1, 1, 3)
        assert summary.type_url == "recon.v1.changes"

    def test_it_is_a_table_sql_knows_and_its_index_is_none(self, tmp_path: Path) -> None:
        _mission(tmp_path, {"key": ["id"]})
        surveys = Surveys(tmp_path)
        surveys.survey(A, B)
        rows = surveys.recon.query("m", "SELECT op, key.id AS id FROM inbox_changes ORDER BY id")
        assert [tuple(r.values()) for r in rows.rows] == [("insert", "a"), ("insert", "b")]
        tables = [t.output for t in ArchiveFiles(FixedClock()).tables(Path(rows.path))]
        assert tables == ["inbox", "inbox.changes"], "the key index is no table"

    def test_nothing_changed_is_an_empty_change_table_that_still_binds(
        self, tmp_path: Path
    ) -> None:
        _mission(tmp_path, {"key": ["id"]})
        surveys = Surveys(tmp_path)
        surveys.survey(A)
        archive, _ = surveys.survey(A)
        assert _rows(archive) == []
        assert surveys.recon.query("m", "SELECT count(*) AS n FROM inbox_changes").rows == [
            {"n": 0}
        ]

    def test_no_hash_leaves_the_key_index(self, tmp_path: Path) -> None:
        """C2: hashes never leave recon: not in a change row, meta.yaml, the event log or a
        warning; the key index alone keeps them."""
        _mission(tmp_path, {"key": ["id"], "deletes": True})
        surveys = Surveys(tmp_path)
        surveys.survey(A, B)
        archive, document = surveys.survey({**A, "subject": "changed"})
        index = [
            json.loads(line) for line in (archive / "inbox.keys.jsonl").read_text().splitlines()
        ]
        hashes = [line["hash"] for line in index[1:]]
        assert hashes and all(len(h) == 32 for h in hashes)
        for path in archive.iterdir():
            if path.name != "inbox.keys.jsonl" and path.is_file():
                text = path.read_text()
                assert not any(h in text for h in hashes), path.name
        assert not any(h in json.dumps(document.document()) for h in hashes)


class TestTheBaseline:
    def test_a_failed_table_is_compared_with_nothing_and_tells_nothing(
        self, tmp_path: Path
    ) -> None:
        class Flaky(Inbox):
            fail = False

            def request(self, source: SourceEntry, method: str, url: str, **kw: Any) -> Any:
                if self.fail:
                    from recon.domain.errors import CollectionError

                    raise CollectionError("down", kind="transient")
                return super().request(source, method, url, **kw)

        _mission(tmp_path, {"key": ["id"], "deletes": True})
        flaky = Flaky()
        surveys = Surveys(tmp_path, flaky)
        surveys.inbox = flaky
        first, _ = surveys.survey(A, B)
        flaky.fail = True
        failed, document = surveys.survey()
        assert document.tables[0].changes is None and not (failed / "inbox.changes.jsonl").exists()
        flaky.fail = False
        third, document = surveys.survey(A, B)
        assert _rows(third) == [] and document.tables[0].changes.previous == first.name

    def test_a_table_surveyed_before_changes_was_set_rebaselines(self, tmp_path: Path) -> None:
        """C4: an earlier table that does not say what normalize: its records were made with
        gives one rebaselined row and no op, never every record as an insert."""
        _mission(tmp_path, None)
        surveys = Surveys(tmp_path)
        before, _ = surveys.survey(A, B)
        _mission(tmp_path, {"key": ["id"]})
        archive, document = surveys.survey(A, B, C)
        assert _rows(archive) == [{"op": "rebaselined", "key": None, "record": None}]
        assert document.tables[0].changes.rebaselined is True
        assert document.tables[0].changes.previous is None and before.name

    def test_a_new_normalize_rebaselines_once(self, tmp_path: Path) -> None:
        _mission(tmp_path, {"key": ["id"]}, normalize={"id": "id", "subject": "subject"})
        surveys = Surveys(tmp_path)
        surveys.survey(A, B)
        _mission(tmp_path, {"key": ["id"]}, normalize={"id": "id", "title": "subject"})
        reshaped, _ = surveys.survey(A, B)
        assert _ops(reshaped) == [("rebaselined", None)]
        again, _ = surveys.survey(A, B, C)
        assert _ops(again) == [("insert", "c")], "the told index starts again from it"

    def test_a_new_volatile_field_computes_the_told_index_again_and_floods_nothing(
        self, tmp_path: Path
    ) -> None:
        """C4: key or volatile changed, what was told is computed again under the new spec:
        the kept index (made under the old) is stale."""
        _mission(tmp_path, {"key": ["id"]})
        surveys = Surveys(tmp_path)
        first, _ = surveys.survey(A, B)
        kept = (first / "inbox.keys.jsonl").read_bytes()
        _mission(tmp_path, {"key": ["id"], "volatile": ["seen"]})
        archive, _ = surveys.survey({**A, "seen": 5}, B)
        assert _rows(archive) == []
        assert (first / "inbox.keys.jsonl").read_bytes() == kept, "a sealed archive is unchanged"

    def test_a_missing_or_stale_index_is_computed_again(self, tmp_path: Path) -> None:
        """C6: the told index is a cache of a pure fold: one gone, or one whose record count is
        not its table's, is computed again from the chain (the earlier archive only read)."""
        _mission(tmp_path, {"key": ["id"], "deletes": True})
        surveys = Surveys(tmp_path)
        first, _ = surveys.survey(A, B)
        (first / "inbox.keys.jsonl").unlink()
        archive, _ = surveys.survey(A)
        assert _ops(archive) == [("delete", "b")]
        assert not (first / "inbox.keys.jsonl").exists()

        # An index whose count is not its table's, which, trusted, would tell zzz deleted.
        header = json.loads((archive / "inbox.keys.jsonl").read_text().splitlines()[0])
        lying = [{**header, "records": 99, "keys": 2}, {"key": canonical(["a"]), "hash": "0"},
                 {"key": canonical(["zzz"]), "hash": "0"}]  # fmt: skip
        (archive / "inbox.keys.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lying))
        third, _ = surveys.survey()
        assert _ops(third) == [("delete", "a")]

        # A kept index whose header holds is what the next comparison reads: one that says
        # zzz was told makes zzz's delete told.
        header = json.loads((third / "inbox.keys.jsonl").read_text().splitlines()[0])
        kept = [{**header, "keys": 1}, {"key": canonical(["zzz"]), "hash": "0"}]
        (third / "inbox.keys.jsonl").write_text("".join(json.dumps(x) + "\n" for x in kept))
        fourth, _ = surveys.survey()
        assert _ops(fourth) == [("delete", "zzz")]

    def test_the_first_told_index_is_its_table_s_keys(self, tmp_path: Path) -> None:
        """C9, as the survey keeps it: nothing told before, the told index is every unambiguous
        key of the table, sorted, with its content."""
        _mission(tmp_path, {"key": ["id"], "volatile": ["seen"]})
        surveys = Surveys(tmp_path)
        archive, _ = surveys.survey(A, B, {**A}, {"subject": "keyless"})
        secret = MissionFiles(tmp_path, FixedClock()).change_key("m")
        hasher = Hasher(ChangesSpec(key=["id"], volatile=["seen"]), secret)
        records = [json.loads(line) for line in (archive / "inbox.jsonl").read_text().splitlines()]
        comparison = Comparison(lambda: records, None, hasher, MemoryJoin(), whole=True)
        rebuilt = [json.dumps(line) for line in comparison.lines(len(records))]
        assert (archive / "inbox.keys.jsonl").read_text().splitlines() == rebuilt
        assert json.loads(rebuilt[0])["keys"] == 2 and json.loads(rebuilt[0])["keyless"] == 1


class _Capped(Inbox):
    """The inbox read in one page, which names a next one when ``more`` is set, so a crawl
    capped at one page is truncated."""

    more = False

    def request(self, source: SourceEntry, method: str, url: str, **kw: Any) -> HttpResponse:
        text = json.dumps({"items": self.messages, "next": "p2" if self.more else None})
        return HttpResponse(status_code=200, headers={}, body=text.encode(), text=text, url=url,
                            content_type="application/json")  # fmt: skip


class TestWhatWasTold:
    """The owner's ruling of 2026-10-24: a truncated table is never a baseline; each table is
    compared with what every table before it told."""

    def _surveys(self, tmp_path: Path) -> tuple[Surveys, _Capped]:
        capped = _Capped()
        paginate = {"type": "cursor", "next": "next", "param": "cursor", "pages": 1}
        _mission(tmp_path, {"key": ["id"], "deletes": True}, paginate=paginate)
        surveys = Surveys(tmp_path, capped)
        surveys.inbox = capped
        return surveys, capped

    def test_a_key_gone_while_a_truncated_table_intervened_is_deleted(self, tmp_path: Path) -> None:
        surveys, capped = self._surveys(tmp_path)
        surveys.survey(A, B, C)
        capped.more = True
        truncated, document = surveys.survey(A)
        assert document.tables[0].truncated and _ops(truncated) == []
        capped.more = False
        whole, document = surveys.survey(A, B)
        assert _ops(whole) == [("delete", "c")]
        assert document.tables[0].changes.previous == truncated.name

    def test_an_update_a_truncated_table_told_is_not_told_again(self, tmp_path: Path) -> None:
        surveys, capped = self._surveys(tmp_path)
        surveys.survey(A, B)
        capped.more = True
        changed = {**A, "subject": "changed"}
        truncated, _ = surveys.survey(changed)
        assert _ops(truncated) == [("update", "a")]
        capped.more = False
        whole, _ = surveys.survey(changed, B)
        assert _ops(whole) == []

    def test_a_survey_stopped_told_nothing(self, tmp_path: Path) -> None:
        """An archive whose survey did not run to its end is skipped: what it wrote was never
        to be read, so the next survey tells it again."""
        _mission(tmp_path, {"key": ["id"]})
        surveys = Surveys(tmp_path)
        first, _ = surveys.survey(A)
        stopped, _ = surveys.survey(A, B)
        meta = yaml.safe_load((stopped / "meta.yaml").read_text())
        (stopped / "meta.yaml").write_text(yaml.safe_dump({**meta, "ended": "stopped"}))
        archive, document = surveys.survey(A, B)
        assert _ops(archive) == [("insert", "b")]
        assert document.tables[0].changes.previous == first.name


class TestTheMissionKey:
    def test_it_is_made_on_first_use_for_its_owner_alone_and_kept(self, tmp_path: Path) -> None:
        _mission(tmp_path, {"key": ["id"]})
        surveys = Surveys(tmp_path)
        key = tmp_path / ".recon" / "m" / "changes.key"
        assert not key.exists()
        first, _ = surveys.survey(A)
        assert stat.S_IMODE(key.stat().st_mode) == 0o600 and len(key.read_bytes()) == 32
        made = key.read_bytes()
        surveys.survey(A)
        assert key.read_bytes() == made

    def test_a_mission_without_changes_has_none(self, tmp_path: Path) -> None:
        _mission(tmp_path, None)
        Surveys(tmp_path).survey(A)
        assert not (tmp_path / ".recon" / "m" / "changes.key").exists()

    def test_another_key_reads_every_kept_index_as_stale(self, tmp_path: Path) -> None:
        _mission(tmp_path, {"key": ["id"]})
        surveys = Surveys(tmp_path)
        surveys.survey(A, B)
        (tmp_path / ".recon" / "m" / "changes.key").unlink()
        archive, _ = surveys.survey(A, B)
        assert _rows(archive) == [], "what was told is computed again under the new key"


class TestDeletesWithheld:
    def test_a_page_cap_gives_zero_deletes(self, tmp_path: Path) -> None:
        """Erlang's: a mailbox read to its page cap is a truncated table, which tells no
        delete, and says why."""
        api = PagedApi("cursor", script={None: (2, "p1"), "p1": (2, "p2"), "p2": (2, None)})
        surveys = Surveys(tmp_path, api)
        whole_paginate = {"type": "cursor", "next": "meta.next", "param": "cursor"}
        _mission(tmp_path, {"key": ["id"], "deletes": True}, paginate=whole_paginate)
        whole, _ = surveys.survey()
        assert len(_rows(whole)) == 6
        _mission(tmp_path, {"key": ["id"], "deletes": True},
                 paginate={**whole_paginate, "pages": 1})  # fmt: skip
        capped, document = surveys.survey()
        [table] = document.tables
        assert table.truncated and table.changes.deletes == 0
        assert [r["op"] for r in _rows(capped)] == []
        assert table.changes.deletes_withheld == "truncated"
        [warning] = [w for w in document.warnings if w["kind"] == "deletes_withheld"]
        assert warning["path"] == "collectors.0.changes.deletes" and warning["got"] == "truncated"

    @pytest.mark.parametrize(
        ("messages", "why"),
        [((A, {"subject": "no id"}), "keyless"), ((A, {**A, "subject": "other"}), "ambiguous")],
    )
    def test_keyless_or_ambiguous_records_hold_deletes_back(
        self, tmp_path: Path, messages: tuple[dict[str, Any], ...], why: str
    ) -> None:
        _mission(tmp_path, {"key": ["id"], "deletes": True})
        surveys = Surveys(tmp_path)
        surveys.survey(A, B)
        archive, document = surveys.survey(*messages)
        assert not any(op == "delete" for op, _ in _ops(archive))
        assert document.tables[0].changes.deletes_withheld == why
        assert [w["got"] for w in document.warnings if w["kind"] == "deletes_withheld"] == [why]


class TestOffsetCrawls:
    """Ixian P8: records shifting under an offset crawl."""

    def test_a_record_added_at_the_front_mid_crawl_reads_twice_and_tells_nothing_false(
        self, tmp_path: Path
    ) -> None:
        """One record shifts onto the next page, so it is read twice: the same content twice
        is one record, so no false insert and no false delete follows."""
        data = [{"id": f"m{n}"} for n in range(4)]

        class Shifting(PagedApi):
            shift = False

            def _page(self, token: Any) -> tuple[list[dict[str, Any]], Any]:
                start = int(token or 0)
                if self.shift and start:  # a message arrives at the front after page one
                    return [{"id": "new"}, *data][start : start + 2], None
                return data[start : start + 2], None

        api = Shifting("offset")
        paginate = {"type": "offset", "param": "offset"}
        _mission(tmp_path, {"key": ["id"]}, paginate=paginate)
        surveys = Surveys(tmp_path, api)
        surveys.survey()
        api.shift = True
        shifted, document = surveys.survey()
        held = [
            json.loads(line)["id"] for line in (shifted / "inbox.jsonl").read_text().splitlines()
        ]
        assert held.count("m1") == 2, "m1 shifted onto the second page and was read twice"
        assert _ops(shifted) == [], "nothing that did not happen is told"

    @pytest.mark.parametrize("kind", ["offset", "page"])
    def test_the_plan_refuses_deletes_of_a_crawl_whose_records_can_shift(self, kind: str) -> None:
        """A source that loses a record while it is read shifts the next onto a page already
        read: skipped, it would read as deleted. The owner's ruling: refused, with a fix."""
        mission = {
            "catalog": [{"name": "mail", "url": "https://mail.test"}],
            "collectors": [{"name": "inbox", "type": "api", "source": "mail", "endpoint": "/m",
                            "paginate": {"type": kind, "param": kind},
                            "changes": {"key": ["id"], "deletes": True}}],
        }  # fmt: skip
        plan = build_plan(mission, load_plugins(), {})
        [problem] = plan.problems
        assert (problem.path, problem.got) == ("collectors.0.changes.deletes", True)
        assert "link" in problem.fix and "deletes: false" in problem.fix
        assert not [w for w in plan.warnings if "deletes" in w.kind]

    @pytest.mark.parametrize("kind", ["link", "cursor"])
    def test_deletes_of_a_crawl_that_names_its_next_page_are_planned(self, kind: str) -> None:
        paginate = {"type": kind, "next": "next"} | ({"param": "c"} if kind == "cursor" else {})
        mission = {
            "catalog": [{"name": "mail", "url": "https://mail.test"}],
            "collectors": [{"name": "inbox", "type": "api", "source": "mail", "endpoint": "/m",
                            "paginate": paginate, "changes": {"key": ["id"], "deletes": True}}],
        }  # fmt: skip
        assert build_plan(mission, load_plugins(), {}).valid


@pytest.fixture(scope="module")
def plugins() -> Plugins:
    return load_plugins()


def _plan_mission(changes: dict[str, Any], **entry: Any) -> dict[str, Any]:
    return {
        "catalog": [{"name": "mail", "url": "https://mail.test"}],
        "collectors": [{"name": "inbox", "type": "api", "source": "mail", "endpoint": "/m",
                        "changes": changes, **entry}],
    }  # fmt: skip


class TestThePlan:
    def test_it_reports_every_default_it_fills_in(self, plugins: Plugins) -> None:
        plan = build_plan(_plan_mission({"key": ["id"]}), plugins, {})
        values = {v.path: (v.value, v.origin) for v in plan.provenance}
        assert values["collectors.0.changes.key"] == (["id"], "mission")
        assert values["collectors.0.changes.volatile"] == ([], "recon")
        assert values["collectors.0.changes.deletes"] == (False, "recon")

    @pytest.mark.parametrize(
        ("changes", "path"),
        [
            ({"key": []}, "collectors.0.changes.key"),
            ({"volatile": ["seen"]}, "collectors.0.changes.key"),
            ({"key": ["_position"]}, "collectors.0.changes"),
            ({"key": ["id"], "volatile": ["id"]}, "collectors.0.changes"),
            ({"key": ["id"], "keys": ["x"]}, "collectors.0.changes.keys"),
        ],
    )
    def test_a_spec_it_cannot_use_is_refused_with_its_path(
        self, plugins: Plugins, changes: dict[str, Any], path: str
    ) -> None:
        problems = {p.path for p in build_plan(_plan_mission(changes), plugins, {}).problems}
        assert path in problems, problems

    def test_a_field_normalize_does_not_make_is_refused(self, plugins: Plugins) -> None:
        mission = _plan_mission({"key": ["id"], "volatile": ["seen"]},
                                normalize={"id": "id", "title": "subject"})  # fmt: skip
        [problem] = build_plan(mission, plugins, {}).problems
        assert problem.path == "collectors.0.changes.volatile.0"
        assert problem.kind == "name_unknown" and problem.allowed == ("id", "title")

    @pytest.mark.parametrize("order", ["before", "after"])
    def test_a_change_table_sql_would_confuse_with_an_output_is_refused(
        self, plugins: Plugins, order: str
    ) -> None:
        mission = _plan_mission({"key": ["id"]})
        other = {"name": "inbox_changes", "type": "api", "source": "mail", "endpoint": "/x"}
        mission["collectors"].insert(0 if order == "before" else 1, other)
        problems = build_plan(mission, plugins, {}).problems
        assert [p.kind for p in problems] == ["name_taken"], problems

    def test_an_output_named_as_a_change_table_is_refused(self, plugins: Plugins) -> None:
        mission = {"collectors": [{"name": "x.changes", "type": "cli", "run": "echo"}]}
        [problem] = build_plan(mission, plugins, {}).problems
        assert problem.kind == "name_taken" and "change table" in problem.message

    def test_a_stream_tells_no_deletes(self, plugins: Plugins) -> None:
        mission = {"collectors": [{"name": "s", "type": "feed", "window": {"records": 2},
                                   "retain": "all",
                                   "changes": {"key": ["id"], "deletes": True}}]}  # fmt: skip
        problems = build_plan(mission, plugins_with(feed=_Feed([])), {}).problems
        assert [p.path for p in problems] == ["collectors.0.changes.deletes"]


# --- streams (C7) ---------------------------------------------------------------------------


class _Feed:
    """A source of the records it is given, each positioned by its index, that then ends."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
        after: Any = None,
    ) -> Iterator[dict[str, Any]]:
        start = 0 if after is None else int(after) + 1
        for n in range(start, len(self.records)):
            yield {**self.records[n], "_position": n}


def _stream_mission(project: Path, retain: Any = "all") -> None:
    mission = {"collectors": [{"name": "s", "type": "feed", "window": {"records": 2},
                               "retain": retain, "changes": {"key": ["id"]}}]}  # fmt: skip
    (project / ".recon" / "m").mkdir(parents=True, exist_ok=True)
    (project / ".recon" / "m" / "config.yaml").write_text(yaml.safe_dump(mission))


def _windows(project: Path) -> list[Path]:
    return ArchiveFiles(FixedClock()).archives(project / ".recon" / "m", stream="s")


def _window_ops(window: Path) -> list[tuple[str, Any]]:
    return [(row["op"], row["key"]["id"]) for row in _rows(window, "s")]


class TestStreams:
    RECORDS = [{"id": "a", "v": 1}, {"id": "b", "v": 1}, {"id": "a", "v": 2}, {"id": "c", "v": 1},
               {"id": "b", "v": 1}, {"id": "d", "v": 1}]  # fmt: skip

    def test_each_window_is_told_against_its_chain_and_never_deletes(self, tmp_path: Path) -> None:
        _stream_mission(tmp_path)
        recon = root({}, project=tmp_path, clock=VirtualClock(),
                     plugins=plugins_with(feed=_Feed(self.RECORDS)))  # fmt: skip
        recon.stream("m", "s", windows=3)
        first, second, third = _windows(tmp_path)
        assert _window_ops(first) == [("insert", "a"), ("insert", "b")]
        assert _window_ops(second) == [("update", "a"), ("insert", "c")]
        assert _window_ops(third) == [("insert", "d")], "b is as the chain last told it"
        meta = ArchiveFiles(FixedClock()).read_meta(third)
        assert meta["tables"][0]["changes"]["previous"] == second.name

    def test_a_window_s_index_is_its_chain_s_fold_and_is_rebuilt_when_gone(
        self, tmp_path: Path
    ) -> None:
        _stream_mission(tmp_path)
        feed = _Feed(self.RECORDS[:4])
        recon = root({}, project=tmp_path, clock=VirtualClock(), plugins=plugins_with(feed=feed))
        recon.stream("m", "s", windows=2)
        first, second = _windows(tmp_path)
        told = [json.loads(json.loads(line)["key"])[0]
                for line in (second / "s.keys.jsonl").read_text().splitlines()[1:]]  # fmt: skip
        assert told == ["a", "b", "c"], "every key the chain told, sorted"

        (second / "s.keys.jsonl").unlink()
        (first / "s.keys.jsonl").unlink()
        feed.records = self.RECORDS
        recon.stream("m", "s", windows=1)
        third = _windows(tmp_path)[-1]
        assert _window_ops(third) == [("insert", "d")], "folded again from the first window"

    def test_a_key_a_window_holds_ambiguous_keeps_what_the_chain_told(self, tmp_path: Path) -> None:
        """A window holding a key with two contents tells nothing of it, and the chain still
        knows what it told last: the same content again is no change."""
        _stream_mission(tmp_path)
        records = [{"id": "a", "v": 1}, {"id": "z", "v": 1},
                   {"id": "a", "v": 2}, {"id": "a", "v": 3},
                   {"id": "a", "v": 1}, {"id": "z", "v": 1}]  # fmt: skip
        recon = root({}, project=tmp_path, clock=VirtualClock(),
                     plugins=plugins_with(feed=_Feed(records)))  # fmt: skip
        recon.stream("m", "s", windows=3)
        _, ambiguous, third = _windows(tmp_path)
        assert _window_ops(ambiguous) == []
        meta = ArchiveFiles(FixedClock()).read_meta(ambiguous)
        assert meta["tables"][0]["changes"]["ambiguous"] == 1
        assert _window_ops(third) == []

    def test_retain_takes_a_window_s_key_index_with_it(self, tmp_path: Path) -> None:
        """Vector: a retired window's keys and hashes go with it."""
        _stream_mission(tmp_path, retain={"windows": 1})
        recon = root({}, project=tmp_path, clock=VirtualClock(),
                     plugins=plugins_with(feed=_Feed(self.RECORDS)))  # fmt: skip
        recon.stream("m", "s", windows=3)
        stream = tmp_path / ".recon" / "m" / "streams" / "s"
        assert [w.name for w in _windows(tmp_path)] == [_windows(tmp_path)[-1].name]
        assert len(list(stream.rglob("s.keys.jsonl"))) == 1
        assert len(list(stream.rglob("s.changes.jsonl"))) == 1

"""A paginated ``api`` table end to end, through recon's root with a paged fake behind the
Requester port (rulings P1-P3; ixian's round-4 P1-P4): every page read once and in order, each
record's ``_position``, the page log beside the table, and every stop recorded in
``meta.yaml`` and the survey's warnings. The plan's refusals and what it reports are here too.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlsplit

import pytest
import yaml

from recon.application.plan import build_plan
from recon.composition import load_plugins, root
from recon.domain.http import HttpResponse
from tests.recon_fakes import run_survey

if TYPE_CHECKING:
    from collections.abc import Callable

    from recon.domain.collector import Plugins
    from recon.domain.models import SourceEntry

BASE = "https://api.test"


class PagedApi:
    """The Requester port answering a paged list of ``records``, ``size`` to a page, as a
    ``kind`` API pages it; ``script`` replaces the pages with token -> (records, next token)
    instead. ``asked`` is each request's token (None for the first page), in order."""

    def __init__(
        self,
        kind: str,
        records: int = 0,
        size: int = 1,
        *,
        script: dict[Any, tuple[int, Any]] | None = None,
        override: Callable[[int, str], dict[str, Any] | None] | None = None,
    ) -> None:
        self.kind = kind
        self.data = [{"id": n, "title": f"t{n}"} for n in range(records)]
        self.size = size
        self.script = script
        self.override = override
        self.asked: list[Any] = []
        self.sent: list[dict[str, Any]] = []

    def request(
        self,
        source: SourceEntry,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> HttpResponse:
        params = dict(params or {})
        self.sent.append({"url": url, "params": params, "headers": dict(headers or {})})
        query = dict(parse_qsl(urlsplit(url).query))
        if self.kind in ("link", "header"):
            token: Any = query.get("page")
        elif self.kind == "cursor":
            token = params.get("cursor")
        elif self.kind == "page":
            token = params.get("page")
        else:
            token = params.get("offset")
        self.asked.append(token)
        rows, following = self._page(token)
        body: dict[str, Any] = {"items": rows}
        response_headers = {"content-type": "application/json"}
        link = None if following is None else f"{BASE}/items?page={following}"
        if self.kind == "link":
            body["links"] = {"next": link}
        elif self.kind == "header" and link is not None:
            response_headers["link"] = f'<{link}>; rel="next"'
        elif self.kind == "cursor":
            body["meta"] = {"next": following}
        if self.override is not None:
            body = self.override(len(self.sent), url) or body
        text = json.dumps(body)
        sent = "&".join(f"{key}={value}" for key, value in params.items())
        return HttpResponse(status_code=200, headers=response_headers, body=text.encode(),
                            text=text, url=f"{url}?{sent}" if sent else url,
                            content_type="application/json")  # fmt: skip

    def _page(self, token: Any) -> tuple[list[dict[str, Any]], Any]:
        if self.script is not None:
            count, following = self.script[token]
            return [{"id": f"{token}-{n}"} for n in range(count)], following
        if self.kind == "offset":
            start = int(token or 0)
            return self.data[start : start + self.size], None
        if self.kind == "page":  # numbered from 1
            start = (int(token) - 1) * self.size
            return self.data[start : start + self.size], None
        page = int(token or 0)
        rows = self.data[page * self.size : (page + 1) * self.size]
        more = (page + 1) * self.size < len(self.data)
        return rows, (page + 1 if more else None)

    def get(
        self, source: SourceEntry, url: str, *, headers: dict[str, str] | None = None
    ) -> HttpResponse:
        return self.request(source, "GET", url, headers=headers)


PAGINATE = {
    "link": {"type": "link", "next": "links.next"},
    "header": {"type": "header"},
    "cursor": {"type": "cursor", "next": "meta.next", "param": "cursor"},
    "offset": {"type": "offset", "param": "offset"},
    "page": {"type": "page", "param": "page"},
}


def _mission(paginate: dict[str, Any], **entry: Any) -> dict[str, Any]:
    return {
        "catalog": [{"name": "api", "url": BASE}],
        "collectors": [
            {"name": "items", "type": "api", "source": "api", "endpoint": "/items",
             "extract": "items", "paginate": paginate, **entry}
        ],
    }  # fmt: skip


def _survey(tmp_path: Path, api: PagedApi, mission: dict[str, Any]) -> tuple[Path, Any]:
    recon = _root(tmp_path, api)
    (tmp_path / ".recon" / "m").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".recon" / "m" / "config.yaml").write_text(yaml.safe_dump(mission))
    document = recon.survey("m")
    return Path(document.path), document


def _root(tmp_path: Path, api: PagedApi) -> Any:
    return root({}, project=tmp_path, requester=lambda telemetry, deadline, limiter: api)


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _meta_table(archive: Path) -> dict[str, Any]:
    [table] = yaml.safe_load((archive / "meta.yaml").read_text())["tables"]
    return dict(table)


class TestEveryPageOnceInOrder:
    """Ixian P1: the table equals the dataset, in order, with no duplicate, at a page of one
    record and with a last page that is not full."""

    @pytest.mark.parametrize("kind", ["link", "header", "cursor", "offset", "page"])
    @pytest.mark.parametrize(("records", "size"), [(7, 1), (7, 3), (6, 3)])
    def test_the_table_is_the_dataset(
        self, tmp_path: Path, kind: str, records: int, size: int
    ) -> None:
        api = PagedApi(kind, records, size)
        archive, document = _survey(tmp_path, api, _mission(PAGINATE[kind]))

        rows = _lines(archive / "items.jsonl")
        assert [row["id"] for row in rows] == list(range(records))
        pages = -(-records // size) + (1 if kind in ("offset", "page") else 0)
        assert len(api.asked) == pages, "each page once, and an offset crawl's empty last"
        assert [row["_position"] for row in rows] == [
            {"page": n // size, "i": n % size} for n in range(records)
        ]
        [table] = document.tables
        assert (table.status, table.records, table.truncated, table.truncated_by) == (
            "ok",
            records,
            False,
            None,
        )
        assert document.status == "complete" and document.warnings == []

    def test_the_page_log_keeps_each_page_s_token_once(self, tmp_path: Path) -> None:
        api = PagedApi("cursor", 5, 2)
        archive, _ = _survey(tmp_path, api, _mission(PAGINATE["cursor"]))

        assert _lines(archive / "items.pages.jsonl") == [
            {"page": 0, "token": None},
            {"page": 1, "token": 1},
            {"page": 2, "token": 2},
        ]
        assert [t.output for t in _tables(tmp_path)] == ["items"], "the page log is no table"

    def test_a_link_s_token_is_its_url_with_the_key_redacted(self, tmp_path: Path) -> None:
        api = PagedApi("link", 2, 1)
        mission = _mission(PAGINATE["link"])
        mission["catalog"][0]["auth"] = {"param": "api_key", "token": "env:KEY"}
        recon = root({"KEY": "s3cret"}, project=tmp_path,
                     requester=lambda telemetry, deadline, limiter: api)  # fmt: skip
        (tmp_path / ".recon" / "m").mkdir(parents=True)
        (tmp_path / ".recon" / "m" / "config.yaml").write_text(yaml.safe_dump(mission))
        archive = Path(recon.survey("m").path)

        assert api.sent[1]["params"] == {"api_key": "s3cret"}, "the key goes with each page"
        assert api.sent[1]["url"] == f"{BASE}/items?page=1"
        logged = _lines(archive / "items.pages.jsonl")
        assert logged[1]["token"] == f"{BASE}/items?page=1"
        assert "s3cret" not in (archive / "items.pages.jsonl").read_text()

    def test_a_page_number_goes_from_1_with_the_query_the_mission_wrote(
        self, tmp_path: Path
    ) -> None:
        api = PagedApi("page", 3, 2)
        archive, _ = _survey(tmp_path, api, _mission(PAGINATE["page"], params={"per": "2"}))

        assert [s["params"] for s in api.sent] == [
            {"per": "2", "page": "1"},
            {"per": "2", "page": "2"},
            {"per": "2", "page": "3"},
        ], "the first page is asked for by its number too, and an empty one ends the crawl"
        assert [line["token"] for line in _lines(archive / "items.pages.jsonl")] == [1, 2, 3]

    def test_a_cursor_or_an_offset_goes_with_the_query_the_mission_wrote(
        self, tmp_path: Path
    ) -> None:
        api = PagedApi("offset", 4, 2)
        _survey(tmp_path, api, _mission(PAGINATE["offset"], params={"limit": "2"}))

        assert [s["params"] for s in api.sent] == [
            {"limit": "2"},
            {"limit": "2", "offset": "2"},
            {"limit": "2", "offset": "4"},
        ]

    def test_each_position_survives_normalize(self, tmp_path: Path) -> None:
        api = PagedApi("cursor", 3, 2)
        mission = _mission(PAGINATE["cursor"], normalize={"name": "title"})
        archive, _ = _survey(tmp_path, api, mission)

        assert _lines(archive / "items.jsonl") == [
            {"name": "t0", "_position": {"page": 0, "i": 0}},
            {"name": "t1", "_position": {"page": 0, "i": 1}},
            {"name": "t2", "_position": {"page": 1, "i": 0}},
        ]

    def test_an_empty_page_that_still_links_on_is_read_past(self, tmp_path: Path) -> None:
        """Ixian P4: an empty page is not the end while it names a next one."""
        api = PagedApi("cursor", script={None: (1, "b"), "b": (0, "c"), "c": (1, None)})
        archive, document = _survey(tmp_path, api, _mission(PAGINATE["cursor"]))

        assert [row["id"] for row in _lines(archive / "items.jsonl")] == ["None-0", "c-0"]
        assert document.tables[0].truncated is False


def _tables(project: Path) -> list[Any]:
    from recon.adapters._out.archive_files import ArchiveFiles

    store = ArchiveFiles(None)  # type: ignore[arg-type]
    [archive] = store.archives(project / ".recon" / "m")
    return store.tables(archive)


class TestEveryStopIsRecorded:
    def test_the_page_cap_truncates_the_table_sealed_ok_and_warns(self, tmp_path: Path) -> None:
        """Ixian P3: an endless source stops at the cap, and the table says it is truncated."""
        script = {None if n == 0 else f"c{n}": (1, f"c{n + 1}") for n in range(10)}
        api = PagedApi("cursor", script=script)
        paginate = {**PAGINATE["cursor"], "pages": 4}
        archive, document = _survey(tmp_path, api, _mission(paginate))

        assert len(api.asked) == 4
        [table] = document.tables
        assert (table.status, table.records, table.truncated, table.truncated_by) == (
            "ok",
            4,
            True,
            "pages",
        )
        assert _meta_table(archive)["truncated"] is True
        assert _meta_table(archive)["truncated_by"] == "pages"
        [warning] = document.warnings
        assert warning["kind"] == "page_cap"
        assert warning["path"] == "collectors.0.paginate.pages"
        assert "paginate.pages" in warning["fix"] and warning["got"] == 4
        assert document.status == "complete", "a truncated table is sealed ok"

    @pytest.mark.parametrize(
        "script",
        [
            {None: (1, "a"), "a": (1, "a")},
            {None: (1, "a"), "a": (1, "b"), "b": (1, "c"), "c": (1, "a")},
        ],
        ids=["repeat", "three-cycle"],
    )
    def test_a_loop_stops_the_crawl_keeping_each_page_once(
        self, tmp_path: Path, script: dict[Any, tuple[int, Any]]
    ) -> None:
        """Ixian P2: a repeated token and a 3-cycle both end the crawl with a classified
        warning; the pages before the loop are kept once; no page is asked for twice."""
        api = PagedApi("cursor", script=script)
        archive, document = _survey(tmp_path, api, _mission(PAGINATE["cursor"]))

        distinct = len(script)
        assert len(api.asked) <= distinct + 1 and len(set(map(str, api.asked))) == distinct
        rows = [row["id"] for row in _lines(archive / "items.jsonl")]
        assert len(rows) == len(set(rows)) == distinct
        [table] = document.tables
        assert (table.truncated, table.truncated_by) == (True, "loop")
        [warning] = document.warnings
        assert warning["kind"] == "paginate_loop" and warning["path"] == "collectors.0.paginate"

    def test_empty_pages_in_a_row_that_link_on_stop_it_truncated(self, tmp_path: Path) -> None:
        script = {None: (1, "a"), "a": (0, "b"), "b": (0, "c"), "c": (0, "d"), "d": (1, None)}
        api = PagedApi("cursor", script=script)
        _, document = _survey(tmp_path, api, _mission(PAGINATE["cursor"]))

        assert api.asked == [None, "a", "b", "c"]
        [table] = document.tables
        assert (table.records, table.truncated_by) == (1, "empty")
        assert [w["kind"] for w in document.warnings] == ["paginate_empty"]

    def test_a_later_page_that_holds_no_records_where_extract_says_fails_the_table(
        self, tmp_path: Path
    ) -> None:
        """A page past the first that is an error the source sent with a success status: the
        table fails, rather than reading as the end."""

        def error_second(sent: int, url: str) -> dict[str, Any] | None:
            return {"error": "rate limited"} if sent == 2 else None

        api = PagedApi("cursor", 4, 1, override=error_second)
        archive, document = _survey(tmp_path, api, _mission(PAGINATE["cursor"]))

        [table] = document.tables
        assert table.status == "error" and table.error is not None
        assert table.error.kind == "run_failed"
        assert not (archive / "items.jsonl").exists()
        assert not (archive / "items.pages.jsonl").exists(), "a failed table keeps no page log"

    @pytest.mark.parametrize(
        "elsewhere",
        ["https://evil.test/items?page=1", "http://api.test/items?page=1",
         "https://api.test:8443/items?page=1", "file:///etc/passwd"],
    )  # fmt: skip
    def test_a_next_link_off_the_source_s_origin_is_not_followed(
        self, tmp_path: Path, elsewhere: str
    ) -> None:
        """The source's credential would go with it: the table fails instead, and nothing is
        sent there."""

        def leading(sent: int, url: str) -> dict[str, Any]:
            return {"items": [{"id": 1}], "links": {"next": elsewhere}}

        api = PagedApi("link", 3, 1, override=leading)
        _, document = _survey(tmp_path, api, _mission(PAGINATE["link"]))

        assert len(api.sent) == 1
        [table] = document.tables
        assert table.status == "error" and table.error is not None
        assert "origin" in table.error.message and table.error.kind == "run_failed"

    def test_a_relative_next_link_is_the_source_s(self, tmp_path: Path) -> None:
        def relative(sent: int, url: str) -> dict[str, Any]:
            following = None if "page=1" in url else "/items?page=1"
            return {"items": [{"id": url}], "links": {"next": following}}

        api = PagedApi("link", override=relative)
        _survey(tmp_path, api, _mission(PAGINATE["link"]))

        assert [s["url"] for s in api.sent] == [f"{BASE}/items", f"{BASE}/items?page=1"]


@pytest.fixture(scope="module")
def plugins() -> Plugins:
    return load_plugins()


def _problems(mission: dict[str, Any], plugins: Plugins) -> dict[str, Any]:
    return {p.path: p for p in build_plan(mission, plugins, {}).problems}


class TestThePlan:
    def test_the_cap_and_the_floor_it_sets_are_reported(self, plugins: Plugins) -> None:
        """P1: the plan says the effective cap and where it came from, and the least time
        the crawl takes at the source's rate limit against the deadline."""
        mission = _mission(PAGINATE["cursor"], deadline=5)
        mission["catalog"][0]["rate_limit"] = {"rps": 10, "burst": 3}
        plan = build_plan(mission, plugins, {})

        values = {v.path: v for v in plan.provenance}
        pages = values["collectors.0.paginate.pages"]
        assert (pages.value, pages.origin) == (100, "recon")
        floor = values["collectors.0.paginate.floor_s"]
        assert floor.value == pytest.approx(9.7) and floor.origin == "recon"
        assert "deadline" in floor.note and "5" in floor.note
        [warning] = [w for w in plan.warnings if w.kind == "deadline_short"]
        assert warning.path == "collectors.0.deadline"

    def test_a_cap_the_mission_wrote_is_the_mission_s(self, plugins: Plugins) -> None:
        plan = build_plan(_mission({**PAGINATE["link"], "pages": 2}), plugins, {})
        values = {v.path: v for v in plan.provenance}
        assert (values["collectors.0.paginate.pages"].value,
                values["collectors.0.paginate.pages"].origin) == (2, "mission")  # fmt: skip
        assert not [w for w in plan.warnings if w.kind == "deadline_short"]

    def test_a_type_that_reads_no_pages_takes_no_paginate(self, plugins: Plugins) -> None:
        entry = {"name": "c", "type": "cli", "run": "echo", "paginate": PAGINATE["link"]}
        mission = {
            "catalog": [{"name": "here", "type": "local", "url": "."}],
            "collectors": [entry],
        }
        problem = _problems(mission, plugins)["collectors.0.paginate"]
        assert problem.kind == "unknown_key" and "paginate" in problem.fix

    def test_a_stream_takes_no_paginate_yet(self, plugins: Plugins) -> None:
        mission = _mission(PAGINATE["link"], window={"seconds": 60})
        problem = _problems(mission, plugins)["collectors.0.paginate"]
        assert "stream" in problem.message and problem.fix

    def test_a_column_named_position_is_refused(self, plugins: Plugins) -> None:
        mission = _mission(PAGINATE["link"], normalize={"_position": "id"})
        assert _problems(mission, plugins)["collectors.0.normalize._position"].kind == "name_taken"

    def test_a_query_parameter_the_crawl_sets_is_not_the_mission_s_too(
        self, plugins: Plugins
    ) -> None:
        mission = _mission(PAGINATE["offset"], params={"offset": "10"})
        problem = _problems(mission, plugins)["collectors.0.params.offset"]
        assert problem.kind == "name_taken"

    def test_a_next_path_with_two_list_maps_is_refused(self, plugins: Plugins) -> None:
        mission = _mission({"type": "link", "next": "a.*.b.*.c"})
        assert "collectors.0.paginate.next" in _problems(mission, plugins)

    def test_each_type_s_missing_key_is_a_problem_with_its_path(self, plugins: Plugins) -> None:
        problems = _problems(_mission({"type": "cursor", "next": "meta.next"}), plugins)
        assert "collectors.0.paginate" in problems

    @pytest.mark.parametrize("output", ["items.pages", "Items.PAGES"])
    def test_an_output_named_as_a_table_s_page_log_is_refused(
        self, plugins: Plugins, output: str
    ) -> None:
        mission = {"collectors": [{"name": output, "type": "cli", "run": "echo"}]}
        problem = _problems(mission, plugins)["collectors.0.name"]
        assert problem.kind == "name_taken"

    def test_the_run_describes_its_crawl(self, plugins: Plugins) -> None:
        [run] = build_plan(_mission(PAGINATE["cursor"]), plugins, {}).runs
        assert run.document()["paginate"] == {
            "type": "cursor", "pages": 100, "next": "meta.next", "param": "cursor"
        }  # fmt: skip


def test_a_survey_without_paginate_asks_once_and_positions_nothing(tmp_path: Path) -> None:
    """The default is no pagination: one request, records as they were."""
    api = PagedApi("cursor", 5, 2)
    mission = _mission(PAGINATE["cursor"])
    del mission["collectors"][0]["paginate"]
    archive, _ = run_survey(mission, tmp_path, requester=lambda t, d, r: api)

    assert len(api.asked) == 1
    rows = _lines(archive / "items.jsonl")
    assert len(rows) == 2 and all("_position" not in row for row in rows)
    assert not (archive / "items.pages.jsonl").exists()

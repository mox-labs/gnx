"""A crawl long enough to need resume, as a stream of windows bounded by pages (ruling P4;
ixian's round-4 P7 and P8), against a loopback server through recon's own requester.

A paginated ``api`` entry with ``window:`` is a stream whose source ends: it crawls page by
page across its windows, ``window: {pages: N}`` sealing each at N pages, until a page names no
next one, which ends the stream (``eof``); a run after that crawls again from the first page.
A run that goes on from an earlier one asks for the last page its page log names, by its
token, and skips the records kept from it, the last of them found by its content, so a page that
moved under the crawl is caught. A token the source no longer takes begins the crawl again,
recorded as a gap. Change detection over such a stream tells no delete, and nothing twice."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any

import yaml

from recon.adapters._out.archive_files import ArchiveFiles
from recon.composition import root
from tests.paged_server import PagedServer
from tests.recon_fakes import FixedClock

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from recon.domain.documents import StreamDocument

CLI = "import sys; from recon.adapters._in.cli import main; sys.exit(main())"
PAGINATE = {"type": "cursor", "next": "meta.next", "param": "cursor"}


def _project(project: Path, url: str, window: dict[str, Any], **entry: Any) -> None:
    (project / ".git").mkdir(parents=True, exist_ok=True)
    mission = {
        "catalog": [{"name": "api", "url": url}],
        "collectors": [
            {"name": "items", "type": "api", "source": "api", "endpoint": "/items",
             "extract": "items", "paginate": PAGINATE, "window": window, "retain": "all",
             **entry}
        ],
    }  # fmt: skip
    (project / ".recon" / "m").mkdir(parents=True, exist_ok=True)
    (project / ".recon" / "m" / "config.yaml").write_text(yaml.safe_dump(mission))


def _stream(project: Path, **bound: Any) -> StreamDocument:
    return root({}, project=project, clock=FixedClock()).stream("m", "items", **bound)


def _windows(project: Path) -> list[Path]:
    return ArchiveFiles(FixedClock()).archives(project / ".recon" / "m", stream="items")


def _lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_bytes().split(b"\n")[:-1]]
    return [row for row in rows if row.get("_empty") is not True]


def _rows(window: Path) -> list[dict[str, Any]]:
    """A window's whole records: its sealed table, or what its dead writer left."""
    sealed = window / "items.jsonl"
    return _lines(sealed if sealed.exists() else window / "items.jsonl.tmp")


def _ids(project: Path) -> list[int]:
    return [row["id"] for window in _windows(project) for row in _rows(window)]


def _pages(window: Path) -> list[int]:
    """The pages a window's page log names, in order."""
    return [line["page"] for line in _lines(window / "items.pages.jsonl")]


def _meta(window: Path) -> dict[str, Any]:
    meta = yaml.safe_load((window / "meta.yaml").read_text())
    assert isinstance(meta, dict)
    return meta


def _gaps(window: Path) -> list[dict[str, Any]]:
    gaps = _meta(window)["stream"]["sources"][0]["gaps"]
    assert isinstance(gaps, list)
    return gaps


class TestWindowsOfPages:
    def test_windows_bound_by_pages_seal_at_exactly_n_pages(self, tmp_path: Path) -> None:
        """``window: {pages: 2}`` over five pages: windows of two pages, two pages, and the
        last page, which names no next one and ends the stream (``eof``). Each window's page
        log names exactly its pages, each page is asked for once, and none past the end."""
        with PagedServer(10, 2) as server:
            _project(tmp_path, server.url, {"pages": 2})
            document = _stream(tmp_path, windows=10)

        windows = _windows(tmp_path)
        assert document.windows == 3, "the crawl's end ended the stream, not --windows"
        assert [_meta(w)["stream"]["closed_by"] for w in windows] == ["pages", "pages", "eof"]
        assert [_pages(w) for w in windows] == [[0, 1], [2, 3], [4]]
        tokens = [[line["token"] for line in _lines(w / "items.pages.jsonl")] for w in windows]
        assert tokens == [[None, 1], [2, 3], [4]], "each page under the token that asked for it"
        assert [len(_rows(w)) for w in windows] == [4, 4, 2]
        assert _ids(tmp_path) == list(range(10))
        assert server.asked == [None, 1, 2, 3, 4]
        assert [row["_position"] for row in _rows(windows[1])] == [
            {"page": 2, "i": 0}, {"page": 2, "i": 1}, {"page": 3, "i": 0}, {"page": 3, "i": 1}
        ]  # fmt: skip

    def test_a_run_that_ends_at_a_page_boundary_asks_for_no_page_it_will_not_keep(
        self, tmp_path: Path
    ) -> None:
        """One window of two pages asks for those two and stops: the next page is not asked
        for until a run will keep it. The next run asks again for the last page kept, by its
        token from the page log (to find where it left off), then reads on: every record
        once."""
        with PagedServer(10, 2) as server:
            _project(tmp_path, server.url, {"pages": 2})
            _stream(tmp_path)  # one window, the default
            assert server.asked == [None, 1]
            _stream(tmp_path)
            assert server.asked == [None, 1, 1, 2, 3]

        first, second = _windows(tmp_path)
        assert _pages(second) == [1, 2, 3], "the page gone back to is logged again"
        assert _meta(second)["stream"]["closed_by"] == "pages", "and is not counted"
        assert _ids(tmp_path) == list(range(8))
        assert _meta(second)["stream"]["previous"] == first.name
        [gap] = _gaps(second)
        assert gap["resumed"] is True

    def test_the_crawl_s_end_ends_the_stream_and_the_next_run_crawls_again(
        self, tmp_path: Path
    ) -> None:
        """``--forever`` over a crawl that ends returns at its end (``eof``); a run after that
        is a new crawl, from the first page, its restart a gap that did not resume."""
        with PagedServer(6, 2) as server:
            _project(tmp_path, server.url, {"pages": 10})
            done = _stream(tmp_path, forever=True)
            assert done.last_window is not None and done.last_window.closed_by == "eof"
            assert (done.windows, done.records) == (1, 6)
            again = _stream(tmp_path)

        assert server.asked == [None, 1, 2, None, 1, 2]
        assert again.last_window is not None and again.last_window.table.records == 6
        [gap] = _gaps(_windows(tmp_path)[-1])
        assert gap["resumed"] is False, "a new crawl goes on from no position"


def _kill_mid_crawl(project: Path, server: PagedServer, held: int) -> None:
    """Run ``recon stream`` in a process of its own, and SIGKILL it while the server holds the
    request for page ``held``, once its window has logged that page and written every record
    before it."""
    command = [sys.executable, "-c", CLI, "stream", "m", "--stream", "items", "--windows", "10"]
    quiet = subprocess.DEVNULL
    process = subprocess.Popen(command, cwd=project, stdout=quiet, stderr=quiet)
    try:
        assert server.holding.wait(30), "the stream reached the held page"
        started = time.monotonic()
        while True:
            windows = _windows(project)
            logged = windows and held in _pages(windows[-1])
            if logged and len(_ids(project)) == held * server.size:
                break
            assert time.monotonic() - started < 30, "the window never logged the held page"
            time.sleep(0.01)
        os.kill(process.pid, signal.SIGKILL)
        process.wait(10)
    finally:
        if process.poll() is None:
            process.kill()
    store = ArchiveFiles(FixedClock())
    started = time.monotonic()
    while store.state(_windows(project)[-1]) == "writing" and time.monotonic() - started < 10:
        time.sleep(0.01)  # the lock goes with the process, as the kernel releases it


def _resumed_wrongly(asked: list[int | None], ids: list[int], *, held: int, n: int) -> list[str]:
    """What is wrong with a resume of a crawl killed while page ``held`` was asked for: its
    first request is not that page's token, or the records are not each kept once."""
    wrong = []
    if asked[:1] != [held]:
        wrong.append(f"the first request after the kill was for page {asked[:1]}, not {held}")
    if ids != list(range(n)):
        wrong.append(f"the records are not each kept once, in order: {ids}")
    return wrong


class TestKilledMidCrawl:
    def test_a_crawl_killed_at_page_k_plus_1_resumes_there_exactly_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ixian P7: windows of two pages, the server holding page 3's request, the writer
        SIGKILLed. The window it was writing reads incomplete, its page log naming page 3 and
        its records page 2's. The next run's first request is page 3's token, not the first
        page's, and every record is kept exactly once.

        Check the checker: the same crash resumed by a stream that ignores where it was (the
        page log and ``_position`` both) asks for the first page, keeps records twice, and the
        check says both."""
        held, n = 3, 12
        with PagedServer(n, 2, hold=held) as server:
            _project(tmp_path, server.url, {"pages": 2})
            _kill_mid_crawl(tmp_path, server, held)
            first, dead = _windows(tmp_path)
            assert _meta(first)["stream"]["closed_by"] == "pages"
            assert ArchiveFiles(FixedClock()).state(dead) == "incomplete"
            assert not (dead / "meta.yaml").exists(), "killed: no record of its end"
            assert _pages(dead) == [2, 3] and [r["id"] for r in _rows(dead)] == [4, 5]
            server.release.set()

            broken = tmp_path / "broken"
            shutil.copytree(tmp_path / ".recon", broken / ".recon")
            from recon.application import stream as stream_module

            with monkeypatch.context() as patched:
                patched.setattr(stream_module, "crawl_resume", lambda *args, **kwargs: None)
                since = len(server.tried)
                _stream(broken, windows=10)
                wrong = _resumed_wrongly(server.tried[since:], _ids(broken), held=held, n=n)
            assert len(wrong) == 2, wrong

            since = len(server.tried)
            document = _stream(tmp_path, windows=10)

        assert _resumed_wrongly(server.tried[since:], _ids(tmp_path), held=held, n=n) == []
        assert document.last_window is not None and document.last_window.closed_by == "eof"
        resumed = _windows(tmp_path)[2]
        assert _meta(resumed)["stream"]["previous"] == dead.name
        assert [gap["resumed"] for gap in _gaps(resumed)] == [True]


class TestTheSourceMovedUnderTheCrawl:
    def test_a_token_the_source_no_longer_takes_crawls_again_from_the_first_page(
        self, tmp_path: Path
    ) -> None:
        """Owner decision (3): a resumed page whose token the source refuses (an expired
        cursor: 400) is no failure. The crawl begins again from its first page, the window
        records the restart as a gap that did not resume, and the run warns
        ``paginate_restarted`` with a fix. The pages it reads again, up to the one it had
        reached, are none of the window's ``pages`` (Erlang: else a schedule whose tokens
        expire reads the same pages every tick), so it reads two more. The next run resumes
        as usual."""
        with PagedServer(12, 2) as server:
            _project(tmp_path, server.url, {"pages": 2})
            _stream(tmp_path)
            server.refuse = {1: [400]}
            since = len(server.tried)
            restarted = _stream(tmp_path)
            assert server.tried[since:] == [1, None, 1, 2, 3], "page 1's token refused; again"
            _stream(tmp_path)

        first, second, third = _windows(tmp_path)
        assert restarted.last_window is not None
        assert restarted.last_window.table.status == "ok"
        assert [r["id"] for r in _rows(second)] == list(range(8)), "again, then two pages on"
        assert [gap["resumed"] for gap in _gaps(second)] == [True, False]
        [warning] = [w for w in restarted.warnings if w["kind"] == "paginate_restarted"]
        assert warning["path"] == "collectors.0.paginate" and warning["fix"]
        assert [r["id"] for r in _rows(third)] == [8, 9, 10, 11], "then on from where it was"

    def test_records_inserted_ahead_of_the_boundary_are_found_by_its_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ixian P8: an offset-like crawl (the server's cursor is a page number) shifts when a
        record is inserted ahead of where it left off. The last record kept is found by its
        content on the next page, so the resumed run keeps nothing twice, and warns
        ``paginate_shifted`` with the shift. Check the checker: a resume that skips by index
        alone keeps the boundary record twice."""

        def run(project: Path) -> tuple[list[int], list[dict[str, Any]]]:
            with PagedServer(12, 3) as server:
                _project(project, server.url, {"pages": 2})
                _stream(project)  # ids 0..5
                server.data.insert(0, {"id": 100, "title": "new"})
                resumed = _stream(project)
                _stream(project, windows=10)
            return _ids(project), resumed.warnings

        ids, warnings = run(tmp_path / "found")
        assert ids == list(range(12)), "nothing twice, nothing of the crawl lost"
        [shifted] = [w for w in warnings if w["kind"] == "paginate_shifted"]
        assert shifted["got"] == 1 and shifted["fix"]

        from recon.domain import paginate

        monkeypatch.setattr(paginate, "found_at", lambda contents, mark, i: i)
        ids, _ = run(tmp_path / "by-index")
        assert ids.count(5) == 2, "by index alone, the boundary record is kept twice"

    def test_records_removed_ahead_of_the_boundary_are_found_by_its_content(
        self, tmp_path: Path
    ) -> None:
        """A record removed ahead of where the crawl left off moves its boundary back on the
        page: found there by its content, the crawl goes on after it and loses nothing."""
        with PagedServer(12, 3) as server:
            _project(tmp_path, server.url, {"pages": 2})
            _stream(tmp_path)  # ids 0..5
            del server.data[1]
            resumed = _stream(tmp_path, windows=10)

        assert _ids(tmp_path) == list(range(12))
        [shifted] = [w for w in resumed.warnings if w["kind"] == "paginate_shifted"]
        assert shifted["got"] == -1

    def test_a_boundary_not_found_goes_on_by_index_and_records_a_gap(self, tmp_path: Path) -> None:
        """The last record kept, changed at the source (or moved past the next page), is not
        found: the crawl goes on by index, so it ends, and records a gap that did not resume,
        warning ``paginate_shifted`` with no shift."""
        with PagedServer(12, 3) as server:
            _project(tmp_path, server.url, {"pages": 2})
            _stream(tmp_path)  # ids 0..5
            server.data[5] = {"id": 5, "title": "edited"}
            resumed = _stream(tmp_path)

        second = _windows(tmp_path)[1]
        assert [r["id"] for r in _rows(second)] == [6, 7, 8, 9, 10, 11]
        assert [gap["resumed"] for gap in _gaps(second)] == [True, False]
        [shifted] = [w for w in resumed.warnings if w["kind"] == "paginate_shifted"]
        assert shifted["got"] is None and shifted["fix"]


class TestChangesOverAPaginatedStream:
    def test_no_delete_and_nothing_told_twice_across_a_crash_and_a_second_crawl(
        self, tmp_path: Path
    ) -> None:
        """Ruling C7 over pages: a crawl killed mid-window and resumed tells each record's
        insert once, across its windows; the next crawl of the same records tells nothing; a
        record changed since is one update; a record gone is never told deleted."""
        held, n = 3, 12
        with PagedServer(n, 2, hold=held) as server:
            _project(tmp_path, server.url, {"pages": 2}, changes={"key": ["id"]})
            _kill_mid_crawl(tmp_path, server, held)
            server.release.set()
            server.hold = None
            _stream(tmp_path, windows=10)
            told = _told(tmp_path)
            assert sorted(key for op, key in told) == list(range(n))
            assert {op for op, _ in told} == {"insert"}

            server.data[4] = {"id": 4, "title": "edited"}
            del server.data[7]
            before = len(told)
            _stream(tmp_path, forever=True)  # a new crawl of the whole list

        assert _told(tmp_path)[before:] == [("update", 4)]


def _told(project: Path) -> list[tuple[str, int]]:
    """Every change row of the stream, window by window, as (op, id)."""
    return [
        (row["op"], row["key"]["id"])
        for window in _windows(project)
        for row in _lines(window / "items.changes.jsonl")
    ]


class TestTicksWhoseTokensExpire:
    """Erlang's starvation case: a schedule whose source's cursors expire between ticks. Each
    tick's crawl is refused its resumed token and begins again from the first page; a crawl
    begun again reads past the page it had reached before the window's ``pages`` count, so
    every tick reads further than the last and the crawl ends. When it cannot (a window cut
    by its records before then), two windows in a row begun again at the same page warn
    ``paginate_starved``, which the tick's log line carries."""

    def _ticks(
        self, tmp_path: Path, window: dict[str, Any], ticks: int
    ) -> tuple[list[Any], list[list[str]]]:
        from datetime import UTC, datetime, timedelta

        from recon.adapters._out.schedule_files import SCHEDULE_LOG
        from recon.application.schedules import offset_s
        from recon.domain.schedule import ScheduleHost
        from tests.test_schedule_install import FakeRunner, Host

        every = 600
        home = tmp_path / "home"
        home.mkdir()
        host = Host(home, FakeRunner())
        at = datetime(2026, 10, 24, 10, 0, tzinfo=UTC) + timedelta(seconds=offset_s("m", every))
        clock = FixedClock(at + timedelta(seconds=2))
        documents = []
        with PagedServer(12, 2, expiring=True) as server:
            _project(tmp_path, server.url, window)
            config = tmp_path / ".recon" / "m" / "config.yaml"
            mission = yaml.safe_load(config.read_text())
            config.write_text(yaml.safe_dump({**mission, "schedule": {"every": every}}))
            scheduled = ScheduleHost("/opt/recon/bin/recon", ("/usr/bin", "/bin"))
            recon = root({"PATH": "/usr/bin:/bin"}, project=tmp_path, clock=clock,
                         schedulers=host.scheduler, schedule_host=scheduled)  # fmt: skip
            digest = recon.schedule_install("m", platform="launchd").config_sha256 or ""
            for _ in range(ticks):
                documents.append(recon.schedule_tick("m", config_sha256=digest))
                server.expire()
                clock.advance(every)
        log = (tmp_path / ".recon" / "m" / SCHEDULE_LOG).read_text().splitlines()
        return documents, [json.loads(line)["warning_kinds"] for line in log]

    def test_a_crawl_begun_again_each_tick_still_reads_past_n_pages_and_ends(
        self, tmp_path: Path
    ) -> None:
        """Windows of two pages, every cursor expired by the next tick: tick 1 reads pages 0
        and 1; tick 2 is refused page 1's cursor, reads 0 and 1 again (no page of its count),
        then 2 and 3; tick 3 reads on to page 5, the last, and the crawl ends (``eof``)."""
        documents, _ = self._ticks(tmp_path, {"pages": 2}, 3)

        windows = _windows(tmp_path)
        assert [_meta(w)["stream"]["closed_by"] for w in windows] == ["pages", "pages", "eof"]
        assert set(_ids(tmp_path)) == set(range(12)), "every record read, the last page too"
        assert [r["id"] for r in _rows(windows[1])] == [0, 1, 2, 3, 4, 5, 6, 7]
        kinds = [[w["kind"] for w in d.survey.warnings] for d in documents]
        assert "paginate_restarted" in kinds[1] and "paginate_starved" not in kinds[1] + kinds[2]

    def test_two_windows_in_a_row_begun_again_at_one_page_warn_starved(
        self, tmp_path: Path
    ) -> None:
        """A window cut by its records before the crawl passes where it was: each tick begins
        again at page 1, reads four records and seals, never past page 1. The second such
        window warns ``paginate_starved`` with its fix, and the tick's log line names it."""
        documents, logged = self._ticks(tmp_path, {"records": 4}, 3)

        [starved] = [w for w in documents[2].survey.warnings if w["kind"] == "paginate_starved"]
        assert starved["got"] == 1 and "window.pages" in starved["fix"]
        assert "every" in starved["fix"]
        assert "paginate_starved" in logged[2] and "paginate_starved" not in logged[1]

"""A paginated ``api`` table through recon's own requester, against a loopback server
(ixian's round-4 P5-P7): every page draws its rate-limit token and is retried as any request
is; a crawl kept with ``preserve_raw`` replays from its captures to the same bytes; a survey
killed mid-crawl leaves an archive that reads crashed, and the next survey starts the crawl
again from its first page (rulings P4: no resume inside a snapshot table)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from recon.adapters._out.archive_files import ArchiveFiles
from recon.composition import root
from tests.paged_server import PagedServer
from tests.recon_fakes import FixedClock

CLI = "import sys; from recon.adapters._in.cli import main; sys.exit(main())"


def _mission(url: str, **entry: Any) -> dict[str, Any]:
    source, top = entry.pop("source", {}), entry.pop("top", {})
    return {
        "catalog": [{"name": "api", "url": url, **source}],
        "collectors": [
            {"name": "items", "type": "api", "source": "api", "endpoint": "/items",
             "extract": "items",
             "paginate": {"type": "cursor", "next": "meta.next", "param": "cursor"}, **entry}
        ],
        **top,
    }  # fmt: skip


def _write(project: Path, name: str, mission: dict[str, Any]) -> None:
    (project / ".recon" / name).mkdir(parents=True, exist_ok=True)
    (project / ".recon" / name / "config.yaml").write_text(yaml.safe_dump(mission))


def _ids(table: Path) -> list[Any]:
    return [json.loads(line)["id"] for line in table.read_text().splitlines()]


def test_every_page_draws_a_token_and_is_retried_as_a_request_is(tmp_path: Path) -> None:
    """P5: pagination goes through the Requester, so each page waits for the source's rate
    limit, a 429's Retry-After is waited out on the clock, and the server sees each page once
    plus the one retry."""
    clock = FixedClock()
    with PagedServer(5, 1, refuse={2: [429]}, retry_after=2) as server:
        _write(tmp_path, "m", _mission(server.url, source={"rate_limit": {"rps": 1, "burst": 1}}))
        document = root({}, project=tmp_path, clock=clock).survey("m")

    assert document.status == "complete", document.warnings
    assert _ids(Path(document.path) / "items.jsonl") == [0, 1, 2, 3, 4]
    assert server.hits == 5 + 1, "each page once, and one retry"
    assert 2.0 in clock.slept, "the 429's Retry-After"
    assert sum(1 for s in clock.slept if s == 1.0) == 4, "each page past the burst waits"


def test_a_crawl_kept_raw_replays_from_its_captures_byte_for_byte(tmp_path: Path) -> None:
    """P6: with preserve_raw, a capture collector reading the crawl's captures, the server
    gone, writes the same table, each record in its place (``_position``)."""
    with PagedServer(7, 3) as server:
        _write(tmp_path, "m", _mission(server.url, normalize={"title": "title"},
                                       top={"preserve_raw": True}))  # fmt: skip
        archive = Path(root({}, project=tmp_path).survey("m").path)
    log = archive / "captures.jsonl"
    assert len(log.read_text().splitlines()) == 3, "a capture a page"

    def replay(name: str) -> bytes:
        _write(tmp_path, name, {"collectors": [
            {"name": "items", "type": "capture", "path": str(log), "extract": "items",
             "normalize": {"title": "title"}}]})  # fmt: skip
        return (Path(root({}, project=tmp_path).survey(name).path) / "items.jsonl").read_bytes()

    original = (archive / "items.jsonl").read_bytes()
    assert replay("r1") == original

    # Check the checker: a page's capture gone, the replay differs.
    lines = log.read_text().splitlines()
    log.write_text("\n".join(lines[:1] + lines[2:]) + "\n")
    assert replay("r2") != original


def test_a_survey_killed_mid_crawl_reads_crashed_and_the_next_starts_again(
    tmp_path: Path,
) -> None:
    """P7, as ruling P4 has it: a snapshot table that paginates is all or nothing. Killed
    after k whole pages (the server holds page k+1, so the kill lands there), the archive
    reads crashed and its table is unsealed, with no page log; the next survey asks for the
    first page again and holds every record exactly once."""
    (tmp_path / ".git").mkdir()
    k = 2
    with PagedServer(6, 1, hold=k) as server:
        _write(tmp_path, "m", _mission(server.url))
        command = [sys.executable, "-c", CLI, "survey", "m"]
        quiet = subprocess.DEVNULL
        process = subprocess.Popen(command, cwd=tmp_path, stdout=quiet, stderr=quiet)
        try:
            assert server.holding.wait(30), "the survey reached the held page"
            os.kill(process.pid, signal.SIGKILL)
            process.wait(10)
        finally:
            if process.poll() is None:
                process.kill()
        store = ArchiveFiles(FixedClock())
        [crashed] = store.archives(tmp_path / ".recon" / "m")
        deadline = time.monotonic() + 10
        while store.state(crashed) == "writing" and time.monotonic() < deadline:
            time.sleep(0.01)  # the lock goes with the process, as the kernel releases it
        assert store.state(crashed) == "incomplete"
        assert not (crashed / "meta.yaml").exists(), "it left no record of its end: crashed"
        assert not (crashed / "items.jsonl").exists(), "the table is not sealed"
        assert not (crashed / "items.pages.jsonl").exists(), "nor its page log written"
        assert server.asked == [None, 1, 2]

        server.release.set()
        asked = len(server.asked)
        document = root({}, project=tmp_path).survey("m")

    assert server.asked[asked] is None, "the next survey starts at the first page"
    assert _ids(Path(document.path) / "items.jsonl") == list(range(6))
    assert document.status == "complete"

"""Determinism, the contract stated in ``recon.application.survey``.

Same mission and same inputs give the same records; the clock, the network and the shell
enter only through ports. Here each is a fake: a fixed clock, a requester answering from a
table, a shell printing fixed lines. ``test_layering`` holds the other half: nothing else in
recon reads a clock.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from recon.domain.models import RateLimitConfig, SourceEntry
from recon.domain.ratelimit import RateLimiter
from tests.recon_fakes import FixedClock, ScriptedRequester, ScriptedShell, run_survey

if TYPE_CHECKING:
    from pathlib import Path

MISSION = {
    "catalog": [
        {"name": "papers", "url": "https://api.example.test", "rate_limit": {"rps": 1}},
        # Absolute, so two projects' captures name one directory (the shell is a fake).
        {"name": "here", "type": "local", "url": "/"},
    ],
    "collectors": [
        {
            "name": "search",
            "type": "api",
            "source": "papers",
            "endpoint": "/search",
            "extract": "data",
            "normalize": {"title": "title", "authors": "authors.*.name", "first": "authors.0.name"},
        },
        # No source: it fans out over the catalog's local sources, so its table is lines-here.
        {"name": "lines", "type": "cli", "run": "list-things", "normalize": {"n": "line_number"}},
    ],
}

BODY = json.dumps(
    {"data": [{"title": "B", "authors": [{"name": "x"}, {"name": "y"}]}, {"title": "A"}]}
)


def _survey(project: Path, clock: FixedClock, **extra: object) -> Path:
    requester = ScriptedRequester({"https://api.example.test/search": BODY})
    shell = ScriptedShell({"list-things": ["one", '{"line_number": 7}', "three"]})
    archive, _ = run_survey(
        {**MISSION, **extra},
        project,
        requester=lambda telemetry, deadline, rate_limiter: requester,
        shell=shell,
        clock=clock,
    )
    return archive


def _files(archive: Path) -> dict[str, bytes]:
    return {
        p.relative_to(archive).as_posix(): p.read_bytes()
        for p in sorted(archive.rglob("*"))
        if p.is_file()
    }


def test_same_mission_same_inputs_same_clock_is_the_same_archive(tmp_path: Path) -> None:
    first = _survey(tmp_path / "one", FixedClock())
    second = _survey(tmp_path / "two", FixedClock())

    assert first.name == second.name == "2026-01-02-030405-678901"
    assert _files(first) == _files(second)
    assert set(_files(first)) == {
        "search.jsonl", "lines-here.jsonl", "meta.yaml", "telemetry.jsonl"
    }, "the event log is part of the archive, and as repeatable as the rest"  # fmt: skip
    records = [json.loads(line) for line in (first / "search.jsonl").read_text().splitlines()]
    assert records == [
        {"title": "B", "authors": ["x", "y"], "first": "x"},
        {"title": "A", "authors": None, "first": None},
    ]


def test_the_clock_reaches_the_archive_s_times_and_never_its_records(tmp_path: Path) -> None:
    later = FixedClock()
    later.advance(3600)
    first = _survey(tmp_path / "one", FixedClock())
    second = _survey(tmp_path / "two", later)

    assert first.name != second.name
    one, two = _files(first), _files(second)
    for table in ("search.jsonl", "lines-here.jsonl"):
        assert one[table] == two[table]
    differing = {
        a.split(":")[0]
        for a, b in zip(
            one["meta.yaml"].decode().splitlines(),
            two["meta.yaml"].decode().splitlines(),
            strict=True,
        )
        if a != b
    }
    assert differing == {"started_at", "timestamp"}


def test_with_preserve_raw_only_each_capture_s_id_differs(tmp_path: Path) -> None:
    first = _survey(tmp_path / "one", FixedClock(), preserve_raw=True)
    second = _survey(tmp_path / "two", FixedClock(), preserve_raw=True)

    one, two = _files(first), _files(second)
    assert {k: v for k, v in one.items() if k != "captures.jsonl"} == {
        k: v for k, v in two.items() if k != "captures.jsonl"
    }
    lines = [
        [json.loads(line) for line in files["captures.jsonl"].decode().splitlines()]
        for files in (one, two)
    ]
    assert [{**c, "id": None} for c in lines[0]] == [{**c, "id": None} for c in lines[1]]
    assert {c["captured_at"] for c in lines[0]} == {FixedClock().now().isoformat()}


def test_a_rate_limit_waits_on_the_clock_port() -> None:
    clock = FixedClock()
    limiter = RateLimiter(clock)
    source = SourceEntry(name="s", url="https://x.test", rate_limit=RateLimitConfig(rps=2, burst=1))

    limiter.acquire(source)
    limiter.acquire(source)

    assert clock.slept == [0.5]

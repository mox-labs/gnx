"""The Datadog case (ruling R1): a scheduled mission asks a metrics API's MCP tool for each
span of source time once, tick after tick, its bounds the tool's own ``from`` and ``to``
arguments as epoch seconds. A tick missed while the host slept is caught up by the next one,
step by step; nothing is asked twice and nothing is left between.

The server is the offline fixture (``tests/mcp_server.py --metrics``), started over stdio as
a survey starts any stdio server; the scheduler is the fake of the schedule tests: nothing is
installed on this machine.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from recon.composition import root
from recon.core.domain.ranges import iso
from recon.observe.application.schedules import offset_s
from recon.observe.domain.scheduling import ScheduleHost
from tests.recon_fakes import FixedClock, kept
from tests.test_schedule_install import FakeRunner, Host

if TYPE_CHECKING:
    from recon.composition import Recon
    from recon.core.domain.documents import SurveyDocument

SERVER = str(Path(__file__).parent / "mcp_server.py")
EVERY = 600
SLOT = datetime(2026, 10, 24, 10, 0, tzinfo=UTC) + timedelta(seconds=offset_s("m", EVERY))
QUERY = "avg:system.cpu.user{*}"


def _mission(project: Path) -> None:
    config = {
        "catalog": [{"name": "datadog", "type": "mcp", "command": sys.executable,
                     "args": [SERVER, "--metrics"]}],
        "schedule": {"every": EVERY},
        "collectors": [{
            "name": "cpu", "type": "mcp", "source": "datadog", "tool": "query_metrics",
            "arguments": {"query": QUERY, "from": "{range.start:epoch}",
                          "to": "{range.end:epoch}"},
            "extract": "series",
            "range": {"step": EVERY, "align": 60},
            "retain": {"archives": 10},
        }],
    }  # fmt: skip
    mission = project / ".recon" / "m"
    mission.mkdir(parents=True, exist_ok=True)
    (mission / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))


def _root(project: Path, host: Host, clock: FixedClock) -> Recon:
    tick = ScheduleHost("/opt/recon/bin/recon", ("/usr/bin", "/bin"))
    return root({"PATH": "/usr/bin:/bin"}, project=project, clock=clock,
                schedulers=host.scheduler, schedule_host=tick)  # fmt: skip


def _rows(project: Path, survey: SurveyDocument) -> list[dict[str, Any]]:
    lines = (kept(project, survey) / "cpu.jsonl").read_text().splitlines()
    return [row for row in map(json.loads, lines) if row != {"_empty": True}]


@pytest.fixture
def host(tmp_path: Path) -> Host:
    home = tmp_path / "home"
    home.mkdir()
    return Host(home, FakeRunner())


def test_each_tick_asks_the_tool_for_the_next_span_once(tmp_path: Path, host: Host) -> None:
    _mission(tmp_path)
    clock = FixedClock(SLOT + timedelta(seconds=3))
    digest = (
        _root(tmp_path, host, clock)
        .observation.schedule_install("m", platform="launchd")
        .config_sha256
    )
    assert digest is not None

    surveys = []
    for advance in (0, EVERY, EVERY, 3 * EVERY):  # the last after two ticks slept through
        clock.advance(advance)
        tick = _root(tmp_path, host, clock).observation.schedule_tick("m", config_sha256=digest)
        assert tick.survey is not None and tick.survey.status == "complete", tick
        surveys.append(tick.survey)

    ranges = [survey.tables[0].range for survey in surveys]
    assert all(r is not None for r in ranges)
    spans = [(r.start, r.end, r.from_, r.steps) for r in ranges if r is not None]
    horizon = datetime.fromtimestamp(
        (int((SLOT + timedelta(seconds=3)).timestamp()) - 60) // 60 * 60, tz=UTC
    )
    assert spans[0] == (iso(horizon - timedelta(seconds=EVERY)), iso(horizon), "default", 1)
    assert [s[2] for s in spans[1:]] == ["checkpoint"] * 3
    assert spans[3][3] == 3, "the slept-through ticks are caught up, a step each"
    for earlier, later in zip(spans, spans[1:], strict=False):
        assert earlier[1] == later[0], "each tick begins where the last ended"

    points = [row["t"] for survey in surveys for row in _rows(tmp_path, survey)]
    assert points == sorted(set(points)), "no point twice"
    assert points == list(range(points[0], points[-1] + 60, 60)), "none left between"
    assert points[0] == int(datetime.fromisoformat(spans[0][0]).timestamp())
    assert points[-1] == int(datetime.fromisoformat(spans[-1][1]).timestamp()) - 60
    row = _rows(tmp_path, surveys[-1])[0]
    assert row["metric"] == QUERY and set(row["_range"]) == {"start", "end"}


def test_the_plan_shows_the_tool_asked_with_the_first_steps_bounds(
    tmp_path: Path, host: Host
) -> None:
    _mission(tmp_path)
    clock = FixedClock(SLOT)

    plan = _root(tmp_path, host, clock).observation.plan("m")

    [run] = plan.runs
    assert run["range"]["from"] == "default" and run["range"]["steps"] == 1
    start = int(datetime.fromisoformat(run["range"]["start"]).timestamp())
    assert f'"from": {start}' in json.dumps(run), "the bound is filled as a number"

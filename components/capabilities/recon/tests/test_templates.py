"""Every bundled template's ``normalize:`` against a recorded response of its source.

A path that does not resolve gives null, never an error, so a template whose paths do not
fit what its source returns plans valid and surveys "ok" with a column null in every record:
``rss-monitor``'s ``link`` and ``author`` were, since an Atom entry's links and authors are
lists. Each collector with a ``normalize:`` spec runs here, through its collector type,
against a response recorded from its source on 2026-10-22 (``fixtures/templates/<template>/
<collector>.*``, trimmed to a few records, and in a record to the fields near the ones read,
their shape kept), and every column it names must hold a value in at least one record.

Recorded from: hnrss.org's front page and github.com/python/cpython's releases feed (Atom);
the Wikipedia search API and Wikidata's SPARQL endpoint with the template's query; PyPI
(pydantic), npm (typescript) and crates.io (serde); the GitHub REST API for python/cpython's
issues and pulls, and astral-sh/uv's releases, since cpython publishes none through it.

``mcp-captures`` reads what a Claude Code hook captured, so its fixture is a ``PostToolUse``
payload; none was recorded. Its ``tool_response`` is a list of MCP text content blocks, the
shape the template's ``text`` column is written for.

``mcp-server``'s fixture is a recording of the official filesystem server
(``@modelcontextprotocol/server-filesystem`` 2026.8.31, over stdio, on 2026-10-22): the
``directory_tree`` tool's input and output schemas and its answer for a small directory. The
collector runs against ``tests/mcp_server.py --replay``, a stdio server that lists that tool
and answers every call with that answer.
"""

from __future__ import annotations

import importlib.resources
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from recon.adapters._out.api_collector import ApiCollector
from recon.adapters._out.capture_collector import CaptureCollector
from recon.adapters._out.capture_files import FilesystemCaptureLog
from recon.adapters._out.mcp_collector import McpCollector
from recon.adapters._out.system_clock import SystemClock
from recon.adapters._out.user_agent import DEFAULT_USER_AGENT
from recon.application.captures import record_hook_payload
from recon.domain.collector import CollectorContext
from recon.domain.deadline import Deadline
from recon.domain.http import HttpResponse
from recon.domain.models import CollectorEntry, SourceEntry
from recon.domain.normalize import BUILTIN_TRANSFORMS
from recon.domain.ratelimit import RateLimiter
from recon.domain.telemetry import Telemetry
from tests.recon_fakes import FixedClock

FIXTURES = Path(__file__).parent / "fixtures" / "templates"
REPLAY_SERVER = Path(__file__).parent / "mcp_server.py"

#: What each template's ``{placeholder}``s held when its responses were recorded.
RECORDED_WITH = {"github-audit": {"owner": "python", "repo": "cpython"}}

_CONTENT_TYPES = {".json": "application/json", ".xml": "application/atom+xml"}


def _templates() -> dict[str, dict[str, Any]]:
    files = importlib.resources.files("recon.configs")
    return {
        Path(f.name).stem: yaml.safe_load(f.read_text())
        for f in files.iterdir()
        if f.name.endswith(".yaml")
    }


def _normalized() -> list[Any]:
    return [
        pytest.param(name, entry, id=f"{name}:{entry['name']}")
        for name, config in sorted(_templates().items())
        for entry in config["collectors"]
        if entry.get("normalize")
    ]


class RecordedRequester:
    """The Requester port answering every request with one recorded body."""

    def __init__(self, fixture: Path) -> None:
        self._body = fixture.read_bytes()
        self._type = _CONTENT_TYPES[fixture.suffix]
        self.urls: list[str] = []

    def request(self, source: SourceEntry, method: str, url: str, **_: Any) -> HttpResponse:
        self.urls.append(url)
        return HttpResponse(
            status_code=200,
            headers={"content-type": self._type},
            body=self._body,
            text=self._body.decode(),
            url=url,
            content_type=self._type,
        )

    def get(self, source: SourceEntry, url: str, **_: Any) -> HttpResponse:
        return self.request(source, "GET", url)


def _records(template: str, raw: dict[str, Any], fixture: Path, tmp_path: Path) -> list[Any]:
    config = _templates()[template]
    filled = RECORDED_WITH.get(template, {})
    values = {key: filled.get(key, value) for key, value in (raw.get("vars") or {}).items()}
    entry = CollectorEntry.model_validate({**raw, **({"vars": values} if values else {})})
    if entry.type == "capture":
        record_hook_payload(
            FilesystemCaptureLog(tmp_path / "captures", FixedClock()),
            json.loads(fixture.read_text()),
        )
        return list(CaptureCollector(tmp_path).collect(entry, None))
    if entry.type == "mcp":
        return _replayed(entry, fixture, tmp_path)
    assert entry.type == "api", f"no fixture runner for a {entry.type} collector"
    [source] = [
        SourceEntry.model_validate(s) for s in config["catalog"] if s["name"] == entry.source
    ]
    requester = RecordedRequester(fixture)
    records = list(ApiCollector(requester, {}, DEFAULT_USER_AGENT).collect(entry, source))
    assert requester.urls, "the collector asked its source"
    return records


def _replayed(entry: CollectorEntry, recording: Path, tmp_path: Path) -> list[Any]:
    """The collector's records from a stdio server answering as the recorded one did."""
    command = {"command": sys.executable, "args": [str(REPLAY_SERVER), "--replay", str(recording)]}
    source = SourceEntry.model_validate({"name": entry.source, "type": "mcp", **command})
    clock = SystemClock()
    ctx = CollectorContext(
        requester=None,  # type: ignore[arg-type]
        converter=None,  # type: ignore[arg-type]
        shell=None,  # type: ignore[arg-type]
        telemetry=Telemetry([], clock),
        deadline=Deadline(clock),
        rate_limiter=RateLimiter(clock),
        transforms=BUILTIN_TRANSFORMS,
        mission_dir=tmp_path,
        env={},
        user_agent=DEFAULT_USER_AGENT,
    )
    collector = McpCollector(ctx)
    try:
        return list(collector.collect(entry, source))
    finally:
        collector.close()


@pytest.mark.parametrize(("template", "entry"), _normalized())
def test_every_column_holds_a_value_from_a_recorded_response(
    template: str, entry: dict[str, Any], tmp_path: Path
) -> None:
    fixtures = sorted(FIXTURES.glob(f"{template}/{entry['name']}.*"))
    assert fixtures, f"record a response of this collector's source as {FIXTURES}/{template}/"
    records = _records(template, entry, fixtures[0], tmp_path)

    assert records, "the recorded response gave no records: check extract:"
    empty = [
        column
        for column in entry["normalize"]
        if all(record[column] in (None, "", [], {}) for record in records)
    ]
    assert not empty, f"null in every record, so the path does not fit the source: {empty}"

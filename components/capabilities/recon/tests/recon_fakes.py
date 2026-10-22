"""Small fakes for the ports a survey reads through, and a one-call survey runner.

Each stands in for exactly one port and does nothing the port's contract does not promise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import recon
from recon.adapters._out.archive_files import ArchiveFiles
from recon.adapters._out.system_clock import SystemClock
from recon.application.survey import Survey
from recon.domain.archives import ARCHIVE_ID_FORMAT
from recon.domain.http import HttpResponse
from recon.domain.telemetry import Telemetry

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Mapping
    from pathlib import Path

    from recon.domain.clock import Clock
    from recon.domain.collector import Collector
    from recon.domain.documents import TableOutcome
    from recon.domain.models import ReconConfig, SourceEntry


class FixedClock:
    """A clock that reads the same instant every time and never waits.

    ``slept`` records every wait asked of it."""

    def __init__(self, at: datetime = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=UTC)) -> None:
        self._at = at
        self.slept: list[float] = []

    def now(self) -> datetime:
        return self._at

    def monotonic(self) -> float:
        return 1000.0 + sum(self.slept)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def advance(self, seconds: float) -> None:
        self._at += timedelta(seconds=seconds)


class ScriptedRequester:
    """The Requester port answering from a table of URL -> JSON body; any other URL is a
    test error. ``sent`` records each request."""

    def __init__(self, bodies: Mapping[str, str]) -> None:
        self._bodies = bodies
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
        self.sent.append({"method": method, "url": url, "params": params, "body": json_body})
        text = self._bodies[url]
        return HttpResponse(status_code=200, headers={"content-type": "application/json"},
                            body=text.encode(), text=text, url=url,
                            content_type="application/json")  # fmt: skip

    def get(
        self, source: SourceEntry, url: str, *, headers: dict[str, str] | None = None
    ) -> HttpResponse:
        return self.request(source, "GET", url, headers=headers)


class ScriptedShell:
    """The ShellRunner port printing a fixed list of lines for each command, which exits 0."""

    def __init__(self, lines: Mapping[str, list[str]]) -> None:
        self._lines = lines

    def run_lines(
        self,
        cmd: str,
        cwd: Path | None,
        timeout: float,
        *,
        capture_path: Path | None = None,
    ) -> Generator[str, None, int]:
        yield from self._lines[cmd]
        return 0


def run_survey(
    config: ReconConfig,
    collectors: Mapping[str, Collector],
    mission: Path,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    *,
    config_sha256: str | None = None,
    clock: Clock | None = None,
) -> tuple[Path, list[TableOutcome]]:
    """One survey over the real archive files, its events in the archive's event log, on the
    system clock unless one is given."""
    clock = clock or SystemClock()
    archives = ArchiveFiles(clock)
    started = clock.now()
    archive = archives.create(mission, started.strftime(ARCHIVE_ID_FORMAT))
    telemetry = Telemetry([archives.event_log(archive)], clock)
    survey = Survey(archives, clock, recon.__version__, telemetry, on_event)
    tables = survey.run(config, collectors, archive, started=started, config_sha256=config_sha256)
    return archive, tables

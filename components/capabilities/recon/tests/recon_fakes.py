"""Small fakes for the ports a survey reads through, and a one-call survey runner.

Each fake stands in for exactly one port and does nothing the port's contract does not
promise. The runner wires nothing itself: it is recon's own root with fakes given for the
ports a test stands in for.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from recon.composition import load_plugins, root
from recon.domain.collector import CollectorType, Plugins
from recon.domain.http import HttpResponse

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Mapping

    from recon.composition import RequesterFactory
    from recon.domain.clock import Clock
    from recon.domain.collector import Collector
    from recon.domain.converters import DocumentConverter
    from recon.domain.documents import TableOutcome
    from recon.domain.models import ReconConfig, SourceEntry
    from recon.domain.shell import ShellRunner


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


class HurriedClock:
    """The real passing of time, but a wait asked of it is recorded and skipped: ``slept``
    holds each one, and ``monotonic`` counts them as passed."""

    def __init__(self) -> None:
        self.slept: list[float] = []

    def now(self) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=sum(self.slept))

    def monotonic(self) -> float:
        return time.monotonic() + sum(self.slept)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


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


def plugins_with(**collectors: Collector) -> Plugins:
    """The installed plugins, and a collector type for each of ``collectors`` by name that
    builds that collector, declaring nothing else (no settings, no check)."""
    plugins = load_plugins()
    for name, collector in collectors.items():
        plugins.collector_types[name] = CollectorType(
            create=lambda ctx, collector=collector: collector, effects=None
        )
    return plugins


def run_survey(
    config: ReconConfig | Mapping[str, Any],
    project: Path,
    *,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    plugins: Plugins | None = None,
    requester: RequesterFactory | None = None,
    shell: ShellRunner | None = None,
    converter: DocumentConverter | None = None,
    clock: Clock | None = None,
    env: Mapping[str, str] | None = None,
    name: str = "m",
) -> tuple[Path, list[TableOutcome]]:
    """Survey ``config`` as the mission ``name`` of ``project`` through recon's own root,
    with a fake for each port given and the real adapter for the rest (no environment
    unless ``env`` is given). Returns the archive and its tables."""
    raw = config if isinstance(config, dict) else config.model_dump(mode="json", exclude_unset=True)
    mission = project / ".recon" / name
    mission.mkdir(parents=True, exist_ok=True)
    (mission / "config.yaml").write_text(yaml.safe_dump(dict(raw), sort_keys=False))
    recon = root(
        {} if env is None else env,
        project=project,
        clock=clock,
        plugins=plugins,
        requester=requester,
        shell=shell,
        converter=converter,
    )
    document = recon.survey(name, on_event=on_event)
    return Path(document.archive), document.tables

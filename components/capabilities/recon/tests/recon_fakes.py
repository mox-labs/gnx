"""Small fakes for the ports a survey reads through, and a one-call survey runner.

Each fake stands in for exactly one port and does nothing the port's contract does not
promise. The runner wires nothing itself: it is recon's own root with fakes given for the
ports a test stands in for.
"""

from __future__ import annotations

import contextlib
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import yaml

from recon.adapters._out.archive_files import FLUSH_BYTES, FLUSH_S, ArchiveFiles
from recon.adapters._out.mission_layout import STATE, archive_dir
from recon.composition import load_plugins, root
from recon.domain.archives import ArchiveRef
from recon.domain.collector import CollectorType, Plugins
from recon.domain.http import HttpResponse
from recon.domain.models import CollectorEntry, ReconConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Iterator, Mapping, Sequence
    from pathlib import Path

    from recon.composition import RequesterFactory
    from recon.domain.archives import Hold, Landed, Sidecar, TableEnds
    from recon.domain.capture import CaptureLog
    from recon.domain.clock import Clock
    from recon.domain.collector import Collector, CollectorContext
    from recon.domain.converters import ConversionResult, DocumentConverter
    from recon.domain.documents import SurveyDocument, TableOutcome, WindowOutcome
    from recon.domain.models import SourceEntry
    from recon.domain.shell import ShellRunner
    from recon.domain.telemetry import EventSink


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


class VirtualClock:
    """A clock whose time moves only when told (:meth:`set`, :meth:`advance`), shared by the
    threads of a stream: a wait asked of it returns once the time reaches its end, so a test
    runs a stream's hours in milliseconds, in the order its script says. A wait whose end
    the script never reaches returns after a little real time instead, so a thread waiting on
    it looks again at what it waits for; nothing sleeps for the time it was asked.
    ``waited`` is set once a thread has asked it to wait: a test waits on it for a thread
    to reach its first wait, rather than sleeping and hoping it has."""

    def __init__(self, at: datetime = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)) -> None:
        self._start = at
        self._t = 0.0
        self._cond = threading.Condition()
        self.waited = threading.Event()

    def now(self) -> datetime:
        with self._cond:
            return self._start + timedelta(seconds=self._t)

    def monotonic(self) -> float:
        with self._cond:
            return self._t

    def sleep(self, seconds: float) -> None:
        self.waited.set()
        with self._cond:
            end = self._t + seconds
            self._cond.wait_for(lambda: self._t >= end, timeout=0.05)

    def set(self, t: float) -> None:
        """Move the time to ``t``; never back."""
        with self._cond:
            self._t = max(self._t, t)
            self._cond.notify_all()

    def advance(self, seconds: float) -> None:
        with self._cond:
            self._t += seconds
            self._cond.notify_all()


class LandedThrough:
    """A :class:`~recon.domain.archives.Landed` that does what ``inner`` does: a test's
    subclass changes the one member it stands in for (a write that waits, a meta that cannot
    be recorded), through the port alone."""

    def __init__(self, inner: Landed) -> None:
        self.inner = inner

    @property
    def ref(self) -> ArchiveRef:
        return self.inner.ref

    def write(
        self,
        output: str,
        records: Generator[dict[str, Any], None, None],
        *,
        columns: Sequence[str] = (),
        keep_on_error: bool = False,
    ) -> int:
        return self.inner.write(output, records, columns=columns, keep_on_error=keep_on_error)

    def ends(self, output: str) -> TableEnds:
        return self.inner.ends(output)

    def keep(self, output: str, sidecar: Sidecar, lines: Iterable[dict[str, Any]]) -> None:
        self.inner.keep(output, sidecar, lines)

    def mark(self, output: str, sidecar: Sidecar, line: dict[str, Any]) -> None:
        self.inner.mark(output, sidecar, line)

    def captures(self) -> CaptureLog:
        return self.inner.captures()

    def events(self) -> EventSink:
        return self.inner.events()

    def record(self, meta: Mapping[str, Any]) -> None:
        self.inner.record(meta)

    def complete(self) -> None:
        self.inner.complete()


class WrappingArchives(ArchiveFiles):
    """The archive adapter over ``root``, each archive it lands handed to ``wrap`` first: a
    test's fault, given through the landing port."""

    def __init__(self, root: Path, clock: Clock, wrap: Callable[[Landed], Landed]) -> None:
        super().__init__(root, clock)
        self._wrap = wrap

    @contextlib.contextmanager
    def hold(self, mission: str, *, stream: str | None = None) -> Iterator[Hold]:
        with super().hold(mission, stream=stream) as held:
            yield _HoldThrough(held, self._wrap)


class _HoldThrough:
    def __init__(self, inner: Hold, wrap: Callable[[Landed], Landed]) -> None:
        self._inner = inner
        self._wrap = wrap

    @contextlib.contextmanager
    def begin(self, archive_id: str) -> Iterator[Landed]:
        with self._inner.begin(archive_id) as landed:
            yield self._wrap(landed)

    def retire(self, ref: ArchiveRef, tombstone: Mapping[str, Any]) -> None:
        self._inner.retire(ref, tombstone)


class StalledArchives(WrappingArchives):
    """The archive adapter, but a table is not written until :attr:`go` is set: a landing too
    slow for its stream, so what arrives meanwhile waits in the buffer or is dropped."""

    def __init__(self, root: Path, clock: Clock) -> None:
        super().__init__(root, clock, lambda landed: _Stalled(landed, self))
        self.go = threading.Event()
        #: Set once a writer is inside a table's write, held there until :attr:`go`.
        self.writing = threading.Event()


class _Stalled(LandedThrough):
    def __init__(self, inner: Landed, stall: StalledArchives) -> None:
        super().__init__(inner)
        self._stall = stall

    def write(
        self,
        output: str,
        records: Generator[dict[str, Any], None, None],
        *,
        columns: Sequence[str] = (),
        keep_on_error: bool = False,
    ) -> int:
        self._stall.writing.set()
        assert self._stall.go.wait(10), "the test never let the landing go"
        return super().write(output, records, columns=columns, keep_on_error=keep_on_error)


def archive_path(project: Path, ref: ArchiveRef) -> Path:
    """Where the file adapter keeps ``ref`` under ``project``: for a test of what it wrote."""
    return archive_dir(project / STATE, ref)


def kept(project: Path, survey: SurveyDocument) -> Path:
    """Where the file adapter keeps the archive ``survey`` landed."""
    return archive_path(project, ArchiveRef(survey.mission, survey.archive))


def window_kept(project: Path, mission: str, window: WindowOutcome) -> Path:
    """Where the file adapter keeps the window ``window`` of the mission's stream."""
    return archive_path(project, ArchiveRef(mission, window.id, window.stream))


#: The id :func:`landing` gives the archive it lands, unless told another.
ARCHIVE_ID = "2026-01-02-030405-000001"


def files_of(
    project: Path,
    clock: Clock | None = None,
    /,
    *,
    flush_bytes: int = FLUSH_BYTES,
    flush_s: float = FLUSH_S,
) -> ArchiveFiles:
    """The file adapter over ``project``'s missions, on ``clock`` (the system's by default),
    with ``flush_bytes`` and ``flush_s`` as given."""
    from recon.adapters._out.system_clock import SystemClock

    return ArchiveFiles(
        project / STATE, clock or SystemClock(), flush_bytes=flush_bytes, flush_s=flush_s
    )


@contextlib.contextmanager
def landing(
    project: Path,
    *,
    store: ArchiveFiles | None = None,
    mission: str = "m",
    archive_id: str = ARCHIVE_ID,
    stream: str | None = None,
) -> Iterator[tuple[Landed, Path]]:
    """One archive of ``mission`` (or a window of its ``stream``) landed through the file
    adapter under ``project`` (``store``, or one on the system clock), held for the block,
    and where the adapter keeps it."""
    files = store if store is not None else files_of(project)
    with files.hold(mission, stream=stream) as held, held.begin(archive_id) as landed:
        yield landed, archive_path(project, landed.ref)


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
        tee: Callable[[bytes], object] | None = None,
    ) -> Generator[str, None, int]:
        yield from self._lines[cmd]
        return 0


class NoConverter:
    """A DocumentConverter for a test that reaches none: converting a document is a failure."""

    def convert(self, content: bytes, content_type: str, url: str) -> ConversionResult:
        raise AssertionError("no test here converts a document")


def collector_entry(**fields: Any) -> CollectorEntry:
    """A ``CollectorEntry`` with the settings of its type (``run=``, ``endpoint=``, ...), which
    the model keeps as extra fields: they are arbitrary keys, so they are not typed."""
    return CollectorEntry(**fields)


def plugins_with(**collectors: Collector) -> Plugins:
    """The installed plugins, and a collector type for each of ``collectors`` by name that
    builds that collector, declaring nothing else (no settings, no check)."""
    plugins = load_plugins()
    for name, collector in collectors.items():
        plugins.collector_types[name] = CollectorType(create=_creating(collector), effects=None)
    return plugins


class EmptyCollector:
    """A Collector that finds no records: for a collector type that is registered, built and
    never run."""

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
    ) -> Iterator[dict[str, Any]]:
        return iter(())


def building(built: list[str], name: str) -> Callable[[CollectorContext], Collector]:
    """A collector type's ``create`` that records ``name`` in ``built`` and then fails: for a
    test of a run that must build no collector, which then sees what was built."""

    def create(ctx: CollectorContext) -> Collector:
        built.append(name)
        raise AssertionError(f"a collector was built: {name!r}")

    return create


def _creating(collector: Collector) -> Callable[[CollectorContext], Collector]:
    def create(ctx: CollectorContext) -> Collector:
        return collector

    return create


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
    unless ``env`` is given). Returns where the file adapter kept the archive, and its
    tables."""
    raw = (
        config.model_dump(mode="json", exclude_unset=True)
        if isinstance(config, ReconConfig)
        else config
    )
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
    return archive_path(project, ArchiveRef(name, document.archive)), document.tables

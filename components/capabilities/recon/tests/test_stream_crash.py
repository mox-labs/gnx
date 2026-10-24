"""A stream killed mid-window, then resumed (ruling B6; B10 #4), and a long stream's memory
(B10 #11).

The kill is a real SIGKILL of a ``recon stream`` process, once its windows hold a count of
lines a seeded generator picks, while its source is still being written: a thread appends to
the file it follows, and a command prints for ever. Afterwards the sealed windows are
unchanged, the window being written reads ``incomplete`` and keeps every whole line it had,
and the resumed stream goes on after the last of them: for a source with positions, the
windows together hold every line exactly once. For a source without positions, the resumed
window records the gap, from the last sealed window's close to its own open.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import random
import signal
import subprocess
import sys
import threading
import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

import recon
from recon.adapters._out.archive_files import ArchiveFiles
from recon.adapters._out.cli_collector import CliCollector, PopenRunner
from recon.adapters._out.system_clock import SystemClock
from recon.application.stream import Stream
from recon.composition import root
from recon.domain.errors import CollectionError
from recon.domain.models import CollectorEntry, SourceEntry
from recon.domain.stream import BufferSpec, WindowSpec
from tests.recon_fakes import VirtualClock
from tests.test_streams import Script, _plan, _telemetry

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator

    from recon.domain.documents import WindowOutcome

CLI = "import sys; from recon.adapters._in.cli import main; sys.exit(main())"
K = 7  # records a window


def _project(tmp_path: Path, collector: dict[str, Any], catalog: list[dict[str, Any]]) -> Path:
    (tmp_path / ".git").mkdir()
    mission = tmp_path / ".recon" / "m"
    mission.mkdir(parents=True)
    config = {"catalog": catalog, "collectors": [collector]}
    (mission / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    return mission / "streams" / str(collector["name"])


def _rows(window: Path, table: str) -> list[dict[str, Any]]:
    """A window's whole records: its sealed table, or the one its dead writer left."""
    sealed = window / f"{table}.jsonl"
    path = sealed if sealed.exists() else window / f"{table}.jsonl.tmp"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_bytes().split(b"\n")[:-1]]
    return [] if rows == [{"_empty": True}] else rows


def _written(stream: Path, table: str) -> int:
    windows = [w for w in sorted(stream.iterdir()) if w.is_dir() and not w.name.startswith(".")]
    return sum(len(_rows(w, table)) for w in windows)


def _digest(window: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(window.rglob("*")):
        if path.is_file():
            digest.update(path.name.encode() + path.read_bytes())
    return digest.hexdigest()


def _killed_at(stream: Path, table: str, lines: int, command: list[str], cwd: Path) -> datetime:
    """Start ``recon stream``, and SIGKILL it once ``lines`` records are in its windows; when
    it was killed."""
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)  # fmt: skip
    try:
        started = time.monotonic()
        while not stream.is_dir() or _written(stream, table) < lines:
            assert process.poll() is None, "the stream ended before it was killed"
            assert time.monotonic() - started < 30, "the stream never wrote enough"
            time.sleep(0.02)
        killed = datetime.now(UTC)
        os.kill(process.pid, signal.SIGKILL)
        process.wait(10)
    finally:
        if process.poll() is None:
            process.kill()
    return killed


class _Appender:
    """A file written to as it is followed: a line ``{"seq": n}`` every few milliseconds, on
    a thread, until stopped; stopped, it leaves the file ending mid-line."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._stopped = threading.Event()
        self.lines = 0
        self._thread = threading.Thread(target=self._append, daemon=True)

    def _append(self) -> None:
        with open(self._path, "a") as f:
            while not self._stopped.is_set():
                f.write(json.dumps({"seq": self.lines}) + "\n")
                f.flush()
                self.lines += 1
                time.sleep(0.004)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> int:
        """Stop appending, and begin one more line without ending it; the lines written whole."""
        self._stopped.set()
        self._thread.join(10)
        with open(self._path, "a") as f:
            f.write(f'{{"seq": {self.lines}, "pad": "xx')
        return self.lines


class TestKilledThenResumed:
    def test_a_positioned_source_resumes_exactly_once(self, tmp_path: Path) -> None:
        seed = random.Random(20261024)
        kill_at = seed.randint(20, 40)
        kill_at += kill_at % K == 0  # mid-window, so a window is being written when killed
        log = tmp_path / "app.log"
        log.write_text("".join(json.dumps({"seq": n}) + "\n" for n in range(kill_at)))
        with open(log, "a") as f:  # the source ends mid-line as the stream is killed
            f.write(f'{{"seq": {kill_at}, "pad": "xx')
        stream = _project(
            tmp_path,
            {"name": "log", "type": "tail", "source": "app", "start": "beginning",
             "interval": 0.02, "window": {"records": K}, "retain": "all"},
            [{"name": "app", "type": "file", "url": "app.log"}],
        )  # fmt: skip
        command = [sys.executable, "-c", CLI, "stream", "m", "--collector", "log", "--forever"]

        _killed_at(stream, "log", kill_at, command, tmp_path)

        store = ArchiveFiles(SystemClock())
        windows = store.archives(tmp_path / ".recon" / "m", stream="log")
        *sealed, dead = windows
        assert all(store.state(w) == "complete" for w in sealed)
        assert store.state(dead) == "incomplete", "the window being written when killed"
        before = {w.name: _digest(w) for w in sealed}
        # A write the kill cut short leaves a torn last line; make one, as the OS may.
        with open(dead / "log.jsonl.tmp", "ab") as f:
            f.write(b'{"seq": 9')
        kept = sum(len(_rows(w, "log")) for w in windows)
        assert kept == kill_at, "every whole line the source had was in a window"
        # Finish the line the source was cut in, and write on to two windows' worth, so the
        # resumed stream ends on a window's last record.
        total = kill_at + 2 * K
        with open(log, "a") as f:
            f.write('"}\n' + "".join(json.dumps({"seq": n}) + "\n"
                                     for n in range(kill_at + 1, total)))  # fmt: skip

        document = root({}, project=tmp_path).stream("m", "log", windows=2)

        after = store.archives(tmp_path / ".recon" / "m", stream="log")
        assert {w.name: _digest(w) for w in after if w.name in before} == before
        assert store.state(dead) == "incomplete", "a dead window is never changed"
        assert (dead / "log.jsonl.tmp.torn").read_bytes() == b'{"seq": 9'
        seqs = [row["seq"] for w in after for row in _rows(w, "log")]
        assert seqs == list(range(total)), "every line once, in order"
        assert document.last is not None
        first_resumed = yaml.safe_load((after[len(windows)] / "meta.yaml").read_text())
        assert first_resumed["stream"]["previous"] == dead.name
        # The restart is in the chain, resumed from the position kept (nothing was missed:
        # every line is there once); since when is unknown, the writer having died.
        [gap] = first_resumed["stream"]["sources"][0]["gaps"]
        assert (gap["from"], gap["resumed"]) == (None, True)

    def test_a_source_still_being_written_when_killed_resumes_exactly_once(
        self, tmp_path: Path
    ) -> None:
        """Review item 16 (Ixian): the kill comes while a thread still appends to the file,
        once the windows hold a seeded count of lines, so it lands wherever the stream is:
        in a window, sealing one, or between two. Wherever, the sealed windows are left as
        they were and the resumed stream holds every line once, in order."""
        kill_at = random.Random(20261026).randint(3 * K, 6 * K)
        log = tmp_path / "app.log"
        log.write_text("")
        stream = _project(
            tmp_path,
            {"name": "log", "type": "tail", "source": "app", "start": "beginning",
             "interval": 0.02, "window": {"records": K}, "retain": "all"},
            [{"name": "app", "type": "file", "url": "app.log"}],
        )  # fmt: skip
        command = [sys.executable, "-c", CLI, "stream", "m", "--collector", "log", "--forever"]
        appender = _Appender(log)
        appender.start()
        try:
            _killed_at(stream, "log", kill_at, command, tmp_path)
        finally:
            whole = appender.stop()  # the source ends mid-line after the kill

        store = ArchiveFiles(SystemClock())
        windows = store.archives(tmp_path / ".recon" / "m", stream="log")
        assert all(store.state(w) == "complete" for w in windows[:-1])
        before = {w.name: _digest(w) for w in windows if store.state(w) == "complete"}
        kept = sum(len(_rows(w, "log")) for w in windows)
        assert kill_at <= kept <= whole, "the windows hold whole lines the source had"
        # Finish the line the source was cut in, and write on to a whole number of windows'
        # worth past what was kept, so the resumed stream ends on a window's last record.
        resumed = -(-(whole + 1 - kept) // K)
        total = kept + resumed * K
        with open(log, "a") as f:
            f.write('"}\n' + "".join(json.dumps({"seq": n}) + "\n"
                                     for n in range(whole + 1, total)))  # fmt: skip

        root({}, project=tmp_path).stream("m", "log", windows=resumed)

        after = store.archives(tmp_path / ".recon" / "m", stream="log")
        assert {w.name: _digest(w) for w in after if w.name in before} == before
        seqs = [row["seq"] for w in after for row in _rows(w, "log")]
        assert seqs == list(range(total)), "every line once, in order"

    def test_a_source_without_positions_records_the_gap(self, tmp_path: Path) -> None:
        printer = (
            "import itertools, json, time\n"
            "for n in itertools.count():\n"
            "    print(json.dumps({'seq': n}), flush=True)\n"
            "    time.sleep(0.005)\n"
        )
        script = tmp_path / "counter.py"
        script.write_text(printer)
        stream = _project(
            tmp_path,
            {"name": "count", "type": "cli", "source": "here",
             "run": f"{sys.executable} {script}", "window": {"records": K}, "retain": "all"},
            [{"name": "here", "type": "local", "url": ".", "timeout": 3600}],
        )  # fmt: skip
        command = [sys.executable, "-c", CLI, "stream", "m", "--collector", "count", "--forever"]
        kill_at = random.Random(20261025).randint(2 * K, 5 * K)

        killed = _killed_at(stream, "count", kill_at, command, tmp_path)
        store = ArchiveFiles(SystemClock())
        last = store.archives(tmp_path / ".recon" / "m", stream="count")[-1]
        sealed = store.read_meta(last).get("stream")
        document = root({}, project=tmp_path).stream("m", "count", windows=1)

        assert document.last is not None
        meta = yaml.safe_load((Path(document.last.archive) / "meta.yaml").read_text())
        [gap] = meta["stream"]["sources"][0]["gaps"]
        assert gap["resumed"] is False, "the source could not go on from where it was"
        # Since when is known when the last window was sealed (the kill came between two
        # windows), and not when its writer died in it.
        assert gap["from"] == (sealed["closed_at"] if sealed else None)
        assert meta["stream"]["previous"] == last.name
        # Until when is the resumed run's start: after the kill, at its own window's open.
        to, opened = _instant(gap["to"]), _instant(meta["stream"]["opened_at"])
        assert to <= opened
        assert killed < to, "no process read the source from the kill on"
        if gap["from"] is not None:
            assert _instant(gap["from"]) <= to


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_a_signal_seals_the_window_with_every_record_it_had(
    tmp_path: Path, signum: signal.Signals
) -> None:
    """Review item 6 (Erlang #3): ``recon stream`` answers SIGTERM and Ctrl-C by sealing the
    window being written, once what waits is written, and returning success."""
    lines = 25
    log = tmp_path / "app.log"
    log.write_text("".join(json.dumps({"seq": n}) + "\n" for n in range(lines)))
    stream = _project(
        tmp_path,
        {"name": "log", "type": "tail", "source": "app", "start": "beginning",
         "interval": 0.02, "window": {"seconds": 300}, "retain": "all"},
        [{"name": "app", "type": "file", "url": "app.log"}],
    )  # fmt: skip
    command = [sys.executable, "-c", CLI, "stream", "m", "--collector", "log", "--forever",
               "--json"]  # fmt: skip
    process = subprocess.Popen(command, cwd=tmp_path, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)  # fmt: skip
    try:
        started = time.monotonic()
        while not stream.is_dir() or _written(stream, "log") < lines:
            assert process.poll() is None, "the stream ended before it was stopped"
            assert time.monotonic() - started < 30, "the stream never wrote its records"
            time.sleep(0.02)
        process.send_signal(signum)
        out, err = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()

    assert process.returncode == 0, err.decode()
    document = json.loads(out)
    assert (document["windows"], document["records"], document["dropped"]) == (1, lines, 0)
    assert document["last"]["closed_by"] == "signal"
    assert document["state"] == "complete"


def test_a_stream_of_a_quiet_command_leaves_no_process_behind(tmp_path: Path) -> None:
    """Review item 2 (Erlang): a stream of a ``cli`` command that goes quiet (``tail -f``,
    ``kubectl logs -f``) stops with the command killed, its process group with it, not left
    running once recon has returned."""
    _project(
        tmp_path,
        {"name": "quiet", "type": "cli", "source": "here",
         "run": 'echo "{\\"pid\\": $$}"; exec sleep 600', "window": {"seconds": 1},
         "retain": "all"},
        [{"name": "here", "type": "local", "url": ".", "timeout": 3600}],
    )  # fmt: skip

    document = root({}, project=tmp_path).stream("m", "quiet", windows=1)

    assert document.last is not None
    [row] = _rows(Path(document.last.archive), "quiet")
    started = time.monotonic()
    while _running(row["pid"]):
        assert time.monotonic() - started < 5, "the command outlived the stream"
        time.sleep(0.02)
    assert not [w for w in document.warnings if w["kind"] == "source_stuck"]


def _instant(value: Any) -> datetime:
    """A time from meta.yaml, which YAML may have read as a datetime already."""
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


class TestACommandStoppedBeforeItStarts:
    """Erlang's re-check: a command's lines start it on their first read, so a stop that lands
    after the collector looked for one but before the command was started found nothing to
    kill, and the command then ran on, orphaned. Neither window leaves a process now."""

    COMMAND = 'echo $$ > "{pid}"; exec sleep 600'

    def test_a_command_killed_before_its_first_read_is_killed_as_it_starts(
        self, tmp_path: Path
    ) -> None:
        pid = tmp_path / "pid"
        lines = PopenRunner().run_lines(self.COMMAND.format(pid=pid), tmp_path, 3600)
        lines.kill()  # type: ignore[attr-defined]

        started = time.monotonic()
        with contextlib.suppress(CollectionError, StopIteration):
            next(lines)

        assert time.monotonic() - started < 5, "the command ran on after its kill"
        _not_left_running(pid)

    def test_a_collector_closed_before_its_command_runs_leaves_no_process(
        self, tmp_path: Path
    ) -> None:
        pid = tmp_path / "pid"
        collector: CliCollector

        class ClosedMeanwhile(PopenRunner):
            """The stream stops between the collector's look for a stop and its command."""

            def run_lines(self, *args: Any, **kwargs: Any) -> Generator[str, None, int]:
                collector.close()
                return super().run_lines(*args, **kwargs)

        collector = CliCollector(ClosedMeanwhile())
        entry = CollectorEntry.model_validate(
            {"name": "quiet", "type": "cli", "source": "here",
             "run": self.COMMAND.format(pid=pid)}
        )  # fmt: skip
        source = SourceEntry.model_validate(
            {"name": "here", "type": "local", "url": str(tmp_path), "timeout": 3600}
        )

        started = time.monotonic()
        with contextlib.suppress(CollectionError):
            assert list(collector.collect(entry, source)) == []

        assert time.monotonic() - started < 5
        _not_left_running(pid)

    def test_a_stream_stopped_before_it_starts_leaves_no_process(self, tmp_path: Path) -> None:
        pid = tmp_path / "pid"
        _project(
            tmp_path,
            {"name": "quiet", "type": "cli", "source": "here",
             "run": self.COMMAND.format(pid=pid), "window": {"seconds": 60}, "retain": "all"},
            [{"name": "here", "type": "local", "url": ".", "timeout": 3600}],
        )  # fmt: skip
        stop = recon.StreamStop()
        stop.ask()

        started = time.monotonic()
        root({}, project=tmp_path).stream("m", "quiet", windows=1, stop=stop)

        assert time.monotonic() - started < 10
        _not_left_running(pid)


def _not_left_running(pid_file: Path) -> None:
    """The command whose shell wrote ``pid_file`` (if it got that far) is not running."""
    time.sleep(0.2)  # a shell killed as it started may still be writing its pid
    if not pid_file.exists() or not pid_file.read_text().strip():
        return
    pid = int(pid_file.read_text())
    started = time.monotonic()
    while _running(pid):
        assert time.monotonic() - started < 5, "the command outlived its stop"
        time.sleep(0.02)


def _running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_a_stream_that_ends_leaves_no_ticker_waiting_out_its_window(tmp_path: Path) -> None:
    """Erlang's re-check: the ticker slept on the clock until the open window's close, so a
    stream that ended early left it asleep for up to a window, an hour here, in a host that
    lives on. It sleeps in slices of at most a second, on the clock still, and looks between."""
    clock = SystemClock()
    store = ArchiveFiles(clock)
    events: list[dict[str, Any]] = []
    stream = Stream(store, clock, "test", telemetry=_telemetry(store, events))

    def ends_soon(after: Any) -> Iterator[dict[str, Any]]:
        yield {"seq": 0}
        time.sleep(0.3)  # the ticker is asleep on the hour by now

    run = stream.run(tmp_path, _plan(WindowSpec(seconds=3600)), ends_soon, dict, close=lambda: None)

    assert run.last is not None and run.last.closed_by == "eof"
    started = time.monotonic()
    while any(t.name == "recon-stream-ticker" for t in threading.enumerate()):
        assert time.monotonic() - started < 3, "the ticker sleeps on into the hour"
        time.sleep(0.02)


def test_memory_stays_flat_over_a_long_stream(tmp_path: Path) -> None:
    """B10 #11: 60 windows of 500 records, at a modest rate on a virtual clock, each window
    captured (``preserve_raw``, review item 17); what the stream holds does not grow with the
    windows it has sealed, and nor do its threads."""
    clock = VirtualClock()

    class Long(Script):
        def records(self, after: Any) -> Iterator[dict[str, Any]]:
            for n in range(30_000):
                self._clock.set(n * 0.01)
                yield {"seq": n, "text": "x" * 100}

    held: list[int] = []
    threads: list[int] = []

    def measure(outcome: WindowOutcome) -> None:
        held.append(tracemalloc.get_traced_memory()[0])
        threads.append(threading.active_count())

    tracemalloc.start()
    try:
        store = ArchiveFiles(clock)
        plan = _plan(WindowSpec(records=500), buffer=BufferSpec(records=64))
        events: list[dict[str, Any]] = []
        stream = Stream(store, clock, "test", telemetry=_telemetry(store, events))
        source = Long(clock, [])
        run = stream.run(
            tmp_path, plan, source.records, dict, close=source.close, on_window=measure,
            preserve_raw=True,
        )  # fmt: skip
    finally:
        tracemalloc.stop()

    assert run.windows == 60
    assert all((Path(o) / "captures.jsonl").exists() for o in _windows(tmp_path)), "captured"
    assert max(threads) == threads[0], f"threads by window: {threads}"
    early, late = max(held[5:15]), max(held[-10:])
    assert late - early < 1_000_000, f"held {early:,} bytes early, {late:,} late"
    assert len(events) == 60, "one event a window, and nothing kept of each but that"


def _windows(project: Path) -> list[Path]:
    home = project / "streams" / "s"
    return [w for w in sorted(home.iterdir()) if w.is_dir() and not w.name.startswith(".")]

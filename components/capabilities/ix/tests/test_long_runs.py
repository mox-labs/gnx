"""Long runs outlast a tool call (ruling Q5; panel 10-09 §6): ``ix run --detach`` starts a run
and returns, ``ix status`` says where it stands, ``ix events --follow`` waits for its end, and
``ix stop`` stops it. The primary caller is an agent, so each answer is a document with an exit
code that says the same.

Each run here is a real ``ix run`` in a process of its own: a ``check`` whose ``command`` probe
sleeps, against an ``http`` subject nothing contacts. Every process a test starts is killed at
its end, the detached run's and each probe's (which writes its id to a file as it starts).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner

import ix
from ix.adapters._in.cli import main
from ix.adapters._out import detached

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

Write = Any  # the write_experiment fixture

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX sessions and signals")

#: A probe that writes its process id into the directory it is given, sleeps, and removes the
#: file as it ends: a file left behind is a probe that did not end by itself.
_SLEEPER = (
    "import os, sys, time; "
    "mine = os.path.join(sys.argv[1], str(os.getpid())); "
    "open(mine, 'w').close(); "
    "time.sleep(float(sys.argv[2])); "
    "os.remove(mine)"
)


class _Processes:
    """What a test started: the detached runs' processes and the probes' pid files."""

    def __init__(self, pids: Path) -> None:
        self.pids = pids
        self.writers: list[int] = []

    def probes(self) -> list[int]:
        return [int(path.name) for path in self.pids.iterdir()]

    def wait_for_a_probe(self, timeout_s: float = 30.0) -> int:
        until = time.monotonic() + timeout_s
        while time.monotonic() < until:
            if found := self.probes():
                return found[0]
            time.sleep(0.05)
        raise AssertionError(f"no probe started within {timeout_s}s")

    def end(self) -> None:
        """Kill each process this test started that is still running, after checking from
        its command line that it is the one: a pid can be reused once its process ended."""
        marks = {pid: "ix run" for pid in self.writers}
        marks |= {pid: "time.sleep" for pid in self.probes()}
        for pid, mark in marks.items():
            if _alive(pid) and mark in _command(pid):
                os.kill(pid, signal.SIGKILL)


@pytest.fixture
def processes(tmp_path: Path) -> Iterator[_Processes]:
    pids = tmp_path / "pids"
    pids.mkdir()
    started = _Processes(pids)
    yield started
    started.end()


def _sleeping(processes: _Processes, seconds: float, repeats: int) -> dict[str, Any]:
    return {
        "name": "a-long-check",
        "question": "Does the command succeed?",
        "genre": "check",
        "repeats": repeats,
        "subjects": {"api": {"kind": "http", "url": "http://127.0.0.1:9"}},
        "probes": [
            {
                "id": "run",
                "kind": "command",
                "command": [sys.executable, "-c", _SLEEPER, str(processes.pids), str(seconds)],
                "timeout": "120s",
            }
        ],
        "primary": {"metric": "exited_ok"},
    }


def _command(pid: int) -> str:
    found = subprocess.run(
        ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    return found.stdout.strip()


def _alive(pid: int) -> bool:
    """Whether ``pid`` is a running process: one that ended and was not yet reaped (a
    zombie of this test's process) is not."""
    found = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    return bool(found.stdout.strip()) and "Z" not in found.stdout


def _cli(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.stdout


def test_an_agent_starts_a_run_returns_at_once_and_follows_it_to_its_verdict(
    write_experiment: Write, processes: _Processes
) -> None:
    """The ruling's loop, through the command line an agent drives: ``--detach`` returns
    while the run runs, with its id first and its directory; ``status`` says how far it got;
    ``events --follow`` returns when it ends; then ``status`` says it completed and ``report``
    gives what it found."""
    file = write_experiment(_sleeping(processes, seconds=1.0, repeats=3))
    began = time.monotonic()
    code, printed = _cli("run", str(file), "--exploratory", "--detach", "--json")
    started = json.loads(printed)
    processes.writers.append(started["pid"])
    assert time.monotonic() - began < 3.0 * 1.0, "it waited for the run"
    assert list(started)[:2] == ["schema", "run"]
    assert (started["schema"], started["status"], code) == ("ix.v1.status", "running", 13)
    assert started["planned"] == 3 and _alive(started["pid"])
    location = started["location"]
    assert location == str(file.parent / ".ix" / "runs" / started["run"])

    code, printed = _cli("status", location, "--json")
    assert (json.loads(printed)["status"], code) == ("running", 13)

    code, printed = _cli("events", location, "--follow", "--json")
    names = [json.loads(line)["name"] for line in printed.splitlines()]
    assert code == 0 and names.count("ix.trial.end") == 3 and names[-1] == "matrix.run.end"

    code, printed = _cli("status", location, "--json")
    ended = json.loads(printed)
    assert (ended["status"], ended["carried_out"], ended["pid"], code) == ("completed", 3, None, 0)
    assert ended["exit"]["kind"] == "ok" and "ix report" in ended["exit"]["next"]
    assert ix.report(location).indication == "holds"


def test_a_detached_run_is_stopped_releases_what_it_started_and_is_kept(
    write_experiment: Write, processes: _Processes
) -> None:
    """``ix stop`` ends a run as Ctrl-C ends one in the foreground: the trial in flight is the
    harness's, the probe it started is gone, the run's process has ended, and the record says
    interrupted (exit 12)."""
    started = ix.start(write_experiment(_sleeping(processes, 60.0, 3)), exploratory=True)
    assert started.pid is not None
    processes.writers.append(started.pid)
    probe = processes.wait_for_a_probe()

    stopped = ix.stop(started.location)
    assert (stopped.status, stopped.exit_code, stopped.pid) == ("interrupted", 12, None)
    assert not _alive(probe) and not _alive(started.pid)
    report = ix.report(started.location)
    assert report.status == "interrupted" and report.trials.harness_failures == 1
    # A run that has ended is left as it is.
    assert ix.stop(started.location).status == "interrupted"


def test_following_a_run_whose_process_was_killed_ends_and_it_reads_as_interrupted(
    write_experiment: Write, processes: _Processes
) -> None:
    """A follower never waits on a run nobody is writing: once the run's process is gone,
    ``events --follow`` returns and the run reads as interrupted."""
    started = ix.start(write_experiment(_sleeping(processes, 60.0, 3)), exploratory=True)
    assert started.pid is not None
    processes.writers.append(started.pid)
    processes.wait_for_a_probe()
    os.kill(started.pid, signal.SIGKILL)

    names = [event["name"] for event in ix.events(started.location, follow=True)]
    assert "ix.trial.start" in names
    after = ix.status(started.location)
    assert (after.status, after.exit_code) == ("interrupted", 12)


def test_a_detached_start_is_refused_before_anything_starts_as_a_run_is(
    write_experiment: Write, processes: _Processes
) -> None:
    """What ``ix run`` refuses, ``--detach`` refuses the same way and with the same exit code,
    and starts nothing: an unregistered experiment, a run id that is taken; and it cannot
    read its experiment from standard input."""
    file = write_experiment(_sleeping(processes, 0.0, 1))
    foreground, _ = _cli("run", str(file))
    background, _ = _cli("run", str(file), "--detach")
    assert foreground == background == ix.EXIT_CODES["unregistered"]
    assert not (file.parent / ".ix").exists()

    ix.run(file, exploratory=True, run_id="mine")
    with pytest.raises(ix.UsageError, match="kept already"):
        ix.start(file, exploratory=True, run_id="mine")
    with pytest.raises(ix.UsageError, match="standard input"):
        ix.start("-", exploratory=True)
    assert [run.run for run in ix.runs(file).runs] == ["mine"]


def test_a_run_is_kept_under_the_id_it_is_given_and_never_under_an_unsafe_one(
    write_experiment: Write, processes: _Processes
) -> None:
    file = write_experiment(_sleeping(processes, 0.0, 1))
    report = ix.run(file, exploratory=True, run_id="nightly.1")
    assert report.run == "nightly.1" and report.location.endswith("/.ix/runs/nightly.1")
    with pytest.raises(ix.UsageError, match="cannot be a run's id"):
        ix.run(file, exploratory=True, run_id="../elsewhere")


class TestTheStart:
    """What the detached start does with a child that never opens its record."""

    def test_a_child_that_fails_first_is_reported_with_the_error_it_printed(
        self, tmp_path: Path
    ) -> None:
        envelope = {
            "schema": "ix.v1.error",
            "error": {"kind": "config", "message": "a requirement does not hold", "fix": "x"},
        }
        code = (
            f"import sys; print({json.dumps(json.dumps(envelope))}, file=sys.stderr); sys.exit(3)"
        )
        log = tmp_path / "run.log"
        with pytest.raises(ix.ConfigError, match="a requirement does not hold") as raised:
            detached.start([sys.executable, "-c", code], log=log, opened=lambda: False)
        assert raised.value.details["log"] == str(log)

    def test_one_that_does_not_start_in_time_is_stopped(self, tmp_path: Path) -> None:
        log = tmp_path / "run.log"
        with pytest.raises(ix.ConfigError, match="did not open its record within") as raised:
            detached.start(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                log=log,
                opened=lambda: False,
                startup_s=0.5,
            )
        assert all(child.poll() is not None for child in detached._started)
        assert raised.value.details["path"] == str(log)

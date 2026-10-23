"""Tests for CliCollector — subprocess → streaming records."""

import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from recon.adapters._out.cli_collector import STDERR_TAIL_BYTES, CliCollector, PopenRunner
from recon.adapters._out.parsing import parse_line as _parse_line
from recon.domain.errors import CollectionError
from recon.domain.models import SourceEntry
from tests.recon_fakes import collector_entry


class TestParseLine:
    def test_jsonl(self) -> None:
        assert _parse_line('{"a": 1}', 1) == {"a": 1}

    def test_text_fallback(self) -> None:
        assert _parse_line("line one", 3) == {"line_number": 3, "line": "line one"}

    def test_empty(self) -> None:
        assert _parse_line("", 1) is None
        assert _parse_line("   ", 1) is None

    def test_non_dict_json_falls_back(self) -> None:
        """JSON arrays/numbers aren't records — treat as text line."""
        assert _parse_line("[1, 2, 3]", 1) == {"line_number": 1, "line": "[1, 2, 3]"}


class TestCliCollector:
    def test_basic_jsonl(self) -> None:
        entry = collector_entry(
            name="test",
            type="cli",
            run='printf \'{"x": 1}\\n{"x": 2}\\n\'',
        )
        records = list(CliCollector().collect(entry, None))
        assert len(records) == 2
        assert records[0]["x"] == 1

    def test_text_output(self) -> None:
        entry = collector_entry(
            name="lines",
            type="cli",
            run="echo 'hello world'",
        )
        records = list(CliCollector().collect(entry, None))
        assert len(records) == 1
        assert "line" in records[0]

    def test_patterns(self) -> None:
        entry = collector_entry(
            name="pats",
            type="cli",
            run="echo '{pattern}'",
            patterns=["alpha", "beta"],
        )
        records = list(CliCollector().collect(entry, None))
        assert len(records) == 2
        assert records[0]["_pattern"] == "alpha"
        assert records[1]["_pattern"] == "beta"

    def test_source_url_substitution(self) -> None:
        entry = collector_entry(
            name="url-test",
            type="cli",
            run="echo '{url}'",
        )
        source = SourceEntry(name="test", url="https://example.com")
        records = list(CliCollector().collect(entry, source))
        assert "https://example.com" in records[0]["line"]

    def test_source_as_cwd(self) -> None:
        """Local source URL is used as working directory."""
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "marker.txt").write_text("found")

            entry = collector_entry(
                name="cwd-test",
                type="cli",
                run="cat marker.txt",
            )
            source = SourceEntry(name="repo", type="local", url=tmp)
            records = list(CliCollector().collect(entry, source))
            assert "found" in records[0]["line"]

    def test_a_local_source_that_is_not_a_directory_runs_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: the command ran in recon's working directory instead."""
        monkeypatch.chdir(tmp_path)
        entry = collector_entry(name="cwd-test", type="cli", run="touch ran-here")
        source = SourceEntry(name="repo", type="local", url=str(tmp_path / "missing"))
        with pytest.raises(CollectionError) as excinfo:
            list(CliCollector().collect(entry, source))
        assert excinfo.value.kind == "config"
        assert str(tmp_path / "missing") in str(excinfo.value)
        assert not (tmp_path / "ran-here").exists()

    def test_a_command_with_no_source_has_300_seconds(self) -> None:
        """A source's ``timeout`` bounds its commands; a command with no source gets 300 s."""
        given: list[float] = []

        class Shell:
            def run_lines(
                self, cmd: str, cwd: Path | None, timeout: float, **kwargs: Any
            ) -> Generator[str, None, int]:
                given.append(timeout)
                yield '{"a": 1}'
                return 0

        entry = collector_entry(name="c", type="cli", run="true")
        list(CliCollector(Shell()).collect(entry, None))
        assert given == [300.0]

    def test_url_with_no_source_to_fill_it_runs_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: ``{url}`` with no source was run literally."""
        monkeypatch.chdir(tmp_path)
        entry = collector_entry(name="u", type="cli", run="touch ran {url}")
        with pytest.raises(CollectionError) as excinfo:
            list(CliCollector().collect(entry, None))
        assert excinfo.value.kind == "config" and "{url}" in str(excinfo.value)
        assert not (tmp_path / "ran").exists()

    def test_normalize(self) -> None:
        entry = collector_entry(
            name="norm",
            type="cli",
            run='printf \'{"nested": {"val": 42}}\\n\'',
            normalize={"value": "nested.val"},
        )
        records = list(CliCollector().collect(entry, None))
        assert records[0]["value"] == 42

    def test_failed_command_raises(self) -> None:
        """A non-zero exit other than a silent exit 1 surfaces during iteration."""
        entry = collector_entry(
            name="fail",
            type="cli",
            run="exit 2",
        )
        with pytest.raises(CollectionError, match="exit 2"):
            list(CliCollector().collect(entry, None))

    def test_empty_output(self) -> None:
        """Command with no stdout yields nothing (dispatch loop adds sentinel)."""
        entry = collector_entry(
            name="empty",
            type="cli",
            run="true",
        )
        records = list(CliCollector().collect(entry, None))
        assert records == []

    def test_streaming_yields_lazily(self) -> None:
        """Generator shape — records are yielded as produced, not batched."""
        entry = collector_entry(
            name="streaming",
            type="cli",
            run='printf \'{"i": 1}\\n{"i": 2}\\n{"i": 3}\\n\'',
        )
        gen = CliCollector().collect(entry, None)
        # next() without list() proves it's a generator
        first = next(gen)
        assert first["i"] == 1
        rest = list(gen)
        assert [r["i"] for r in rest] == [2, 3]

    def test_generator_cleanup_on_abandonment(self, tmp_path: Path) -> None:
        """Abandoning the iterator kills the command and what it started (no leak): the
        shell and its background ``sleep`` are both gone once the generator is closed."""
        pids = tmp_path / "pids"
        entry = collector_entry(
            name="longlived",
            type="cli",
            run=f"sleep 10 & echo $$ $! > {pids}; printf '{{\"a\": 1}}\\n'; wait",
        )
        gen = CliCollector().collect(entry, None)
        assert isinstance(gen, Generator)
        assert next(gen) == {"a": 1}
        shell, sleeper = map(int, pids.read_text().split())
        started = time.monotonic()
        gen.close()
        assert time.monotonic() - started < 3, "closing did not wait out the command"
        deadline = time.monotonic() + 5
        while any(_alive(pid) for pid in (shell, sleeper)) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(shell) and not _alive(sleeper)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _run_in_thread(cmd: str, timeout: float, limit: float) -> dict[str, Any]:
    """Run ``cmd`` through PopenRunner on a thread, so a runner that hangs fails the test
    after ``limit`` seconds instead of hanging it. Returns the lines, the exit code or the
    error, and how long it took."""
    outcome: dict[str, Any] = {}

    def go() -> None:
        start = time.monotonic()
        lines = PopenRunner().run_lines(cmd, None, timeout)
        got: list[str] = []
        try:
            while True:
                got.append(next(lines))
        except StopIteration as finished:
            outcome["code"] = finished.value
        except CollectionError as error:
            outcome["error"] = error
        outcome["lines"] = got
        outcome["seconds"] = time.monotonic() - start

    thread = threading.Thread(target=go, daemon=True)
    thread.start()
    thread.join(limit)
    assert outcome, f"still running after {limit}s"
    return outcome


class TestPopenRunnerFailures:
    """Each way a command can fail lands as a CollectionError; none hangs or reads as ok."""

    def test_the_timeout_holds_while_the_command_keeps_stdout_open(self) -> None:
        """Regression: the timeout applied only after stdout closed, so `sleep 5` with a
        0.5s timeout ran 5s and counted as success."""
        outcome = _run_in_thread("sleep 5", timeout=0.5, limit=4)
        assert "timed out" in str(outcome["error"]) and outcome["error"].fix
        assert outcome["seconds"] < 2

    def test_a_timeout_kills_what_the_command_started(self) -> None:
        marker = f"recon-timeout-{uuid.uuid4().hex}"
        _run_in_thread(f"sh -c 'sleep 30; : {marker}' & sleep 30", timeout=0.5, limit=4)
        time.sleep(0.2)
        found = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
        assert found.stdout == "", "a process the command started outlived the timeout"

    def test_exit_1_with_stderr_fails_the_table_and_keeps_the_reason(self) -> None:
        """Regression: exit 1 counted as success, so `cat` of a missing file wrote an ok,
        empty table and dropped its stderr."""
        outcome = _run_in_thread("cat /nonexistent/recon-test-file", timeout=5, limit=4)
        error = outcome["error"]
        assert "exit 1" in str(error) and "No such file" in str(error)
        assert error.fix

    def test_exit_1_with_nothing_on_stderr_is_no_match(self) -> None:
        outcome = _run_in_thread("echo hay | grep needle", timeout=5, limit=4)
        assert outcome["code"] == 1 and outcome["lines"] == []

    def test_the_exit_code_is_returned(self) -> None:
        outcome = _run_in_thread("printf 'a\\nb'", timeout=5, limit=4)
        assert outcome == {**outcome, "code": 0, "lines": ["a", "b"]}

    def test_a_flood_on_stderr_does_not_block_the_command(self) -> None:
        """Regression: stderr was drained only after stdout closed, so a command writing
        more than a pipe buffer to stderr blocked forever."""
        flood = f"{sys.executable} -c \"import sys; sys.stderr.write('x' * 200000); print('done')\""
        outcome = _run_in_thread(flood, timeout=10, limit=5)
        assert outcome["code"] == 0 and outcome["lines"] == ["done"]

    def test_only_the_tail_of_stderr_reaches_the_error(self) -> None:
        noisy = (f"{sys.executable} -c \"import sys; sys.stderr.write('x' * 200000 + 'END'); "
                 "sys.exit(3)\"")  # fmt: skip
        error = _run_in_thread(noisy, timeout=10, limit=5)["error"]
        assert str(error).endswith("END") and len(str(error)) < STDERR_TAIL_BYTES + 500

    def test_a_command_reading_stdin_reads_nothing_and_finishes(self) -> None:
        """Regression: the command inherited recon's stdin, so ``rg`` with no path (which
        reads stdin when it is not a tty) hung until its timeout. The survey runs in a child
        whose stdin is a pipe this test holds open."""
        survey = (
            "from recon.adapters._out.cli_collector import CliCollector\n"
            "from recon.domain.models import CollectorEntry\n"
            "entry = CollectorEntry(name='c', type='cli', run='cat')\n"
            "print(list(CliCollector().collect(entry, None)))\n"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", survey],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert child.stdin is not None and child.stdout is not None and child.stderr is not None
        try:
            finished = child.wait(timeout=15) is not None
        except subprocess.TimeoutExpired:
            child.kill()
            finished = False
        child.stdin.close()  # a leftover `cat` now reads end-of-file and exits
        stdout, stderr = child.stdout.read(), child.stderr.read()
        child.wait()
        assert finished, "the command read the survey's stdin and blocked on it"
        assert child.returncode == 0, stderr.decode()
        assert stdout.decode().strip() == "[]"

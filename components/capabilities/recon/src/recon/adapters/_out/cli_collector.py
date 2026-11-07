"""CliCollector: runs shell commands, each command's stdout a body recon converts line by line.

stdout is read as it arrives, and recon converts and captures it as it flows, so a large output
(``rg --json`` across a huge tree) is never held in memory whole. The command runs in its own
session (POSIX), so a timeout, an abandoned read or an interrupt kills its whole process group,
not just the shell. stderr is
drained as the command runs and its tail kept for the error message, so a command that
writes a lot to it cannot block on a full pipe. stdin is empty: a command that reads it
(``rg`` given no path) reads nothing, rather than waiting on recon's own stdin.

Registered as the built-in collector type ``cli``, with the source kind ``local``.
"""

from __future__ import annotations

import contextlib
import os
import selectors
import signal
import subprocess
import threading
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from recon.core.domain.collector import Body, BodyEnd, PlainKind
from recon.core.domain.errors import CollectionError, Problem
from recon.core.domain.substitution import substitute

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from recon.core.domain.collector import Effect, Intent, Item, Position, RecordsKind, Run
    from recon.core.domain.deadline import Deadline
    from recon.core.domain.models import SourceEntry
    from recon.core.domain.shell import ShellRunner

#: The most of a command's stderr kept for its error message: the end, where the reason is.
STDERR_TAIL_BYTES = 4096

_READ_CHUNK = 65536


class CliSettings(BaseModel):
    """A ``cli`` collector's own keys: ``run``, the shell command, and ``patterns``, each
    filling ``{pattern}`` in turn."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run: str | None = None
    patterns: list[str] | None = None


@runtime_checkable
class _Killable(Protocol):
    """Lines a shell runner gave that can be killed from another thread (``_Command``'s); a
    runner's that cannot (a test's) end when they are closed."""

    def kill(self) -> None: ...


class _Command(Generator[str, None, int]):
    """A command's lines as they come (the generator ``run_lines`` gives), which can also be
    killed from another thread: ``kill()`` kills the command's process group, so a read of
    it blocked in another thread ends. A stream's ``cli`` collector kills its command so. The
    command starts on the first read; one killed before that is killed as it starts."""

    def __init__(self, lines: Callable[[_Command], Generator[str, None, int]]) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.killed = False
        self._lock = threading.Lock()
        self._lines = lines(self)

    def send(self, value: None) -> str:
        return self._lines.send(value)

    def throw(self, *args: Any) -> str:
        return self._lines.throw(*args)

    def close(self) -> None:
        self._lines.close()

    def kill(self) -> None:
        with self._lock:
            self.killed = True
            process = self.process
        if process is not None:
            _kill_group(process)

    def started(self, process: subprocess.Popen[bytes]) -> None:
        """The command is running as ``process``: killed at once if it was killed before."""
        with self._lock:
            self.process = process
            killed = self.killed
        if killed:
            _kill_group(process)


class PopenRunner:
    """The ShellRunner over ``/bin/sh``. A timer kills the command at its timeout, so the
    deadline holds however slowly the lines are read, and no clock is read here. The lines it
    gives have a ``kill()``, which kills the command from any thread."""

    def run_lines(
        self,
        cmd: str,
        cwd: Path | None,
        timeout: float,
        *,
        tee: Callable[[bytes], object] | None = None,
    ) -> Generator[str, None, int]:
        return _Command(lambda command: self._lines(cmd, cwd, timeout, tee, command))

    def _lines(
        self,
        cmd: str,
        cwd: Path | None,
        timeout: float,
        tee: Callable[[bytes], object] | None,
        command: _Command,
    ) -> Generator[str, None, int]:
        # shell=True is intentional: configs are trusted local input.
        proc = subprocess.Popen(  # noqa: S602
            cmd,
            shell=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            start_new_session=True,
        )
        command.started(proc)
        expired = threading.Event()

        def expire() -> None:
            expired.set()
            _kill_group(proc)

        timer = threading.Timer(timeout, expire)
        timer.daemon = True
        timer.start()
        stderr = bytearray()
        try:
            for line in _stdout_lines(proc, stderr):
                if tee is not None:
                    tee(line.encode("utf-8"))
                yield line.removesuffix("\n").removesuffix("\r")
            proc.wait()
        finally:
            timer.cancel()
            _kill_group(proc)
            proc.wait()
            for pipe in (proc.stdout, proc.stderr):
                if pipe is not None:
                    pipe.close()
        if expired.is_set():
            raise CollectionError(
                f"Command timed out after {timeout}s: {cmd}",
                fix="raise the source's timeout:, or make the command finish sooner",
            )
        code = proc.returncode
        tail = stderr.decode("utf-8", errors="replace").strip()
        # grep and rg exit 1, silently, when nothing matched; any other failure says why.
        if code == 0 or (code == 1 and not tail):
            return code
        where = f" in {cwd}" if cwd else ""
        raise CollectionError(
            f"Command failed (exit {code}): {cmd}" + (f"\n{tail}" if tail else ""),
            fix=f"run the command by hand{where} to see why it failed, then fix run:",
        )


def _stdout_lines(proc: subprocess.Popen[bytes], stderr: bytearray) -> Iterator[str]:
    """Each stdout line, decoded, as it completes; meanwhile the tail of stderr into
    ``stderr``. Ends when both pipes have closed."""
    assert proc.stdout is not None and proc.stderr is not None
    with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ)
        selector.register(proc.stderr, selectors.EVENT_READ)
        pending = b""
        while selector.get_map():
            for key, _events in selector.select():
                chunk = os.read(key.fd, _READ_CHUNK)
                if not chunk:
                    selector.unregister(key.fileobj)
                elif key.fileobj is proc.stderr:
                    stderr += chunk
                    del stderr[:-STDERR_TAIL_BYTES]
                else:
                    *complete, pending = (pending + chunk).split(b"\n")
                    for raw in complete:
                        yield (raw + b"\n").decode("utf-8", errors="replace")
        if pending:
            yield pending.decode("utf-8", errors="replace")


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    """Kill the command and everything it started, unless it has already been reaped (until
    then its pid, and so its group id, cannot be reused)."""
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)


class CliCollector:
    """Runs shell commands; each command's stdout is one body, read as it arrives."""

    effects: frozenset[Effect] | None = frozenset({"subprocess"})
    records: RecordsKind = "finite"
    # A command runs in a local source's directory; any other source would leave it in
    # recon's working directory.
    accepts: frozenset[str] | None = frozenset({"local"})

    def __init__(self, *, shell: ShellRunner, deadline: Deadline | None = None) -> None:
        self._runner = shell
        self._deadline = deadline
        #: The command running now, and whether the collector was closed.
        self._running: Any = None
        self._closed = threading.Event()

    def describe(self, run: Run) -> Intent:
        # Effects are declared, not inspected: the command may do anything a shell can.
        # The plan shows the exact commands so a reviewer can read them before they run, and
        # where: a local source's directory, or None for recon's working directory.
        source = run.source
        return {
            "commands": [cmd for _pattern, cmd in _commands(run)],
            "cwd": source.url if source is not None and source.type == "local" else None,
        }

    def check(self, run: Run) -> list[Problem]:
        return _check(run) + _check_run(run)

    def close(self) -> None:
        """Kill the command in flight, from any thread, and start no other: a stream's
        source that goes quiet (``tail -f``) ends so, its process group with it."""
        self._closed.set()
        if isinstance(self._running, _Killable):
            self._running.kill()

    def collect(self, run: Run, resume: Position | None) -> Iterator[Item]:
        """Each command's stdout as a body (``text/plain``, converted line by line), with
        ``patterns:`` one command a pattern, each record of it carrying ``_pattern``."""
        for problem in _check_run(run):  # the plan's check, for a direct caller
            raise CollectionError(
                f"collector {run.entry.name!r}: {problem.message}; nothing was run",
                kind="config",
                fix=problem.fix,
            )
        source = run.source
        cwd = _cwd(source)
        timeout = source.timeout if source else 300.0
        for pattern, cmd in _commands(run):
            if self._closed.is_set():
                return
            request = {"commands": [cmd], "cwd": str(cwd) if cwd else None}
            yield Body(
                media_type="text/plain; charset=utf-8",
                data=self._stdout(cmd, cwd, timeout, request),
                request=request,
                kind="cli",
                fields={"_pattern": pattern} if pattern is not None else {},
            )

    def _stdout(
        self, cmd: str, cwd: Path | None, timeout: float, request: dict[str, Any]
    ) -> Generator[bytes, None, BodyEnd]:
        """One command's stdout as it flows, each line's bytes as the shell read them (or
        the line again, from a shell that tees nothing); returns its exit code."""
        # A deadline shorter than the command's timeout cuts it: the error says so.
        limit = timeout
        if self._deadline is not None:
            self._deadline.check()
            limit = self._deadline.limit(timeout)
        teed: list[bytes] = []
        lines = self._runner.run_lines(cmd, cwd, limit, tee=teed.append)
        self._running = lines
        if self._closed.is_set():  # closed meanwhile, before it saw these lines
            lines.close()
            return BodyEnd()
        try:
            with contextlib.closing(lines):
                while True:
                    try:
                        line = next(lines)
                    except StopIteration as finished:
                        code: int = finished.value
                        yield from teed
                        return BodyEnd(status=code, request={**request, "exit_codes": [code]})
                    if teed:
                        yield from teed
                        teed.clear()
                    else:
                        yield f"{line}\n".encode()
        except CollectionError as exc:
            cut = self._deadline is not None and self._deadline.remaining() == 0
            if cut and limit < timeout and self._deadline is not None:
                raise self._deadline.exceeded() from exc
            raise


class LocalSource(PlainKind):
    """The ``local`` source kind: a directory of this host (its ``url``, relative to the
    project root), where a command runs."""


def _cwd(source: SourceEntry | None) -> Path | None:
    """The directory the commands run in: a ``local`` source's, which must be one; None,
    recon's working directory, with no source or a source of another type."""
    if source is None or source.type != "local":
        return None
    path = Path(source.url)
    if not path.is_dir():
        raise CollectionError(
            f"local source {source.name!r} names {source.url}, which is not a directory; "
            "nothing was run",
            kind="config",
            fix="create the directory, or correct the source's url:",
        )
    return path


def _commands(run: Run) -> list[tuple[str | None, str]]:
    """(pattern, substituted command) for each invocation the entry makes. recon fills
    ``{url}`` and ``{pattern}``; any other ``{...}`` is the command's own (cargo's
    ``--format '{p}'``, a jq object, docker's ``'{{.Name}}'``) and is passed as written."""
    settings = run.settings_as(CliSettings)
    source = run.source
    if not settings.run:
        return []
    variables = {"url": source.url} if source else {}
    patterns: list[str | None] = list(settings.patterns) if settings.patterns else [None]
    out = []
    for pattern in patterns:
        cmd_vars = {**variables, "pattern": pattern} if pattern is not None else variables
        out.append((pattern, substitute(settings.run, cmd_vars, escapes=False)))
    return out


# --- Registration ---


def _check_run(run: Run) -> list[Problem]:
    """``{url}`` is the source's url, so a run with no source cannot fill it."""
    command = run.settings_as(CliSettings).run
    if run.source is not None or not command or "{url}" not in command:
        return []
    return [
        Problem(
            "run",
            "run: uses {url}, the source's url, and this run has no source",
            kind="unresolved",
            fix="pin a catalog source with source:, or take {url} out of run:",
            got=command,
        )
    ]


def _check(run: Run) -> list[Problem]:
    settings = run.settings_as(CliSettings)
    if not settings.run:
        return [
            Problem(
                "run",
                "a cli collector needs run: (the shell command)",
                kind="missing_key",
                fix="run: \"rg --json 'TODO'\"",
            )
        ]
    if "{pattern}" in settings.run and not settings.patterns:
        return [
            Problem(
                "patterns",
                "run: uses {pattern} but no patterns: are listed",
                kind="missing_key",
                fix="list the values under patterns:, or remove {pattern} from run:",
            )
        ]
    return []


def register(registry: Any) -> None:
    """The ``cli`` collector type and the ``local`` source kind."""
    registry.register(
        "recon.collector",
        "recon.v1.collector.cli",
        CliCollector,
        config=CliSettings,
        needs={"shell", "deadline"},
        summary="shell command → stdout lines (JSON objects or text) → records",
    )
    registry.register(
        "recon.source",
        "recon.v1.source.local",
        LocalSource,
        summary="a directory of this host, where a command runs",
    )

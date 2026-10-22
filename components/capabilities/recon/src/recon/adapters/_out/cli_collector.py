"""CliCollector: runs shell commands and yields records line by line.

stdout is read as it arrives, so a large output (``rg --json`` across a huge tree) is never
held in memory whole. The command runs in its own session (POSIX), so a timeout, an
abandoned read or an interrupt kills its whole process group, not just the shell. stderr is
drained as the command runs and its tail kept for the error message, so a command that
writes a lot to it cannot block on a full pipe. stdin is empty: a command that reads it
(``rg`` given no path) reads nothing, rather than waiting on recon's own stdin.

Registered as the built-in collector type ``cli`` (entry-point group ``recon.collectors``).
"""

from __future__ import annotations

import contextlib
import os
import selectors
import signal
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from recon.adapters._out.parsing import parse_line
from recon.domain.collector import CollectorType
from recon.domain.errors import CollectionError, Problem
from recon.domain.normalize import BUILTIN_TRANSFORMS, apply_normalize, bound
from recon.domain.substitution import substitute

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator, Mapping

    from recon.domain.capture import CaptureLog
    from recon.domain.collector import Transform
    from recon.domain.deadline import Deadline
    from recon.domain.models import CollectorEntry, SourceEntry
    from recon.domain.shell import ShellRunner

#: The most of a command's stderr kept for its error message: the end, where the reason is.
STDERR_TAIL_BYTES = 4096

_READ_CHUNK = 65536


class CliSettings(BaseModel):
    """A ``cli`` collector's own keys: ``run``, the shell command, and ``patterns``, each
    filling ``{pattern}`` in turn."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run: str | None = None
    patterns: list[str] | None = None


def _settings(entry: CollectorEntry) -> CliSettings:
    return CliSettings.model_validate(entry.settings)


class PopenRunner:
    """The ShellRunner over ``/bin/sh``. A timer kills the command at its timeout, so the
    deadline holds however slowly the lines are read, and no clock is read here."""

    def run_lines(
        self,
        cmd: str,
        cwd: Path | None,
        timeout: float,
        *,
        capture_path: Path | None = None,
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
        expired = threading.Event()

        def expire() -> None:
            expired.set()
            _kill_group(proc)

        timer = threading.Timer(timeout, expire)
        timer.daemon = True
        timer.start()
        stderr = bytearray()
        try:
            with _tee(capture_path) as tee:
                for line in _stdout_lines(proc, stderr):
                    tee(line)
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


@contextlib.contextmanager
def _tee(path: Path | None) -> Iterator[Callable[[str], object]]:
    """A function writing each line to ``path`` (appended: a pattern fan-out concatenates
    into one body), or one that does nothing when there is no path."""
    if path is None:
        yield lambda line: None
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        yield f.write


class CliCollector:
    """Runs shell commands, yields parsed stdout records as they stream."""

    def __init__(
        self,
        runner: ShellRunner | None = None,
        transforms: Mapping[str, Transform] = BUILTIN_TRANSFORMS,
        deadline: Deadline | None = None,
    ) -> None:
        self._runner = runner if runner is not None else PopenRunner()
        self._transforms = transforms
        self._deadline = deadline

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
    ) -> Iterator[dict[str, Any]]:
        run = _settings(entry).run
        assert run is not None  # _check requires it, and the plan holds it
        if source is None and "{url}" in run:
            raise CollectionError(
                f"collector {entry.name!r}: run: uses {{url}}, the source's url, and this run "
                "has no source; nothing was run",
                kind="config",
                fix="pin a catalog source with source:, or take {url} out of run:",
            )

        cwd = _cwd(source)
        timeout = source.timeout if source else 300.0
        commands = _commands(entry, source)

        # Raw capture: tee every invocation's stdout into one body per collector entry
        # (patterns concatenate). Path is None when the mission does not preserve raw.
        capture_path = captures.stream_path(entry.name) if captures is not None else None
        exit_codes: list[int] = []

        # A path a command prints is relative to where it ran: a transform that reads one
        # resolves it there.
        transforms = bound(self._transforms, base=cwd) if cwd is not None else self._transforms
        try:
            for pattern, cmd in commands:
                # A deadline shorter than the command's timeout cuts it: the error says so.
                limit = timeout
                if self._deadline is not None:
                    self._deadline.check()
                    limit = self._deadline.limit(timeout)
                lines = self._runner.run_lines(cmd, cwd, limit, capture_path=capture_path)
                try:
                    exit_codes.append((yield from self._records(lines, entry, pattern, transforms)))
                except CollectionError as exc:
                    cut = self._deadline is not None and self._deadline.remaining() == 0
                    if cut and limit < timeout and self._deadline is not None:
                        raise self._deadline.exceeded() from exc
                    raise
        finally:
            if captures is not None:
                # Finalize even on mid-iteration abandonment: preserves partial captures
                # for audit.
                captures.finalize_stream(
                    entry.name,
                    source=source.name if source else None,
                    kind="cli",
                    request={
                        "commands": [cmd for _pattern, cmd in commands],
                        "cwd": str(cwd) if cwd else None,
                        "exit_codes": exit_codes,
                    },
                    status=exit_codes[-1] if exit_codes else None,
                    content_type="text/plain",
                )

    def _records(
        self,
        lines: Generator[str, None, int],
        entry: CollectorEntry,
        pattern: str | None,
        transforms: Mapping[str, Transform],
    ) -> Generator[dict[str, Any], None, int]:
        """The records of one command's lines; returns the command's exit code."""
        with contextlib.closing(lines):
            index = 0
            while True:
                try:
                    line = next(lines)
                except StopIteration as finished:
                    exit_code: int = finished.value
                    return exit_code
                index += 1
                record = parse_line(line, index)
                if record is None:
                    continue
                if pattern is not None:
                    record["_pattern"] = pattern
                if entry.normalize:
                    record = apply_normalize(record, entry.normalize, transforms)
                yield record


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


def _commands(entry: CollectorEntry, source: SourceEntry | None) -> list[tuple[str | None, str]]:
    """(pattern, substituted command) for each invocation the entry makes. recon fills
    ``{url}`` and ``{pattern}``; any other ``{...}`` is the command's own (cargo's
    ``--format '{p}'``, a jq object) and is passed as written."""
    settings = _settings(entry)
    if not settings.run:
        return []
    variables = {"url": source.url} if source else {}
    patterns: list[str | None] = list(settings.patterns) if settings.patterns else [None]
    out = []
    for pattern in patterns:
        cmd_vars = {**variables, "pattern": pattern} if pattern is not None else variables
        out.append((pattern, substitute(settings.run, cmd_vars)))
    return out


# --- Registration ---


def _check(entry: CollectorEntry) -> list[Problem]:
    settings = _settings(entry)
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


def _describe(entry: CollectorEntry, source: SourceEntry | None) -> dict[str, Any]:
    # Effects are declared, not inspected: the command may do anything a shell can.
    # The plan shows the exact commands so a reviewer can read them before they run, and
    # where: a local source's directory, or None for recon's working directory.
    return {
        "commands": [cmd for _pattern, cmd in _commands(entry, source)],
        "cwd": source.url if source is not None and source.type == "local" else None,
    }


collector_type = CollectorType(
    create=lambda ctx: CliCollector(ctx.shell, ctx.transforms, ctx.deadline),
    effects=frozenset({"subprocess"}),
    summary="shell command → stdout lines (JSON objects or text) → records",
    settings=CliSettings,
    source_kinds=frozenset({"local"}),
    # A command runs in a local source's directory; any other source would leave it in
    # recon's working directory.
    accepts_kinds=frozenset({"local"}),
    normalizes=True,
    check=_check,
    describe=_describe,
)

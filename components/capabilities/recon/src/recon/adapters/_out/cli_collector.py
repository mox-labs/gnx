"""CliCollector — runs shell commands, yields records line-by-line.

Uses subprocess.Popen with a streaming stdout pipe so large outputs (e.g.
`rg --json` across a huge tree) don't materialize in memory before parsing.
Each stdout line is parsed and yielded as a record; memory usage is O(1) in
the number of records produced.

Registered as the built-in collector type ``cli`` (entry-point group ``recon.collectors``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from recon.adapters._out.parsing import parse_line
from recon.application.transforms import BUILTIN_TRANSFORMS, apply_normalize
from recon.domain.collector import CollectorType
from recon.domain.exceptions import CollectionError, Problem
from recon.domain.substitution import substitute

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from recon.domain.capture import CaptureLog
    from recon.domain.collector import Transform
    from recon.domain.models import CollectorEntry, SourceEntry
    from recon.domain.shell import ShellRunner


class PopenRunner:
    """Default ShellRunner — Popen + iterate stdout lines, optional tee."""

    def run_lines(
        self,
        cmd: str,
        cwd: Path | None,
        timeout: float,
        *,
        capture_path: Path | None = None,
        capture_exit: list[int] | None = None,
    ) -> Iterator[str]:
        import os
        import signal

        # shell=True is intentional — configs are trusted local input.
        proc = subprocess.Popen(  # noqa: S602
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            # Start the child in its own process group so timeout kills the
            # whole tree, not just the shell.
            preexec_fn=os.setsid if os.name == "posix" else None,
        )

        tee_file = None
        if capture_path is not None:
            # Append mode: if called twice (e.g., patterns fan-out across one
            # entry), concatenate into one captured body.
            capture_path.parent.mkdir(parents=True, exist_ok=True)
            tee_file = open(capture_path, "a")  # noqa: SIM115

        try:
            assert proc.stdout is not None
            # Iterate stdout as lines flow. Pull-based backpressure applies;
            # tee writes happen at the same rate as consumer reads.
            for raw_line in proc.stdout:
                if tee_file is not None:
                    tee_file.write(raw_line)
                yield raw_line.rstrip("\n")

            try:
                _stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                if os.name == "posix":
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                else:
                    proc.kill()
                proc.wait()
                msg = f"Command timed out after {timeout}s: {cmd}"
                raise CollectionError(msg) from exc

            if capture_exit is not None:
                capture_exit.append(proc.returncode)

            if proc.returncode not in (0, 1):
                err = (stderr or "").strip()
                detail = f"\n{err}" if err else ""
                msg = f"Command failed (exit {proc.returncode}): {cmd}{detail}"
                raise CollectionError(msg)
        finally:
            if tee_file is not None:
                try:
                    tee_file.flush()
                    tee_file.close()
                except Exception:  # noqa: BLE001
                    pass
            # Guarantee cleanup on consumer abandonment (GeneratorExit).
            try:
                if proc.poll() is None:
                    if os.name == "posix":
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    else:
                        proc.kill()
                    proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass


class CliCollector:
    """Runs shell commands, yields parsed stdout records as they stream."""

    def __init__(
        self,
        runner: ShellRunner | None = None,
        transforms: Mapping[str, Transform] = BUILTIN_TRANSFORMS,
    ) -> None:
        self._runner = runner if runner is not None else PopenRunner()
        self._transforms = transforms

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
    ) -> Iterator[dict[str, Any]]:
        if not entry.run:
            msg = f"Collector '{entry.name}' has type=cli but no 'run' field"
            raise CollectionError(msg, kind="config")

        cwd = _cwd(source)
        timeout = source.timeout if source else 300.0
        commands = _commands(entry, source)

        # Raw capture: tee every invocation's stdout into one body per collector entry
        # (patterns concatenate). Path is None when the mission does not preserve raw.
        capture_path = captures.stream_path(entry.name) if captures is not None else None
        exit_codes: list[int] = []

        try:
            for pattern, cmd in commands:
                index = 0
                for line in self._runner.run_lines(
                    cmd,
                    cwd,
                    timeout,
                    capture_path=capture_path,
                    capture_exit=exit_codes,
                ):
                    index += 1
                    record = parse_line(line, index)
                    if record is None:
                        continue
                    if pattern is not None:
                        record["_pattern"] = pattern
                    if entry.normalize:
                        record = apply_normalize(record, entry.normalize, self._transforms)
                    yield record
        finally:
            if captures is not None:
                # Finalize even on mid-iteration abandonment — preserves partial
                # captures for audit.
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


def _cwd(source: SourceEntry | None) -> Path | None:
    if source is None:
        return None
    path = Path(source.url)
    return path if path.is_dir() else None


def _commands(entry: CollectorEntry, source: SourceEntry | None) -> list[tuple[str | None, str]]:
    """(pattern, substituted command) for each invocation the entry makes."""
    if not entry.run:
        return []
    variables = {"url": source.url} if source else {}
    patterns: list[str | None] = list(entry.patterns) if entry.patterns else [None]
    out = []
    for pattern in patterns:
        cmd_vars = {**variables, "pattern": pattern} if pattern is not None else variables
        out.append((pattern, substitute(entry.run, cmd_vars)))
    return out


# --- Registration ---


def _check(entry: CollectorEntry) -> list[Problem]:
    if not entry.run:
        return [Problem("run", "a cli collector needs run: (the shell command)")]
    if "{pattern}" in entry.run and not entry.patterns:
        return [Problem("patterns", "run: uses {pattern} but no patterns: are listed")]
    return []


def _describe(entry: CollectorEntry, source: SourceEntry | None) -> dict[str, Any]:
    # Effects are declared, not inspected: the command may do anything a shell can.
    # The plan shows the exact commands so a reviewer can read them before they run.
    return {
        "commands": [cmd for _pattern, cmd in _commands(entry, source)],
        "cwd": source.url if source is not None and source.type == "local" else None,
    }


collector_type = CollectorType(
    create=lambda ctx: CliCollector(transforms=ctx.transforms),
    effects=frozenset({"subprocess"}),
    summary="shell command → stdout lines (JSON objects or text) → records",
    check=_check,
    describe=_describe,
)

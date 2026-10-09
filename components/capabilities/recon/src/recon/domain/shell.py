"""ShellRunner — the port the cli collector runs commands through."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


class ShellRunner(Protocol):
    """Given a command, yield stdout lines as they arrive.

    Implementations own the subprocess lifecycle. Timeout raises
    CollectionError; non-zero exit codes (other than 0/1) raise too.

    When `capture_path` is provided, each stdout line is also written to
    that file as it flows — a streaming tee. Memory stays O(1).
    `capture_exit` is filled with the process exit code at the end (for
    metadata capture by the caller).
    """

    def run_lines(
        self,
        cmd: str,
        cwd: Path | None,
        timeout: float,
        *,
        capture_path: Path | None = None,
        capture_exit: list[int] | None = None,
    ) -> Iterator[str]: ...

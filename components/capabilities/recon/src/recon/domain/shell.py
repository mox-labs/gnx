"""ShellRunner: the port the cli collector runs commands through."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


class ShellRunner(Protocol):
    def run_lines(
        self,
        cmd: str,
        cwd: Path | None,
        timeout: float,
        *,
        capture_path: Path | None = None,
    ) -> Generator[str, None, int]:
        """Yield the command's stdout lines as they arrive; return its exit code.

        Exit 0 succeeds, and so does exit 1 with nothing on stderr (grep's and rg's "no
        match"). Any other exit, or exiting past ``timeout`` seconds while still printing,
        raises CollectionError with the end of stderr; the command and everything it
        started are killed then, and when the generator is closed early. With
        ``capture_path``, each line is also appended to that file as it flows.
        """
        ...

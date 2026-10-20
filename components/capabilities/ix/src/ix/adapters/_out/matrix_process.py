"""The launcher the run path uses: matrix's process groups.

A child is started in its own process group and ended by ending the group, so a program that
forks leaves nothing behind. Ending is SIGTERM, then SIGKILL once the grace has passed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from matrix import process as groups

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Sequence


class MatrixLauncher:
    """:class:`~ix.adapters._out.processes.Launcher` over ``matrix.process``."""

    async def start(self, argv: Sequence[str], **options: Any) -> asyncio.subprocess.Process:
        return await groups.start(argv, **options)

    async def stop(self, process: asyncio.subprocess.Process, *, grace_s: float = 5.0) -> None:
        await groups.kill_group(process, grace_s=grace_s)

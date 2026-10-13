"""Child processes that cannot outlive the run that started them.

A component that shells out owns what it starts. :func:`spawn` makes the common case carry
itself: the child starts in its own process group, and killing that group is deferred with the
run (``ctx.defer``), so it happens however the run ends: completed, failed, cancelled or
signalled. The group, not the process: a shell or a tool that forks leaves grandchildren a
plain ``kill`` would miss.

The kill is polite first (SIGTERM to the group), then final (SIGKILL) once ``grace_s`` has
passed with the group still alive.

What this does not cover:

* a child that moves itself to another group or session (a daemon) has left the group and is
  not reached;
* nothing in-process survives SIGKILL of the run itself, so a component that provisions
  something expensive keeps its own record of it;
* the group is addressed by the child's pid. Once the child has exited *and* its group is
  empty, the operating system may hand that number to an unrelated process group. The window
  is the rest of the run; a component that waits for its child long before the run ends can
  close it by calling :func:`kill_group` itself at that point.
"""

from __future__ import annotations

import asyncio
import os
import signal
import weakref
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from matrix.domain.component import RunContext

#: How often the group is checked while waiting for it to exit after SIGTERM.
_POLL_S = 0.02
#: Processes whose group was killed already: a second call must not signal that number again.
_SWEPT: weakref.WeakSet[asyncio.subprocess.Process] = weakref.WeakSet()


async def spawn(
    ctx: RunContext,
    argv: Sequence[str],
    *,
    grace_s: float = 5.0,
    **options: Any,
) -> asyncio.subprocess.Process:
    """Start ``argv`` in its own process group and defer killing that group with the run.

    ``options`` go to :func:`asyncio.create_subprocess_exec` (``stdin``, ``stdout``, ``cwd``,
    ``env``, ...). The process is returned for the component to talk to and wait on; ending
    it early is fine, the deferred kill then finds nothing left to do.
    """
    starting = asyncio.ensure_future(
        asyncio.create_subprocess_exec(*argv, start_new_session=True, **options)
    )
    try:
        process = await asyncio.shield(starting)
    except asyncio.CancelledError:
        # Cancelled while the child was being started: it may exist already. Finish starting
        # it so that it can be killed, then let the cancellation through.
        process = await starting
        await kill_group(process, grace_s=0)
        raise
    ctx.defer(lambda: kill_group(process, grace_s=grace_s))
    return process


async def kill_group(process: asyncio.subprocess.Process, *, grace_s: float = 5.0) -> None:
    """End ``process`` and everything in its group: SIGTERM, then SIGKILL after ``grace_s``.

    Returns once the process has been reaped. Only the first call for a process signals.
    """
    group = process.pid
    if process in _SWEPT:
        return
    _SWEPT.add(process)
    if not hasattr(os, "killpg"):  # no process groups on this platform: the process alone
        if process.returncode is None:
            process.kill()
        await process.wait()
        return
    if _signal(group, signal.SIGTERM):
        loop = asyncio.get_running_loop()
        until = loop.time() + grace_s
        while _signal(group, 0):
            if loop.time() >= until:
                _signal(group, signal.SIGKILL)
                break
            await asyncio.sleep(_POLL_S)
    await process.wait()


def _signal(group: int, sig: int) -> bool:
    """Send ``sig`` to the process group; False when the group no longer exists."""
    try:
        os.killpg(group, sig)
    except (ProcessLookupError, PermissionError):
        # PermissionError: macOS reports a group holding only unreaped zombies this way.
        return False
    return True

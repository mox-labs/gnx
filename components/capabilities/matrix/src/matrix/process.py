"""Child processes that cannot outlive the run that started them.

:func:`spawn` starts a child in its own process group and defers killing that group with the
run, so it happens however the run ends. The group, not the process: a tool that forks
leaves grandchildren a plain ``kill`` would miss. A component that starts a child per
repetition of its work uses :func:`start` and :func:`kill_group` instead, so the run's
releases do not grow with the repetitions. A kill is SIGTERM to the group, then SIGKILL once
``grace_s`` has passed.

Not covered: a child that leaves its group (a daemon); anything after SIGKILL of the run
itself; and, once a child has exited and its group is empty, the pid may be reused by an
unrelated group for the rest of the run (call :func:`kill_group` when the child is done to
close that window).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import weakref
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

#: How often the group is checked while waiting for it to exit after SIGTERM.
_POLL_S = 0.02
#: Processes whose group is gone or was sent SIGKILL: a later call must not signal that number
#: again, since the pid may by then belong to another group.
_SWEPT: weakref.WeakSet[asyncio.subprocess.Process] = weakref.WeakSet()


class Deferring(Protocol):
    """What :func:`spawn` asks of its first argument: somewhere to defer a release. A
    :class:`~matrix.domain.component.RunContext` is one; so is anything else with ``defer``."""

    def defer(self, release: Callable[[], Awaitable[None] | None], /) -> None: ...


async def start(argv: Sequence[str], **options: Any) -> asyncio.subprocess.Process:
    """Start ``argv`` in its own process group. Ending it is the caller's: :func:`kill_group`.

    ``options`` go to :func:`asyncio.create_subprocess_exec` (``stdin``, ``stdout``, ``cwd``,
    ``env``, ...). If the caller is cancelled while the child is being started, the child is
    killed before the cancellation goes on.
    """
    starting = asyncio.ensure_future(
        asyncio.create_subprocess_exec(*argv, start_new_session=True, **options)
    )
    try:
        return await asyncio.shield(starting)
    except asyncio.CancelledError:
        # Cancelled while the child was being started: it may exist already. Finish starting
        # it so that it can be killed, then let the cancellation through. A start that failed
        # left nothing to kill, and its error does not replace the cancellation.
        with contextlib.suppress(Exception):
            process = await starting
            await kill_group(process, grace_s=0)
        raise


async def spawn(
    ctx: Deferring,
    argv: Sequence[str],
    *,
    grace_s: float = 5.0,
    **options: Any,
) -> asyncio.subprocess.Process:
    """Start ``argv`` in its own process group and defer killing that group with the run.

    ``ctx`` is the component's context, or anything with its ``defer``. ``options`` go to
    :func:`asyncio.create_subprocess_exec`. The process is returned for the component to talk
    to and wait on; ending it early is fine, the deferred kill then finds nothing left to do.
    """
    process = await start(argv, **options)
    ctx.defer(lambda: kill_group(process, grace_s=grace_s))
    return process


async def kill_group(process: asyncio.subprocess.Process, *, grace_s: float = 5.0) -> None:
    """End ``process`` and everything in its group: SIGTERM, then SIGKILL after ``grace_s``.

    Returns once the process has been reaped. A call cancelled during the grace sends
    SIGKILL before the cancellation goes on. Once a call has seen the group gone or sent
    SIGKILL, later calls for the process do nothing.
    """
    if process in _SWEPT:
        return
    if not hasattr(os, "killpg"):  # no process groups on this platform: the process alone
        if process.returncode is None:
            process.kill()
        _SWEPT.add(process)
        await process.wait()
        return
    group = process.pid
    try:
        if _signal(group, signal.SIGTERM):
            loop = asyncio.get_running_loop()
            until = loop.time() + grace_s
            while _signal(group, 0):
                if loop.time() >= until:
                    _signal(group, signal.SIGKILL)
                    break
                await asyncio.sleep(_POLL_S)
    except asyncio.CancelledError:
        _signal(group, signal.SIGKILL)
        raise
    finally:
        _SWEPT.add(process)
    await process.wait()


def _signal(group: int, sig: int) -> bool:
    """Send ``sig`` to the process group; False when the group no longer exists."""
    try:
        os.killpg(group, sig)
    except (ProcessLookupError, PermissionError):
        # PermissionError: macOS reports a group holding only unreaped zombies this way.
        return False
    return True

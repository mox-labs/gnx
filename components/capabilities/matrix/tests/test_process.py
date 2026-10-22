"""A child process started through ``matrix.process.spawn`` cannot outlive its run.

The child here starts a grandchild and reports the grandchild's pid, so a kill that reached
only the child would leave something running. Whether a pid is gone is the operating
system's to settle, so those checks wait on it, bounded; nothing else here waits on time.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from contextlib import suppress
from typing import Any

import pytest

from matrix import Executor, Inputs, Limits, RunCancelled, RunContext, RunError, wire
from matrix.process import kill_group, spawn, start

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")

_GRANDCHILD = "import time; time.sleep(600)"
_STUBBORN_GRANDCHILD = (
    "import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "print(os.getpid(), flush=True); time.sleep(600)"
)
#: Starts a grandchild, reports its pid on stdout, and waits.
_CHILD = (
    "import subprocess, sys, time; "
    f"p = subprocess.Popen([sys.executable, '-c', {_GRANDCHILD!r}]); "
    "print(p.pid, flush=True); time.sleep(600)"
)
#: Starts a grandchild that ignores SIGTERM and reports its own pid once it does.
_CHILD_OF_STUBBORN = (
    "import subprocess, sys; "
    f"subprocess.Popen([sys.executable, '-c', {_STUBBORN_GRANDCHILD!r}]).wait()"
)


class Shell:
    """Spawns a child, learns the grandchild's pid, then does what ``then`` says."""

    requires: tuple[str, ...] = ()
    provides = ("acme.v1.text",)

    def __init__(self, then: str, child: str = _CHILD, grace_s: float = 5.0) -> None:
        self._then, self._child, self._grace_s = then, child, grace_s
        self.pids: list[int] = []
        self.ready = asyncio.Event()

    async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
        process = await spawn(
            ctx,
            [sys.executable, "-c", self._child],
            grace_s=self._grace_s,
            stdout=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        self.pids = [process.pid, int(await process.stdout.readline())]
        self.ready.set()
        if self._then == "raise":
            raise RuntimeError("kaput")
        if self._then == "hang":
            await asyncio.Event().wait()
        return {"acme.v1.text": "done"}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _gone(pids: list[int]) -> bool:
    """Whether every pid disappears; the grandchild is reaped by init, not by us."""
    try:
        async with asyncio.timeout(30):
            while any(_alive(pid) for pid in pids):
                await asyncio.sleep(0.01)
        return True
    except TimeoutError:
        return False
    finally:
        for pid in pids:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def _flow(component: Shell) -> Any:
    return wire({"shell": component})


async def test_the_whole_group_is_killed_when_the_run_completes() -> None:
    shell = Shell("return")
    run = await Executor().run(_flow(shell))
    assert [r.document() for r in run.releases] == [
        {"component": "shell", "status": "released", "error": None}
    ]
    assert await _gone(shell.pids)


async def test_the_whole_group_is_killed_when_the_component_fails() -> None:
    shell = Shell("raise")
    with pytest.raises(RunError):
        await Executor().run(_flow(shell))
    assert await _gone(shell.pids)


async def test_the_whole_group_is_killed_when_the_run_is_cancelled() -> None:
    shell = Shell("hang")
    task = asyncio.ensure_future(Executor().run(_flow(shell), limits=Limits(grace_s=0)))
    await shell.ready.wait()
    task.cancel()
    with pytest.raises(RunCancelled) as e:
        await task
    assert [r.document() for r in e.value.run.releases] == [
        {"component": "shell", "status": "released", "error": None}
    ]
    assert await _gone(shell.pids)


async def test_a_child_that_ignores_sigterm_is_killed_after_the_grace() -> None:
    shell = Shell("return", child=_CHILD_OF_STUBBORN, grace_s=0.05)
    run = await Executor().run(_flow(shell))
    assert run.releases[0].status == "released"
    assert await _gone(shell.pids)


class Repeats:
    """Starts and ends one child per piece of work, deferring nothing per child."""

    requires: tuple[str, ...] = ()
    provides = ("acme.v1.text",)

    def __init__(self, times: int) -> None:
        self._times = times
        self.pids: list[int] = []

    async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
        for _ in range(self._times):
            process = await start([sys.executable, "-c", _CHILD], stdout=asyncio.subprocess.PIPE)
            try:
                assert process.stdout is not None
                self.pids += [process.pid, int(await process.stdout.readline())]
            finally:
                await kill_group(process)
        return {"acme.v1.text": "done"}


async def test_start_and_kill_group_end_each_child_without_deferring_anything() -> None:
    repeats = Repeats(3)
    run = await Executor().run(wire({"repeats": repeats}))
    assert run.releases == []
    assert len(repeats.pids) == 6 and await _gone(repeats.pids)


async def test_spawn_takes_anything_that_can_defer() -> None:
    """A caller that holds only a narrow view of the context can still spawn."""

    class Scope:
        def __init__(self) -> None:
            self.releases: list[Any] = []

        def defer(self, release: Any, /) -> None:
            self.releases.append(release)

    scope = Scope()
    process = await spawn(scope, [sys.executable, "-c", _GRANDCHILD])
    assert process.returncode is None and len(scope.releases) == 1
    await scope.releases[0]()
    assert process.returncode is not None


async def test_a_child_being_started_when_the_caller_is_cancelled_does_not_survive() -> None:
    task = asyncio.ensure_future(start([sys.executable, "-c", _GRANDCHILD]))
    await asyncio.sleep(0)  # the subprocess is being created
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_cancellation_during_a_start_that_fails_is_still_a_cancellation() -> None:
    """The child could not be started (no such program) while the caller was being cancelled:
    the caller is told it was cancelled, not handed the start's error in its place."""
    starting = asyncio.ensure_future(start(["/nonexistent/matrix-test-program"]))
    await asyncio.sleep(0)  # the start is under way
    assert starting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await starting


async def test_a_kill_cancelled_during_its_grace_still_kills_the_group() -> None:
    """Cancelled while waiting out the grace, the call sends SIGKILL before it gives up, so
    a child that ignores SIGTERM does not survive; a later call then has nothing to do."""
    process = await start(
        [sys.executable, "-c", _STUBBORN_GRANDCHILD], stdout=asyncio.subprocess.PIPE
    )
    assert process.stdout is not None
    pid = int(await process.stdout.readline())
    killing = asyncio.ensure_future(kill_group(process, grace_s=600))
    await asyncio.sleep(0.1)  # SIGTERM sent and ignored; the call is waiting out the grace
    killing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await killing
    assert await _gone([pid])
    await kill_group(process, grace_s=600)
    assert process.returncode is not None

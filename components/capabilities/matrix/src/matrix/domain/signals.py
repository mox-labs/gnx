"""Termination signals reach a run as cancellation, so a signalled run still cleans up.

Left alone, SIGTERM ends the process where it stands: no ``finally`` block runs, no deferred
release is called, and a child process a component started outlives the run. While a run is in
flight the executor therefore routes SIGTERM (and SIGINT, when nothing else handles it) to
``task.cancel()`` on the task awaiting the run. From there it is the one stop path every
cancellation takes.

Deliberately narrow:

* only a signal still at its default disposition is taken over; a host that installed its own
  handler keeps it, and is then the one responsible for cancelling the run;
* only on the main thread of a loop that supports signal handlers (not Windows);
* the first signal cancels and hands the signal back, so a second one does what it always did.
"""

from __future__ import annotations

import asyncio
import signal
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass
class _Watch:
    tasks: list[asyncio.Task[Any]] = field(default_factory=list)
    signals: list[signal.Signals] = field(default_factory=list)


_WATCHES: dict[asyncio.AbstractEventLoop, _Watch] = {}


def _unhandled() -> list[signal.Signals]:
    """The termination signals nobody has claimed in this process."""
    found: list[signal.Signals] = []
    if signal.getsignal(signal.SIGTERM) is signal.SIG_DFL:
        found.append(signal.SIGTERM)
    if signal.getsignal(signal.SIGINT) is signal.default_int_handler:
        found.append(signal.SIGINT)
    return found


def _hand_back(loop: asyncio.AbstractEventLoop, watch: _Watch) -> None:
    for sig in watch.signals:
        loop.remove_signal_handler(sig)
    watch.signals.clear()


def _fire(loop: asyncio.AbstractEventLoop) -> None:
    watch = _WATCHES.get(loop)
    if watch is None:
        return
    _hand_back(loop, watch)
    for task in list(watch.tasks):
        task.cancel()


@contextmanager
def cancel_on_signals(enabled: bool = True) -> Iterator[None]:
    """While inside, a termination signal cancels the current task."""
    task = asyncio.current_task()
    if not enabled or task is None or threading.current_thread() is not threading.main_thread():
        yield
        return
    loop = asyncio.get_running_loop()
    watch = _WATCHES.get(loop)
    if watch is None:
        watch = _WATCHES[loop] = _Watch()
        for sig in _unhandled():
            try:
                loop.add_signal_handler(sig, _fire, loop)
            except (NotImplementedError, RuntimeError, ValueError):
                continue  # a loop without signal support: cancellation stays the caller's
            watch.signals.append(sig)
    watch.tasks.append(task)
    try:
        yield
    finally:
        watch.tasks.remove(task)
        if not watch.tasks:
            _hand_back(loop, watch)
            del _WATCHES[loop]

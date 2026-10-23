"""Deadlines: the wall time a survey, and each of its tables, may take.

A mission may bound its whole survey (``deadline:`` at the top) and each table of a collector
(``deadline:`` on the collector), in seconds. One :class:`Deadline` serves a survey: the
survey opens a bound around its tables and one around each table, and whoever waits or works
inside asks it what is left, so the nearest end holds. The requester bounds each send and
each wait by it, the ``cli`` collector its command's timeout, and the survey checks it between
records. What runs past it fails with :class:`DeadlineExceededError`, ``transient``: a slow
source may be quicker later. Time comes from the clock port, so a test clock decides it.
Single-threaded, as a survey is.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from recon.domain.errors import CollectionError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from recon.domain.clock import Clock


class DeadlineExceededError(CollectionError):
    """Work that ran, or would wait, past a deadline the mission set."""


class Deadline:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        #: Each open bound: when it ends on the clock's monotonic time, what it is, the fix.
        self._ends: list[tuple[float, str, str]] = []

    @contextmanager
    def bound(self, seconds: float | None, what: str, fix: str) -> Iterator[None]:
        """Within the block, nothing may run past ``seconds`` from now; None bounds nothing.
        ``what`` names the bound in an error (``table papers's deadline``); ``fix`` says how
        to raise it."""
        if seconds is None:
            yield
            return
        self._ends.append((self._clock.monotonic() + seconds, what, fix))
        try:
            yield
        finally:
            self._ends.pop()

    def remaining(self) -> float | None:
        """Seconds left to the nearest open bound, never below 0; None with no bound open."""
        if not self._ends:
            return None
        return max(0.0, min(end for end, _what, _fix in self._ends) - self._clock.monotonic())

    def check(self) -> None:
        """Raise once the nearest bound has passed."""
        if self._ends and self.remaining() == 0:
            raise self.exceeded()

    def allow_wait(self, seconds: float) -> None:
        """Raise when a wait of ``seconds`` would end past the nearest bound: rather than
        sleep into the deadline, fail now."""
        left = self.remaining()
        if left is not None and seconds > left:
            _end, what, fix = min(self._ends)
            message = f"a wait of {seconds:g}s would end past {what}"
            raise DeadlineExceededError(message, kind="transient", fix=fix)

    def limit(self, seconds: float) -> float:
        """``seconds``, or what is left when that is less."""
        left = self.remaining()
        return seconds if left is None else min(seconds, left)

    def exceeded(self) -> DeadlineExceededError:
        """The error for the nearest bound, which has passed."""
        _end, what, fix = min(self._ends)
        return DeadlineExceededError(f"{what} passed", kind="transient", fix=fix)

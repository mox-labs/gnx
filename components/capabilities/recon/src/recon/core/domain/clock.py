"""The clock port: the one way the time of day and the passing of time reach recon.

An archive's name, its ``meta.yaml`` times, each table's ``seconds``, a capture's
``captured_at``, every rate-limit wait and every wait before a retry read this port. Nothing
else in recon reads a clock, so a survey run against a fixed clock writes the same archive
twice.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from datetime import datetime

#: The longest wait, in seconds, this platform's timers take: a socket's timeout, a sleep and
#: a thread's wait each refuse a longer one (``OverflowError``). A source's ``timeout`` and
#: its rate limit's longest wait for a token are held to it when the mission is planned.
#: Reading this platform constant in the domain is accepted (owner ruling 2026-10-22).
LONGEST_WAIT: Final = threading.TIMEOUT_MAX


class Clock(Protocol):
    def now(self) -> datetime:
        """The current time, timezone-aware, in UTC."""
        ...

    def monotonic(self) -> float:
        """Seconds on a clock that never goes back; only differences mean anything."""
        ...

    def sleep(self, seconds: float) -> None: ...

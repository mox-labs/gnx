"""The clock port: the one way the time of day and the passing of time reach recon.

An archive's name, its ``meta.yaml`` times, each table's ``seconds``, a capture's
``captured_at`` and every rate-limit wait read this port. Nothing else in recon reads a
clock, so a survey run against a fixed clock writes the same archive twice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime


class Clock(Protocol):
    def now(self) -> datetime:
        """The current time, timezone-aware, in UTC."""
        ...

    def monotonic(self) -> float:
        """Seconds on a clock that never goes back; only differences mean anything."""
        ...

    def sleep(self, seconds: float) -> None: ...

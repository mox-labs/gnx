"""Schedules: a mission that runs itself, by the operating system's own scheduler.

A mission declares ``schedule: {every, missed}`` and recon projects it onto the scheduler the
host has (launchd, systemd's user timers, or cron): there is no recon daemon. Each tick the
scheduler starts is one survey of the mission (``recon schedule tick``).

``every`` is bare seconds, as every duration in a recon config is. It is a whole number of
minutes that divides a day, so the ticks fall on one daily grid of wall-clock times that every
scheduler keeps alike: launchd and cron count in minutes, and a systemd timer that runs a
missed tick (``Persistent=true``) is a calendar, which repeats daily. ``missed`` says what a
tick missed while the host was asleep or off becomes: ``once``, the missed ticks coalesced
into one run when the host is back (the default), or ``skip``, none. Missed ticks are never
caught up one by one: that would be a burst of surveys against each source's rate limit.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: What a tick the host missed (asleep, off) becomes: one run once it is back, or none.
Missed = Literal["once", "skip"]

#: The shortest ``every``, in seconds.
MIN_EVERY_S = 60

#: The seconds in a day: the grid every schedule's ticks fall on repeats daily.
DAY_S = 86400

#: Every ``every`` a schedule may have, in seconds: the whole minutes that divide a day.
EVERY_ALLOWED: tuple[int, ...] = tuple(m * 60 for m in range(1, 1441) if 1440 % m == 0)

#: A scheduled mission's ``deadline:``, unless it sets one, as a share of ``every``: a tick
#: ends before the next one is due, so two ticks do not overlap (ruling S2).
DEADLINE_SHARE = 0.8


def nearest_every(every: int) -> tuple[int, ...]:
    """The allowed ``every`` nearest ``every``: the one below it and the one above, or the one
    at the end of the range it is past."""
    below = [e for e in EVERY_ALLOWED if e < every]
    above = [e for e in EVERY_ALLOWED if e > every]
    return tuple(([below[-1]] if below else []) + ([above[0]] if above else []))


def every_fix(every: int) -> str:
    """The fix for an ``every`` no scheduler keeps: the nearest that one does."""
    nearest = " or ".join(f"every: {e}" for e in nearest_every(every))
    return f"set {nearest}: whole minutes that divide a day (86400 s), in seconds"


class ScheduleSpec(BaseModel):
    """``schedule:``: run the mission every ``every`` seconds, by the operating system's
    scheduler, a tick missed while the host was away run ``once`` when it is back or
    ``skip``-ped. ``every`` has no default: how often is the one thing only the mission's
    author knows, and no ``schedule:`` at all is the default."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    every: int = Field(ge=MIN_EVERY_S)
    missed: Missed = "once"

    @field_validator("every")
    @classmethod
    def _on_a_daily_grid(cls, value: int) -> int:
        if value not in EVERY_ALLOWED:
            raise ValueError(
                f"{value} s is not whole minutes that divide a day: launchd and cron count in "
                "minutes, and a schedule's ticks repeat daily"
            )
        return value

    @property
    def deadline(self) -> float:
        """The ``deadline:`` a mission scheduled so has unless it sets one."""
        return DEADLINE_SHARE * self.every

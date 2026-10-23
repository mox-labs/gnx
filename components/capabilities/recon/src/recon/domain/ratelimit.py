"""Rate limiting: one token bucket per catalog source, over the clock port.

The limiter is shared by every collector in a survey, so an ``api`` and a ``web`` collector
hitting the same source draw from one bucket. Single-threaded: a survey runs its tables one
at a time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from recon.domain.clock import Clock
    from recon.domain.deadline import Deadline
    from recon.domain.models import SourceEntry


def wait_for_token(tokens: float, rps: float) -> float:
    """The seconds until a bucket holding ``tokens`` (fewer than one) at ``rps`` holds one.
    The longest is an empty bucket's, ``1 / rps``: the plan holds it to what the clock can
    sleep (:data:`~recon.domain.clock.LONGEST_WAIT`)."""
    return (1.0 - tokens) / rps


class TokenBucket:
    def __init__(self, rps: float, burst: int, clock: Clock) -> None:
        self._rps = rps
        self._burst = burst
        self._clock = clock
        self._tokens = float(burst)
        self._last = clock.monotonic()

    def acquire(self, deadline: Deadline | None = None) -> None:
        """Return once a token is available, sleeping on the clock until then; a wait that
        would end past ``deadline`` raises instead."""
        while True:
            now = self._clock.monotonic()
            self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rps)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            wait = wait_for_token(self._tokens, self._rps)
            if deadline is not None:
                deadline.allow_wait(wait)
            self._clock.sleep(wait)


class RateLimiter:
    """One bucket per source name, created from the source's ``rate_limit`` on first use."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._buckets: dict[str, TokenBucket] = {}

    def acquire(self, source: SourceEntry, deadline: Deadline | None = None) -> None:
        bucket = self._buckets.get(source.name)
        if bucket is None:
            limit = source.rate_limit
            bucket = TokenBucket(limit.rps, limit.burst, self._clock)
            self._buckets[source.name] = bucket
        bucket.acquire(deadline)

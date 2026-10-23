"""The sensors ix can read: ``timing`` from what a load probe recorded, and ``memory`` by
sampling the subject while the probe runs."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any

from ix.domain.kind import seconds

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping
    from contextlib import AbstractAsyncContextManager

    from ix.domain.ports import Measured
    from ix.domain.run import Interaction, Target

MEMORY = "ix.v1.sensor.memory"

#: Time between memory samples when the experiment does not say.
INTERVAL_S = 0.2


class TimingSensor:
    """Latency, time to first byte, throughput and error rate, from the ``load`` facet a
    load probe leaves. It watches nothing: the load tool did the timing. The error rate
    counts a request that failed and an arrival the load dropped, of every request sent
    and every arrival dropped."""

    def watch(
        self, target: Target, options: Mapping[str, Any]
    ) -> AbstractAsyncContextManager[dict[str, Any]] | None:
        return None

    async def sense(
        self, interaction: Interaction, *, truth: Mapping[str, Any], options: Mapping[str, Any]
    ) -> Mapping[str, Measured]:
        load = interaction.observations["load"]
        return {
            "latency": dict(load["latency_ms"]),
            "ttfb": dict(load["ttfb_ms"]),
            "throughput": float(load["throughput"]),
            "error_rate": {
                "events": int(load["failed"]) + int(load.get("dropped", 0)),
                "units": int(load["sent"]) + int(load.get("dropped", 0)),
            },
        }


async def group_rss_bytes(group: int) -> int | None:
    """The resident memory, in bytes, of every process in process group ``group`` now, as
    ``ps`` reports it. None when ``ps`` could not be asked."""
    try:
        process = await asyncio.create_subprocess_exec(
            "ps",
            "-axo",
            "pgid=,rss=",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None
    try:
        output, _ = await process.communicate()
    except asyncio.CancelledError:
        with suppress(ProcessLookupError):
            process.kill()
        raise
    total = 0
    for line in output.decode("ascii", errors="replace").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == str(group) and parts[1].isdigit():
            total += int(parts[1]) * 1024  # ps reports kibibytes
    return total


class MemorySensor:
    """Peak resident memory of the subject while the probe runs.

    It samples the resident set size of the subject's process group (the process ix
    started and everything it forked) every ``interval`` and keeps the highest total. It is
    a sampled peak: a spike between two samples is missed, and the sampler shares the
    machine with the subject it measures. Option ``interval`` (default 200ms).
    """

    def watch(
        self, target: Target, options: Mapping[str, Any]
    ) -> AbstractAsyncContextManager[dict[str, Any]] | None:
        return self._watch(
            int(target.facets["pid"]), seconds(options.get("interval")) or INTERVAL_S
        )

    @asynccontextmanager
    async def _watch(self, group: int, interval_s: float) -> AsyncIterator[dict[str, Any]]:
        seen: dict[str, Any] = {"peak_bytes": 0, "samples": 0, "interval_s": interval_s}

        async def sample() -> None:
            while True:
                total = await group_rss_bytes(group)
                if total:
                    seen["peak_bytes"] = max(seen["peak_bytes"], total)
                    seen["samples"] += 1
                await asyncio.sleep(interval_s)

        sampler = asyncio.ensure_future(sample())
        try:
            yield seen
        finally:
            sampler.cancel()
            with suppress(asyncio.CancelledError):
                await sampler

    async def sense(
        self, interaction: Interaction, *, truth: Mapping[str, Any], options: Mapping[str, Any]
    ) -> Mapping[str, Measured]:
        seen = interaction.watched[MEMORY]
        if not seen.get("samples"):
            raise RuntimeError("no memory sample was taken while the probe ran")
        return {"memory_peak": float(seen["peak_bytes"])}

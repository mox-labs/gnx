"""NativeEngine — each probe × trial runs as a four-node matrix DAG.

``concurrency`` bounds how many trials run at once (default 1). Readings and trials come
back in probe × trial order however they were scheduled, so a run's records read the same
at any concurrency. Raise it for subjects whose runtime and provider can take parallel
calls; every built-in runtime is safe to run concurrently.

Type URL: ``ix.v1/engine.native``. The default engine; no dependency beyond matrix.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from matrix import Orchestrator
from pydantic import BaseModel, ConfigDict, Field

from ix.adapters._out.components import ProbeNode, SensorNode, SubjectNode, TrialNode
from ix.domain import type_urls
from ix.domain.ports import EngineOutcome

if TYPE_CHECKING:
    from ix.domain.ports import EngineRun
    from ix.domain.types import Probe, Reading, Trial


class NativeEngineConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Trials in flight at once. 1 = strictly one after another.
    concurrency: int = Field(default=1, ge=1)


class NativeEngine:
    name = "native"

    def __init__(self, config: NativeEngineConfig | None = None) -> None:
        self._config = config or NativeEngineConfig()

    @property
    def config(self) -> NativeEngineConfig:
        return self._config

    async def run(self, run: EngineRun) -> EngineOutcome:
        jobs = [(probe, t) for probe in run.probes for t in range(run.trials)]
        gate = asyncio.Semaphore(self._config.concurrency)

        async def one(probe: Probe, trial_index: int) -> tuple[Trial, list[Reading]]:
            async with gate:
                construct = await Orchestrator(
                    [
                        ProbeNode(probe),
                        SubjectNode(run.subject),
                        TrialNode(run.agents, trial_index, run.run_index),
                        SensorNode(run.sensor),
                    ]
                ).run()
            trial, readings = construct[type_urls.TRIAL], construct[type_urls.READINGS]
            if run.on_trial:
                run.on_trial(trial, readings)
            return trial, readings

        results = await asyncio.gather(*(one(p, t) for p, t in jobs))
        return EngineOutcome(
            readings=[r for _, readings in results for r in readings],
            trials=[trial for trial, _ in results],
        )

"""NativeEngine: every trial is one run of a matrix flow, compiled once per repeat.

The flow has one member, a :class:`TrialStep` that builds the subject's agent and puts the
probe to it. Its inputs are the probe and the trial index, so the same compiled flow runs
for every probe × trial; matrix records each run and reports it to its observers (tracing).

``concurrency`` bounds how many trials run at once (default 1). Trials come back in
probe × trial order however they were scheduled.

Type URL: ``ix.v1.engine.native``. The default engine; no dependency beyond matrix.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from matrix import Executor, Flow, Member, compile_flow
from pydantic import BaseModel, ConfigDict, Field

from ix.domain import type_urls
from ix.domain.ports import EngineOutcome
from ix.eval.measure import run_trial

if TYPE_CHECKING:
    from collections.abc import Sequence

    from matrix import Inputs, Observer

    from ix.domain.ports import AgentFactory, EngineRun
    from ix.domain.types import Probe, Subject, Trial


class NativeEngineConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Trials in flight at once. 1 = strictly one after another.
    concurrency: int = Field(default=1, ge=1)


class TrialStep:
    """One trial: the subject's agent, given one probe."""

    requires = {"probe": type_urls.PROBE, "index": type_urls.TRIAL_INDEX}
    provides = {"trial": type_urls.TRIAL}

    def __init__(self, agents: AgentFactory, subject: Subject, run_index: int) -> None:
        self._agents = agents
        self._subject = subject
        self._run_index = run_index

    async def run(self, inputs: Inputs) -> dict[str, Any]:
        probe: Probe = inputs["probe"]
        index: int = inputs["index"]
        agent = self._agents(self._subject, index, self._run_index)
        return {"trial": await run_trial(agent, probe.id, probe.prompt, index)}


class NativeEngine:
    name = "native"

    def __init__(
        self, config: NativeEngineConfig | None = None, *, observers: Sequence[Observer] = ()
    ) -> None:
        self._config = config or NativeEngineConfig()
        self._executor = Executor(observers)

    @property
    def config(self) -> NativeEngineConfig:
        return self._config

    async def run(self, run: EngineRun) -> EngineOutcome:
        flow = compile_flow(
            Flow(
                f"{run.experiment}.trial",
                (
                    Member(
                        "trial",
                        TrialStep(run.agents, run.subject, run.run_index),
                        {"probe": "probe", "index": "index", "trial": "trial"},
                    ),
                ),
                inputs={"probe": type_urls.PROBE, "index": type_urls.TRIAL_INDEX},
            )
        )
        gate = asyncio.Semaphore(self._config.concurrency)

        async def one(probe: Probe, index: int) -> Trial:
            async with gate:
                result = await self._executor.run(flow, {"probe": probe, "index": index})
            trial: Trial = result.construct.last("trial").value
            if run.on_trial:
                run.on_trial(trial)
            return trial

        trials = await asyncio.gather(*(one(p, t) for p in run.probes for t in range(run.trials)))
        return EngineOutcome(trials=list(trials))

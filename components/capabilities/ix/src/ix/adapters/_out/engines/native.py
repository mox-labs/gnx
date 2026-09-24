"""NativeEngine — each probe × trial runs as a four-node matrix DAG.

Type URL: ``ix.v1/engine.native``. The default engine; no dependency beyond matrix.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from matrix import Orchestrator

from ix.adapters._out.components import ProbeNode, SensorNode, SubjectNode, TrialNode
from ix.domain import type_urls
from ix.domain.ports import EngineOutcome

if TYPE_CHECKING:
    from ix.domain.ports import EngineRun
    from ix.domain.types import Reading


class NativeEngine:
    name = "native"

    async def run(self, run: EngineRun) -> EngineOutcome:
        readings: list[Reading] = []
        for probe in run.probes:
            for trial_index in range(run.trials):
                construct = await Orchestrator(
                    [
                        ProbeNode(probe),
                        SubjectNode(run.subject),
                        TrialNode(run.agents, trial_index, run.run_index),
                        SensorNode(run.sensor),
                    ]
                ).run()
                readings.extend(construct[type_urls.READINGS])
        return EngineOutcome(readings=readings)

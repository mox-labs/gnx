"""Measuring one trial — the same rule whichever engine produced it.

An errored trial is a failed reading, not a missing one: a subject that crashes on a probe
has failed that probe. A sensor that raises is also a failed reading, with the error in its
details, so one broken grader cannot silently drop trials from the pass rate.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from ix.domain.types import Reading, Trial

if TYPE_CHECKING:
    from matrix import Agent

    from ix.domain.ports import Sensor

logger = logging.getLogger(__name__)


def measure_trial(sensor: Sensor, trial: Trial) -> list[Reading]:
    if trial.error:
        return [_failed(sensor, trial, f"error: {trial.error}", "subject")]
    try:
        return sensor.measure(trial)
    except Exception as e:
        logger.warning(
            "Sensor %s failed on trial %s/%d: %s", sensor.name, trial.probe_id, trial.trial_index, e
        )
        return [_failed(sensor, trial, f"sensor error: {e}", "sensor")]


def _failed(
    sensor: Sensor, trial: Trial, details: str, fault: Literal["subject", "sensor"]
) -> Reading:
    return Reading(
        sensor_name=sensor.name,
        probe_id=trial.probe_id,
        trial_index=trial.trial_index,
        passed=False,
        score=0.0,
        details=details,
        fault=fault,
    )


async def run_trial(agent: Agent, probe_id: str, prompt: str, trial_index: int) -> Trial:
    """Run one probe through an agent; an exception becomes an errored Trial."""
    try:
        response = await agent.run(prompt)
    except Exception as e:
        logger.warning("Trial failed for probe %s: %s", probe_id, e)
        return Trial(probe_id=probe_id, trial_index=trial_index, error=str(e))
    return Trial(probe_id=probe_id, trial_index=trial_index, response=response)

"""Measuring a trial: one rule, applied by the experiment to every trial, whatever the engine.

* A trial whose session failed is a failed reading, never a missing one. Whose failure it
  was comes from the runtime's classified reason: rate limits, outages, timeouts, credentials
  and runtimes that cannot run the definition are the **harness**'s; anything else (the
  session broke, the model refused) is the **subject**'s.
* A sensor that raises is a failed reading with ``fault="sensor"``, so one broken grader
  cannot silently drop trials from the pass rate.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Literal

from matrix import AgentRuntimeError

from ix.domain.types import Reading, Trial

if TYPE_CHECKING:
    from matrix import Agent

    from ix.domain.ports import Sensor

logger = logging.getLogger(__name__)

#: Reasons that say the subject never had a fair attempt: the runtime port's infrastructure
#: reasons; ``engine`` (the engine lost the trial); ``setup`` (the subject's agent could not
#: be built); and ``unclassified`` (the runtime broke its port by raising something other
#: than a classified error: an adapter bug, not the subject's failure).
HARNESS_REASONS = frozenset(
    {
        "unavailable",
        "rate_limited",
        "timeout",
        "auth",
        "incapable",
        "engine",
        "setup",
        "unclassified",
    }
)


def fault_of(trial: Trial) -> Literal["subject", "harness"]:
    return "harness" if trial.error_reason in HARNESS_REASONS else "subject"


def measure_trial(sensor: Sensor, trial: Trial) -> list[Reading]:
    if trial.error:
        return [_failed(sensor, trial, f"error: {trial.error}", fault_of(trial))]
    try:
        return sensor.measure(trial)
    except Exception as e:
        logger.warning(
            "Sensor %s failed on trial %s/%d: %s", sensor.name, trial.probe_id, trial.trial_index, e
        )
        return [_failed(sensor, trial, f"sensor error: {e}", "sensor")]


def _failed(
    sensor: Sensor,
    trial: Trial,
    details: str,
    fault: Literal["subject", "harness", "sensor"],
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


async def run_trial(
    agent: Agent,
    probe_id: str,
    prompt: str,
    trial_index: int,
    *,
    timeout_s: float | None = None,
) -> Trial:
    """Run one probe through an agent; a failure becomes an errored Trial with its reason.

    A session still running after ``timeout_s`` is cut off as a ``timeout``: a harness fault,
    so one hung session cannot hang the experiment or count against the subject.
    """
    try:
        async with asyncio.timeout(timeout_s):
            response = await agent.run(prompt)
    except TimeoutError:
        logger.warning("Trial timed out for probe %s after %ss", probe_id, timeout_s)
        return Trial(
            probe_id=probe_id,
            trial_index=trial_index,
            error=f"the session did not finish within {timeout_s}s",
            error_reason="timeout",
        )
    except AgentRuntimeError as e:
        logger.warning("Trial failed for probe %s (%s): %s", probe_id, e.reason, e)
        return Trial(
            probe_id=probe_id, trial_index=trial_index, error=str(e), error_reason=e.reason
        )
    except Exception as e:
        logger.warning("Trial failed for probe %s (unclassified): %s", probe_id, e)
        return Trial(
            probe_id=probe_id,
            trial_index=trial_index,
            error=f"{type(e).__name__}: {e}",
            error_reason="unclassified",
        )
    return Trial(probe_id=probe_id, trial_index=trial_index, response=response)

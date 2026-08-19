"""Post-DAG analysis — aggregate readings into results.

Pure functions. The sensor is the grader — passed=True means correct.
Analysis just counts.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import TYPE_CHECKING

from ix.eval.models import ProbeResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from ix.domain.types import Probe, Reading


def aggregate_readings(
    readings: list[Reading],
    probes: dict[str, Probe],
    on_probe_complete: Callable[[ProbeResult], None] | None = None,
) -> list[ProbeResult]:
    """Group readings by probe_id, compute pass rate per probe."""
    by_probe: dict[str, list[Reading]] = defaultdict(list)
    for reading in readings:
        by_probe[reading.probe_id].append(reading)

    probe_results: list[ProbeResult] = []
    for probe_id, probe_readings in by_probe.items():
        # `Reading.score` is optional: `Sensor` is a Protocol, so a sensor outside this
        # package may report only `passed`. The docstring's contract — "the sensor is the
        # grader" — makes the boolean the fallback, and taking it here keeps a third-party
        # sensor from crashing aggregation on `sum(None, ...)`.
        trial_scores = tuple(
            r.score if r.score is not None else (1.0 if r.passed else 0.0)
            for r in probe_readings
        )
        score = sum(trial_scores) / len(trial_scores) if trial_scores else 0.0

        # Pass comes from the SENSOR's verdict, not from re-deriving one out of the score.
        # The two agree for a binary sensor (activation scores 1.0/0.0), and diverge for a
        # fractional one: a function-test submission failing 1 of 4 cases scores 0.75, and
        # `score > 0.5` reported it as PASS while the sensor had said otherwise. Aggregation
        # counts; it does not grade — a majority of trials must have passed.
        n_passed = sum(1 for r in probe_readings if r.passed)
        passed = n_passed / len(probe_readings) > 0.5 if probe_readings else False

        probe_result = ProbeResult(
            probe_id=probe_id,
            score=score,
            passed=passed,
            trial_scores=trial_scores,
            details=tuple(r.details for r in probe_readings if r.details),
        )
        probe_results.append(probe_result)

        if on_probe_complete:
            on_probe_complete(probe_result)

    return probe_results


def compute_metrics(results: list[ProbeResult]) -> dict[str, float]:
    """Compute pass rate and mean score across all probes.

    pass_rate: fraction of probes that passed (binary).
    mean_score: mean of continuous per-probe scores (preserves resolution).
    """
    if not results:
        return {"pass_rate": 0.0, "mean_score": 0.0, "min_score": 0.0, "max_score": 0.0}

    scores = [r.score for r in results]
    n_passed = sum(1 for r in results if r.passed)

    return {
        "pass_rate": n_passed / len(results),
        "mean_score": sum(scores) / len(scores),
        "min_score": min(scores),
        "max_score": max(scores),
    }


def compute_noise_floor(per_run_pass_rates: list[float]) -> float | None:
    """Standard deviation of pass_rate across repeated runs.

    Returns None if fewer than 2 runs (can't compute variance).
    """
    if len(per_run_pass_rates) < 2:
        return None
    return statistics.stdev(per_run_pass_rates)


def build_confusion_matrix(readings: list[Reading]) -> dict[str, dict[str, int]]:
    """Build (expected_skill, activated_skill) confusion matrix from readings.

    Only includes readings that have both expected_skill and activated_skill
    in their metrics dict. Returns nested dict: {expected: {activated: count}}.
    """
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in readings:
        expected = r.metrics.get("expected_skill")
        activated = r.metrics.get("activated_skill")
        if expected is None:
            continue
        label = activated if activated else "(none)"
        matrix[expected][label] += 1
    return {k: dict(v) for k, v in matrix.items()}

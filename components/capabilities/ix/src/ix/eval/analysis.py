"""Post-DAG analysis — aggregate readings into results.

Pure functions. The sensor is the grader — passed=True means correct.
Analysis just counts.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import TYPE_CHECKING

from ix.domain.errors import ResultsError
from ix.domain.models import MAX_DETAILS, Comparison, ProbeDelta, ProbeResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from ix.domain.models import ExperimentResults
    from ix.domain.types import Probe, Reading


def aggregate_readings(
    readings: list[Reading],
    probes: dict[str, Probe],
    on_probe_complete: Callable[[ProbeResult], None] | None = None,
) -> list[ProbeResult]:
    """Group readings by probe_id, compute pass rate per probe.

    Harness-faulted readings are not the subject's and are left out; a probe left with no
    reading at all is not scored (see :func:`unmeasured_probes`).
    """
    by_probe: dict[str, list[Reading]] = defaultdict(list)
    for reading in readings:
        if reading.fault == "harness":
            continue
        by_probe[reading.probe_id].append(reading)

    probe_results: list[ProbeResult] = []
    for probe_id, probe_readings in by_probe.items():
        # `Reading.score` is optional: `Sensor` is a Protocol, so a sensor outside this
        # package may report only `passed`. The docstring's contract — "the sensor is the
        # grader" — makes the boolean the fallback, and taking it here keeps a third-party
        # sensor from crashing aggregation on `sum(None, ...)`.
        trial_scores = tuple(
            r.score if r.score is not None else (1.0 if r.passed else 0.0) for r in probe_readings
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
            details=tuple(dict.fromkeys(r.details for r in probe_readings if r.details))[
                :MAX_DETAILS
            ],
        )
        probe_results.append(probe_result)

        if on_probe_complete:
            on_probe_complete(probe_result)

    return probe_results


def unmeasured_probes(readings: list[Reading]) -> tuple[str, ...]:
    """Probes every one of whose readings was a harness fault, in first-seen order."""
    seen: dict[str, bool] = {}
    for reading in readings:
        accountable = reading.fault != "harness"
        seen[reading.probe_id] = seen.get(reading.probe_id, False) or accountable
    return tuple(probe for probe, measured in seen.items() if not measured)


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


def standard_errors(results: list[ProbeResult]) -> tuple[float | None, float | None]:
    """CLT standard errors over probes: (pass rate, mean score). None below two probes.

    Pass rate is a proportion over n probes, so its SE is √(p(1−p)/n). Mean score's SE is the
    sample SD of per-probe scores over √n. Both treat probes as the sampled unit — the trials
    within a probe are already averaged into its score.
    """
    n = len(results)
    if n < 2:
        return None, None
    p = sum(1 for r in results if r.passed) / n
    pass_se = math.sqrt(p * (1 - p) / n)
    score_se = statistics.stdev(r.score for r in results) / math.sqrt(n)
    return pass_se, score_se


def compare_results(a: ExperimentResults, b: ExperimentResults) -> Comparison:
    """Pair two subjects' results probe by probe. See :class:`Comparison` for the method."""
    if a.experiment_name != b.experiment_name:
        raise ResultsError(
            f"cannot compare results of different experiments: "
            f"{a.experiment_name!r} and {b.experiment_name!r}"
        )
    by_a = {r.probe_id: r for r in a.probe_results}
    by_b = {r.probe_id: r for r in b.probe_results}
    shared = [pid for pid in by_a if pid in by_b]
    if not shared:
        raise ResultsError(f"{a.subject!r} and {b.subject!r} share no probes to compare")
    unmatched = tuple(sorted(set(by_a) ^ set(by_b)))

    probes = tuple(
        ProbeDelta(
            probe_id=pid,
            score_a=by_a[pid].score,
            score_b=by_b[pid].score,
            passed_a=by_a[pid].passed,
            passed_b=by_b[pid].passed,
        )
        for pid in shared
    )
    deltas = [p.delta for p in probes]
    n = len(deltas)
    mean_delta = sum(deltas) / n
    se = statistics.stdev(deltas) / math.sqrt(n) if n >= 2 else None
    ci = (mean_delta - 1.96 * se, mean_delta + 1.96 * se) if se is not None else None
    floors = [f for f in (a.score_noise_floor_sd, b.score_noise_floor_sd) if f is not None]

    return Comparison(
        experiment=a.experiment_name,
        a=a.subject,
        b=b.subject,
        run_id_a=a.run_id,
        run_id_b=b.run_id,
        n=n,
        pass_rate_a=sum(p.passed_a for p in probes) / n,
        pass_rate_b=sum(p.passed_b for p in probes) / n,
        mean_delta=mean_delta,
        delta_stderr=se,
        ci95=ci,
        a_only_passed=sum(1 for p in probes if p.passed_a and not p.passed_b),
        b_only_passed=sum(1 for p in probes if p.passed_b and not p.passed_a),
        noise_floor_sd=max(floors) if floors else None,
        unmatched=unmatched,
        warning=_comparison_warning(a, b),
        sensor_faults=a.sensor_faults + b.sensor_faults,
        harness_faults=a.harness_faults + b.harness_faults,
        both_measured=a.measured_a_model and b.measured_a_model,
        probes=probes,
    )


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


def _comparison_warning(a: ExperimentResults, b: ExperimentResults) -> str | None:
    notes = []
    unmeasured = [r.subject for r in (a, b) if not r.measured_a_model]
    if unmeasured:
        notes.append(
            f"{' and '.join(repr(s) for s in unmeasured)} answered by no real model "
            "(simulator or mock): this compares the harness, not the subjects"
        )
    faulted = [f"{r.subject!r} {r.sensor_faults}" for r in (a, b) if r.sensor_faults]
    if faulted:
        notes.append(
            f"sensor faults ({', '.join(faulted)}): the sensor crashed on some trials, so "
            "the delta may be the sensor's, not the subjects'"
        )
    harness = [f"{r.subject!r} {r.harness_faults}" for r in (a, b) if r.harness_faults]
    if harness:
        notes.append(
            f"harness faults ({', '.join(harness)}): some trials never ran fairly (rate "
            "limits, outages, credentials); rerun them before reading the delta"
        )
    return "; ".join(notes) or None

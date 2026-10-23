"""The statistics, held to what they claim: simulations with fixed seeds that call ix's own
analysis on data whose truth is known.

A verdict is only as good as the error rates behind it, and those are a property of the
method at the trial counts ix runs, not of any one run. So each check here builds a design
by hand, writes readings drawn from a known distribution, runs :func:`ix.domain.analysis
.analyze` over them, and counts how often the outcome is right.

The fast checks run in the normal suite, at a genre's minimum repeats and at the default,
with replications enough for a tolerance about three Monte Carlo standard errors wide. The
full set (more replications, more trial counts, skewed data, clustered counts) is marked
``slow`` and runs with ``IX_SLOW=1 pytest -m slow``. The numbers it prints are the ones in
``docs/design/2026-10-19-record.md``.
"""

from __future__ import annotations

import math
import os
import random
from typing import TYPE_CHECKING, Any, Literal

import pytest

from ix.domain import intervals
from ix.domain.analysis import CHECK_LEVEL, Analysis, analyze
from ix.domain.design import Design, RoleAssignment
from ix.domain.experiment import Margin, MetricRef, Threshold
from ix.domain.genre import AnalysisRef
from ix.domain.kind import Metric
from ix.domain.run import Interaction, Reading, Trial

if TYPE_CHECKING:
    from collections.abc import Callable

slow = pytest.mark.skipif(
    os.environ.get("IX_SLOW") != "1", reason="the full simulation: IX_SLOW=1 pytest -m slow"
)

#: The nominal chance of a false ``holds`` (a one-sided 2.5% test) and of a false alarm
#: from the A/A check.
NOMINAL = 0.025

Shape = Literal["scalar", "boolean", "count"]


# --- building a run by hand ----------------------------------------------------------------


def _design(
    *,
    compare: bool,
    blocks: int,
    shape: Shape = "scalar",
    margin: str | float | None = None,
    comparison: str | None = None,
    threshold: Threshold | None = None,
    calibration: Literal["aa", "none"] = "none",
    estimator: str = "mean",
    direction: Literal["higher", "lower"] = "higher",
) -> Design:
    roles = (
        (RoleAssignment(subject="a", role="control", kind="k"),)
        + (RoleAssignment(subject="b", role="variant", kind="k"),)
        if compare
        else (RoleAssignment(subject="a", role="subject", kind="k"),)
    )
    statistic = {"scalar": "value", "boolean": "value", "count": "rate"}[shape]
    return Design(
        experiment="simulated",
        genre="ix.v1.genre.compare" if compare else "ix.v1.genre.evaluate",
        roles=roles,
        control="a" if compare else None,
        primary=MetricRef(
            metric="m",
            statistic=statistic,
            estimator="rate" if shape != "scalar" else estimator,
        ),
        threshold=threshold,
        comparison=comparison,
        margin=None if margin is None else Margin.model_validate(margin),
        metrics=(Metric(name="m", unit="u", direction=direction, shape=shape),),
        repeats=blocks,
        seed=1,
        order="interleaved",
        calibration=calibration,
        analysis=AnalysisRef(id="ix.v1.analysis.compare" if compare else "ix.v1.analysis.evaluate"),
        verdict_form="three-way",
        report=("question",),
    )


def _trials(design: Design) -> tuple[Trial, ...]:
    takes = [(role.subject, role.role) for role in design.roles]
    if design.calibration == "aa":
        takes.append(("a", "calibration"))
    return tuple(
        Trial(seq=index, repeat=repeat, probe="p", subject=subject, role=role, seed=0)
        for index, (repeat, (subject, role)) in enumerate(
            (repeat, take) for repeat in range(1, design.repeats + 1) for take in takes
        )
    )


type Draw = Callable[[Trial, float, random.Random], Any]


def _analyze(design: Design, trials: tuple[Trial, ...], draw: Draw, rng: random.Random) -> Analysis:
    """One run: every trial's reading from ``draw(trial, block effect, rng)``, a block
    effect shared by the trials of one block."""
    shape = design.metrics[0].shape
    shared = {repeat: rng.gauss(0, 0.5) for repeat in range(1, design.repeats + 1)}
    interactions, readings = [], []
    for trial in trials:
        interactions.append(
            Interaction(
                seq=trial.seq, subject=trial.subject, role=trial.role, started_ns=0, duration_ns=1
            )
        )
        value = draw(trial, shared[trial.repeat], rng)
        fields: dict[str, Any] = (
            {"statistics": {"events": float(value[0]), "units": float(value[1])}}
            if shape == "count"
            else {"value": value}
        )
        readings.append(
            Reading(
                seq=trial.seq,
                subject=trial.subject,
                role=trial.role,
                sensor="ix.v1.sensor.simulated",
                metric="m",
                shape=shape,
                **fields,
            )
        )
    return analyze(design, trials, interactions, readings)


def _rate(
    design: Design, draw: Draw, replications: int, seed: int, count: Callable[[Analysis], bool]
) -> float:
    rng = random.Random(seed)
    trials = _trials(design)
    return (
        sum(count(_analyze(design, trials, draw, rng)) for _ in range(replications)) / replications
    )


def _tolerance(rate: float, replications: int) -> float:
    """Three Monte Carlo standard errors of a rate, and never less than half a point."""
    return max(0.005, 3 * math.sqrt(rate * (1 - rate) / replications))


def _normal(shift_b: float = 0.0) -> Draw:
    """Control and its second sample at 10, the variant ``shift_b`` higher; each trial adds
    its block's effect and its own N(0, 1) noise."""

    def draw(trial: Trial, block: float, rng: random.Random) -> Any:
        return 10.0 + block + rng.gauss(0, 1) + (shift_b if trial.subject == "b" else 0.0)

    return draw


# --- the scenarios -------------------------------------------------------------------------


def covered(blocks: int, replications: int, seed: int = 1) -> float:
    """How often the printed 95% interval of the difference covers the true difference."""
    design = _design(compare=True, blocks=blocks, margin=1.0, comparison="non-inferiority")

    def hit(found: Analysis) -> bool:
        interval = found.findings[0].decisions[0].interval
        return interval is not None and interval.low <= 0.3 <= interval.high

    return _rate(design, _normal(0.3), replications, seed, hit)


def false_holds_at_margin(blocks: int, replications: int, seed: int = 2) -> float:
    """Non-inferiority with the variant worse by exactly the margin: ``holds`` is an error."""
    design = _design(compare=True, blocks=blocks, margin=1.0, comparison="non-inferiority")
    return _rate(design, _normal(-1.0), replications, seed, lambda found: found.outcome == "holds")


def false_holds_at_threshold(blocks: int, replications: int, seed: int = 3) -> float:
    """evaluate with the true mean exactly on the threshold: ``holds`` is an error."""
    design = _design(compare=False, blocks=blocks, threshold=Threshold(op="<=", value=10.0))
    return _rate(design, _normal(), replications, seed, lambda found: found.outcome == "holds")


def identical_builds_hold(blocks: int, replications: int, seed: int = 4) -> float:
    """Two identical builds under non-inferiority with a margin of one within-block standard
    deviation, with the A/A check: how often the gate holds (its power)."""
    design = _design(
        compare=True, blocks=blocks, margin=1.0, comparison="non-inferiority", calibration="aa"
    )
    return _rate(design, _normal(), replications, seed, lambda found: found.outcome == "holds")


def aa_false_alarms(blocks: int, replications: int, seed: int = 5) -> float:
    """A stable environment: how often the A/A check makes the run inconclusive."""
    design = _design(
        compare=True, blocks=blocks, margin=1.0, comparison="non-inferiority", calibration="aa"
    )
    return _rate(
        design, _normal(), replications, seed, lambda found: found.reason.startswith("calibration")
    )


def wilson_false_holds(trials: int, rate: float) -> float:
    """The exact chance that a pass rate whose truth is exactly ``rate`` holds ``>= rate``:
    the sum, over every count of passes that would hold, of its binomial probability."""
    total = 0.0
    for passes in range(trials + 1):
        low, _ = intervals.wilson(passes, trials)(0.05)
        if low > rate:
            total += math.comb(trials, passes) * rate**passes * (1 - rate) ** (trials - passes)
    return total


def clustered_covered(
    trials: int, rate: float, correlation: float, replications: int, seed: int = 6
) -> float:
    """How often a pooled error rate's 95% interval covers the true rate, with 1000 requests
    per trial whose failures cluster within trials (a beta-binomial with intra-trial
    correlation ``correlation``)."""
    rng = random.Random(seed)
    hit = 0
    for _ in range(replications):
        events, units = [], []
        for _trial in range(trials):
            if correlation > 0:
                a = rate * (1 - correlation) / correlation
                b = (1 - rate) * (1 - correlation) / correlation
                share = rng.betavariate(a, b)
            else:
                share = rate
            events.append(rng.binomialvariate(1000, share))
            units.append(1000)
        low, high = intervals.clustered_rate(events, units)(0.05)
        hit += low <= rate <= high
    return hit / replications


def clustered_false_holds(
    trials: int, units: int, rate: float, correlation: float, replications: int, seed: int = 7
) -> float:
    """A pooled error rate whose truth is exactly its threshold: how often ``< rate`` holds,
    with ``units`` requests per trial whose failures cluster as in
    :func:`clustered_covered`."""
    rng = random.Random(seed)
    a = rate * (1 - correlation) / correlation
    b = (1 - rate) * (1 - correlation) / correlation
    held = 0
    for _ in range(replications):
        events = [rng.binomialvariate(units, rng.betavariate(a, b)) for _trial in range(trials)]
        held += intervals.clustered_rate(events, [units] * trials).below(rate) <= 0.05
    return held / replications


# --- the fast checks -----------------------------------------------------------------------

MINIMUM, DEFAULT = 5, 10


@pytest.mark.parametrize("blocks", [MINIMUM, DEFAULT])
def test_the_interval_of_a_difference_covers_as_stated(blocks: int) -> None:
    found = covered(blocks, 1500)
    assert abs(found - 0.95) <= _tolerance(0.95, 1500), found


@pytest.mark.parametrize("blocks", [MINIMUM, DEFAULT])
def test_a_loss_of_exactly_the_margin_holds_no_more_often_than_stated(blocks: int) -> None:
    """Changed requirement: before, 10.6% at 5 trials (the percentile bootstrap) or 0.2%
    once widened by the noise floor; now the one-sided 2.5% the decision claims."""
    found = false_holds_at_margin(blocks, 2000)
    assert abs(found - NOMINAL) <= _tolerance(NOMINAL, 2000), found


@pytest.mark.parametrize("blocks", [MINIMUM, DEFAULT])
def test_a_mean_on_the_threshold_holds_no_more_often_than_stated(blocks: int) -> None:
    """Changed requirement: before, 8.6% at 5 trials and 5.2% at 10, against 2.5%."""
    found = false_holds_at_threshold(blocks, 2000)
    assert abs(found - NOMINAL) <= _tolerance(NOMINAL, 2000), found


def test_identical_builds_pass_a_gate_with_the_power_the_t_test_has() -> None:
    """Changed requirement: with the A/A noise floor added, two identical builds passed a
    non-inferiority gate of one standard deviation 4.6% of the time at 10 trials. The paired
    t test's power there is 0.51 (the noncentral t, noncentrality 1 / (sqrt(2) / sqrt(10))),
    less the A/A check's 1% of false alarms."""
    found = identical_builds_hold(DEFAULT, 1000)
    assert 0.42 <= found <= 0.56, found


def test_a_stable_environment_rarely_fails_the_check() -> None:
    found = aa_false_alarms(MINIMUM, 2000)
    assert found <= CHECK_LEVEL + _tolerance(CHECK_LEVEL, 2000), found


@pytest.mark.parametrize("trials", [10, 30, 60])
def test_a_pass_rate_on_its_threshold_holds_at_most_4_percent_of_the_time(trials: int) -> None:
    """Exact, not simulated: Wilson's one-sided error at the boundary, at 0.8 and 0.9. The
    bound is 4%, not the stated 2.5%: Wilson's error oscillates with the number of trials,
    up to 3.9% between 5 and 200, the record's named weakness. Changed name: it said "no
    more often than stated" while allowing 1.5 points more."""
    for rate in (0.8, 0.9):
        assert wilson_false_holds(trials, rate) <= NOMINAL + 0.015


def test_zero_errors_never_make_an_interval_of_zero_width() -> None:
    """The defect behind this: an error rate bootstrapped per trial gave [0, 0] for any
    number of clean trials, and a threshold on it held as if certain."""
    low, high = intervals.clustered_rate([0] * 5, [1000] * 5)(0.05)
    assert low == 0.0 and high == pytest.approx(3.84 / 5003.84, rel=1e-3)
    low, high = intervals.wilson(0, 5)(0.05)
    assert low == 0.0 and high > 0.4


def test_clustered_failures_are_covered_at_the_minimum() -> None:
    found = clustered_covered(MINIMUM, 0.01, 0.001, 600)
    assert found >= 0.95 - _tolerance(0.95, 600), found


def test_a_count_on_its_threshold_over_many_units_holds_at_most_twice_as_often_as_stated() -> None:
    """Regression: over 10000 requests a trial the score test's level is exactly 0, and the
    decision stopped there, without the t test at the effective sample size; an error rate
    on its threshold then held 22% of the time. Clustering this heavy is the record's named
    weakness (it under-covers), so the bound is twice the nominal 2.5%, not the nominal."""
    found = clustered_false_holds(MINIMUM, 10_000, 0.05, 0.01, 2000)
    assert found <= 2 * NOMINAL, found


# --- the full set --------------------------------------------------------------------------


@slow
@pytest.mark.slow
@pytest.mark.timeout(900)  # about 90 seconds on a laptop, past the suite's default
def test_the_full_simulation() -> None:
    """Every scenario across trial counts, printed as the record's table, each held to its
    tolerance."""
    replications = 10_000
    rows = []
    for blocks in (3, 5, 10, 20):
        rows.append(
            (
                blocks,
                covered(blocks, replications),
                false_holds_at_margin(blocks, replications),
                false_holds_at_threshold(blocks, replications),
                identical_builds_hold(blocks, 4000),
                aa_false_alarms(blocks, replications),
            )
        )
    print("\nblocks coverage false-holds(NI) false-holds(threshold) power(identical) aa-alarms")
    for row in rows:
        print(
            " ".join(f"{value:.4f}" if isinstance(value, float) else f"{value:6d}" for value in row)
        )
        _, cover, margin, threshold, _, alarms = row
        assert abs(cover - 0.95) <= _tolerance(0.95, replications)
        assert abs(margin - NOMINAL) <= _tolerance(NOMINAL, replications)
        assert abs(threshold - NOMINAL) <= _tolerance(NOMINAL, replications)
        assert alarms <= CHECK_LEVEL + _tolerance(CHECK_LEVEL, replications)
    worst = max(
        wilson_false_holds(trials, rate)
        for trials in range(5, 201)
        for rate in (0.5, 0.8, 0.9, 0.95)
    )
    print(f"wilson worst one-sided error, 5-200 trials at 0.5/0.8/0.9/0.95: {worst:.4f}")
    assert worst <= 0.04  # the record's named weakness: Wilson's error oscillates with n
    for trials in (3, 5, 10):
        for rate, correlation in ((0.01, 0.0), (0.01, 0.001), (0.01, 0.01), (0.05, 0.01)):
            found = clustered_covered(trials, rate, correlation, 2000)
            print(f"clustered rate {rate} correlation {correlation} trials {trials}: {found:.3f}")
            if correlation <= 0.001:  # heavier clustering is the record's named weakness
                assert found >= 0.95 - _tolerance(0.95, 2000)
    for trials in (5, 10):
        for units, correlation in ((1000, 0.001), (1000, 0.01), (10_000, 0.001), (10_000, 0.01)):
            found = clustered_false_holds(trials, units, 0.05, correlation, 4000)
            print(
                f"count on its threshold 0.05, {units} units, correlation {correlation}, "
                f"trials {trials}: holds {found:.4f}"
            )
            assert found <= 2 * NOMINAL

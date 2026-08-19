"""Regressions for the two real defects the strict-typing pass surfaced.

Both were reachable only through ix's declared extension points — a third-party `Sensor`,
and a probe's free-form frontmatter — which is exactly where a type error stops being
cosmetic. The first three tests fail on the pre-fix code; the last is an invariant guard,
not a regression, and is labelled as such.
"""

import pytest

from ix.domain.types import Probe, Reading
from ix.eval.analysis import aggregate_readings
from ix.eval.sensors import ActivationSensor, ActivationSensorConfig


def test_aggregation_survives_a_sensor_that_reports_only_passed() -> None:
    """`Reading.score` is optional, so a sensor outside ix may report only `passed`.

    Pre-fix, `sum(tuple_containing_None)` raised TypeError and took the whole run down
    after every trial had already been paid for.
    """
    readings = [
        Reading(sensor_name="third-party", probe_id="p1", trial_index=0, passed=True),
        Reading(sensor_name="third-party", probe_id="p1", trial_index=1, passed=False),
    ]

    results = aggregate_readings(readings, probes={"p1": Probe(id="p1", prompt="x")})

    assert len(results) == 1
    # The docstring's contract — "the sensor is the grader" — makes passed the fallback.
    assert results[0].trial_scores == (1.0, 0.0)
    assert results[0].score == pytest.approx(0.5)


def test_aggregation_mixes_scored_and_unscored_readings() -> None:
    """A CompositeSensor can pair an ix sensor with a third-party one on the same probe."""
    readings = [
        Reading(sensor_name="ix", probe_id="p1", trial_index=0, passed=True, score=0.8),
        Reading(sensor_name="third-party", probe_id="p1", trial_index=0, passed=True),
    ]

    (result,) = aggregate_readings(readings, probes={"p1": Probe(id="p1", prompt="x")})

    assert result.trial_scores == (0.8, 1.0)
    assert result.score == pytest.approx(0.9)


def test_explicit_null_expected_skill_does_not_become_a_none_expectation() -> None:
    """A probe may carry `expected_skill:` with no value — YAML parses that to None.

    Pre-fix the guard and the value disagreed: the probe passed
    `... or config.expected_skill` on the config's truthiness, then stored the probe's
    own None. The sensor read that back as "no expectation" and scored the probe against
    nothing while still reporting a verdict for it.
    """
    probes = (
        Probe(id="p-null", prompt="x", metadata={"expected_skill": None}),
        Probe(id="p-own", prompt="y", metadata={"expected_skill": "research"}),
        Probe(id="p-bare", prompt="z"),
    )

    sensor = ActivationSensor.from_config(
        ActivationSensorConfig(expected_skill="build-eval"), probes
    )

    skills = sensor._expected_skills
    assert skills["p-null"] == "build-eval", "explicit null must fall back to the config"
    assert skills["p-own"] == "research", "a probe's own skill still wins"
    assert skills["p-bare"] == "build-eval"
    assert None not in skills.values()


def test_non_string_frontmatter_expectation_is_coerced() -> None:
    """Invariant guard, not a regression — this passed before the fix too.

    `expectation` is compared against string literals downstream, and frontmatter is
    untyped YAML (`expectation: no` parses to the bool False under YAML 1.1). Pinning the
    stored type here keeps a future edit from widening it back.
    """
    probes = (Probe(id="p1", prompt="x", metadata={"expectation": "should_not_trigger"}),)

    sensor = ActivationSensor.from_config(ActivationSensorConfig(expected_skill="s"), probes)

    assert sensor._expectations["p1"] == "should_not_trigger"
    assert all(isinstance(v, str) for v in sensor._expectations.values())


def test_a_fractional_score_does_not_override_the_sensors_verdict() -> None:
    """Found by `lab/sensor-integrity` on its first run.

    A function-test submission failing 1 of 4 cases scores 0.75. Aggregation derived
    `passed = score > 0.5` and reported PASS — contradicting the sensor, which had already
    ruled the submission incorrect. In a benchmark, a wrong verdict is worse than a crash:
    a crash gets noticed.
    """
    readings = [
        Reading(
            sensor_name="function-test",
            probe_id="p1",
            trial_index=0,
            passed=False,
            score=0.75,
        )
    ]

    (result,) = aggregate_readings(readings, probes={"p1": Probe(id="p1", prompt="x")})

    assert result.score == pytest.approx(0.75), "the score still reports partial credit"
    assert result.passed is False, "but the verdict is the sensor's"


def test_binary_sensors_are_unaffected_by_the_verdict_fix() -> None:
    """Activation scores 1.0/0.0, so majority-of-passed and score > 0.5 must agree."""
    readings = [
        Reading(sensor_name="activation", probe_id="p1", trial_index=i, passed=p, score=float(p))
        for i, p in enumerate([True, True, False])
    ]

    (result,) = aggregate_readings(readings, probes={"p1": Probe(id="p1", prompt="x")})

    assert result.passed is True
    assert result.score == pytest.approx(2 / 3)

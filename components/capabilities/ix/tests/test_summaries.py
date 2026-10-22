"""What a report says beside its verdict, as pure functions of a design and a run's tables:
the probe screen, flags and confusion tables."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ix.application.experiments import examine
from ix.domain.run import Reading, Trial
from ix.domain.summaries import confusion, flags, screen

if TYPE_CHECKING:
    from ix.application.experiments import Vocabulary
    from ix.domain.design import Design


def _design(data: dict[str, Any], vocabulary: Vocabulary) -> Design:
    examined = examine(data, vocabulary, base=".")
    assert examined.design is not None, examined.problems
    return examined.design


def _reading(seq: int, value: bool, **detail: Any) -> Reading:
    return Reading(
        seq=seq, subject="catalog", role="subject", sensor="ix.v1.sensor.activation",
        metric="activated_correctly", shape="boolean", value=value, detail=detail,
    )  # fmt: skip


def _trials(probes: dict[str, int]) -> list[Trial]:
    trials: list[Trial] = []
    for probe, count in probes.items():
        for _ in range(count):
            trials.append(
                Trial(
                    seq=len(trials),
                    repeat=1,
                    probe=probe,
                    subject="catalog",
                    role="subject",
                    seed=0,
                )  # fmt: skip
            )
    return trials


def test_the_screen_names_the_probes_that_cannot_discriminate(
    evaluate_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    design = _design(evaluate_data, vocabulary)
    trials = _trials({"easy": 5, "coin": 5, "broken": 5, "hard": 5})
    values = {"easy": [True] * 5, "coin": [True, False, True, False, True], "hard": [False] * 5}
    readings = [
        _reading(trial.seq, values[trial.probe][index % 5])
        for index, trial in enumerate(trials)
        if trial.probe in values
    ]
    screened = {probe.probe: probe.screen for probe in screen(design, trials, readings)}
    assert screened == {"easy": "ceiling", "coin": "noisy", "broken": "failed", "hard": "floor"}


def test_a_flag_is_said_only_when_a_trial_raised_it(
    evaluate_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    evaluate_data["sensors"] = [{"kind": "judge", "model_runtime": "mock", "rubric": "Helpful."}]
    design = _design(evaluate_data, vocabulary)
    # The judge's flagged metrics are read whenever the judge is in use.
    assert {"judge_in_family", "judge_inconsistent"} <= {m.name for m in design.metrics}
    read = [
        Reading(
            seq=seq,
            subject="catalog",
            role="subject",
            sensor="ix.v1.sensor.judge",
            metric="judge_in_family",
            shape="boolean",
            value=seq == 0,
        )  # fmt: skip
        for seq in range(4)
    ]
    (said,) = flags(design, read)
    assert said.startswith("judge_in_family: the judge is of the subject's model family")
    assert said.endswith("(1 of 4 trials)")
    assert flags(design, [r.model_copy(update={"value": False}) for r in read]) == ()


def test_a_confusion_table_counts_expected_against_observed(
    evaluate_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    design = _design(evaluate_data, vocabulary)
    pairs = [("review", "review"), ("review", "none"), ("none", "review"), ("none", "none")]
    readings = [
        _reading(seq, expected == fired, expected=expected, fired=fired)
        for seq, (expected, fired) in enumerate(pairs)
    ]
    calibration = _reading(9, True, expected="review", fired="review")
    (table,) = confusion(
        design, [*readings, calibration.model_copy(update={"role": "calibration"})]
    )
    assert table.counts == {"review": {"review": 1, "none": 1}, "none": {"review": 1, "none": 1}}
    assert (table.true_positive, table.false_positive) == (1, 1)
    assert (table.false_negative, table.true_negative) == (1, 1)
    assert table.precision == table.recall == 0.5

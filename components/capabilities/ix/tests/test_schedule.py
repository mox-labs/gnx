"""The schedule: the trials of a run, as a pure function of the design.

A plan and a run call the same function, so the plan a person reviewed is the list a run
carries out. These tests hold the list's shape and the properties that make that true.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ix.application.experiments import examine, make_plan
from ix.domain.run import CALIBRATION, Trial, schedule, schedule_digest

if TYPE_CHECKING:
    from ix.application.experiments import Vocabulary


def _trials(data: dict[str, Any], vocabulary: Vocabulary) -> tuple[Trial, ...]:
    examined = examine(data, vocabulary)
    assert examined.problems == (), examined.problems
    assert examined.experiment and examined.design and examined.probes
    return schedule(examined.experiment, examined.design, examined.probes)


def test_it_is_every_subject_in_every_block_plus_the_controls_second_sample(
    compare_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    compare_data["factors"] = {"size": ["1MiB", "1GiB"]}
    compare_data["probes"][0]["target"] = "{url}/{size}"
    trials = _trials(compare_data, vocabulary)
    # 10 repeats x 2 cells x 1 probe, and in each block: control, variant, calibration.
    assert len(trials) == 10 * 2 * 3
    assert [trial.seq for trial in trials] == list(range(60))
    blocks: dict[tuple[int, str, str], list[tuple[str, str]]] = {}
    for trial in trials:
        blocks.setdefault(trial.block, []).append((trial.subject, trial.role))
    assert len(blocks) == 20
    for turns in blocks.values():
        assert sorted(turns) == [
            ("buffered", CALIBRATION),
            ("buffered", "control"),
            ("streaming", "variant"),
        ]


def test_blocks_follow_by_repeat_then_cell_and_a_block_is_never_split(
    compare_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    compare_data["factors"] = {"size": ["1MiB", "1GiB"]}
    compare_data["probes"][0]["target"] = "{url}/{size}"
    trials = _trials(compare_data, vocabulary)
    order = list(dict.fromkeys(trial.block for trial in trials))
    assert order == sorted(
        order, key=lambda block: (block[0], ["size=1MiB", "size=1GiB"].index(block[1]))
    )
    for index in range(0, len(trials), 3):
        assert len({trial.block for trial in trials[index : index + 3]}) == 1


def test_interleaved_draws_the_order_from_the_seed_and_sequential_keeps_the_order_written(
    compare_data: dict[str, Any], vocabulary: Vocabulary, builtin_genres: Any
) -> None:
    compare_data["repeats"] = 12
    interleaved = _trials(compare_data, vocabulary)
    orders = {tuple(t.role for t in interleaved[i : i + 3]) for i in range(0, 36, 3)}
    assert len(orders) > 1  # 12 blocks of 3 turns do not all come out the same way

    examined = examine(compare_data, vocabulary)
    assert examined.experiment and examined.design and examined.probes
    written = examined.design.model_copy(update={"order": "sequential"})
    sequential = schedule(examined.experiment, written, examined.probes)
    assert {tuple(t.role for t in sequential[i : i + 3]) for i in range(0, 36, 3)} == {
        ("control", "variant", CALIBRATION)
    }


def test_the_same_design_gives_the_same_list_and_another_seed_another(
    compare_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    compare_data["repeats"] = 12
    first, again = _trials(compare_data, vocabulary), _trials(compare_data, vocabulary)
    assert first == again and schedule_digest(first) == schedule_digest(again)
    other = _trials({**compare_data, "seed": 7}, vocabulary)
    assert schedule_digest(other) != schedule_digest(first)


def test_the_control_and_its_second_sample_never_share_a_seed(
    compare_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    """Seeding on the trial alone would make the two samples of the control identical and
    the A/A check pass by construction."""
    trials = _trials(compare_data, vocabulary)
    by_block: dict[tuple[int, str, str], dict[str, int]] = {}
    for trial in trials:
        by_block.setdefault(trial.block, {})[trial.role] = trial.seed
    assert all(seeds["control"] != seeds[CALIBRATION] for seeds in by_block.values())
    assert len({trial.seed for trial in trials}) == len(trials)


def test_a_genre_without_calibration_schedules_the_subjects_alone(
    evaluate_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    trials = _trials(evaluate_data, vocabulary)
    assert len(trials) == 10 and {trial.role for trial in trials} == {"subject"}


def test_the_plan_counts_and_names_exactly_this_list(
    compare_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    compare_data["factors"] = {"size": ["1MiB", "1GiB"]}
    compare_data["probes"][0]["target"] = "{url}/{size}"
    examined = examine(compare_data, vocabulary)
    plan = make_plan(examined, vocabulary, None)
    trials = _trials(compare_data, vocabulary)
    assert plan.trials.total == len(trials)
    assert plan.schedule_digest == schedule_digest(trials)


def test_the_plan_digest_covers_the_order_of_the_trials(
    compare_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    """A changed seed changes the order and nothing else a plan counts; the digest moves."""
    one = make_plan(examine({**compare_data, "seed": 1}, vocabulary), vocabulary, None)
    two = make_plan(examine({**compare_data, "seed": 2}, vocabulary), vocabulary, None)
    assert one.trials == two.trials
    assert one.schedule_digest != two.schedule_digest and one.digest != two.digest

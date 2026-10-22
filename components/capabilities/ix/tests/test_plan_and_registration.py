"""The plan states the decision and is identified by meaning; a registration freezes the
whole design and shows deviations.

``plan`` runs nothing. Its digest is stable against a comment, key order, a default written
out, reworded prose and a field a later ix adds; it changes when a value a run would use
changes. ``register`` stores the design as resolved, including what the genre supplied, so
a genre that moves after registration is a deviation. A stored record tolerates fields this
ix does not know. Amending keeps the superseded record and the reason.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from click.testing import CliRunner

import ix
from ix.adapters._in.cli import main
from ix.application.experiments import Vocabulary, examine, make_plan
from ix.domain.experiment import Experiment
from ix.domain.plan import Plan, cells
from ix.domain.registration import Registration, deviations

if TYPE_CHECKING:
    from pathlib import Path

Write = Any  # the write_experiment fixture


def _plan(data: dict[str, Any], vocabulary: Vocabulary) -> Plan:
    return make_plan(examine(data, vocabulary, base="."), vocabulary, None)


def _record(file: Path) -> Path:
    return file.parent / "experiment.registration.json"


# --- the plan --------------------------------------------------------------------------


class TestPlanStatesTheDecision:
    def test_question_hypothesis_guardrails_and_verdict_form(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        compare_data["guardrails"] = [
            {"metric": "throughput", "margin": "5%"},
            {"metric": "error_rate", "threshold": {"op": "<", "value": 0.01}},
        ]
        made = _plan(compare_data, vocabulary)
        assert made.question == "Is streaming no worse than buffered?"
        assert made.decides == ("streaming is at most 5% worse than buffered on ttfb p99",)
        assert made.comparison == "non-inferiority"
        assert made.guardrails == (
            "throughput at most 5% worse than buffered",
            "error_rate < 0.01",
        )
        assert (made.verdict_form, made.verdict) == ("three-way", "holds / fails / inconclusive")

    def test_the_decision_reads_as_a_sentence_and_the_margin_says_whose_it_is(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary, write_experiment: Write
    ) -> None:
        """Regression: for "is the 25 ms build slower than the 20 ms build?", regress printed
        "build25 is no worse than build20 by more than 5%", and nothing said where the 5%
        came from."""
        subjects = compare_data["subjects"]
        regress = {
            **compare_data,
            "genre": "regress",
            "subjects": {"build20": subjects["buffered"], "build25": subjects["streaming"]},
            "primary": {"metric": "latency", "statistic": "p50"},
        }
        del regress["comparison"]
        made = _plan(regress, vocabulary)
        assert made.decides == ("build25 is at most 5% worse than build20 on latency p50",)
        (margin,) = [value for value in made.provenance if value.path == "margin"]
        assert (margin.value, margin.source) == ("5%", "experiment")
        assert "the person's decision" in margin.note
        # A margin in the metric's own unit says the unit.
        absolute = _plan({**regress, "margin": 3}, vocabulary)
        assert absolute.decides == ("build25 is at most 3 ms worse than build20 on latency p50",)
        shown = CliRunner(env={"COLUMNS": "200"}).invoke(
            main, ["plan", str(write_experiment(regress))]
        )
        assert (
            "Margin:       5%, set in the experiment: the largest loss still counted as no "
            "worse; the person's decision, made before the run"
        ) in shown.stdout

    def test_a_count_combines_as_a_rate_whatever_the_genre_says(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """Regression: a count took the genre's estimator, so the plan, the registration and
        each decision on ``error_rate`` said ``mean`` while the analysis pooled it as a
        rate."""
        compare_data["guardrails"] = [
            {"metric": "error_rate", "threshold": {"op": "<", "value": 0.01}}
        ]
        made = _plan(compare_data, vocabulary)
        (estimator,) = [v for v in made.provenance if v.path == "guardrails.0.estimator"]
        assert (estimator.value, estimator.source) == ("rate", "ix")
        assert "counts" in estimator.note

    def test_the_genre_is_carried_short_and_as_its_url(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        made = _plan(compare_data, vocabulary)
        assert (made.genre, made.genre_url) == ("compare", "ix.v1.genre.compare")
        assert made.document()["schema"] == "ix.v1.plan"

    def test_a_genre_without_a_verdict_says_so(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        del compare_data["subjects"]["streaming"], compare_data["comparison"]
        del compare_data["margin"]
        made = _plan(
            {**compare_data, "genre": "characterize", "factors": {"n": [1, 2]}}, vocabulary
        )
        assert made.verdict == "none: estimates only"
        assert made.decides == ("buffered is measured on ttfb p99 across n",)

    def test_it_states_roles_and_which_subject_is_the_control(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        made = _plan(compare_data, vocabulary)
        assert made.control == "buffered"
        assert [(s.subject, s.role, s.kind) for s in made.subjects] == [
            ("buffered", "control", "process"), ("streaming", "variant", "process"),
        ]  # fmt: skip
        assert _plan({**compare_data, "control": "streaming"}, vocabulary).control == "streaming"


class TestPlanCounts:
    def test_cells_are_every_combination_of_levels(self) -> None:
        assert cells({}) == ({},)
        grid = cells({"size": ("1MiB", "1GiB"), "concurrency": (1, 8, 64)})
        assert len(grid) == 6
        assert (grid[0], grid[-1]) == (
            {"size": "1MiB", "concurrency": 1}, {"size": "1GiB", "concurrency": 64},
        )  # fmt: skip

    def test_the_trial_arithmetic_is_shown(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        compare_data["factors"] = {"size": ["1MiB", "64MiB", "1GiB"], "rate": ["10/s", "80/s"]}
        made = _plan(compare_data, vocabulary)
        trials = made.trials
        assert (trials.subjects, trials.cells, trials.probes, trials.repeats) == (2, 6, 1, 10)
        assert (trials.measured, trials.calibration, trials.total) == (120, 60, 180)
        assert trials.text() == (
            "2 subjects x 6 cells x 1 probe x 10 repeats = 120, + 60 A/A = 180"
        )
        assert made.calibration == "aa"

    def test_no_calibration_where_the_genre_has_none(
        self, evaluate_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        made = _plan(evaluate_data, vocabulary)
        assert made.calibration == "none" and made.trials.calibration == 0
        assert made.trials.text() == "1 subject x 1 cell x 1 probe x 10 repeats = 10"

    def test_the_json_plan_counts_cells_and_does_not_enumerate_them(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        compare_data["factors"] = {"a": list(range(40)), "b": list(range(40))}
        document = _plan(compare_data, vocabulary).document()
        assert document["trials"]["cells"] == 1600
        assert "cells" not in document and len(json.dumps(document)) < 20_000

    def test_duration_is_estimated_from_what_probe_kinds_declare(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        compare_data["probes"][0]["warmup"] = "15s"
        made = _plan(compare_data, vocabulary)
        assert made.duration.seconds == made.trials.total * 75
        assert made.duration.text().startswith("about 38m (30 trials, probe time only")

    def test_duration_follows_a_factor_through_a_placeholder(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        compare_data["factors"] = {"length": ["30s", "2m"]}
        compare_data["probes"][0]["duration"] = "{length}"
        made = _plan(compare_data, vocabulary)
        assert made.duration.seconds == (30 + 120) * 10 * 3

    def test_duration_is_not_invented(
        self, evaluate_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        duration = _plan(evaluate_data, vocabulary).duration
        assert duration.seconds is None
        assert duration.text() == "not estimated (probe kind prompt declares no duration)"

    def test_the_order_is_described_truthfully(
        self, compare_data: dict[str, Any], evaluate_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """A one-subject plan used to say "the subjects run in a random order"."""
        compared = _plan({**compare_data, "seed": 42}, vocabulary).order
        assert (compared.scheme, compared.seed) == ("interleaved", 42)
        assert "subjects run in a random order" in compared.description
        alone = _plan(evaluate_data, vocabulary).order
        assert "nothing to interleave" in alone.description

    def test_a_plan_of_a_run_that_calls_a_model_states_no_spend(
        self, evaluate_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """Changed requirement: spend is removed. A plan estimates no cost and holds a run
        to no ceiling; what the run will do is in its trials and its effects."""
        made = _plan(evaluate_data, vocabulary)
        assert "spend" not in made.document()
        assert {e.effect for e in made.effects} == {"tools", "process", "filesystem"}

    def test_the_genres_note_is_said_once(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        made = _plan(compare_data, vocabulary)
        (note,) = made.notes
        assert note.startswith("checked by simulation") and "design record" in note
        assert not any("design record" in value.note for value in made.provenance)

    def test_an_invalid_experiment_is_not_planned(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        del compare_data["margin"]
        file = write_experiment(compare_data)
        with pytest.raises(ix.InvalidExperimentError) as raised:
            ix.plan(file)
        assert [p.kind for p in raised.value.problems] == ["margin_required"]
        assert raised.value.fix == f"ix validate {file}"

    def test_planning_and_validating_write_nothing(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file = write_experiment(compare_data)
        ix.plan(file)
        ix.validate(file)
        ix.resolve(file)
        assert sorted(p.name for p in file.parent.iterdir()) == ["experiment.yaml"]


class TestPlanDigest:
    def test_the_same_file_gives_the_same_plan(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file = write_experiment(compare_data)
        first, second = ix.plan(file), ix.plan(file)
        assert first == second and len(first.digest) == 64

    def test_comments_key_order_and_written_out_defaults_change_nothing(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        one = write_experiment(compare_data, "one")
        other = write_experiment(dict(reversed(list(compare_data.items()))), "other")
        other.write_text(
            "# reviewed 2026-10-13\n" + other.read_text() + "environment: {kind: none}\n"
        )
        assert ix.plan(one).digest == ix.plan(other).digest
        assert ix.plan(one).experiment_digest == ix.plan(other).experiment_digest

    def test_prose_is_outside_the_plan_digest(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """Reviewed defect: the digest covered notes and descriptions, so rewording one
        changed every plan id."""
        before = _plan(compare_data, vocabulary)
        reworded = {**compare_data, "question": "Does streaming hold?", "rationale": "A hunch."}
        after = _plan(reworded, vocabulary)
        assert after.digest == before.digest
        assert after.experiment_digest != before.experiment_digest

        genres = vocabulary.genres.genres
        compare = genres["ix.v1.genre.compare"]
        renoted = compare.model_copy(
            update={
                "design": compare.design.model_copy(update={"note": "settled by the canon"}),
                "guidance": "Rewritten guidance.",
                "question": "Reworded?",
            }
        )
        moved = Vocabulary(
            genres=vocabulary.genres.model_copy(
                update={"genres": {**genres, compare.name: renoted}}
            ),
            kinds=vocabulary.kinds,
        )
        assert _plan(compare_data, moved).digest == before.digest
        assert _plan(compare_data, moved).notes == ("settled by the canon",)

    def test_a_reworded_note_is_outside_the_plan_digest(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """Regression: an ``x-`` note stayed in the options the plan digest covers, so
        rewording one moved the plan, against the digest's own promise."""
        compare_data["subjects"]["buffered"]["x-why"] = "the current proxy"
        compare_data["probes"][0]["x-why"] = "a large download"
        before = _plan(compare_data, vocabulary)
        compare_data["subjects"]["buffered"]["x-why"] = "the proxy in production today"
        compare_data["probes"][0]["x-why"] = "one big file"
        after = _plan(compare_data, vocabulary)
        assert after.digest == before.digest
        assert after.experiment_digest != before.experiment_digest

    def test_a_field_added_to_the_plan_document_changes_no_digest(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """The digest is over a named basis, not over the plan document, so the document can
        grow."""
        made = _plan(compare_data, vocabulary)
        registered = made.model_copy(update={"registration": made.registration.model_copy(
            update={"status": "matches", "location": "somewhere"})})  # fmt: skip
        assert registered.digest == made.digest
        assert "digest" in Plan.model_fields and "plan" not in Plan.model_fields

    @pytest.mark.parametrize(
        "change",
        [
            {"repeats": 7},
            {"seed": 3},
            {"margin": "6%"},
            {"comparison": "equivalence"},
            {"control": "streaming"},
            {"factors": {"size": ["1MiB", "1GiB"]}},
            {"guardrails": [{"metric": "throughput", "margin": "5%"}]},
            {"primary": {"metric": "ttfb", "statistic": "p50"}},
            {"primary": {"metric": "ttfb", "statistic": "p99", "estimator": "median"}},
            {"genre": "regress"},
            {"environment": {"kind": "compose", "file": "compose.yaml"}},
        ],
    )
    def test_a_change_to_what_a_run_would_do_changes_the_digest(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary, change: dict[str, Any]
    ) -> None:
        assert _plan({**compare_data, **change}, vocabulary).digest != (
            _plan(compare_data, vocabulary).digest
        )

    def test_a_changed_probe_or_subject_option_changes_the_digest(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        before = _plan(compare_data, vocabulary)
        compare_data["probes"][0]["target"] = "{url}/y"
        retargeted = _plan(compare_data, vocabulary)
        assert retargeted.probes.digest != before.probes.digest
        assert retargeted.digest != before.digest
        compare_data["subjects"]["streaming"]["command"] = ["./proxy", "--streaming", "-O2"]
        assert _plan(compare_data, vocabulary).digest != retargeted.digest


# --- registration ----------------------------------------------------------------------


class TestRegister:
    def test_the_record_holds_the_design_as_resolved_and_the_plan_that_was_read(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        plan = ix.plan(file)
        registered = ix.register(file)
        assert (registered.outcome, registered.location) == ("created", str(_record(file)))
        stored = json.loads(_record(file).read_text())
        assert stored == registered.registration.document()
        assert stored["schema"] == "ix.v1.registration"
        assert stored["plan_digest"] == plan.digest
        assert stored["experiment_digest"] == plan.experiment_digest
        assert stored["probes_digest"] == plan.probes.digest
        assert stored["ix_version"] == ix.__version__ and stored["registered_at"]
        design = stored["design"]
        resolved = ix.resolve(file).document()
        assert set(design) == set(resolved) - {"provenance"}
        assert [metric["name"] for metric in design["metrics"]] == ["ttfb"]
        assert design["sensors"][0]["kind"] == "ix.v1.sensor.timing"
        # What the genre supplied is in the record, not just the genre's name.
        assert (design["repeats"], design["order"], design["calibration"]) == (
            10,
            "interleaved",
            "aa",
        )
        assert design["analysis"] == {"id": "ix.v1.analysis.compare", "params": {}}
        assert design["primary"] == {"metric": "ttfb", "statistic": "p99", "estimator": "mean"}
        assert design["margin"] == {"value": 5.0, "basis": "relative"}
        assert design["comparison"] == "non-inferiority" and design["control"] == "buffered"
        assert design["hypotheses"] == ["streaming is at most 5% worse than buffered on ttfb p99"]

    def test_the_record_is_named_after_the_file(
        self, compare_data: dict[str, Any], tmp_path: Path
    ) -> None:
        file = tmp_path / "proxy.yaml"
        file.write_text(yaml.safe_dump(compare_data))
        assert ix.register(file).location == str(tmp_path / "proxy.registration.json")

    def test_a_write_cut_short_leaves_the_record_as_it_was(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: the registration was written in place, so a crash mid-write left a
        file the next read refuses as not JSON. It is written beside and moved into place:
        a write that does not finish leaves the old record whole, and no stray file."""
        import os

        from ix.adapters._out.registration_file import RegistrationFile

        record = RegistrationFile(str(tmp_path / "experiment.registration.json"))
        record.write({"experiment": "before"})

        def cut(*_: Any) -> None:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(os, "replace", cut)
        with pytest.raises(OSError, match="No space"):
            record.write({"experiment": "after", "design": {"repeats": 5}})
        assert record.read() == {"experiment": "before"}
        assert [p.name for p in tmp_path.iterdir()] == ["experiment.registration.json"]

    def test_registering_again_unchanged_keeps_the_record(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        first = ix.register(file)
        written = _record(file).read_bytes()
        again = ix.register(file)
        assert again.outcome == "unchanged"
        assert again.registration == first.registration
        assert _record(file).read_bytes() == written

    def test_a_changed_experiment_is_refused_and_the_record_kept(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        ix.register(file)
        written = _record(file).read_bytes()
        write_experiment({**compare_data, "margin": "20%"})
        with pytest.raises(ix.RegistrationConflictError) as raised:
            ix.register(file)
        assert raised.value.kind == "conflict" and "--amend" in (raised.value.fix or "")
        assert [d.field for d in raised.value.deviations][0] == "design.margin.value"
        assert _record(file).read_bytes() == written

    def test_an_invalid_experiment_is_not_registered(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        del compare_data["primary"]
        file: Path = write_experiment(compare_data)
        with pytest.raises(ix.InvalidExperimentError):
            ix.register(file)
        assert not _record(file).exists()

    def test_plan_says_where_the_experiment_stands(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        assert ix.plan(file).registration.text() == "none"
        ix.register(file)
        assert ix.plan(file).registration.status == "matches"
        write_experiment({**compare_data, "repeats": 9, "seed": 1})
        status = ix.plan(file).registration
        assert (status.status, status.location) == ("deviations", str(_record(file)))
        assert status.text() == f"{status.deviations} deviations" and status.deviations >= 2


class TestDeviations:
    def test_none_when_nothing_changed(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        ix.register(file)
        found = ix.deviations(file)
        assert found.deviations == () and found.exit_code == 0
        assert found.document()["schema"] == "ix.v1.deviations"

    def test_a_comment_or_reordering_is_not_a_deviation(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        """Reproduced in review: appending ``# a comment`` gave a ``file_digest`` deviation
        and blocked ``register``. The digests are over meaning now."""
        file: Path = write_experiment(compare_data)
        ix.register(file)
        reordered = yaml.safe_dump(dict(reversed(list(compare_data.items()))), sort_keys=False)
        file.write_text("# a comment\n" + reordered + "\n# and another\n")
        assert ix.deviations(file).deviations == ()
        assert ix.register(file).outcome == "unchanged"

    @pytest.mark.parametrize(
        ("change", "field"),
        [
            ({"margin": "10%"}, "design.margin.value"),
            ({"margin": "5pp"}, "design.margin.basis"),
            ({"comparison": "equivalence"}, "design.comparison"),
            ({"control": "streaming"}, "design.control"),
            ({"repeats": 9}, "design.repeats"),
            ({"seed": 5}, "design.seed"),
            ({"primary": {"metric": "latency", "statistic": "p99"}}, "design.primary.metric"),
            ({"primary": {"metric": "ttfb", "statistic": "p50"}}, "design.primary.statistic"),
            ({"primary": {"metric": "ttfb", "statistic": "p99", "estimator": "median"}},
             "design.primary.estimator"),
            ({"guardrails": [{"metric": "throughput", "margin": "5%"}]}, "design.guardrails"),
            ({"genre": "regress"}, "design.genre"),
        ],
    )  # fmt: skip
    def test_each_decision_value_deviates_by_name(
        self,
        compare_data: dict[str, Any],
        write_experiment: Write,
        change: dict[str, Any],
        field: str,
    ) -> None:
        """Reviewed defect: only margin, repeats, seed and the primary were frozen, so a
        moved comparison, control or guardrail surfaced only as "the file changed"."""
        file: Path = write_experiment(compare_data)
        ix.register(file)
        write_experiment({**compare_data, **change})
        found = ix.deviations(file)
        fields = [d.field for d in found.deviations]
        assert field in fields, fields
        assert fields[-1] == "plan_digest" and found.exit_code == 14
        named = next(d for d in found.deviations if d.field == field)
        assert named.registered != named.current and "after registration" in named.message

    def test_a_threshold_that_moves_is_named(
        self, evaluate_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(evaluate_data)
        ix.register(file)
        write_experiment({**evaluate_data, "threshold": {"op": ">=", "value": 0.6}})
        moved = next(d for d in ix.deviations(file).deviations if d.field.startswith("design."))
        assert (moved.field, moved.registered, moved.current) == (
            "design.threshold.value", 0.9, 0.6,
        )  # fmt: skip

    def test_a_removed_margin_is_named(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """Regression: values were compared only on paths both records held, so a margin
        that became none (``margin.value`` against ``margin``) was no deviation, and only
        the digests said that the plan changed."""
        registered = examine(compare_data, vocabulary, base=".").design
        changed = {**compare_data, "comparison": "superiority"}
        del changed["margin"]
        now = examine(changed, vocabulary, base=".").design
        assert registered is not None and now is not None
        named = {
            d.field: d
            for d in deviations(
                Registration(experiment="x", design=registered.document()),
                Registration(experiment="x", design=now.document()),
            )
        }
        assert named["design.margin"].current is None
        assert named["design.margin"].registered["value"] == 5.0
        assert named["design.margin"].message == "margin changed after registration"

    @pytest.mark.parametrize(
        ("before", "after"),
        [({"op": ">=", "value": 0.9}, None), (None, {"op": ">=", "value": 0.9})],
    )
    def test_a_threshold_removed_or_added_is_named(self, before: Any, after: Any) -> None:
        def registered(threshold: Any) -> Registration:
            return Registration(experiment="x", design={"threshold": threshold, "repeats": 5})

        (found,) = deviations(registered(before), registered(after))
        assert (found.field, found.registered, found.current) == (
            "design.threshold", before, after,
        )  # fmt: skip

    def test_a_reworded_question_changes_the_declaration_and_not_the_plan(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        ix.register(file)
        write_experiment({**compare_data, "question": "Is streaming any worse?"})
        assert [d.field for d in ix.deviations(file).deviations] == ["experiment_digest"]

    def test_a_changed_probe_file_deviates(
        self, evaluate_data: dict[str, Any], write_experiment: Write
    ) -> None:
        data = {key: value for key, value in evaluate_data.items() if key != "probes"}
        file: Path = write_experiment(data)
        (file.parent / "probes").mkdir()
        probe = file.parent / "probes" / "ask.md"
        probe.write_text("---\nexpectation: must_trigger\n---\nReview this change.\n")
        ix.register(file)
        probe.write_text("---\nexpectation: must_trigger\n---\nReview this change, please.\n")
        assert [d.field for d in ix.deviations(file).deviations] == [
            "probes_digest", "plan_digest",
        ]  # fmt: skip

    def test_a_genre_that_moves_after_registration_is_a_deviation(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        """Reproduced in review: a lab genre extending compare was registered, then its
        design was edited, and ``deviations`` returned nothing: only the genre's name was
        frozen. Everything the genre supplies is frozen now."""
        file: Path = write_experiment({**compare_data, "genre": "lab.v1.genre.gate"})
        (file.parent / "genres").mkdir()
        genre_file = file.parent / "genres" / "gate.yaml"
        gate: dict[str, Any] = {"name": "lab.v1.genre.gate", "extends": "compare"}
        genre_file.write_text(yaml.safe_dump(gate))
        ix.register(file)
        assert ix.deviations(file).deviations == ()

        gate["design"] = {"repeats": {"default": 12, "min": 5}}
        genre_file.write_text(yaml.safe_dump(gate))
        moved = ix.deviations(file)
        assert [d.field for d in moved.deviations] == ["design.repeats", "plan_digest"]
        assert (moved.deviations[0].registered, moved.deviations[0].current) == (10, 12)
        assert ix.plan(file).registration.status == "deviations"

    def test_every_value_a_genre_supplies_is_frozen(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """Change each thing a genre supplies, one at a time, under one unchanged
        experiment: each is a deviation at its own path."""
        from ix.application.experiments import find_deviations, register

        compare = vocabulary.genres.genres["ix.v1.genre.compare"]

        def under(**design: Any) -> Vocabulary:
            changed = compare.model_copy(
                update={
                    key: value for key, value in design.items() if key in ("analysis", "report")
                }
                | {
                    "design": compare.design.model_copy(
                        update={k: v for k, v in design.items() if k not in ("analysis", "report")}
                    )
                }
            )
            return Vocabulary(
                genres=vocabulary.genres.model_copy(
                    update={"genres": {**vocabulary.genres.genres, compare.name: changed}}
                ),
                kinds=vocabulary.kinds,
            )

        examined = examine(compare_data, vocabulary, base=".")
        stored = register(examined, vocabulary, None, location="r", now="t", version="v")
        cases = {
            "design.order": under(order="sequential"),
            "design.calibration": under(calibration="none"),
            "design.primary.estimator": under(estimator="median"),
            # A parameter where the registration held none is not a path both records hold;
            # the plan it changes is.
            "plan_digest": under(
                analysis=compare.analysis.model_copy(update={"params": {"correction": "none"}})
            ),
            "design.analysis.id": under(
                analysis=compare.analysis.model_copy(update={"id": "ix.v1.analysis.other"})
            ),
            "design.report": under(report=("question",)),
        }
        for field, moved in cases.items():
            found = find_deviations(
                examine(compare_data, moved, base="."), moved, stored.registration
            )
            assert field in [d.field for d in found.deviations], field

    def test_checking_an_unregistered_experiment_is_not_found(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        with pytest.raises(ix.NotFoundError) as raised:
            ix.deviations(file)
        assert raised.value.fix == f"ix register {file}"


class TestStoredRecordsTolerateChange:
    """An authored file is strict; a stored record is read generously."""

    def test_a_record_with_fields_this_ix_does_not_know_still_loads(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        """Reproduced in review: one extra key in the registration gave a config error whose
        fix was "restore from version control"."""
        file: Path = write_experiment(compare_data)
        ix.register(file)
        stored = json.loads(_record(file).read_text())
        stored["reviewed_by"] = "someone"
        stored["design"]["blocking"] = "by-cell"
        stored["design"]["primary"]["window"] = "steady"
        _record(file).write_text(json.dumps(stored))
        assert ix.deviations(file).deviations == ()
        assert ix.register(file).outcome == "unchanged"
        assert ix.plan(file).registration.status == "matches"

    def test_a_record_from_an_older_ix_is_compared_on_what_it_holds(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        """A value the old record never stored reads as "not recorded", not as changed."""
        file: Path = write_experiment(compare_data)
        ix.register(file)
        stored = json.loads(_record(file).read_text())
        del stored["plan_digest"], stored["design"]["order"], stored["design"]["sensors"]
        del stored["design"]["primary"]["estimator"], stored["ix_version"]
        _record(file).write_text(json.dumps(stored))
        assert ix.deviations(file).deviations == ()
        write_experiment({**compare_data, "margin": "9%"})
        assert [d.field for d in ix.deviations(file).deviations] == [
            "design.margin.value", "experiment_digest",
        ]  # fmt: skip

    def test_a_record_of_another_schema_major_is_refused_with_what_this_ix_reads(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        _record(file).write_text('{"schema": "ix.v2.registration", "experiment": "proxy"}')
        with pytest.raises(ix.ConfigError, match=r"reads ix\.v1\.registration") as raised:
            ix.deviations(file)
        assert "the ix that wrote it" in (raised.value.fix or "")
        _record(file).write_text("not json")
        with pytest.raises(ix.ConfigError, match="not JSON"):
            ix.register(file)

    def test_an_authored_file_stays_strict(self) -> None:
        with pytest.raises(ValueError, match="extra"):
            Experiment.model_validate({"name": "x", "extra_key": 1})
        assert Registration.model_validate({"experiment": "x", "later": 1}).experiment == "x"

    def test_deviations_between_two_records_ignore_provenance(self) -> None:
        one = Registration(experiment="x", design={"repeats": 5}, registered_at="t1")
        other = one.model_copy(update={"registered_at": "t2", "ix_version": "9"})
        assert deviations(one, other) == ()


class TestAmend:
    def test_amending_keeps_the_superseded_record_and_the_reason(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        original = ix.register(file).registration
        write_experiment({**compare_data, "margin": "10%"})
        amended = ix.register(file, amend="the 5% margin was below what 10 repeats can detect")
        assert amended.outcome == "amended"
        record = amended.registration
        assert record.design["margin"] == {"value": 10.0, "basis": "relative"}
        (amendment,) = record.amendments
        assert amendment.reason == "the 5% margin was below what 10 repeats can detect"
        assert amendment.amended_at
        kept = {k: v for k, v in original.document().items() if k != "amendments"}
        assert amendment.superseded == kept
        assert [d.field for d in amendment.deviations][0] == "design.margin.value"
        assert json.loads(_record(file).read_text()) == record.document()
        assert ix.deviations(file).deviations == ()

    def test_a_second_amendment_keeps_the_whole_history_in_order(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        ix.register(file)
        write_experiment({**compare_data, "margin": "10%"})
        ix.register(file, amend="first")
        write_experiment({**compare_data, "margin": "10%", "repeats": 9})
        record = ix.register(file, amend="second").registration
        assert [a.reason for a in record.amendments] == ["first", "second"]
        margins = [a.superseded["design"]["margin"]["value"] for a in record.amendments]
        assert margins == [5.0, 10.0]
        assert all("amendments" not in a.superseded for a in record.amendments)

    def test_amending_needs_a_reason(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        ix.register(file)
        write_experiment({**compare_data, "margin": "10%"})
        written = _record(file).read_bytes()
        with pytest.raises(ix.ConfigError, match="needs a reason"):
            ix.register(file, amend="   ")
        assert _record(file).read_bytes() == written

    def test_amending_with_nothing_changed_changes_nothing(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        ix.register(file)
        written = _record(file).read_bytes()
        assert ix.register(file, amend="no reason at all").outcome == "unchanged"
        assert _record(file).read_bytes() == written

    def test_there_must_be_a_registration_to_amend(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        with pytest.raises(ix.NotFoundError, match="no registration to amend"):
            ix.register(file, amend="why")
        assert not _record(file).exists()


# --- the library takes an experiment as well as a path ---------------------------------


class TestExperimentObjects:
    def test_an_experiment_built_in_memory_is_validated_planned_and_resolved(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        """Reviewed defect: the records were exported and no function accepted one."""
        experiment = ix.Experiment.model_validate(compare_data)
        assert ix.validate(experiment).valid
        assert ix.validate(experiment).source == "<experiment>"
        file = write_experiment(compare_data)
        assert ix.plan(experiment).digest == ix.plan(file).digest
        assert ix.resolve(experiment) == ix.resolve(file)
        assert ix.load(file) == experiment

    def test_an_invalid_one_is_reported_the_same_way(self, compare_data: dict[str, Any]) -> None:
        del compare_data["margin"]
        experiment = ix.Experiment.model_validate(compare_data)
        assert [p.kind for p in ix.validate(experiment).problems] == ["margin_required"]
        with pytest.raises(ix.InvalidExperimentError) as raised:
            ix.plan(experiment)
        assert "ix.validate(experiment)" in (raised.value.fix or "")

    def test_base_says_where_relative_paths_resolve(
        self, evaluate_data: dict[str, Any], tmp_path: Path
    ) -> None:
        (tmp_path / "probes").mkdir()
        (tmp_path / "probes" / "ask.md").write_text("---\nexpectation: must_trigger\n---\nHi.\n")
        del evaluate_data["probes"]
        experiment = ix.Experiment.model_validate(evaluate_data)
        assert ix.plan(experiment, base=tmp_path).probes.ids == ("ask",)
        assert [p.kind for p in ix.validate(experiment, base=tmp_path / "nowhere").problems] == [
            "probes_empty"
        ]

    def test_registering_one_needs_to_be_told_where(
        self, compare_data: dict[str, Any], tmp_path: Path
    ) -> None:
        experiment = ix.Experiment.model_validate(compare_data)
        with pytest.raises(ix.ConfigError, match="no file to register beside"):
            ix.register(experiment)
        record = tmp_path / "proxy.registration.json"
        assert ix.register(experiment, registration=record).outcome == "created"
        assert ix.deviations(experiment, registration=record).deviations == ()
        assert ix.plan(experiment, registration=record).registration.status == "matches"
        assert ix.plan(experiment).registration.status == "none"


# --- the one port, and binding other adapters ------------------------------------------


class TestTheProbesFolder:
    """Probes not written inline are the files of the ``probes`` folder beside the
    experiment, read through matrix's loader: one probe a file, markdown or YAML."""

    def _lab(self, write: Write, data: dict[str, Any], files: dict[str, str]) -> Path:
        file: Path = write({key: value for key, value in data.items() if key != "probes"})
        (file.parent / "probes").mkdir()
        for name, text in files.items():
            (file.parent / "probes" / name).write_text(text)
        return file

    def test_files_are_read_in_name_order_each_named_by_its_stem(
        self, evaluate_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file = self._lab(
            write_experiment,
            evaluate_data,
            {
                "b-ask.md": "---\nexpectation: must_trigger\nexpected_skill: review\n---\nAsk.\n",
                "a-decoy.yaml": "id: decoy\nexpectation: should_not_trigger\nprompt: Rename x.\n",
                "notes.txt": "not a probe",
            },
        )
        plan = ix.plan(file)
        assert plan.probes.ids == ("decoy", "b-ask")
        design = ix.resolve(file)
        assert design.experiment == "routing"

    def test_an_id_two_files_declare_names_both_files(
        self, evaluate_data: dict[str, Any], write_experiment: Write
    ) -> None:
        ask = "---\nid: ask\nexpectation: must_trigger\nexpected_skill: review\n---\nAsk.\n"
        file = self._lab(write_experiment, evaluate_data, {"one.md": ask, "two.md": ask})
        (problem,) = ix.validate(file).problems
        assert (problem.path, problem.kind, problem.got) == ("id", "name_taken", "ask")
        assert problem.source.endswith("two.md") and "one.md" in problem.message

    def test_inline_probes_leave_the_folder_unread(
        self, evaluate_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(evaluate_data)
        (file.parent / "probes").mkdir()
        (file.parent / "probes" / "broken.md").write_text("---\nid: [\n")
        assert ix.validate(file).valid

    def test_an_experiment_object_reads_the_folder_of_its_base(
        self, evaluate_data: dict[str, Any], tmp_path: Path
    ) -> None:
        (tmp_path / "probes").mkdir()
        (tmp_path / "probes" / "ask.md").write_text(
            "---\nexpectation: must_trigger\nexpected_skill: review\n---\nReview this.\n"
        )
        del evaluate_data["probes"]
        experiment = Experiment.model_validate(evaluate_data)
        assert ix.plan(experiment, base=tmp_path).probes.ids == ("ask",)
        (problem,) = ix.validate(experiment, base=tmp_path / "elsewhere").problems
        assert problem.kind == "probes_empty"

    def test_the_root_can_be_bound_to_pinned_genres_and_kinds(
        self, compare_data: dict[str, Any]
    ) -> None:
        from ix.adapters._out.matrix_documents import GenreFiles, KindFiles
        from ix.composition import Root

        pinned = Root(
            GenreFiles(packages=["ix.builtin.genres"]),
            KindFiles(packages=["ix.builtin.kinds"]),
            version="9.9.9",
            clock=lambda: "2026-10-13T09:00:00+00:00",
        )
        experiment = Experiment.model_validate(compare_data)
        assert pinned.plan(experiment).digest == ix.plan(experiment).digest
        empty = Root(GenreFiles(packages=[]), KindFiles(packages=[]))
        kinds = {p.kind for p in empty.validate(experiment).problems}
        assert kinds == {"name_unknown", "metric_unprovided"}  # the genre and the kinds


class TestSensitivity:
    """What a plan says about the smallest effect a run can detect."""

    def test_without_an_earlier_run_it_is_unknown_and_says_how_to_learn_it(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        made = _plan(compare_data, vocabulary)
        assert made.sensitivity.detectable is None and made.sensitivity.blocks == 10
        assert made.sensitivity.text.startswith("unknown:")
        assert "ix run --exploratory" in made.sensitivity.text

    def test_a_pass_rate_that_cannot_reach_its_threshold_is_said_before_the_run(
        self, evaluate_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """Ten trials, every one passing, show a pass rate only above 0.72: a threshold of
        0.9 cannot hold, and the plan says so with the trials it would take (35)."""
        made = _plan(evaluate_data, vocabulary)
        assert made.sensitivity.text.startswith("This run cannot hold: with 10 trials")
        assert "it takes 35 trials per cell" in made.sensitivity.text
        enough = _plan({**evaluate_data, "repeats": 35}, vocabulary)
        assert not enough.sensitivity.text.startswith("This run cannot hold")

    def test_the_spread_of_an_earlier_run_gives_the_detectable_difference(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        from ix.domain.analysis import Spread
        from ix.domain.intervals import t_quantile
        from ix.domain.plan import sensitivity

        made = _plan(compare_data, vocabulary)
        examined = examine(compare_data, vocabulary, base=".")
        assert examined.design is not None
        spread = Spread(run="r1", metric="ttfb", statistic="p99", unit="%", sd=2.0, n=10)
        found = sensitivity(examined.design, made.sensitivity.blocks, spread)
        expected = (t_quantile(0.975, 9) + t_quantile(0.8, 9)) * 2.0 / 10**0.5
        assert found.detectable == pytest.approx(expected) and found.source == "r1"
        assert "at least 1.99% better than -5%" in found.text and "run r1" in found.text

    def test_the_plan_reads_the_spread_from_the_latest_finished_run(
        self, write_experiment: Write
    ) -> None:
        from ix_fakes import experiment, fake_registry

        from ix.composition import Root

        root = Root(registry=fake_registry())
        file = write_experiment(experiment(variant_ms=50.0))
        before = root.plan(file)
        assert before.sensitivity.text.startswith("unknown:")
        root.run(file, exploratory=True)
        after = root.plan(file)
        sensed = after.sensitivity
        assert sensed.detectable is not None and sensed.source.startswith("fake-compare-")
        assert sensed.unit == "%" and sensed.blocks == 5
        # It changes with the runs, not with what a run would do: the digest stays.
        assert after.digest == before.digest

    def test_a_dry_run_says_nothing_of_how_much_the_subject_varies(
        self, evaluate_data: dict[str, Any], write_experiment: Write
    ) -> None:
        """Regression: the plan took its spread from a dry run, whose canned replies are the
        harness's and not the subject's, and stated what a real run could detect from it."""
        decoy = {
            "id": "decoy",
            "kind": "prompt",
            "prompt": "Rename a variable.",
            "truth": {"expectation": "should_not_trigger", "expected_skill": "review"},
        }
        evaluate_data["probes"].append(decoy)
        for probe in evaluate_data["probes"]:
            probe["mock_response"] = "No skill needed."
        file = write_experiment(evaluate_data)
        report = ix.run(file, exploratory=True)
        assert report.simulated == "all"  # one probe passes and one fails: a spread of 0.5
        sensed = ix.plan(file).sensitivity
        assert sensed.detectable is None and "unknown:" in sensed.text

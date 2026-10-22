"""The experiment record, and its resolution against a genre and the kinds.

One field set serves every genre; an unknown key is refused; resolution gives every subject
a role, picks the sensor for every metric, settles the two reductions (per-trial statistic,
across-trial estimator), renders the hypothesis, and shows where each value came from.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from ix.domain.design import assign_roles, resolve
from ix.domain.experiment import (
    ESTIMATORS,
    STATISTICS,
    Experiment,
    Guardrail,
    Margin,
    ProbeConfig,
    ProbeFile,
    SubjectConfig,
    Threshold,
    probe_set,
)
from ix.domain.problem import InvalidExperimentError
from ix.domain.validation import check, parse

if TYPE_CHECKING:
    from ix.domain.design import Design
    from ix.domain.genre import Genre, Genres
    from ix.domain.kind import Kinds


def _load(data: dict[str, Any]) -> Experiment:
    experiment, problems = parse(data)
    assert experiment is not None, problems
    return experiment


def _genre(genres: Genres, name: str) -> Genre:
    genre = genres.get(name)
    assert genre is not None
    return genre


def _design(data: dict[str, Any], genres: Genres, kinds: Kinds) -> Design:
    experiment = _load(data)
    assert isinstance(experiment.probes, tuple)
    return resolve(
        experiment, _genre(genres, experiment.genre), kinds, probe_set(experiment.probes)
    )


# --- the record ------------------------------------------------------------------------


class TestExperimentRecord:
    def test_the_documents_restart_at_v1(self, compare_data: dict[str, Any]) -> None:
        experiment = _load(compare_data)
        assert experiment.document()["schema"] == "ix.v1.experiment"
        assert _load({**compare_data, "schema": "ix.v1.experiment"}) == experiment

    def test_another_schema_is_named_as_unsupported(self, compare_data: dict[str, Any]) -> None:
        _, problems = parse({**compare_data, "schema": "ix.v2.experiment"})
        (problem,) = problems
        assert (problem.path, problem.kind) == ("schema", "schema_unsupported")
        assert problem.allowed == ("ix.v1.experiment",) and problem.got == "ix.v2.experiment"

    def test_document_round_trips(self, compare_data: dict[str, Any]) -> None:
        experiment = _load(compare_data)
        assert Experiment.model_validate(experiment.document()) == experiment

    def test_the_plain_names_belong_to_the_records(self, compare_data: dict[str, Any]) -> None:
        experiment = _load(compare_data)
        assert isinstance(experiment.subjects["buffered"], SubjectConfig)
        assert isinstance(experiment.probes, tuple) and isinstance(
            experiment.probes[0], ProbeConfig
        )

    def test_subjects_keep_the_order_written(self, compare_data: dict[str, Any]) -> None:
        compare_data["subjects"] = {
            name: {"kind": "process", "command": ["./p"]} for name in ("zeta", "alpha", "mid")
        }
        assert list(_load(compare_data).subjects) == ["zeta", "alpha", "mid"]

    def test_a_kinds_options_are_kept_apart_from_ix_keys(
        self, compare_data: dict[str, Any]
    ) -> None:
        compare_data["subjects"]["buffered"] = {
            "kind": "process",
            "description": "the current build",
            "effects": ["binds :8080"],
            "command": ["./proxy"],
        }
        subject = _load(compare_data).subjects["buffered"]
        assert (subject.kind, subject.effects) == ("process", ("binds :8080",))
        assert subject.options == {"command": ["./proxy"]}

    def test_truth_is_a_field_of_the_probe_not_an_option(
        self, evaluate_data: dict[str, Any]
    ) -> None:
        experiment = _load(evaluate_data)
        assert isinstance(experiment.probes, tuple)
        probe = experiment.probes[0]
        assert probe.truth == {"expectation": "must_trigger", "expected_skill": "review"}
        assert probe.options == {"prompt": "Review this change."}

    def test_probes_left_out_are_read_from_the_folder_by_convention(
        self, compare_data: dict[str, Any]
    ) -> None:
        """No probes written: the record holds none, and the folder beside the file is read."""
        del compare_data["probes"]
        assert _load(compare_data).probes == ()

    def test_a_probe_files_keys_beside_its_own_are_its_truth(self) -> None:
        """The body is the prompt; id, kind, options and effects are the probe's; every other
        key is truth, beside what is written under truth:."""
        probe = ProbeFile.model_validate(
            {
                "id": "vague",
                "expectation": "must_trigger",
                "truth": {"expected_skill": "hardening"},
                "options": {"timeout": "30s"},
                "body": "Make onboarding better.\n",
            }
        ).config()
        assert (probe.id, probe.kind) == ("vague", "prompt")
        assert probe.truth == {"expectation": "must_trigger", "expected_skill": "hardening"}
        assert probe.options == {"timeout": "30s", "prompt": "Make onboarding better.\n"}

    def test_a_probe_file_of_another_kind_has_no_prompt_unless_it_has_text(self) -> None:
        probe = ProbeFile.model_validate({"kind": "http-load", "target": "x"}).config()
        assert "prompt" not in probe.options and probe.truth == {"target": "x"}

    def test_the_environment_names_its_kind_like_everything_else(
        self, compare_data: dict[str, Any]
    ) -> None:
        assert _load(compare_data).environment.kind == "none"
        compare_data["environment"] = {"kind": "compose", "file": "compose.yaml"}
        environment = _load(compare_data).environment
        assert (environment.kind, environment.options) == ("compose", {"file": "compose.yaml"})
        _, problems = parse({**compare_data, "environment": {"provider": "compose"}})
        assert problems == []  # 'provider' is now an option; the kind check refuses it

    @pytest.mark.parametrize(
        ("text", "value", "basis"),
        [("5%", 5.0, "relative"), ("10pp", 10.0, "points"), (0.5, 0.5, "absolute"),
         ("2.5", 2.5, "absolute"), ("0%", 0.0, "relative")],
    )  # fmt: skip
    def test_margin_forms(self, text: Any, value: float, basis: str) -> None:
        margin = Margin.model_validate(text)
        assert (margin.value, margin.basis) == (value, basis)
        assert Margin.model_validate(margin.text()) == margin

    @pytest.mark.parametrize("text", ["five percent", "-5%", "5 percent", True])
    def test_a_malformed_margin_is_refused(self, text: Any) -> None:
        with pytest.raises(ValidationError):
            Margin.model_validate(text)

    def test_a_threshold_reads_a_quantity(self) -> None:
        threshold = Threshold.model_validate({"op": "<", "value": "300ms"})
        assert (threshold.op, threshold.value, threshold.unit) == ("<", 300.0, "ms")
        assert threshold.text() == "< 300 ms"
        assert Threshold.model_validate({"op": ">=", "value": 0.9}).text() == ">= 0.9"
        with pytest.raises(ValidationError, match="unit once"):
            Threshold.model_validate({"op": "<", "value": "300ms", "unit": "s"})

    def test_a_guardrail_is_a_margin_or_a_threshold_never_both_or_neither(self) -> None:
        assert Guardrail.model_validate({"metric": "m", "margin": "5%"}).threshold is None
        bounded = Guardrail.model_validate({"metric": "m", "threshold": {"op": "<", "value": 1}})
        assert bounded.margin is None
        for bad in (
            {"metric": "m"},
            {"metric": "m", "margin": 1, "threshold": {"op": "<", "value": 1}},
        ):
            with pytest.raises(ValidationError, match="exactly one"):
                Guardrail.model_validate(bad)

    def test_inline_probes_get_ids_and_a_digest(self) -> None:
        probes = (ProbeConfig(kind="prompt"), ProbeConfig(id="named", kind="prompt"))
        pinned = probe_set(probes)
        assert pinned.ids == ("probe-1", "named")
        assert pinned.digest == probe_set(probes).digest
        changed = probe_set((ProbeConfig(kind="prompt", options={"prompt": "x"}), probes[1]))
        retruthed = probe_set((ProbeConfig(kind="prompt", truth={"expectation": "x"}), probes[1]))
        assert len({pinned.digest, changed.digest, retruthed.digest}) == 3


class TestDigestIsOverMeaning:
    def test_key_order_and_written_out_defaults_change_nothing(
        self, compare_data: dict[str, Any]
    ) -> None:
        one = _load(compare_data)
        reordered = _load(dict(reversed(list(compare_data.items()))))
        spelled_out = _load(
            {**compare_data, "schema": "ix.v1.experiment", "rationale": "", "guardrails": [],
             "factors": {}, "environment": {"kind": "none"}, "control": None}
        )  # fmt: skip
        assert one.digest() == reordered.digest() == spelled_out.digest()
        assert one.digest(operative=True) == spelled_out.digest(operative=True)

    def test_a_field_ix_adds_later_with_a_default_changes_no_digest(
        self, compare_data: dict[str, Any]
    ) -> None:
        """Reproduced in review: the digest hashed the full dump, defaults included, so a new
        defaulted field would have changed every unchanged file's digest."""

        class LaterExperiment(Experiment):
            blocking: str = "none"
            warmup_trials: int = 0

        data = {k: v for k, v in compare_data.items()}
        now = Experiment.model_validate(data)
        later = LaterExperiment.model_validate(data)
        assert set(later.document()) - set(now.document()) == {"blocking", "warmup_trials"}
        assert later.digest() == now.digest()
        assert later.digest(operative=True) == now.digest(operative=True)

    def test_a_changed_value_changes_it(self, compare_data: dict[str, Any]) -> None:
        one = _load(compare_data)
        assert _load({**compare_data, "margin": "6%"}).digest() != one.digest()
        assert _load({**compare_data, "repeats": 5}).digest() != one.digest()

    def test_prose_is_in_the_declaration_and_outside_what_a_run_would_do(
        self, compare_data: dict[str, Any]
    ) -> None:
        one = _load(compare_data)
        reworded = dict(compare_data, question="Does streaming hold up?", rationale="A hunch.")
        reworded["subjects"]["buffered"]["description"] = "the build in production"
        other = _load(reworded)
        assert other.digest() != one.digest()
        assert other.digest(operative=True) == one.digest(operative=True)


class TestFileProblems:
    """What a file that is not an experiment is told, each with a path and a fix."""

    def test_unknown_key(self, compare_data: dict[str, Any]) -> None:
        _, problems = parse({**compare_data, "repeets": 5})
        assert [(p.path, p.kind) for p in problems] == [("repeets", "unknown_key")]
        assert "repeets" in problems[0].fix

    @pytest.mark.parametrize(
        "old", ["rule", "bar", "guards", "hypothesis", "arms", "trials", "engine"]
    )
    def test_names_that_were_replaced_are_not_accepted(
        self, compare_data: dict[str, Any], old: str
    ) -> None:
        _, problems = parse({**compare_data, old: "x"})
        assert [(p.path, p.kind) for p in problems] == [(old, "unknown_key")]

    def test_missing_keys_are_all_reported(self) -> None:
        _, problems = parse({"name": "x"})
        assert {(p.path, p.kind) for p in problems} == {
            ("question", "missing_key"),
            ("genre", "missing_key"),
            ("subjects", "missing_key"),
        }  # fmt: skip  (probes may be left out: they are then the probes/ folder's)

    def test_a_nested_path_names_the_key_the_author_wrote(
        self, compare_data: dict[str, Any]
    ) -> None:
        del compare_data["subjects"]["streaming"]["kind"]
        compare_data["probes"][0].pop("kind")
        compare_data["guardrails"] = [{"metric": "throughput"}]
        _, problems = parse(compare_data)
        assert {(p.path, p.kind) for p in problems} == {
            ("subjects.streaming.kind", "missing_key"),
            ("probes.0.kind", "missing_key"),
            ("guardrails.0", "invalid_value"),
        }

    def test_a_closed_choice_lists_what_is_allowed_and_what_was_written(
        self, compare_data: dict[str, Any]
    ) -> None:
        compare_data["threshold"] = {"op": "=<", "value": 3}
        (problem,) = parse(compare_data)[1]
        assert (problem.path, problem.kind, problem.got) == ("threshold.op", "invalid_value", "=<")
        assert problem.allowed == ("<", "<=", ">", ">=")
        assert problem.fix == "op: one of <, <=, >, >="

    def test_a_bad_name_is_explained_without_a_regex(self, compare_data: dict[str, Any]) -> None:
        (problem,) = parse({**compare_data, "name": "two words"})[1]
        assert (problem.path, problem.kind, problem.got) == ("name", "invalid_value", "two words")
        assert "one token" in problem.message and "^" not in problem.message
        assert "'two-words'" in problem.fix

    @pytest.mark.parametrize(
        "change",
        [{"repeats": 0}, {"seed": -1}, {"factors": {"size": []}}, {"factors": {"size": [1, 1]}},
         {"subjects": {}}, {"metrics": ["ttfb", "ttfb"]}],
    )  # fmt: skip
    def test_invalid_values(self, compare_data: dict[str, Any], change: dict[str, Any]) -> None:
        experiment, problems = parse({**compare_data, **change})
        assert experiment is None
        assert {p.kind for p in problems} == {"invalid_value"}
        assert all(p.fix and "correct the value" not in p.fix for p in problems)

    def test_subjects_written_as_a_list(self, compare_data: dict[str, Any]) -> None:
        """A likely slip, answered with the shape that fixes it."""
        compare_data["subjects"] = [{"name": "buffered", "kind": "process"}]
        (problem,) = parse(compare_data)[1]
        assert (problem.path, problem.kind) == ("subjects", "wrong_shape")
        assert "mapping" in problem.message
        assert "subjects: {<name>: {kind: <subject kind>, ...}}" in problem.fix

    def test_probes_written_as_one_mapping(self, compare_data: dict[str, Any]) -> None:
        """The other likely slip, answered with both ways to write probes."""
        compare_data["probes"] = {"kind": "prompt", "prompt": "Hello"}
        (problem,) = parse(compare_data)[1]
        assert (problem.path, problem.kind) == ("probes", "wrong_shape")
        assert "a list" in problem.fix and "probes/ folder" in problem.fix

    def test_not_a_mapping(self) -> None:
        (problem,) = parse(["name", "x"])[1]
        assert problem.kind == "wrong_shape"


# --- roles -----------------------------------------------------------------------------


class TestRoles:
    def test_the_first_subject_is_the_control(
        self, compare_data: dict[str, Any], builtin_genres: Genres
    ) -> None:
        roles, control, problems = assign_roles(
            _load(compare_data), _genre(builtin_genres, "compare")
        )
        assert problems == [] and control == "buffered"
        assert [(r.subject, r.role) for r in roles] == [
            ("buffered", "control"), ("streaming", "variant"),
        ]  # fmt: skip

    def test_control_names_another_subject(
        self, compare_data: dict[str, Any], builtin_genres: Genres
    ) -> None:
        compare_data["control"] = "streaming"
        roles, control, _ = assign_roles(_load(compare_data), _genre(builtin_genres, "compare"))
        assert control == "streaming"
        assert [(r.subject, r.role) for r in roles] == [
            ("buffered", "variant"), ("streaming", "control"),
        ]  # fmt: skip

    def test_one_control_and_many_variants(
        self, compare_data: dict[str, Any], builtin_genres: Genres
    ) -> None:
        for name in ("zero-copy", "chunked", "mmap"):
            compare_data["subjects"][name] = {"kind": "process", "command": ["./proxy", name]}
        roles, _, problems = assign_roles(_load(compare_data), _genre(builtin_genres, "compare"))
        assert problems == []
        assert [r.role for r in roles] == ["control", "variant", "variant", "variant", "variant"]

    def test_compare_with_one_subject_has_no_variant(
        self, compare_data: dict[str, Any], builtin_genres: Genres
    ) -> None:
        del compare_data["subjects"]["streaming"]
        _, _, problems = assign_roles(_load(compare_data), _genre(builtin_genres, "compare"))
        assert [(p.path, p.kind) for p in problems] == [("subjects", "genre_roles")]
        assert "variant" in problems[0].message and "2 or more" in problems[0].fix

    def test_regress_takes_exactly_one_variant(
        self, compare_data: dict[str, Any], builtin_genres: Genres
    ) -> None:
        compare_data["subjects"]["third"] = {"kind": "process"}
        _, _, problems = assign_roles(_load(compare_data), _genre(builtin_genres, "regress"))
        assert [p.kind for p in problems] == ["genre_roles"]
        assert "exactly 2" in problems[0].fix

    def test_a_control_that_is_not_a_subject(
        self, compare_data: dict[str, Any], builtin_genres: Genres
    ) -> None:
        compare_data["control"] = "bufered"
        _, control, problems = assign_roles(_load(compare_data), _genre(builtin_genres, "compare"))
        assert control is None
        (problem,) = problems
        assert (problem.path, problem.kind, problem.got) == (
            "control",
            "name_unknown",
            "bufered",
        )
        assert problem.allowed == ("buffered", "streaming")

    def test_control_in_a_genre_without_one(
        self, evaluate_data: dict[str, Any], builtin_genres: Genres
    ) -> None:
        evaluate_data["control"] = "catalog"
        roles, control, problems = assign_roles(
            _load(evaluate_data), _genre(builtin_genres, "evaluate")
        )
        assert control is None and [r.role for r in roles] == ["subject"]
        assert [(p.path, p.kind) for p in problems] == [("control", "control_not_applicable")]


# --- resolution ------------------------------------------------------------------------


class TestResolution:
    def test_every_value_the_genre_supplies_is_visible_and_attributed(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        genre = _genre(builtin_genres, "compare")
        design = _design(compare_data, builtin_genres, builtin_kinds)
        sourced = {value.path: value for value in design.provenance}

        assert design.repeats == genre.design.repeats.default
        assert (design.order, design.calibration) == (genre.design.order, "aa")
        for path in ("repeats", "order", "calibration", "primary.estimator"):
            assert sourced[path].source == "genre", path
        assert (sourced["control"].source, sourced["control"].value) == ("ix", "buffered")
        assert sourced["seed"].source == "ix"
        assert sourced["comparison"].source == "experiment"
        assert sourced["primary.statistic"].source == "experiment"
        assert design.document()["schema"] == "ix.v1.design"
        assert design.genre == genre.name
        assert (design.analysis, design.report) == (genre.analysis, genre.report)

    def test_what_the_author_wrote_wins_and_is_attributed_to_them(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        compare_data.update(repeats=7, seed=11, control="streaming")
        design = _design(compare_data, builtin_genres, builtin_kinds)
        assert (design.repeats, design.seed, design.control) == (7, 11, "streaming")
        sources = {value.path: value.source for value in design.provenance}
        assert sources["repeats"] == sources["seed"] == sources["control"] == "experiment"

    def test_a_genre_supplies_the_comparison_it_fixes(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        del compare_data["comparison"]
        compare_data["genre"] = "regress"
        design = _design(compare_data, builtin_genres, builtin_kinds)
        assert design.comparison == "non-inferiority"
        (comparison,) = [v for v in design.provenance if v.path == "comparison"]
        assert comparison.source == "genre"

    def test_the_seed_is_fixed_by_the_name_when_none_is_stated(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        one = _design(compare_data, builtin_genres, builtin_kinds).seed
        assert _design({**compare_data, "margin": "9%"}, builtin_genres, builtin_kinds).seed == one
        assert _design({**compare_data, "name": "other"}, builtin_genres, builtin_kinds).seed != one

    def test_the_design_holds_the_sensors_and_the_metric_declarations(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        design = _design(compare_data, builtin_genres, builtin_kinds)
        assert [(s.kind, s.metrics) for s in design.sensors] == [("ix.v1.sensor.timing", ("ttfb",))]
        ttfb = design.metric("ttfb")
        assert ttfb is not None
        assert (ttfb.unit, ttfb.direction, ttfb.shape, ttfb.judged) == (
            "ms", "lower", "histogram", False,
        )  # fmt: skip

    def test_subjects_that_do_not_fit_stop_resolution(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        compare_data["genre"] = "evaluate"
        with pytest.raises(InvalidExperimentError) as raised:
            _design(compare_data, builtin_genres, builtin_kinds)
        assert [p.kind for p in raised.value.problems] == ["genre_roles"]
        assert raised.value.kind == "config" and raised.value.fix


class TestTwoReductions:
    """``statistic`` reduces one trial; ``estimator`` combines the trials."""

    def test_the_tables_keep_the_two_apart(self) -> None:
        assert STATISTICS["boolean"] == ("value",) == STATISTICS["scalar"]
        assert "p99" in STATISTICS["histogram"] and "p99" not in ESTIMATORS["histogram"]
        assert ESTIMATORS["boolean"] == ("rate",)
        assert "rate" not in STATISTICS["boolean"]

    def test_a_histogram_is_reduced_per_trial_then_combined_by_the_genres_estimator(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        design = _design(compare_data, builtin_genres, builtin_kinds)
        assert design.primary is not None
        assert (design.primary.statistic, design.primary.estimator) == ("p99", "mean")
        sources = {v.path: v.source for v in design.provenance}
        assert sources["primary.statistic"] == "experiment"
        assert sources["primary.estimator"] == "genre"

    def test_the_statistic_defaults_by_shape(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        compare_data["primary"] = {"metric": "ttfb"}
        compare_data["guardrails"] = [{"metric": "memory_peak", "estimator": "max", "margin": 0}]
        design = _design(compare_data, builtin_genres, builtin_kinds)
        assert design.primary is not None and design.primary.statistic == "p50"
        guardrail = design.guardrails[0]
        assert (guardrail.statistic, guardrail.estimator) == ("value", "max")
        sources = {v.path: v.source for v in design.provenance}
        assert sources["primary.statistic"] == "ix"
        assert sources["guardrails.0.estimator"] == "experiment"

    def test_a_boolean_combines_as_a_rate_whatever_the_genre_says(
        self, evaluate_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        design = _design(evaluate_data, builtin_genres, builtin_kinds)
        assert design.primary is not None
        assert (design.primary.statistic, design.primary.estimator) == ("value", "rate")
        sources = {v.path: v.source for v in design.provenance}
        assert sources["primary.estimator"] == "ix"

    def test_an_estimator_written_as_a_statistic_is_told_where_it_goes(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        compare_data["guardrails"] = [{"metric": "throughput", "statistic": "mean", "margin": 5}]
        experiment = _load(compare_data)
        assert isinstance(experiment.probes, tuple)
        (problem,) = check(
            experiment, builtin_genres, builtin_kinds, probe_set(experiment.probes)
        ).problems
        assert (problem.path, problem.kind) == ("guardrails.0.statistic", "statistic_illegal")
        assert problem.allowed == ("value",)
        assert problem.fix == "remove 'statistic' and write estimator: mean"

    def test_an_estimator_the_shape_does_not_have(
        self, evaluate_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        evaluate_data["primary"] = {"metric": "activated_correctly", "estimator": "median"}
        experiment = _load(evaluate_data)
        assert isinstance(experiment.probes, tuple)
        (problem,) = check(
            experiment, builtin_genres, builtin_kinds, probe_set(experiment.probes)
        ).problems
        assert (problem.path, problem.kind) == ("primary.estimator", "estimator_illegal")
        assert problem.allowed == ("rate",) and problem.got == "median"


class TestHypothesis:
    """The hypothesis is rendered from the genre and the decision values, one per variant."""

    def test_a_comparison_renders_its_own_sentence(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        design = _design(compare_data, builtin_genres, builtin_kinds)
        assert design.hypotheses == ("streaming is at most 5% worse than buffered on ttfb p99",)
        better = _design(
            {**compare_data, "comparison": "superiority"}, builtin_genres, builtin_kinds
        )
        assert better.hypotheses == ("streaming is better than buffered on ttfb p99",)

    def test_one_sentence_per_variant(
        self, compare_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        compare_data["subjects"]["chunked"] = {
            "kind": "process", "command": ["./proxy", "--chunked"], "url": "http://127.0.0.1:8080",
        }  # fmt: skip
        design = _design(compare_data, builtin_genres, builtin_kinds)
        assert [h.split(" is ")[0] for h in design.hypotheses] == ["streaming", "chunked"]

    def test_a_threshold_genre_renders_the_threshold(
        self, evaluate_data: dict[str, Any], builtin_genres: Genres, builtin_kinds: Kinds
    ) -> None:
        design = _design(evaluate_data, builtin_genres, builtin_kinds)
        assert design.hypotheses == ("catalog has activated_correctly >= 0.9",)

    def test_the_experiment_has_no_free_text_hypothesis_to_drift(
        self, compare_data: dict[str, Any]
    ) -> None:
        assert "hypothesis" not in Experiment.model_fields
        assert _load({**compare_data, "rationale": "Streaming holds less."}).rationale

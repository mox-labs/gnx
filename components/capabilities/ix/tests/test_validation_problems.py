"""``validate`` reports every problem in one batch, each with path, kind, message, fix, the
allowed values and what was written.

One case per problem kind, and a check that no kind in the published vocabulary lacks one.
The rule that is not a genre's to relax gets its own class: a model-judged metric never
decides, and whether a metric is judged is its sensor's word, not the author's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import yaml

import ix
from ix.application.experiments import Vocabulary, examine, validate
from ix.domain.problem import PROBLEM_KINDS, Problem, problem

if TYPE_CHECKING:
    from pathlib import Path

Write = Any  # the write_experiment fixture: (data, directory="lab") -> Path
Pairs = set[tuple[str, str]]


def _problems(data: dict[str, Any], vocabulary: Vocabulary) -> tuple[Problem, ...]:
    return examine(data, vocabulary, base=".").problems


def _found(data: dict[str, Any], vocabulary: Vocabulary) -> Pairs:
    return {(p.path, p.kind) for p in _problems(data, vocabulary)}


def _well_formed(problems: tuple[Problem, ...] | list[Problem]) -> None:
    for found in problems:
        assert found.kind in PROBLEM_KINDS, found
        assert found.message and found.fix, found


def test_the_baselines_are_valid(
    compare_data: dict[str, Any], evaluate_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    for data in (compare_data, evaluate_data):
        examined = examine(data, vocabulary, base=".")
        assert examined.problems == () and examined.warnings == ()
        assert examined.complete and examined.design is not None
        assert validate(examined).valid


def test_an_invalid_experiment_has_no_design(
    compare_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    del compare_data["margin"]
    examined = examine(compare_data, vocabulary, base=".")
    assert examined.problems and examined.design is None


def test_a_problem_kind_outside_the_vocabulary_cannot_be_made() -> None:
    with pytest.raises(ValueError, match="not a problem kind"):
        problem("x", "made_up", "nope")


def test_a_fix_is_built_from_what_is_allowed() -> None:
    found = problem("design.order", "invalid_value", "bad", allowed=["a", "b"], got="c")
    assert found.fix == "order: one of a, b" and found.allowed == ("a", "b")


# --- one case per kind -----------------------------------------------------------------
# Each case returns the (path, kind) pairs validate reported for one deliberate mistake.
# c: a valid compare experiment; e: a valid evaluate experiment; v: the vocabulary.


def _api(write: Write, data: Any) -> Pairs:
    result = ix.validate(write(data))
    _well_formed(result.problems)
    _well_formed(result.warnings)
    return {(p.path, p.kind) for p in (*result.problems, *result.warnings)}


def _lab(write: Write, data: dict[str, Any], directory: str, *records: dict[str, Any]) -> Pairs:
    file = write(data)
    (file.parent / directory).mkdir()
    for index, record in enumerate(records):
        (file.parent / directory / f"f{index}.yaml").write_text(yaml.safe_dump(record))
    result = ix.validate(file)
    _well_formed(result.problems)
    _well_formed(result.warnings)
    return {(p.path, p.kind) for p in (*result.problems, *result.warnings)}


_PARENT = {
    "name": "lab.v1.genre.base",
    "question": "?",
    "hypothesis_form": "{subject} on {metric}",
    "roles": [{"name": "subject", "min": 1, "max": 1}],
    "factors": "forbidden",
    "design": {
        "repeats": {"default": 3, "min": 2},
        "order": "interleaved",
        "calibration": "none",
        "note": "a test design",
    },
    "analysis": {"id": "lab.v1.analysis.base"},
    "verdict_form": "none",
    "report": ["question"],
    "guidance": "Write it.",
}
_LOOSE = {"name": "lab.v1.genre.loose", "extends": "compare", "must_set": []}


def _yaml_syntax(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    file = write(c)
    file.write_text("name: x\n  question: [unclosed\n")
    return {(p.path, p.kind) for p in ix.validate(file).problems}


def _schema_unsupported(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _api(write, {**c, "schema": "ix.v9.experiment"})


def _wrong_shape(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _api(write, {**c, "subjects": [{"name": "a", "kind": "process"}]})


def _unknown_key(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _api(write, {**c, "arms": {}})


def _missing_key(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    del c["question"]
    return _api(write, c)


def _invalid_value(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _api(write, {**c, "repeats": "many"})


def _genre_unknown(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _found({**c, "genre": "benchmark"}, v)


def _genre_roles(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    del c["subjects"]["streaming"]
    return _found(c, v)


def _genre_factors(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _found({**e, "genre": "characterize", "threshold": None}, v)


def _must_set(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    del e["threshold"]
    return _found(e, v)


def _repeats_below_min(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _found({**c, "repeats": 2}, v)


def _control_unknown(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _found({**c, "control": "bufered"}, v)


def _control_not_applicable(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _found({**e, "control": "catalog"}, v)


def _comparison_not_allowed(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _found({**c, "comparison": "bayesian"}, v)


def _margin_required(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    del c["margin"]
    return _found(c, v)


def _guardrail_form(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _found({**e, "guardrails": [{"metric": "tokens_total", "margin": "5%"}]}, v)


def _kind_unknown(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    c["probes"][0]["kind"] = "http-laod"
    return _found(c, v)


def _kind_unfit(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    # A load probe needs a url; an agent offers none. (The primary still has its sensor:
    # timing reads what the load probe leaves behind.)
    c["subjects"] = {
        "old": {"kind": "agent", "model": "a"},
        "new": {"kind": "agent", "model": "b"},
    }
    c["probes"][0]["target"] = "http://127.0.0.1:8080/x"
    return _found({**c, "spend": {"ceiling": 1}}, v)


def _option_unknown(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    c["probes"][0]["durration"] = "5s"
    return _found(c, v)


def _option_missing(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    del c["probes"][0]["duration"]
    return _found(c, v)


def _option_invalid(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    c["probes"][0]["load_model"] = "sideways"
    return _found(c, v)


def _metric_unprovided(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _found({**c, "primary": {"metric": "latencyy"}}, v)


def _metric_ambiguous(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    twin = {
        "name": "lab.v1.sensor.twin",
        "summary": "s",
        "needs": ["load"],
        "metrics": [{"name": "ttfb", "direction": "lower", "shape": "histogram"}],
    }
    return _lab(write, c, "kinds", twin)


def _metric_mismatch(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _found({**c, "metrics": [{"name": "ttfb", "shape": "scalar"}]}, v)


def _metric_direction(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    drift = {
        "name": "lab.v1.sensor.drift",
        "summary": "s",
        "needs": ["load"],
        "metrics": [{"name": "drift", "direction": "neither", "shape": "scalar"}],
    }
    return _lab(write, {**c, "primary": {"metric": "drift"}}, "kinds", drift)


def _statistic_illegal(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _found({**e, "primary": {"metric": "activated_correctly", "statistic": "p99"}}, v)


def _estimator_illegal(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _found({**c, "primary": {"metric": "ttfb", "estimator": "rate"}}, v)


def _judged_gate(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    del e["probes"][0]["truth"]  # nothing reads it once activation is not in use
    e["primary"] = {"metric": "judged_pass"}
    e["sensors"] = [{"kind": "judge", "rubric": "rubric.md"}]
    return _found(e, v)


def _unit_mismatch(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    e["metrics"] = ["tokens_total"]
    e["guardrails"] = [{"metric": "tokens_total", "threshold": {"op": "<", "value": "5s"}}]
    return _found(e, v)


def _probes_empty(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _found({**c, "probes": []}, v) - {("primary.metric", "metric_unprovided")}


def _probe_source(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _api(write, {**e, "probes": {"source": "directory", "path": "nowhere"}})


def _probe_duplicate_id(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _found({**e, "probes": [e["probes"][0], e["probes"][0]]}, v)


def _truth_unclaimed(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    e["probes"][0]["truth"]["expected_skil"] = "review"
    return _found(e, v)


def _unbound_placeholder(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    c["probes"][0]["target"] = "{url}/blobs/{size}"
    return _found(c, v)


def _spend_over_ceiling(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _found({**e, "spend": {"ceiling": 1.0, "per_trial": 0.5}}, v)


def _spend_unbounded(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _found({**e, "spend": {"per_trial": 0.5}}, v)


def _genre_invalid(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    broken = {**_PARENT, "name": "lab.v1.genre.broken", "must_set": ["sensor"]}
    return _lab(write, {**e, "genre": "lab.v1.genre.broken"}, "genres", broken)


def _genre_duplicate(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _lab(write, {**e, "genre": "lab.v1.genre.base"}, "genres", _PARENT, _PARENT)


def _genre_extends_unknown(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    orphan = {"name": "lab.v1.genre.orphan", "extends": "lab.v1.genre.gone"}
    return _lab(write, {**e, "genre": "lab.v1.genre.orphan"}, "genres", orphan)


def _genre_depth(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    deep = {"name": "lab.v1.genre.deep", "extends": "regress"}
    return _lab(write, {**c, "genre": "lab.v1.genre.deep"}, "genres", deep)


def _genre_narrowing(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _lab(write, {**c, "genre": "lab.v1.genre.loose"}, "genres", _LOOSE)


def _kind_invalid(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    file = write(c)
    (file.parent / "kinds").mkdir()
    (file.parent / "kinds" / "bad.yaml").write_text("name: lab.v1.sensor.bad\nsummary: s\n")
    rejected = ix.kinds(file.parent).rejected
    return {(p.path, p.kind) for problems in rejected.values() for p in problems}


def _kind_duplicate(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    file = write(c)
    (file.parent / "kinds").mkdir()
    twin = {"name": "ix.v1.environment.none", "summary": "Another nothing."}
    (file.parent / "kinds" / "none.yaml").write_text(yaml.safe_dump(twin))
    rejected = ix.kinds(file.parent).rejected
    return {(p.path, p.kind) for problems in rejected.values() for p in problems}


def _subjects_differ_in(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    c["subjects"]["streaming"]["ready"] = "http://127.0.0.1:9090/healthz"
    return {(p.path, p.kind) for p in examine(c, v, base=".").warnings}


def _genre_rejected(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _lab(write, c, "genres", _LOOSE)


def _kind_rejected(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _lab(write, c, "kinds", {"name": "lab.v1.sensor.bad", "summary": "s"})


CASES: dict[str, tuple[Any, str]] = {
    "yaml_syntax": (_yaml_syntax, ""),
    "schema_unsupported": (_schema_unsupported, "schema"),
    "wrong_shape": (_wrong_shape, "subjects"),
    "unknown_key": (_unknown_key, "arms"),
    "missing_key": (_missing_key, "question"),
    "invalid_value": (_invalid_value, "repeats"),
    "genre_unknown": (_genre_unknown, "genre"),
    "genre_roles": (_genre_roles, "subjects"),
    "genre_factors": (_genre_factors, "factors"),
    "must_set": (_must_set, "threshold"),
    "repeats_below_min": (_repeats_below_min, "repeats"),
    "control_unknown": (_control_unknown, "control"),
    "control_not_applicable": (_control_not_applicable, "control"),
    "comparison_not_allowed": (_comparison_not_allowed, "comparison"),
    "margin_required": (_margin_required, "margin"),
    "guardrail_form": (_guardrail_form, "guardrails.0.margin"),
    "kind_unknown": (_kind_unknown, "probes.0.kind"),
    "kind_unfit": (_kind_unfit, "probes.0.kind"),
    "option_unknown": (_option_unknown, "probes.0.durration"),
    "option_missing": (_option_missing, "probes.0.duration"),
    "option_invalid": (_option_invalid, "probes.0.load_model"),
    "metric_unprovided": (_metric_unprovided, "primary.metric"),
    "metric_ambiguous": (_metric_ambiguous, "primary.metric"),
    "metric_mismatch": (_metric_mismatch, "metrics.0.shape"),
    "metric_direction": (_metric_direction, "primary.metric"),
    "statistic_illegal": (_statistic_illegal, "primary.statistic"),
    "estimator_illegal": (_estimator_illegal, "primary.estimator"),
    "judged_gate": (_judged_gate, "primary.metric"),
    "unit_mismatch": (_unit_mismatch, "guardrails.0.threshold.value"),
    "probes_empty": (_probes_empty, "probes"),
    "probe_source": (_probe_source, "probes.path"),
    "probe_duplicate_id": (_probe_duplicate_id, "probes"),
    "truth_unclaimed": (_truth_unclaimed, "probes.0.truth.expected_skil"),
    "unbound_placeholder": (_unbound_placeholder, "probes.0.target"),
    "spend_over_ceiling": (_spend_over_ceiling, "spend.ceiling"),
    "spend_unbounded": (_spend_unbounded, "spend.ceiling"),
    "genre_invalid": (_genre_invalid, ""),
    "genre_duplicate": (_genre_duplicate, "name"),
    "genre_extends_unknown": (_genre_extends_unknown, "extends"),
    "genre_depth": (_genre_depth, "extends"),
    "genre_narrowing": (_genre_narrowing, "must_set"),
    "kind_invalid": (_kind_invalid, ""),
    "kind_duplicate": (_kind_duplicate, "name"),
    "subjects_differ_in": (_subjects_differ_in, "subjects.streaming"),
    "genre_rejected": (_genre_rejected, ""),
    "kind_rejected": (_kind_rejected, ""),
}

#: Cases that go through a refused genre or kind file, where the file's own problems come
#: along with the experiment's.
_THROUGH_A_FILE = {
    "genre_invalid", "genre_duplicate", "genre_extends_unknown", "genre_depth",
    "genre_narrowing", "genre_rejected", "kind_rejected",
}  # fmt: skip


def test_every_published_kind_has_a_case() -> None:
    assert set(CASES) == set(PROBLEM_KINDS)


@pytest.mark.parametrize("kind", sorted(CASES))
def test_each_problem_kind_is_reported_at_its_path(
    kind: str,
    compare_data: dict[str, Any],
    evaluate_data: dict[str, Any],
    vocabulary: Vocabulary,
    write_experiment: Write,
) -> None:
    case, path = CASES[kind]
    found = case(compare_data, evaluate_data, vocabulary, write_experiment)
    assert (path, kind) in found, found


@pytest.mark.parametrize("kind", sorted(set(CASES) - _THROUGH_A_FILE))
def test_one_mistake_is_reported_as_one_kind(
    kind: str,
    compare_data: dict[str, Any],
    evaluate_data: dict[str, Any],
    vocabulary: Vocabulary,
    write_experiment: Write,
) -> None:
    """A single mistake does not fan out into unrelated problems."""
    case, _ = CASES[kind]
    found = case(compare_data, evaluate_data, vocabulary, write_experiment)
    assert {found_kind for _, found_kind in found} == {kind}, found


# --- batching and completeness ---------------------------------------------------------


def test_every_problem_is_reported_in_one_pass(
    compare_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    del compare_data["subjects"]["streaming"]
    del compare_data["margin"]
    compare_data["primary"] = {"metric": "ttfb", "statistic": "rate"}
    compare_data["probes"][0]["target"] = "{base}/x"
    compare_data["probes"][0]["load_model"] = "sideways"
    compare_data["repeats"] = 1
    problems = _problems(compare_data, vocabulary)
    _well_formed(problems)
    assert {(p.path, p.kind) for p in problems} == {
        ("subjects", "genre_roles"),
        ("margin", "margin_required"),
        ("primary.statistic", "statistic_illegal"),
        ("probes.0.target", "unbound_placeholder"),
        ("probes.0.load_model", "option_invalid"),
        ("repeats", "repeats_below_min"),
    }


def test_a_validation_that_stopped_at_the_files_shape_says_so(
    compare_data: dict[str, Any], write_experiment: Write
) -> None:
    """A file that does not load is not checked against its genre or kinds. The document
    says the list is partial instead of letting it read as "every problem"."""
    compare_data["primary"] = {"metric": "nope"}  # a later check would catch this
    partial = ix.validate(write_experiment({**compare_data, "repeats": "many"}))
    assert not partial.valid and partial.complete is False
    assert [p.kind for p in partial.problems] == ["invalid_value"]
    full = ix.validate(write_experiment(compare_data))
    assert not full.valid and full.complete is True
    assert [p.kind for p in full.problems] == ["metric_unprovided"]
    assert ix.validate(write_experiment({**compare_data, "primary": {"metric": "ttfb"}})).complete


def test_a_refused_genre_file_brings_its_own_problems(
    compare_data: dict[str, Any], write_experiment: Write
) -> None:
    loose = {**_LOOSE, "verdict_form": "none"}
    file: Path = write_experiment({**compare_data, "genre": "lab.v1.genre.loose"})
    (file.parent / "genres").mkdir()
    genre_file = file.parent / "genres" / "loose.yaml"
    genre_file.write_text(yaml.safe_dump(loose))
    result = ix.validate(file)
    assert not result.valid and result.genre == ""
    assert [(p.path, p.kind, p.source) for p in result.problems] == [
        ("genre", "genre_unknown", ""),
        ("must_set", "genre_narrowing", str(genre_file)),
        ("verdict_form", "genre_narrowing", str(genre_file)),
    ]


def test_the_document_carries_the_genres_short_name_and_its_url(
    compare_data: dict[str, Any], write_experiment: Write
) -> None:
    result = ix.validate(write_experiment(compare_data))
    assert (result.genre, result.genre_url) == ("compare", "ix.v1.genre.compare")
    unknown = ix.validate(write_experiment({**compare_data, "genre": "benchmark"}))
    (found,) = unknown.problems
    assert found.got == "benchmark"
    assert found.allowed == ("characterize", "compare", "evaluate", "regress")
    assert found.fix == "genre: one of characterize, compare, evaluate, regress"


# --- placeholders ----------------------------------------------------------------------


class TestPlaceholders:
    def test_a_factor_binds(self, compare_data: dict[str, Any], vocabulary: Vocabulary) -> None:
        compare_data["factors"] = {"size": ["1MiB", "1GiB"]}
        compare_data["probes"][0]["target"] = "{url}/blobs/{size}"
        assert _found(compare_data, vocabulary) == set()

    def test_an_option_only_one_subject_sets_does_not(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        del compare_data["subjects"]["streaming"]["url"]
        (found,) = _problems(compare_data, vocabulary)
        assert (found.path, found.kind, found.got) == (
            "probes.0.target", "unbound_placeholder", "url",
        )  # fmt: skip
        assert found.allowed == ("command",)

    def test_probe_files_are_content_and_are_not_scanned(
        self, evaluate_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file = write_experiment({**evaluate_data, "probes": {"source": "directory"}})
        (file.parent / "probes").mkdir()
        (file.parent / "probes" / "a.md").write_text("---\nexpectation: must_trigger\n---\n{x}\n")
        assert ix.validate(file).valid


# --- the genre's requirements, across genres -------------------------------------------


class TestGenreRequirements:
    def test_superiority_needs_no_margin(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        del compare_data["margin"]
        compare_data["comparison"] = "superiority"
        assert _found(compare_data, vocabulary) == set()

    def test_compare_requires_the_author_to_state_the_comparison(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        del compare_data["comparison"]
        (found,) = _problems(compare_data, vocabulary)
        assert (found.path, found.kind) == ("comparison", "must_set")
        assert found.allowed == ("superiority", "non-inferiority", "equivalence")

    def test_regress_supplies_the_comparison_and_requires_the_margin(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        compare_data["genre"] = "regress"
        del compare_data["comparison"]
        assert _found(compare_data, vocabulary) == set()
        del compare_data["margin"]
        assert _found(compare_data, vocabulary) == {("margin", "must_set")}

    def test_regress_refuses_any_comparison_but_its_own(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        compare_data.update(genre="regress", comparison="superiority")
        (found,) = _problems(compare_data, vocabulary)
        assert (found.path, found.kind) == ("comparison", "comparison_not_allowed")
        assert found.allowed == ("non-inferiority",)

    def test_a_comparison_means_nothing_where_the_genre_makes_none(
        self, evaluate_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        (found,) = _problems({**evaluate_data, "comparison": "superiority"}, vocabulary)
        assert (found.path, found.kind, found.fix) == (
            "comparison", "comparison_not_allowed", "remove 'comparison'",
        )  # fmt: skip

    def test_a_guardrail_takes_the_form_the_genre_can_judge(
        self, evaluate_data: dict[str, Any], compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """Without a control there is nothing to be within a margin of; with one, both
        forms mean something."""
        bounded = {"metric": "tokens_total", "threshold": {"op": "<", "value": 20000}}
        assert _found({**evaluate_data, "guardrails": [bounded]}, vocabulary) == set()
        either = [
            {"metric": "throughput", "margin": "5%"},
            {"metric": "error_rate", "threshold": {"op": "<", "value": 0.01}},
        ]
        assert _found({**compare_data, "guardrails": either}, vocabulary) == set()

    def test_characterize_needs_factors_and_no_decision(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        del compare_data["subjects"]["streaming"]
        data = {**compare_data, "genre": "characterize", "factors": {"size": ["1MiB"]}}
        del data["comparison"], data["margin"]
        assert _found(data, vocabulary) == set()

    def test_a_genre_that_forbids_factors(
        self, evaluate_data: dict[str, Any], write_experiment: Write
    ) -> None:
        data = {**evaluate_data, "genre": "lab.v1.genre.base", "factors": {"size": [1, 2]}}
        del data["threshold"]
        assert ("factors", "genre_factors") in _lab(write_experiment, data, "genres", _PARENT)


# --- the judged-metric rule ------------------------------------------------------------


class TestJudgedMetricNeverDecides:
    """Enforced by the sensor's declaration, in every genre with a verdict."""

    _JUDGE = [{"kind": "judge", "rubric": "rubrics/quality.md"}]

    @pytest.fixture
    def judged_data(self, evaluate_data: dict[str, Any]) -> dict[str, Any]:
        """The evaluate baseline with a judge listed and no activation truth: the tests
        here decide on a judged metric, so nothing would read that truth."""
        del evaluate_data["probes"][0]["truth"]
        return {**evaluate_data, "sensors": self._JUDGE}

    def test_refused_as_the_primary_against_a_threshold(
        self, judged_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        judged_data["primary"] = {"metric": "judged_pass"}
        (found,) = _problems(judged_data, vocabulary)
        assert (found.path, found.kind) == ("primary.metric", "judged_gate")
        assert "never decides" in found.message and "its sensor declares it" in found.message
        assert "metrics:" in found.fix

    @pytest.mark.parametrize("genre", ["compare", "regress"])
    def test_refused_as_the_primary_between_subjects(
        self, genre: str, judged_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        subject = judged_data["subjects"]["catalog"]
        data = {
            **judged_data,
            "genre": genre,
            "subjects": {"old": {**subject, "model": "a"}, "new": {**subject, "model": "b"}},
            "primary": {"metric": "judged_score"},
            "comparison": "non-inferiority",
            "margin": "5%",
        }
        del data["threshold"]
        assert _found(data, vocabulary) == {("primary.metric", "judged_gate")}

    def test_refused_as_a_guardrail(
        self, evaluate_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        evaluate_data["sensors"] = self._JUDGE
        evaluate_data["guardrails"] = [
            {"metric": "judged_score", "threshold": {"op": ">=", "value": 3}}
        ]
        assert _found(evaluate_data, vocabulary) == {("guardrails.0.metric", "judged_gate")}

    def test_an_author_cannot_talk_a_judged_metric_into_deciding(
        self, judged_data: dict[str, Any], write_experiment: Write
    ) -> None:
        """Reviewed defect: ``judged`` was a flag in the experiment file, so writing
        ``judged: false`` on a rubric score passed. There is no such key now, and a lab
        cannot redeclare a built-in sensor to drop the flag either."""
        judged_data["primary"] = {"metric": "judged_pass"}
        stated = {**judged_data, "metrics": [{"name": "judged_pass", "judged": False}]}
        claimed = ix.validate(write_experiment(stated, "claimed"))
        assert [(p.path, p.kind) for p in claimed.problems] == [("metrics.0.judged", "unknown_key")]

        file: Path = write_experiment(judged_data, "redeclared")
        (file.parent / "kinds").mkdir()
        honest = {
            "name": "ix.v1.sensor.judge",
            "summary": "s",
            "needs": ["response"],
            "metrics": [{"name": "judged_pass", "direction": "higher", "shape": "boolean"}],
        }
        (file.parent / "kinds" / "judge.yaml").write_text(yaml.safe_dump(honest))
        redeclared = ix.validate(file)
        assert not redeclared.valid
        assert "kind_unknown" in {p.kind for p in redeclared.problems}

    def test_the_refusal_makes_the_experiment_unplannable(
        self, judged_data: dict[str, Any], write_experiment: Write
    ) -> None:
        judged_data["primary"] = {"metric": "judged_pass"}
        file: Path = write_experiment(judged_data)
        with pytest.raises(ix.InvalidExperimentError) as raised:
            ix.plan(file)
        assert [p.kind for p in raised.value.problems] == ["judged_gate"]

    def test_a_judged_metric_may_be_reported_beside_an_unjudged_primary(
        self, evaluate_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        evaluate_data.update(metrics=["judged_score"], sensors=self._JUDGE)
        assert _found(evaluate_data, vocabulary) == set()

    def test_a_judged_metric_may_be_the_primary_where_there_is_no_verdict(
        self, judged_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        judged_data["subjects"]["catalog"]["model"] = "{model}"
        data = {
            **judged_data,
            "genre": "characterize",
            "factors": {"model": ["small", "large"]},
            "primary": {"metric": "judged_score"},
        }
        del data["threshold"]
        assert _found(data, vocabulary) == set()

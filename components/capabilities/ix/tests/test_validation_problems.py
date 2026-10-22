"""``validate`` reports every problem in one batch, each with path, kind, message, fix, the
allowed values and what was written.

One case per problem kind, and a check that no kind in the published vocabulary lacks one.
The rule that is not a genre's to relax gets its own class: a model-judged metric never
decides, and whether a metric is judged is its sensor's word, not the author's.
"""

from __future__ import annotations

from dataclasses import replace
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
    return examine(data, vocabulary).problems


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
        examined = examine(data, vocabulary)
        assert examined.problems == () and examined.warnings == ()
        assert examined.complete and examined.design is not None
        assert validate(examined).valid


def test_an_invalid_experiment_has_no_design(
    compare_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    del compare_data["margin"]
    examined = examine(compare_data, vocabulary)
    assert examined.problems and examined.design is None


def test_a_problem_kind_outside_the_vocabulary_cannot_be_made() -> None:
    with pytest.raises(ValueError, match="not a problem kind"):
        problem("x", kind="made_up", message="nope")


def test_a_fix_is_built_from_what_is_allowed() -> None:
    found = problem(
        "design.order", kind="invalid_value", message="bad", allowed=["a", "b"], got="c"
    )
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
    """A value of the right type that the schema refuses. (``repeats: many`` is the wrong
    shape: a string where a number goes, as matrix's loader reads it.)"""
    return _api(write, {**c, "repeats": 0})


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
        "old": {"kind": "agent-session", "runtime": "mock", "model": "a"},
        "new": {"kind": "agent-session", "runtime": "mock", "model": "b"},
    }
    c["probes"][0]["target"] = "http://127.0.0.1:8080/x"
    return _found(c, v)


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
    e["sensors"] = [{"kind": "judge", "model_runtime": "hardline", "rubric": "rubric.md"}]
    return _found(e, v)


def _unit_mismatch(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    e["metrics"] = ["tokens_total"]
    e["guardrails"] = [{"metric": "tokens_total", "threshold": {"op": "<", "value": "5s"}}]
    return _found(e, v)


def _probes_empty(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _found({**c, "probes": []}, v) - {("primary.metric", "metric_unprovided")}


def _probe_file(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    """A probe file in the probes/ folder that is not YAML frontmatter: matrix's loader
    reports it, naming the file."""
    data = {key: value for key, value in e.items() if key != "probes"}
    file = write(data)
    (file.parent / "probes").mkdir()
    (file.parent / "probes" / "broken.md").write_text("---\nid: [unclosed\n---\nAsk.\n")
    result = ix.validate(file)
    _well_formed(result.problems)
    assert all(p.source.endswith("broken.md") for p in result.problems), result.problems
    return {(p.path, p.kind) for p in result.problems}


def _probes_folder_missing(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _api(write, {key: value for key, value in e.items() if key != "probes"})


def _probe_duplicate_id(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _found({**e, "probes": [e["probes"][0], e["probes"][0]]}, v)


def _sensor_listed_twice(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    """Regression: a run keeps one entry per sensor kind, so the second silently replaced
    the first one's options."""
    twice = [{"kind": "text-match", "contains": "a"}, {"kind": "text-match", "contains": "b"}]
    return _found({**e, "sensors": twice, "metrics": ["text_matched"]}, v)


def _truth_unclaimed(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    e["probes"][0]["truth"]["expected_skil"] = "review"
    return _found(e, v)


def _unbound_placeholder(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    c["probes"][0]["target"] = "{url}/blobs/{size}"
    return _found(c, v)


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
    return {(p.path, p.kind) for p in examine(c, v).warnings}


def _genre_rejected(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _lab(write, c, "genres", _LOOSE)


def _kind_rejected(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    return _lab(write, c, "kinds", {"name": "lab.v1.sensor.bad", "summary": "s"})


def _kind_unimplemented(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    """Asked of a vocabulary that says what can run: everything but the memory sensor."""
    c["guardrails"] = [{"metric": "memory_peak", "margin": "5%"}]
    runs = frozenset(v.kinds.kinds) - {"ix.v1.sensor.memory"}
    examined = examine(c, replace(v, adapters=runs))
    assert examined.problems == ()
    return {(p.path, p.kind) for p in examined.warnings}


def _analysis_unimplemented(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    foreign = {
        "name": "lab.v1.genre.sequential",
        "question": "Does it hold as the data comes in?",
        "hypothesis_form": "{subject} has {metric} {threshold}",
        "roles": [{"name": "subject", "min": 1, "max": 1}],
        "factors": "allowed",
        "must_set": ["primary", "threshold"],
        "design": {"repeats": {"default": 3, "min": 1}, "order": "sequential",
                   "calibration": "none", "note": "a lab's own"},
        "analysis": {"id": "lab.v1.analysis.sequential"},
        "verdict_form": "three-way",
        "report": ["question"],
        "guidance": "A lab's genre whose analysis this ix does not have.",
    }  # fmt: skip
    file = write({**e, "genre": "lab.v1.genre.sequential"})
    (file.parent / "genres").mkdir()
    (file.parent / "genres" / "sequential.yaml").write_text(yaml.safe_dump(foreign))
    result = ix.validate(file)
    assert result.valid
    return {(p.path, p.kind) for p in result.warnings if p.kind != "kind_unimplemented"}


def _validated(write: Write, data: dict[str, Any]) -> Pairs:
    """The problems ``ix.validate`` finds in ``data`` written to a file: the adapters' review
    included, which the use case alone does not run."""
    return {(p.path, p.kind) for p in ix.validate(write(data)).problems}


def _option_inapplicable(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    """Plugins given to a session on the mock runtime, whose config takes none."""
    e["subjects"]["catalog"]["plugins"] = ["plugins/review"]
    return _validated(write, e)


def _unobservable(c: Any, e: Any, v: Vocabulary, write: Write) -> Pairs:
    """A skill evaluated on claude-sdk with the Skill tool left out: it can never fire."""
    e["subjects"]["catalog"] = {
        "kind": "agent-session",
        "runtime": "claude-sdk",
        "max_turns": 1,
        "tools": ["Read"],
    }
    return _validated(write, e)


#: Each case by the scenario it builds: (the case, the path, the problem kind it reports).
#: Changed requirement: an unknown genre, kind, control subject or parent genre is the
#: shared ``name_unknown``, and a genre, kind or probe id defined twice the shared
#: ``name_taken``, so several scenarios report one kind.
CASES: dict[str, tuple[Any, str, str]] = {
    "yaml_syntax": (_yaml_syntax, "", "yaml_syntax"),
    "schema_unsupported": (_schema_unsupported, "schema", "schema_unsupported"),
    "wrong_shape": (_wrong_shape, "subjects", "wrong_shape"),
    "unknown_key": (_unknown_key, "arms", "unknown_key"),
    "missing_key": (_missing_key, "question", "missing_key"),
    "invalid_value": (_invalid_value, "repeats", "invalid_value"),
    "genre_unknown": (_genre_unknown, "genre", "name_unknown"),
    "genre_roles": (_genre_roles, "subjects", "genre_roles"),
    "genre_factors": (_genre_factors, "factors", "genre_factors"),
    "must_set": (_must_set, "threshold", "must_set"),
    "repeats_below_min": (_repeats_below_min, "repeats", "repeats_below_min"),
    "control_unknown": (_control_unknown, "control", "name_unknown"),
    "control_not_applicable": (_control_not_applicable, "control", "control_not_applicable"),
    "comparison_not_allowed": (_comparison_not_allowed, "comparison", "comparison_not_allowed"),
    "margin_required": (_margin_required, "margin", "margin_required"),
    "guardrail_form": (_guardrail_form, "guardrails.0.margin", "guardrail_form"),
    "kind_unknown": (_kind_unknown, "probes.0.kind", "name_unknown"),
    "kind_unfit": (_kind_unfit, "probes.0.kind", "kind_unfit"),
    "option_unknown": (_option_unknown, "probes.0.durration", "option_unknown"),
    "option_missing": (_option_missing, "probes.0.duration", "option_missing"),
    "option_invalid": (_option_invalid, "probes.0.load_model", "option_invalid"),
    "option_inapplicable": (
        _option_inapplicable,
        "subjects.catalog.plugins",
        "option_inapplicable",
    ),
    "unobservable": (_unobservable, "subjects.catalog.tools", "unobservable"),
    "metric_unprovided": (_metric_unprovided, "primary.metric", "metric_unprovided"),
    "metric_ambiguous": (_metric_ambiguous, "primary.metric", "metric_ambiguous"),
    "metric_mismatch": (_metric_mismatch, "metrics.0.shape", "metric_mismatch"),
    "metric_direction": (_metric_direction, "primary.metric", "metric_direction"),
    "statistic_illegal": (_statistic_illegal, "primary.statistic", "statistic_illegal"),
    "estimator_illegal": (_estimator_illegal, "primary.estimator", "estimator_illegal"),
    "judged_gate": (_judged_gate, "primary.metric", "judged_gate"),
    "unit_mismatch": (_unit_mismatch, "guardrails.0.threshold.value", "unit_mismatch"),
    "probes_empty": (_probes_empty, "probes", "probes_empty"),
    "probe_file": (_probe_file, "", "yaml_syntax"),
    "probes_folder_missing": (_probes_folder_missing, "probes", "probes_empty"),
    "probe_duplicate_id": (_probe_duplicate_id, "probes", "name_taken"),
    "sensor_listed_twice": (_sensor_listed_twice, "sensors.1.kind", "name_taken"),
    "truth_unclaimed": (_truth_unclaimed, "probes.0.truth.expected_skil", "truth_unclaimed"),
    "unbound_placeholder": (_unbound_placeholder, "probes.0.target", "unbound_placeholder"),
    "genre_invalid": (_genre_invalid, "", "genre_invalid"),
    "genre_duplicate": (_genre_duplicate, "name", "name_taken"),
    "genre_extends_unknown": (_genre_extends_unknown, "extends", "name_unknown"),
    "genre_depth": (_genre_depth, "extends", "genre_depth"),
    "genre_narrowing": (_genre_narrowing, "must_set", "genre_narrowing"),
    "kind_invalid": (_kind_invalid, "", "kind_invalid"),
    "kind_duplicate": (_kind_duplicate, "name", "name_taken"),
    "subjects_differ_in": (_subjects_differ_in, "subjects.streaming", "subjects_differ_in"),
    "kind_unimplemented": (_kind_unimplemented, "sensors", "kind_unimplemented"),
    "analysis_unimplemented": (_analysis_unimplemented, "genre", "analysis_unimplemented"),
    "genre_rejected": (_genre_rejected, "", "genre_rejected"),
    "kind_rejected": (_kind_rejected, "", "kind_rejected"),
}

#: Cases that go through a refused genre or kind file, where the file's own problems come
#: along with the experiment's.
_THROUGH_A_FILE = {
    "genre_invalid", "genre_duplicate", "genre_extends_unknown", "genre_depth",
    "genre_narrowing", "genre_rejected", "kind_rejected",
}  # fmt: skip


def test_every_published_kind_has_a_case() -> None:
    assert {kind for _, _, kind in CASES.values()} == set(PROBLEM_KINDS)


@pytest.mark.parametrize("scenario", sorted(CASES))
def test_each_problem_kind_is_reported_at_its_path(
    scenario: str,
    compare_data: dict[str, Any],
    evaluate_data: dict[str, Any],
    vocabulary: Vocabulary,
    write_experiment: Write,
) -> None:
    case, path, kind = CASES[scenario]
    found = case(compare_data, evaluate_data, vocabulary, write_experiment)
    assert (path, kind) in found, found


@pytest.mark.parametrize("scenario", sorted(set(CASES) - _THROUGH_A_FILE))
def test_one_mistake_is_reported_as_one_kind(
    scenario: str,
    compare_data: dict[str, Any],
    evaluate_data: dict[str, Any],
    vocabulary: Vocabulary,
    write_experiment: Write,
) -> None:
    """A single mistake does not fan out into unrelated problems."""
    case, _, kind = CASES[scenario]
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
    assert [p.kind for p in partial.problems] == ["wrong_shape"]
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
        ("genre", "name_unknown", ""),
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
        # Changed requirement (Q11): a facet every subject's kind offers binds too.
        assert found.allowed == ("command", "pid")

    def test_a_probe_files_placeholder_is_filled_and_an_unbound_one_is_text(
        self, evaluate_data: dict[str, Any], write_experiment: Write
    ) -> None:
        """Changed requirement (Q11): a probe file's options were not scanned, so ``{x}`` in
        one ran as the text ``{x}`` whatever was meant. They are filled like an inline
        probe's. Changed again: a prompt is text, so ``{x}`` that nothing fills is sent as
        written, and validate warns rather than refuses; ``{{x}}`` is sent as written too."""
        file = write_experiment({k: v for k, v in evaluate_data.items() if k != "probes"})
        (file.parent / "probes").mkdir()
        probe = file.parent / "probes" / "a.md"
        probe.write_text("---\nexpectation: must_trigger\n---\nReturn {x}.\n")
        found = ix.validate(file)
        assert found.valid
        (warned,) = found.warnings
        assert (warned.path, warned.kind, warned.got) == (
            "probes.a.prompt", "unbound_placeholder", "x",
        )  # fmt: skip
        assert "sent as written" in warned.message
        probe.write_text("---\nexpectation: must_trigger\n---\nReturn {{x}}.\n")
        found = ix.validate(file)
        assert found.valid and not found.warnings

    def test_a_typo_in_prompt_text_warns_and_names_the_likely_placeholder(
        self, evaluate_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """Prompt text may hold braces of its own (code), so a name nothing fills there is
        sent as written; a typo for a factor (``{sise}``) is still said, with the name it
        is likely to be."""
        evaluate_data["factors"] = {"size": ["small", "large"]}
        evaluate_data["probes"][0]["prompt"] = "Review the {size} change; {sise} is wrong."
        examined = examine(evaluate_data, vocabulary)
        assert examined.problems == ()
        (warned,) = examined.warnings
        assert (warned.path, warned.kind, warned.got) == (
            "probes.0.prompt", "unbound_placeholder", "sise",
        )  # fmt: skip
        assert "{size}" in warned.message and "{size}" in warned.fix

    def test_a_canned_reply_may_hold_braces_as_a_prompt_may(
        self, evaluate_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """A stand-in's canned reply is free text as a prompt is: code in it, such as an
        f-string, is no placeholder to refuse."""
        evaluate_data["probes"][0]["mock_response"] = 'print(f"{user}")'
        examined = examine(evaluate_data, vocabulary)
        assert examined.problems == ()

    def test_a_name_only_some_subjects_fill_is_refused_in_prompt_text(
        self, evaluate_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """A prompt's ``{model}`` is filled for a subject that sets ``model`` and sent as
        written to one that does not: the subjects would be put different prompts, so it is
        refused, as it is in any other option."""
        agent = evaluate_data["subjects"]["catalog"]
        evaluate_data.update(genre="compare", comparison="superiority")
        evaluate_data["subjects"] = {"plain": dict(agent), "tuned": {**agent, "model": "m-2"}}
        evaluate_data["probes"][0]["prompt"] = "Review this change as {model}."
        problems = [p for p in _problems(evaluate_data, vocabulary) if p.got == "model"]
        assert [(p.path, p.kind) for p in problems] == [("probes.0.prompt", "unbound_placeholder")]

    def test_a_subject_options_placeholder_must_be_a_factor(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """Regression: a subject's options were not scanned, so a typo such as ``{sise}``
        passed validate and ran as the text ``{sise}``. Only a factor fills a subject's
        options."""
        compare_data["factors"] = {"size": ["1MiB", "1GiB"]}
        compare_data["probes"][0]["target"] = "{url}/blobs/{size}"
        compare_data["subjects"]["streaming"]["command"] = ["./proxy", "--size", "{sise}"]
        (found,) = _problems(compare_data, vocabulary)
        assert (found.path, found.kind, found.got) == (
            "subjects.streaming.command", "unbound_placeholder", "sise",
        )  # fmt: skip
        assert found.allowed == ("size",)
        # A command is no prompt: the typo is still refused, and the fix names the factor.
        assert "{size}" in found.fix
        compare_data["subjects"]["streaming"]["command"] = ["./proxy", "--size", "{size}"]
        assert _found(compare_data, vocabulary) == set()

    def test_a_facet_every_subject_offers_binds_a_probe(
        self, compare_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """A run fills a probe's options with the subject's facets too: ``{pid}`` is a
        process subject's, though no subject writes it."""
        compare_data["probes"][0]["target"] = "{url}/proc/{pid}"
        assert _found(compare_data, vocabulary) == set()

    def test_an_option_that_fills_its_own_placeholders_binds_them(
        self, evaluate_data: dict[str, Any], vocabulary: Vocabulary
    ) -> None:
        """A loop's ``prompt`` is filled by the loop each round (``{prompt}``,
        ``{response}``); the kind declares them, so they are not unbound. A loop is prompt
        text, so ``{oops}``, which nothing fills, is a warning and sent as written."""
        evaluate_data["subjects"]["catalog"]["loop"] = {
            "strategy": "self-review", "rounds": 2, "prompt": "{prompt} / {response} / {oops}",
        }  # fmt: skip
        examined = examine(evaluate_data, vocabulary)
        assert examined.problems == ()
        (warned,) = examined.warnings
        assert (warned.path, warned.kind, warned.got) == (
            "subjects.catalog.loop", "unbound_placeholder", "oops",
        )  # fmt: skip
        assert set(warned.allowed) >= {"prompt", "response", "failures", "until"}

    def test_a_subject_is_reviewed_in_every_cell(
        self, evaluate_data: dict[str, Any], write_experiment: Write
    ) -> None:
        """Regression: a subject with any placeholder was not reviewed at all, so a factor
        on one option hid every other problem the runtime would refuse. Each cell's options
        are reviewed as a trial would have them."""
        sweep = {k: v for k, v in evaluate_data.items() if k != "threshold"}
        sweep.update(genre="characterize", factors={"turns": [1, 2]})
        sweep["subjects"]["catalog"].update(max_turns="{turns}", runtime_options={"bogus": 1})
        assert _validated(write_experiment, sweep) == {
            ("subjects.catalog.runtime_options.bogus", "option_unknown")
        }


class TestAddresses:
    @pytest.mark.parametrize("option", ["url", "ready"])
    def test_a_port_out_of_range_is_refused_at_validate(
        self, option: str, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        """Regression: ``urlsplit(url).port`` raised out of the readiness check, so every
        trial became a harness ``error`` instead of the experiment being refused."""
        compare_data["subjects"]["buffered"][option] = "http://127.0.0.1:99999/healthz"
        compare_data["subjects"]["streaming"]["url"] = "http://127.0.0.1:8080"
        if option == "url":
            compare_data["probes"][0]["target"] = "http://127.0.0.1:8080/x"
        assert _validated(write_experiment, compare_data) == {
            (f"subjects.buffered.{option}", "option_invalid")
        }


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

    _JUDGE = [{"kind": "judge", "model_runtime": "hardline", "rubric": "rubrics/quality.md"}]

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
        assert "judged_gate" in {p.kind for p in redeclared.problems}  # ix's judge still holds
        assert "ix.v1.sensor.judge" in ix.kinds(file.parent).rejected

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


# --- what the interval methods cannot decide on ----------------------------------------


def test_a_min_or_a_max_never_decides(compare_data: dict[str, Any], vocabulary: Vocabulary) -> None:
    """A min or a max has no interval ix can stand behind at these trial counts. Before,
    a bootstrap of one decided anyway; now ``validate`` refuses it where a verdict rests on
    it, and still allows it to be measured and reported."""
    compare_data["primary"] = {"metric": "ttfb", "statistic": "p99", "estimator": "max"}
    (problem,) = _problems(compare_data, vocabulary)
    assert (problem.path, problem.kind, problem.got) == (
        "primary.estimator",
        "estimator_illegal",
        "max",
    )
    assert problem.allowed == ("mean", "median")
    compare_data["primary"] = {"metric": "ttfb", "statistic": "p99"}
    compare_data["metrics"] = ["throughput"]
    assert _problems(compare_data, vocabulary) == ()


def test_a_rate_is_never_compared_in_percent(
    compare_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    compare_data["guardrails"] = [{"metric": "error_rate", "margin": "5%"}]
    (problem,) = _problems(compare_data, vocabulary)
    assert (problem.path, problem.kind) == ("guardrails.0.margin", "unit_mismatch")
    assert "5pp" in problem.fix
    compare_data["guardrails"] = [{"metric": "error_rate", "margin": "1pp"}]
    assert _problems(compare_data, vocabulary) == ()


def test_a_median_needs_six_blocks(compare_data: dict[str, Any], vocabulary: Vocabulary) -> None:
    compare_data["primary"] = {"metric": "ttfb", "statistic": "p99", "estimator": "median"}
    assert _problems({**compare_data, "repeats": 6}, vocabulary) == ()
    (problem,) = _problems({**compare_data, "repeats": 5}, vocabulary)
    assert (problem.path, problem.kind, problem.got) == ("repeats", "repeats_below_min", 5)


@pytest.mark.parametrize("margin", [5, 1, "100pp"])
def test_a_rate_margin_beyond_the_whole_range_is_refused(
    margin: float | str, compare_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    """Regression: a bare margin on a rate is a share, so ``margin: 5`` allowed a loss of
    500 points and held for a variant that lost 80 of them."""
    compare_data["guardrails"] = [{"metric": "error_rate", "margin": margin}]
    (problem,) = _problems(compare_data, vocabulary)
    assert (problem.path, problem.kind) == ("guardrails.0.margin", "invalid_value")
    assert "0.05" in problem.fix
    compare_data["guardrails"] = [{"metric": "error_rate", "margin": 0.05}]
    assert _problems(compare_data, vocabulary) == ()


@pytest.mark.parametrize("value", [5, 1, 0, -0.1])
def test_a_rate_threshold_outside_zero_to_one_is_refused(
    value: float, evaluate_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    """Regression: a threshold on a rate is never range-checked, so ``< 5`` held at once,
    decided at 0, for any error rate at all; at 0 or 1 a rate can never pass it, or always
    does."""
    evaluate_data["threshold"] = {"op": ">=", "value": value}
    (problem,) = _problems(evaluate_data, vocabulary)
    assert (problem.path, problem.kind) == ("threshold.value", "invalid_value")
    assert "share" in problem.fix and problem.got == value
    evaluate_data["threshold"] = {"op": ">=", "value": 0.9}
    assert _problems(evaluate_data, vocabulary) == ()


def test_a_rate_threshold_in_percent_is_told_to_be_a_share(
    evaluate_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    """Regression: the fix said "state the threshold as a bare number", which led an author
    from ``90%`` straight to ``90``."""
    evaluate_data["threshold"] = {"op": ">=", "value": "90%"}
    (problem,) = _problems(evaluate_data, vocabulary)
    assert (problem.path, problem.kind) == ("threshold.value", "unit_mismatch")
    assert problem.fix == "state the threshold as a share: 0.9"


_THIN = {
    **_PARENT,
    "name": "lab.v1.genre.thin",
    "hypothesis_form": "{subject} meets {threshold} on {metric}",
    "must_set": ["primary", "threshold"],
    "design": {
        "repeats": {"default": 2, "min": 1},
        "order": "interleaved",
        "calibration": "none",
        "note": "fewer repeats than any interval needs",
    },
    "analysis": {"id": "ix.v1.analysis.evaluate"},
    "verdict_form": "three-way",
}


def test_a_run_too_thin_for_any_interval_is_refused(
    evaluate_data: dict[str, Any], write_experiment: Write
) -> None:
    """Regression: validate held a median to its 6 blocks but no decision to the 3 every
    interval needs, so a genre with ``repeats.min`` 1 validated and every decision of every
    run was then "too few"."""
    thin = {**evaluate_data, "genre": "lab.v1.genre.thin", "repeats": 2}
    assert _lab(write_experiment, thin, "genres", _THIN) == {("repeats", "repeats_below_min")}
    enough = {**evaluate_data, "genre": "lab.v1.genre.thin", "repeats": 3}
    assert ix.validate(write_experiment(enough)).problems == ()  # the same lab, its genres/


def test_too_few_repeats_is_one_problem_with_one_fix(
    evaluate_data: dict[str, Any], vocabulary: Vocabulary
) -> None:
    """Regression: repeats under the genre's minimum and under the 3 blocks an interval
    needs were two ``repeats_below_min`` problems at one path with two fixes. The genre's
    minimum, the larger, says it once."""
    evaluate_data["probes"] = evaluate_data["probes"][:1]
    problems = [p for p in _problems({**evaluate_data, "repeats": 1}, vocabulary)]
    (only,) = [p for p in problems if p.kind == "repeats_below_min"]
    assert "genre evaluate" in only.message

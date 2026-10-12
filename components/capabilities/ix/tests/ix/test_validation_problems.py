"""``validate`` reports every problem in one batch, each with path, kind, message and fix.

One case per problem kind, and a check that no kind in the published vocabulary lacks one.
The rule that is not a genre's to relax gets its own class: a model-judged metric never
decides a gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import yaml

import ix
from ix.domain.experiment import probe_set
from ix.domain.genre import Catalogue, GenreRecord, catalogue
from ix.domain.problem import PROBLEM_KINDS, Problem
from ix.domain.validation import Checked, check, parse

if TYPE_CHECKING:
    from pathlib import Path

Write = Any  # the write_experiment fixture: (data, directory="lab") -> Path


def _check(data: dict[str, Any], genres: Catalogue) -> Checked:
    experiment, problems = parse(data)
    assert experiment is not None, problems
    assert isinstance(experiment.probes, tuple)
    return check(experiment, genres, probe_set(experiment.probes))


def _found(data: dict[str, Any], genres: Catalogue) -> set[tuple[str, str]]:
    return {(p.path, p.kind) for p in _check(data, genres).problems}


def _well_formed(problems: tuple[Problem, ...] | list[Problem]) -> None:
    for problem in problems:
        assert problem.kind in PROBLEM_KINDS, problem
        assert problem.message and problem.fix, problem


# --- the valid baselines ---------------------------------------------------------------


def test_the_baselines_are_valid(
    compare_data: dict[str, Any], evaluate_data: dict[str, Any], builtin_genres: Catalogue
) -> None:
    for data in (compare_data, evaluate_data):
        checked = _check(data, builtin_genres)
        assert checked.problems == () and checked.warnings == ()
        assert checked.valid and checked.design is not None


def test_an_invalid_experiment_has_no_design(
    compare_data: dict[str, Any], builtin_genres: Catalogue
) -> None:
    del compare_data["margin"]
    checked = _check(compare_data, builtin_genres)
    assert not checked.valid and checked.design is None


# --- one case per kind -----------------------------------------------------------------
# Each case returns the (path, kind) pairs validate reported for one deliberate mistake.


def _api(write: Write, data: Any) -> set[tuple[str, str]]:
    result = ix.validate(write(data))
    _well_formed(result.problems)
    _well_formed(result.warnings)
    return {(p.path, p.kind) for p in (*result.problems, *result.warnings)}


def _yaml_syntax(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    file = write(c)
    file.write_text("name: x\n  question: [unclosed\n")
    return {(p.path, p.kind) for p in ix.validate(file).problems}


def _earlier_format(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    return _api(write, {"name": "x", "sensors": [{"type": "activation"}], "trials": 5})


def _unknown_key(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    return _api(write, {**c, "arms": {}})


def _missing_key(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    del c["question"]
    return _api(write, c)


def _invalid_value(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    return _api(write, {**c, "repeats": "many"})


def _genre_unknown(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    return _found({**c, "genre": "benchmark"}, g)


def _genre_subjects(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    return _found({**c, "genre": "evaluate", "bar": {"op": "<", "value": 300}, "rule": None}, g)


def _genre_requires(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    del e["bar"]
    return _found(e, g)


def _genre_factors(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    return _found({**e, "genre": "characterise", "bar": None}, g)


def _genre_min_repeats(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    return _found({**c, "repeats": 2}, g)


def _control_unknown(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    return _found({**c, "control": "bufered"}, g)


def _control_not_applicable(
    c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write
) -> Any:
    return _found({**e, "control": "catalog"}, g)


def _rule_not_allowed(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    return _found({**c, "rule": "bayesian"}, g)


def _rule_requires_margin(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    del c["margin"]
    return _found(c, g)


def _metric_unknown(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    return _found({**c, "primary": {"metric": "latency"}}, g)


def _metric_direction(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    return _found({**c, "primary": {"metric": "drift"}}, g)


def _statistic_illegal(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    return _found({**c, "primary": {"metric": "passed", "statistic": "p99"}}, g)


def _judged_gate(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    return _found({**c, "primary": {"metric": "quality"}}, g)


def _unit_mismatch(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    return _found({**e, "primary": {"metric": "ttfb"}, "bar": {"op": "<", "value": "1s"}}, g)


def _probes_empty(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    return _found({**c, "probes": []}, g)


def _probe_source(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    return _api(write, {**c, "probes": {"source": "directory", "path": "nowhere"}})


def _probe_duplicate_id(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    probe = {"id": "same", "kind": "prompt"}
    return _found({**c, "probes": [probe, probe]}, g)


def _unbound_binding(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    c["probes"][0]["target"] = "http://127.0.0.1:8080/blobs/{size}"
    return _found(c, g)


def _spend_over_ceiling(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    return _found({**c, "spend": {"ceiling": 1.0, "per_trial": 0.5}}, g)


_PARENT = {
    "name": "lab.v1.genre.base",
    "question": "?",
    "hypothesis_form": "{subject}",
    "subjects": [{"name": "subject", "min": 1, "max": 1}],
    "factors": "allowed",
    "defaults": {
        key: {"value": value, "note": "a test default"}
        for key, value in {"repeats": 3, "min_repeats": 2, "interleave": True, "aa": False}.items()
    },
    "analysis": {"id": "lab.v1.analysis.base"},
    "verdict": "none",
    "report": ["question"],
    "skill": "Write it.",
}


def _with_lab_genres(write: Write, data: dict[str, Any], *genres: dict[str, Any]) -> Any:
    file = write(data)
    (file.parent / "genres").mkdir()
    for index, genre in enumerate(genres):
        (file.parent / "genres" / f"g{index}.yaml").write_text(yaml.safe_dump(genre))
    result = ix.validate(file)
    _well_formed(result.problems)
    _well_formed(result.warnings)
    return {(p.path, p.kind) for p in (*result.problems, *result.warnings)}


def _genre_invalid(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    broken = {**_PARENT, "name": "lab.v1.genre.broken", "requires": ["sensor"]}
    return _with_lab_genres(write, {**e, "genre": "lab.v1.genre.broken"}, broken)


def _genre_duplicate(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    return _with_lab_genres(write, {**e, "genre": "lab.v1.genre.base"}, _PARENT, _PARENT)


def _genre_extends_unknown(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    orphan = {"name": "lab.v1.genre.orphan", "extends": "lab.v1.genre.gone"}
    return _with_lab_genres(write, {**e, "genre": "lab.v1.genre.orphan"}, orphan)


def _genre_depth(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    deep = {"name": "lab.v1.genre.deep", "extends": "regress"}
    return _with_lab_genres(write, {**c, "genre": "lab.v1.genre.deep"}, deep)


def _genre_narrowing(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    loose = {"name": "lab.v1.genre.loose", "extends": "compare", "requires": []}
    return _with_lab_genres(write, {**c, "genre": "lab.v1.genre.loose"}, loose)


def _subjects_differ_in(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    c["subjects"]["streaming"]["ready"] = "http://127.0.0.1:9090/healthz"
    return {(p.path, p.kind) for p in _check(c, g).warnings}


def _genre_rejected(c: dict[str, Any], e: dict[str, Any], g: Catalogue, write: Write) -> Any:
    loose = {"name": "lab.v1.genre.loose", "extends": "compare", "requires": []}
    return _with_lab_genres(write, c, loose)


CASES: dict[str, tuple[Any, str]] = {
    "yaml_syntax": (_yaml_syntax, ""),
    "earlier_format": (_earlier_format, ""),
    "unknown_key": (_unknown_key, "arms"),
    "missing_key": (_missing_key, "question"),
    "invalid_value": (_invalid_value, "repeats"),
    "genre_unknown": (_genre_unknown, "genre"),
    "genre_subjects": (_genre_subjects, "subjects"),
    "genre_requires": (_genre_requires, "bar"),
    "genre_factors": (_genre_factors, "factors"),
    "genre_min_repeats": (_genre_min_repeats, "repeats"),
    "control_unknown": (_control_unknown, "control"),
    "control_not_applicable": (_control_not_applicable, "control"),
    "rule_not_allowed": (_rule_not_allowed, "rule"),
    "rule_requires_margin": (_rule_requires_margin, "margin"),
    "metric_unknown": (_metric_unknown, "primary.metric"),
    "metric_direction": (_metric_direction, "primary.metric"),
    "statistic_illegal": (_statistic_illegal, "primary.statistic"),
    "judged_gate": (_judged_gate, "primary.metric"),
    "unit_mismatch": (_unit_mismatch, "bar.value"),
    "probes_empty": (_probes_empty, "probes"),
    "probe_source": (_probe_source, "probes.path"),
    "probe_duplicate_id": (_probe_duplicate_id, "probes"),
    "unbound_binding": (_unbound_binding, "probes.0.target"),
    "spend_over_ceiling": (_spend_over_ceiling, "spend.ceiling"),
    "genre_invalid": (_genre_invalid, ""),
    "genre_duplicate": (_genre_duplicate, "name"),
    "genre_extends_unknown": (_genre_extends_unknown, "extends"),
    "genre_depth": (_genre_depth, "extends"),
    "genre_narrowing": (_genre_narrowing, "requires"),
    "subjects_differ_in": (_subjects_differ_in, "subjects.streaming"),
    "genre_rejected": (_genre_rejected, ""),
}


def test_every_published_kind_has_a_case() -> None:
    assert set(CASES) == set(PROBLEM_KINDS)


@pytest.mark.parametrize("kind", sorted(CASES))
def test_each_problem_kind_is_reported_at_its_path(
    kind: str,
    compare_data: dict[str, Any],
    evaluate_data: dict[str, Any],
    builtin_genres: Catalogue,
    write_experiment: Write,
) -> None:
    case, path = CASES[kind]
    found = case(compare_data, evaluate_data, builtin_genres, write_experiment)
    assert (path, kind) in found, found


@pytest.mark.parametrize(
    "kind",
    sorted(
        set(CASES)
        - {
            "genre_rejected",
            "genre_invalid",
            "genre_duplicate",
            "genre_extends_unknown",
            "genre_depth",
            "genre_narrowing",
        }
    ),
)
def test_one_mistake_is_reported_as_one_kind(
    kind: str,
    compare_data: dict[str, Any],
    evaluate_data: dict[str, Any],
    builtin_genres: Catalogue,
    write_experiment: Write,
) -> None:
    """A single mistake does not fan out into unrelated problems."""
    case, _ = CASES[kind]
    found = case(compare_data, evaluate_data, builtin_genres, write_experiment)
    assert {found_kind for _, found_kind in found} == {kind}, found


# --- batching --------------------------------------------------------------------------


def test_every_problem_is_reported_in_one_pass(
    compare_data: dict[str, Any], builtin_genres: Catalogue
) -> None:
    """The file from the design's "what a wrong experiment looks like", and then some."""
    del compare_data["subjects"]["streaming"]
    del compare_data["margin"]
    compare_data["primary"] = {"metric": "quality", "statistic": "p99"}
    compare_data["probes"][0]["target"] = "{base_url}/x"
    compare_data["repeats"] = 1
    compare_data["spend"] = {"ceiling": 0.01, "per_trial": 1}
    checked = _check(compare_data, builtin_genres)
    _well_formed(checked.problems)
    assert {(p.path, p.kind) for p in checked.problems} == {
        ("subjects", "genre_subjects"),
        ("margin", "rule_requires_margin"),
        ("primary.statistic", "statistic_illegal"),
        ("primary.metric", "judged_gate"),
        ("probes.0.target", "unbound_binding"),
        ("repeats", "genre_min_repeats"),
    }


def test_a_refused_genre_file_brings_its_own_problems(
    compare_data: dict[str, Any], write_experiment: Write
) -> None:
    """An experiment that names a refused genre is told what is wrong in the genre file,
    with the file named."""
    loose = {
        "name": "lab.v1.genre.loose",
        "extends": "compare",
        "requires": [],
        "verdict": "none",
    }
    file = write_experiment({**compare_data, "genre": "lab.v1.genre.loose"})
    (file.parent / "genres").mkdir()
    genre_file = file.parent / "genres" / "loose.yaml"
    genre_file.write_text(yaml.safe_dump(loose))
    result = ix.validate(file)
    assert not result.valid
    assert [(p.path, p.kind, p.source) for p in result.problems] == [
        ("genre", "genre_unknown", ""),
        ("requires", "genre_narrowing", str(genre_file)),
        ("verdict", "genre_narrowing", str(genre_file)),
    ]


# --- bindings --------------------------------------------------------------------------


class TestBindings:
    def test_a_factor_binds(self, compare_data: dict[str, Any], builtin_genres: Catalogue) -> None:
        compare_data["factors"] = {"size": ["1MiB", "1GiB"]}
        compare_data["probes"][0]["target"] = "http://127.0.0.1:8080/blobs/{size}"
        assert _found(compare_data, builtin_genres) == set()

    def test_an_option_every_subject_sets_binds(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        for subject in compare_data["subjects"].values():
            subject["base_url"] = "http://127.0.0.1:8080"
        compare_data["probes"][0]["target"] = "{base_url}/x"
        assert _found(compare_data, builtin_genres) == set()

    def test_an_option_only_one_subject_sets_does_not(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        compare_data["subjects"]["buffered"]["base_url"] = "http://127.0.0.1:8080"
        compare_data["probes"][0]["target"] = "{base_url}/x"
        assert ("probes.0.target", "unbound_binding") in _found(compare_data, builtin_genres)

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
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        del compare_data["margin"]
        compare_data["rule"] = "superiority"
        assert _found(compare_data, builtin_genres) == set()

    def test_compare_requires_the_author_to_state_the_rule(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        del compare_data["rule"]
        assert _found(compare_data, builtin_genres) == {("rule", "genre_requires")}

    def test_regress_supplies_the_rule_and_requires_the_margin(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        compare_data["genre"] = "regress"
        del compare_data["rule"]
        assert _found(compare_data, builtin_genres) == set()
        del compare_data["margin"]
        assert _found(compare_data, builtin_genres) == {("margin", "genre_requires")}

    def test_regress_refuses_any_rule_but_its_own(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        compare_data.update(genre="regress", rule="superiority")
        assert _found(compare_data, builtin_genres) == {("rule", "rule_not_allowed")}

    def test_a_rule_means_nothing_where_the_genre_decides_by_none(
        self, evaluate_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        problems = _check({**evaluate_data, "rule": "superiority"}, builtin_genres).problems
        assert [(p.path, p.kind, p.fix) for p in problems] == [
            ("rule", "rule_not_allowed", "remove 'rule'")
        ]

    def test_characterise_needs_factors_and_no_decision(
        self, evaluate_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        data = {**evaluate_data, "genre": "characterise", "factors": {"size": [1, 2]}}
        del data["bar"], data["primary"]
        assert _found(data, builtin_genres) == set()

    def test_a_genre_that_forbids_factors(
        self, evaluate_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        strict = {**_PARENT, "factors": "forbidden"}
        genres = catalogue([GenreRecord(origin="strict.yaml", data=strict)])
        data = {**evaluate_data, "genre": "lab.v1.genre.base", "factors": {"size": [1, 2]}}
        assert ("factors", "genre_factors") in _found(data, genres)

    def test_the_unknown_genre_message_lists_the_catalogue(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        problem = _check({**compare_data, "genre": "benchmark"}, builtin_genres).problems[0]
        for name in ("characterise", "compare", "evaluate", "regress"):
            assert name in problem.message and name in problem.fix


# --- the judged-metric gate ------------------------------------------------------------


class TestJudgedMetricNeverGates:
    """Enforced by the metric's declared type, in every genre with a verdict."""

    @pytest.mark.parametrize("genre", ["compare", "regress"])
    def test_refused_as_the_primary_between_subjects(
        self, genre: str, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        compare_data.update(genre=genre, primary={"metric": "quality"})
        problems = _check(compare_data, builtin_genres).problems
        assert [(p.path, p.kind) for p in problems] == [("primary.metric", "judged_gate")]
        assert "never decides a gate" in problems[0].message
        assert "quality" in problems[0].fix

    def test_refused_as_the_primary_against_a_bar(
        self, evaluate_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        evaluate_data["primary"] = {"metric": "quality"}
        assert _found(evaluate_data, builtin_genres) == {("primary.metric", "judged_gate")}

    def test_refused_as_a_guard(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        compare_data["guards"] = [{"metric": "quality", "margin": "5%"}]
        assert _found(compare_data, builtin_genres) == {("guards.0.metric", "judged_gate")}

    def test_the_refusal_makes_the_experiment_invalid_and_unplannable(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment({**compare_data, "primary": {"metric": "quality"}})
        assert not ix.validate(file).valid
        with pytest.raises(ix.InvalidExperimentError) as raised:
            ix.plan(file)
        assert [p.kind for p in raised.value.problems] == ["judged_gate"]

    def test_a_judged_metric_may_be_declared_beside_an_unjudged_primary(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        assert any(m["judged"] for m in compare_data["metrics"] if "judged" in m)
        assert _found(compare_data, builtin_genres) == set()

    def test_a_judged_metric_may_be_the_primary_where_there_is_no_verdict(
        self, evaluate_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        data = {**evaluate_data, "genre": "characterise", "factors": {"size": [1, 2]}}
        del data["bar"]
        data["primary"] = {"metric": "quality"}
        assert _found(data, builtin_genres) == set()

    def test_judged_defaults_to_false_and_is_part_of_the_declaration(
        self, compare_data: dict[str, Any]
    ) -> None:
        experiment, _ = parse(compare_data)
        assert experiment is not None
        judged = {metric.name: metric.judged for metric in experiment.metrics}
        assert judged["ttfb"] is False and judged["quality"] is True

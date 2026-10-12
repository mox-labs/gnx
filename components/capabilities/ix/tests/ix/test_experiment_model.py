"""The experiment record, and its resolution against a genre.

One field set serves every genre; an unknown key is refused; a subject's kind-specific keys
are kept apart from ix's own. Resolution gives every subject a role and shows where each
design value came from.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from ix.domain.design import CORE, EXPERIMENT, assign_roles, resolve
from ix.domain.experiment import (
    DEFAULT_STATISTIC,
    STATISTICS,
    Bar,
    Experiment,
    Margin,
    ProbeBinding,
    ProbeSource,
    probe_set,
)
from ix.domain.problem import InvalidExperimentError
from ix.domain.validation import parse

if TYPE_CHECKING:
    from ix.domain.genre import Catalogue, Genre


def _load(data: dict[str, Any]) -> Experiment:
    experiment, problems = parse(data)
    assert experiment is not None, problems
    return experiment


def _genre(catalogue: Catalogue, name: str) -> Genre:
    genre = catalogue.get(name)
    assert genre is not None
    return genre


# --- the record ------------------------------------------------------------------------


class TestExperimentRecord:
    def test_schema_id_is_versioned(self, compare_data: dict[str, Any]) -> None:
        experiment = _load(compare_data)
        assert experiment.document()["schema"] == "ix.v2.experiment"
        assert _load({**compare_data, "schema": "ix.v2.experiment"}) == experiment

    def test_another_schema_id_is_refused(self, compare_data: dict[str, Any]) -> None:
        _, problems = parse({**compare_data, "schema": "ix.v1.experiment"})
        assert [(p.path, p.kind) for p in problems] == [("schema", "invalid_value")]

    def test_document_round_trips(self, compare_data: dict[str, Any]) -> None:
        experiment = _load(compare_data)
        assert Experiment.model_validate(experiment.document()) == experiment

    def test_subjects_keep_the_order_written(self, compare_data: dict[str, Any]) -> None:
        compare_data["subjects"] = {
            "zeta": {"kind": "process"},
            "alpha": {"kind": "process"},
            "mid": {"kind": "process"},
        }
        assert list(_load(compare_data).subjects) == ["zeta", "alpha", "mid"]

    def test_kind_options_are_kept_apart_from_ix_keys(self, compare_data: dict[str, Any]) -> None:
        compare_data["subjects"]["buffered"] = {
            "kind": "process",
            "description": "the current build",
            "effects": ["starts process ./proxy"],
            "command": ["./proxy"],
            "ready": "http://127.0.0.1:8080/healthz",
        }
        subject = _load(compare_data).subjects["buffered"]
        assert subject.kind == "process"
        assert subject.effects == ("starts process ./proxy",)
        assert subject.options == {"command": ["./proxy"], "ready": "http://127.0.0.1:8080/healthz"}

    def test_probes_are_inline_or_a_source(self, compare_data: dict[str, Any]) -> None:
        inline = _load(compare_data).probes
        assert isinstance(inline, tuple) and isinstance(inline[0], ProbeBinding)
        assert inline[0].options == {"target": "http://127.0.0.1:8080/x"}
        compare_data["probes"] = {"source": "directory", "path": "tasks"}
        source = _load(compare_data).probes
        assert isinstance(source, ProbeSource)
        assert (source.source, source.options) == ("directory", {"path": "tasks"})

    @pytest.mark.parametrize(
        ("text", "value", "unit"),
        [
            ("5%", 5.0, "percent"),
            ("10pp", 10.0, "pp"),
            (0.5, 0.5, "absolute"),
            ("2.5", 2.5, "absolute"),
            ("0%", 0.0, "percent"),
        ],
    )
    def test_margin_forms(self, text: Any, value: float, unit: str) -> None:
        margin = Margin.model_validate(text)
        assert (margin.value, margin.unit) == (value, unit)
        assert Margin.model_validate(margin.text()) == margin

    @pytest.mark.parametrize("text", ["five percent", "-5%", "5 percent", True])
    def test_a_malformed_margin_is_refused(self, text: Any) -> None:
        with pytest.raises(ValidationError):
            Margin.model_validate(text)

    def test_a_bar_reads_a_quantity(self) -> None:
        bar = Bar.model_validate({"op": "<", "value": "300ms"})
        assert (bar.op, bar.value, bar.unit) == ("<", 300.0, "ms")
        assert Bar.model_validate({"op": ">=", "value": 0.9}).unit == ""
        with pytest.raises(ValidationError, match="unit once"):
            Bar.model_validate({"op": "<", "value": "300ms", "unit": "s"})

    def test_every_shape_with_a_statistic_has_a_default(self) -> None:
        for shape, statistics in STATISTICS.items():
            if statistics:
                assert DEFAULT_STATISTIC[shape] == statistics[0]
            else:
                assert shape not in DEFAULT_STATISTIC

    def test_the_digest_follows_the_declaration_not_the_spelling(
        self, compare_data: dict[str, Any]
    ) -> None:
        one = _load(compare_data)
        reordered = _load(dict(reversed(list(compare_data.items()))))
        assert one.digest() == reordered.digest()
        assert _load({**compare_data, "margin": "6%"}).digest() != one.digest()

    def test_inline_probes_get_ids_and_a_digest(self) -> None:
        probes = (ProbeBinding(kind="prompt"), ProbeBinding(id="named", kind="prompt"))
        pinned = probe_set(probes)
        assert pinned.ids == ("probe-1", "named")
        assert pinned.digest == probe_set(probes).digest
        changed = probe_set((ProbeBinding(kind="prompt", options={"prompt": "x"}), probes[1]))
        assert changed.digest != pinned.digest


class TestRecordProblems:
    """What a file that is not an experiment is told, each with a path and a fix."""

    def test_unknown_key(self, compare_data: dict[str, Any]) -> None:
        _, problems = parse({**compare_data, "repeets": 5})
        assert [(p.path, p.kind) for p in problems] == [("repeets", "unknown_key")]
        assert "repeets" in problems[0].fix

    def test_missing_keys_are_all_reported(self) -> None:
        _, problems = parse({"name": "x"})
        assert {(p.path, p.kind) for p in problems} == {
            ("question", "missing_key"),
            ("genre", "missing_key"),
            ("subjects", "missing_key"),
            ("probes", "missing_key"),
        }

    def test_a_nested_path_names_the_key_the_author_wrote(
        self, compare_data: dict[str, Any]
    ) -> None:
        del compare_data["subjects"]["streaming"]["kind"]
        compare_data["probes"][0].pop("kind")
        compare_data["metrics"][0]["shape"] = "matrix"
        _, problems = parse(compare_data)
        assert {(p.path, p.kind) for p in problems} == {
            ("subjects.streaming.kind", "missing_key"),
            ("probes.0.kind", "missing_key"),
            ("metrics.0.shape", "invalid_value"),
        }

    @pytest.mark.parametrize(
        "change",
        [
            {"repeats": 0},
            {"seed": -1},
            {"factors": {"size": []}},
            {"factors": {"size": [1, 1]}},
            {"subjects": {}},
            {"name": "two words"},
            {"spend": {"ceiling": -1}},
        ],
    )
    def test_invalid_values(self, compare_data: dict[str, Any], change: dict[str, Any]) -> None:
        experiment, problems = parse({**compare_data, **change})
        assert experiment is None
        assert {p.kind for p in problems} == {"invalid_value"}
        assert all(p.fix for p in problems)

    def test_a_metric_declared_twice(self, compare_data: dict[str, Any]) -> None:
        compare_data["metrics"].append(compare_data["metrics"][0])
        _, problems = parse(compare_data)
        assert [(p.path, p.kind) for p in problems] == [("metrics", "invalid_value")]
        assert "ttfb" in problems[0].message

    def test_a_file_for_the_earlier_harness_is_named_as_one(self) -> None:
        earlier = {
            "name": "routing",
            "sensor": "activation",
            "trials": 5,
            "subjects": [{"name": "baseline", "config": {"agent": "reviewer"}}],
        }
        experiment, problems = parse(earlier)
        assert experiment is None
        assert [p.kind for p in problems] == ["earlier_format"]
        assert "ix experiment validate" in problems[0].fix

    def test_not_a_mapping(self) -> None:
        _, problems = parse(["name", "x"])
        assert [p.kind for p in problems] == ["invalid_value"]


# --- roles -----------------------------------------------------------------------------


class TestRoles:
    def test_the_first_subject_is_the_control(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        roles, control, problems = assign_roles(
            _load(compare_data), _genre(builtin_genres, "compare")
        )
        assert problems == []
        assert control == "buffered"
        assert [(r.name, r.role) for r in roles] == [
            ("buffered", "control"),
            ("streaming", "variant"),
        ]

    def test_control_names_another_subject(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        compare_data["control"] = "streaming"
        roles, control, problems = assign_roles(
            _load(compare_data), _genre(builtin_genres, "compare")
        )
        assert problems == []
        assert control == "streaming"
        assert [(r.name, r.role) for r in roles] == [
            ("buffered", "variant"),
            ("streaming", "control"),
        ]

    def test_one_control_and_many_variants(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        for name in ("zero-copy", "chunked", "mmap"):
            compare_data["subjects"][name] = {"kind": "process", "command": ["./proxy", name]}
        roles, control, problems = assign_roles(
            _load(compare_data), _genre(builtin_genres, "compare")
        )
        assert problems == []
        assert [r.role for r in roles] == ["control", "variant", "variant", "variant", "variant"]
        assert [r.role for r in roles].count("control") == 1

    def test_compare_with_one_subject_has_no_variant(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        del compare_data["subjects"]["streaming"]
        _, _, problems = assign_roles(_load(compare_data), _genre(builtin_genres, "compare"))
        assert [(p.path, p.kind) for p in problems] == [("subjects", "genre_subjects")]
        assert "variant" in problems[0].message and "2 or more" in problems[0].fix

    def test_regress_takes_exactly_one_variant(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        compare_data["subjects"]["third"] = {"kind": "process"}
        _, _, problems = assign_roles(_load(compare_data), _genre(builtin_genres, "regress"))
        assert [p.kind for p in problems] == ["genre_subjects"]
        assert "exactly 2" in problems[0].fix

    def test_a_control_that_is_not_a_subject(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        compare_data["control"] = "bufered"
        _, control, problems = assign_roles(_load(compare_data), _genre(builtin_genres, "compare"))
        assert control is None
        assert [(p.path, p.kind) for p in problems] == [("control", "control_unknown")]
        assert problems[0].fix == "control: buffered"

    def test_control_in_a_genre_without_one(
        self, evaluate_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        evaluate_data["control"] = "catalog"
        roles, control, problems = assign_roles(
            _load(evaluate_data), _genre(builtin_genres, "evaluate")
        )
        assert control is None
        assert [r.role for r in roles] == ["subject"]
        assert [(p.path, p.kind) for p in problems] == [("control", "control_not_applicable")]


# --- resolution ------------------------------------------------------------------------


class TestResolution:
    def test_every_genre_default_is_visible_and_attributed(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        genre = _genre(builtin_genres, "compare")
        design = resolve(_load(compare_data), genre)
        by_field = {setting.field: setting for setting in design.settings}

        assert design.repeats == genre.defaults.repeats.value
        assert by_field["repeats"].source == genre.name
        assert by_field["repeats"].note == genre.defaults.repeats.note
        for field in ("interleave", "aa"):
            assert by_field[field].source == genre.name
            assert by_field[field].value == getattr(genre.defaults, field).value
            assert "provisional" in by_field[field].note

        assert by_field["control"].source == CORE and by_field["control"].value == "buffered"
        assert by_field["seed"].source == CORE
        assert by_field["rule"].source == EXPERIMENT
        assert by_field["primary.statistic"].source == EXPERIMENT
        assert {s.field for s in design.defaults} == {
            "control",
            "repeats",
            "seed",
            "interleave",
            "aa",
        }

    def test_what_the_author_wrote_wins_and_is_attributed_to_them(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        compare_data.update(repeats=7, seed=11, control="streaming")
        design = resolve(_load(compare_data), _genre(builtin_genres, "compare"))
        assert (design.repeats, design.seed, design.control) == (7, 11, "streaming")
        sources = {s.field: s.source for s in design.settings}
        assert sources["repeats"] == sources["seed"] == sources["control"] == EXPERIMENT

    def test_a_genre_supplies_the_rule_it_fixes(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        del compare_data["rule"]
        compare_data["genre"] = "regress"
        genre = _genre(builtin_genres, "regress")
        design = resolve(_load(compare_data), genre)
        assert design.rule == "non-inferiority"
        rule = next(s for s in design.settings if s.field == "rule")
        assert rule.source == genre.name and "provisional" in rule.note

    def test_the_statistic_defaults_by_the_metrics_shape(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        compare_data["primary"] = {"metric": "ttfb"}
        compare_data["guards"] = [{"metric": "throughput", "margin": "5%"}]
        design = resolve(_load(compare_data), _genre(builtin_genres, "compare"))
        assert design.primary is not None and design.primary.statistic == "p50"
        assert design.guards[0].statistic == "mean"
        by_field = {s.field: s for s in design.settings}
        assert by_field["primary.statistic"].source == CORE
        assert by_field["guards.0.statistic"].source == CORE

    def test_the_seed_is_fixed_by_the_name_when_none_is_stated(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        genre = _genre(builtin_genres, "compare")
        one = resolve(_load(compare_data), genre).seed
        assert resolve(_load({**compare_data, "margin": "9%"}), genre).seed == one
        assert resolve(_load({**compare_data, "name": "other"}), genre).seed != one

    def test_the_genre_carries_through(
        self, evaluate_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        genre = _genre(builtin_genres, "evaluate")
        design = resolve(_load(evaluate_data), genre)
        assert design.document()["schema"] == "ix.v1.design"
        assert (design.genre, design.verdict) == (genre.name, "three-way")
        assert design.analysis == genre.analysis and design.report == genre.report
        assert design.control is None and design.aa is False

    def test_subjects_that_do_not_fit_stop_resolution(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        compare_data["genre"] = "evaluate"
        with pytest.raises(InvalidExperimentError) as raised:
            resolve(_load(compare_data), _genre(builtin_genres, "evaluate"))
        assert [p.kind for p in raised.value.problems] == ["genre_subjects"]
        assert raised.value.kind == "config" and raised.value.fix

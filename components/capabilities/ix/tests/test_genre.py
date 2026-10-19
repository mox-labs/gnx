"""Genres are data: a record per file, extended only by narrowing, one level deep.

The requirements pinned here: a child may tighten its parent and never loosen it or add a
mechanism (an analysis parameter included); ``extends`` goes one level; a genre file holds
no code and names no kind; a genre's design carries its standing; the hypothesis sentence
refers only to what can be rendered; a third party ships a genre as a data file.
"""

from __future__ import annotations

import copy
import random
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from ix.adapters._out.matrix_documents import GENRE_GROUP, GenreFiles
from ix.composition import type_url_problem
from ix.domain.genre import (
    MUST_SETTABLE,
    Genre,
    Genres,
    genres,
    narrowing_violations,
)
from ix.domain.problem import problem as a_problem
from ix.domain.schema import DeclarationFile

BUILT_IN = ("characterize", "compare", "evaluate", "regress")
FORMS = {
    "superiority": "{variant} is better than {control} on {metric}",
    "non-inferiority": "{variant} is no worse than {control} by more than {margin} on {metric}",
    "equivalence": "{variant} is within {margin} of {control} on {metric}",
}
BASE, CHILD = "acme.v1.genre.base", "acme.v1.genre.child"


def _genre(name: str = BASE, **changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema": "ix.v1.genre",
        "name": name,
        "question": "Is the variant no worse?",
        "roles": [{"name": "control", "min": 1, "max": 1}, {"name": "variant", "min": 1}],
        "factors": "allowed",
        "must_set": ["primary"],
        "comparisons": dict(FORMS),
        "design": {
            "repeats": {"default": 5, "min": 3},
            "order": "interleaved",
            "calibration": "none",
            "estimator": "mean",
            "note": "a test design",
        },
        "analysis": {"id": "acme.v1.analysis.base", "params": {"correction": "holm"}},
        "verdict_form": "three-way",
        "report": ["question", "estimates"],
        "guidance": "Decide the margin first.",
    }
    data.update(changes)
    return data


def _genres(*records: dict[str, Any]) -> Genres:
    files = [DeclarationFile(origin=f"file-{i}.yaml", data=r) for i, r in enumerate(records)]
    return genres(files, grammar=type_url_problem)


def _child(**changes: Any) -> dict[str, Any]:
    return {"name": CHILD, "extends": BASE, **changes}


def _kinds(found: Genres, name: str = CHILD) -> list[str]:
    return [problem.kind for problem in found.rejected.get(name, ())]


def _paths(found: Genres, name: str = CHILD) -> list[str]:
    return [problem.path for problem in found.rejected.get(name, ())]


# --- the record ------------------------------------------------------------------------


class TestGenreRecord:
    def test_unknown_key_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            Genre.model_validate(_genre(hook="acme.hooks:expand"))

    def test_the_design_states_its_standing(self) -> None:
        data = _genre()
        del data["design"]["note"]
        with pytest.raises(ValidationError, match="note"):
            Genre.model_validate(data)

    def test_default_repeats_cannot_be_below_the_minimum(self) -> None:
        data = _genre()
        data["design"]["repeats"] = {"default": 2, "min": 3}
        with pytest.raises(ValidationError, match="below min"):
            Genre.model_validate(data)

    def test_must_set_names_experiment_fields(self) -> None:
        with pytest.raises(ValidationError, match="not experiment fields"):
            Genre.model_validate(_genre(must_set=["sensor"]))

    def test_default_comparison_is_one_of_the_comparisons(self) -> None:
        with pytest.raises(ValidationError, match="not among"):
            Genre.model_validate(_genre(default_comparison="bayesian"))

    def test_one_role_besides_control(self) -> None:
        roles = [{"name": "control"}, {"name": "variant"}, {"name": "twin"}]
        with pytest.raises(ValidationError, match="at most one role besides"):
            Genre.model_validate(_genre(roles=roles))

    def test_name_is_a_genre_type_url(self) -> None:
        for name in ("soak", "acme.v1.sensor.soak"):
            with pytest.raises(ValidationError, match="type URL"):
                Genre.model_validate(_genre(name=name))

    def test_analysis_params_hold_no_structure(self) -> None:
        """Parameters are scalars: nothing a genre carries can be a program."""
        data = _genre(analysis={"id": "a.v1.analysis.x", "params": {"steps": ["a", "b"]}})
        with pytest.raises(ValidationError):
            Genre.model_validate(data)

    def test_order_and_calibration_are_named_choices_not_flags(self) -> None:
        for field, value in (("order", True), ("calibration", True), ("order", "random")):
            data = _genre()
            data["design"][field] = value
            with pytest.raises(ValidationError):
                Genre.model_validate(data)

    def test_aa_calibration_needs_a_control_to_run_against_itself(self) -> None:
        data = _genre(roles=[{"name": "subject"}], comparisons={}, hypothesis_form="{subject}")
        data["design"]["calibration"] = "aa"
        with pytest.raises(ValidationError, match="control"):
            Genre.model_validate(data)

    def test_a_genre_has_a_hypothesis_to_render(self) -> None:
        with pytest.raises(ValidationError, match="hypothesis_form"):
            Genre.model_validate(_genre(comparisons={}))

    @pytest.mark.parametrize(
        "form", ["{variant} is {rule} to {control}", "{subject} on {metric}", "{arm} wins"]
    )
    def test_a_hypothesis_refers_only_to_roles_and_decision_values(self, form: str) -> None:
        """A sentence that cannot be rendered is refused when the genre loads, not printed
        with a hole in it."""
        with pytest.raises(ValidationError, match="refers to"):
            Genre.model_validate(_genre(comparisons={"superiority": form}))

    def test_a_comparison_needs_a_margin_exactly_when_its_sentence_states_one(self) -> None:
        genre = Genre.model_validate(_genre())
        assert not genre.needs_margin("superiority")
        assert genre.needs_margin("non-inferiority") and genre.needs_margin("equivalence")
        assert not genre.needs_margin(None)

    def test_what_the_author_must_set_leaves_out_what_a_default_meets(self) -> None:
        open_choice = Genre.model_validate(_genre(must_set=["primary", "comparison"]))
        assert open_choice.author_must_set == (
            "primary", "comparison", "margin (for non-inferiority, equivalence)",
        )  # fmt: skip
        fixed = Genre.model_validate(
            _genre(
                must_set=["primary", "comparison", "margin"],
                comparisons={"non-inferiority": FORMS["non-inferiority"]},
                default_comparison="non-inferiority",
            )
        )
        assert fixed.author_must_set == ("primary", "margin")


# --- the built-in files ----------------------------------------------------------------


class TestBuiltInGenres:
    def test_the_four_load_clean(self, builtin_genres: Genres) -> None:
        assert builtin_genres.rejected == {}
        assert builtin_genres.names() == BUILT_IN
        assert tuple(builtin_genres.genres) == tuple(f"ix.v1.genre.{n}" for n in BUILT_IN)

    def test_short_and_full_names_resolve(self, builtin_genres: Genres) -> None:
        assert builtin_genres.get("compare") is builtin_genres.get("ix.v1.genre.compare")
        assert builtin_genres.get("nope") is None

    def test_regress_extends_compare_and_only_narrows(self, builtin_genres: Genres) -> None:
        compare, regress = builtin_genres.get("compare"), builtin_genres.get("regress")
        assert compare is not None and regress is not None
        assert regress.extends == compare.name
        assert narrowing_violations(compare, regress) == []
        assert regress.analysis == compare.analysis
        assert set(regress.comparisons) < set(compare.comparisons)
        assert set(regress.must_set) > set(compare.must_set)
        variant = regress.role("variant")
        assert variant is not None and variant.max == 1

    def test_regress_takes_what_it_does_not_restate_from_compare(
        self, builtin_genres: Genres
    ) -> None:
        compare, regress = builtin_genres.get("compare"), builtin_genres.get("regress")
        assert compare is not None and regress is not None
        assert regress.factors == compare.factors
        assert regress.verdict_form == compare.verdict_form
        assert regress.design == compare.design

    def test_compare_has_one_control_and_one_or_more_variants(self, builtin_genres: Genres) -> None:
        compare = builtin_genres.get("compare")
        assert compare is not None
        control, variant = compare.role("control"), compare.role("variant")
        assert control is not None and (control.min, control.max) == (1, 1)
        assert variant is not None and (variant.min, variant.max) == (1, None)

    def test_every_design_says_how_it_was_checked(self, builtin_genres: Genres) -> None:
        """Changed requirement: the designs said "provisional: pending the experimentation
        canon"; their methods are now checked by simulation (test_calibration.py)."""
        for genre in builtin_genres.genres.values():
            assert "design record, 2026-10-19" in genre.design.note, genre.name

    def test_what_each_genre_asks_of_its_author(self, builtin_genres: Genres) -> None:
        asked = {g.label: g.author_must_set for g in builtin_genres.genres.values()}
        assert asked == {
            "characterize": ("primary",),
            "compare": ("primary", "comparison", "margin (for non-inferiority, equivalence)"),
            "evaluate": ("primary", "threshold"),
            "regress": ("primary", "margin"),
        }

    def test_extension_is_at_most_one_level(self, builtin_genres: Genres) -> None:
        for genre in builtin_genres.genres.values():
            if genre.extends is not None:
                parent = builtin_genres.get(genre.extends)
                assert parent is not None and parent.extends is None

    def test_no_genre_keeps_a_field_nothing_reads(self) -> None:
        assert "presupposes" not in Genre.model_fields
        assert {"roles", "must_set", "design", "verdict_form", "guidance"} <= set(
            Genre.model_fields
        )

    def test_a_genre_file_names_no_kind(self) -> None:
        """No subject kind, probe kind, sensor, environment kind or tool appears in a genre
        file: not in a field that ix acts on (any word), and not in prose (the ids that are
        not also plain English)."""
        acted_on = {
            *("process", "http", "agent-session"),
            *("prompt", "http-load", "inspect-task"),
            *("timing", "memory", "activation", "function-test", "usage", "judge"),
            *("k6", "compose", "kind"),
        }
        in_prose = {"http", "http-load", "inspect-task", "function-test", "k6"}
        prose_fields = {"question", "hypothesis_form", "guidance", "comparisons"}

        def words(value: Any) -> set[str]:
            if isinstance(value, dict):
                return set().union(*(words(k) | words(v) for k, v in value.items()), set())
            if isinstance(value, list):
                return set().union(*(words(item) for item in value), set())
            return set(str(value).lower().replace(",", " ").replace(".", " ").split())

        directory = Path(__file__).resolve().parents[1] / "src" / "ix" / "builtin" / "genres"
        files = sorted(directory.glob("*.yaml"))
        assert [file.stem for file in files] == list(BUILT_IN)
        for file in files:
            data = yaml.safe_load(file.read_text(encoding="utf-8"))
            for field, value in data.items():
                banned = in_prose if field in prose_fields else acted_on
                assert not (words(value) & banned), f"{file.name}: {field}"


# --- extension: narrowing only ---------------------------------------------------------


class TestExtension:
    def test_a_child_inherits_what_it_omits(self) -> None:
        found = _genres(_genre(), _child(must_set=["primary", "margin"]))
        child = found.get(CHILD)
        assert child is not None, found.rejected
        assert child.must_set == ("primary", "margin")
        assert tuple(child.comparisons) == tuple(FORMS)
        assert child.extends == BASE

    def test_a_child_restates_one_design_choice_and_keeps_the_rest(self) -> None:
        found = _genres(_genre(), _child(design={"repeats": {"min": 4}}))
        child = found.get(CHILD)
        assert child is not None, found.rejected
        assert (child.design.repeats.min, child.design.repeats.default) == (4, 5)
        assert child.design.order == "interleaved"

    def test_extends_takes_a_short_name_of_a_built_in(self) -> None:
        files = [*GenreFiles(packages=["ix.builtin.genres"]).files()]
        gate = {
            "name": "acme.v1.genre.gate",
            "extends": "compare",
            "comparisons": {"superiority": FORMS["superiority"]},
        }
        files.append(DeclarationFile(origin="lab/genres/gate.yaml", data=gate))
        found = genres(files, grammar=type_url_problem)
        loaded = found.get("acme.v1.genre.gate")
        assert loaded is not None, found.rejected
        assert loaded.extends == "ix.v1.genre.compare"

    @pytest.mark.parametrize(
        ("change", "path"),
        [
            ({"roles": [{"name": "control", "min": 0, "max": 1}, {"name": "variant"}]},
             "roles.control.min"),
            ({"roles": [{"name": "control", "min": 1, "max": 2}, {"name": "variant"}]},
             "roles.control.max"),
            ({"roles": [{"name": "control", "min": 1}, {"name": "variant"}]},
             "roles.control.max"),
            ({"roles": [{"name": "control", "min": 1, "max": 1}],
              "comparisons": {"superiority": "{control} is good on {metric}"}}, "roles.variant"),
            ({"roles": [{"name": "control", "min": 1, "max": 1}, {"name": "twin"}],
              "comparisons": {"superiority": "{twin} beats {control}"}}, "roles.twin"),
            ({"must_set": []}, "must_set"),
            ({"comparisons": {**FORMS, "bayesian": "{variant} beats {control}"}}, "comparisons"),
            ({"design": {"repeats": {"default": 5, "min": 2}}}, "design.repeats.min"),
            ({"design": {"order": "sequential"}}, "design.order"),
            ({"design": {"estimator": "median"}}, "design.estimator"),
            ({"analysis": {"id": "acme.v1.analysis.other", "params": {"correction": "holm"}}},
             "analysis.id"),
            ({"analysis": {"id": "acme.v1.analysis.base", "params": {}}},
             "analysis.params.correction"),
            ({"analysis": {"id": "acme.v1.analysis.base", "params": {"correction": "none"}}},
             "analysis.params.correction"),
            ({"verdict_form": "none"}, "verdict_form"),
            ({"report": ["question"]}, "report"),
        ],
    )  # fmt: skip
    def test_loosening_or_adding_a_mechanism_is_refused(
        self, change: dict[str, Any], path: str
    ) -> None:
        found = _genres(_genre(), _child(**change))
        assert found.get(CHILD) is None
        assert set(_kinds(found)) == {"genre_narrowing"}
        assert path in _paths(found)
        for problem in found.rejected[CHILD]:
            assert problem.fix and problem.source == "file-1.yaml"
        assert found.get(BASE) is not None, "the parent is unaffected"

    def test_a_child_cannot_add_an_analysis_parameter(self) -> None:
        """Reproduced in review: a lab genre extending compare added ``alpha: 0.2`` and was
        accepted, though a new parameter can change what the finding means. It is refused,
        whatever else the child leaves alone."""
        added = {"id": "acme.v1.analysis.base", "params": {"correction": "holm", "alpha": 0.2}}
        found = _genres(_genre(), _child(analysis=added))
        assert found.get(CHILD) is None
        assert _paths(found) == ["analysis.params.alpha"]
        assert "alpha" in found.rejected[CHILD][0].message

        files = [*GenreFiles(packages=["ix.builtin.genres"]).files()]
        added = {"id": "ix.v1.analysis.compare", "params": {"baseline": "stored"}}
        lab = {"name": "lab.v1.genre.x", "extends": "compare", "analysis": added}
        found = genres(
            [*files, DeclarationFile(origin="genres/x.yaml", data=lab)], grammar=type_url_problem
        )
        assert _paths(found, "lab.v1.genre.x") == ["analysis.params.baseline"]

    def test_calibration_can_be_added_and_not_removed(self) -> None:
        assert _genres(_genre(), _child(design={"calibration": "aa"})).rejected == {}
        parent = _genre()
        parent["design"]["calibration"] = "aa"
        found = _genres(parent, _child(design={"calibration": "none"}))
        assert _paths(found) == ["design.calibration"]

    @pytest.mark.parametrize(
        ("parent", "child"),
        [("forbidden", "allowed"), ("forbidden", "required"), ("required", "allowed"),
         ("required", "forbidden")],
    )  # fmt: skip
    def test_a_closed_factor_policy_cannot_be_reopened(self, parent: str, child: str) -> None:
        found = _genres(_genre(factors=parent), _child(factors=child))
        assert _paths(found) == ["factors"]

    @pytest.mark.parametrize("child", ["forbidden", "required", "allowed"])
    def test_an_open_factor_policy_can_be_closed(self, child: str) -> None:
        assert _genres(_genre(factors="allowed"), _child(factors=child)).rejected == {}

    def test_a_comparison_can_be_reworded_and_not_added(self) -> None:
        reworded = {"non-inferiority": "{variant} may ship against {control} within {margin}"}
        assert _genres(_genre(), _child(comparisons=reworded)).rejected == {}

    def test_every_violation_is_reported_at_once(self) -> None:
        found = _genres(_genre(), _child(must_set=[], verdict_form="none", report=["question"]))
        assert sorted(_paths(found)) == ["must_set", "report", "verdict_form"]

    def test_depth_is_at_most_two(self) -> None:
        grandchild = {"name": "acme.v1.genre.grandchild", "extends": CHILD}
        found = _genres(_genre(), _child(), grandchild)
        assert found.get(CHILD) is not None
        assert _kinds(found, "acme.v1.genre.grandchild") == ["genre_depth"]
        assert BASE in found.rejected["acme.v1.genre.grandchild"][0].fix

    def test_a_genre_cannot_extend_itself(self) -> None:
        loop = _genre(name="acme.v1.genre.loop", extends="acme.v1.genre.loop")
        assert _kinds(_genres(loop), "acme.v1.genre.loop") == ["genre_depth"]

    def test_composition_is_not_expressible(self) -> None:
        """``extends`` names one genre. A list of parents is not a genre file."""
        found = _genres(_genre(), _child(extends=[BASE, "ix.v1.genre.compare"]))
        assert found.get(CHILD) is None

    def test_an_unknown_parent_is_named_with_the_genres_there_are(self) -> None:
        found = _genres(_genre(), _child(extends="acme.v1.genre.missing"))
        (problem,) = found.rejected[CHILD]
        assert problem.kind == "name_unknown"
        assert problem.got == "acme.v1.genre.missing" and BASE in problem.allowed

    def test_a_child_of_a_refused_parent_is_refused(self) -> None:
        found = _genres(_genre(must_set=["sensor"]), _child())
        assert _kinds(found, BASE) == ["genre_invalid"]
        assert _kinds(found) == ["genre_invalid"]

    def test_two_files_with_one_name_are_both_refused(self) -> None:
        found = _genres(_genre(), _genre(question="Another question?"), _genre())
        assert found.genres == {}
        assert _kinds(found, BASE) == ["name_taken"] * 3
        assert {p.source for p in found.rejected[BASE]} == {
            "file-0.yaml", "file-1.yaml", "file-2.yaml",
        }  # fmt: skip

    def test_a_broken_file_does_not_take_the_others_down(self) -> None:
        unreadable_yaml = a_problem(
            "", kind="yaml_syntax", message="the file is not valid YAML", source="broken.yaml"
        )
        found = genres(
            [
                DeclarationFile(origin="good.yaml", data=_genre()),
                DeclarationFile(origin="broken.yaml", problems=(unreadable_yaml,)),
                DeclarationFile(origin="nameless.yaml", data={"question": "?"}),
            ],
            grammar=type_url_problem,
        )
        assert tuple(found.genres) == (BASE,)
        assert found.rejected["broken.yaml"] == (unreadable_yaml,)  # as the reader said it
        assert _kinds(found, "nameless.yaml") == ["genre_invalid"]


# --- narrowing, as a property ----------------------------------------------------------


def _random_parent(rng: random.Random) -> dict[str, Any]:
    low = rng.randint(1, 2)
    roles = [
        {"name": "control", "min": 1, "max": 1},
        {"name": "variant", "min": low, "max": rng.choice([None, low + 1, low + 3])},
    ]
    names = rng.sample(sorted(FORMS), rng.randint(1, 3))
    minimum = rng.randint(1, 4)
    return _genre(
        roles=roles,
        factors=rng.choice(["forbidden", "allowed", "required"]),
        must_set=rng.sample(sorted(MUST_SETTABLE), rng.randint(0, 4)),
        comparisons={name: FORMS[name] for name in names},
        design={
            "repeats": {"default": minimum + rng.randint(0, 3), "min": minimum},
            "order": rng.choice(["interleaved", "sequential"]),
            "calibration": rng.choice(["aa", "none"]),
            "estimator": rng.choice(["mean", "median"]),
            "note": "a test design",
        },
        verdict_form=rng.choice(["three-way", "none"]),
        report=rng.sample(["question", "design", "estimates", "failures"], rng.randint(1, 4)),
    )


def _tighten(rng: random.Random, parent: dict[str, Any]) -> dict[str, Any]:
    """A child that restates a random subset of fields, each tightened or left as it was."""
    child: dict[str, Any] = {}
    if rng.random() < 0.5:
        roles = copy.deepcopy(parent["roles"])
        variant = roles[1]
        ceiling = variant["max"]
        variant["min"] += (
            rng.randint(0, 1) if ceiling is None else rng.randint(0, ceiling - variant["min"])
        )
        variant["max"] = (
            rng.choice([None, variant["min"], variant["min"] + 5])
            if ceiling is None
            else rng.randint(variant["min"], ceiling)
        )
        child["roles"] = roles
    if rng.random() < 0.5 and parent["factors"] == "allowed":
        child["factors"] = rng.choice(["forbidden", "required", "allowed"])
    if rng.random() < 0.5:
        extra = rng.sample(sorted(MUST_SETTABLE), rng.randint(0, 3))
        child["must_set"] = sorted(set(parent["must_set"]) | set(extra))
    if rng.random() < 0.5:
        names = sorted(parent["comparisons"])
        kept = rng.sample(names, rng.randint(1, len(names)))
        child["comparisons"] = {name: FORMS[name] for name in kept}
    if rng.random() < 0.5:
        minimum = parent["design"]["repeats"]["min"] + rng.randint(0, 2)
        child["design"] = {
            "repeats": {"min": minimum, "default": minimum + rng.randint(0, 2)},
            "order": "interleaved",
            "calibration": "aa",
        }
    if rng.random() < 0.5:
        child["report"] = [*parent["report"], "gate"]
    return child


def _loosen(rng: random.Random, parent: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """One loosening of ``parent`` that applies to it, and the path it must be reported at."""
    options: list[tuple[dict[str, Any], str]] = []
    variant = parent["roles"][1]
    if variant["min"] > 0:
        lower = copy.deepcopy(parent["roles"])
        lower[1]["min"] -= 1
        options.append(({"roles": lower}, "roles.variant.min"))
    if variant["max"] is not None:
        higher = copy.deepcopy(parent["roles"])
        higher[1]["max"] = rng.choice([None, variant["max"] + 1])
        options.append(({"roles": higher}, "roles.variant.max"))
    if parent["factors"] != "allowed":
        options.append(({"factors": "allowed"}, "factors"))
    if parent["must_set"]:
        options.append(({"must_set": parent["must_set"][1:]}, "must_set"))
    absent = [name for name in FORMS if name not in parent["comparisons"]]
    if absent:
        added = {**parent["comparisons"], absent[0]: FORMS[absent[0]]}
        options.append(({"comparisons": added}, "comparisons"))
    if parent["design"]["repeats"]["min"] > 1:
        lowered = {"repeats": {"min": parent["design"]["repeats"]["min"] - 1}}
        options.append(({"design": lowered}, "design.repeats.min"))
    if parent["design"]["order"] == "interleaved":
        options.append(({"design": {"order": "sequential"}}, "design.order"))
    if parent["design"]["calibration"] == "aa":
        options.append(({"design": {"calibration": "none"}}, "design.calibration"))
    other = "median" if parent["design"]["estimator"] == "mean" else "mean"
    options.append(({"design": {"estimator": other}}, "design.estimator"))
    params = parent["analysis"]["params"]
    options.append(({"analysis": {"id": "acme.v1.analysis.new", "params": params}}, "analysis.id"))
    options.append(
        ({"analysis": {"id": parent["analysis"]["id"], "params": {**params, "alpha": 0.1}}},
         "analysis.params.alpha")
    )  # fmt: skip
    flipped = "none" if parent["verdict_form"] == "three-way" else "three-way"
    options.append(({"verdict_form": flipped}, "verdict_form"))
    options.append(({"report": [*parent["report"][1:], "extra"]}, "report"))
    return rng.choice(options)


class TestNarrowingProperty:
    """Over many random parents: any tightening is accepted; any single loosening is
    refused at the field that was loosened."""

    def test_a_tightened_child_is_always_accepted(self) -> None:
        rng = random.Random(20261013)
        for case in range(300):
            parent = _random_parent(rng)
            child = _child(**_tighten(rng, parent))
            found = _genres(parent, child)
            assert found.rejected == {}, (case, child, found.rejected)
            accepted, base = found.get(CHILD), found.get(BASE)
            assert accepted is not None and base is not None
            assert narrowing_violations(base, accepted) == []

    def test_a_loosened_child_is_always_refused(self) -> None:
        rng = random.Random(1337)
        for case in range(300):
            parent = _random_parent(rng)
            change, path = _loosen(rng, parent)
            found = _genres(parent, _child(**change))
            assert found.get(CHILD) is None, (case, change)
            assert path in _paths(found), (case, change, found.rejected)

    def test_a_loosening_is_not_hidden_by_tightenings_beside_it(self) -> None:
        rng = random.Random(7)
        for case in range(200):
            parent = _random_parent(rng)
            tightened = _tighten(rng, parent)
            change, path = _loosen(rng, parent)
            child = _child(**{**tightened, **change})
            if "design" in tightened and "design" in change:
                child["design"] = {**tightened["design"], **change["design"]}
                repeats = child["design"]["repeats"]
                repeats["default"] = repeats["min"] + 3
            found = _genres(parent, child)
            assert path in _paths(found), (case, child, found.rejected)

    def test_a_genre_narrows_itself(self) -> None:
        rng = random.Random(3)
        for _ in range(100):
            genre = Genre.model_validate(_random_parent(rng))
            assert narrowing_violations(genre, genre) == []


# --- the extension point ---------------------------------------------------------------


class TestGenreExtensionPoint:
    def test_ix_registers_its_genres_at_the_point_it_owns(self) -> None:
        points = {point.name: point.value for point in metadata.entry_points(group="ix.genre")}
        assert GENRE_GROUP == "ix.genre"
        assert points.get("ix") == "ix.builtin.genres"

    def test_discovery_finds_the_built_ins(self) -> None:
        assert set(BUILT_IN) <= set(genres(GenreFiles().files(), grammar=type_url_problem).names())

    def test_a_third_party_ships_a_genre_as_a_data_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A package with a YAML file and no Python beyond ``__init__``, named at the entry
        point, adds a genre."""
        package = tmp_path / "acme_ix" / "genres"
        package.mkdir(parents=True)
        (tmp_path / "acme_ix" / "__init__.py").write_text("")
        (package / "__init__.py").write_text("")
        soak = {"name": "acme.v1.genre.soak", "extends": "characterize",
                "question": "Does it hold for hours?"}  # fmt: skip
        (package / "soak.yaml").write_text(yaml.safe_dump(soak))
        monkeypatch.syspath_prepend(str(tmp_path))

        real = metadata.entry_points

        def entry_points(**params: Any) -> Any:
            found = list(real(**params))
            if params.get("group") == "ix.genre":
                found.append(metadata.EntryPoint("acme", "acme_ix.genres", "ix.genre"))
            return found

        monkeypatch.setattr(metadata, "entry_points", entry_points)
        found = genres(GenreFiles().files(), grammar=type_url_problem)
        soaked = found.get("acme.v1.genre.soak")
        assert soaked is not None, found.rejected
        assert soaked.extends == "ix.v1.genre.characterize"
        assert soaked.factors == "required"
        assert soaked.label == "acme.v1.genre.soak"

    def test_a_package_that_cannot_be_read_is_one_refused_file(self) -> None:
        files = GenreFiles(packages=["ix.builtin.genres", "no_such_package_anywhere"]).files()
        found = genres(files, grammar=type_url_problem)
        assert len(found.genres) == len(BUILT_IN)
        # The package named is not there: a name not defined.
        assert [problems[0].kind for problems in found.rejected.values()] == ["name_unknown"]

    def test_a_lab_keeps_genres_beside_its_experiment(self, tmp_path: Path) -> None:
        (tmp_path / "genres").mkdir()
        gate = {"name": "lab.v1.genre.gate", "extends": "regress"}
        (tmp_path / "genres" / "gate.yaml").write_text(yaml.safe_dump(gate))
        library = GenreFiles(packages=["ix.builtin.genres"])
        found = genres(library.files(str(tmp_path)), grammar=type_url_problem)
        assert _kinds(found, "lab.v1.genre.gate") == ["genre_depth"]
        gate["extends"] = "compare"
        (tmp_path / "genres" / "gate.yaml").write_text(yaml.safe_dump(gate))
        found = genres(library.files(str(tmp_path)), grammar=type_url_problem)
        assert found.get("lab.v1.genre.gate") is not None
        assert genres(library.files(), grammar=type_url_problem).get("lab.v1.genre.gate") is None

    def test_a_genre_file_is_data_and_loads_no_code(self, tmp_path: Path) -> None:
        """A YAML tag that would construct a Python object is a refused file, not a call."""
        (tmp_path / "genres").mkdir()
        marker = tmp_path / "ran"
        (tmp_path / "genres" / "evil.yaml").write_text(
            f"name: !!python/object/apply:pathlib.Path.touch [{str(marker)!r}]\n"
        )
        found = genres(GenreFiles(packages=[]).files(str(tmp_path)), grammar=type_url_problem)
        assert not marker.exists()
        assert found.genres == {}
        # matrix's loader reads YAML safely: the tag is a syntax it refuses.
        assert [p[0].kind for p in found.rejected.values()] == ["yaml_syntax"]

"""Genres are data: a record per file, extended only by narrowing, one level deep.

The requirements pinned here: a child may tighten its parent and never loosen it or add a
mechanism; ``extends`` goes one level; a genre file holds no code and names no stimulus kind;
every default says it is provisional; a third party ships a genre as a data file.
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

from ix.adapters._out.genre_files import ENTRY_POINT_GROUP, GenreFiles
from ix.domain.genre import (
    REQUIRABLE,
    Catalogue,
    Genre,
    GenreRecord,
    catalogue,
    narrowing_violations,
)

BUILT_IN = ("characterise", "compare", "evaluate", "regress")
RULES = ("superiority", "non-inferiority", "equivalence")
NOTE = "a test default"


def _default(value: Any) -> dict[str, Any]:
    return {"value": value, "note": NOTE}


def _genre(name: str = "acme.v1.genre.base", **changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema": "ix.v1.genre",
        "name": name,
        "question": "Is the variant no worse?",
        "hypothesis_form": "{variant} is no worse than {control}",
        "subjects": [{"name": "control", "min": 1, "max": 1}, {"name": "variant", "min": 1}],
        "factors": "allowed",
        "requires": ["primary"],
        "rules": list(RULES),
        "defaults": {
            "repeats": _default(5),
            "min_repeats": _default(3),
            "interleave": _default(True),
            "aa": _default(False),
        },
        "analysis": {"id": "acme.v1.analysis.base", "params": {"correction": "holm"}},
        "verdict": "three-way",
        "report": ["question", "estimates"],
        "skill": "Decide the margin first.",
    }
    data.update(changes)
    return data


def _catalogue(*records: dict[str, Any]) -> Catalogue:
    return catalogue(GenreRecord(origin=f"file-{i}.yaml", data=r) for i, r in enumerate(records))


def _child(**changes: Any) -> dict[str, Any]:
    return {"name": "acme.v1.genre.child", "extends": "acme.v1.genre.base", **changes}


def _kinds(found: Catalogue, name: str = "acme.v1.genre.child") -> list[str]:
    return [problem.kind for problem in found.rejected.get(name, ())]


def _paths(found: Catalogue, name: str = "acme.v1.genre.child") -> list[str]:
    return [problem.path for problem in found.rejected.get(name, ())]


# --- the record ------------------------------------------------------------------------


class TestGenreRecord:
    def test_unknown_key_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            Genre.model_validate(_genre(hook="acme.hooks:expand"))

    def test_a_default_without_a_note_is_refused(self) -> None:
        data = _genre()
        data["defaults"]["repeats"] = {"value": 5}
        with pytest.raises(ValidationError, match="note"):
            Genre.model_validate(data)

    def test_default_repeats_cannot_be_below_the_minimum(self) -> None:
        data = _genre()
        data["defaults"]["repeats"] = _default(2)
        with pytest.raises(ValidationError, match="below min_repeats"):
            Genre.model_validate(data)

    def test_requires_must_name_experiment_fields(self) -> None:
        with pytest.raises(ValidationError, match="not experiment fields"):
            Genre.model_validate(_genre(requires=["sensor"]))

    def test_default_rule_must_be_one_of_the_rules(self) -> None:
        data = _genre(rules=["superiority"])
        data["defaults"]["rule"] = _default("equivalence")
        with pytest.raises(ValidationError, match="not among"):
            Genre.model_validate(data)

    def test_one_role_besides_control(self) -> None:
        roles = [{"name": "control"}, {"name": "variant"}, {"name": "twin"}]
        with pytest.raises(ValidationError, match="at most one role besides"):
            Genre.model_validate(_genre(subjects=roles))

    def test_name_is_a_type_url(self) -> None:
        with pytest.raises(ValidationError):
            Genre.model_validate(_genre(name="soak"))

    def test_analysis_params_hold_no_structure(self) -> None:
        """Parameters are scalars: nothing a genre carries can be a program."""
        data = _genre(analysis={"id": "a.v1.analysis.x", "params": {"steps": ["a", "b"]}})
        with pytest.raises(ValidationError):
            Genre.model_validate(data)


# --- the built-in files ----------------------------------------------------------------


class TestBuiltInGenres:
    def test_the_four_load_clean(self, builtin_genres: Catalogue) -> None:
        assert builtin_genres.rejected == {}
        assert builtin_genres.names() == tuple(f"ix.v1.genre.{name}" for name in BUILT_IN)

    def test_short_and_full_names_resolve(self, builtin_genres: Catalogue) -> None:
        assert builtin_genres.get("compare") is builtin_genres.get("ix.v1.genre.compare")
        assert builtin_genres.get("nope") is None

    def test_regress_extends_compare_and_only_narrows(self, builtin_genres: Catalogue) -> None:
        compare, regress = builtin_genres.get("compare"), builtin_genres.get("regress")
        assert compare is not None and regress is not None
        assert regress.extends == compare.name
        assert narrowing_violations(compare, regress) == []
        assert regress.analysis.id == compare.analysis.id
        assert set(regress.rules) < set(compare.rules)
        assert set(regress.requires) > set(compare.requires)
        variant = regress.role("variant")
        assert variant is not None and variant.max == 1

    def test_regress_takes_what_it_does_not_restate_from_compare(
        self, builtin_genres: Catalogue
    ) -> None:
        compare, regress = builtin_genres.get("compare"), builtin_genres.get("regress")
        assert compare is not None and regress is not None
        assert regress.factors == compare.factors
        assert regress.verdict == compare.verdict
        assert regress.defaults.repeats == compare.defaults.repeats
        assert regress.defaults.aa == compare.defaults.aa

    def test_compare_has_one_control_and_one_or_more_variants(
        self, builtin_genres: Catalogue
    ) -> None:
        compare = builtin_genres.get("compare")
        assert compare is not None
        control, variant = compare.role("control"), compare.role("variant")
        assert control is not None and (control.min, control.max) == (1, 1)
        assert variant is not None and (variant.min, variant.max) == (1, None)

    def test_every_default_says_it_is_provisional(self, builtin_genres: Catalogue) -> None:
        for genre in builtin_genres.genres.values():
            defaults = genre.defaults.model_dump()
            for field, default in defaults.items():
                if default is None:
                    continue
                note = default["note"]
                assert "provisional" in note, f"{genre.name}.{field}"
                assert "experimentation canon" in note, f"{genre.name}.{field}"

    def test_extension_is_at_most_one_level(self, builtin_genres: Catalogue) -> None:
        for genre in builtin_genres.genres.values():
            if genre.extends is not None:
                parent = builtin_genres.get(genre.extends)
                assert parent is not None and parent.extends is None

    def test_a_genre_file_names_no_stimulus_kind(self) -> None:
        """No probe kind, sensor, subject kind or tool appears in a genre file: not in a
        field that ix acts on (any word), and not in prose (the unambiguous ids)."""
        acted_on = {
            *("process", "http", "agent", "command"),  # subject kinds
            *("prompt", "http-load", "inspect-task"),  # probe kinds
            *("timing", "memory", "activation", "function-test", "usage", "judge"),  # sensors
            *("k6", "wrk2", "oha", "locust", "inspect", "compose", "kind"),  # tools, providers
        }
        in_prose = {"http", "http-load", "inspect-task", "function-test", "k6", "wrk2", "oha"}
        prose_fields = {"question", "hypothesis_form", "skill"}

        def words(value: Any) -> set[str]:
            if isinstance(value, dict):
                return set().union(*(words(k) | words(v) for k, v in value.items()), set())
            if isinstance(value, list):
                return set().union(*(words(item) for item in value), set())
            return set(str(value).lower().replace(",", " ").replace(".", " ").split())

        genres = Path(__file__).resolve().parents[2] / "src" / "ix" / "genres"
        files = sorted(genres.glob("*.yaml"))
        assert [file.stem for file in files] == list(BUILT_IN)
        for file in files:
            data = yaml.safe_load(file.read_text(encoding="utf-8"))
            for field, value in data.items():
                if field in prose_fields:
                    assert not (words(value) & in_prose), f"{file.name}: {field}"
                elif field == "defaults":
                    values = {key: default["value"] for key, default in value.items()}
                    notes = [default["note"] for default in value.values()]
                    assert not (words(values) & acted_on), f"{file.name}: defaults"
                    assert not (words(notes) & in_prose), f"{file.name}: default notes"
                else:
                    assert not (words(value) & acted_on), f"{file.name}: {field}"


# --- extension: narrowing only ---------------------------------------------------------


class TestExtension:
    def test_a_child_inherits_what_it_omits(self) -> None:
        found = _catalogue(_genre(), _child(requires=["primary", "margin"]))
        child = found.get("acme.v1.genre.child")
        assert child is not None, found.rejected
        assert child.requires == ("primary", "margin")
        assert child.rules == RULES
        assert child.extends == "acme.v1.genre.base"

    def test_a_child_restates_one_default_and_keeps_the_rest(self) -> None:
        found = _catalogue(_genre(), _child(defaults={"min_repeats": _default(4)}))
        child = found.get("acme.v1.genre.child")
        assert child is not None, found.rejected
        assert child.defaults.min_repeats.value == 4
        assert child.defaults.repeats.value == 5

    def test_extends_takes_a_short_name_of_a_built_in(self, builtin_genres: Catalogue) -> None:
        records = [*GenreFiles(packages=["ix.genres"]).records()]
        records.append(
            GenreRecord(
                origin="lab/genres/gate.yaml",
                data={"name": "acme.v1.genre.gate", "extends": "compare", "rules": ["superiority"]},
            )
        )
        found = catalogue(records)
        gate = found.get("acme.v1.genre.gate")
        assert gate is not None, found.rejected
        assert gate.extends == "ix.v1.genre.compare"

    @pytest.mark.parametrize(
        ("change", "path"),
        [
            (
                {"subjects": [{"name": "control", "min": 0, "max": 1}, {"name": "variant"}]},
                "subjects.control.min",
            ),
            (
                {"subjects": [{"name": "control", "min": 1, "max": 2}, {"name": "variant"}]},
                "subjects.control.max",
            ),
            (
                {"subjects": [{"name": "control", "min": 1}, {"name": "variant"}]},
                "subjects.control.max",
            ),
            ({"subjects": [{"name": "control", "min": 1, "max": 1}]}, "subjects.variant"),
            (
                {"subjects": [{"name": "control", "min": 1, "max": 1}, {"name": "twin"}]},
                "subjects.twin",
            ),
            ({"requires": []}, "requires"),
            ({"rules": [*RULES, "bayesian"]}, "rules"),
            ({"rules": []}, "rules"),
            (
                {"defaults": {"min_repeats": _default(2), "repeats": _default(5)}},
                "defaults.min_repeats",
            ),
            ({"defaults": {"interleave": _default(False)}}, "defaults.interleave"),
            (
                {"analysis": {"id": "acme.v1.analysis.other", "params": {"correction": "holm"}}},
                "analysis.id",
            ),
            (
                {"analysis": {"id": "acme.v1.analysis.base", "params": {}}},
                "analysis.params.correction",
            ),
            (
                {"analysis": {"id": "acme.v1.analysis.base", "params": {"correction": "none"}}},
                "analysis.params.correction",
            ),
            ({"verdict": "none"}, "verdict"),
            ({"report": ["question"]}, "report"),
        ],
    )
    def test_loosening_or_adding_a_mechanism_is_refused(
        self, change: dict[str, Any], path: str
    ) -> None:
        found = _catalogue(_genre(), _child(**change))
        assert found.get("acme.v1.genre.child") is None
        assert set(_kinds(found)) == {"genre_narrowing"}
        assert path in _paths(found)
        for problem in found.rejected["acme.v1.genre.child"]:
            assert problem.fix and problem.source == "file-1.yaml"
        assert found.get("acme.v1.genre.base") is not None, "the parent is unaffected"

    @pytest.mark.parametrize(
        ("parent", "child"),
        [
            ("forbidden", "allowed"),
            ("forbidden", "required"),
            ("required", "allowed"),
            ("required", "forbidden"),
        ],
    )
    def test_a_closed_factor_rule_cannot_be_reopened(self, parent: str, child: str) -> None:
        found = _catalogue(_genre(factors=parent), _child(factors=child))
        assert _paths(found) == ["factors"]

    @pytest.mark.parametrize("child", ["forbidden", "required", "allowed"])
    def test_an_open_factor_rule_can_be_closed(self, child: str) -> None:
        found = _catalogue(_genre(factors="allowed"), _child(factors=child))
        assert found.rejected == {}

    def test_every_violation_is_reported_at_once(self) -> None:
        found = _catalogue(_genre(), _child(requires=[], verdict="none", report=["question"]))
        assert sorted(_paths(found)) == ["report", "requires", "verdict"]

    def test_depth_is_at_most_two(self) -> None:
        grandchild = {"name": "acme.v1.genre.grandchild", "extends": "acme.v1.genre.child"}
        found = _catalogue(_genre(), _child(), grandchild)
        assert found.get("acme.v1.genre.child") is not None
        assert _kinds(found, "acme.v1.genre.grandchild") == ["genre_depth"]
        assert "acme.v1.genre.base" in found.rejected["acme.v1.genre.grandchild"][0].fix

    def test_a_genre_cannot_extend_itself(self) -> None:
        loop = _genre(name="acme.v1.genre.loop", extends="acme.v1.genre.loop")
        assert _kinds(_catalogue(loop), "acme.v1.genre.loop") == ["genre_depth"]

    def test_composition_is_not_expressible(self) -> None:
        """``extends`` names one genre. A list of parents is not a genre file."""
        both = _child(extends=["acme.v1.genre.base", "ix.v1.genre.compare"])
        found = _catalogue(_genre(), both)
        assert found.get("acme.v1.genre.child") is None

    def test_an_unknown_parent_is_named(self) -> None:
        found = _catalogue(_child(extends="acme.v1.genre.missing"))
        assert _kinds(found) == ["genre_extends_unknown"]

    def test_a_child_of_a_refused_parent_is_refused(self) -> None:
        found = _catalogue(_genre(requires=["sensor"]), _child())
        assert _kinds(found, "acme.v1.genre.base") == ["genre_invalid"]
        assert _kinds(found) == ["genre_invalid"]

    def test_two_files_with_one_name_are_both_refused(self) -> None:
        found = _catalogue(_genre(), _genre(question="Another question?"), _genre())
        assert found.genres == {}
        assert _kinds(found, "acme.v1.genre.base") == ["genre_duplicate"] * 3
        sources = {problem.source for problem in found.rejected["acme.v1.genre.base"]}
        assert sources == {"file-0.yaml", "file-1.yaml", "file-2.yaml"}

    def test_a_broken_file_does_not_take_the_others_down(self) -> None:
        found = catalogue(
            [
                GenreRecord(origin="good.yaml", data=_genre()),
                GenreRecord(origin="broken.yaml", error="mapping values are not allowed here"),
                GenreRecord(origin="nameless.yaml", data={"question": "?"}),
            ]
        )
        assert found.names() == ("acme.v1.genre.base",)
        assert _kinds(found, "broken.yaml") == ["genre_invalid"]
        assert _kinds(found, "nameless.yaml") == ["genre_invalid"]


# --- narrowing, as a property ----------------------------------------------------------


def _random_parent(rng: random.Random) -> dict[str, Any]:
    low = rng.randint(0, 2)
    roles = [{"name": "variant", "min": low, "max": rng.choice([None, low + 1, low + 3])}]
    if rng.random() < 0.6:
        roles.insert(0, {"name": "control", "min": 1, "max": 1})
    rules = rng.sample(RULES, rng.randint(0, 3))
    minimum = rng.randint(1, 4)
    defaults = {
        "repeats": _default(minimum + rng.randint(0, 3)),
        "min_repeats": _default(minimum),
        "interleave": _default(rng.random() < 0.5),
        "aa": _default(rng.random() < 0.5),
    }
    return _genre(
        subjects=roles,
        factors=rng.choice(["forbidden", "allowed", "required"]),
        requires=rng.sample(sorted(REQUIRABLE), rng.randint(0, 4)),
        rules=rules,
        defaults=defaults,
        analysis={"id": "acme.v1.analysis.base", "params": {"correction": "holm"}},
        verdict=rng.choice(["three-way", "none"]),
        report=rng.sample(["question", "design", "estimates", "faults"], rng.randint(1, 4)),
        presupposes=rng.sample(["noise-floor", "baseline"], rng.randint(0, 2)),
    )


def _tighten(rng: random.Random, parent: dict[str, Any]) -> dict[str, Any]:
    """A child that restates a random subset of fields, each tightened or left as it was."""
    child: dict[str, Any] = {}
    if rng.random() < 0.5:
        roles = copy.deepcopy(parent["subjects"])
        for role in roles:
            if role["name"] == "control":
                continue
            ceiling = role["max"]
            role["min"] += (
                rng.randint(0, 1) if ceiling is None else rng.randint(0, ceiling - role["min"])
            )
            floor = max(role["min"], 1)  # a role nobody may play is not a role
            role["max"] = (
                rng.choice([None, floor, floor + 5])
                if ceiling is None
                else rng.randint(floor, ceiling)
            )
        child["subjects"] = roles
    if rng.random() < 0.5 and parent["factors"] == "allowed":
        child["factors"] = rng.choice(["forbidden", "required", "allowed"])
    if rng.random() < 0.5:
        extra = rng.sample(sorted(REQUIRABLE), rng.randint(0, 3))
        child["requires"] = sorted(set(parent["requires"]) | set(extra))
    if rng.random() < 0.5 and parent["rules"]:
        child["rules"] = rng.sample(parent["rules"], rng.randint(1, len(parent["rules"])))
    if rng.random() < 0.5:
        minimum = parent["defaults"]["min_repeats"]["value"] + rng.randint(0, 2)
        child["defaults"] = {
            "min_repeats": _default(minimum),
            "repeats": _default(minimum + rng.randint(0, 2)),
            "interleave": _default(True),
            "aa": _default(parent["defaults"]["aa"]["value"] or rng.random() < 0.5),
        }
    if rng.random() < 0.5:
        child["analysis"] = {
            "id": parent["analysis"]["id"],
            "params": {**parent["analysis"]["params"], "baseline": "rebuilt"},
        }
    if rng.random() < 0.5:
        child["report"] = [*parent["report"], "gate"]
        child["presupposes"] = [*parent["presupposes"], "registration"]
    return child


def _loosen(rng: random.Random, parent: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """One loosening of ``parent`` that applies to it, and the path it must be reported at."""
    options: list[tuple[dict[str, Any], str]] = []
    roles = copy.deepcopy(parent["subjects"])
    variant = next(role for role in roles if role["name"] == "variant")
    if variant["min"] > 0:
        lower = copy.deepcopy(roles)
        next(r for r in lower if r["name"] == "variant")["min"] -= 1
        options.append(({"subjects": lower}, "subjects.variant.min"))
    if variant["max"] is not None:
        higher = copy.deepcopy(roles)
        next(r for r in higher if r["name"] == "variant")["max"] = rng.choice(
            [None, variant["max"] + 1]
        )
        options.append(({"subjects": higher}, "subjects.variant.max"))
    if parent["factors"] != "allowed":
        options.append(({"factors": "allowed"}, "factors"))
    if parent["requires"]:
        options.append(({"requires": parent["requires"][1:]}, "requires"))
    absent = [rule for rule in RULES if rule not in parent["rules"]]
    if absent:
        options.append(({"rules": [*parent["rules"], absent[0]]}, "rules"))
    if parent["defaults"]["min_repeats"]["value"] > 1:
        lowered = {"min_repeats": _default(parent["defaults"]["min_repeats"]["value"] - 1)}
        options.append(({"defaults": lowered}, "defaults.min_repeats"))
    for flag in ("interleave", "aa"):
        if parent["defaults"][flag]["value"]:
            options.append(({"defaults": {flag: _default(False)}}, f"defaults.{flag}"))
    options.append(
        (
            {"analysis": {"id": "acme.v1.analysis.new", "params": parent["analysis"]["params"]}},
            "analysis.id",
        )
    )
    flipped = "none" if parent["verdict"] == "three-way" else "three-way"
    options.append(({"verdict": flipped}, "verdict"))
    options.append(({"report": parent["report"][1:] + ["extra"]}, "report"))
    return rng.choice(options)


class TestNarrowingProperty:
    """Over many random parents: any tightening is accepted; any single loosening is
    refused at the field that was loosened."""

    def test_a_tightened_child_is_always_accepted(self) -> None:
        rng = random.Random(20261012)
        for case in range(300):
            parent = _random_parent(rng)
            child = _child(**_tighten(rng, parent))
            found = _catalogue(parent, child)
            assert found.rejected == {}, (case, child, found.rejected)
            accepted = found.get("acme.v1.genre.child")
            base = found.get("acme.v1.genre.base")
            assert accepted is not None and base is not None
            assert narrowing_violations(base, accepted) == []

    def test_a_loosened_child_is_always_refused(self) -> None:
        rng = random.Random(1337)
        for case in range(300):
            parent = _random_parent(rng)
            change, path = _loosen(rng, parent)
            found = _catalogue(parent, _child(**change))
            assert found.get("acme.v1.genre.child") is None, (case, change)
            assert "genre_narrowing" in _kinds(found), (case, change, found.rejected)
            assert path in _paths(found), (case, change, _paths(found))

    def test_tightening_then_loosening_is_still_refused(self) -> None:
        """A loosening is not hidden by tightenings made alongside it."""
        rng = random.Random(7)
        for case in range(200):
            parent = _random_parent(rng)
            tightened = _tighten(rng, parent)
            change, path = _loosen(rng, parent)
            child = _child(**{**tightened, **change})
            if "defaults" in tightened and "defaults" in change:
                child["defaults"] = {**tightened["defaults"], **change["defaults"]}
                minimum = child["defaults"]["min_repeats"]["value"]
                child["defaults"]["repeats"] = _default(minimum + 3)
            found = _catalogue(parent, child)
            assert path in _paths(found), (case, child, found.rejected)

    def test_narrowing_is_transitive_in_what_it_allows(self) -> None:
        """A genre narrows itself: the check accepts a record against itself."""
        rng = random.Random(3)
        for _ in range(100):
            genre = Genre.model_validate(_random_parent(rng))
            assert narrowing_violations(genre, genre) == []


# --- the extension point ---------------------------------------------------------------


class TestGenreExtensionPoint:
    def test_ix_registers_its_genres_at_the_point_it_owns(self) -> None:
        points = {point.name: point.value for point in metadata.entry_points(group="ix.genre")}
        assert ENTRY_POINT_GROUP == "ix.genre"
        assert points.get("ix") == "ix.genres"

    def test_discovery_finds_the_built_ins(self) -> None:
        found = catalogue(GenreFiles().records())
        assert {f"ix.v1.genre.{name}" for name in BUILT_IN} <= set(found.names())

    def test_a_third_party_ships_a_genre_as_a_data_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A package with a YAML file and no Python beyond ``__init__``, named at the entry
        point, adds a genre."""
        package = tmp_path / "acme_ix" / "genres"
        package.mkdir(parents=True)
        (tmp_path / "acme_ix" / "__init__.py").write_text("")
        (package / "__init__.py").write_text("")
        soak = {
            "name": "acme.v1.genre.soak",
            "extends": "characterise",
            "question": "Does it hold for hours?",
        }
        (package / "soak.yaml").write_text(yaml.safe_dump(soak))
        monkeypatch.syspath_prepend(str(tmp_path))

        real = metadata.entry_points

        def entry_points(**params: Any) -> Any:
            found = list(real(**params))
            if params.get("group") == "ix.genre":
                found.append(metadata.EntryPoint("acme", "acme_ix.genres", "ix.genre"))
            return found

        monkeypatch.setattr(metadata, "entry_points", entry_points)
        found = catalogue(GenreFiles().records())
        soaked = found.get("acme.v1.genre.soak")
        assert soaked is not None, found.rejected
        assert soaked.extends == "ix.v1.genre.characterise"
        assert soaked.factors == "required"

    def test_a_package_that_cannot_be_read_is_one_refused_record(self) -> None:
        records = GenreFiles(packages=["ix.genres", "no_such_package_anywhere"]).records()
        found = catalogue(records)
        assert len(found.genres) == len(BUILT_IN)
        assert [problems[0].kind for problems in found.rejected.values()] == ["genre_invalid"]

    def test_a_lab_keeps_genres_beside_its_experiment(self, tmp_path: Path) -> None:
        (tmp_path / "genres").mkdir()
        gate = {"name": "lab.v1.genre.gate", "extends": "compare", "rules": ["superiority"]}
        (tmp_path / "genres" / "gate.yaml").write_text(yaml.safe_dump(gate))
        library = GenreFiles(packages=["ix.genres"])
        beside = catalogue(library.records(str(tmp_path / "experiment.yaml")))
        assert beside.get("lab.v1.genre.gate") is not None, beside.rejected
        assert catalogue(library.records()).get("lab.v1.genre.gate") is None

    def test_a_genre_file_is_data_and_loads_no_code(self, tmp_path: Path) -> None:
        """A YAML tag that would construct a Python object is a refused file, not a call."""
        (tmp_path / "genres").mkdir()
        marker = tmp_path / "ran"
        (tmp_path / "genres" / "evil.yaml").write_text(
            f"name: !!python/object/apply:pathlib.Path.touch [{str(marker)!r}]\n"
        )
        library = GenreFiles(packages=[])
        found = catalogue(library.records(str(tmp_path / "experiment.yaml")))
        assert not marker.exists()
        assert found.genres == {}
        assert [p[0].kind for p in found.rejected.values()] == ["genre_invalid"]

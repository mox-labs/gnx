"""The plan is pure and repeatable; a registration freezes the decision and shows deviations.

``plan`` reads the experiment and nothing else, so the same file gives the same plan and the
same digest, and a change to what a run would do changes the digest. ``register`` writes the
frozen parameters beside the experiment; whatever differs afterwards is listed, field by
field, and the record is never silently replaced.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
import yaml

import ix
from ix.adapters._out.experiment_files import FileRegistrations, YamlExperimentFiles
from ix.adapters._out.genre_files import GenreFiles
from ix.adapters._out.probe_files import ProbeFiles
from ix.application.experiments import Experiments
from ix.domain.design import resolve
from ix.domain.experiment import probe_set
from ix.domain.plan import Plan, cells, plan
from ix.domain.registration import FROZEN, Registration, deviations, freeze
from ix.domain.validation import parse

if TYPE_CHECKING:
    from pathlib import Path

    from ix.domain.genre import Catalogue

Write = Any  # the write_experiment fixture


def _plan(data: dict[str, Any], genres: Catalogue) -> Plan:
    experiment, problems = parse(data)
    assert experiment is not None, problems
    genre = genres.get(experiment.genre)
    assert genre is not None and isinstance(experiment.probes, tuple)
    return plan(experiment, resolve(experiment, genre), probe_set(experiment.probes))


# --- the plan --------------------------------------------------------------------------


class TestPlan:
    def test_the_same_file_gives_the_same_digest(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file = write_experiment(compare_data)
        first, second = ix.plan(file), ix.plan(file)
        assert first == second
        assert first.plan == second.plan and len(first.plan) == 64

    def test_the_digest_ignores_comments_and_key_order(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        one = write_experiment(compare_data, "one")
        other = write_experiment(dict(reversed(list(compare_data.items()))), "other")
        other.write_text("# reviewed 2026-10-12\n" + other.read_text())
        assert ix.plan(one).plan == ix.plan(other).plan

    def test_the_digest_is_over_every_other_field(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        from ix.domain.schema import digest

        made = _plan(compare_data, builtin_genres)
        document = made.document()
        assert document.pop("plan") == made.plan
        assert digest(document) == made.plan

    @pytest.mark.parametrize(
        "change",
        [
            {"repeats": 7},
            {"seed": 3},
            {"margin": "6%"},
            {"control": "streaming"},
            {"factors": {"size": ["1MiB", "1GiB"]}},
            {"spend": {"ceiling": 100, "per_trial": 0.01}},
            {"genre": "regress", "rule": None},
        ],
    )
    def test_a_change_to_what_a_run_would_do_changes_the_digest(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue, change: dict[str, Any]
    ) -> None:
        before = _plan(compare_data, builtin_genres)
        after = _plan({**compare_data, **change}, builtin_genres)
        assert after.plan != before.plan

    def test_a_changed_probe_changes_the_digest(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        before = _plan(compare_data, builtin_genres)
        compare_data["probes"][0]["target"] = "http://127.0.0.1:8080/y"
        after = _plan(compare_data, builtin_genres)
        assert after.probes.digest != before.probes.digest and after.plan != before.plan

    def test_it_states_roles_and_which_subject_is_the_control(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        made = _plan(compare_data, builtin_genres)
        assert made.control == "buffered"
        assert [(s.name, s.role, s.kind) for s in made.subjects] == [
            ("buffered", "control", "process"),
            ("streaming", "variant", "process"),
        ]
        flipped = _plan({**compare_data, "control": "streaming"}, builtin_genres)
        assert flipped.control == "streaming"

    def test_cells_are_every_combination_of_levels(self) -> None:
        assert cells({}) == ({},)
        grid = cells({"size": ("1MiB", "1GiB"), "concurrency": (1, 8, 64)})
        assert len(grid) == 6
        assert grid[0] == {"size": "1MiB", "concurrency": 1}
        assert grid[-1] == {"size": "1GiB", "concurrency": 64}
        assert len({tuple(cell.items()) for cell in grid}) == 6

    def test_trials_are_subjects_by_cells_by_probes_by_repeats_plus_calibration(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        compare_data["factors"] = {"size": ["1MiB", "64MiB", "1GiB"], "concurrency": [1, 8, 64]}
        compare_data["repeats"] = 5
        made = _plan(compare_data, builtin_genres)
        assert (len(made.cells), made.probes.count, made.repeats) == (9, 1, 5)
        assert made.calibration.aa and made.calibration.subject == "buffered"
        assert made.calibration.trials == 9 * 1 * 5
        assert made.trials == 2 * 9 * 1 * 5 + 45

    def test_no_calibration_where_the_genre_has_none(
        self, evaluate_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        made = _plan(evaluate_data, builtin_genres)
        assert made.calibration.trials == 0 and made.calibration.subject is None
        assert made.trials == 1 * 1 * 1 * made.repeats

    def test_the_order_rule_is_interleaved_and_seeded(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        made = _plan({**compare_data, "seed": 42}, builtin_genres)
        assert (made.order.rule, made.order.seed) == ("interleaved", 42)
        assert "random order" in made.order.description

    def test_effects_are_those_the_file_declares_with_where(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        compare_data["subjects"]["buffered"]["effects"] = ["starts process ./proxy"]
        compare_data["probes"][0]["effects"] = ["network to 127.0.0.1:8080"]
        compare_data["environment"] = {"provider": "process", "effects": ["binds :9000"]}
        made = _plan(compare_data, builtin_genres)
        assert [(e.effect, e.declared_by) for e in made.effects] == [
            ("binds :9000", "environment"),
            ("starts process ./proxy", "subjects.buffered"),
            ("network to 127.0.0.1:8080", "probes.0"),
        ]
        assert any("the file declares" in note for note in made.notes)

    def test_spend_is_estimated_from_the_declared_cost(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        made = _plan({**compare_data, "spend": {"ceiling": 5, "per_trial": 0.02}}, builtin_genres)
        assert made.spend.estimated == pytest.approx(made.trials * 0.02)
        assert (made.spend.ceiling, made.spend.currency) == (5, "USD")
        assert str(made.trials) in made.spend.basis

    def test_spend_is_not_invented(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        assert _plan(compare_data, builtin_genres).spend.estimated is None
        ceiling_only = _plan({**compare_data, "spend": {"ceiling": 5}}, builtin_genres).spend
        assert ceiling_only.estimated is None and ceiling_only.ceiling == 5
        assert "per_trial" in ceiling_only.basis

    def test_the_plan_shows_every_setting_with_its_source(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        made = _plan(compare_data, builtin_genres)
        sources = {s.field: s.source for s in made.settings}
        assert sources["repeats"] == "ix.v1.genre.compare"
        assert sources["rule"] == "experiment"
        assert made.document()["schema"] == "ix.v2.plan"

    def test_an_invalid_experiment_is_not_planned(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        del compare_data["margin"]
        file = write_experiment(compare_data)
        with pytest.raises(ix.InvalidExperimentError) as raised:
            ix.plan(file)
        assert [p.kind for p in raised.value.problems] == ["rule_requires_margin"]
        assert raised.value.fix == f"ix validate {file}"

    def test_planning_writes_nothing(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file = write_experiment(compare_data)
        before = sorted(p.name for p in file.parent.iterdir())
        ix.plan(file)
        ix.validate(file)
        assert sorted(p.name for p in file.parent.iterdir()) == before


# --- registration: the record ----------------------------------------------------------


def _registration(data: dict[str, Any], genres: Catalogue, file_digest: str = "f0") -> Registration:
    experiment, problems = parse(data)
    assert experiment is not None, problems
    genre = genres.get(experiment.genre)
    assert genre is not None and isinstance(experiment.probes, tuple)
    return freeze(
        experiment,
        resolve(experiment, genre),
        probe_set(experiment.probes),
        file_digest=file_digest,
        registered_at="2026-10-12T00:00:00+00:00",
        ix_version="test",
    )


class TestRegistrationRecord:
    def test_it_freezes_exactly_the_decision_parameters(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        compare_data.update(repeats=5, seed=9, spend={"ceiling": 3.5})
        record = _registration(compare_data, builtin_genres, "abc")
        document = record.document()
        assert document["schema"] == "ix.v1.registration"
        assert set(FROZEN) == {
            "file_digest",
            "probes_digest",
            "genre",
            "primary",
            "margin",
            "repeats",
            "seed",
            "spend_ceiling",
        }
        assert set(document) == {*FROZEN, "schema", "experiment", "registered_at", "ix_version"}
        assert document["file_digest"] == "abc"
        assert document["genre"] == "ix.v1.genre.compare"
        assert document["primary"] == {"metric": "ttfb", "statistic": "p99"}
        assert document["margin"] == {"value": 5.0, "unit": "percent"}
        assert (document["repeats"], document["seed"], document["spend_ceiling"]) == (5, 9, 3.5)

    def test_defaults_are_frozen_as_resolved(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        """Repeats and seed the author left to the genre are frozen at what they resolved
        to, so a later change of default is a deviation too."""
        record = _registration(compare_data, builtin_genres)
        compare = builtin_genres.get("compare")
        assert compare is not None
        assert record.repeats == compare.defaults.repeats.value
        assert record.seed > 0 and record.spend_ceiling is None

    def test_no_change_no_deviation(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        one = _registration(compare_data, builtin_genres)
        later = one.model_copy(update={"registered_at": "2027-01-01", "ix_version": "9"})
        assert deviations(one, later) == ()

    @pytest.mark.parametrize(
        ("change", "field"),
        [
            ({"genre": "regress", "rule": None}, "genre"),
            ({"primary": {"metric": "throughput"}}, "primary"),
            ({"primary": {"metric": "ttfb", "statistic": "p50"}}, "primary"),
            ({"margin": "10%"}, "margin"),
            ({"repeats": 9}, "repeats"),
            ({"seed": 5}, "seed"),
            ({"spend": {"ceiling": 1}}, "spend_ceiling"),
        ],
    )
    def test_each_frozen_parameter_deviates_alone(
        self,
        compare_data: dict[str, Any],
        builtin_genres: Catalogue,
        change: dict[str, Any],
        field: str,
    ) -> None:
        registered = _registration(compare_data, builtin_genres)
        current = _registration({**compare_data, **change}, builtin_genres)
        found = deviations(registered, current)
        assert [d.field for d in found] == [field]
        assert found[0].registered == registered.document()[field]
        assert found[0].current == current.document()[field]
        assert "after registration" in found[0].message

    def test_a_changed_probe_set_and_file_deviate(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        registered = _registration(compare_data, builtin_genres, "f0")
        compare_data["probes"].append({"id": "second", "kind": "http-load"})
        current = _registration(compare_data, builtin_genres, "f1")
        assert [d.field for d in deviations(registered, current)] == [
            "file_digest",
            "probes_digest",
        ]

    def test_deviations_are_listed_in_the_frozen_order(
        self, compare_data: dict[str, Any], builtin_genres: Catalogue
    ) -> None:
        registered = _registration(compare_data, builtin_genres)
        current = _registration(
            {**compare_data, "seed": 5, "repeats": 9, "margin": "1%"}, builtin_genres, "f1"
        )
        assert [d.field for d in deviations(registered, current)] == [
            "file_digest",
            "margin",
            "repeats",
            "seed",
        ]


# --- registration: beside the experiment -----------------------------------------------


class TestRegister:
    def test_the_record_is_written_beside_the_experiment(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        registered = ix.register(file)
        record = file.parent / "experiment.registration.json"
        assert registered.created and registered.location == str(record)
        stored = json.loads(record.read_text())
        assert stored == registered.registration.document()
        assert stored["schema"] == "ix.v1.registration"
        assert stored["experiment"] == "proxy" and stored["registered_at"]
        assert stored["ix_version"] == ix.__version__

    def test_the_record_is_named_after_the_file(
        self, compare_data: dict[str, Any], tmp_path: Path
    ) -> None:
        file = tmp_path / "proxy.yaml"
        file.write_text(yaml.safe_dump(compare_data))
        assert ix.register(file).location == str(tmp_path / "proxy.registration.json")

    def test_registering_again_unchanged_keeps_the_record(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        first = ix.register(file)
        record = file.parent / "experiment.registration.json"
        written = record.read_bytes()
        again = ix.register(file)
        assert not again.created
        assert again.registration == first.registration
        assert record.read_bytes() == written

    def test_a_changed_experiment_is_refused_and_the_record_kept(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        ix.register(file)
        record = file.parent / "experiment.registration.json"
        written = record.read_bytes()
        write_experiment({**compare_data, "margin": "20%"})
        with pytest.raises(ix.RegistrationConflictError) as raised:
            ix.register(file)
        assert raised.value.kind == "conflict" and raised.value.fix
        assert [d.field for d in raised.value.deviations] == ["file_digest", "margin"]
        assert record.read_bytes() == written

    def test_deviations_against_the_stored_record(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        ix.register(file)
        assert ix.deviations(file).deviations == ()
        write_experiment({**compare_data, "repeats": 9, "seed": 1})
        checked = ix.deviations(file)
        assert [d.field for d in checked.deviations] == ["file_digest", "repeats", "seed"]
        document = checked.document()
        assert document["schema"] == "ix.v1.deviations"
        assert [d["field"] for d in document["deviations"]] == ["file_digest", "repeats", "seed"]

    def test_a_comment_changes_the_file_and_nothing_else(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        """The file digest is over the bytes: an edit is visible even when it decides
        nothing, and it is the only field that says so."""
        file: Path = write_experiment(compare_data)
        ix.register(file)
        file.write_text(file.read_text() + "# a note\n")
        assert [d.field for d in ix.deviations(file).deviations] == ["file_digest"]

    def test_a_changed_probe_file_deviates(
        self, evaluate_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment({**evaluate_data, "probes": {"source": "directory"}})
        (file.parent / "probes").mkdir()
        probe = file.parent / "probes" / "ask.md"
        probe.write_text("---\nexpectation: must_trigger\n---\nReview this change.\n")
        ix.register(file)
        probe.write_text("---\nexpectation: must_trigger\n---\nReview this change, please.\n")
        assert [d.field for d in ix.deviations(file).deviations] == ["probes_digest"]

    def test_deviations_against_a_registration_handed_in(
        self, compare_data: dict[str, Any], write_experiment: Write, builtin_genres: Catalogue
    ) -> None:
        file: Path = write_experiment(compare_data)
        earlier = _registration({**compare_data, "margin": "1%"}, builtin_genres)
        fields = [d.field for d in ix.deviations(file, earlier).deviations]
        assert fields == ["file_digest", "margin"]
        assert not (file.parent / "experiment.registration.json").exists()

    def test_checking_an_unregistered_experiment_is_not_found(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        with pytest.raises(ix.NotFoundError) as raised:
            ix.deviations(file)
        assert raised.value.fix == f"ix register {file}"

    def test_an_invalid_experiment_is_not_registered(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        del compare_data["primary"]
        file: Path = write_experiment(compare_data)
        with pytest.raises(ix.InvalidExperimentError):
            ix.register(file)
        assert not (file.parent / "experiment.registration.json").exists()

    def test_a_damaged_record_is_a_config_error_not_a_fresh_start(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        record = file.parent / "experiment.registration.json"
        record.write_text('{"schema": "ix.v1.registration"}')
        with pytest.raises(ix.ConfigError, match="not an ix.v1.registration"):
            ix.register(file)
        record.write_text("not json")
        with pytest.raises(ix.ConfigError, match="not JSON"):
            ix.deviations(file)

    def test_the_clock_and_version_are_the_adapters_to_give(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        experiments = Experiments(
            YamlExperimentFiles(),
            GenreFiles(packages=["ix.genres"]),
            ProbeFiles(),
            FileRegistrations(),
            version="9.9.9",
            clock=lambda: "2026-10-12T09:00:00+00:00",
        )
        record = experiments.register(str(file)).registration
        assert (record.registered_at, record.ix_version) == ("2026-10-12T09:00:00+00:00", "9.9.9")

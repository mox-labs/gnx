"""``ix validate``, ``ix plan``, ``ix register``, ``ix genres`` and ``ix --skill`` as an
agent drives them.

An agent acts on the exit code, one JSON document on stdout, and one JSON error line on
stderr. These tests pin that contract, the checked-in examples, and the examples the skill
teaches. Nothing here starts a subject or reaches a network.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner, Result

import ix
from ix.adapters._in.cli import main
from ix.application.skill import EXAMPLES
from ix.domain.problem import PROBLEM_KINDS

Write = Any  # the write_experiment fixture

#: ix's checked-in example experiments.
EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"


def _run(*args: str) -> Result:
    return CliRunner().invoke(main, list(args))


def _json(result: Result) -> dict[str, Any]:
    document = json.loads(result.stdout)
    assert isinstance(document, dict) and "schema" in document, result.stdout
    return document


def _error(result: Result) -> dict[str, Any]:
    """The one JSON error line on stderr, with nothing on stdout."""
    assert result.stdout == "", result.stdout
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1, result.stderr
    error = json.loads(lines[0])["error"]
    assert set(error) >= {"kind", "message", "fix"}
    assert error["message"] and error["fix"]
    return dict(error)


# --- the checked-in examples -----------------------------------------------------------


class TestExamples:
    @pytest.mark.parametrize("example", ["proxy-streaming", "catalog-routing"])
    def test_each_example_validates_and_plans_clean(self, example: str) -> None:
        path = str(EXAMPLES_DIR / example)
        validated = _run("validate", path, "--json")
        assert validated.exit_code == 0, validated.stdout
        document = _json(validated)
        assert document["valid"] is True
        assert document["problems"] == [] and document["warnings"] == []
        planned = _run("plan", path, "--json")
        assert planned.exit_code == 0, planned.stderr
        assert _json(planned)["experiment"] == example

    def test_the_proxy_comparison(self) -> None:
        """The service example: compare, a control and a variant, a 3 x 3 sweep."""
        plan = ix.plan(EXAMPLES_DIR / "proxy-streaming")
        assert plan.genre == "ix.v1.genre.compare"
        assert plan.control == "buffered"
        assert [(s.name, s.role) for s in plan.subjects] == [
            ("buffered", "control"),
            ("streaming", "variant"),
        ]
        assert set(plan.factors) == {"size", "concurrency"}
        assert (len(plan.cells), plan.probes.count, plan.repeats) == (9, 1, 5)
        assert plan.trials == 2 * 9 * 5 + plan.calibration.trials == 135
        assert {e.declared_by for e in plan.effects} == {
            "environment",
            "subjects.buffered",
            "subjects.streaming",
        }
        design = ix.resolve(EXAMPLES_DIR / "proxy-streaming")
        assert design.rule == "non-inferiority" and design.margin is not None
        assert [guard.metric for guard in design.guards] == ["throughput", "memory_peak"]

    def test_the_agent_evaluation(self) -> None:
        """The agent example: evaluate, one subject, probes read from files."""
        plan = ix.plan(EXAMPLES_DIR / "catalog-routing")
        assert plan.genre == "ix.v1.genre.evaluate"
        assert plan.control is None and [s.role for s in plan.subjects] == ["subject"]
        assert plan.probes.ids == ("architecture-bottleneck", "decoy-clear-ask", "vague-ask")
        assert plan.trials == 9 and plan.spend.estimated == pytest.approx(0.45)
        experiment = ix.load(EXAMPLES_DIR / "catalog-routing")
        assert experiment.subjects["catalog"].kind == "agent"

    def test_the_experiment_the_readme_shows_validates(self, tmp_path: Path) -> None:
        readme = (EXAMPLES_DIR.parent / "README.md").read_text(encoding="utf-8")
        blocks = [b for b in re.findall(r"```yaml\n(.*?)```", readme, re.S) if "genre:" in b]
        assert len(blocks) == 1
        file = tmp_path / "experiment.yaml"
        file.write_text(blocks[0])
        result = ix.validate(file)
        assert result.valid, result.problems
        assert result.warnings == ()

    def test_examples_plan_the_same_twice(self) -> None:
        for example in ("proxy-streaming", "catalog-routing"):
            assert ix.plan(EXAMPLES_DIR / example).plan == ix.plan(EXAMPLES_DIR / example).plan


# --- validate --------------------------------------------------------------------------


class TestValidateCommand:
    def test_valid_is_exit_0_with_a_versioned_document(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        result = _run("validate", str(write_experiment(compare_data)), "--json")
        assert result.exit_code == 0 and result.stderr == ""
        document = _json(result)
        assert list(document) == [
            "schema",
            "experiment",
            "source",
            "genre",
            "valid",
            "problems",
            "warnings",
        ]
        assert document["schema"] == "ix.v2.validation"
        assert (document["experiment"], document["genre"]) == ("proxy", "ix.v1.genre.compare")

    def test_invalid_is_exit_3_with_every_problem_on_stdout(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        del compare_data["margin"]
        compare_data["primary"] = {"metric": "quality"}
        compare_data["repeats"] = 1
        result = _run("validate", str(write_experiment(compare_data)), "--json")
        assert result.exit_code == 3
        document = _json(result)
        assert document["valid"] is False
        assert {p["kind"] for p in document["problems"]} == {
            "rule_requires_margin",
            "judged_gate",
            "genre_min_repeats",
        }
        for problem in document["problems"]:
            assert list(problem) == ["path", "kind", "message", "fix", "source"]
            assert problem["kind"] in PROBLEM_KINDS
            assert problem["path"] and problem["message"] and problem["fix"]

    def test_a_directory_holding_experiment_yaml_is_accepted(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        assert _json(_run("validate", str(file.parent), "--json"))["source"] == str(file)

    def test_a_missing_file_is_exit_4(self, tmp_path: Path) -> None:
        result = _run("validate", str(tmp_path / "nowhere"), "--json")
        assert result.exit_code == 4
        assert _error(result)["kind"] == "not_found"

    def test_a_file_that_is_not_yaml_is_a_problem_not_a_crash(self, tmp_path: Path) -> None:
        file = tmp_path / "experiment.yaml"
        file.write_text("name: [unclosed\n")
        result = _run("validate", str(file), "--json")
        assert result.exit_code == 3
        assert [p["kind"] for p in _json(result)["problems"]] == ["yaml_syntax"]

    def test_the_earlier_harness_file_is_pointed_at_its_own_command(self, tmp_path: Path) -> None:
        file = tmp_path / "experiment.yaml"
        file.write_text("name: routing\nsensor: activation\ntrials: 5\n")
        result = _run("validate", str(file), "--json")
        assert result.exit_code == 3
        (problem,) = _json(result)["problems"]
        assert problem["kind"] == "earlier_format"
        assert "ix experiment validate" in problem["fix"]

    def test_warnings_do_not_fail_it(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        compare_data["subjects"]["streaming"]["ready"] = "http://127.0.0.1:9090/healthz"
        result = _run("validate", str(write_experiment(compare_data)), "--json")
        assert result.exit_code == 0
        document = _json(result)
        assert document["valid"] is True
        assert [w["kind"] for w in document["warnings"]] == ["subjects_differ_in"]

    def test_for_a_person_it_lists_path_kind_message_and_fix(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        del compare_data["margin"]
        result = _run("validate", str(write_experiment(compare_data)))
        assert result.exit_code == 3
        assert "invalid" in result.stdout and "rule_requires_margin" in result.stdout
        assert "margin" in result.stdout and "Fix: margin: 5%" in result.stdout

    def test_a_bad_flag_is_a_usage_error(self) -> None:
        assert _run("validate").exit_code == 2
        assert _run("validate", "x", "--format", "json").exit_code == 2


# --- plan ------------------------------------------------------------------------------


class TestPlanCommand:
    def test_the_document_shape(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        result = _run("plan", str(write_experiment(compare_data)), "--json")
        assert result.exit_code == 0 and result.stderr == ""
        document = _json(result)
        assert list(document) == [
            "schema",
            "plan",
            "experiment",
            "experiment_digest",
            "genre",
            "subjects",
            "control",
            "factors",
            "cells",
            "probes",
            "repeats",
            "calibration",
            "trials",
            "order",
            "effects",
            "spend",
            "verdict",
            "settings",
            "notes",
        ]
        assert document["schema"] == "ix.v2.plan"
        assert re.fullmatch(r"[0-9a-f]{64}", document["plan"])
        assert document["control"] == "buffered"
        assert document["subjects"][0] == {
            "name": "buffered",
            "role": "control",
            "kind": "process",
        }
        assert set(document["order"]) == {"rule", "seed", "description"}
        assert set(document["spend"]) == {"estimated", "ceiling", "currency", "basis"}
        for setting in document["settings"]:
            assert set(setting) == {"field", "value", "source", "note"}

    def test_the_same_file_prints_the_same_document(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        path = str(write_experiment(compare_data))
        assert _run("plan", path, "--json").stdout == _run("plan", path, "--json").stdout

    def test_an_invalid_experiment_is_exit_3_with_the_problems(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        del compare_data["subjects"]["streaming"]
        path = str(write_experiment(compare_data))
        result = _run("plan", path, "--json")
        assert result.exit_code == 3
        error = _error(result)
        assert error["kind"] == "config" and error["fix"] == f"ix validate {path}"
        assert [p["kind"] for p in error["problems"]] == ["genre_subjects"]

    def test_for_a_person_it_names_the_control_and_the_digest(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        result = _run("plan", str(write_experiment(compare_data)))
        assert result.exit_code == 0
        plan = ix.plan(write_experiment(compare_data))
        assert "Control: buffered" in result.stdout
        assert plan.plan[:12] in result.stdout
        assert f"Trials: {plan.trials}" in result.stdout
        assert "interleaved" in result.stdout and "Effects: none declared" in result.stdout

    def test_planning_runs_nothing(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        """The subject's command names a file that would be created if anything ran."""
        file: Path = write_experiment(compare_data)
        marker = file.parent / "ran"
        compare_data["subjects"]["buffered"]["command"] = ["touch", str(marker)]
        write_experiment(compare_data)
        assert _run("plan", str(file), "--json").exit_code == 0
        assert not marker.exists()
        assert sorted(p.name for p in file.parent.iterdir()) == ["experiment.yaml"]


# --- register --------------------------------------------------------------------------


class TestRegisterCommand:
    def test_register_writes_the_record_and_says_where(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        result = _run("register", str(file), "--json")
        assert result.exit_code == 0
        document = _json(result)
        assert list(document) == ["schema", "created", "location", "registration"]
        assert document["schema"] == "ix.v1.registered" and document["created"] is True
        record = file.parent / "experiment.registration.json"
        assert document["location"] == str(record)
        assert json.loads(record.read_text()) == document["registration"]
        assert document["registration"]["schema"] == "ix.v1.registration"

    def test_registering_twice_is_exit_0_and_not_created(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        path = str(write_experiment(compare_data))
        _run("register", path, "--json")
        again = _run("register", path, "--json")
        assert again.exit_code == 0 and _json(again)["created"] is False

    def test_a_changed_experiment_is_exit_3_with_the_deviations(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        path = str(write_experiment(compare_data))
        _run("register", path, "--json")
        write_experiment({**compare_data, "margin": "20%"})
        result = _run("register", path, "--json")
        assert result.exit_code == 3
        error = _error(result)
        assert error["kind"] == "conflict"
        assert [d["field"] for d in error["deviations"]] == ["file_digest", "margin"]
        assert "--check" in error["fix"]

    def test_check_lists_deviations_and_writes_nothing(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        _run("register", str(file), "--json")
        record = file.parent / "experiment.registration.json"
        written = record.read_bytes()
        clean = _run("register", str(file), "--check", "--json")
        assert clean.exit_code == 0 and _json(clean)["deviations"] == []
        write_experiment({**compare_data, "repeats": 9})
        result = _run("register", str(file), "--check", "--json")
        assert result.exit_code == 0
        document = _json(result)
        assert list(document) == [
            "schema",
            "experiment",
            "location",
            "registered_at",
            "deviations",
        ]
        assert document["schema"] == "ix.v1.deviations"
        assert [d["field"] for d in document["deviations"]] == ["file_digest", "repeats"]
        for deviation in document["deviations"]:
            assert set(deviation) == {"field", "registered", "current", "message"}
        assert record.read_bytes() == written

    def test_check_before_registering_is_exit_4(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        path = str(write_experiment(compare_data))
        result = _run("register", path, "--check", "--json")
        assert result.exit_code == 4
        error = _error(result)
        assert error["kind"] == "not_found" and error["fix"] == f"ix register {path}"

    def test_an_invalid_experiment_is_exit_3_and_writes_nothing(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        del compare_data["margin"]
        file: Path = write_experiment(compare_data)
        result = _run("register", str(file), "--json")
        assert result.exit_code == 3
        assert [p["kind"] for p in _error(result)["problems"]] == ["rule_requires_margin"]
        assert sorted(p.name for p in file.parent.iterdir()) == ["experiment.yaml"]

    def test_for_a_person(self, compare_data: dict[str, Any], write_experiment: Write) -> None:
        path = str(write_experiment(compare_data))
        assert "registered proxy" in _run("register", path).stdout.replace("\n", "")
        assert "already registered" in _run("register", path).stdout
        assert "no deviations" in _run("register", path, "--check").stdout
        write_experiment({**compare_data, "margin": "20%"})
        refused = _run("register", path)
        assert refused.exit_code == 3 and refused.stderr.startswith("Error:")
        assert "margin" in refused.stderr and "Fix:" in refused.stderr


# --- genres and the skill --------------------------------------------------------------


class TestGenresCommand:
    def test_the_catalogue_document(self) -> None:
        result = _run("genres", "--json")
        assert result.exit_code == 0
        document = _json(result)
        assert list(document) == ["schema", "genres", "rejected"]
        assert document["schema"] == "ix.v1.genres"
        names = [genre["name"] for genre in document["genres"]]
        for name in ("characterise", "compare", "evaluate", "regress"):
            assert f"ix.v1.genre.{name}" in names
        for genre in document["genres"]:
            assert genre["schema"] == "ix.v1.genre"

    def test_a_refused_lab_genre_is_listed_with_its_problems(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        (file.parent / "genres").mkdir()
        loose = {"name": "lab.v1.genre.loose", "extends": "compare", "verdict": "none"}
        (file.parent / "genres" / "loose.yaml").write_text(yaml.safe_dump(loose))
        document = _json(_run("genres", "--beside", str(file), "--json"))
        (rejected,) = document["rejected"]
        assert rejected["name"] == "lab.v1.genre.loose"
        assert [p["kind"] for p in rejected["problems"]] == ["genre_narrowing"]
        assert "lab.v1.genre.loose" not in [g["name"] for g in document["genres"]]


class TestSkill:
    def test_skill_prints_the_document_and_exits_0(self) -> None:
        result = _run("--skill")
        assert result.exit_code == 0
        assert result.stdout.rstrip() == ix.skill().rstrip()

    def test_it_teaches_every_genre(self) -> None:
        skill = ix.skill()
        catalogue = ix.catalogue()
        for genre in catalogue.genres.values():
            assert genre.skill.strip() in skill, genre.name
            assert genre.question in skill
        for name in ("evaluate", "compare", "characterise", "regress"):
            assert f"### {name}" in skill
            assert f"genre: {name}" in skill

    def test_it_names_the_commands_the_documents_and_the_kinds(self) -> None:
        skill = ix.skill()
        for command in ("ix validate", "ix plan", "ix register", "--check", "ix genres", "--json"):
            assert command in skill
        for schema in ("ix.v2.validation", "ix.v2.plan", "ix.v1.registered", "ix.v1.deviations"):
            assert schema in skill
        for kind in PROBLEM_KINDS:
            assert f"`{kind}`" in skill

    def test_it_says_which_commands_drive_the_earlier_harness(self) -> None:
        skill = " ".join(ix.skill().split())
        assert "earlier agent-eval harness" in skill
        for command in ("ix run", "ix results", "ix compare", "ix experiment"):
            assert command in skill

    @pytest.mark.parametrize("genre", sorted(EXAMPLES))
    def test_every_example_it_teaches_validates_and_plans(self, genre: str, tmp_path: Path) -> None:
        """What the skill shows is what ix accepts: each example is written out and run
        through validate and plan."""
        assert f"```yaml\n{EXAMPLES[genre].rstrip()}\n```" in ix.skill()
        file = tmp_path / "experiment.yaml"
        file.write_text(EXAMPLES[genre])
        result = ix.validate(file)
        assert result.valid, result.problems
        assert result.warnings == ()
        assert result.genre == f"ix.v1.genre.{genre}"
        assert ix.plan(file).trials > 0

    def test_there_is_an_example_for_each_built_in_genre(self) -> None:
        built_in = {name.removeprefix("ix.v1.genre.") for name in ix.catalogue().names()}
        assert set(EXAMPLES) == {name for name in built_in if "." not in name}


# --- the two harnesses side by side ----------------------------------------------------


class TestCoexistence:
    def test_the_root_help_lists_both_sets_of_commands(self) -> None:
        result = _run("--help")
        assert result.exit_code == 0
        for command in (
            "validate",
            "plan",
            "register",
            "genres",
            "run",
            "results",
            "compare",
            "experiment",
            "lab",
        ):
            assert command in result.stdout
        assert "--skill" in result.stdout

    def test_the_earlier_validate_still_reads_its_own_format(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        experiment = tmp_path / "lab" / "e"
        (experiment / "tasks").mkdir(parents=True)
        (experiment / "experiment.yaml").write_text(
            "name: e\nsensor: {type: activation, expected_skill: x}\ntrials: 2\n"
            "subjects:\n  - name: sim\n    config: {runtime: {type: simulated}}\n"
        )
        (experiment / "tasks" / "p.md").write_text(
            "---\nid: p\nexpectation: must_trigger\n---\nprompt\n"
        )
        earlier = _run("experiment", "validate", "e", "--lab", "lab", "--format", "json")
        assert earlier.exit_code == 0, earlier.stderr
        assert json.loads(earlier.stdout)["schema"] == "ix.v1.validation"
        model = _run("validate", str(experiment), "--json")
        assert model.exit_code == 3
        assert [p["kind"] for p in _json(model)["problems"]] == ["earlier_format"]

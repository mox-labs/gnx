"""The ``ix`` command line as an agent drives it.

An agent acts on the exit code, one JSON document on stdout, and one error envelope on
stderr. These tests pin that contract, the plan a person approves, the checked-in examples
and the lab, and the skill document. Nothing here starts a subject or reaches a network.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner, Result

import ix
from ix.adapters._in.cli import main
from ix.application.skill import COMMANDS, EXAMPLES
from ix.domain.errors import EXIT_BY_KIND, EXIT_CODES
from ix.domain.problem import PROBLEM_KINDS

Write = Any  # the write_experiment fixture

PACKAGE = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = PACKAGE / "examples"
SRC = PACKAGE / "src" / "ix"


def _run(*args: str) -> Result:
    return CliRunner(env={"COLUMNS": "80"}).invoke(main, list(args))


def _json(result: Result) -> dict[str, Any]:
    document = json.loads(result.stdout)
    assert isinstance(document, dict) and "schema" in document, result.stdout
    return document


def _error(result: Result) -> dict[str, Any]:
    """The one error envelope on stderr, as one JSON line, with nothing on stdout."""
    assert result.stdout == "", result.stdout
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1, result.stderr
    envelope = json.loads(lines[0])
    assert list(envelope) == ["schema", "error"] and envelope["schema"] == "ix.v1.error"
    error = envelope["error"]
    assert list(error) == ["kind", "message", "fix", "problems", "deviations"]
    assert error["message"] and error["fix"]
    return dict(error)


# --- one error envelope ----------------------------------------------------------------


class TestErrorEnvelope:
    def test_not_found(self, tmp_path: Path) -> None:
        for command in ("validate", "plan", "register", "deviations"):
            result = _run(command, str(tmp_path / "nowhere"), "--json")
            assert result.exit_code == 4, command
            error = _error(result)
            assert error["kind"] == "not_found"
            assert error["problems"] == [] and error["deviations"] == []

    def test_an_invalid_experiment_carries_its_problems(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        del compare_data["subjects"]["streaming"]
        path = str(write_experiment(compare_data))
        for command in ("plan", "register", "deviations"):
            result = _run(command, path, "--json")
            assert result.exit_code == 3, command
            error = _error(result)
            assert error["kind"] == "config" and error["fix"] == f"ix validate {path}"
            assert [p["kind"] for p in error["problems"]] == ["genre_roles"]
            assert error["deviations"] == []

    def test_a_registration_conflict_carries_its_deviations(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        path = str(write_experiment(compare_data))
        _run("register", path)
        write_experiment({**compare_data, "margin": "20%"})
        result = _run("register", path, "--json")
        assert result.exit_code == 3
        error = _error(result)
        assert error["kind"] == "conflict" and error["problems"] == []
        assert error["deviations"][0]["field"] == "design.margin.value"
        assert "--amend" in error["fix"] and f"ix deviations {path}" in error["fix"]

    def test_a_usage_error_is_the_same_envelope(self) -> None:
        cases = (
            ("validate", "--json"),
            ("frobnicate", "--json"),
            ("register", "x", "--amend", "--json"),
            ("plan", "x", "--format", "json", "--json"),
        )
        for args in cases:
            result = _run(*args)
            assert result.exit_code == 2, args
            assert _error(result)["kind"] == "usage"

    def test_for_a_person_the_same_content_is_readable(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        del compare_data["margin"]
        result = _run("plan", str(write_experiment(compare_data)))
        assert result.exit_code == 3 and result.stdout == ""
        assert result.stderr.startswith("Error:")
        assert "margin_required" in result.stderr and "Fix: margin: 5%" in result.stderr
        assert _run("validate").exit_code == 2 and "Error:" in _run("validate").stderr

    def test_error_kinds_map_through_the_one_exit_code_table(self) -> None:
        assert ix.EXIT_CODES is EXIT_CODES
        assert set(EXIT_BY_KIND.values()) == set(EXIT_CODES)
        assert ix.ConfigError("x").exit_code == 3 == ix.RegistrationConflictError("x", ()).exit_code
        assert ix.NotFoundError("x").exit_code == 4 and ix.IxError("x").exit_code == 1
        help_text = _run("validate", "--help").stdout
        for code, meaning in EXIT_CODES.items():
            assert f"{code}  {meaning}" in " ".join(help_text.split()) or str(code) in help_text

    def test_the_exit_code_table_is_written_once(self) -> None:
        """No module restates the table: the codes' meanings appear in one file."""
        marker = "not found: no experiment file"
        holders = [
            file.relative_to(SRC).as_posix()
            for file in sorted(SRC.rglob("*.py"))
            if marker in file.read_text(encoding="utf-8")
        ]
        assert holders == ["domain/errors.py"]


class TestFixesNameRealCommands:
    """A ``fix`` is a contract with whoever reads it: every command it names exists."""

    def _mentions(self) -> set[str]:
        found: set[str] = set()
        for file in sorted(SRC.rglob("*.py")):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            # Docstrings are not messages; the root help says there is no `ix run`.
            docstrings = {
                id(node.value)
                for node in ast.walk(tree)
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            }
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and id(node) not in docstrings
                ):
                    found.update(re.findall(r"`ix ([a-z][a-z-]*)", node.value))
                    found.update(re.findall(r"^ix ([a-z][a-z-]*) (?:[{<\[]|$)", node.value))
        return found

    def test_every_ix_command_in_a_message_is_a_real_command(self) -> None:
        mentioned = self._mentions()
        assert {"validate", "register", "deviations", "genres", "kinds"} <= mentioned
        assert mentioned <= set(main.commands), mentioned - set(main.commands)

    def test_the_commands_are_these_and_there_is_no_run(self) -> None:
        assert sorted(main.commands) == [
            "deviations", "genres", "kinds", "plan", "register", "validate",
        ]  # fmt: skip
        assert {command.split()[1] for command, _ in COMMANDS} == set(main.commands)
        result = _run("run", "x", "--json")
        assert result.exit_code == 2 and "No such command" in _error(result)["message"]

    def test_every_flag_a_fix_names_exists(self) -> None:
        register = main.commands["register"]
        flags = {option for param in register.params for option in param.opts}
        assert {"--amend", "--reason", "--json"} <= flags
        for command in main.commands.values():
            assert "--json" in {o for p in command.params for o in p.opts}, command.name


# --- validate --------------------------------------------------------------------------


class TestValidateCommand:
    def test_valid_is_exit_0_with_a_versioned_document(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        result = _run("validate", str(write_experiment(compare_data)), "--json")
        assert result.exit_code == 0 and result.stderr == ""
        document = _json(result)
        assert list(document) == [
            "schema", "experiment", "source", "genre", "genre_url", "valid", "complete",
            "problems", "warnings",
        ]  # fmt: skip
        assert document["schema"] == "ix.v1.validation"
        assert (document["genre"], document["genre_url"]) == ("compare", "ix.v1.genre.compare")
        assert document["valid"] is True and document["complete"] is True

    def test_invalid_is_exit_3_with_every_problem_on_stdout(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        del compare_data["margin"]
        compare_data["primary"] = {"metric": "tfb"}
        compare_data["repeats"] = 1
        result = _run("validate", str(write_experiment(compare_data)), "--json")
        assert result.exit_code == 3 and result.stderr == ""
        document = _json(result)
        assert document["valid"] is False
        assert {p["kind"] for p in document["problems"]} == {
            "margin_required", "metric_unprovided", "repeats_below_min",
        }  # fmt: skip
        for problem in document["problems"]:
            assert list(problem) == ["path", "kind", "message", "fix", "allowed", "got", "source"]
            assert problem["kind"] in PROBLEM_KINDS
            assert problem["path"] and problem["message"] and problem["fix"]
        unprovided = next(p for p in document["problems"] if p["kind"] == "metric_unprovided")
        assert unprovided["got"] == "tfb" and "ttfb" in unprovided["allowed"]

    def test_a_file_that_did_not_load_says_the_check_is_incomplete(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        path = str(write_experiment({**compare_data, "subjects": []}))
        document = _json(_run("validate", path, "--json"))
        assert document["complete"] is False
        assert [p["kind"] for p in document["problems"]] == ["wrong_shape"]
        assert "Incomplete" in _run("validate", path).stdout

    def test_a_directory_holding_experiment_yaml_is_accepted(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        assert _json(_run("validate", str(file.parent), "--json"))["source"] == str(file)

    def test_a_file_that_is_not_yaml_is_a_problem_not_a_crash(self, tmp_path: Path) -> None:
        file = tmp_path / "experiment.yaml"
        file.write_text("name: [unclosed\n")
        result = _run("validate", str(file), "--json")
        assert result.exit_code == 3
        document = _json(result)
        assert [p["kind"] for p in document["problems"]] == ["yaml_syntax"]
        assert document["complete"] is False

    def test_warnings_do_not_fail_it(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        compare_data["subjects"]["streaming"]["ready"] = "http://127.0.0.1:9090/healthz"
        result = _run("validate", str(write_experiment(compare_data)), "--json")
        assert result.exit_code == 0
        assert [w["kind"] for w in _json(result)["warnings"]] == ["subjects_differ_in"]

    def test_for_a_person_it_lists_path_kind_message_and_fix_and_what_is_next(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        path = str(write_experiment(compare_data))
        valid = _run("validate", path)
        assert valid.exit_code == 0 and "valid proxy (compare)" in valid.stdout
        assert "Next: ix plan" in valid.stdout
        del compare_data["margin"]
        invalid = _run("validate", str(write_experiment(compare_data)))
        assert invalid.exit_code == 3
        assert "invalid" in invalid.stdout and "margin_required" in invalid.stdout
        assert "Fix: margin: 5%" in invalid.stdout


# --- plan ------------------------------------------------------------------------------


class TestPlanCommand:
    def test_the_document_shape(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        result = _run("plan", str(file), "--json")
        assert result.exit_code == 0 and result.stderr == ""
        document = _json(result)
        assert list(document) == [
            "schema", "digest", "experiment", "experiment_digest", "genre", "genre_url",
            "question", "decides", "comparison", "guardrails", "verdict_form", "subjects",
            "control", "factors", "probes", "calibration", "trials", "duration", "order",
            "sensors", "effects", "spend", "registration", "provenance", "notes",
        ]  # fmt: skip
        assert document["schema"] == "ix.v1.plan"
        assert re.fullmatch(r"[0-9a-f]{64}", document["digest"])
        assert document["subjects"][0] == {
            "subject": "buffered", "role": "control", "kind": "process",
        }  # fmt: skip
        assert set(document["trials"]) == {
            "subjects", "cells", "probes", "repeats", "measured", "calibration", "total",
        }  # fmt: skip
        assert document["registration"] == {
            "status": "none",
            "deviations": 0,
            "location": str(file.parent / "experiment.registration.json"),
        }
        for value in document["provenance"]:
            assert set(value) == {"path", "value", "source", "note"}
            assert value["source"] in ("experiment", "genre", "ix")
        for effect in document["effects"]:
            assert set(effect) == {"effect", "detail", "source", "declared_by"}

    def test_the_same_file_prints_the_same_document(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        path = str(write_experiment(compare_data))
        assert _run("plan", path, "--json").stdout == _run("plan", path, "--json").stdout

    def test_the_decision_comes_second_after_the_title(self) -> None:
        """What a person approves is on the first screen, in this order: the title, the
        question, what the run decides, the guardrails, the form of the verdict."""
        result = _run("plan", str(EXAMPLES_DIR / "proxy-streaming"))
        assert result.exit_code == 0, result.stderr
        text = result.stdout
        plan = ix.plan(EXAMPLES_DIR / "proxy-streaming")
        lines = text.splitlines()
        assert lines[0].split() == [
            "proxy-streaming", "compare", "(ix.v1.genre.compare)", "plan", plan.digest[:12],
        ]  # fmt: skip
        assert lines[1].startswith("Question:")
        order = [text.index(label) for label in (
            "Question:", "Decides:", "Comparison:", "Guardrails:", "Verdict:", "Subjects",
            "Trials:", "Duration:", "Order:", "Effects:", "Spend:", "Registration:", "Design",
        )]  # fmt: skip
        assert order == sorted(order)
        flat = " ".join(text.split())
        assert "streaming is no worse than buffered by more than 5% on ttfb p99" in flat
        assert "Comparison: non-inferiority" in flat
        assert "memory_peak no worse than buffered by more than 0%" in flat
        assert "Verdict: holds / fails / inconclusive" in flat
        assert "Control: buffered" in flat
        assert "2 subjects x 9 cells x 1 probe x 5 repeats = 90, + 45 A/A = 135" in flat
        assert "Duration: about 2h 49m" in flat
        assert "Registration: none" in flat
        assert "Next: ix register" in flat

    def test_the_provisional_note_is_a_footer_not_a_column(self) -> None:
        text = _run("plan", str(EXAMPLES_DIR / "proxy-streaming")).stdout
        assert text.count("provisional") == 1
        footer = text[text.index("└") :]
        assert "The genre's design is provisional: pending the experimentation canon." in footer

    def test_nothing_is_printed_as_python_prints_it(
        self, evaluate_data: dict[str, Any], write_experiment: Write
    ) -> None:
        for path in (EXAMPLES_DIR / "proxy-streaming", EXAMPLES_DIR / "catalog-routing",
                     write_experiment(evaluate_data)):  # fmt: skip
            text = _run("plan", str(path)).stdout
            for repr_ in ("True", "False", "None", "('", "['", "{'"):
                assert repr_ not in text, (path, repr_)

    def test_one_role_needs_no_role_column(self) -> None:
        text = _run("plan", str(EXAMPLES_DIR / "catalog-routing")).stdout
        header = next(line for line in text.splitlines() if "subject" in line and "kind" in line)
        assert "role" not in header
        assert "catalog has activated_correctly >= 0.9" in " ".join(text.split())
        assert "Control:" not in text and "Comparison:" not in text

    def test_registration_status_follows_the_record(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        path = str(write_experiment(compare_data))
        _run("register", path)
        assert "Registration: matches" in " ".join(_run("plan", path).stdout.split())
        write_experiment({**compare_data, "margin": "9%"})
        text = " ".join(_run("plan", path).stdout.split())
        assert re.search(r"Registration: \d+ deviations", text)
        assert f"Next: ix deviations {path}" in text

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


class TestEightyColumns:
    """``plan``, ``genres`` and ``kinds`` are read in a terminal: at 80 columns nothing
    overflows and nothing is cut off."""

    @pytest.mark.parametrize(
        "args",
        [
            ("plan", "examples/proxy-streaming"),
            ("plan", "examples/catalog-routing"),
            ("genres",),
            ("kinds",),
        ],
    )
    def test_fits_and_truncates_nothing(
        self, args: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(PACKAGE)
        result = _run(*args)
        assert result.exit_code == 0, result.stderr
        for line in result.stdout.splitlines():
            assert len(line) <= 80, line
        assert "…" not in result.stdout

    def test_kinds_shows_every_option_whole(self) -> None:
        flat = "".join(_run("kinds").stdout.split())
        for kind in ix.kinds().kinds.values():
            for name, option in kind.options.items():
                assert (name + "*" if option.required else name) in flat.replace("│", ""), name

    def test_genres_shows_what_the_author_must_set(self) -> None:
        flat = " ".join(_run("genres").stdout.replace("│", " ").split())
        assert "regress" in flat and "characterize" in flat
        for part in ("primary, comparison,", "margin (for", "non-inferiority,", "equivalence)"):
            assert part in flat


# --- register and deviations -----------------------------------------------------------


class TestRegisterAndDeviationsCommands:
    def test_register_writes_the_record_and_says_where(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        result = _run("register", str(file), "--json")
        assert result.exit_code == 0
        document = _json(result)
        assert list(document) == ["schema", "outcome", "location", "registration"]
        assert (document["schema"], document["outcome"]) == ("ix.v1.registered", "created")
        record = file.parent / "experiment.registration.json"
        assert document["location"] == str(record)
        assert json.loads(record.read_text()) == document["registration"]
        assert _json(_run("register", str(file), "--json"))["outcome"] == "unchanged"

    def test_deviations_is_its_own_command_and_exits_9_when_it_finds_any(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        _run("register", str(file))
        record = file.parent / "experiment.registration.json"
        written = record.read_bytes()

        clean = _run("deviations", str(file), "--json")
        assert clean.exit_code == 0 and _json(clean)["deviations"] == []

        write_experiment({**compare_data, "repeats": 9})
        result = _run("deviations", str(file), "--json")
        assert result.exit_code == 9 and result.stderr == ""
        document = _json(result)
        assert list(document) == ["schema", "experiment", "location", "registered_at", "deviations"]
        assert document["schema"] == "ix.v1.deviations"
        assert [d["field"] for d in document["deviations"]] == [
            "design.repeats", "experiment_digest", "plan_digest",
        ]  # fmt: skip
        assert document["deviations"][0] == {
            "field": "design.repeats", "registered": 5, "current": 9,
            "message": "repeats changed after registration",
        }  # fmt: skip
        assert record.read_bytes() == written
        assert "--check" not in _run("register", "--help").stdout

    def test_deviations_before_registering_is_exit_4(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        path = str(write_experiment(compare_data))
        result = _run("deviations", path, "--json")
        assert result.exit_code == 4
        assert _error(result)["fix"] == f"ix register {path}"

    def test_amend_with_a_reason_replaces_the_record_and_keeps_the_old_one(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        _run("register", str(file))
        write_experiment({**compare_data, "margin": "10%"})
        result = _run("register", str(file), "--amend", "--reason", "noise floor was 6%", "--json")
        assert result.exit_code == 0
        document = _json(result)
        assert document["outcome"] == "amended"
        (amendment,) = document["registration"]["amendments"]
        assert amendment["reason"] == "noise floor was 6%"
        assert amendment["superseded"]["design"]["margin"]["value"] == 5.0
        assert _run("deviations", str(file)).exit_code == 0

    def test_amend_and_reason_go_together(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        path = str(write_experiment(compare_data))
        for args in (("--amend",), ("--reason", "why")):
            result = _run("register", path, *args, "--json")
            assert result.exit_code == 2
            assert "--amend and --reason go together" in _error(result)["message"]

    def test_for_a_person(self, compare_data: dict[str, Any], write_experiment: Write) -> None:
        path = str(write_experiment(compare_data))
        assert "registered proxy" in " ".join(_run("register", path).stdout.split())
        assert "already registered, unchanged" in _run("register", path).stdout
        assert "no deviations" in _run("deviations", path).stdout
        write_experiment({**compare_data, "margin": "20%"})
        listed = _run("deviations", path)
        assert listed.exit_code == 9
        flat = " ".join(listed.stdout.split())
        assert "design.margin.value: registered 5.0, now 20.0" in flat and "--amend" in flat
        refused = _run("register", path)
        assert refused.exit_code == 3 and refused.stderr.startswith("Error:")
        assert "design.margin.value" in refused.stderr and "Fix:" in refused.stderr


# --- genres, kinds and the skill -------------------------------------------------------


class TestGenresAndKindsCommands:
    def test_the_genres_document(self) -> None:
        result = _run("genres", "--json")
        assert result.exit_code == 0
        document = _json(result)
        assert list(document) == ["schema", "genres", "rejected"]
        assert document["schema"] == "ix.v1.genres"
        for name in ("characterize", "compare", "evaluate", "regress"):
            assert document["genres"][f"ix.v1.genre.{name}"]["schema"] == "ix.v1.genre"
        compare = document["genres"]["ix.v1.genre.compare"]
        assert {"roles", "must_set", "comparisons", "design", "verdict_form", "guidance"} <= set(
            compare
        )
        assert compare["design"]["repeats"] == {"default": 5, "min": 3}

    def test_the_kinds_document(self) -> None:
        result = _run("kinds", "--json")
        assert result.exit_code == 0
        document = _json(result)
        assert list(document) == ["schema", "kinds", "rejected"]
        assert document["schema"] == "ix.v1.kinds"
        load = document["kinds"]["ix.v1.probe.http-load"]
        assert load["schema"] == "ix.v1.kind"
        assert load["options"]["duration"] == {
            "type": "duration", "required": True, "allowed": [],
            "description": "how long the measured load lasts",
        }  # fmt: skip
        assert (load["needs"], load["offers"]) == (["url"], ["load"])
        judge = document["kinds"]["ix.v1.sensor.judge"]
        assert [m["judged"] for m in judge["metrics"]] == [True, True]

    def test_a_labs_refused_files_are_listed_with_their_problems(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        (file.parent / "genres").mkdir()
        (file.parent / "kinds").mkdir()
        loose = {"name": "lab.v1.genre.loose", "extends": "compare", "verdict_form": "none"}
        (file.parent / "genres" / "loose.yaml").write_text(yaml.safe_dump(loose))
        (file.parent / "kinds" / "bad.yaml").write_text("name: lab.v1.sensor.bad\nsummary: s\n")
        lab = str(file.parent)
        genres = _json(_run("genres", "--lab", lab, "--json"))
        assert [p["kind"] for p in genres["rejected"]["lab.v1.genre.loose"]] == ["genre_narrowing"]
        kinds = _json(_run("kinds", "--lab", lab, "--json"))
        assert [p["kind"] for p in kinds["rejected"]["lab.v1.sensor.bad"]] == ["kind_invalid"]
        assert "refused lab.v1.genre.loose" in " ".join(_run("genres", "--lab", lab).stdout.split())


class TestSkill:
    def test_skill_prints_the_document_and_exits_0(self) -> None:
        result = _run("--skill")
        assert result.exit_code == 0
        assert result.stdout.rstrip() == ix.skill().rstrip()

    def test_it_says_plainly_that_ix_does_not_run(self) -> None:
        assert "ix has no `run` command yet" in ix.skill()
        help_text = " ".join(_run("--help").stdout.split())
        assert "There is no `ix run` yet" in help_text
        assert "does not execute one" in help_text

    def test_it_teaches_every_genre_from_its_record(self) -> None:
        skill = ix.skill()
        for genre in ix.genres().genres.values():
            assert f"### {genre.label}" in skill
            assert genre.guidance.strip() in skill and genre.question in skill
            assert f"- You must set: {', '.join(genre.author_must_set)}." in skill
            for name, form in genre.comparisons.items():
                assert f"  - `{name}`: {form}" in skill

    def test_what_it_says_must_be_set_is_what_validate_enforces(self, tmp_path: Path) -> None:
        """Reviewed defect: the skill said regress needs ``rule`` while its example left it
        out, and never said compare needs a margin. For every genre: with all the fields it
        lists, the example is valid; with any one of them removed, it is not."""
        for label, text in EXAMPLES.items():
            genre = ix.genres().get(label)
            assert genre is not None
            data = yaml.safe_load(text)
            comparison = data.get("comparison") or genre.default_comparison
            listed = [field.split(" ")[0] for field in genre.author_must_set]
            for field in listed:
                if field == "margin" and not genre.needs_margin(comparison):
                    continue
                assert field in data, (label, field)
                without = {key: value for key, value in data.items() if key != field}
                file = tmp_path / f"{label}-{field}.yaml"
                file.write_text(yaml.safe_dump(without, sort_keys=False))
                kinds = {p.kind for p in ix.validate(file).problems}
                assert kinds & {"must_set", "margin_required"}, (label, field, kinds)
        assert "You must set: primary, margin." in ix.skill()  # regress: no 'comparison'

    def test_it_teaches_every_kind_from_its_declaration(self) -> None:
        skill = ix.skill()
        for kind in ix.kinds().kinds.values():
            assert f"**`{kind.label}`**: {kind.summary}" in skill
            for name, option in kind.options.items():
                assert f"`{name}`{'*' if option.required else ''}" in skill, (kind.label, name)
            for metric in kind.metrics:
                assert f"metric `{metric.name}`" in skill
        assert skill.count("JUDGED by a model") == 2

    def test_what_it_says_about_unknown_keys_is_true(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        """Reviewed defect: the skill said "an unknown key is refused" while a typo beside
        ``kind`` passed. The sentence stays; the behaviour was made to match it."""
        assert "An unknown key is refused, at the top of the file and beside" in ix.skill()
        compare_data["tyop"] = 1
        compare_data["subjects"]["buffered"]["comand"] = ["x"]
        compare_data["probes"][0]["durration"] = "1s"
        compare_data["environment"] = {"kind": "none", "servces": {}}
        top = ix.validate(write_experiment(compare_data, "top"))
        assert [(p.path, p.kind) for p in top.problems] == [("tyop", "unknown_key")]
        del compare_data["tyop"]
        beside = ix.validate(write_experiment(compare_data, "beside"))
        assert {(p.path, p.kind) for p in beside.problems} == {
            ("subjects.buffered.comand", "option_unknown"),
            ("probes.0.durration", "option_unknown"),
            ("environment.servces", "option_unknown"),
        }

    def test_it_names_the_commands_the_documents_the_codes_and_the_kinds(self) -> None:
        skill = ix.skill()
        for command, _ in COMMANDS:
            assert command in skill
        for schema in ("ix.v1.validation", "ix.v1.plan", "ix.v1.registered", "ix.v1.deviations",
                       "ix.v1.genres", "ix.v1.kinds", "ix.v1.error"):  # fmt: skip
            assert schema in skill
        for code, meaning in EXIT_CODES.items():
            assert f"| {code} | {meaning} |" in skill
        for kind in PROBLEM_KINDS:
            assert f"`{kind}`" in skill
        for word in ("--amend", "--reason", '"complete": false', "allowed", "got"):
            assert word in skill
        assert "ix.v2" not in skill and "--format" not in skill and "--check" not in skill

    @pytest.mark.parametrize("genre", sorted(EXAMPLES))
    def test_every_example_it_teaches_validates_and_plans(self, genre: str, tmp_path: Path) -> None:
        assert f"```yaml\n{EXAMPLES[genre].rstrip()}\n```" in ix.skill()
        file = tmp_path / "experiment.yaml"
        file.write_text(EXAMPLES[genre])
        result = ix.validate(file)
        assert result.valid, result.problems
        assert result.warnings == () and result.genre == genre
        assert ix.plan(file).trials.total > 0

    @pytest.mark.parametrize("genre", sorted(EXAMPLES))
    def test_no_example_restates_a_default(self, genre: str, tmp_path: Path) -> None:
        """An example shows the minimum: every value it sets differs from what it would get
        by leaving the key out."""
        data = yaml.safe_load(EXAMPLES[genre])
        file = tmp_path / "experiment.yaml"
        file.write_text(EXAMPLES[genre])
        full = ix.resolve(file)
        for key in ("repeats", "seed", "control", "comparison", "environment", "rationale"):
            if key not in data:
                continue
            file.write_text(yaml.safe_dump({k: v for k, v in data.items() if k != key}))
            without = ix.validate(file)
            assert not without.valid or getattr(ix.resolve(file), key, None) != getattr(
                full, key, None
            ), (genre, key)
        for ref in [data.get("primary", {}), *data.get("guardrails", [])]:
            for reduction in ("statistic", "estimator"):
                if reduction in ref:
                    trimmed = yaml.safe_load(EXAMPLES[genre])
                    target = (
                        trimmed["primary"]
                        if ref is data.get("primary")
                        else trimmed["guardrails"][data["guardrails"].index(ref)]
                    )
                    del target[reduction]
                    file.write_text(yaml.safe_dump(trimmed))
                    assert ix.resolve(file) != full, (genre, reduction)

    def test_there_is_an_example_for_each_built_in_genre(self) -> None:
        assert set(EXAMPLES) == {g.label for g in ix.genres().genres.values() if "." not in g.label}


# --- the checked-in examples and the lab -----------------------------------------------


def _lab() -> Path:
    for parent in PACKAGE.parents:
        if (parent / "lab").is_dir() and (parent / "justfile").is_file():
            return parent / "lab"
    raise AssertionError("the repository's lab directory was not found above the ix package")


class TestExamplesAndLab:
    @pytest.mark.parametrize("example", ["proxy-streaming", "catalog-routing"])
    def test_each_example_validates_and_plans_clean(self, example: str) -> None:
        path = str(EXAMPLES_DIR / example)
        validated = _run("validate", path, "--json")
        assert validated.exit_code == 0, validated.stdout
        document = _json(validated)
        assert document["valid"] and document["problems"] == [] == document["warnings"]
        planned = _run("plan", path, "--json")
        assert planned.exit_code == 0, planned.stderr
        assert _json(planned)["experiment"] == example

    def test_the_proxy_comparison(self) -> None:
        plan = ix.plan(EXAMPLES_DIR / "proxy-streaming")
        assert (plan.genre, plan.control) == ("compare", "buffered")
        assert set(plan.factors) == {"size", "rate"}
        assert (plan.trials.cells, plan.probes.count, plan.trials.repeats) == (9, 1, 5)
        assert plan.trials.total == 135 and plan.duration.seconds == 135 * 75
        assert {s.label: s.metrics for s in plan.sensors} == {
            "timing": ("ttfb", "throughput"), "memory": ("memory_peak",),
        }  # fmt: skip
        declared = {(e.effect, e.source) for e in plan.effects if e.declared_by == "kind"}
        assert ("process", "subjects.buffered") in declared and ("network", "probes.0") in declared
        added = [e.effect for e in plan.effects if e.declared_by == "experiment"]
        assert added == ["binds 127.0.0.1:9000"]

    def test_the_agent_evaluation(self) -> None:
        plan = ix.plan(EXAMPLES_DIR / "catalog-routing")
        assert plan.genre == "evaluate" and plan.control is None
        assert plan.probes.ids == ("architecture-bottleneck", "decoy-clear-ask", "vague-ask")
        assert plan.trials.total == 9 and plan.spend.estimated == pytest.approx(0.45)
        assert {s.label for s in plan.sensors} == {"activation", "usage"}
        assert {e.effect for e in plan.effects} == {"spend", "tools"}

    def test_the_experiment_the_readme_shows_validates(self, tmp_path: Path) -> None:
        readme = (PACKAGE / "README.md").read_text(encoding="utf-8")
        blocks = [b for b in re.findall(r"```yaml\n(.*?)```", readme, re.S) if "genre:" in b]
        assert blocks, "the README shows no experiment"
        for index, block in enumerate(blocks):
            file = tmp_path / f"readme-{index}.yaml"
            file.write_text(block)
            result = ix.validate(file)
            assert result.valid and result.warnings == (), result.problems

    def test_every_lab_experiment_validates_and_plans(self) -> None:
        """The repository's lab is written in the current format."""
        experiments = sorted(path.parent for path in _lab().glob("*/experiment.yaml"))
        assert [path.name for path in experiments] == ["catalog-routing"]
        for path in experiments:
            result = ix.validate(path)
            assert result.valid and result.warnings == (), (path.name, result.problems)
            plan = ix.plan(path)
            assert plan.trials.total == plan.probes.count * plan.trials.repeats

    def test_the_labs_routing_experiment(self) -> None:
        plan = ix.plan(_lab() / "catalog-routing")
        assert plan.genre == "evaluate" and plan.probes.count == 8
        assert plan.decides == ("catalog has activated_correctly >= 0.9",)
        assert plan.spend.ceiling == 5.0 and plan.spend.estimated == pytest.approx(2.4)

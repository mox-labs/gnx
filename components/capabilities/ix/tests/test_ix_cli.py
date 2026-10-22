"""The ``ix`` command line as an agent drives it.

An agent acts on the exit code, one JSON document on stdout, and one error envelope on
stderr. These tests pin that contract, the plan a person approves, the checked-in examples
and the lab. The skill has its own file. Nothing here starts a subject or reaches a network.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from click import unstyle
from click.testing import CliRunner, Result

import ix
from ix.adapters._in.cli import main
from ix.application.skill import COMMANDS
from ix.domain.errors import ERROR_KINDS, EXIT_CODES, EXIT_MEANINGS
from ix.domain.problem import PROBLEM_KINDS

Write = Any  # the write_experiment fixture

PACKAGE = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = PACKAGE / "examples"
SRC = PACKAGE / "src" / "ix"


def _run(*args: str) -> Result:
    return CliRunner(env={"COLUMNS": "80"}).invoke(main, list(args))


def _help(*command: str) -> str:
    """A command's help as plain text: Rich styles it wherever it sees a CI runner."""
    return unstyle(_run(*command, "--help").stdout)


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
    # Changed requirement: ``details`` carries the wrong value and where it is, and
    # ``complete`` says whether checking found every problem (the shared envelope).
    assert list(error) == [
        "kind", "message", "fix", "complete", "details", "problems", "deviations"
    ]  # fmt: skip
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
            assert error["details"] == {"path": str(tmp_path / "nowhere")}

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
        assert result.exit_code == 7  # the shared code of a conflict
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
        assert result.stderr.startswith("error (config):")
        assert "margin_required" in result.stderr and "fix: margin: 5%" in result.stderr
        usage = _run("validate")
        assert usage.exit_code == 2 and usage.stderr.startswith("error (usage):")

    def test_a_defect_in_ix_is_still_one_envelope_of_kind_unknown(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def broken(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("a bug")

        monkeypatch.setattr(ix, "validate", broken)
        result = _run("validate", str(tmp_path), "--json")
        error = _error(result)
        assert result.exit_code == 1 and error["kind"] == "unknown"
        assert error["message"] == "RuntimeError: a bug" and "bug in ix" in error["fix"]
        for_a_person = _run("validate", str(tmp_path))
        assert "Traceback" in for_a_person.stderr
        assert "error (unknown): RuntimeError: a bug" in for_a_person.stderr

    def test_ctrl_c_is_one_envelope_of_kind_interrupted_and_exits_130(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The code every capability CLI gives a command cut short by Ctrl-C; not exit 12,
        which is a run that was stopped and reported."""

        def interrupted(*args: Any, **kwargs: Any) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(ix, "validate", interrupted)
        result = _run("validate", str(tmp_path), "--json")
        assert result.exit_code == 130 == EXIT_CODES["interrupted"]
        assert json.loads(result.stderr) == {
            "schema": "ix.v1.error",
            "error": {
                "kind": "interrupted",
                "message": "interrupted before the command finished",
                "fix": "run the command again",
                "complete": True,
                "details": {},
                "problems": [],
                "deviations": [],
            },
        }
        assert EXIT_CODES["run_interrupted"] == 12 and "interrupted" in ix.ERROR_KINDS
        for_a_person = _run("validate", str(tmp_path))
        assert for_a_person.exit_code == 130
        assert "error (interrupted):" in for_a_person.stderr

    def test_help_answers_to_h(self) -> None:
        assert _run("-h").stdout == _run("--help").stdout != ""
        assert _run("plan", "-h").exit_code == 0

    def test_error_kinds_map_through_the_one_exit_code_table(self) -> None:
        assert ix.EXIT_CODES is EXIT_CODES and set(EXIT_MEANINGS) == set(EXIT_CODES)
        assert ix.ConfigError("x").exit_code == 3
        assert ix.RegistrationConflictError("x", ()).exit_code == 7
        assert ix.NotFoundError("x").exit_code == 4 and ix.IxError("x").exit_code == 1
        assert ix.UsageError("x").exit_code == 2

    def test_every_error_class_has_a_listed_kind(self) -> None:
        def subclasses(cls: type[ix.IxError]) -> set[type[ix.IxError]]:
            return {cls} | {found for sub in cls.__subclasses__() for found in subclasses(sub)}

        assert {cls.kind for cls in subclasses(ix.IxError)} == set(ERROR_KINDS)
        assert set(ERROR_KINDS) <= set(EXIT_CODES)

    def test_the_help_prints_each_exit_code_on_a_line_of_its_own(self) -> None:
        lines = [line.strip() for line in _help("run").splitlines()]
        for kind, code in EXIT_CODES.items():
            assert any(line.startswith(f"{code}  {kind}:") for line in lines), kind

    def test_the_exit_code_table_is_written_once(self) -> None:
        """No module restates the table: the codes' meanings appear in one file."""
        marker = "no experiment file, no registration, or no run"
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
            # Docstrings are not messages.
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

    def test_the_commands_are_these(self) -> None:
        assert sorted(main.commands) == [
            "deviations", "genres", "kinds", "plan", "register", "report", "run", "runs",
            "validate",
        ]  # fmt: skip
        assert {command.split()[1] for command, _ in COMMANDS} == set(main.commands)
        result = _run("results", "x", "--json")
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
        # Commentary, so on stderr: stdout holds the validation alone.
        assert "Incomplete" in _run("validate", path).stderr

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
        for_a_person = _run("validate", str(write_experiment(compare_data)))
        assert "1 warning(s)" in for_a_person.stderr and "subjects_differ_in" in for_a_person.stderr
        assert "warning" not in for_a_person.stdout

    def test_for_a_person_it_lists_path_kind_message_and_fix_and_what_is_next(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        path = str(write_experiment(compare_data))
        valid = _run("validate", path)
        assert valid.exit_code == 0 and "valid proxy (compare)" in valid.stdout
        # Changed requirement: the next step is commentary, so it is on stderr.
        assert "Next: ix plan" in valid.stderr and "Next:" not in valid.stdout
        del compare_data["margin"]
        invalid = _run("validate", str(write_experiment(compare_data)))
        assert invalid.exit_code == 3
        assert "invalid" in invalid.stdout and "margin_required" in invalid.stdout
        assert "fix: margin: 5%" in invalid.stdout


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
            "control", "factors", "probes", "calibration", "trials", "schedule_digest", "duration",
            "order", "sensors", "effects", "registration", "sensitivity",
            "provenance", "unimplemented", "unstartable", "notes", "warnings", "runnable",
        ]  # fmt: skip
        assert re.fullmatch(r"[0-9a-f]{64}", document["schedule_digest"])
        assert document["unimplemented"] == []
        # Its subjects run ./proxy, which is not there: the plan only shows, so it exits 0,
        # and says in the document as on the terminal that a run here would not start.
        assert document["unstartable"] and document["runnable"] is False
        assert "Runnable" in _run("plan", str(file)).stdout
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
            "Trials:", "Duration:", "Order:", "Effects:", "Registration:", "Design",
        )]  # fmt: skip
        assert order == sorted(order) and "Spend" not in text
        flat = " ".join(text.split())
        assert "streaming is at most 5% worse than buffered on ttfb p99" in flat
        assert "Comparison: non-inferiority" in flat
        assert "memory_peak at most 5% worse than buffered" in flat
        assert "Verdict: holds / fails / inconclusive" in flat
        assert "Control: buffered" in flat
        assert "2 subjects x 9 cells x 1 probe x 10 repeats = 180, + 90 A/A = 270" in flat
        assert "Duration: about 5h 38m" in flat
        assert "Registration: none" in flat
        assert "Next: ix register" in result.stderr and "Next:" not in flat

    def test_the_genres_note_is_a_footer_not_a_column(self) -> None:
        text = _run("plan", str(EXAMPLES_DIR / "proxy-streaming")).stdout
        assert text.count("design record") == 1
        footer = text[text.index("└") :]
        assert "The genre's design is checked by simulation at its minimum repeats" in footer

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
        planned = _run("plan", path)
        text = " ".join(planned.stdout.split())
        assert re.search(r"Registration: \d+ deviations", text)
        assert f"Next: ix deviations {path}" in " ".join(planned.stderr.split())

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
    """``plan``, ``genres`` and ``kinds`` are read in a terminal: at 80 columns no table
    overflows and nothing is cut off. Changed requirement: off a terminal a line of prose is
    not wrapped at all (a path split at column 80 cannot be copied), so only the tables are
    held to the width."""

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
            if line[:1] in set("┏┃┡│└┌├╭╰"):
                assert len(line) <= 80, line
        assert "…" not in result.stdout

    def test_off_a_terminal_a_long_line_is_not_wrapped(self) -> None:
        result = _run("validate", "a/path/" + "long-directory-name/" * 6 + "experiment.yaml")
        assert result.exit_code == 4
        named = [line for line in result.stderr.splitlines() if "long-directory-name" in line]
        assert all(line.count("long-directory-name") == 6 for line in named), result.stderr

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

    def test_deviations_is_its_own_command_and_exits_14_when_it_finds_any(
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
        assert result.exit_code == 14 and result.stderr == ""
        document = _json(result)
        assert list(document) == ["schema", "experiment", "location", "registered_at", "deviations"]
        assert document["schema"] == "ix.v1.deviations"
        assert [d["field"] for d in document["deviations"]] == [
            "design.repeats", "experiment_digest", "plan_digest",
        ]  # fmt: skip
        assert document["deviations"][0] == {
            "field": "design.repeats", "registered": 10, "current": 9,
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
        result = _run(
            "register", str(file), "--amend", "--reason", "the plan's sensitivity was 6%", "--json"
        )
        assert result.exit_code == 0
        document = _json(result)
        assert document["outcome"] == "amended"
        (amendment,) = document["registration"]["amendments"]
        assert amendment["reason"] == "the plan's sensitivity was 6%"
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
        assert listed.exit_code == 14
        flat = " ".join(listed.stdout.split())
        assert "design.margin.value: registered 5.0, now 20.0" in flat
        assert "--amend" in listed.stderr and "--amend" not in flat
        refused = _run("register", path)
        assert refused.exit_code == 7 and refused.stderr.startswith("error (conflict):")
        assert "design.margin.value" in refused.stderr and "fix:" in refused.stderr


# --- genres and kinds ------------------------------------------------------------------


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
        assert compare["design"]["repeats"] == {"default": 10, "min": 5}

    def test_the_kinds_document(self) -> None:
        result = _run("kinds", "--json")
        assert result.exit_code == 0
        document = _json(result)
        assert list(document) == ["schema", "kinds", "rejected", "runnable", "quarantined"]
        assert "ix.v1.probe.http-load" in document["runnable"]
        assert "ix.v1.subject.agent-session" in document["runnable"]
        assert "ix.v1.probe.inspect-task" not in document["runnable"]
        assert document["schema"] == "ix.v1.kinds"
        load = document["kinds"]["ix.v1.probe.http-load"]
        assert load["schema"] == "ix.v1.kind"
        assert load["options"]["duration"] == {
            "type": "duration", "required": True, "allowed": [], "required_when": {},
            "choices": {}, "within": "", "binds": [],
            "description": "how long the measured load lasts",
        }  # fmt: skip
        command = document["kinds"]["ix.v1.subject.process"]["options"]["command"]
        assert (command["type"], command["within"]) == ("command", "cwd")
        runtime = document["kinds"]["ix.v1.subject.agent-session"]["options"]["runtime"]
        assert runtime["required"] and set(runtime["choices"]) == {"claude-sdk", "mock"}
        runtime = document["kinds"]["ix.v1.subject.model-call"]["options"]["runtime"]
        assert runtime["required"] and set(runtime["choices"]) == {"hardline", "mock"}
        assert load["options"]["rate"]["required_when"] == {"load_model": "open"}
        assert (load["needs"], load["offers"]) == (["url"], ["load"])
        judge = document["kinds"]["ix.v1.sensor.judge"]
        assert [m["judged"] for m in judge["metrics"]] == [True, True, True, True, False]

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

    def test_a_kind_named_with_a_segment_the_registry_forbids_is_refused(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        """One grammar: a kind ix accepted that matrix's registry could never register would
        be a kind no adapter can serve. ``agent`` is a component kind in matrix's grammar."""
        file: Path = write_experiment(compare_data)
        (file.parent / "kinds").mkdir()
        (file.parent / "kinds" / "agent.yaml").write_text(
            "name: lab.v1.subject.agent\nsummary: s\n"
        )
        lab = str(file.parent)
        document = _json(_run("kinds", "--lab", lab, "--json"))
        assert "lab.v1.subject.agent" not in document["kinds"]
        (refused,) = document["rejected"]["lab.v1.subject.agent"]
        assert (refused["kind"], refused["path"], refused["got"]) == (
            "kind_invalid", "name", "lab.v1.subject.agent",
        )  # fmt: skip
        assert "segment 'agent'" in refused["message"] and "rename" in refused["fix"]
        printed = " ".join(_run("kinds", "--lab", lab).stdout.split())
        assert "refused lab.v1.subject.agent" in printed and "segment 'agent'" in printed


# --- the checked-in examples and the lab -----------------------------------------------


def _lab() -> Path:
    for parent in PACKAGE.parents:
        if (parent / "lab").is_dir() and (parent / "justfile").is_file():
            return parent / "lab"
    raise AssertionError("the repository's lab directory was not found above the ix package")


class TestExamplesAndLab:
    @pytest.mark.parametrize("example", ["proxy-streaming", "catalog-routing", "harness-integrity"])
    def test_each_example_validates_and_plans_clean(self, example: str) -> None:
        path = str(EXAMPLES_DIR / example)
        validated = _run("validate", path, "--json")
        assert validated.exit_code == 0, validated.stdout
        document = _json(validated)
        assert document["valid"] and document["problems"] == []
        planned = _run("plan", path, "--json")
        assert planned.exit_code == 0, planned.stderr
        plan = _json(planned)
        assert plan["experiment"] == example
        # Every kind they use has an adapter here; whether this host can start the subjects
        # (./proxy, the claude extra) is the plan's unstartable.
        assert document["warnings"] == [] and plan["unimplemented"] == []
        assert plan["runnable"] is (plan["unstartable"] == [])

    def test_the_proxy_comparison(self) -> None:
        plan = ix.plan(EXAMPLES_DIR / "proxy-streaming")
        assert (plan.genre, plan.control) == ("compare", "buffered")
        assert set(plan.factors) == {"size", "rate"}
        assert (plan.trials.cells, plan.probes.count, plan.trials.repeats) == (9, 1, 10)
        assert plan.trials.total == 270 and plan.duration.seconds == 270 * 75
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
        assert plan.probes.ids == ("decoy-clear-ask", "does-it-follow", "vague-ask")
        assert plan.runnable, plan.unstartable
        assert plan.trials.total == 36
        # 36 trials, every one passing, can show a pass rate above 0.9; 30 could not.
        assert not plan.sensitivity.text.startswith("This run cannot hold")
        assert {s.label for s in plan.sensors} == {"activation", "usage"}
        assert {e.effect for e in plan.effects} == {"tools", "process", "filesystem"}

    def test_the_experiment_the_readme_shows_validates(self, tmp_path: Path) -> None:
        readme = (PACKAGE / "README.md").read_text(encoding="utf-8")
        blocks = [b for b in re.findall(r"```yaml\n(.*?)```", readme, re.S) if "genre:" in b]
        assert blocks, "the README shows no experiment"
        for index, block in enumerate(blocks):
            file = tmp_path / f"readme-{index}.yaml"
            file.write_text(block)
            result = ix.validate(file)
            assert result.valid and result.warnings == (), result.problems

    def test_the_agent_how_to_s_experiments_validate_and_its_dry_run_grades_as_it_says(
        self, tmp_path: Path
    ) -> None:
        """The how-to's lab, built from its own blocks: the experiment and its two probe
        files validate clean, and the dry run grades bad-input at 0.6, as the page says."""
        page = (PACKAGE / "docs" / "how-to" / "evaluate-an-agent.md").read_text(encoding="utf-8")
        experiments = [b for b in re.findall(r"```yaml\n(.*?)```", page, re.S) if "genre:" in b]
        probes = re.findall(r"```markdown\n(.*?)\n```\n", page, re.S)
        parse_duration, routing, skill_experiment = experiments
        plain, bad, bad_canned, review_ask, skill_file, greet = probes

        def lab(name: str, experiment: str, files: dict[str, str]) -> Path:
            folder = tmp_path / name
            (folder / "probes").mkdir(parents=True)
            (folder / "experiment.yaml").write_text(experiment)
            for file, text in files.items():
                (folder / "probes" / file).write_text(text)
            return folder

        skill = lab("review-skill", skill_experiment, {"review-ask.md": review_ask})
        (skill / "skills" / "review").mkdir(parents=True)
        (skill / "skills" / "review" / "SKILL.md").write_text(skill_file + "\n")
        text_check = lab(
            "greet",
            routing.replace("activated_correctly", "text_matched"),
            {"greet.md": greet},
        )
        for folder in (
            lab("parse-duration", parse_duration, {"plain-units.md": plain, "bad-input.md": bad}),
            lab("review-routing", routing, {"review-ask.md": review_ask}),
            skill,
            text_check,
        ):
            result = ix.validate(folder)
            assert result.valid and result.warnings == (), (folder.name, result.problems)
        # The dry run as the page gives it: the experiment as written, nothing edited.
        dry = lab("dry", parse_duration, {"plain-units.md": plain, "bad-input.md": bad_canned})
        report = ix.run(dry, dry_run=True)
        assert report.simulated == "all" and report.verdict is None and report.exit_code == 0
        assert report.dry_run and report.indication is None

        def rows(kind: str) -> list[dict[str, Any]]:
            text = Path(report.tables[kind]).read_text(encoding="utf-8")
            return [json.loads(line) for line in text.splitlines()]

        probe_of = {row["seq"]: row["probe"] for row in rows("ix.v1.trial")}
        scores = {
            row["value"]
            for row in rows("ix.v1.reading")
            if row["metric"] == "tests_score" and probe_of[row["seq"]] == "bad-input"
        }
        assert scores == {0.6}

    def test_the_api_the_readme_declares_is_ix_s(self) -> None:
        readme = (PACKAGE / "README.md").read_text(encoding="utf-8")
        start = readme.index("`ix.__all__` is the declared API")
        section = readme[start : readme.index("Every part of", start)]
        named = set(re.findall(r"`([A-Za-z_]+)`", section))
        assert named == set(ix.__all__), named ^ set(ix.__all__)

    def test_every_lab_experiment_validates_and_plans(self) -> None:
        """The repository's lab is written in the current format."""
        experiments = sorted(path.parent for path in _lab().glob("*/experiment.yaml"))
        assert [path.name for path in experiments] == ["catalog-routing"]
        for path in experiments:
            result = ix.validate(path)
            assert result.valid, (path.name, result.problems)
            assert result.warnings == ()
            plan = ix.plan(path)
            assert plan.trials.total == plan.probes.count * plan.trials.repeats

    def test_the_labs_routing_experiment(self) -> None:
        plan = ix.plan(_lab() / "catalog-routing")
        assert plan.genre == "evaluate" and plan.probes.count == 8
        assert plan.decides == ("catalog has activated_correctly >= 0.9",)


# --- run, report and runs ----------------------------------------------------------------
# What these commands refuse before anything starts. A run itself is exercised with
# test-only adapters in test_run_path.py and with k6 in test_k6_run.py.


class TestRunReportAndRunsCommands:
    def test_run_refuses_an_unregistered_experiment_with_exit_15(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        result = _run("run", str(file), "--json")
        assert result.exit_code == 15 and result.stdout == ""
        error = _error(result)
        assert error["kind"] == "unregistered"
        assert f"ix register {file}" in error["fix"] and "--exploratory" in error["fix"]
        assert not (file.parent / ".ix").exists()

    def test_run_refuses_an_invalid_experiment_and_a_kind_it_has_no_adapter_for(
        self, compare_data: dict[str, Any], evaluate_data: dict[str, Any], write_experiment: Write
    ) -> None:
        del compare_data["margin"]
        invalid = _run("run", str(write_experiment(compare_data)), "--exploratory", "--json")
        assert invalid.exit_code == 3
        assert _error(invalid)["problems"][0]["kind"] == "margin_required"
        evaluate_data["probes"][0] = {
            "id": "ask", "kind": "inspect-task", "task": "humaneval",
            "truth": {"expectation": "must_trigger", "expected_skill": "review"},
        }  # fmt: skip
        other = str(write_experiment(evaluate_data, "agent"))
        agent = _run("run", other, "--exploratory", "--json")
        assert agent.exit_code == 3 and "no adapter for" in _error(agent)["message"]
        assert "ix.v1.probe.inspect-task" in _error(agent)["message"]

    def test_report_and_runs_before_any_run(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file: Path = write_experiment(compare_data)
        missing = _run("report", str(file), "--json")
        assert missing.exit_code == 4 and f"ix run {file}" in _error(missing)["fix"]
        nowhere = _run("report", str(file.parent / "no-such-run"), "--json")
        assert nowhere.exit_code == 4
        listed = _run("runs", str(file), "--json")
        assert listed.exit_code == 0
        assert _json(listed) == {
            "schema": "ix.v1.runs",
            "location": str(file.parent.resolve() / ".ix" / "runs"),
            "runs": [],
        }
        assert "no runs in" in _run("runs", str(file)).stdout

    def test_the_exit_codes_of_a_run_are_in_the_one_table(self) -> None:
        assert {EXIT_CODES[kind] for kind in ("fails", "inconclusive", "run_interrupted")} == {
            10, 11, 12,
        }  # fmt: skip
        assert EXIT_CODES["unregistered"] == 15 and EXIT_CODES["run_failed"] == 8
        assert EXIT_CODES["running"] == 13 and EXIT_CODES["deviations"] == 14
        assert EXIT_CODES["simulated"] == 16
        assert ix.UnregisteredError("x").exit_code == 15
        help_text = " ".join(_help("run").split())
        for said in ("--exploratory", "10 fails", "11 inconclusive", "exits 12", "exits 8",
                     "exits 16"):  # fmt: skip
            assert said in help_text, said
        report_help = " ".join(_help("report").split())
        for said in ("10 fails", "11 inconclusive", "12 for a run that was stopped",
                     "8 for one the harness", "16 for a registered run a stand-in",
                     "13 for a run that is still running"):  # fmt: skip
            assert said in report_help, said


def test_the_agreed_kinds_keep_their_agreed_codes() -> None:
    """``components/capabilities/exit-codes.json`` holds the codes every tool agrees on; a
    tool's own kinds take codes from 10 up, one kind to a code. Changed requirement: the
    table now shares ``conflict`` (7) and ``run_failed`` (8), so ix's ``unregistered`` moved
    from 7 to 15 and ``deviations`` from 9 (now ``store``) to 14."""
    agreed = json.loads((Path(ix.__file__).parents[3] / "exit-codes.json").read_text())["kinds"]
    shared = set(agreed) & set(EXIT_CODES)
    assert shared == {
        "ok", "unknown", "usage", "config", "not_found", "conflict", "run_failed", "interrupted",
    }  # fmt: skip
    assert agreed["interrupted"] == 130
    assert {kind: EXIT_CODES[kind] for kind in shared} == {kind: agreed[kind] for kind in shared}
    own = {kind: code for kind, code in EXIT_CODES.items() if kind not in agreed}
    assert all(10 <= code < 128 for code in own.values()), own
    assert own["simulated"] == 16  # a registered run a stand-in answered is ix's own kind
    assert len(set(EXIT_CODES.values())) == len(EXIT_CODES)  # one kind to a code
    assert not set(own.values()) & set(agreed.values())


class TestStandardInputAndWarnings:
    def test_a_dash_reads_the_experiment_from_standard_input(
        self, compare_data: dict[str, Any]
    ) -> None:
        """Audit S15: a file argument takes ``-`` for standard input, as POSIX and clig.dev
        have it. Such an experiment has no file to be registered beside."""
        text = yaml.safe_dump(compare_data)
        valid = CliRunner().invoke(main, ["validate", "-", "--json"], input=text)
        assert valid.exit_code == 0 and _json(valid)["source"] == "<stdin>"
        planned = CliRunner().invoke(main, ["plan", "-", "--json"], input=text)
        assert planned.exit_code == 0 and _json(planned)["registration"]["status"] == "none"
        refused = CliRunner().invoke(main, ["register", "-", "--json"], input=text)
        assert refused.exit_code == 3 and "standard input" in _error(refused)["message"]

    def test_an_envelope_says_when_checking_stopped_early(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file = write_experiment({**compare_data, "repeats": "many"})
        error = _error(_run("plan", str(file), "--json"))
        assert error["kind"] == "config" and error["complete"] is False
        assert [p["kind"] for p in error["problems"]] == ["wrong_shape"]
        whole = write_experiment({**compare_data, "comparison": "nonsense"})
        assert _error(_run("plan", str(whole), "--json"))["complete"] is True

    def test_the_plan_document_carries_what_validate_warns_of(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        """Warnings travel inside the --json document, never only as prose on stderr."""
        compare_data["subjects"]["streaming"]["ready"] = "http://127.0.0.1:9090/healthz"
        result = _run("plan", str(write_experiment(compare_data)), "--json")
        assert result.exit_code == 0 and result.stderr == ""
        warnings = _json(result)["warnings"]
        assert [(w["path"], w["kind"]) for w in warnings] == [
            ("subjects.streaming", "subjects_differ_in")
        ]

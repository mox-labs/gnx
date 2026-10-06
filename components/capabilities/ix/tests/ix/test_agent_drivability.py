"""The CLI as an agent drives it (software-design-canon, rulings-agent-drivability F1–F8).

An agent acts on what a command returns: the exit code, one JSON document on stdout, one
JSON error line on stderr. These tests pin that contract. Everything runs offline — the
simulator, or a stand-in service where a live runtime would otherwise be reached.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner
from matrix import AgentDefinition, AgentResponse, AgentRuntimeError, BoundAgent

from ix.adapters._in.cli import _classify, main
from ix.adapters._out.engines.native import NativeEngine
from ix.adapters._out.filesystem_store import FilesystemStore
from ix.composition import validate_experiment
from ix.domain.errors import ConfigError, EngineError, LabNotFoundError, NotFoundError
from ix.domain.types import Probe, Reading, Subject
from ix.eval.analysis import aggregate_readings
from ix.eval.experiment import Experiment
from ix.eval.models import MAX_DETAILS, ExperimentConfig, ExperimentResults, ProbeResult
from ix.eval.sensors import ActivationSensor

if TYPE_CHECKING:
    from pathlib import Path

LIVE_SUBJECT = (
    "  - name: live\n"
    "    config: {runtime: {type: claude-sdk, setting_sources: [], "
    "permission_mode: bypassPermissions}}\n"
)


def _lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, live: bool) -> Path:
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    exp = tmp_path / "lab" / "e"
    (exp / "tasks").mkdir(parents=True)
    (exp / "experiment.yaml").write_text(
        "name: e\nsensor: {type: activation, expected_skill: x}\ntrials: 2\nrepeats: 3\n"
        "subjects:\n  - name: sim\n    config: {runtime: {type: simulated}}\n"
        + (LIVE_SUBJECT if live else "  - name: sim2\n    config: {runtime: {type: simulated}}\n")
    )
    for i in range(4):
        (exp / "tasks" / f"p{i}.md").write_text(
            f"---\nid: p{i}\nexpectation: must_trigger\n---\nprompt {i}\n"
        )
    return tmp_path / "lab"


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Two simulated subjects."""
    return _lab(tmp_path, monkeypatch, live=False)


@pytest.fixture
def live_lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One simulated subject and one live (claude-sdk) subject."""
    return _lab(tmp_path, monkeypatch, live=True)


def _invoke(*args: str) -> Any:
    return CliRunner().invoke(main, list(args))


def _error(result: Any) -> dict[str, Any]:
    """The one JSON error line on stderr — its last line; progress lines may precede it."""
    lines = result.stderr.strip().splitlines()
    errors = [line for line in lines if line.startswith('{"error"')]
    assert errors == lines[-1:], result.stderr
    body: dict[str, Any] = json.loads(errors[0])["error"]
    return body


# --- exit codes and machine-readable errors (F2) ----------------------------------------


class TestExitCodes:
    def test_unknown_experiment_is_not_found_with_a_working_fix(self, lab: Path):
        result = _invoke("run", "nope", "--lab", "lab", "--format", "json")
        assert result.exit_code == 4
        error = _error(result)
        assert error["kind"] == "not_found" and "nope" in error["message"]
        assert error["fix"] == "ix experiment list --lab lab"
        assert result.stdout == ""

    def test_unknown_lab_is_not_found_and_names_the_labs_that_exist(self, lab: Path):
        result = _invoke("experiment", "list", "--lab", "nolab", "--format", "json")
        assert result.exit_code == 4
        error = _error(result)
        assert error["kind"] == "not_found" and "--lab lab" in error["fix"]

    def test_unknown_subject_is_not_found(self, lab: Path):
        result = _invoke("run", "e", "--lab", "lab", "--subject", "zz", "--format", "json")
        assert result.exit_code == 4
        error = _error(result)
        assert error["kind"] == "not_found" and "Subjects: sim, sim2" in error["message"]

    def test_results_for_a_missing_experiment_does_not_suggest_running_it(self, lab: Path):
        """`ix results nope` used to suggest `ix run nope`, which fails the same way."""
        result = _invoke("results", "nope", "--lab", "lab", "--format", "json")
        assert result.exit_code == 4
        error = _error(result)
        assert "ix run" not in (error["fix"] or "")
        assert error["fix"] == "ix experiment list --lab lab"

    def test_no_results_yet_suggests_a_plan_not_a_spend(self, lab: Path):
        result = _invoke("results", "e", "--lab", "lab", "--format", "json")
        assert result.exit_code == 4
        assert _error(result)["fix"] == "ix run e --lab lab --plan"
        assert _invoke("run", "e", "--lab", "lab", "--plan").exit_code == 0  # the fix works

    def test_missing_subject_results_point_at_the_ones_that_exist(self, lab: Path):
        assert _invoke("run", "e", "--lab", "lab", "--subject", "sim").exit_code == 0
        result = _invoke("results", "e", "--lab", "lab", "--subject", "zz", "--format", "json")
        assert result.exit_code == 4
        error = _error(result)
        assert "Subjects with results: sim" in error["message"]
        assert error["fix"] == "ix results e --lab lab"

    def test_failed_validate_is_a_config_error_with_its_problems(self, lab: Path):
        (lab / "e" / "experiment.yaml").write_text(
            "name: e\nsensor: activaton\n"
            "subjects:\n  - name: a\n    config: {runtime: {type: strands}}\n"
        )
        result = _invoke("experiment", "validate", "e", "--lab", "lab", "--format", "json")
        assert result.exit_code == 3
        error = _error(result)
        assert error["kind"] == "config"
        assert len(error["problems"]) == 2
        assert any("strands" in p for p in error["problems"])

    def test_a_file_that_does_not_load_is_a_validate_problem_too(self, lab: Path):
        path = lab / "e" / "experiment.yaml"
        path.write_text(path.read_text() + "trails: 3\n")
        result = _invoke("experiment", "validate", "e", "--lab", "lab", "--format", "json")
        assert result.exit_code == 3
        assert "trails" in _error(result)["problems"][0]

    def test_usage_error_is_2(self, lab: Path):
        assert _invoke("run", "e", "--lab", "lab", "--trials", "0").exit_code == 2
        assert _invoke("run", "e", "--lab", "lab", "--all", "--subject", "sim").exit_code == 2

    def test_without_json_errors_stay_prose(self, lab: Path):
        result = _invoke("run", "nope", "--lab", "lab")
        assert result.exit_code == 4
        assert result.stderr.startswith("Error:")
        assert "Fix: ix experiment list --lab lab" in result.stderr

    def test_help_documents_the_exit_codes(self):
        result = _invoke("--help")
        text = " ".join(result.output.split())
        assert "3 config" in text and "4 not found" in text and "5 transient" in text


class _CauseError(Exception):
    def __init__(self, reason: str, retryable: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


def _runtime_error(cause: Exception | None) -> AgentRuntimeError:
    try:
        try:
            if cause is not None:
                raise cause
            raise AgentRuntimeError("agent 'x': session failed")
        except _CauseError as e:
            raise AgentRuntimeError("agent 'x': provider failed") from e
    except AgentRuntimeError as e:
        return e


class TestClassify:
    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (ConfigError("bad"), ("config", 3)),
            (NotFoundError("gone"), ("not_found", 4)),
            (LabNotFoundError("no lab"), ("not_found", 4)),
            (EngineError("inspect died"), ("engine", 1)),
            (RuntimeError("?"), ("unknown", 1)),
        ],
    )
    def test_ix_errors(self, error: Exception, expected: tuple[str, int]):
        assert _classify(error) == expected

    def test_a_retryable_runtime_cause_is_transient(self):
        assert _classify(_runtime_error(_CauseError("rate_limit", True))) == ("transient", 5)

    def test_an_auth_runtime_cause_is_auth(self):
        assert _classify(_runtime_error(_CauseError("auth", False))) == ("auth", 6)

    def test_a_runtime_error_with_no_structured_cause_is_unclassified(self):
        assert _classify(_runtime_error(None)) == ("unknown", 1)

    def test_a_transient_failure_exits_5_from_the_cli(
        self, lab: Path, monkeypatch: pytest.MonkeyPatch
    ):
        class _Service:
            async def run(self, *args: Any, **kwargs: Any) -> ExperimentResults:
                raise _runtime_error(_CauseError("timeout", True))

        monkeypatch.setattr("ix.composition.create_service", lambda *a, **k: _Service())
        result = _invoke("run", "e", "--lab", "lab", "--subject", "sim", "--format", "json")
        assert result.exit_code == 5
        assert _error(result)["kind"] == "transient"


# --- JSON everywhere (F3) ----------------------------------------------------------------


class TestJsonEverywhere:
    def test_lab_list(self, lab: Path):
        doc = json.loads(_invoke("lab", "list", "--format", "json").stdout)
        assert doc == {
            "schema": "ix.v1/labs",
            "labs": [{"name": "lab", "experiments": 1, "path": "lab"}],
        }

    def test_experiment_list_and_show(self, lab: Path):
        listed = json.loads(
            _invoke("experiment", "list", "--lab", "lab", "--format", "json").stdout
        )
        assert listed["schema"] == "ix.v1/experiments"
        assert listed["experiments"][0]["name"] == "e" and listed["experiments"][0]["valid"]
        shown = json.loads(
            _invoke("experiment", "show", "e", "--lab", "lab", "--format", "json").stdout
        )
        assert shown["schema"] == "ix.v1/experiment"
        assert [s["runtime"] for s in shown["subjects"]] == ["simulated", "simulated"]
        assert shown["probes"][0] == {
            "id": "p0",
            "prompt": "prompt 0",
            "metadata": {"expectation": "must_trigger"},
        }

    def test_validate_success(self, lab: Path):
        result = _invoke("experiment", "validate", "e", "--lab", "lab", "--format", "json")
        assert result.exit_code == 0, result.output
        doc = json.loads(result.stdout)
        assert doc["schema"] == "ix.v1/validation" and doc["valid"] and doc["problems"] == []

    def test_experiment_list_exits_3_on_an_invalid_experiment_and_still_lists(self, lab: Path):
        bad = lab / "broken"
        bad.mkdir()
        (bad / "experiment.yaml").write_text("trails: 3\n")
        table = _invoke("experiment", "list", "--lab", "lab")
        assert table.exit_code == 3
        assert "broken" in table.stdout and "e" in table.stdout
        result = _invoke("experiment", "list", "--lab", "lab", "--format", "json")
        assert result.exit_code == 3
        rows = {r["name"]: r for r in json.loads(result.stdout)["experiments"]}
        assert rows["e"]["valid"] and not rows["broken"]["valid"]
        assert "trails" in rows["broken"]["error"]
        assert _error(result)["fix"] == "ix experiment validate broken --lab lab"

    def test_run_and_results_are_always_a_list(self, lab: Path):
        one = _invoke("run", "e", "--lab", "lab", "--subject", "sim", "--format", "json")
        both = _invoke("run", "e", "--lab", "lab", "--seed", "1", "--format", "json")
        assert isinstance(json.loads(one.stdout), list) and len(json.loads(one.stdout)) == 1
        assert [r["subject"] for r in json.loads(both.stdout)] == ["sim", "sim2"]
        shown = json.loads(
            _invoke("results", "e", "--lab", "lab", "--subject", "sim", "--format", "json").stdout
        )
        assert isinstance(shown, list) and shown[0]["schema"] == "ix.v1/results"

    def test_compare_names_its_schema_and_both_runs(self, lab: Path):
        ran = json.loads(_invoke("run", "e", "--lab", "lab", "--format", "json").stdout)
        doc = json.loads(
            _invoke("compare", "e", "sim", "sim2", "--lab", "lab", "--format", "json").stdout
        )
        assert doc["schema"] == "ix.v1/comparison"
        assert (doc["run_id_a"], doc["run_id_b"]) == (ran[0]["run_id"], ran[1]["run_id"])


# --- no accidental live spend (F1) --------------------------------------------------------


class TestPlanAndLiveGuard:
    def test_plan_counts_sessions_and_marks_live_without_running(self, live_lab: Path):
        result = _invoke("run", "e", "--lab", "lab", "--plan", "--format", "json")
        assert result.exit_code == 0, result.output
        plan = json.loads(result.stdout)
        assert plan["schema"] == "ix.v1/plan"
        rows = {r["subject"]: r for r in plan["subjects"]}
        assert rows["live"]["runtime"] == "claude-sdk" and rows["live"]["live"]
        assert rows["live"]["permission_mode"] == "bypassPermissions"
        assert rows["sim"]["live"] is False
        assert rows["live"]["sessions"] == 4 * 2 * 3
        assert (plan["total_sessions"], plan["live_sessions"]) == (48, 24)
        assert plan["refused"] is True  # what a bare run would do
        assert not (live_lab / "e" / "results").exists()

    def test_plan_table(self, live_lab: Path):
        result = _invoke("run", "e", "--lab", "lab", "--plan")
        assert result.exit_code == 0
        assert "4 × 2 × 3 = 24" in result.stdout and "LIVE" in result.stdout

    def test_implicit_multi_subject_live_run_is_refused(self, live_lab: Path):
        result = _invoke("run", "e", "--lab", "lab", "--format", "json")
        assert result.exit_code == 3
        error = _error(result)
        assert "sim (simulated, 24 sessions)" in error["message"]
        assert "live (claude-sdk, 24 sessions, live)" in error["message"]
        assert "--subject" in error["fix"] and "--all" in error["fix"]
        assert not (live_lab / "e" / "results").exists()

    def test_all_opts_in(self, live_lab: Path, monkeypatch: pytest.MonkeyPatch):
        ran: list[str] = []

        class _Service:
            async def run(self, config: Any, subject: Any = None, **kw: Any) -> ExperimentResults:
                ran.append(subject.name)
                return ExperimentResults(experiment_name="e", subject=kw["save_as"])

        monkeypatch.setattr("ix.composition.create_service", lambda *a, **k: _Service())
        result = _invoke("run", "e", "--lab", "lab", "--all", "--format", "json")
        assert result.exit_code == 0, result.output
        assert ran == ["sim", "live"]

    def test_simulate_or_a_named_subject_is_not_refused(self, live_lab: Path):
        assert _invoke("run", "e", "--lab", "lab", "--simulate").exit_code == 0
        assert _invoke("run", "e", "--lab", "lab", "--subject", "sim").exit_code == 0

    def test_several_simulated_subjects_run_as_before(self, lab: Path):
        assert _invoke("run", "e", "--lab", "lab").exit_code == 0


# --- provenance (F4) ---------------------------------------------------------------------


class TestProvenance:
    def test_seed_and_simulate_are_recorded(self, lab: Path):
        seeded = _invoke(
            "run", "e", "--lab", "lab", "--subject", "sim", "--seed", "7", "--format", "json"
        )
        unseeded = _invoke(
            "run", "e", "--lab", "lab", "--subject", "sim", "--simulate", "--format", "json"
        )
        assert json.loads(seeded.stdout)[0]["seed"] == 7
        assert json.loads(seeded.stdout)[0]["simulated"] is False  # its own runtime simulates
        assert json.loads(unseeded.stdout)[0]["seed"] is None
        assert json.loads(unseeded.stdout)[0]["simulated"] is True

    def test_simulated_json_status_is_unmeasured(self, lab: Path):
        doc = json.loads(
            _invoke("run", "e", "--lab", "lab", "--subject", "sim", "--format", "json").stdout
        )
        assert doc[0]["status"] == "unmeasured"
        table = _invoke("results", "e", "--lab", "lab", "--subject", "sim")
        assert "Status: harness only" in table.stdout

    def test_simulating_a_live_subject_never_replaces_its_results(self, live_lab: Path):
        store = FilesystemStore(live_lab)
        measured = ExperimentResults(
            experiment_name="e",
            subject="live",
            run_id="measured",
            families=("claude",),
            probe_results=tuple(
                ProbeResult(probe_id=f"p{i}", score=1.0, passed=True) for i in range(4)
            ),
        )
        store.save_summary("e", measured)

        result = _invoke(
            "run", "e", "--lab", "lab", "--subject", "live", "--simulate", "--format", "json"
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)[0]["subject"] == "live@simulated"
        assert store.load_summary("e", "live").run_id == "measured"
        assert store.load_summary("e", "live@simulated").families == ("simulated",)

        shown = _invoke(
            "results", "e", "--lab", "lab", "--subject", "live@simulated", "--format", "json"
        )
        assert shown.exit_code == 0 and json.loads(shown.stdout)[0]["subject"] == "live@simulated"
        compared = _invoke(
            "compare", "e", "live", "live@simulated", "--lab", "lab", "--format", "json"
        )
        assert compared.exit_code == 0, compared.output
        assert json.loads(compared.stdout)["run_id_a"] == "measured"

    async def test_config_hash_covers_the_probes(self, tmp_path: Path):
        class _Echo:
            async def run(self, definition: Any, task: str) -> AgentResponse:
                return AgentResponse(content=task, family="test")

        service = Experiment(
            sensor=ActivationSensor(expected_skill="x"),
            store=FilesystemStore(tmp_path),
            engine=NativeEngine(),
            agents=lambda s, t, r=0: BoundAgent(AgentDefinition(name="a"), _Echo()),
        )
        base = ExperimentConfig(name="e", trials=1, probes=(Probe(id="p", prompt="q"),))
        reworded = base.model_copy(update={"probes": (Probe(id="p", prompt="q2"),)})
        retagged = base.model_copy(
            update={"probes": (Probe(id="p", prompt="q", metadata={"expectation": "acceptable"}),)}
        )
        hashes = {(await service.run(c)).config_hash for c in (base, reworded, retagged)}
        assert len(hashes) == 3
        assert (await service.run(base)).config_hash in hashes  # and stable


# --- validators (F5) ---------------------------------------------------------------------


class TestPluginPaths:
    def _exp(self, path: str) -> ExperimentConfig:
        runtime = {"type": "claude-sdk", "plugins": [{"type": "local", "path": path}]}
        return ExperimentConfig(
            name="e",
            probes=(Probe(id="p", prompt="q", metadata={"expected_skill": "x"}),),
            subjects=(Subject(name="live", config={"runtime": runtime}),),
        )

    def test_a_missing_plugin_path_is_a_problem(self, tmp_path: Path):
        problems = validate_experiment(self._exp("../plugins/gone"), experiment_cwd=str(tmp_path))
        joined = "\n".join(problems)
        assert "runtime.plugins[0].path '../plugins/gone' does not exist" in joined

    def test_an_existing_plugin_path_is_not(self, tmp_path: Path):
        (tmp_path / "plugins" / "here").mkdir(parents=True)
        exp_dir = tmp_path / "exp"
        exp_dir.mkdir()
        problems = validate_experiment(self._exp("../plugins/here"), experiment_cwd=str(exp_dir))
        assert not [p for p in problems if "plugins[" in p]


# --- token economy (F6) ------------------------------------------------------------------


class TestDetailsAreCapped:
    def test_distinct_details_capped_in_first_seen_order(self):
        texts = ["b", "a", "b", "c", "d", "a", "e"]
        readings = [
            Reading(sensor_name="s", probe_id="p", trial_index=i, passed=True, details=t)
            for i, t in enumerate(texts)
        ]
        [result] = aggregate_readings(readings, {})
        assert result.details == ("b", "a", "c")
        assert len(result.details) == MAX_DETAILS
        assert len(result.trial_scores) == len(texts)  # every trial still scored


# --- init (F7, F8) -----------------------------------------------------------------------


class TestInit:
    def test_init_reports_created_then_existed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        first = json.loads(_invoke("lab", "init", "lab", "--format", "json").stdout)
        again = json.loads(_invoke("lab", "init", "lab", "--format", "json").stdout)
        assert (first["created"], again["created"]) == (True, False)
        assert first["schema"] == "ix.v1/init"

        made = _invoke("experiment", "init", "e", "--lab", "lab", "--format", "json")
        assert made.exit_code == 0, made.output
        doc = json.loads(made.stdout)
        assert doc["created"] is True and "tasks/example.md" in doc["files"]
        repeat = json.loads(
            _invoke("experiment", "init", "e", "--lab", "lab", "--format", "json").stdout
        )
        assert repeat["created"] is False and repeat["files"] == []

    def test_the_scaffold_has_an_example_probe_and_validates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        _invoke("lab", "init", "lab")
        _invoke("experiment", "init", "e", "--lab", "lab")
        probe = (tmp_path / "lab" / "e" / "tasks" / "example.md").read_text()
        assert "expectation: must_trigger" in probe and "expected_skill:" in probe
        result = _invoke("experiment", "validate", "e", "--lab", "lab")
        assert result.exit_code == 0, result.output

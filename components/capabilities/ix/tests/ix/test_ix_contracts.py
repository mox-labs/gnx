"""The contracts the 2026-10 audit added to ix (research/drafts/api-design-canon/AUDIT-*.md).

Trials are persisted; results are per subject; uncertainty is reported two ways; two
subjects can be compared; the CLI keeps machine output clean; validate composes.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner
from matrix import AgentDefinition, AgentResponse, BoundAgent

from ix.adapters._in.cli import main
from ix.adapters._out.engines.native import NativeEngine, NativeEngineConfig
from ix.adapters._out.filesystem_store import FilesystemStore
from ix.composition import default_skill, validate_experiment
from ix.domain.errors import ResultsError
from ix.domain.ports import EngineRun
from ix.domain.types import Probe, Subject
from ix.eval.analysis import compare_results, standard_errors
from ix.eval.experiment import Experiment
from ix.eval.models import ExperimentConfig, ExperimentResults, ProbeResult
from ix.eval.sensors import ActivationSensor

if TYPE_CHECKING:
    from pathlib import Path

# --- statistics ------------------------------------------------------------------------


def _pr(pid: str, score: float, passed: bool | None = None) -> ProbeResult:
    return ProbeResult(probe_id=pid, score=score, passed=score >= 0.5 if passed is None else passed)


class TestStandardErrors:
    def test_clt_over_probes(self):
        results = [_pr("a", 1.0), _pr("b", 0.0), _pr("c", 1.0), _pr("d", 0.0)]
        pass_se, score_se = standard_errors(results)
        assert pass_se == pytest.approx((0.5 * 0.5 / 4) ** 0.5)
        assert score_se == pytest.approx(0.57735 / 2, rel=1e-3)  # sd([1,0,1,0]) / √4

    def test_undefined_below_two_probes(self):
        assert standard_errors([_pr("a", 1.0)]) == (None, None)


def _results(subject: str, scores: dict[str, float], noise: float | None = None):
    return ExperimentResults(
        experiment_name="e",
        subject=subject,
        probe_results=tuple(_pr(p, s) for p, s in scores.items()),
        score_noise_floor_sd=noise,
    )


class TestCompare:
    PROBES = [f"p{i}" for i in range(20)]

    def test_a_consistent_gain_is_b_better(self):
        a = _results("a", {p: 0.2 + 0.01 * i for i, p in enumerate(self.PROBES)})
        b = _results("b", {p: 0.8 + 0.01 * i for i, p in enumerate(self.PROBES)})
        c = compare_results(a, b)
        assert c.mean_delta == pytest.approx(0.6)
        assert c.verdict == "b_better"
        assert c.b_only_passed == 20 and c.a_only_passed == 0

    def test_noise_cancels_out_to_inconclusive(self):
        a = _results("a", {p: 0.5 for p in self.PROBES})
        b = _results("b", {p: 0.5 + (0.1 if i % 2 else -0.1) for i, p in enumerate(self.PROBES)})
        assert compare_results(a, b).verdict == "inconclusive"

    def test_a_gap_inside_the_noise_floor_is_inconclusive_even_when_the_ci_excludes_zero(self):
        a = _results("a", {p: 0.40 for p in self.PROBES}, noise=0.20)
        b = _results("b", {p: 0.45 + 0.001 * i for i, p in enumerate(self.PROBES)}, noise=0.05)
        c = compare_results(a, b)
        assert c.ci95 is not None and c.ci95[0] > 0  # the probes say B is ahead...
        assert c.noise_floor_sd == 0.20  # ...by less than a rerun of A moves by itself
        assert c.verdict == "inconclusive"

    def test_the_same_gap_clears_a_small_noise_floor(self):
        a = _results("a", {p: 0.40 for p in self.PROBES}, noise=0.01)
        b = _results("b", {p: 0.45 + 0.001 * i for i, p in enumerate(self.PROBES)}, noise=0.01)
        assert compare_results(a, b).verdict == "b_better"

    def test_unmatched_probes_are_reported_not_silently_dropped(self):
        a = _results("a", {"x": 1.0, "y": 0.0, "only-a": 1.0})
        b = _results("b", {"x": 1.0, "y": 1.0, "only-b": 0.0})
        assert compare_results(a, b).unmatched == ("only-a", "only-b")

    def test_different_experiments_refuse(self):
        a = _results("a", {"x": 1.0})
        b = _results("b", {"x": 1.0}).model_copy(update={"experiment_name": "other"})
        with pytest.raises(ResultsError, match="different experiments"):
            compare_results(a, b)


# --- persistence -----------------------------------------------------------------------


class _Echo:
    async def run(self, definition, task):
        return AgentResponse(content=f"echo {task}", family="test", model="m")


def _probes(n: int) -> tuple[Probe, ...]:
    return tuple(Probe(id=f"p{i}", prompt=f"q{i}") for i in range(n))


class TestTrialsArePersisted:
    async def test_every_trial_of_every_repeat_is_a_record(self, tmp_path: Path):
        exp = ExperimentConfig(name="e", probes=_probes(3), trials=2, repeats=2)
        service = Experiment(
            sensor=ActivationSensor(expected_skill="x"),
            store=FilesystemStore(tmp_path),
            engine=NativeEngine(),
            agents=lambda s, t, r=0: BoundAgent(AgentDefinition(name="a"), _Echo()),
        )
        results = await service.run(exp, Subject(name="baseline"))

        log = tmp_path / "e" / results.trials_log
        records = [json.loads(line) for line in log.read_text().splitlines()]
        assert len(records) == 3 * 2 * 2
        assert {r["run_index"] for r in records} == {0, 1}
        assert records[0]["response"]["content"] == "echo q0"
        assert records[0]["response"]["family"] == "test"
        assert records[0]["readings"][0]["probe_id"] == "p0"
        assert results.subject == "baseline" and results.run_id in results.trials_log


class TestNativeConcurrency:
    async def test_order_is_probe_by_trial_at_any_concurrency(self):
        run = EngineRun(
            experiment="e",
            probes=_probes(4),
            subject=Subject(name="s"),
            sensor=ActivationSensor(expected_skill="x"),
            agents=lambda s, t, r=0: BoundAgent(AgentDefinition(name="a"), _Echo()),
            trials=3,
        )
        serial = await NativeEngine().run(run)
        parallel = await NativeEngine(NativeEngineConfig(concurrency=8)).run(run)
        key = [(t.probe_id, t.trial_index) for t in serial.trials]
        assert key == [(t.probe_id, t.trial_index) for t in parallel.trials]
        assert [(r.probe_id, r.trial_index) for r in serial.readings] == [
            (r.probe_id, r.trial_index) for r in parallel.readings
        ]


# --- config ----------------------------------------------------------------------------


class TestExperimentConfig:
    def test_sensor_and_sensors_together_is_an_error(self):
        with pytest.raises(ValueError, match="either 'sensor' or 'sensors'"):
            ExperimentConfig(name="e", sensor="activation", sensors=({"type": "outcome"},))

    def test_unknown_field_is_an_error(self):
        with pytest.raises(ValueError, match="agent"):
            ExperimentConfig(name="e", agent={})

    def test_default_skill_reads_the_sensors_list(self):
        """Regression: the simulator's skill was read from the singular `sensor` field only,
        so a `sensors:` experiment silently fell back to a hardcoded 'build-eval'."""
        exp = ExperimentConfig(
            name="e",
            sensors=({"type": "function-test"}, {"type": "activation", "expected_skill": "dao"}),
        )
        assert default_skill(exp) == "dao"
        assert default_skill(ExperimentConfig(name="e")) is None


class TestValidate:
    def test_reports_every_problem_without_running(self):
        exp = ExperimentConfig(
            name="e",
            probes=_probes(1),
            engine={"type": "ray"},
            sensors=({"type": "activation", "bogus": 1},),
            subjects=(
                Subject(name="ok", config={"runtime": {"type": "simulated"}}),
                Subject(name="typo", config={"runtime": {"type": "claude-sdk", "cdw": "/"}}),
                Subject(name="none", config={}),
            ),
        )
        problems = validate_experiment(exp)
        joined = "\n".join(problems)
        assert len(problems) == 4, joined
        assert "engine: Unknown engine: 'ray'" in joined
        assert "sensors:" in joined and "bogus" in joined
        assert "subject 'typo'" in joined and "cdw" in joined
        assert "subject 'none'" in joined and "no runtime.type" in joined

    def test_a_valid_experiment_has_no_problems(self):
        exp = ExperimentConfig(
            name="e",
            probes=_probes(1),
            subjects=(Subject(name="s", config={"runtime": {"type": "simulated"}}),),
        )
        assert validate_experiment(exp) == []


# --- CLI -------------------------------------------------------------------------------


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    exp = tmp_path / "lab" / "e"
    (exp / "tasks").mkdir(parents=True)
    (exp / "experiment.yaml").write_text(
        "name: e\nsensor: {type: activation, expected_skill: x}\ntrials: 1\n"
        "subjects:\n"
        "  - name: a\n    config: {runtime: {type: simulated}}\n"
        "  - name: b\n    config: {runtime: {type: simulated}}\n"
    )
    for i in range(3):
        (exp / "tasks" / f"p{i}.md").write_text(
            f"---\nid: p{i}\nexpectation: must_trigger\n---\nprompt {i}\n"
        )
    return tmp_path / "lab"


class TestCli:
    def test_run_without_subject_runs_every_subject_and_saves_each(self, lab: Path):
        result = CliRunner().invoke(main, ["run", "e", "--lab", "lab", "--seed", "1"])
        assert result.exit_code == 0, result.output
        assert FilesystemStore(lab).subjects_with_results("e") == ["a", "b"]

    def test_json_output_is_clean_stdout(self, lab: Path):
        result = CliRunner().invoke(
            main, ["run", "e", "--lab", "lab", "--subject", "a", "--format", "json"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)  # banners went to stderr
        assert payload["subject"] == "a"
        assert "Running" in result.stderr

    def test_compare_two_subjects(self, lab: Path):
        runner = CliRunner()
        assert runner.invoke(main, ["run", "e", "--lab", "lab", "--seed", "1"]).exit_code == 0
        result = runner.invoke(main, ["compare", "e", "a", "b", "--lab", "lab", "--format", "json"])
        assert result.exit_code == 0, result.output
        comparison = json.loads(result.stdout)
        assert (comparison["a"], comparison["b"], comparison["n"]) == ("a", "b", 3)
        assert comparison["verdict"] in {"a_better", "b_better", "inconclusive"}

    def test_mock_flag_still_works_with_a_deprecation_notice(self, lab: Path):
        result = CliRunner().invoke(main, ["run", "e", "--lab", "lab", "--subject", "a", "--mock"])
        assert result.exit_code == 0, result.output
        assert "--mock is deprecated; use --simulate" in result.stderr

    def test_validate_fails_with_exit_1_and_lists_problems(self, lab: Path):
        (lab / "e" / "experiment.yaml").write_text(
            "name: e\nsubjects:\n  - name: a\n    config: {runtime: {type: strands}}\n"
        )
        result = CliRunner().invoke(main, ["experiment", "validate", "e", "--lab", "lab"])
        assert result.exit_code == 1
        assert "runtime.type 'strands' is not registered" in result.stderr

    def test_unknown_subject_names_the_known_ones(self, lab: Path):
        result = CliRunner().invoke(main, ["run", "e", "--lab", "lab", "--subject", "zz"])
        assert result.exit_code == 1
        assert "Subjects: a, b" in result.stderr


class TestProvenance:
    def test_families_come_from_the_responses(self):
        live = _results("live", {"x": 1.0}).model_copy(update={"families": ("claude",)})
        sim = _results("live", {"x": 1.0}).model_copy(update={"families": ("simulated",)})
        assert live.measured_a_model and not sim.measured_a_model

    def test_compare_warns_when_a_side_measured_no_model(self):
        a = _results("a", {"x": 1.0, "y": 0.0}).model_copy(update={"families": ("simulated",)})
        b = _results("b", {"x": 1.0, "y": 1.0}).model_copy(update={"families": ("qwen",)})
        warning = compare_results(a, b).warning
        assert warning is not None and "'a'" in warning and "harness" in warning

    def test_simulated_cli_run_records_simulated(self, lab: Path):
        result = CliRunner().invoke(
            main, ["run", "e", "--lab", "lab", "--subject", "a", "--format", "json"]
        )
        assert json.loads(result.stdout)["families"] == ["simulated"]


def test_inspect_engine_json_output_is_clean_stdout(lab: Path):
    """Regression: Inspect's progress display wrote to stdout and broke --format json."""
    pytest.importorskip("inspect_ai")
    result = CliRunner().invoke(
        main,
        ["run", "e", "--lab", "lab", "--subject", "a", "--engine", "inspect", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["engine"] == "inspect"


# --- the run as it happens (the cix experience, kept) ------------------------------------


class TestProgress:
    async def test_every_trial_of_every_repeat_reports_as_it_is_measured(self, tmp_path: Path):
        exp = ExperimentConfig(name="e", probes=_probes(2), trials=3, repeats=2)
        service = Experiment(
            sensor=ActivationSensor(expected_skill="x"),
            store=FilesystemStore(tmp_path),
            engine=NativeEngine(),
            agents=lambda s, t, r=0: BoundAgent(AgentDefinition(name="a"), _Echo()),
        )
        seen: list[tuple[int, str, int]] = []
        await service.run(
            exp,
            on_trial=lambda rep, trial, readings: seen.append((rep, trial.probe_id, len(readings))),
        )
        assert len(seen) == 2 * 3 * 2
        assert {rep for rep, _, _ in seen} == {0, 1}
        assert all(n >= 1 for _, _, n in seen)

    async def test_probe_lines_print_with_repeats_too(self, tmp_path: Path):
        """cix printed per-probe PASS/FAIL only when repeats == 1; a repeated run said nothing."""
        exp = ExperimentConfig(name="e", probes=_probes(3), trials=1, repeats=2)
        service = Experiment(
            sensor=ActivationSensor(expected_skill="x"),
            store=FilesystemStore(tmp_path),
            engine=NativeEngine(),
            agents=lambda s, t, r=0: BoundAgent(AgentDefinition(name="a"), _Echo()),
        )
        completed: list[ProbeResult] = []
        await service.run(exp, on_probe_complete=completed.append)
        assert [p.probe_id for p in completed] == ["p0", "p1", "p2"]
        assert all(len(p.trial_scores) == 2 for p in completed)  # across both repeats

    def test_cli_prints_a_verdict_line_per_probe(self, lab: Path):
        result = CliRunner().invoke(
            main, ["run", "e", "--lab", "lab", "--subject", "a", "--repeats", "2", "--seed", "1"]
        )
        assert result.exit_code == 0, result.output
        for i in range(3):
            assert f"p{i}: " in result.stderr
        assert "repeat 2/2:" in result.stderr

    def test_compare_header_reads_in_argument_order(self, lab: Path):
        runner = CliRunner()
        assert runner.invoke(main, ["run", "e", "--lab", "lab", "--seed", "1"]).exit_code == 0
        result = runner.invoke(main, ["compare", "e", "a", "b", "--lab", "lab"])
        assert result.exit_code == 0, result.output
        assert "a → b" in result.stdout


class TestCixConfigKeys:
    @pytest.mark.parametrize(
        ("key", "hint"), [("agent", "belong to a subject"), ("skill", "expected_skill")]
    )
    def test_a_removed_key_says_where_it_moved(self, lab: Path, key: str, hint: str):
        path = lab / "e" / "experiment.yaml"
        path.write_text(path.read_text() + f"{key}: anything\n")
        result = CliRunner().invoke(main, ["experiment", "validate", "e", "--lab", "lab"])
        assert result.exit_code == 1
        assert hint in " ".join(result.stderr.split())  # rich wraps at terminal width

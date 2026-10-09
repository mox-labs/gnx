"""Tests for FilesystemStore — YAML+MD loading, JSONL persistence.

Workspace = lab directory. Experiments are direct children.
"""

import json
from pathlib import Path

import pytest

from ix.adapters._out.filesystem_store import FilesystemStore
from ix.domain.errors import ConfigError, ResultsError
from ix.domain.models import (
    ExperimentResults,
    TrialRecord,
)
from ix.domain.types import Reading


@pytest.fixture
def lab(tmp_path: Path) -> Path:
    """Lab directory (the workspace)."""
    return tmp_path


@pytest.fixture
def store(lab: Path) -> FilesystemStore:
    return FilesystemStore(lab)


@pytest.fixture
def sample_experiment(lab: Path) -> Path:
    """Create a minimal experiment directly in the lab."""
    exp_dir = lab / "test-exp"
    exp_dir.mkdir()

    (exp_dir / "experiment.yaml").write_text(
        "name: test-exp\n"
        "description: A test experiment\n"
        "subjects:\n"
        "  - name: build-eval\n"
        "trials: 3\n"
    )

    cases_dir = exp_dir / "cases"
    cases_dir.mkdir()

    (cases_dir / "must-001.md").write_text(
        "---\nid: must-001\nexpectation: must_trigger\n---\nHow do I write evals?\n"
    )

    (cases_dir / "not-001.md").write_text(
        "---\n"
        "id: not-001\n"
        "expectation: should_not_trigger\n"
        "---\n"
        "Write a Python function to sort a list\n"
    )

    return exp_dir


class TestLoadExperiment:
    def test_loads_config(self, store: FilesystemStore, sample_experiment: Path):
        exp = store.load_experiment(sample_experiment)
        assert exp.name == "test-exp"
        assert exp.subjects[0].name == "build-eval"
        assert exp.trials == 3

    def test_loads_probes(self, store: FilesystemStore, sample_experiment: Path):
        exp = store.load_experiment(sample_experiment)
        assert len(exp.probes) == 2
        assert exp.probes[0].id == "must-001"
        assert exp.probes[0].metadata["expectation"] == "must_trigger"
        assert "evals" in exp.probes[0].prompt

    def test_probes_sorted_by_filename(self, store: FilesystemStore, sample_experiment: Path):
        exp = store.load_experiment(sample_experiment)
        assert exp.probes[0].id == "must-001"
        assert exp.probes[1].id == "not-001"

    def test_missing_config_is_a_config_error_naming_the_directory(
        self, store: FilesystemStore, lab: Path
    ):
        with pytest.raises(ConfigError, match="no experiment.yaml in .*nonexistent"):
            store.load_experiment(lab / "nonexistent")


class TestListExperiments:
    def test_finds_experiments(self, store: FilesystemStore, sample_experiment: Path, lab: Path):
        experiments = store.list_experiments(lab)
        assert len(experiments) == 1
        assert experiments[0].name == "test-exp"

    def test_empty_lab(self, store: FilesystemStore, lab: Path):
        experiments = store.list_experiments(lab)
        assert experiments == []


def _record(probe: str, trial: int, run_id: str = "r1") -> TrialRecord:
    return TrialRecord(
        run_id=run_id,
        run_index=0,
        probe_id=probe,
        trial_index=trial,
        response={"content": f"resp-{trial}"},
        readings=(
            Reading(sensor_name="activation", probe_id=probe, trial_index=trial, passed=True),
        ),
    )


class TestAppendTrials:
    def test_writes_one_line_per_trial_under_subject_and_run(
        self, store: FilesystemStore, lab: Path
    ):
        rel = store.append_trials("test-exp", "baseline", "r1", [_record("must-001", 0)])
        assert rel == Path("results/baseline/r1/trials.jsonl")
        line = json.loads((lab / "test-exp" / rel).read_text().strip())
        assert line["probe_id"] == "must-001"
        assert line["readings"][0]["passed"] is True

    def test_appends_across_repeats(self, store: FilesystemStore, lab: Path):
        for i in range(3):
            store.append_trials("test-exp", "baseline", "r1", [_record("must-001", i)])
        log = lab / "test-exp" / "results" / "baseline" / "r1" / "trials.jsonl"
        assert len(log.read_text().strip().split("\n")) == 3

    def test_subject_names_become_safe_directory_names(self, store: FilesystemStore, lab: Path):
        rel = store.append_trials("test-exp", "claude / live!", "r1", [_record("p", 0)])
        assert rel.parts[1] == "claude---live"


class TestSaveSummary:
    def test_creates_summary(self, store: FilesystemStore, lab: Path):
        results = ExperimentResults(
            experiment_name="test-exp",
            pass_rate=0.95,
            mean_score=0.90,
            min_score=0.8,
            max_score=1.0,
        )
        path = store.save_summary("test-exp", results)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["pass_rate"] == 0.95
        assert data["mean_score"] == 0.90

    def test_latest_is_per_subject_so_one_subject_never_overwrites_another(
        self, store: FilesystemStore, lab: Path
    ):
        store.save_summary("test-exp", ExperimentResults(experiment_name="test-exp", subject="a"))
        store.save_summary(
            "test-exp", ExperimentResults(experiment_name="test-exp", subject="b", pass_rate=0.5)
        )
        assert store.load_summary("test-exp", "a").subject == "a"
        assert store.load_summary("test-exp", "b").pass_rate == 0.5
        assert store.subjects_with_results("test-exp") == ["a", "b"]

    def test_missing_subject_results_name_what_exists(self, store: FilesystemStore):
        store.save_summary("test-exp", ExperimentResults(experiment_name="test-exp", subject="a"))
        with pytest.raises(ResultsError, match="Subjects with results: a"):
            store.load_summary("test-exp", "zzz")

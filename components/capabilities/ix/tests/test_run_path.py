"""The run path, end to end through matrix, with test-only adapters.

Everything between ``Root.run`` and the record is the real thing: the use cases, the three
matrix components, matrix's executor and its JSONL store. Only the adapters are fakes
(``ix_fakes``): they start nothing and call nothing, so these tests need no load tool and
no network.

Two tests run a run in another process, because what they check is what a signal or a kill
leaves behind.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import pytest
import yaml
from ix_fakes import LOG, FakeLoad, FakeSubject, FakeTiming, experiment, fake_registry
from matrix import (
    Config,
    Extension,
    JsonlConstructStore,
    MatrixConfig,
    default_registry,
    load_config,
)
from matrix.adapters._out.stores.jsonl import JsonlStoreConfig

import ix
from ix.adapters._in.matrix_components import (
    COMPOSITION,
    ENVIRONMENT,
    NEED,
    SENSE,
    TRIALS,
)
from ix.adapters._out import matrix_errors, matrix_runs
from ix.adapters._out.environments import NoEnvironment
from ix.adapters._out.matrix_process import MatrixLauncher
from ix.adapters._out.matrix_runs import Runs as RunRecords
from ix.adapters._out.subjects import ProcessSubject
from ix.application import running
from ix.composition import RUNS_DIRECTORY, Root
from ix.composition.extension import POINTS, register_run_path
from ix.composition.settings import IxSettings
from ix.domain import schema
from ix.domain.kind import Metric
from ix.domain.ports import Liveness, Preflight, Sensed
from ix.domain.report import report as build_report
from ix.domain.run import CALIBRATION, Failure, HarnessError, Interaction, Reading, Target

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ix.domain.experiment import Shape

Write = Any


@pytest.fixture
def root() -> Root:
    LOG.clear()
    return Root(registry=fake_registry())


def _rows(location: str, kind: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(location, f"{kind}.jsonl").read_text().splitlines()]


class TestAFullRun:
    def test_a_registered_run_records_every_table_and_gives_a_verdict(
        self, root: Root, write_experiment: Write
    ) -> None:
        file = write_experiment(experiment(variant_ms=55.0))
        root.register(file)
        events: list[str] = []
        report = root.run(file, on_event=lambda name, fields: events.append(name))

        assert report.status == "completed" and report.gated and report.readings == "recorded"
        assert report.trials.planned == report.trials.carried_out == 15
        assert report.verdict == "fails" and report.indication is None and report.exit_code == 10
        assert report.decides == ("candidate is better than baseline on latency p50",)
        assert Path(report.location).parent == file.parent / RUNS_DIRECTORY

        # One matrix run: the manifest and schedule supplied, three tables provided.
        assert len(_rows(report.location, schema.RUN)) == 1
        assert len(_rows(report.location, schema.TRIAL)) == 15
        assert len(_rows(report.location, schema.BINDINGS)) == 1
        assert len(_rows(report.location, schema.INTERACTION)) == 15
        assert len(_rows(report.location, schema.READING)) == 15
        described = json.loads(Path(report.location, "run.json").read_text())
        assert described["status"] == "completed"
        assert {name: c["status"] for name, c in described["components"].items()} == {
            "environment": "completed",
            "trials": "completed",
            "sense": "completed",
        }
        assert events.count("ix.trial.end") == 15 and "ix.environment.up" in events

    def test_identical_subjects_are_inconclusive_and_the_exit_code_says_so(
        self, root: Root, write_experiment: Write
    ) -> None:
        file = write_experiment(experiment(variant_ms=50.0))
        root.register(file)
        report = root.run(file)
        assert report.verdict == "inconclusive" and report.exit_code == 11
        (check,) = report.analysis.calibration
        assert check.stable is True and check.interval is not None

    def test_the_run_carries_out_the_plan_it_was_registered_with(
        self, root: Root, write_experiment: Write
    ) -> None:
        file = write_experiment(experiment())
        plan = root.plan(file)
        root.register(file)
        report = root.run(file)
        (manifest,) = _rows(report.location, schema.RUN)
        assert manifest["plan_digest"] == plan.digest == report.plan_digest
        assert manifest["schedule_digest"] == plan.schedule_digest
        assert manifest["registration"]["plan_digest"] == plan.digest
        trials = _rows(report.location, schema.TRIAL)
        interactions = _rows(report.location, schema.INTERACTION)
        # One trial at a time, in schedule order, each subject reached and released.
        assert [row["seq"] for row in interactions] == [row["seq"] for row in trials]
        expected: list[str] = []
        for trial in trials:
            name = trial["subject"]
            expected += [f"reach {name}", f"apply {name}", f"release {name}"]
        assert expected == LOG
        assert sum(1 for trial in trials if trial["role"] == CALIBRATION) == 5

    def test_the_report_can_be_made_again_from_the_record_alone(
        self, root: Root, write_experiment: Write
    ) -> None:
        file = write_experiment(experiment(variant_ms=55.0))
        root.register(file)
        report = root.run(file)
        assert Root(registry=fake_registry()).report(report.location) == report
        assert root.report(file) == report  # an experiment means its latest run
        listed = root.runs(file)
        assert [(r.run, r.status, r.carried_out, r.gated) for r in listed.runs] == [
            (report.run, "completed", 15, True)
        ]

    def test_a_run_still_being_written_has_no_verdict_and_an_exit_code_of_its_own(
        self, root: Root, write_experiment: Write
    ) -> None:
        file = write_experiment(experiment(variant_ms=55.0))
        root.register(file)
        finished = root.run(file)
        location = Path(finished.location)
        record = RunRecords(str(location.parent), JsonlConstructStore(location.parent)).record(
            location.name
        )
        live = build_report(dataclasses.replace(record, status="running"))
        assert live.status == "running" and live.verdict is None
        assert live.no_verdict == "the run has not finished: 15 of 15 carried out so far"
        assert live.exit_code == 13 != finished.exit_code


class _RecordingStore(JsonlConstructStore):
    """matrix's JSONL store under another type URL, noting each directory it is built for."""

    built: ClassVar[list[str]] = []

    @classmethod
    def from_config(cls, config: JsonlStoreConfig) -> JsonlConstructStore:
        cls.built.append(config.root)
        return cls(config.root)


class TestTheRunStore:
    def test_is_chosen_by_the_root_at_matrixs_store_point(self, write_experiment: Write) -> None:
        """No adapter names a store: the root builds the one it chose through the registry,
        for running, reporting and listing alike."""
        registry = fake_registry()
        url = "acme.v1.store.recording"
        registry.register("store", url, _RecordingStore.from_config, config=JsonlStoreConfig)
        _RecordingStore.built.clear()
        root = Root(registry=registry, run_store=url)
        file = write_experiment(experiment())
        report = root.run(file, exploratory=True)
        kept = str(file.parent.resolve() / RUNS_DIRECTORY)
        assert report.location == f"{kept}/{report.run}"
        assert root.report(report.location).run == report.run
        assert [r.run for r in root.runs(file).runs] == [report.run]
        assert set(_RecordingStore.built) == {kept}

    def test_a_store_that_cannot_keep_runs_in_a_directory_is_a_config_error(
        self, write_experiment: Write
    ) -> None:
        root = Root(registry=fake_registry(), run_store="matrix.v1.store.memory")
        with pytest.raises(ix.ConfigError, match="memory"):
            root.run(write_experiment(experiment()), exploratory=True)


class TestAProcessSubjectsProgram:
    def test_the_files_its_command_names_are_digested_and_an_edit_is_a_deviation(
        self, write_experiment: Write
    ) -> None:
        """Regression: a process subject's command named its program and script by path,
        and nothing digested them, so a registered run of an edited server was gated. Each
        item of ``command`` that names a file, relative to ``cwd``, is now part of what was
        tested; a program found on PATH and a plain argument are not files of the
        experiment."""
        data = experiment()
        for subject in data["subjects"].values():
            subject.update(command=["python3", "server.py", "--port", "1"], cwd="app")
        file = write_experiment(data)
        (file.parent / "app").mkdir()
        (file.parent / "app" / "server.py").write_text("print('v1')\n")
        root = Root(registry=fake_registry())
        root.register(file)
        report = root.run(file)
        assert report.gated
        named = {(c.entry, c.option, c.path, c.label) for c in report.contents}
        assert named == {
            (f"subjects.{name}", "command", "app/server.py", "file server.py")
            for name in ("baseline", "candidate")
        }

        (file.parent / "app" / "server.py").write_text("print('v2')\n")
        changed = root.deviations(file).deviations
        assert {d.field for d in changed} >= {
            "subjects.baseline.command[app/server.py]",
            "subjects.candidate.command[app/server.py]",
        }
        assert not root.run(file).gated


class _CountingStore(JsonlConstructStore):
    """matrix's JSONL store, counting the runs it loads."""

    loaded: ClassVar[list[str]] = []

    def load(self, run_id: str) -> Any:
        _CountingStore.loaded.append(run_id)
        return super().load(run_id)

    @classmethod
    def from_config(cls, config: JsonlStoreConfig) -> JsonlConstructStore:
        return cls(config.root)


class TestListingRuns:
    def test_each_run_is_read_once(self, write_experiment: Write) -> None:
        """Regression: listing read every record in full to summarize it, then again to
        report it; a plan and a report of an experiment did the same to find its runs."""
        registry = fake_registry()
        url = "acme.v1.store.counting"
        registry.register("store", url, _CountingStore.from_config, config=JsonlStoreConfig)
        root = Root(registry=registry, run_store=url)
        file = write_experiment(experiment())
        runs = [root.run(file, exploratory=True).run for _ in range(2)]
        _CountingStore.loaded.clear()
        assert sorted(r.run for r in root.runs(file).runs) == sorted(runs)
        assert sorted(_CountingStore.loaded) == sorted(runs)
        _CountingStore.loaded.clear()
        latest = root.report(file).run
        assert _CountingStore.loaded == [latest]

    def test_with_no_path_every_experiments_runs_under_the_directory(
        self, write_experiment: Write, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: with no path ``ix runs`` read only ``./.ix/runs``, though its help
        says every experiment's runs under the current directory: at a repository's root
        after ``ix run lab/static`` it said there were none."""
        root = Root(registry=fake_registry())
        one = write_experiment(experiment(), "lab/one")
        two = write_experiment(experiment(name="other"), "lab/two")
        ran = [root.run(file, exploratory=True).run for file in (one, two)]
        monkeypatch.chdir(one.parents[1])
        listed = root.runs()
        assert sorted(r.run for r in listed.runs) == sorted(ran)
        assert listed.location == str(one.parents[1].resolve())


class TestWhereEachSettingCameFrom:
    def test_the_run_records_the_tier_that_set_each_setting_it_read(
        self, tmp_path: Path, write_experiment: Write
    ) -> None:
        """A setting the run read is recorded with its rung: the file that set it, or the
        default when no tier did. Never its value, which may be a model registry."""
        kept = tmp_path / "runs"
        yours = tmp_path / "yours.yaml"
        yours.write_text(yaml.safe_dump({"ix": {"runs": str(kept)}}))
        project = tmp_path / "project.yaml"
        project.write_text(yaml.safe_dump({"matrix": {"observers": []}}))
        settings = load_config(client_type=IxSettings, client_key="ix", sources=[yours, project])
        report = Root(registry=fake_registry(), settings=settings).run(
            write_experiment(experiment()), exploratory=True
        )
        assert report.location.startswith(str(kept))
        assert report.provenance is not None
        assert report.provenance.settings == {
            "ix.runs": str(yours),
            "matrix.observers": str(project),
            "matrix.models": "default",
        }

    def test_settings_given_in_code_say_so(self, write_experiment: Write) -> None:
        report = Root(registry=fake_registry(), settings=Config[IxSettings](client=IxSettings()))
        ran = report.run(write_experiment(experiment()), exploratory=True)
        assert ran.provenance is not None
        assert set(ran.provenance.settings.values()) == {"given in code"}


class TestRegistrationAtRun:
    def test_an_unregistered_experiment_is_refused_before_anything_starts(
        self, root: Root, write_experiment: Write
    ) -> None:
        file = write_experiment(experiment())
        with pytest.raises(ix.UnregisteredError) as refused:
            root.run(file)
        assert refused.value.exit_code == 15 and "ix register" in (refused.value.fix or "")
        assert LOG == [] and not (file.parent / RUNS_DIRECTORY).exists()

    def test_an_exploratory_run_gives_no_verdict_whatever_it_found(
        self, root: Root, write_experiment: Write
    ) -> None:
        file = write_experiment(experiment(variant_ms=55.0))
        report = root.run(file, exploratory=True)
        assert report.status == "completed" and not report.gated
        assert report.verdict is None and report.indication == "fails"
        assert "exploratory" in report.no_verdict and report.exit_code == 0

    def test_exploratory_stays_exploratory_even_when_a_registration_exists(
        self, root: Root, write_experiment: Write
    ) -> None:
        file = write_experiment(experiment(variant_ms=55.0))
        root.register(file)
        report = root.run(file, exploratory=True)
        assert report.verdict is None and not report.gated and report.registered_at

    def test_a_changed_experiment_runs_ungated_until_its_registration_is_amended(
        self, root: Root, write_experiment: Write
    ) -> None:
        """Changed requirement: a registered experiment that changed since still gave a
        gated verdict, so moving the margin (or adding repeats) and running again could pass
        a gate. Now it runs ungated: what it found is an indication, the deviations are in
        the report, and it exits ``deviations``, never 0, until the registration is amended
        with a reason."""
        file = write_experiment(experiment(variant_ms=40.0))
        root.register(file)
        write_experiment(experiment(variant_ms=40.0, repeats=6))
        report = root.run(file)
        assert report.status == "completed" and not report.gated and not report.exploratory
        assert report.verdict is None and report.indication == "holds"
        assert "changed since its registration" in report.no_verdict
        fields = {deviation.field for deviation in report.deviations}
        assert "design.repeats" in fields and report.trials.planned == 18
        assert report.exit_code == ix.EXIT_CODES["deviations"] == 14
        (manifest,) = _rows(report.location, schema.RUN)
        assert manifest["gated"] is False and manifest["deviations"]

        root.register(file, amend="six repeats, decided before this run was read")
        amended = root.run(file)
        assert amended.gated and amended.deviations == () and amended.verdict == "holds"
        assert amended.exit_code == 0

    def test_an_exploratory_run_of_a_changed_experiment_stays_exploratory(
        self, root: Root, write_experiment: Write
    ) -> None:
        file = write_experiment(experiment())
        root.register(file)
        write_experiment(experiment(repeats=6))
        report = root.run(file, exploratory=True)
        assert report.exploratory and "exploratory" in report.no_verdict
        assert report.deviations and report.exit_code == 0

    def test_an_invalid_experiment_is_refused_with_its_problems(
        self, root: Root, write_experiment: Write
    ) -> None:
        data = experiment()
        del data["comparison"]
        with pytest.raises(ix.InvalidExperimentError) as refused:
            root.run(write_experiment(data), exploratory=True)
        assert [problem.kind for problem in refused.value.problems] == ["must_set"]

    def test_the_observers_matrix_config_names_for_ix_watch_the_run(
        self,
        root: Root,
        write_experiment: Write,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        events = tmp_path / "events.jsonl"
        config = tmp_path / "ix-config.yaml"
        config.write_text(
            yaml.safe_dump({"matrix": {"observers": [{"type": "jsonl", "path": str(events)}]}})
        )
        monkeypatch.setenv("IX_CONFIG", str(config))
        root.run(write_experiment(experiment()), exploratory=True)
        names = {json.loads(line)["name"] for line in events.read_text().splitlines()}
        assert {"ix.trial.start", "ix.trial.end", "ix.sense.end"} <= names


class TestWhoseFailure:
    def test_subject_harness_and_sensor_failures_are_told_apart(
        self, root: Root, write_experiment: Write
    ) -> None:
        """The candidate dies in its first trial, the load tool breaks in the baseline's
        first, and one reading of the baseline cannot be taken. None of it stops the run."""
        data = experiment()
        file = write_experiment(data)
        root.register(file)
        plan_trials = _trial_positions(root, file)
        data["subjects"]["candidate"]["env"] |= {
            "FAIL": "subject",
            "FAIL_SEQS": str(plan_trials["candidate", "variant"][0]),
        }
        write_experiment(data)
        report = root.run(file, exploratory=True)

        assert report.status == "completed" and report.trials.carried_out == 15
        assert report.trials.subject_failures == 1 and report.trials.harness_failures == 0
        failed = [row for row in _rows(report.location, schema.INTERACTION) if row["failure"]]
        assert [
            (row["subject"], row["failure"]["owner"], row["failure"]["reason"]) for row in failed
        ] == [("candidate", "subject", "exited")]
        assert failed[0]["observations"] == {}
        # A failed trial is a row with no reading; the subject's failure counts against it.
        assert len(_rows(report.location, schema.READING)) == 14
        assert report.indication == "fails"
        assert report.analysis.findings[0].reason == "the subject failed 1 of 5 trial(s)"

    def test_a_harness_failure_is_missing_data_not_the_subjects_fault(
        self, root: Root, write_experiment: Write
    ) -> None:
        data = experiment()
        file = write_experiment(data)
        positions = _trial_positions(root, file)
        data["subjects"]["candidate"]["env"] |= {
            "FAIL": "harness",
            "FAIL_SEQS": str(positions["candidate", "variant"][0]),
        }
        write_experiment(data)
        report = root.run(file, exploratory=True)
        assert report.trials.harness_failures == 1 and report.trials.subject_failures == 0
        assert [(c.owner, c.reason, c.count) for c in report.analysis.failures] == [
            ("harness", "tool_failed", 1)
        ]
        assert report.indication == "inconclusive"  # identical subjects, one block short
        assert report.analysis.findings[0].decisions[0].n == 4

    def test_a_sensor_failure_is_a_reading_with_a_failure(
        self, root: Root, write_experiment: Write
    ) -> None:
        data = experiment()
        file = write_experiment(data)
        positions = _trial_positions(root, file)
        data["subjects"]["candidate"]["env"] |= {
            "FAIL": "sensor",
            "FAIL_SEQS": str(positions["candidate", "variant"][1]),
        }
        write_experiment(data)
        report = root.run(file, exploratory=True)
        readings = _rows(report.location, schema.READING)
        broken = [row for row in readings if row["failure"]]
        assert len(readings) == 15 and len(broken) == 1
        assert broken[0]["failure"]["owner"] == "sensor" and broken[0]["value"] is None
        assert report.trials.subject_failures == report.trials.harness_failures == 0
        assert [(c.owner, c.count) for c in report.analysis.failures] == [("sensor", 1)]

    def test_a_subject_that_dies_during_the_probe_is_the_subjects_failure(
        self, root: Root, write_experiment: Write
    ) -> None:
        """The probe gets no answer, which alone would be the harness's; releasing the
        subject finds it dead, and that is the cause."""
        data = experiment()
        file = write_experiment(data)
        positions = _trial_positions(root, file)
        data["subjects"]["candidate"]["env"] |= {
            "FAIL": "dies",
            "FAIL_SEQS": str(positions["candidate", "variant"][0]),
        }
        write_experiment(data)
        report = root.run(file, exploratory=True)
        failed = [row for row in _rows(report.location, schema.INTERACTION) if row["failure"]]
        assert [
            (row["subject"], row["failure"]["owner"], row["failure"]["reason"]) for row in failed
        ] == [("candidate", "subject", "exited")]
        assert report.trials.subject_failures == 1 and report.trials.harness_failures == 0
        assert report.analysis.findings[0].outcome == "fails"


class TestAReadingOutsideItsShapesDomain:
    """Regression: ``Reading`` accepted a boolean of 2, negative events, more events than
    units and a statistic that is not finite, and the analysis of such a row failed with a
    bare ``math domain error``. A row like that is refused where it is made, and a stored
    run holding one is a damaged record."""

    @pytest.mark.parametrize(
        ("shape", "fields"),
        [("boolean", {"value": 2.0}), ("boolean", {"value": -1.0}),
         ("scalar", {"value": float("nan")}),
         ("count", {"statistics": {"events": -5.0, "units": 100.0}}),
         ("count", {"statistics": {"events": 150.0, "units": 100.0}}),
         ("count", {"statistics": {"events": 1.5, "units": 100.0}}),
         ("count", {"statistics": {"events": float("nan"), "units": 100.0}}),
         ("histogram", {"statistics": {"p99": float("inf")}})],
    )  # fmt: skip
    def test_is_refused(self, shape: Shape, fields: dict[str, Any]) -> None:
        with pytest.raises(ValueError, match="outside"):
            Reading(seq=0, subject="a", role="r", sensor="s", metric="m", shape=shape, **fields)

    @pytest.mark.parametrize(
        ("shape", "fields"),
        [("boolean", {"value": True}), ("boolean", {"value": 0.0}),
         ("scalar", {"value": -3.5}), ("count", {"statistics": {"events": 0.0, "units": 1.0}}),
         ("histogram", {"statistics": {"p99": 12.0}}), ("count", {})],
    )  # fmt: skip
    def test_one_inside_it_is_read(self, shape: Shape, fields: dict[str, Any]) -> None:
        Reading(seq=0, subject="a", role="r", sensor="s", metric="m", shape=shape, **fields)

    def test_a_stored_run_holding_one_is_a_damaged_record(
        self, root: Root, write_experiment: Write
    ) -> None:
        file = write_experiment(experiment(variant_ms=55.0))
        root.register(file)
        report = root.run(file)
        readings = Path(report.location, f"{schema.READING}.jsonl")
        rows = [json.loads(line) for line in readings.read_text().splitlines()]
        rows[0]["statistics"] = {"p50": float("inf")}
        readings.write_text("".join(json.dumps(row) + "\n" for row in rows))
        with pytest.raises(ix.ConfigError, match="a reading ix never writes") as refused:
            root.report(report.location)
        assert refused.value.exit_code == 3 and "damaged" in (refused.value.fix or "")


class TestAReadingOfTheWrongShape:
    _INTERACTION = Interaction(seq=0, subject="a", role="control", started_ns=0, duration_ns=1)

    @pytest.mark.parametrize("given", [[1.0, 2.0], {"p99": None}, {"p99": "fast"}, {"p99": True}])
    def test_a_distribution_that_is_not_one_is_the_sensors_shape_failure(self, given: Any) -> None:
        latency = Metric(name="latency", unit="ms", direction="lower", shape="histogram")
        reading = running._reading(self._INTERACTION, "ix.v1.sensor.test", latency, given)
        assert reading.failure is not None
        assert (reading.failure.owner, reading.failure.reason) == ("sensor", "shape")
        assert reading.statistics == {}

    @pytest.mark.parametrize("given", [float("nan"), float("inf"), float("-inf")])
    def test_a_scalar_that_is_not_finite_is_the_sensors_failure(self, given: float) -> None:
        """Recorded as a value it would be dropped by the analysis without a word."""
        throughput = Metric(name="throughput", unit="1/s", direction="higher", shape="scalar")
        reading = running._reading(self._INTERACTION, "s", throughput, given)
        assert reading.failure is not None and reading.value is None
        assert (reading.failure.owner, reading.failure.reason) == ("sensor", "not_finite")

    @pytest.mark.parametrize("shape", ["histogram", "series"])
    @pytest.mark.parametrize(
        "given", [{"p99": float("nan")}, {"p50": 3.0, "p99": float("inf")}, {"p99": float("-inf")}]
    )
    def test_a_distribution_with_a_statistic_not_finite_is_the_sensors_failure(
        self, shape: Shape, given: dict[str, float]
    ) -> None:
        """Regression: a NaN statistic was a ``shape`` failure, where a NaN scalar is
        ``not_finite``."""
        latency = Metric(name="latency", unit="ms", direction="lower", shape=shape)
        reading = running._reading(self._INTERACTION, "s", latency, given)
        assert reading.failure is not None and reading.statistics == {}
        assert (reading.failure.owner, reading.failure.reason) == ("sensor", "not_finite")

    @pytest.mark.parametrize(
        "given", [10**400, -(10**400), 10**5000], ids=["1e400", "-1e400", "1e5000"]
    )
    def test_a_scalar_int_too_large_for_a_float_is_not_finite(self, given: int) -> None:
        """Regression: ``float()`` raised OverflowError out of the run."""
        throughput = Metric(name="throughput", unit="1/s", direction="higher", shape="scalar")
        reading = running._reading(self._INTERACTION, "s", throughput, given)
        assert reading.failure is not None and reading.value is None
        assert (reading.failure.owner, reading.failure.reason) == ("sensor", "not_finite")
        assert "too large for a float" in reading.failure.message

    @pytest.mark.parametrize("shape", ["histogram", "series"])
    @pytest.mark.parametrize(
        "given", [{"p99": 10**400}, {"p50": 3, "p99": -(10**5000)}], ids=["1e400", "-1e5000"]
    )
    def test_a_distribution_with_an_int_too_large_for_a_float_is_not_finite(
        self, shape: Shape, given: dict[str, int]
    ) -> None:
        """Regression: ``float()`` raised OverflowError out of the run."""
        latency = Metric(name="latency", unit="ms", direction="lower", shape=shape)
        reading = running._reading(self._INTERACTION, "s", latency, given)
        assert reading.failure is not None and reading.statistics == {}
        assert (reading.failure.owner, reading.failure.reason) == ("sensor", "not_finite")
        assert "p99=" in reading.failure.message
        assert "too large for a float" in reading.failure.message

    @pytest.mark.parametrize(
        "given",
        [{"events": 10**400, "units": 10**401}, {"events": 1, "units": 10**400}],
        ids=["events", "units"],
    )
    def test_a_count_with_an_int_too_large_for_a_float_is_the_sensors_failure(
        self, given: dict[str, int]
    ) -> None:
        """Regression: ``float()`` raised OverflowError out of the sense step, failing the
        run's sensing where one reading was meant to fail."""
        share = Metric(name="tests_pass_share", unit="ratio", direction="higher", shape="count")
        reading = running._reading(self._INTERACTION, "s", share, given)
        assert reading.failure is not None and reading.statistics == {}
        assert (reading.failure.owner, reading.failure.reason) == ("sensor", "shape")

    @pytest.mark.parametrize(
        "given",
        [[0.5], None, "fast", Sensed(values=[0.5]), Sensed(values={}, detail=["x"])],
        ids=["list", "none", "text", "sensed-list", "sensed-detail-list"],
    )
    async def test_a_sensor_that_gives_no_mapping_fails_its_readings(self, given: Any) -> None:
        """Regression: a sensor that returned something other than a mapping of metric names
        to values raised out of the sense step, failing every reading of the run."""

        class Odd:
            async def sense(self, interaction: Any, *, truth: Any, options: Any) -> Any:
                return given

        measured = await running._measure(Odd(), self._INTERACTION, {}, {})
        assert isinstance(measured, Failure)
        assert (measured.owner, measured.reason) == ("sensor", "shape")

    def test_a_distribution_of_numbers_is_read(self) -> None:
        latency = Metric(name="latency", unit="ms", direction="lower", shape="histogram")
        reading = running._reading(self._INTERACTION, "s", latency, {"p50": 3, "p99": 7.5})
        assert reading.failure is None and reading.statistics == {"p50": 3.0, "p99": 7.5}


class _Overrunning:
    """A load probe that takes far longer than the budget it declares."""

    def budget_s(self, options: Any) -> float:
        return 0.05

    async def apply(self, options: Any, target: Target, *, seed: int) -> Any:
        await asyncio.sleep(30)


class _BlindSensor:
    """A sensor whose watch cannot begin."""

    def watch(self, target: Target, options: Any) -> Any:
        @asynccontextmanager
        async def watching() -> AsyncIterator[dict[str, Any]]:
            raise RuntimeError("the counter could not be opened")
            yield {}

        return watching()

    async def sense(self, interaction: Any, *, truth: Any, options: Any) -> Any:
        return {}


class _FailingEnvironment:
    """An environment that is found down after its second trial."""

    def __init__(self) -> None:
        self.asked = 0

    async def up(self, environment: Any, *, base: str, scope: Any) -> Any:
        return {"upstream": {"pid": 1}}

    async def lost(self, bindings: Any) -> str | None:
        self.asked += 1
        return "service upstream exited" if self.asked >= 2 else None


class TestWhoseFailureAtTheEdges:
    def test_a_probe_that_overruns_its_budget_is_the_harnesss_and_the_subject_is_released(
        self, write_experiment: Write
    ) -> None:
        LOG.clear()
        file = write_experiment(experiment())
        report = Root(registry=fake_registry(probe=_Overrunning)).run(file, exploratory=True)
        failures = [row["failure"] for row in _rows(report.location, schema.INTERACTION)]
        assert [(f["owner"], f["reason"]) for f in failures] == [("harness", "timeout")] * 15
        assert report.status == "completed" and report.trials.subject_failures == 0
        assert LOG.count("release baseline") == 10 and LOG.count("release candidate") == 5

    def test_a_sensor_whose_watch_cannot_begin_is_the_harnesss_and_the_subject_is_released(
        self, write_experiment: Write
    ) -> None:
        LOG.clear()
        file = write_experiment(experiment())
        report = Root(registry=fake_registry(sensor=_BlindSensor)).run(file, exploratory=True)
        failures = [row["failure"] for row in _rows(report.location, schema.INTERACTION)]
        assert [(f["owner"], f["reason"]) for f in failures] == [("harness", "error")] * 15
        assert "the counter could not be opened" in failures[0]["message"]
        assert [line for line in LOG if line.startswith("apply")] == []
        assert LOG.count("reach baseline") == LOG.count("release baseline") == 10

    def test_an_environment_lost_mid_run_fails_the_run_and_the_trial_in_flight_is_the_harnesss(
        self, write_experiment: Write
    ) -> None:
        file = write_experiment(experiment())
        report = Root(registry=fake_registry(environment=_FailingEnvironment)).run(
            file, exploratory=True
        )
        assert report.status == "failed" and report.exit_code == 8
        rows = _rows(report.location, schema.INTERACTION)
        assert len(rows) == 2 and rows[0]["failure"] is None
        assert rows[1]["observations"] == {}
        assert rows[1]["failure"] == {
            "owner": "harness",
            "reason": "environment_lost",
            "message": "service upstream exited",
        }
        assert any("the environment went down" in error for error in report.errors)

    def test_a_defect_in_ix_raises_out_of_the_trial_instead_of_becoming_data(
        self, write_experiment: Write
    ) -> None:
        """A sensor the design selected that the kit does not hold is ix's own mistake: the
        trial does not start, and nothing is recorded as anyone's failure."""
        LOG.clear()
        root = Root(registry=fake_registry())
        file = write_experiment(experiment())
        examined = ix.application.experiments.examine(
            yaml.safe_load(file.read_text()), root.vocabulary(file.parent), base=str(file.parent)
        )
        prepared = running.prepare(
            examined, root.vocabulary(file.parent), None, run="r", base=str(file.parent),
            now="", version="", exploratory=True,
        )  # fmt: skip
        kit = running.Kit(
            subjects={schema.url("subject", "process"): FakeSubject()},
            probes={schema.url("probe", "http-load"): FakeLoad()},
            sensors={},
            environments={},
            clock_ns=time.time_ns,
        )
        with pytest.raises(KeyError):
            asyncio.run(
                running.invoke(
                    prepared.trials[0], running.setup_of(prepared.manifest), kit, _Scope()
                )
            )
        assert LOG == []

    def test_a_subject_command_that_is_not_there_is_refused_before_anything_starts(
        self, write_experiment: Write
    ) -> None:
        data = experiment()
        data["subjects"]["candidate"]["command"] = ["./proxy-typo"]
        file = write_experiment(data)
        registry = fake_registry(subject=lambda: ProcessSubject(MatrixLauncher()))
        with pytest.raises(ix.ConfigError, match="subjects.candidate: './proxy-typo'") as refused:
            Root(registry=registry).run(file, exploratory=True)
        assert refused.value.exit_code == 3
        assert not (file.parent / RUNS_DIRECTORY).exists()


class _StuckSensor:
    """A sensor whose watch never begins and does not look at the stop flag, so a stop
    request is not honoured within the grace and matrix cancels the trial in flight."""

    def __init__(self) -> None:
        self.begun = asyncio.Event()

    def watch(self, target: Target, options: Any) -> Any:
        @asynccontextmanager
        async def watching() -> AsyncIterator[dict[str, Any]]:
            self.begun.set()
            await asyncio.Event().wait()
            yield {}

        return watching()

    async def sense(self, interaction: Any, *, truth: Any, options: Any) -> Any:
        return {}


class _Held:
    """A subject held open: released by its ``__aexit__`` and by nothing else, so a release
    cannot come from a generator being collected."""

    def __init__(self, name: str) -> None:
        self._name = name

    async def __aenter__(self) -> Target:
        LOG.append(f"reach {self._name}")
        return Target(subject=self._name, facets={"url": "http://fake", "env": {}})

    async def __aexit__(self, *_: object) -> None:
        LOG.append(f"release {self._name}")


class _HeldSubject:
    def unstartable(self, subject: Any, *, base: str) -> str | None:
        return None

    def reach(self, name: str, subject: Any, *, base: str) -> _Held:
        return _Held(name)


def test_a_trial_cancelled_mid_flight_by_matrix_still_releases_its_subject(
    write_experiment: Write, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stop is not honoured within the grace, so matrix cancels the trials component
    while its subject is held open; the run's release releases that subject before the run
    returns."""
    monkeypatch.setattr(matrix_runs, "GRACE_S", 0.05)
    LOG.clear()
    root = Root(registry=fake_registry())
    file = write_experiment(experiment())
    vocabulary = root.vocabulary(file.parent)
    examined = ix.application.experiments.examine(
        yaml.safe_load(file.read_text()), vocabulary, base=str(file.parent)
    )
    prepared = running.prepare(
        examined, vocabulary, None, run="r", base=str(file.parent), now="", version="",
        exploratory=True,
    )  # fmt: skip
    sensor = _StuckSensor()
    kit = running.Kit(
        subjects={schema.url("subject", "process"): _HeldSubject()},
        probes={schema.url("probe", "http-load"): FakeLoad()},
        sensors={schema.url("sensor", "timing"): sensor},
        environments={schema.url("environment", "none"): NoEnvironment()},
        clock_ns=time.time_ns,
    )

    async def cancelled_mid_trial() -> list[str]:
        run = asyncio.ensure_future(
            matrix_runs.execute(
                store=JsonlConstructStore(file.parent / RUNS_DIRECTORY),
                registry=fake_registry(),
                config=MatrixConfig.model_validate(COMPOSITION),
                needs={NEED: kit},
                manifest=prepared.manifest,
                trials=prepared.trials,
            )
        )
        await sensor.begun.wait()
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        return list(LOG)

    seen = asyncio.run(cancelled_mid_trial())
    (subject,) = {line.split()[1] for line in seen if line.startswith("reach")}
    assert seen == [f"reach {subject}", f"release {subject}"]


class _LeakyEnvironment:
    """An environment whose deferred stop raises: what it started may be left running."""

    async def up(self, environment: Any, *, base: str, scope: Any) -> Any:
        def stop() -> None:
            raise RuntimeError("the upstream would not stop")

        scope.defer(stop)
        return {}

    async def lost(self, bindings: Any) -> str | None:
        return None


class TestWhatTheRecordSaysWentWrong:
    def test_a_release_that_failed_is_in_the_report(self, write_experiment: Write) -> None:
        file = write_experiment(experiment())
        report = Root(registry=fake_registry(environment=_LeakyEnvironment)).run(
            file, exploratory=True
        )
        assert report.status == "completed"
        assert report.errors == (
            "environment: a release failed: RuntimeError: the upstream would not stop",
        )

    def test_what_matrix_could_not_do_keeps_its_kind(self, write_experiment: Write) -> None:
        """Without ix's components in the registry the run cannot be wired: matrix's config
        error reaches the caller as ix's, not as an unclassified failure."""
        registry = default_registry(discover=False)
        for point in POINTS.values():
            registry.add_point(point)
        adapters = {"subject": ("process", FakeSubject), "probe": ("http-load", FakeLoad),
                    "sensor": ("timing", FakeTiming),
                    "environment": ("none", NoEnvironment)}  # fmt: skip
        for family, (name, build) in adapters.items():
            registry.register(POINTS[family], schema.url(family, name), build)
        file = write_experiment(experiment())
        with pytest.raises(ix.ConfigError) as failed:
            Root(registry=registry).run(file, exploratory=True)
        assert failed.value.exit_code == 3
        assert str(failed.value).startswith("the run could not be carried out: ")


class TestABrokenExtension:
    def test_an_adapter_that_cannot_be_built_fails_only_the_run_that_uses_it(
        self, write_experiment: Write
    ) -> None:
        def needs_a_client(*, s3_client: Any) -> Any:
            return s3_client

        registry = fake_registry()
        registry.register(
            POINTS["sensor"], "ix.v1.sensor.memory", needs_a_client, needs={"s3_client"}
        )
        root = Root(registry=registry)
        plain = root.run(write_experiment(experiment(), "plain"), exploratory=True)
        assert plain.status == "completed"
        data = experiment(metrics=["memory_peak"])
        with pytest.raises(ix.ConfigError) as refused:
            root.run(write_experiment(data, "memory"), exploratory=True)
        assert "s3_client" in str(refused.value)

    def test_a_kind_without_an_adapter_names_a_quarantined_extension(
        self, evaluate_data: dict[str, Any], write_experiment: Write
    ) -> None:
        def broken(registry: Any) -> None:
            raise ImportError("no module named acme_agents")

        registry = fake_registry().load([Extension(name="acme", origin="acme", register=broken)])
        root = Root(registry=registry)
        assert root.kinds().quarantined == (
            "acme (acme): ImportError: no module named acme_agents",
        )
        warnings = root.validate(write_experiment(evaluate_data)).warnings
        unimplemented = [w for w in warnings if w.kind == "kind_unimplemented"]
        assert unimplemented and all("matrix catalog" in w.fix for w in unimplemented)
        assert "no module named acme_agents" in unimplemented[0].fix


class _Scope:
    def __init__(self) -> None:
        self.stopping = asyncio.Event()

    def defer(self, release: Any, /) -> None: ...

    def emit(self, name: str, /, **fields: Any) -> None: ...


def _trial_positions(root: Root, file: Path) -> dict[tuple[str, str], list[int]]:
    """Where each subject's trials fall in the order, from a throwaway exploratory run."""
    report = Root(registry=fake_registry()).run(file, exploratory=True)
    found: dict[tuple[str, str], list[int]] = {}
    for trial in _rows(report.location, schema.TRIAL):
        found.setdefault((trial["subject"], trial["role"]), []).append(trial["seq"])
    LOG.clear()
    return found


class _ReadsAFile:
    """A third party's sensor: reports the ``file`` option it was given."""

    seen: ClassVar[list[str]] = []

    def watch(self, target: Target, options: Any) -> None:
        return None

    async def sense(self, interaction: Interaction, *, truth: Any, options: Any) -> Any:
        _ReadsAFile.seen.append(str(options["file"]))
        return {"lines": 1.0}


class TestALabsOwnKinds:
    def test_reach_the_run_so_a_sensors_path_is_placed(self, write_experiment: Write) -> None:
        """Regression: the run's kit took the installed kinds only, so a sensor kind a lab
        declares in ``<lab>/kinds/`` ran without its declaration: its ``path`` option reached
        the adapter as written, not resolved against the experiment."""
        registry = fake_registry()
        sensor = "acme.v1.sensor.reads-file"
        registry.register(POINTS["sensor"], sensor, _ReadsAFile)
        file = write_experiment(
            experiment(sensors=[{"kind": sensor, "file": "data.txt"}], metrics=["lines"])
        )
        (file.parent / "kinds").mkdir()
        (file.parent / "kinds" / "reads-file.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema": "ix.v1.kind",
                    "name": sensor,
                    "summary": "Reads a file.",
                    "options": {"file": {"type": "path", "required": True}},
                    "metrics": [{"name": "lines", "direction": "higher", "shape": "scalar"}],
                }
            )
        )
        _ReadsAFile.seen.clear()
        report = Root(registry=registry).run(file, exploratory=True)
        assert report.status == "completed"
        assert set(_ReadsAFile.seen) == {f"{file.parent.resolve()}/data.txt"}


class TestWhatCannotRun:
    def test_a_kind_with_no_adapter_is_a_warning_a_plan_note_and_a_refusal(
        self, evaluate_data: dict[str, Any], write_experiment: Write
    ) -> None:
        """A declared kind with no adapter (an Inspect sample) is a valid declaration that
        this ix cannot carry out: it says so when checked and planned, not after starting."""
        evaluate_data["probes"][0] = {
            "id": "ask", "kind": "inspect-task", "task": "humaneval",
            "truth": {"expectation": "must_trigger", "expected_skill": "review"},
        }  # fmt: skip
        file = write_experiment(evaluate_data)
        checked = ix.validate(file)
        assert checked.valid
        assert [(w.kind, w.got) for w in checked.warnings] == [
            ("kind_unimplemented", "inspect-task")
        ]
        plan = ix.plan(file)
        assert plan.unimplemented == ("probe kind inspect-task (probes.ask)",)
        with pytest.raises(ix.ConfigError, match="no adapter for") as refused:
            ix.run(file, exploratory=True)
        assert refused.value.exit_code == 3
        assert not (file.parent / RUNS_DIRECTORY).exists()

    def test_an_analysis_parameter_ix_does_not_read_is_a_warning_and_a_refusal(
        self, root: Root, write_experiment: Write
    ) -> None:
        """ix's analyses take no parameters, so a genre that gives one asks for something
        the analysis would silently not do."""
        compare = Path(ix.__file__).parent / "builtin" / "genres" / "compare.yaml"
        genre = yaml.safe_load(compare.read_text())
        genre["name"] = "lab.v1.genre.uncorrected"
        genre["analysis"]["params"] = {"correction": "none"}
        file = write_experiment({**experiment(), "genre": "lab.v1.genre.uncorrected"})
        (file.parent / "genres").mkdir()
        (file.parent / "genres" / "uncorrected.yaml").write_text(yaml.safe_dump(genre))

        checked = root.validate(file)
        assert checked.valid
        (warning,) = checked.warnings
        assert warning.kind == "analysis_unimplemented" and "correction" in warning.message
        with pytest.raises(ix.ConfigError, match="takes no parameters"):
            root.run(file, exploratory=True)
        assert LOG == [] and not (file.parent / RUNS_DIRECTORY).exists()

    def test_what_ix_can_run_is_listed_with_the_kinds(self) -> None:
        kinds = ix.kinds()
        runnable = {schema.short(name) for name in kinds.runnable}
        assert runnable == {
            "process", "http", "http-load", "timing", "memory", "none", "agent-session",
            "model-call", "prompt", "activation", "tool-usage", "function-test", "outcome",
            "text-match", "usage", "judge",
        }  # fmt: skip
        assert set(kinds.kinds) - set(kinds.runnable) == {
            "ix.v1.probe.inspect-task", "ix.v1.environment.compose",
        }  # fmt: skip
        assert set(kinds.runnable) < set(kinds.kinds)

    def test_a_plan_of_something_runnable_names_nothing_missing(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file = write_experiment(compare_data)
        assert ix.plan(file).unimplemented == () and ix.validate(file).warnings == ()

    def test_a_subject_this_host_cannot_start_is_in_the_plan_before_it_is_registered(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        file = write_experiment(compare_data)  # its subjects run ./proxy, which is not there
        unstartable = ix.plan(file).unstartable
        assert [reason.split(":")[0] for reason in unstartable] == [
            "subjects.buffered",
            "subjects.streaming",
        ]
        assert all("'./proxy' is not an executable file" in reason for reason in unstartable)
        assert ix.register(file).outcome == "created"
        with pytest.raises(ix.ConfigError, match="cannot start"):
            ix.run(file)
        assert not (file.parent / RUNS_DIRECTORY).exists()


class TestTheComponents:
    def test_they_register_with_matrix_and_wire_by_kind(self) -> None:
        from matrix import MatrixConfig, compose, default_registry

        registry = default_registry(discover=False)
        register_run_path(registry)
        for point, url in [("component", ENVIRONMENT), ("component", TRIALS), ("component", SENSE)]:
            assert (point, url) in registry
        assert set(registry.payload_schemas()) >= {
            schema.RUN, schema.TRIAL, schema.BINDINGS, schema.INTERACTION, schema.READING,
        }  # fmt: skip
        config = MatrixConfig.model_validate(
            {
                "inputs": [schema.RUN, schema.TRIAL],
                "components": [
                    {"name": "sense", "type": SENSE},
                    {"name": "trials", "type": TRIALS},
                    {"name": "environment", "type": ENVIRONMENT},
                ],
            }
        )
        wiring = compose(config, registry=registry, context={NEED: object()}).wire()
        assert wiring.order() == ("environment", "trials", "sense")
        assert wiring.providers == {
            schema.BINDINGS: "environment",
            schema.INTERACTION: "trials",
            schema.READING: "sense",
        }

    def test_matrix_finds_them_through_its_entry_point(self) -> None:
        from matrix import default_registry

        registry = default_registry()
        assert registry.failures == []
        assert registry.entry("component", TRIALS).origin == "ix"
        for family, name in [("subject", "process"), ("probe", "http-load"),
                             ("sensor", "memory"), ("subject", "agent-session"),
                             ("sensor", "judge")]:  # fmt: skip
            assert registry.entry(POINTS[family], schema.url(family, name)).origin == "ix"


# --- what a signal and a kill leave behind ---------------------------------------------

_DRIVER = """
import sys
from ix_fakes import fake_registry
from ix.composition import Root

report = Root(registry=fake_registry()).run(sys.argv[1])
print(report.status, report.exit_code, flush=True)
sys.exit(report.exit_code)
"""


def _start_slow_run(tmp_path: Path, write_experiment: Write) -> tuple[subprocess.Popen[str], Path]:
    data = experiment()
    for subject in data["subjects"].values():
        subject["env"]["HOLD_S"] = "0.3"
    file = write_experiment(data)
    Root(registry=fake_registry()).register(file)
    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER)
    process = subprocess.Popen(
        [sys.executable, str(driver), str(file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parent)},
    )
    return process, file


def _wait_for_interactions(file: Path, count: int, process: subprocess.Popen[str]) -> Path:
    """Block until the run has ``count`` interactions on disk; return its directory."""
    runs = file.parent / RUNS_DIRECTORY
    until = time.monotonic() + 60
    while time.monotonic() < until:
        for table in runs.glob(f"*/{schema.INTERACTION}.jsonl"):
            if len(table.read_text().splitlines()) >= count:
                return table.parent
        assert process.poll() is None, process.stderr.read() if process.stderr else ""
        time.sleep(0.02)
    raise AssertionError("the run recorded nothing in 60s")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_hard_kill_mid_run_leaves_every_finished_trial_readable_and_reportable(
    tmp_path: Path, write_experiment: Write, monkeypatch: pytest.MonkeyPatch
) -> None:
    process, file = _start_slow_run(tmp_path, write_experiment)
    try:
        location = _wait_for_interactions(file, 4, process)
        process.send_signal(signal.SIGKILL)
        process.wait(timeout=30)
    finally:
        process.kill()
    assert process.returncode == -signal.SIGKILL

    # Nothing ran after the kill: no finish, no sense. The record is what was on disk.
    kept = _rows(str(location), schema.INTERACTION)
    assert len(kept) >= 4 and all(row["failure"] is None for row in kept)
    assert _rows(str(location), schema.READING) == []
    assert json.loads((location / "run.json").read_text())["status"] == "running"

    root = Root(registry=fake_registry())
    report = root.report(location)
    assert report.status == "interrupted"
    assert report.trials.planned == 15 and report.trials.carried_out == len(kept)
    assert report.readings == "derived"  # the sensors read the stored interactions now
    assert sum(e.n for e in report.analysis.estimates) == len(kept)
    assert report.verdict is None and "interrupted" in report.no_verdict
    assert report.exit_code == 12
    assert [(r.status, r.carried_out) for r in root.runs(file).runs] == [("interrupted", len(kept))]

    # Where the run's sensor has no adapter, the readings cannot be derived and the report
    # says none were taken; any other error while deriving them is raised, not read as that.
    unread = Root(registry=fake_registry(sensor=None)).report(location)
    assert unread.readings == "none" and unread.trials.carried_out == len(kept)
    assert unread.unread.startswith("no adapter here reads timing: run the experiment again")

    def broken(*_: Any, **__: Any) -> Any:
        raise KeyError("a defect, not a missing adapter")

    monkeypatch.setattr(running, "sense", broken)
    with pytest.raises(KeyError, match="a defect"):
        root.report(location)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("stop", [signal.SIGINT, signal.SIGTERM])
def test_a_stop_request_ends_the_run_cleanly_and_the_partial_run_is_reportable(
    stop: signal.Signals, tmp_path: Path, write_experiment: Write
) -> None:
    process, file = _start_slow_run(tmp_path, write_experiment)
    try:
        location = _wait_for_interactions(file, 3, process)
        process.send_signal(stop)
        out, err = process.communicate(timeout=60)
    finally:
        process.kill()
    # The run reported on itself and exited with the code for an interrupted run.
    assert out.split() == ["interrupted", "12"], err
    assert process.returncode == 12

    described = json.loads((location / "run.json").read_text())
    assert described["status"] == "cancelled"
    # It returned what it had when asked to stop: matrix records that as stopped, never completed,
    # so a resumed run does not take the partial trials for finished work.
    assert described["components"]["trials"]["status"] == "stopped"
    assert described["components"]["sense"]["status"] == "skipped"
    kept = _rows(str(location), schema.INTERACTION)
    assert 3 <= len(kept) < 15
    # The trial in flight was stopped and recorded as the harness's, never the subject's.
    assert kept[-1]["failure"] == {
        "owner": "harness",
        "reason": "cancelled",
        "message": "the run was asked to stop mid-trial",
    }
    assert all(row["failure"] is None for row in kept[:-1])

    report = Root(registry=fake_registry()).report(location)
    assert report.status == "interrupted" and report.exit_code == 12
    assert report.trials.carried_out == len(kept) and report.trials.harness_failures == 1
    assert report.readings == "derived" and report.verdict is None


def test_an_environment_that_cannot_come_up_fails_the_run_and_the_record_says_why(
    write_experiment: Write,
) -> None:
    class Broken:
        async def up(self, environment: Any, *, base: str, scope: Any) -> Any:
            raise RuntimeError("the upstream would not start")

    file = write_experiment(experiment())
    report = Root(registry=fake_registry(environment=Broken)).run(file, exploratory=True)
    assert report.status == "failed" and report.exit_code == 8
    assert report.trials.carried_out == 0 and report.verdict is None
    assert report.no_verdict == "the run failed"
    assert any("the upstream would not start" in error for error in report.errors)


def test_the_example_experiments_are_what_the_fakes_can_stand_in_for(
    tmp_path: Path, write_experiment: Write
) -> None:
    """The example beside the delay server is an ordinary experiment: it validates, plans
    30 trials (the genre's 10 repeats of control, candidate and the control again), and
    names only kinds this ix can run."""
    for name in ("identical", "slower"):
        file = Path(ix.__file__).parents[2] / "examples" / "delay-server" / f"{name}.yaml"
        assert ix.validate(file).valid
        plan = ix.plan(file)
        assert plan.trials.total == 30 and plan.unimplemented == ()
        assert yaml.safe_load(file.read_text())["comparison"] == "superiority"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class _Unanswered:
    """A load probe whose subject has stopped answering: it waits until the subject's
    process is gone, then reports its own failure, as a load tool that got no answer might."""

    def budget_s(self, options: Any) -> float:
        return 30.0

    async def apply(self, options: Any, target: Target, *, seed: int) -> Any:
        while _alive(target.facets["pid"]):
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)  # let the loop see the child exit
        raise HarnessError("tool_failed", "the load tool gave up: nothing answered")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_a_started_program_that_exits_mid_probe_is_the_subjects_failure(
    write_experiment: Write,
) -> None:
    registry = fake_registry(subject=lambda: ProcessSubject(MatrixLauncher()), probe=_Unanswered)
    file = write_experiment(
        {
            "name": "short-lived",
            "question": "Does it stay up under load?",
            "genre": "evaluate",
            "subjects": {
                "server": {
                    "kind": "process",
                    "command": [sys.executable, "-c", "import time; time.sleep(0.3)"],
                }
            },
            "probes": [
                {
                    "id": "get",
                    "kind": "http-load",
                    "target": "http://127.0.0.1:9/x",
                    "load_model": "closed",
                    "duration": "1s",
                }
            ],
            "primary": {"metric": "latency", "statistic": "p50"},
            "threshold": {"op": "<", "value": 100},
            "repeats": 5,  # the genre's minimum; every trial ends the same way
        }
    )
    report = Root(registry=registry).run(file, exploratory=True)
    rows = _rows(report.location, schema.INTERACTION)
    assert len(rows) == 5
    for row in rows:
        assert (row["failure"]["owner"], row["failure"]["reason"]) == ("subject", "exited")
        assert "during the probe" in row["failure"]["message"]


def test_an_extension_adds_an_adapter_for_its_own_kind_without_an_edit_to_ix(
    write_experiment: Write,
) -> None:
    """A third party declares a kind as data and registers its adapter at ix's point for the
    family, under the kind's type URL. ix lists it as runnable and runs it."""

    class Echo:
        def unstartable(self, subject: Any, *, base: str) -> str | None:
            return None

        @asynccontextmanager
        async def reach(self, name: str, subject: Any, *, base: str) -> AsyncIterator[Target]:
            yield Target(subject=name, facets={"url": "http://echo", "env": {}})

    registry = fake_registry()
    registry.register(POINTS["subject"], "acme.v1.subject.echo", Echo)
    data = experiment()
    data["subjects"] = {name: {"kind": "acme.v1.subject.echo"} for name in ("one", "two")}
    data["probes"][0]["target"] = "http://echo/x"
    file = write_experiment(data)
    (file.parent / "kinds").mkdir()
    (file.parent / "kinds" / "echo.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": "ix.v1.kind",
                "name": "acme.v1.subject.echo",
                "summary": "An endpoint that answers at once.",
                "offers": ["url"],
            }
        )
    )
    root = Root(registry=registry)
    assert "acme.v1.subject.echo" in root.kinds(file.parent).runnable
    report = root.run(file, exploratory=True)
    assert report.status == "completed" and report.trials.carried_out == 15
    assert report.trials.subject_failures == report.trials.harness_failures == 0


def test_a_matrix_error_carried_over_keeps_its_details() -> None:
    from matrix import ConfigError as MatrixConfigError

    carried = matrix_errors.carried_over(
        "the run could not be carried out", MatrixConfigError("bad", fix="f", path="a.b")
    )
    assert isinstance(carried, ix.ConfigError) and carried.details == {"path": "a.b"}
    assert ix.error_document(carried)["error"]["details"] == {"path": "a.b"}


def test_an_adapter_without_the_optional_checks_runs_with_nothing_to_check(
    write_experiment: Write,
) -> None:
    """A subject kind that is not a Preflight and an environment provider that is not a
    Liveness are whole adapters: there is nothing to check before the run or between
    trials, and the run proceeds."""

    class Bare:
        @asynccontextmanager
        async def reach(self, name: str, subject: Any, *, base: str) -> AsyncIterator[Target]:
            yield Target(subject=name, facets={"url": "http://bare", "env": {}})

    class Nothing:
        async def up(self, environment: Any, *, base: str, scope: Any) -> Any:
            return {}

    assert not isinstance(Bare(), Preflight) and not isinstance(Nothing(), Liveness)
    registry = fake_registry(subject=Bare, environment=Nothing)
    report = Root(registry=registry).run(write_experiment(experiment()), exploratory=True)
    assert report.status == "completed" and report.trials.carried_out == 15
    assert report.trials.harness_failures == report.trials.subject_failures == 0

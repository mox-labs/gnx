"""Trials, and whose fault a failure is.

* The native engine runs each trial as one run of a matrix flow compiled once per repeat.
* Engines execute; the experiment measures every trial with one rule, whatever the engine.
* A session that never had a fair chance (rate limit, outage, credentials) is a *harness*
  fault: recorded and counted, kept out of the score, and it stops ``compare`` calling a
  winner. A session that broke on its own is the *subject*'s. A grader that crashed is the
  *sensor*'s.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from matrix import (
    AgentDefinition,
    AgentResponse,
    AgentRuntimeError,
    BoundAgent,
    Component,
    Inputs,
    RecordingObserver,
    RunContext,
)

from ix.adapters._out.engines.native import NativeEngine, TrialStep
from ix.adapters._out.filesystem_store import FilesystemStore
from ix.domain.models import ExperimentConfig
from ix.domain.ports import EngineOutcome, EngineRun
from ix.domain.types import Probe, Reading, Subject, Trial
from ix.eval.analysis import compare_results
from ix.eval.experiment import Experiment
from ix.eval.measure import HARNESS_REASONS, fault_of, measure_trial, run_trial
from ix.eval.sensors import ActivationSensor

if TYPE_CHECKING:
    from pathlib import Path


class _Scripted:
    """An agent runtime that activates the skill, or fails with a given reason per task."""

    def __init__(self, failures: dict[str, str] | None = None) -> None:
        self.failures = failures or {}
        self.calls: list[str] = []

    def check(self, definition: AgentDefinition) -> None:
        pass

    async def run(self, definition: AgentDefinition, task: str) -> AgentResponse:
        self.calls.append(task)
        if task in self.failures:
            raise AgentRuntimeError(f"failed: {task}", reason=self.failures[task])  # type: ignore[arg-type]
        return AgentResponse(
            content="ok",
            tool_calls=({"name": "Skill", "input": {"skill": "s"}},),
            family="test",
        )


def _agents(runtime: _Scripted) -> Any:
    def build(subject: Subject, trial_index: int, run_index: int = 0) -> BoundAgent:
        return BoundAgent(AgentDefinition(name="a"), runtime)

    return build


def _probes(*prompts: str) -> tuple[Probe, ...]:
    return tuple(Probe(id=p, prompt=p) for p in prompts)


async def test_trial_step_is_a_component_that_puts_the_probe_to_the_agent() -> None:
    seen: list[tuple[str, int, int]] = []
    runtime = _Scripted()

    def agents(subject: Subject, trial_index: int, run_index: int = 0) -> BoundAgent:
        seen.append((subject.name, trial_index, run_index))
        return BoundAgent(AgentDefinition(name="a"), runtime)

    step = TrialStep(agents, Subject(name="s"), run_index=2)
    assert isinstance(step, Component)
    out = await step.run(
        Inputs({"probe": Probe(id="p", prompt="q"), "index": 3}, RunContext("r", "trial"))
    )
    trial: Trial = out["trial"]
    assert (trial.probe_id, trial.trial_index, trial.response.content) == ("p", 3, "ok")
    assert seen == [("s", 3, 2)] and runtime.calls == ["q"]


async def test_the_native_engine_runs_one_compiled_flow_and_reports_each_trial() -> None:
    recorder = RecordingObserver()
    progress: list[tuple[str, int]] = []
    outcome = await NativeEngine(observers=[recorder]).run(
        EngineRun(
            experiment="e",
            probes=_probes("a", "b"),
            subject=Subject(name="s"),
            agents=_agents(_Scripted()),
            trials=2,
            measure=lambda trial: [],
            on_trial=lambda t: progress.append((t.probe_id, t.trial_index)),
        )
    )
    assert [(t.probe_id, t.trial_index) for t in outcome.trials] == [
        ("a", 0),
        ("a", 1),
        ("b", 0),
        ("b", 1),
    ]
    assert sorted(progress) == sorted((t.probe_id, t.trial_index) for t in outcome.trials)
    assert recorder.names().count("run.start") == 4  # one flow run per trial, observable


async def test_a_failed_session_is_an_errored_trial_with_its_reason() -> None:
    agent = BoundAgent(AgentDefinition(name="a"), _Scripted({"q": "rate_limited"}))
    trial = await run_trial(agent, "p", "q", 0)
    assert trial.error and trial.error_reason == "rate_limited"


def test_whose_fault_a_failed_trial_is() -> None:
    sensor = ActivationSensor(expected_skill="s")
    for reason in HARNESS_REASONS:
        trial = Trial(probe_id="p", trial_index=0, error="x", error_reason=reason)
        assert fault_of(trial) == "harness"
        (reading,) = measure_trial(sensor, trial)
        assert (reading.passed, reading.fault) == (False, "harness")
    for reason in ("failed", "refused", None):
        trial = Trial(probe_id="p", trial_index=0, error="x", error_reason=reason)
        assert measure_trial(sensor, trial)[0].fault == "subject"


def test_a_crashing_sensor_is_the_sensors_fault() -> None:
    class Broken:
        name = "broken"

        def measure(self, trial: Trial) -> list[Reading]:
            raise RuntimeError("grader bug")

    (reading,) = measure_trial(Broken(), Trial(probe_id="p", trial_index=0, response="x"))
    assert (reading.fault, reading.passed) == ("sensor", False)


class _CountingSensor:
    """Activation, counting how often each trial is measured."""

    name = "activation"

    def __init__(self) -> None:
        self.inner = ActivationSensor(expected_skill="s")
        self.measured: list[tuple[str, int]] = []

    def measure(self, trial: Trial) -> list[Reading]:
        self.measured.append((trial.probe_id, trial.trial_index))
        return self.inner.measure(trial)


class _ScoringEngine:
    """An engine that scores as it goes (as Inspect must), through the experiment's rule."""

    name = "scoring"

    def __init__(self) -> None:
        self.native = NativeEngine()

    async def run(self, run: EngineRun) -> EngineOutcome:
        outcome = await self.native.run(run)
        for trial in outcome.trials:
            run.measure(trial)
        return outcome


async def test_every_trial_is_measured_once_by_the_experiments_rule(tmp_path: Path) -> None:
    sensor = _CountingSensor()
    service = Experiment(
        sensor=sensor,
        store=FilesystemStore(tmp_path),
        engine=_ScoringEngine(),
        agents=_agents(_Scripted()),
    )
    config = ExperimentConfig(name="e", probes=_probes("a", "b"), trials=2)
    results = await service.run(config, Subject(name="s"))
    assert sorted(sensor.measured) == [("a", 0), ("a", 1), ("b", 0), ("b", 1)]  # once each
    assert results.pass_rate == 1.0


async def test_harness_faults_are_counted_and_kept_out_of_the_score(tmp_path: Path) -> None:
    runtime = _Scripted({"b": "rate_limited", "c": "failed"})
    service = Experiment(
        sensor=ActivationSensor(expected_skill="s"),
        store=FilesystemStore(tmp_path),
        engine=NativeEngine(),
        agents=_agents(runtime),
    )
    config = ExperimentConfig(name="e", probes=_probes("a", "b", "c"), trials=2)
    results = await service.run(config, Subject(name="s"))
    # 'b' never ran fairly: not scored. 'c' broke on its own: a failure. 'a' passed.
    assert results.harness_faults == 2
    assert results.unmeasured_probes == ("b",)
    assert results.n_probes == 2
    assert results.pass_rate == 0.5
    assert {p.probe_id for p in results.probe_results} == {"a", "c"}


async def test_compare_will_not_call_a_winner_over_harness_faults(tmp_path: Path) -> None:
    async def run(name: str, failures: dict[str, str]) -> Any:
        service = Experiment(
            sensor=ActivationSensor(expected_skill="s"),
            store=FilesystemStore(tmp_path),
            engine=NativeEngine(),
            agents=_agents(_Scripted(failures)),
        )
        config = ExperimentConfig(name="e", probes=_probes(*"abcdefgh"), trials=1)
        return await service.run(config, Subject(name=name))

    a = await run("a", {p: "failed" for p in "abcdefgh"})
    b = await run("b", {"h": "unavailable"})
    comparison = compare_results(a, b)
    assert comparison.harness_faults == 1
    assert comparison.verdict == "inconclusive"
    assert comparison.warning and "harness faults" in comparison.warning

"""Core protocols — Sensor, SensorClass, AgentFactory, Engine.

Composable building blocks of any experiment. Each is a typing.Protocol: implement the
methods, satisfy the contract.

* A **Sensor** measures a trial and produces readings — like an instrument.
* An **AgentFactory** turns a subject into a runnable agent for one trial.
* An **Engine** runs every probe × trial of one repeat and returns the trials and their
  readings. The native engine runs each trial as a matrix DAG; the Inspect engine runs the
  repeat as an Inspect AI task. Aggregation, noise floor and persistence are the same either
  way — they belong to the Experiment, not the engine.
* A **Storage** loads experiments and persists what a run produced: every trial, and a
  summary per subject.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from matrix import Agent

    from ix.domain.types import Probe, Reading, Subject, Trial
    from ix.eval.models import ExperimentConfig, ExperimentResults, TrialRecord


@runtime_checkable
class Sensor(Protocol):
    """Measures a trial and produces readings.

    Ground truth (test cases, rubrics) is injected at construction by the composition root,
    not discovered from the probe. The trial gives the sensor a key (probe_id) to look up
    its ground truth, and a response to measure.
    """

    @property
    def name(self) -> str: ...

    def measure(self, trial: Trial) -> list[Reading]: ...


@runtime_checkable
class SensorClass(Protocol):
    """A sensor *class* — the shape the composition root holds in its registry.

    Every ix sensor pairs a pydantic ``Config`` model with a ``from_config`` classmethod;
    naming the pair as a port is what lets a third party plug a sensor in, typed.
    """

    def from_config(
        self,
        config: Any,
        probes: tuple[Probe, ...] = ...,
        **kwargs: Any,
    ) -> Sensor: ...


class AgentFactory(Protocol):
    """Builds the agent that plays ``subject`` for one trial of one repeat.

    Per trial rather than per subject so a simulated runtime can seed each trial of each
    repeat independently. Seeding on the trial alone made every repeat of a seeded run
    replay identical draws, so the noise floor across repeats was 0 by construction.
    """

    def __call__(self, subject: Subject, trial_index: int, run_index: int = 0) -> Agent: ...


@dataclass(frozen=True)
class EngineRun:
    """Everything an engine needs for one repeat of an experiment."""

    experiment: str
    probes: tuple[Probe, ...]
    subject: Subject
    sensor: Sensor
    agents: AgentFactory
    trials: int
    run_index: int = 0
    #: Called as each trial is measured, in completion order — for progress, not results.
    #: The outcome is still the source of truth; an engine that cannot report per trial
    #: may leave it uncalled.
    on_trial: Callable[[Trial, list[Reading]], None] | None = None


@dataclass(frozen=True)
class EngineOutcome:
    readings: list[Reading]
    #: Every trial the repeat ran, response or error included — what the readings measured.
    trials: list[Trial] = field(default_factory=list)
    #: Engine-specific provenance a reader can open — e.g. the Inspect ``.eval`` log path.
    artifacts: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class Engine(Protocol):
    @property
    def name(self) -> str: ...

    async def run(self, run: EngineRun) -> EngineOutcome: ...


class Storage(Protocol):
    """Persistence boundary: experiments in, trial records and summaries out."""

    def load_experiment(self, path: Path) -> ExperimentConfig: ...

    def list_experiments(self, base: Path) -> list[Path]: ...

    def append_trials(
        self, experiment: str, subject: str, run_id: str, records: list[TrialRecord]
    ) -> Path: ...

    def save_summary(self, experiment: str, results: ExperimentResults) -> Path: ...

    def load_summary(self, experiment: str, subject: str) -> ExperimentResults: ...

    def subjects_with_results(self, experiment: str) -> list[str]: ...

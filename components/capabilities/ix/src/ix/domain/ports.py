"""Core protocols: Sensor, SensorClass, AgentFactory, Engine, Storage.

* A **Sensor** measures a trial and produces readings, like an instrument. It declares the
  probe truth keys it reads (``truth_keys``), so a probe key no sensor reads is caught as a
  typo when the experiment loads, not ignored.
* An **AgentFactory** turns a subject into a runnable agent for one trial.
* An **Engine** *executes*: it runs every probe × trial of one repeat and returns the trials.
  It never decides how a trial is judged. The experiment hands it ``measure`` (its one
  measuring rule), which an engine that must score as it goes (Inspect) calls; the experiment
  then measures every trial with the same function, so results never depend on the engine.
* A **Storage** loads experiments and persists what a run produced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from matrix import Agent

    from ix.domain.models import ExperimentConfig, ExperimentResults, TrialRecord
    from ix.domain.types import Probe, Reading, Subject, Trial


@runtime_checkable
class Sensor(Protocol):
    """Measures a trial and produces readings.

    Ground truth (test cases, expectations) is read from the probes at construction, by the
    keys the sensor declares in ``truth_keys``; the trial gives it a probe id to look its
    truth up and a response to measure.
    """

    @property
    def name(self) -> str: ...

    def measure(self, trial: Trial) -> list[Reading]: ...


@runtime_checkable
class SensorClass(Protocol):
    """A sensor *class*: what a third party registers at ix's ``sensor`` extension point.

    ``truth_keys`` names the probe frontmatter keys it reads. ``from_config`` builds it from
    its validated config, the experiment's probes, and ``judge``: a function returning a
    matrix agent for a model or agent name, for sensors that ask a model to grade.
    """

    truth_keys: frozenset[str]

    def from_config(
        self,
        config: Any,
        probes: tuple[Probe, ...] = ...,
        *,
        judge: Callable[[str], Agent] | None = ...,
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
    agents: AgentFactory
    trials: int
    #: The experiment's measuring rule. Call it to score a trial as it completes (it is
    #: memoised, so the experiment's own pass costs nothing extra); never judge otherwise.
    measure: Callable[[Trial], list[Reading]]
    run_index: int = 0
    #: Call as each trial completes, in completion order: progress only, never results.
    on_trial: Callable[[Trial], None] | None = None
    #: The sensors' combined name, for engines that label their own logs.
    sensor_name: str = ""


@dataclass(frozen=True)
class EngineOutcome:
    #: Exactly one trial per (probe, trial index), in probe × trial order; a failed session
    #: is a trial with an error, never a missing one.
    trials: list[Trial]
    #: Engine-specific provenance a reader can open, e.g. the Inspect ``.eval`` log path.
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

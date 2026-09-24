"""Experiment — repeats × engine, then aggregation.

An engine runs every probe × trial of one repeat and returns readings. The Experiment runs
the configured number of repeats, aggregates across them, computes the noise floor (the
spread of pass rates across repeats), and persists a summary with provenance.

The engine and the agent factory are injected by the composition root. This module imports
no concrete engine, runtime or DAG node.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from ix import __version__
from ix.domain.ports import EngineRun
from ix.domain.types import Reading, Subject
from ix.eval.analysis import (
    aggregate_readings,
    build_confusion_matrix,
    compute_metrics,
    compute_noise_floor,
)
from ix.eval.models import ExperimentConfig, ExperimentResults, ProbeResult, TrialRecord

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ix.domain.ports import AgentFactory, Engine, Sensor


class Storage(Protocol):
    """Persistence boundary for experiments and eval results."""

    def load_experiment(self, path: Path) -> ExperimentConfig: ...

    def list_experiments(self, base: Path) -> list[Path]: ...

    def append_result(self, experiment_name: str, result: TrialRecord) -> None: ...

    def save_summary(self, experiment_name: str, results: ExperimentResults) -> Path: ...


class Experiment:
    """An experiment you run: ``run(config, subject)`` → ExperimentResults."""

    def __init__(
        self, *, sensor: Sensor, store: Storage, engine: Engine, agents: AgentFactory
    ) -> None:
        self._sensor = sensor
        self._store = store
        self._engine = engine
        self._agents = agents

    async def run(
        self,
        config: ExperimentConfig,
        subject: Subject | None = None,
        on_probe_complete: Callable[[ProbeResult], None] | None = None,
        on_run_complete: Callable[[int, float], None] | None = None,
    ) -> ExperimentResults:
        """Run ``config.repeats`` repeats through the engine, then aggregate."""
        active_subject = subject or Subject(name="default")
        probe_map = {p.id: p for p in config.probes}

        all_readings: list[Reading] = []
        per_run_pass_rates: list[float] = []
        artifacts: list[str] = []

        for run_idx in range(config.repeats):
            outcome = await self._engine.run(
                EngineRun(
                    experiment=config.name,
                    probes=config.probes,
                    subject=active_subject,
                    sensor=self._sensor,
                    agents=self._agents,
                    trials=config.trials,
                    run_index=run_idx,
                )
            )
            all_readings.extend(outcome.readings)
            artifacts.extend(f"{k}:{v}" for k, v in outcome.artifacts.items())

            run_metrics = compute_metrics(aggregate_readings(outcome.readings, probe_map))
            per_run_pass_rates.append(run_metrics["pass_rate"])
            if on_run_complete:
                on_run_complete(run_idx, run_metrics["pass_rate"])

        callback = on_probe_complete if config.repeats == 1 else None
        probe_results = aggregate_readings(all_readings, probe_map, callback)
        metrics = compute_metrics(probe_results)

        config_json = config.model_dump_json(exclude={"probes"})
        config_hash = hashlib.sha256(config_json.encode()).hexdigest()[:16]

        results = ExperimentResults(
            experiment_name=config.name,
            subject=active_subject.name,
            probe_results=tuple(probe_results),
            pass_rate=metrics["pass_rate"],
            mean_score=metrics["mean_score"],
            min_score=metrics["min_score"],
            max_score=metrics["max_score"],
            repeats=config.repeats,
            per_run_pass_rates=tuple(per_run_pass_rates),
            noise_floor_sd=compute_noise_floor(per_run_pass_rates),
            confusion_matrix=build_confusion_matrix(all_readings),
            engine=self._engine.name,
            engine_artifacts=tuple(artifacts),
            config_hash=config_hash,
            run_timestamp=datetime.now(UTC),
            ix_version=__version__,
        )
        self._store.save_summary(config.name, results)
        return results

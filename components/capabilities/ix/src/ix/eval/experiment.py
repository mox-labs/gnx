"""Experiment: repeats × engine, then measurement, aggregation and persistence.

An engine executes every probe × trial of one repeat and returns the trials. The Experiment
measures each trial with its one rule (:func:`~ix.eval.measure.measure_trial` over the
configured sensors), whatever engine ran it. It also writes every trial to the run's
``trials.jsonl``, aggregates across repeats, computes the probe-sampling standard errors and
the across-repeat noise floor, and saves a summary under the subject's name.

Trials whose session never had a fair chance (harness faults) are recorded and counted, and
kept out of every score.

The engine, the agent factory and the store are injected by the composition root. This
module imports no concrete engine, runtime or flow.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ix import __version__
from ix.domain.models import ExperimentConfig, ExperimentResults, ProbeResult, TrialRecord
from ix.domain.ports import EngineRun
from ix.domain.types import Reading, Subject
from ix.eval.analysis import (
    aggregate_readings,
    build_confusion_matrix,
    compute_metrics,
    compute_noise_floor,
    standard_errors,
    unmeasured_probes,
)
from ix.eval.measure import measure_trial

if TYPE_CHECKING:
    from collections.abc import Callable

    from ix.domain.ports import AgentFactory, Engine, Sensor, Storage
    from ix.domain.types import Trial

DEFAULT_SUBJECT = "default"


class Experiment:
    """An experiment you run: ``run(config, subject)`` → ExperimentResults."""

    def __init__(
        self,
        *,
        sensor: Sensor,
        store: Storage,
        engine: Engine,
        agents: AgentFactory,
        seed: int | None = None,
        simulated: bool = False,
    ) -> None:
        self._sensor = sensor
        self._store = store
        self._engine = engine
        self._agents = agents
        # Provenance only: the agent factory already carries both. Recorded on the results
        # so a run can be reproduced, and told apart, from its own summary.
        self._seed = seed
        self._simulated = simulated

    async def run(
        self,
        config: ExperimentConfig,
        subject: Subject | None = None,
        on_probe_complete: Callable[[ProbeResult], None] | None = None,
        on_run_complete: Callable[[int, float], None] | None = None,
        on_trial: Callable[[int, Trial, list[Reading]], None] | None = None,
        save_as: str | None = None,
    ) -> ExperimentResults:
        """Run ``config.repeats`` repeats of one subject through the engine, then aggregate.

        Callbacks are for progress: ``on_trial(repeat, trial, readings)`` as each trial is
        measured, ``on_run_complete(repeat, pass_rate)`` after each repeat, and
        ``on_probe_complete`` once per probe with its result across all repeats.

        ``save_as`` is the name the results are recorded and saved under (default: the
        subject's name) — the CLI saves a simulated run of a real subject as
        ``<name>@simulated`` so it never replaces that subject's measured results.
        """
        active = subject or Subject(name=DEFAULT_SUBJECT)
        label = save_as or active.name
        probe_map = {p.id: p for p in config.probes}
        started = datetime.now(UTC)
        run_id = started.strftime("%Y%m%dT%H%M%S%fZ")

        all_readings: list[Reading] = []
        per_run_pass_rates: list[float] = []
        per_run_mean_scores: list[float] = []
        artifacts: list[str] = []
        families: set[str] = set()
        stops: dict[str, int] = {}
        trials_log = ""

        for run_idx in range(config.repeats):
            measure = _Memo(self._sensor)
            outcome = await self._engine.run(
                EngineRun(
                    experiment=config.name,
                    probes=config.probes,
                    subject=active,
                    agents=self._agents,
                    trials=config.trials,
                    measure=measure,
                    run_index=run_idx,
                    on_trial=_bind_repeat(on_trial, run_idx, measure),
                    sensor_name=self._sensor.name,
                )
            )
            readings = [r for trial in outcome.trials for r in measure(trial)]
            all_readings.extend(readings)
            families.update(f for t in outcome.trials if (f := getattr(t.response, "family", None)))
            for trial in outcome.trials:
                if trial.response is not None:
                    stop = str(getattr(trial.response, "stop", None) or "unreported")
                    stops[stop] = stops.get(stop, 0) + 1
            artifacts.extend(f"{k}:{v}" for k, v in outcome.artifacts.items())
            path = self._store.append_trials(
                config.name,
                label,
                run_id,
                _records(run_id, run_idx, outcome.trials, readings),
            )
            trials_log = str(path)

            run_metrics = compute_metrics(aggregate_readings(readings, probe_map))
            per_run_pass_rates.append(run_metrics["pass_rate"])
            per_run_mean_scores.append(run_metrics["mean_score"])
            if on_run_complete:
                on_run_complete(run_idx, run_metrics["pass_rate"])

        probe_results = aggregate_readings(all_readings, probe_map, on_probe_complete)
        metrics = compute_metrics(probe_results)
        pass_se, score_se = standard_errors(probe_results)

        # The probes are part of what was measured: a reworded prompt or a changed expectation
        # must change the hash, or two summaries with one hash measured different things.
        config_json = config.model_dump_json()
        config_hash = hashlib.sha256(config_json.encode()).hexdigest()[:16]

        results = ExperimentResults(
            experiment_name=config.name,
            subject=label,
            run_id=run_id,
            probe_results=tuple(probe_results),
            pass_rate=metrics["pass_rate"],
            mean_score=metrics["mean_score"],
            min_score=metrics["min_score"],
            max_score=metrics["max_score"],
            n_probes=len(probe_results),
            pass_rate_stderr=pass_se,
            mean_score_stderr=score_se,
            repeats=config.repeats,
            per_run_pass_rates=tuple(per_run_pass_rates),
            noise_floor_sd=compute_noise_floor(per_run_pass_rates),
            per_run_mean_scores=tuple(per_run_mean_scores),
            score_noise_floor_sd=compute_noise_floor(per_run_mean_scores),
            confusion_matrix=build_confusion_matrix(all_readings),
            sensor_faults=sum(1 for r in all_readings if r.fault == "sensor"),
            harness_faults=sum(1 for r in all_readings if r.fault == "harness"),
            unmeasured_probes=unmeasured_probes(all_readings),
            stops=stops,
            families=tuple(sorted(families)),
            engine=self._engine.name,
            engine_artifacts=tuple(artifacts),
            trials_log=trials_log,
            config_hash=config_hash,
            run_timestamp=started,
            ix_version=__version__,
            seed=self._seed,
            simulated=self._simulated,
        )
        self._store.save_summary(config.name, results)
        return results


class _Memo:
    """The experiment's measuring rule for one repeat, computed once per trial."""

    def __init__(self, sensor: Sensor) -> None:
        self._sensor = sensor
        self._done: dict[tuple[str, int], list[Reading]] = {}

    def __call__(self, trial: Trial) -> list[Reading]:
        key = (trial.probe_id, trial.trial_index)
        if key not in self._done:
            self._done[key] = measure_trial(self._sensor, trial)
        return self._done[key]


def _bind_repeat(
    on_trial: Callable[[int, Trial, list[Reading]], None] | None,
    run_index: int,
    measure: _Memo,
) -> Callable[[Trial], None] | None:
    if on_trial is None:
        return None

    def bound(trial: Trial) -> None:
        on_trial(run_index, trial, measure(trial))

    return bound


def _records(
    run_id: str, run_index: int, trials: list[Trial], readings: list[Reading]
) -> list[TrialRecord]:
    by_trial: dict[tuple[str, int], list[Reading]] = {}
    for reading in readings:
        by_trial.setdefault((reading.probe_id, reading.trial_index), []).append(reading)
    return [
        TrialRecord(
            run_id=run_id,
            run_index=run_index,
            probe_id=trial.probe_id,
            trial_index=trial.trial_index,
            response=_serialise(trial.response),
            error=trial.error,
            readings=tuple(by_trial.get((trial.probe_id, trial.trial_index), [])),
        )
        for trial in trials
    ]


def _serialise(response: Any) -> dict[str, Any] | None:
    if response is None:
        return None
    if isinstance(response, BaseModel):
        dumped: dict[str, Any] = response.model_dump(mode="json")
        return dumped
    return {"content": str(response)}

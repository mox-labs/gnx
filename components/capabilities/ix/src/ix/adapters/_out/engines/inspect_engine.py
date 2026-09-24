"""InspectEngine — one repeat of an ix experiment, run as an Inspect AI task.

The mapping, chosen so nothing about the experiment changes when the engine does:

======================  =================================================
ix                      Inspect AI
======================  =================================================
probe                   Sample (``input`` = prompt, ``id`` = probe id)
subject's agent         a solver that runs the ix agent and sets ``output``
sensor                  a scorer: ``measure_trial`` → Score + readings
trials                  epochs
the repeat's readings   read back out of the ``.eval`` log
======================  =================================================

The subject is run by ix's own agent factory — the same BoundAgent the native engine uses —
so a Claude SDK subject, a local-model subject and a simulated one all run unchanged. Inspect
contributes the log format, the log viewer (``inspect view``), sample-level transcripts, and
its limits machinery. ``model="none"``: Inspect's own model is never called.

Type URL: ``ix.v1/engine.inspect``. Requires the ``inspect`` extra.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from ix.domain.ports import EngineOutcome
from ix.domain.types import Reading, Trial
from ix.eval.measure import measure_trial, run_trial

if TYPE_CHECKING:
    from ix.domain.ports import EngineRun

SCORER_NAME = "ix_sensor"


class EngineError(RuntimeError):
    """The Inspect evaluation did not complete; carries the log location when there is one."""


class InspectEngineConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Where ``.eval`` logs go. Composition sets ``<lab>/<experiment>/results/inspect``.
    log_dir: str | None = None
    #: Concurrent samples. **1 by default**: the claude-sdk runtime mutates a process-global
    #: environment variable per call (matrix SECURITY.md M-3), so concurrent SDK subjects are
    #: unsafe. Raise it only for subjects on the model or mock runtimes.
    max_samples: int = Field(default=1, ge=1)
    #: Fail the whole evaluation on the first sample error. ix's own rule is that an errored
    #: trial is a failed reading, so the default keeps going.
    fail_on_error: bool = False


class InspectEngine:
    name = "inspect"

    def __init__(self, config: InspectEngineConfig | None = None) -> None:
        try:
            import inspect_ai  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "the inspect engine requires 'inspect-ai'. Install with: uv add 'ix[inspect]'"
            ) from e
        self._config = config or InspectEngineConfig()

    @property
    def config(self) -> InspectEngineConfig:
        return self._config

    async def run(self, run: EngineRun) -> EngineOutcome:
        from inspect_ai import Task, eval_async
        from inspect_ai.dataset import MemoryDataset, Sample
        from inspect_ai.model import ModelOutput
        from inspect_ai.scorer import Score, Target, mean, scorer
        from inspect_ai.solver import Generate, TaskState, solver

        # The live Trial objects, keyed by (probe id, trial index). Sensors read typed
        # responses (AgentResponse.tool_calls); a JSON round trip through the sample store
        # would hand them dicts. The store still gets the JSON, for the log.
        trials: dict[tuple[str, int], Trial] = {}

        @solver(name="ix_subject")
        def subject_solver() -> Any:
            async def solve(state: TaskState, generate: Generate) -> TaskState:
                trial_index = state.epoch - 1
                probe_id = str(state.sample_id)
                agent = run.agents(run.subject, trial_index, run.run_index)
                trial = await run_trial(agent, probe_id, state.input_text, trial_index)
                trials[(probe_id, trial_index)] = trial
                state.store.set("ix_trial", trial.model_dump(mode="json"))
                content = getattr(trial.response, "content", None) or trial.error or ""
                state.output = ModelOutput.from_content(
                    model=f"ix/{run.subject.name}", content=str(content)
                )
                state.completed = True
                return state

            return solve

        @scorer(name=SCORER_NAME, metrics=[mean()])
        def sensor_scorer() -> Any:
            async def score(state: TaskState, target: Target) -> Score:
                key = (str(state.sample_id), state.epoch - 1)
                trial = trials.get(key) or Trial(
                    probe_id=key[0], trial_index=key[1], error="solver produced no trial"
                )
                readings = measure_trial(run.sensor, trial)
                passed = bool(readings) and all(r.passed for r in readings)
                scores = [r.score for r in readings if r.score is not None]
                return Score(
                    value=sum(scores) / len(scores) if scores else (1.0 if passed else 0.0),
                    explanation="; ".join(r.details for r in readings if r.details),
                    metadata={
                        "passed": passed,
                        "readings": [r.model_dump(mode="json") for r in readings],
                    },
                )

            return score

        task = Task(
            dataset=MemoryDataset(
                [Sample(input=p.prompt, id=p.id, metadata=dict(p.metadata)) for p in run.probes]
            ),
            solver=subject_solver(),
            scorer=sensor_scorer(),
            epochs=run.trials,
            name=f"ix-{run.experiment}",
            metadata={
                "ix_experiment": run.experiment,
                "ix_subject": run.subject.name,
                "ix_sensor": run.sensor.name,
                "ix_run_index": run.run_index,
            },
        )
        logs = await eval_async(
            task,
            model="none",
            log_dir=self._config.log_dir,
            max_samples=self._config.max_samples,
            fail_on_error=self._config.fail_on_error,
        )
        log = logs[0]
        if log.status != "success":
            raise EngineError(
                f"Inspect evaluation {log.status}: {getattr(log.error, 'message', log.error)} "
                f"(log: {log.location})"
            )

        readings: list[Reading] = []
        for sample in log.samples or []:
            score = (sample.scores or {}).get(SCORER_NAME)
            if score is None or not score.metadata:
                readings.append(
                    Reading(
                        sensor_name=run.sensor.name,
                        probe_id=str(sample.id),
                        trial_index=sample.epoch - 1,
                        passed=False,
                        score=0.0,
                        details=f"no score recorded: {getattr(sample.error, 'message', '')}",
                    )
                )
                continue
            readings.extend(Reading.model_validate(r) for r in score.metadata["readings"])
        return EngineOutcome(readings=readings, artifacts={"inspect_log": str(Path(log.location))})

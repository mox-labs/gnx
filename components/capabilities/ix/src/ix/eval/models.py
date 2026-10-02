"""Eval domain models — frozen Pydantic value objects for evaluation.

Experiment definition and result types.
Agent response types (AgentResponse) live in Matrix.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, computed_field

from ix.domain.types import Probe, Subject

# --- Expectation Constants ---
# Legacy: used by activation sensor experiments.
MUST_TRIGGER = "must_trigger"
SHOULD_NOT_TRIGGER = "should_not_trigger"
ACCEPTABLE = "acceptable"


class ExperimentConfig(BaseModel, frozen=True):
    """Experiment definition — loaded from experiment.yaml + tasks/*.md + subjects/*.md.

    ``sensors`` is a list of config dicts, each with a ``type``::

      sensors:
        - type: activation
          expected_skill: build-eval
        - type: function-test

    A single ``sensor`` dict is normalised to a one-element ``sensors`` list.

    ``engine`` selects how a repeat is executed — ``native`` (a matrix DAG per trial, the
    default) or ``inspect`` (an Inspect AI task per repeat) — plus that engine's options.

    ``models`` is a hardline registry section (``{default?, models: {...}}``) for subjects on
    the ``model`` runtime and for judge-backed sensors. ``None`` = hardline's own tiers.
    """

    name: str
    description: str = ""
    subjects: tuple[Subject, ...] = ()
    sensor: dict[str, Any] = {}
    sensors: tuple[dict[str, Any], ...] = ()
    engine: dict[str, Any] = {"type": "native"}
    models: dict[str, Any] | None = None
    trials: int = 5
    repeats: int = 1
    probes: tuple[Probe, ...] = ()

    def __init__(self, **data: Any) -> None:
        # Normalize: sensor (single) → sensors (list)
        if not data.get("sensors") and data.get("sensor"):
            data["sensors"] = (data["sensor"],)
        elif not data.get("sensors") and not data.get("sensor"):
            data["sensors"] = ({"type": "activation"},)
        # Normalize: engine: inspect → engine: {type: inspect}
        if isinstance(data.get("engine"), str):
            data["engine"] = {"type": data["engine"]}
        super().__init__(**data)


class TrialRecord(BaseModel, frozen=True):
    """One trial of one probe: observation + sensor reading."""

    probe_id: str
    trial: int
    observation: Any = None
    reading: Any = None


class ProbeResult(BaseModel, frozen=True):
    """Aggregated result across all trials for one probe."""

    probe_id: str
    score: float
    passed: bool
    trial_scores: tuple[float, ...] = ()
    details: tuple[str, ...] = ()


class ExperimentResults(BaseModel, frozen=True):
    """Complete results for one experiment run.

    Status is derived from pass_rate — no separate interpretation layer.
    Provenance fields trace results back to the config that produced them.
    """

    experiment_name: str
    subject: str = ""
    probe_results: tuple[ProbeResult, ...] = ()

    # Summary metrics
    pass_rate: float = 0.0
    mean_score: float = 0.0
    min_score: float = 0.0
    max_score: float = 0.0

    # Multi-run stats (populated when repeats > 1)
    repeats: int = 1
    per_run_pass_rates: tuple[float, ...] = ()
    noise_floor_sd: float | None = None
    confusion_matrix: dict[str, dict[str, int]] = {}

    # Provenance — trace results to their source
    engine: str = "native"
    #: Engine-specific records a reader can open, e.g. ``inspect_log:<path>`` per repeat.
    engine_artifacts: tuple[str, ...] = ()
    config_hash: str = ""
    run_timestamp: datetime | None = None
    ix_version: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> str:
        """Status derived from pass rate. The numbers speak."""
        if self.pass_rate >= 1.0:
            return "excellent"
        if self.pass_rate >= 0.85:
            return "good"
        if self.pass_rate >= 0.50:
            return "needs_work"
        return "poor"

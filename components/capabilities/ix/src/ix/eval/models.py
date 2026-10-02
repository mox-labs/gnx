"""Eval domain models — frozen Pydantic value objects for evaluation.

Experiment definition, per-trial records, per-subject results, and the comparison of two
subjects' results. Agent response types (AgentResponse) live in matrix.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from ix.domain.types import Probe, Reading, Subject

# Activation expectations a probe declares in its frontmatter.
MUST_TRIGGER = "must_trigger"
SHOULD_NOT_TRIGGER = "should_not_trigger"
ACCEPTABLE = "acceptable"


class ExperimentConfig(BaseModel):
    """Experiment definition — loaded from experiment.yaml + tasks/*.md + subjects/*.md.

    ``sensors`` is a list of config dicts, each with a ``type``::

      sensors:
        - type: activation
          expected_skill: build-eval
        - type: function-test

    ``sensor`` (one dict, or a bare type name) is shorthand for a one-element ``sensors``.
    With neither, the experiment measures activation.

    ``engine`` selects how a repeat is executed — ``native`` (a matrix DAG per trial, the
    default) or ``inspect`` (an Inspect AI task per repeat) — plus that engine's options; a
    bare name is shorthand for ``{type: <name>}``.

    ``models`` is a hardline registry section (``{default?, models: {...}}``) for subjects on
    the ``model`` runtime and for judge-backed sensors. ``None`` = hardline's own tiers.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str = ""
    subjects: tuple[Subject, ...] = ()
    sensors: tuple[dict[str, Any], ...] = ()
    engine: dict[str, Any] = {"type": "native"}
    models: dict[str, Any] | None = None
    trials: int = Field(default=5, ge=1)
    repeats: int = Field(default=1, ge=1)
    probes: tuple[Probe, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _shorthands(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        sensor = data.pop("sensor", None)
        if sensor is not None and data.get("sensors"):
            raise ValueError("give either 'sensor' or 'sensors', not both")
        if sensor is not None:
            data["sensors"] = ({"type": sensor} if isinstance(sensor, str) else sensor,)
        elif not data.get("sensors"):
            data["sensors"] = ({"type": "activation"},)
        if isinstance(data.get("engine"), str):
            data["engine"] = {"type": data["engine"]}
        return data

    def subject(self, name: str) -> Subject:
        for subject in self.subjects:
            if subject.name == name:
                return subject
        from ix.domain.errors import ConfigError

        known = ", ".join(s.name for s in self.subjects) or "(none)"
        raise ConfigError(f"no subject named {name!r} in {self.name}. Subjects: {known}")


class TrialRecord(BaseModel):
    """One trial of one probe in one repeat: what the subject did and what was measured.

    Written one JSON line per trial to ``results/<subject>/<run_id>/trials.jsonl`` — the
    evidence an aggregate is computed from, kept so a surprising number can be audited.
    """

    model_config = ConfigDict(frozen=True)

    run_id: str
    run_index: int
    probe_id: str
    trial_index: int
    #: The agent's response, serialised (content, tool calls, usage, family, model).
    response: dict[str, Any] | None = None
    error: str | None = None
    readings: tuple[Reading, ...] = ()


class ProbeResult(BaseModel, frozen=True):
    """Aggregated result across all trials for one probe."""

    probe_id: str
    score: float
    passed: bool
    trial_scores: tuple[float, ...] = ()
    details: tuple[str, ...] = ()


class ExperimentResults(BaseModel, frozen=True):
    """Complete results for one subject's run of an experiment.

    Uncertainty comes in two kinds, reported separately:

    * ``pass_rate_stderr`` / ``mean_score_stderr`` — sampling error over the probes
      (CLT standard error; ``stderr_method`` names it). How much the number would move
      with a different draw of probes like these.
    * ``noise_floor_sd`` / ``score_noise_floor_sd`` — the spread of pass rate / mean score
      across repeats. How much the number moves when nothing changes but the run.

    A difference between two subjects means something only when it is larger than both.
    """

    experiment_name: str
    subject: str = ""
    run_id: str = ""
    probe_results: tuple[ProbeResult, ...] = ()

    pass_rate: float = 0.0
    mean_score: float = 0.0
    min_score: float = 0.0
    max_score: float = 0.0
    n_probes: int = 0
    pass_rate_stderr: float | None = None
    mean_score_stderr: float | None = None
    stderr_method: str = "clt-over-probes"

    repeats: int = 1
    per_run_pass_rates: tuple[float, ...] = ()
    noise_floor_sd: float | None = None
    per_run_mean_scores: tuple[float, ...] = ()
    score_noise_floor_sd: float | None = None
    confusion_matrix: dict[str, dict[str, int]] = {}

    # Provenance — trace results to their source
    #: The model families that answered, read off the responses (``simulated`` for the
    #: simulator, ``mock`` for matrix's mock). Data, not config: a subject named "live" that
    #: ran under --simulate says ``("simulated",)`` here.
    families: tuple[str, ...] = ()
    engine: str = "native"
    #: Engine-specific records a reader can open, e.g. ``inspect_log:<path>`` per repeat.
    engine_artifacts: tuple[str, ...] = ()
    #: Where this run's trial records are, relative to the experiment directory.
    trials_log: str = ""
    config_hash: str = ""
    run_timestamp: datetime | None = None
    ix_version: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measured_a_model(self) -> bool:
        """False when no real model answered — a harness check, not a measurement."""
        return bool(self.families) and not set(self.families) <= {"simulated", "mock"}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> str:
        """A coarse label of the pass rate, for a glance; the numbers are the result."""
        if self.pass_rate >= 1.0:
            return "excellent"
        if self.pass_rate >= 0.85:
            return "good"
        if self.pass_rate >= 0.50:
            return "needs_work"
        return "poor"


class ProbeDelta(BaseModel, frozen=True):
    probe_id: str
    score_a: float
    score_b: float
    passed_a: bool
    passed_b: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def delta(self) -> float:
        return self.score_b - self.score_a


class Comparison(BaseModel, frozen=True):
    """Subject B against subject A, paired probe by probe.

    ``mean_delta`` is the mean over shared probes of (score B − score A); its standard error
    is the SD of those paired differences over √n — pairing removes the variance the two
    subjects share through the probes. ``ci95`` is mean ± 1.96·SE (normal approximation;
    with few probes it is optimistic, and ``n`` is reported so a reader can judge).
    ``a_only_passed`` / ``b_only_passed`` count probes whose verdict flipped.

    ``noise_floor_sd`` is on the same scale as ``mean_delta``: the across-repeat spread of
    mean score, the larger of the two subjects' where measured (``repeats > 1``).
    """

    experiment: str
    a: str
    b: str
    n: int
    pass_rate_a: float
    pass_rate_b: float
    mean_delta: float
    delta_stderr: float | None
    ci95: tuple[float, float] | None
    a_only_passed: int
    b_only_passed: int
    #: Across-repeat SD of mean score — the larger of the subjects' that measured one.
    noise_floor_sd: float | None
    unmatched: tuple[str, ...] = ()
    #: Set when either side was answered by no real model (simulator or mock).
    warning: str | None = None
    probes: tuple[ProbeDelta, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def verdict(self) -> str:
        """``b_better`` / ``a_better`` only when the CI of the mean score delta excludes 0
        *and* the delta is larger than the run-to-run noise floor where one was measured;
        otherwise ``inconclusive``. Never a guess on thin data."""
        if self.ci95 is None:
            return "inconclusive"
        low, high = self.ci95
        clears_noise = self.noise_floor_sd is None or abs(self.mean_delta) > self.noise_floor_sd
        if low > 0 and clears_noise:
            return "b_better"
        if high < 0 and clears_noise:
            return "a_better"
        return "inconclusive"

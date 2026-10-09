# Domain Models

ix has a two-layer type system. Core types (`ix.domain`) are domain-agnostic — they express any experiment. Eval types (`ix.eval`) are eval-specific vocabulary that flows through the core as `Any`.

All models are frozen Pydantic `BaseModel` unless otherwise noted. Protocols use `typing.Protocol` with `@runtime_checkable`.

---

## Core Types

**Import path**: `ix.domain.types`

### `Subject`

A Subject Under Test — one variant being compared.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | required | Identifier for this subject |
| `description` | `str` | `""` | Human-readable description |
| `config` | `dict` | `{}` | Adapter-specific configuration |

### `Trial`

One execution of a probe against a subject. Pairs probe identity with the response (or error). The sensor gets `trial.response`; analysis uses `trial.probe_id` to look up ground truth.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `probe_id` | `str` | required | Which probe was executed |
| `trial_index` | `int` | required | Which trial this belongs to (0-indexed) |
| `response` | `Any \| None` | `None` | What the SUT returned |
| `error` | `str \| None` | `None` | Error message if invocation failed |

### `Reading`

Result of a sensor evaluating a single interaction. Sensors produce readings like instruments.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `sensor_name` | `str` | required | Name of the sensor that produced this reading |
| `passed` | `bool` | required | Whether this interaction passed the sensor's criteria |
| `score` | `float \| None` | `None` | Numeric score (sensor-defined semantics) |
| `metrics` | `dict` | `{}` | Additional numeric measurements |
| `details` | `str` | `""` | Human-readable explanation |
| `fault` | `"subject" \| "sensor" \| None` | `None` | Set when the sensor never judged the response: the trial errored (`subject`) or the sensor raised (`sensor`) |

---

## Core Protocols

**Import path**: `ix.domain.ports`

All protocols are `@runtime_checkable`. Implement the methods — no base classes, no inheritance.

### `Sensor`

Measures a trial and produces readings.

| Member | Signature | Description |
|--------|-----------|-------------|
| `name` | `@property -> str` | Sensor identifier (appears in `Reading.sensor_name`) |
| `measure` | `(trial: Trial) -> list[Reading]` | Measures one trial |

### `AgentFactory` (Protocol)

`(subject: Subject, trial_index: int, run_index: int = 0) -> matrix.Agent`. Built by the
composition root: splits the subject's config into a matrix `AgentDefinition` (`system_prompt`,
`model`, `tools`, `max_turns`) and a runtime (`runtime.type` + options), and binds them.
Seeded simulated runtimes draw per `(run_index, trial_index)`, so repeats are independent.

### `Engine` (Protocol)

`name: str`; `async run(run: EngineRun) -> EngineOutcome`. Executes one repeat — every probe ×
trial — and returns `EngineOutcome(readings, trials, artifacts)`: `trials` is every `Trial` the
repeat ran, response or error included — what `readings` measured, and what `Storage` persists.
`EngineRun` carries `experiment`, `probes`, `subject`, `sensor`, `agents` (an AgentFactory),
`trials`, `run_index`, and an optional `on_trial(trial, readings)` an engine calls as each trial
is measured — progress only; the outcome stays the source of truth.

### `Storage` (Protocol)

**Import path**: `ix.domain.ports`

Persistence boundary: experiments in, trial records and per-subject summaries out.

| Method | Signature | Description |
|--------|-----------|-------------|
| `load_experiment` | `(path: Path) -> ExperimentConfig` | Load an experiment directory |
| `list_experiments` | `(base: Path) -> list[Path]` | Enumerate experiment directories |
| `append_trials` | `(experiment: str, subject: str, run_id: str, records: list[TrialRecord]) -> Path` | Append this run's trial records; returns the `trials.jsonl` path |
| `save_summary` | `(experiment: str, results: ExperimentResults) -> Path` | Write the subject's summary (archived and `-latest`) |
| `load_summary` | `(experiment: str, subject: str) -> ExperimentResults` | Load one subject's latest summary |
| `subjects_with_results` | `(experiment: str) -> list[str]` | Subject names that have a summary |

| engine | type URL | executes a repeat as |
|---|---|---|
| native | `ix.v1/engine.native` | one four-node matrix DAG per probe × trial; `concurrency` (default 1) bounds trials in flight, results stay in probe × trial order |
| inspect | `ix.v1/engine.inspect` | one Inspect AI task: probes → samples, trials → epochs, subject → solver, sensor → scorer; `artifacts["inspect_log"]` is the `.eval` path |

---

## Eval Models

**Import path**: `ix.eval.models`. Field-by-field tables for the persisted shapes are in
[Experiment Format](experiment-format.md); this is what each type is for.

| Type | What it is |
|------|------------|
| `ExperimentConfig` | An experiment as loaded: name, subjects, probes, `sensors`, `engine`, `models`, `trials`, `repeats`. `extra="forbid"`; `sensor` (one) and `engine` (a name) are shorthands, and giving both `sensor` and `sensors` is an error. `subject(name)` looks one up or raises `ConfigError` naming the others. |
| `TrialRecord` | One trial of one probe in one repeat — `run_id`, `run_index`, `probe_id`, `trial_index`, the serialised `response` or the `error`, and its `readings`. One JSON line each in `trials.jsonl`. |
| `ProbeResult` | One probe aggregated over its trials: mean `score`, `passed` (a majority of trials passed, by the sensor's verdict), `trial_scores`, `details` (at most 3 distinct strings). |
| `ExperimentResults` | One subject's run: pass rate and mean score with their standard errors over probes, the across-repeat noise floors, the confusion matrix, `sensor_faults` (readings the sensor crashed on, scored as failures), the `families` that answered (`measured_a_model` is false for the simulator or mock), provenance (`run_id`, `engine`, `trials_log`, `config_hash` over config and probes, `seed`, `simulated`), and a `status` that is `unmeasured` when no real model answered. |
| `Comparison` | Subject B against A (`run_id_a`, `run_id_b` name the two runs), paired by probe: `mean_delta` with its SE and 95% CI, verdict flips, the score noise floor, `unmatched` probes, a `warning` when either side measured no model or had sensor faults, and a `verdict` of `b_better` / `a_better` / `inconclusive` — always `inconclusive` when `sensor_faults` > 0. |

Activation expectations a probe can declare: `must_trigger`, `should_not_trigger`,
`acceptable` (constants `MUST_TRIGGER`, `SHOULD_NOT_TRIGGER`, `ACCEPTABLE`).

---

## DAG Topology

Inner DAG (per probe × trial):

```
ProbeNode ──┐
            ├──▶ TrialNode ──▶ SensorNode
SubjectNode ┘

ProbeNode:   requires: ∅                          provides: ix.v1/probe.stimulus    (Probe)
SubjectNode: requires: ∅                          provides: ix.v1/subject           (Subject)
TrialNode:   requires: {probe.stimulus, subject}  provides: ix.v1/trial.observation (Trial)
SensorNode:  requires: {trial.observation}        provides: ix.v1/sensor.readings   (list[Reading])
```

The native engine runs this DAG per probe × trial; reads are declared and enforced by matrix.
The Experiment runs `repeats` repeats through the engine, then aggregates. Status is derived
from pass_rate via computed property.

All components resolve through one `ComponentRegistry` by type URL: `matrix.v1/runtime.*`
(includes matrix's own `mock`), `ix.v1/runtime.simulated` (ix's simulator), `ix.v1/sensor.*`,
`ix.v1/engine.*`, plus `matrix.components` and `ix.components` entry points.

## Type Flow

```
Probe (stimulus) + Subject (definition fields + runtime)
    |
AgentFactory: AgentDefinition + registry.create("matrix.v1/runtime.<type>", options) → BoundAgent
              BoundAgent.run(prompt) → AgentResponse (content, tool_calls, usage, family)
    |
Trial(probe_id, trial_index, response, error)
    |
Sensor.measure(trial) → list[Reading]

Reading(sensor_name, probe_id, trial_index, passed, score, metrics, details)
    | Storage.append_trials() → TrialRecord(run_id, run_index, probe_id, trial_index,
    |                                        response, error, readings) per trial, to trials.jsonl
    | aggregate_readings()
ProbeResult(probe_id, score, passed, trial_scores)
    | compute_metrics() + standard_errors() + compute_noise_floor() per repeat
ExperimentResults(experiment_name, subject, run_id, probe_results, pass_rate, mean_score,
                   min_score, max_score, n_probes, pass_rate_stderr, mean_score_stderr,
                   noise_floor_sd, families, ...)
    .status → computed from pass_rate
    .measured_a_model → computed from families
```

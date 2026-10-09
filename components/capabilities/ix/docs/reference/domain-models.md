# Domain models

The values, ports and models in `ix.domain`, and how the composition root wires them. Values
are frozen pydantic models; ports are `typing.Protocol`s, so an implementation needs no base
class.

---

## Values: `ix.domain.types`

### `Probe`

The stimulus.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `str` | required | the join key for every trial and reading |
| `prompt` | `str` | required | sent to the subject verbatim |
| `metadata` | `dict` | `{}` | ground truth; read by sensors by the keys they declare |

### `Subject`

One variant under test.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | required | results are saved under it |
| `description` | `str` | `""` | |
| `config` | `dict` | `{}` | `agent: <name>`, or an inline definition and `runtime`; validated as `SubjectSpec` |

### `Trial`

One probe put to one subject once.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `probe_id` | `str` | required | |
| `trial_index` | `int` | required | 0-based, within the repeat |
| `response` | `Any` | `None` | matrix's `AgentResponse` when the session completed |
| `error` | `str \| None` | `None` | set when the session failed |
| `error_reason` | `str \| None` | `None` | why the session failed: the runtime's classified reason (`rate_limited`, `auth`, `failed`, ...), or ix's own (`timeout`, `engine`, `setup`, `unclassified`); decides whose fault the failure is |

### `Reading`

What a sensor concluded about one trial.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `sensor_name` | `str` | required | |
| `probe_id`, `trial_index` | `str`, `int` | required | the trial it measured |
| `passed` | `bool` | required | the sensor's verdict; aggregation counts it and never re-derives it |
| `score` | `float \| None` | `None` | when absent, aggregation uses 1.0 or 0.0 from `passed` |
| `metrics` | `dict` | `{}` | decomposed measurements (`tests_passed`, `expected_skill`, `judge_family`, ...) |
| `details` | `str` | `""` | a human-readable reason |
| `fault` | `"subject" \| "harness" \| "sensor" \| None` | `None` | set when the response was never judged; see below |

| `fault` | Set when | Scored? |
|---|---|---|
| `subject` | the session failed on the agent's account: the runtime said `failed` or `refused` (or a reason of its own beyond the harness list) | yes, as a failure |
| `harness` | `unavailable`, `rate_limited`, `timeout`, `auth`, `incapable` from the runtime; `timeout` from `trial_timeout_s`; `engine` (the engine lost the trial); `setup` (the subject's agent could not be built); `unclassified` (the runtime raised something other than `AgentRuntimeError`) | no; counted in `harness_faults` |
| `sensor` | the sensor raised | yes, as a failure; counted in `sensor_faults` |

The rule lives in `ix.eval.measure`: `HARNESS_REASONS`, `fault_of(trial)` and
`measure_trial(sensor, trial)`. `run_trial(agent, probe_id, prompt, trial_index, *,
timeout_s=None)` puts one probe to an agent and turns a failure into an errored Trial: an
`AgentRuntimeError` keeps its reason, any other exception is `unclassified`, and a session
past `timeout_s` is `timeout`.

---

## Ports: `ix.domain.ports`

### `Sensor`

| Member | Signature | Description |
|--------|-----------|-------------|
| `name` | `str` (attribute or property) | appears in `Reading.sensor_name` |
| `measure` | `(trial: Trial) -> list[Reading]` | judge one trial; called only for trials with a response |

### `SensorClass`

What ix's built-in sensors are: a class with `Config` (its pydantic config model),
`truth_keys: frozenset[str]` (the probe keys it reads) and
`from_config(config, probes, *, judge=None) -> Sensor`. ix wraps each in a registry build
function with `ix.composition.builtins.sensor_builder`. A third-party sensor does not have to
follow this shape; it registers any build function (see Registry entries).

### `AgentFactory`

`(subject: Subject, trial_index: int, run_index: int = 0) -> matrix.Agent`. Built by
`ix.composition.make_agent_factory`. A subject naming an agent gets that agent's definition,
overridden by the subject's own fields, and its runtime. An inline subject gets its own
definition (one turn by default) on a runtime built once per subject from `runtime.type`.
On the native engine, a subject whose agent cannot be built still yields a Trial, with
`error_reason: setup`; on the Inspect engine the sample fails and the trial is filled in with
`error_reason: engine`. Either way it is a harness fault and earlier trials are kept.
Under `--simulate`, every subject gets a fresh simulator seeded per repeat and trial, so
repeats of a seeded run differ.

### `Engine`

| Member | Signature | Description |
|--------|-----------|-------------|
| `name` | `str` | recorded in `ExperimentResults.engine` |
| `run` | `async (run: EngineRun) -> EngineOutcome` | execute one repeat |

An engine executes and never judges. If it needs a score while it runs (Inspect's scorer
does), it calls `run.measure`, the experiment's own rule. It must return exactly one Trial
per probe × trial index, in probe × trial order; a failed session is a Trial with an error,
never a missing one.

`EngineRun` (frozen dataclass):

| Field | Type | Description |
|-------|------|-------------|
| `experiment` | `str` | the experiment's name |
| `probes` | `tuple[Probe, ...]` | |
| `subject` | `Subject` | |
| `agents` | `AgentFactory` | builds the subject's agent for a trial |
| `trials` | `int` | trials per probe |
| `measure` | `(Trial) -> list[Reading]` | the experiment's measuring rule, memoised per repeat |
| `run_index` | `int` | which repeat, 0-based |
| `on_trial` | `(Trial) -> None \| None` | call as each trial completes; progress only |
| `sensor_name` | `str` | the sensors' combined name, for engines that label their own logs |

`EngineOutcome` (frozen dataclass):

| Field | Type | Description |
|-------|------|-------------|
| `trials` | `list[Trial]` | one per probe × trial index |
| `artifacts` | `dict[str, str]` | records a reader can open, e.g. `{"inspect_log": "<path>"}` |

### `Storage`

| Method | Signature | Description |
|--------|-----------|-------------|
| `load_experiment` | `(path: Path) -> ExperimentConfig` | load an experiment directory |
| `list_experiments` | `(base: Path) -> list[Path]` | experiment directories under a lab |
| `append_trials` | `(experiment, subject, run_id, records: list[TrialRecord]) -> Path` | append to the run's `trials.jsonl`; returns its path relative to the experiment |
| `save_summary` | `(experiment, results: ExperimentResults) -> Path` | write `summary-<run_id>.json` and `summary-latest.json` |
| `load_summary` | `(experiment, subject) -> ExperimentResults` | a subject's latest summary |
| `subjects_with_results` | `(experiment) -> list[str]` | subjects that have a summary |

`FilesystemStore` (`ix.adapters._out.filesystem_store`) is the implementation.

---

## Models: `ix.domain.models`

Field-by-field tables for the persisted shapes are in [Experiment format](experiment-format.md).

| Type | What it is |
|------|------------|
| `ExperimentConfig` | an experiment as loaded: `name`, `description`, `subjects`, `sensors`, `engine`, `models`, `trials`, `repeats`, `probes`. `extra="forbid"`. `sensor` (one) and a bare `engine` name are shorthands. `subject(name)` returns one or raises `NotFoundError` listing the others. |
| `TrialRecord` | one line of `trials.jsonl`: `run_id`, `run_index`, `probe_id`, `trial_index`, the serialised `response` or the `error`, and the `readings` |
| `ProbeResult` | one probe over its non-harness trials: mean `score`, `passed` (a majority passed), `trial_scores`, up to 3 distinct `details` |
| `ExperimentResults` | one subject's run: rates and their standard errors, noise floors, confusion matrix, `sensor_faults`, `harness_faults`, `unmeasured_probes`, `stops`, `families`, provenance; computed `measured_a_model` and `status` |
| `ProbeDelta` | one shared probe in a comparison: both scores and verdicts, computed `delta` |
| `Comparison` | B against A paired by probe: `mean_delta`, `delta_stderr`, `ci95`, flips, `noise_floor_sd`, both fault counts, `both_measured`, `warning`, computed `verdict` |

Activation expectations: `MUST_TRIGGER`, `SHOULD_NOT_TRIGGER`, `ACCEPTABLE`.

Errors (`ix.domain.errors`), all subclasses of `IxError`: `ConfigError`, `NotFoundError`,
`LabNotFoundError`, `MissingExtraError` (an optional extra is not installed),
`EngineError` (an engine could not finish a repeat), `ResultsError`, `ResultsNotFoundError`.
Each carries a `kind` from the vocabulary matrix and hardline share (`config`, `not_found`,
`transient`, `auth`, `unknown`, ...) and an optional `fix`. `from_matrix(error, context)`
wraps a matrix error with ix's context and keeps its kind and fix. The CLI maps the kind to
the exit code, so a rate-limited runtime exits 5 and refused credentials exit 6.

---

## The experiment: `ix.eval.experiment.Experiment`

`Experiment(sensor=, store=, engine=, agents=, seed=None, simulated=False)`;
`await experiment.run(config, subject=None, on_probe_complete=, on_run_complete=, on_trial=,
save_as=)` returns `ExperimentResults` and saves them.

Per repeat it calls `engine.run(EngineRun(...))`, measures every returned trial with the
memoised rule, and appends the trial records. After the last repeat it aggregates
(`ix.eval.analysis`: `aggregate_readings`, `compute_metrics`, `standard_errors`,
`compute_noise_floor`, `unmeasured_probes`, `build_confusion_matrix`) and saves the summary.
`compare_results(a, b)` builds a `Comparison`. The module imports no engine, runtime or flow;
`ix.composition.create_service` wires them.

---

## Registry entries and type URLs

ix declares two points on matrix's registry, `sensor` and `engine`, and registers its
built-ins there from `ix.composition.builtins:register`, through the `matrix.extensions`
entry point. Type URLs use the dotted grammar `<namespace>.v<major>.<kind>.<name>`.

| Point | Type URL | Config | Needs | Effects |
|---|---|---|---|---|
| sensor | `ix.v1.sensor.activation` | `ActivationSensorConfig` | `probes`, `judge` | none |
| sensor | `ix.v1.sensor.function-test` | `FunctionTestSensorConfig` | `probes`, `judge` | unknown (it runs the subject's untrusted code) |
| sensor | `ix.v1.sensor.tool-usage` | `ToolUsageSensorConfig` | `probes`, `judge` | none |
| sensor | `ix.v1.sensor.outcome` | `OutcomeSensorConfig` | `probes`, `judge` | unknown |
| sensor | `ix.v1.sensor.deepeval` | `DeepEvalSensorConfig` | `probes`, `judge` | `model`, `network` |
| engine | `ix.v1.engine.native` | `NativeEngineConfig` | `observers` | unknown |
| engine | `ix.v1.engine.inspect` | `InspectEngineConfig` | `results_dir` | `filesystem` |
| payload-type | `ix.v1.probe` | `Probe` | | |
| payload-type | `ix.v1.trial` | `Trial` | | |

Config names a built-in by its short name (`activation` means `ix.v1.sensor.activation`) and
anything else by its full type URL. `matrix catalog --point sensor` lists what is installed;
`matrix describe <type URL>` prints one entry.

What composition provides:

- a **sensor** build may declare `probes` (the experiment's probes) and `judge`
  (`judge(name) -> matrix.Agent`: the configured agent of that name, else that model on
  matrix's `model` runtime). `build.truth_keys`, when set, names the probe keys it reads.
- an **engine** build may declare `observers` (matrix's configured observers) and
  `results_dir` (the experiment's results directory; Inspect's default `log_dir` is
  `inspect/` under it).

Payload types of the native engine's flow: `ix.v1.probe`, `ix.v1.trial-index`, `ix.v1.trial`.
ix registers the schemas of the first and last, and the flow validates its probe and trial
values against them. The flow, named `<experiment>.trial`, has one member, `TrialStep`, which requires `probe` and
`index` and provides `trial`. It is compiled once per repeat and run once per probe × trial.

`ix.domain.type_urls` builds these: `sensor(kind)`, `engine(kind)`, `schema(document)`
(`results` gives `ix.v1.results`), and `short(url)`.

## Type flow

```
Probe + Subject
   │  AgentFactory: the subject's definition bound to its runtime → matrix Agent
   ▼
engine: run_trial(agent, probe) per probe × trial → Trial(response | error, error_reason)
   │  experiment: measure_trial(sensor, trial)
   ▼
Reading(passed, score, metrics, details, fault)
   │  Storage.append_trials → TrialRecord per trial, in trials.jsonl
   │  aggregate_readings (harness faults left out) → ProbeResult per probe
   ▼
ExperimentResults (rates, standard errors, noise floors, faults, families, provenance)
   │  compare_results(A, B)
   ▼
Comparison (mean_delta, ci95, noise floor, faults → verdict)
```

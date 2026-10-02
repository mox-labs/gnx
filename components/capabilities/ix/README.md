# ix

Intelligent Experimentation — evals, benchmarks and QoS experiments for AI agents, composed
from config.

```bash
uv add "ix[inspect] @ git+https://github.com/mox-labs/gnx#subdirectory=components/capabilities/ix"
# extras: [inspect] the Inspect AI engine · [deepeval] DeepEval metrics as sensors
```

## What this is

An **experiment** is a directory: `experiment.yaml`, a `tasks/` folder of probes, and
optionally `subjects/`. ix runs each probe against a subject for some number of trials, hands
every response to a **sensor** that grades it, and repeats the whole run to measure its own
noise. Sensors are the instruments — activation, function-test, tool-usage, outcome,
deepeval — and the sensor is the grader. Aggregation only counts.

The vocabulary is domain-neutral: probe, subject, trial, reading. Nothing about "eval" is baked
into the core types, because the same shapes express a benchmark or a load test.

Two things ix is built to protect against, both learned the hard way:

**A simulated run measures the harness, not the thing.** `--mock` proves the pipeline, store
and aggregation work end to end without an API call. It tells you nothing about whether a
catalog routes or a model can code. Those are different claims and ix keeps them separate.

**A wrong verdict is worse than a crash**, because a crash gets noticed. Aggregation takes the
sensor's `passed` rather than re-deriving one from the score. An errored trial is a failed
reading, never a missing one.

`repeats` exists for the same reason: one run's pass rate has no error bar. The reported
noise floor — the spread of pass rates across repeats — is what says whether a difference
between two subjects is real. Seeded simulated runs draw independently per repeat, so the
noise floor is measured rather than zero by construction.

### What it is not

ix does not sandbox. `FunctionTestSensor` imports and runs model-generated Python **in this
process**, which is the intended behaviour of a code benchmark and is written down in
`SECURITY.md` rather than left implicit.

## Composition

Everything an experiment uses is named in its config and resolved by type URL through one
registry — matrix's agent runtimes, ix's sensors and engines, and any installed extension.

**A subject is an agent definition plus the runtime that plays it.** The definition fields
(`system_prompt`, `model`, `tools`, `max_turns`) describe the agent; `runtime` says where it
runs. Moving a subject from the Claude SDK to a local model is a one-line change.

```yaml
name: local-codegen
engine: inspect                    # native (matrix DAG per trial) | inspect (Inspect AI task)
models:                            # a hardline registry, for model-runtime subjects and judges
  default: qwen3-8b
  models:
    qwen3-8b: {backend: openai-compat, base_url: "http://127.0.0.1:8080/v1",
               model: mlx-community/Qwen3-8B-4bit, family: qwen, local: true}
subjects:
  - name: local
    config:
      system_prompt: Reply with only a Python code block.
      tools: []
      runtime: {type: model}                       # matrix.v1/runtime.model
  - name: claude
    config:
      max_turns: 3
      runtime: {type: claude-sdk, setting_sources: [], permission_mode: default}
sensors:
  - type: function-test
    timeout: 5
trials: 5
repeats: 3
```

| runtime type | what plays the subject |
|---|---|
| `claude-sdk` | a Claude Agent SDK session (cwd defaults to the experiment directory) |
| `model` | one call to any model in the `models` registry — local MLX, ollama, Gemini, Claude |
| `mock` | ix's simulator: canned `mock_response`s, or a seeded 90/10 activation split |

A misconfigured experiment fails before anything runs, naming the key and the legal set:

```
Error: subject 'claude': runtime.type 'strands' is not registered. Registered: claude-sdk, mock, model
Error: experiment.yaml: unknown key(s) ['agent']. Legal: ['description', 'engine', 'models', ...]
```

## Engines

The engine executes one repeat — every probe × trial — and returns readings. Aggregation,
noise floor and persistence belong to the experiment, so **the same experiment gives the same
results on either engine**; a parity test asserts it.

- **native** — each trial is a four-node matrix DAG (probe → subject → trial → sensor), with
  every read declared and enforced.
- **inspect** — each repeat runs as an [Inspect AI](https://inspect.aisi.org.uk) task: probes
  are samples, trials are epochs, the subject runs as a solver, the sensor as a scorer. Each
  repeat leaves an `.eval` log under `results/inspect/` — open it with `inspect view`. The log
  path is recorded in the summary's `engine_artifacts`.

## Usage

```bash
ix run catalog-routing --lab lab --mock --seed 42          # simulated, native engine
ix run sensor-integrity --lab lab --engine inspect         # same experiment, Inspect engine
ix run local-codegen --lab lab --subject local             # a real model
ix experiment list --lab lab
ix results catalog-routing --lab lab --format json
```

## Out of family

The DeepEval sensor's judge (`judge: <model>`) runs through matrix's model runtime, so any
configured family can grade. Every reading records `judge_family`, `subject_family` and
`out_of_family` — `None` when either side is unknown, never a guess.

## Extending

A sensor, engine or runtime registers without editing ix — one entry point, a callable
`register(registry) -> None`:

```toml
[project.entry-points."ix.components"]
my-sensor = "my_pkg.sensors:register"
```

## Documentation

| Document | Description |
|----------|-------------|
| [Running Experiments](docs/how-to/running-experiments.md) | Create a lab, write probes, run, interpret results |
| [What is ix?](docs/explanation/what-is-ix.md) | The problem, the design, what it's not |
| [Domain Models](docs/reference/domain-models.md) | Probe, Subject, Trial, Reading, the ports |
| [Experiment Format](docs/reference/experiment-format.md) | `experiment.yaml`, markdown probes and subjects, results |
| [SECURITY.md](SECURITY.md) | In-process code execution, config as a capability grant, telemetry |

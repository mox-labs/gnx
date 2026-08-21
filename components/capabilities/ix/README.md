# ix

Intelligent Experimentation — evals, benchmarks, and QoS experiments for AI agents.

## What this is

An **experiment** is a directory: `experiment.yaml` plus a `tasks/` folder of probes. ix
runs each probe against each subject, some number of trials, and hands every response to a
**sensor** that grades it. Sensors are the instruments — activation, function-test, tool-use,
outcome — and a sensor is the grader. Aggregation only counts.

The vocabulary is deliberately domain-neutral: probe, subject, trial, reading. Nothing about
"eval" is baked into the core types, because the same shapes express a benchmark or a load
test. Execution runs through matrix as a four-node DAG (probe → subject → trial → sensor).

Two things ix is built to protect against, both learned the hard way:

**A mock run measures the harness, not the thing.** `--mock` proves the DAG, store and
aggregation work end to end without spending a single API call. It tells you nothing about
whether a catalog routes or a model can code. Those are different claims and ix keeps them
separate rather than letting a green number stand in for both.

**A wrong verdict is worse than a crash**, because a crash gets noticed. Aggregation takes
the sensor's `passed` rather than re-deriving one from the score — the two agree for a
binary sensor and diverge for a fractional one, and the divergence silently reported
failing submissions as passes until a lab run caught it.

`repeats` exists for the same reason: one run's pass rate has no error bar, and routing is
stochastic. The reported noise floor is what says whether a difference between two subjects
is real.

### What it is not

ix does not sandbox. `FunctionTestSensor` imports and runs model-generated Python **in this
process**, which is the intended behaviour of a code benchmark and is written down in
`SECURITY.md` rather than left implicit.

## Usage

```bash
# List experiments in a lab
ix experiment list --lab ci-lab

# Run an experiment (mock mode, no API calls)
ix run skill-activation --lab ci-lab --mock --seed 42

# Show experiment details
ix experiment show skill-activation --lab ci-lab

# View past results
ix results skill-activation --lab ci-lab
```

## What ix Does

ix runs **experiments** against AI systems. You write cases, ix runs them multiple times, and gives you F1, precision, recall.

Each experiment runs multiple **trials** per case — AI behavior is stochastic, so you need the distribution, not a single result. Majority vote per case, F1 across all cases.

## Quick Example

```
$ ix run skill-activation --lab ci-lab --mock --seed 42
Running skill-activation in lab ci-lab (28 cases, 5 trials, mock)
  must-001: OK (score=100%)
  must-002: OK (score=80%)
  ...

────────────── Results ──────────────
  Precision    100.0%
  Recall        93.3%
  F1            96.6%

Status: EXCELLENT
```

## Documentation

| Document | Description |
|----------|-------------|
| [Running Experiments](docs/how-to/running-experiments.md) | Create a lab, write cases, run, interpret results |
| [What is ix?](docs/explanation/what-is-ix.md) | The problem, what ix does, what it's not |
| [Domain Models](docs/reference/domain-models.md) | All types: Subject, Interaction, Reading, EvalCase, ... |
| [Experiment Format](docs/reference/experiment-format.md) | YAML config, markdown cases, results format |

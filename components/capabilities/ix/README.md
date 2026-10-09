# ix

ix runs experiments on agents. You write probes (prompts) and subjects (agents) as files;
ix puts every probe to every subject several times, has a sensor judge each response, and
reports the pass rate with its uncertainty. `ix compare` then says whether one subject beat
another, and refuses to say so when the data cannot carry it.

```bash
uv add "ix @ git+https://github.com/mox-labs/gnx#subdirectory=components/capabilities/ix"
# extras: ix[inspect] for the Inspect AI engine, ix[deepeval] for DeepEval metrics as sensors
```

## The loop

```bash
ix lab init lab                                 # a directory that holds experiments
ix experiment init routing --lab lab            # experiment.yaml, a probe, a starter subject
ix experiment validate routing --lab lab        # compose everything, run nothing
ix run routing --lab lab --plan                 # sessions per subject, and which are live
ix run routing --lab lab                        # run every subject; results saved per subject
ix results routing --lab lab                    # the latest results, per subject
ix compare routing --lab lab baseline candidate # is the difference real?
```

An experiment is a directory:

```
lab/routing/
├── experiment.yaml     # sensors, subjects, trials, repeats, engine
├── tasks/*.md          # one probe per file: frontmatter is ground truth, body is the prompt
├── subjects/*.md       # optional: one subject per file
└── results/            # written by ix: trials.jsonl per run, summary-latest.json per subject
```

A minimal `experiment.yaml`:

```yaml
name: routing
sensor: activation
trials: 5
repeats: 3
subjects:
  - name: baseline
    config: {agent: reviewer}                  # an agent configured in matrix.yaml
  - name: candidate
    config:                                    # or an agent defined right here
      system_prompt: Use a skill only when the request is underspecified.
      runtime: {type: claude-sdk, setting_sources: []}
```

## What ix guarantees

- **One measuring rule.** The engine only runs trials. The experiment measures every trial
  with the same function, whatever engine ran it, so changing the engine never changes how a
  response is judged.
- **Every failure has an owner.** A failed trial is a failed reading, never a missing one,
  and it records whose failure it was: the **subject**'s (the runtime said the session
  `failed` or the model `refused`), the **harness**'s (rate limit, outage, timeout,
  credentials, a runtime that cannot run the definition, an engine that lost the trial, an
  agent that could not be built, or a runtime that raised an unclassified error), or the
  **sensor**'s (the grader raised). Harness faults are counted, left out of every score, and
  make `ix compare` inconclusive. Sensor faults are scored as failures and also make
  `ix compare` inconclusive.
- **Two kinds of uncertainty.** A standard error over the probes (would the number move with
  different probes?) and a noise floor across repeats (does it move when nothing changes?).
  `ix compare` names a winner only when the 95% interval of the paired difference excludes
  zero and the difference is larger than the noise floor.
- **A harness check is not a measurement.** `--simulate` runs every subject on ix's
  simulator, which calls no model. Results record the model families that actually
  answered; when none did, the status is `unmeasured`, and `ix compare` warns and names no
  winner.
- **Typos fail at load.** A probe key that none of the experiment's sensors reads is refused
  (`expectaton` instead of `expectation`). Prefix a key with `x-` to keep it as a note.

## Configuration

ix reads matrix's config tiers, so it shares runtimes, models and agents with every tool
built on matrix. Lowest priority first: ix's defaults; the `ix:` and `matrix:` sections of
`~/.matrix/config.yaml`, `~/.ix/config.yaml`, `./matrix.yaml`, `./ix.yaml`, `$MATRIX_CONFIG`,
`$IX_CONFIG`; then the experiment's own `experiment.yaml`; then command-line flags.

```yaml
# ./ix.yaml: your defaults for every experiment
ix:
  lab: lab
  trials: 3
  repeats: 2
  engine: native
```

`matrix config --tool ix --sources` lists the files read, in order. The how-to has a worked
example with a `matrix.yaml` that defines a runtime and an agent.

## Engines and sensors

| engine | runs a repeat as |
|---|---|
| `native` (default) | one run of a matrix flow per trial; the flow is compiled once per repeat |
| `inspect` | one Inspect AI task: probes are samples, trials are epochs; writes an `.eval` log |

| sensor | judges |
|---|---|
| `activation` | did the expected skill fire (or stay quiet, for a decoy)? |
| `function-test` | does the generated function pass the probe's test cases? Runs the code **in this process**; see SECURITY.md |
| `tool-usage` | did the agent call the expected tool and subcommand? |
| `outcome` | does the answer contain the expected facts, or pass your grader functions? |
| `deepeval` | a DeepEval metric, graded by a judge model or a configured agent |

Sensors and engines are extension points on matrix's registry. A package adds its own
through the `matrix.extensions` entry point, and an experiment names it by type URL:

```toml
[project.entry-points."matrix.extensions"]
acme-sensors = "acme_sensors:register"
```

```python
def register(registry):
    registry.register("sensor", "acme.v1.sensor.brevity", build,
                      config=BrevityConfig, needs={"probes"}, effects=set())
```

```yaml
sensor: {type: acme.v1.sensor.brevity, max_chars: 120}
```

`matrix catalog --point sensor` lists what is installed.

## For programs and agents

Every command takes `--format json`. Each document names its shape in a `schema` field:
`ix.v1.results-list`, `ix.v1.results`, `ix.v1.comparison`, `ix.v1.plan`, `ix.v1.experiment`, `ix.v1.experiments`,
`ix.v1.validation`, `ix.v1.labs`, `ix.v1.init`. `ix run` and `ix results` print one
`ix.v1.results-list` document, `{"schema", "experiment", "results": [...]}`, with one
`ix.v1.results` entry per subject at any subject count. An error is one JSON line on stderr,
`{"error": {"kind", "message", "fix"}}`, where `fix` is a command that will work.

| Exit | Meaning |
|------|---------|
| 0 | success |
| 1 | failure not otherwise classified (an engine error, a runtime error) |
| 2 | usage: a bad flag or argument |
| 3 | config: an invalid experiment, a failed validate, or a refused implicit live run |
| 4 | not found: a lab, experiment, subject or saved results |
| 5 | transient: a retryable runtime failure (rate limit, outage, timeout) outside a trial |
| 6 | auth: the provider refused the credentials |

`--plan` (also `--dry-run`, `-n`) prints what a run would start and runs nothing. With no
`--subject`, a run that would start several subjects when any is live is refused until you
name a `--subject` or pass `--all`. A runtime is live when its registered effects include
`model` or `network`, or are unknown; ix's simulator and matrix's `mock` are offline.

## Documentation

| Document | What it covers |
|----------|----------------|
| [Running experiments](docs/how-to/running-experiments.md) | From an empty directory to a comparison; shared config; writing a sensor |
| [What is ix?](docs/explanation/what-is-ix.md) | The model behind it: trials, faults, uncertainty, extension points |
| [Experiment format](docs/reference/experiment-format.md) | Every file, key, option and result field |
| [Domain models](docs/reference/domain-models.md) | The types, the ports, the registry entries |
| [SECURITY.md](SECURITY.md) | In-process code execution, config as a capability grant, telemetry |

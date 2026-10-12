# ix

ix answers a question about a system with an experiment. This package holds two things, and
each command belongs to exactly one of them:

| | Commands | Reads |
|---|---|---|
| **The experiment model** | `ix validate`, `ix plan`, `ix register`, `ix genres`, `ix --skill` | one experiment file with a `genre:` (schema `ix.v2.experiment`) |
| **The agent-eval harness** | `ix run`, `ix results`, `ix compare`, `ix experiment ...`, `ix lab ...` | a lab directory whose `experiment.yaml` has `sensors:`, `trials:`, `engine:` |

The experiment model checks, plans and registers an experiment for an agent, a model, a skill
or a service; it starts nothing. The agent-eval harness runs agent evals. The two file
formats are different and neither command set reads the other's: `ix validate` given a
harness file says so and names `ix experiment validate`.

```bash
uv add "ix @ git+https://github.com/mox-labs/gnx#subdirectory=components/capabilities/ix"
# extras: ix[inspect] for the Inspect AI engine, ix[deepeval] for DeepEval metrics as sensors
```

## The experiment model

```bash
ix --skill                                     # how to write an experiment for each genre
ix validate examples/proxy-streaming           # every problem at once; exit 3 when invalid
ix plan examples/proxy-streaming               # what a run would do; runs nothing
ix register examples/proxy-streaming           # freeze the decision parameters beside the file
ix register examples/proxy-streaming --check   # what changed since registration
ix genres                                      # the genres installed, and any file ix refused
```

An experiment is one YAML file (or a directory holding `experiment.yaml`). It names its
**genre**, the decision the answer must equip, and declares its subjects, probes and metrics:

```yaml
name: proxy-streaming
question: Does streaming download bodies cut peak memory without costing TTFB?
genre: compare
subjects:                 # the first is the control, unless control: names another
  buffered:  {kind: process, command: ["./proxy", "--config", "env/buffered.yaml"]}
  streaming: {kind: process, command: ["./proxy", "--config", "env/streaming.yaml"]}
factors: {size: [1MiB, 64MiB, 1GiB]}
probes:
  - {kind: http-load, target: "http://127.0.0.1:8080/blobs/{size}", load_model: open}
metrics:
  - {name: ttfb, unit: ns, direction: lower, shape: histogram}
primary: {metric: ttfb, statistic: p99}
rule: non-inferiority
margin: 5%
```

Two complete examples are checked in:
[`examples/proxy-streaming`](examples/proxy-streaming/experiment.yaml) (a service comparison)
and [`examples/catalog-routing`](examples/catalog-routing/experiment.yaml) (an agent
evaluation with probes in files).

### Genres

A genre is a record in a YAML file: the roles subjects play, what the experiment must
declare, the design defaults, which analysis decides, and what the report must contain. It
holds no code and names no probe kind, sensor, subject kind or tool.

| genre | question | subjects | you must set | verdict |
|---|---|---|---|---|
| `evaluate` | Does this subject meet the bar? | one | `primary`, `bar` | holds / fails / inconclusive |
| `compare` | Is the variant better than the control, or no worse? | one control, one or more variants | `primary`, `rule`, and `margin` unless the rule is `superiority` | holds / fails / inconclusive |
| `regress` | May this change ship against the baseline? | one control, one variant | `primary`, `margin` | holds / fails / inconclusive |
| `characterise` | How does this subject behave across the range? | one | `factors` | none: estimates only |

`regress` extends `compare`. A genre extends at most one other, one level deep, and only by
narrowing: it may raise a minimum, lower a maximum, require more, allow fewer rules or add
report sections; it may not loosen its parent, change the analysis or the verdict kind, or
add a role. A genre file that breaks this is refused with a `genre_narrowing` problem for
each place it does.

Every default a genre supplies (repeats, interleaving, the A/A calibration, a fixed rule)
carries a note, and the built-in ones say they are provisional pending the experimentation
canon. `ix plan` shows each design value with where it came from: the experiment, the genre
or ix.

To ship a genre, put its YAML file in a package and name the package at the `ix.genre` entry
point; ix registers its own four the same way. A lab can also keep genre files in a
`genres/` directory beside its experiment.

```toml
[project.entry-points."ix.genre"]
acme = "acme_ix.genres"      # a package of *.yaml files; read, never called
```

### What validate refuses

`ix validate` reports every problem in one batch, each with `path`, `kind`, `message` and
`fix`. Among them: a genre's requirements that are not met; a rule the genre does not allow;
a metric that is not declared, or a statistic its shape does not have; a `{placeholder}` in a
probe that is bound to nothing; an estimated spend over the ceiling.

**A model-judged metric never decides a gate.** A metric declared `judged: true` is refused
as `primary` and as a guard in every genre with a verdict (`judged_gate`). It may be measured
and reported.

### Plan and registration

`ix plan` is pure: it runs nothing, and the same file gives the same plan with the same
digest. It states which subject is the control, the roles, the cells (factor combinations),
the probes, the repeats, the order rule (interleaved, seeded), the effects the file declares,
and the estimated spend.

`ix register` writes `<file-stem>.registration.json` beside the experiment: the digests of
the file and of the probe set, the genre, the primary metric, the margin, the repeats, the
seed and the spend ceiling. Whatever differs afterwards is a deviation, listed by
`ix register <path> --check`. Registering an experiment that has changed is refused (exit 3)
and the record is kept. `ix run` belongs to the harness and does not read a registration.

### Library

The commands are an adapter over the library; `import ix` gives the same thing.

```python
import ix

result = ix.validate("examples/proxy-streaming")     # ix.Validation: valid, problems, warnings
plan = ix.plan("examples/proxy-streaming")           # ix.Plan; raises ix.InvalidExperimentError
design = ix.resolve("examples/proxy-streaming")      # ix.ResolvedDesign: every value, attributed
record = ix.register("examples/proxy-streaming")     # ix.Registered
changed = ix.deviations("examples/proxy-streaming")  # ix.RegistrationCheck
genres = ix.catalogue()                              # ix.Catalogue: genres, and refused files
```

`ix.__all__` is the declared API: those functions, `load` and `skill`, the records
(`Experiment`, `Genre`, `Metric`, `Plan`, `Registration`, `Problem`, ...) and the errors
(`IxError`, `ConfigError`, `NotFoundError`, `InvalidExperimentError`,
`RegistrationConflictError`). Importing `ix` loads no host runtime.

### Output

These commands take `--json` and print one document with a `schema` field: `ix.v2.validation`,
`ix.v2.plan`, `ix.v1.registered`, `ix.v1.deviations`, `ix.v1.genres`. An error is one JSON
line on stderr, `{"error": {"kind", "message", "fix"}}`. Exit codes: 0 done (for `validate`,
valid); 2 usage; 3 config (an invalid experiment, or `register` on a changed one); 4 not
found (no experiment file, or no registration to check).

## The agent-eval harness

The rest of this README describes the harness: `ix run`, `ix results`, `ix compare`,
`ix experiment` and `ix lab`. You write probes (prompts) and subjects (agents) as files; ix
puts every probe to every subject several times, has a sensor judge each response, and
reports the pass rate with its uncertainty. `ix compare` then says whether one subject beat
another, and refuses to say so when the data cannot carry it.

### The loop

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

### What ix guarantees

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

### Configuration

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

### Engines and sensors

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

### For programs and agents

Every harness command takes `--format json`. Each document names its shape in a `schema` field:
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
| `ix --skill` | The experiment model: the file, each genre, the commands, the problem kinds |
| [CHANGELOG.md](CHANGELOG.md) | What each release changed |
| [Running experiments](docs/how-to/running-experiments.md) | The harness: from an empty directory to a comparison; shared config; writing a sensor |
| [What is ix?](docs/explanation/what-is-ix.md) | The harness: trials, faults, uncertainty, extension points |
| [Experiment format](docs/reference/experiment-format.md) | The harness: every file, key, option and result field |
| [Domain models](docs/reference/domain-models.md) | The harness: the types, the ports, the registry entries |
| [SECURITY.md](SECURITY.md) | In-process code execution, config as a capability grant, telemetry |

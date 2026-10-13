# ix

ix answers a question about a system with an experiment. You write one YAML file that names
its **genre** (the decision the answer must inform), its subjects, its probes and the metric
that decides. ix checks the file, shows the decision a run would inform and what it would
cost, and freezes the design before anything runs.

**ix does not run an experiment.** There is no `ix run`: this package validates, plans and
registers. Nothing it does starts a subject, applies a probe or spends money.

```bash
uv add "ix @ git+https://github.com/mox-labs/gnx#subdirectory=components/capabilities/ix"
```

## The loop

```bash
ix --skill                             # how to write an experiment, from what is installed
ix validate examples/proxy-streaming   # every problem at once; exit 3 when invalid
ix plan examples/proxy-streaming       # the decision, the trials, the effects, the spend
ix register examples/proxy-streaming   # freeze the design beside the file
ix deviations examples/proxy-streaming # what changed since; exit 9 when anything did
ix genres                              # the genres installed
ix kinds                               # the subject kinds, probe kinds, sensors, environments
```

A path is the experiment file, or a directory holding `experiment.yaml`. Every command takes
`--json`.

## An experiment

```yaml
name: proxy-streaming
question: Does streaming keep time to first byte without costing memory?
genre: compare
subjects:                  # the first is the control, unless control: names another
  buffered:  {kind: process, command: [./proxy, --buffered], url: "http://127.0.0.1:8080"}
  streaming: {kind: process, command: [./proxy, --streaming], url: "http://127.0.0.1:8080"}
factors: {size: [1MiB, 64MiB, 1GiB]}
probes:
  - kind: http-load
    target: "{url}/blobs/{size}"
    load_model: open
    rate: 80/s
    duration: 60s
primary: {metric: ttfb, statistic: p99}
comparison: non-inferiority
margin: 5%
guardrails:
  - {metric: memory_peak, margin: 0%}
```

That is the whole file. Repeats, the order of trials, the A/A calibration and the estimator
come from the genre; the sensors come from the metrics named (`timing` provides `ttfb`,
`memory` provides `memory_peak`); the effects come from the kinds. `ix plan` shows each of
them and where it came from.

Two complete examples are checked in:
[`examples/proxy-streaming`](examples/proxy-streaming/experiment.yaml) (a service
comparison) and [`examples/catalog-routing`](examples/catalog-routing/experiment.yaml) (an
agent evaluation with probes in files).

## The plan

`ix plan` states the decision first, then the cost:

```
proxy-streaming  compare (ix.v1.genre.compare)  plan 2cf8740f9a52
Question:     Does streaming download bodies keep time to first byte and throughput, ...
Decides:      streaming is no worse than buffered by more than 5% on ttfb p99
Comparison:   non-inferiority
Guardrails:   throughput no worse than buffered by more than 5%
              memory_peak no worse than buffered by more than 0%
Verdict:      holds / fails / inconclusive
...
Trials:       2 subjects x 9 cells x 1 probe x 5 repeats = 90, + 45 A/A = 135
Duration:     about 2h 49m (135 trials, probe time only; setup and teardown add to it)
Effects:      process: starts a process on this machine  (environment, subjects.buffered, ...)
Registration: none
```

The `Decides:` line is rendered from the genre, the metric and the margin, so it cannot
drift from them. The plan's digest identifies what a run would do: it is unchanged by a
comment, key order, a default written out or a reworded question, and it changes when a
value a run would use changes, including one the genre supplied.

## Genres

A genre is a record in a YAML file: the roles subjects play, what the experiment must set,
the comparisons it may make, how a run is laid out, which analysis decides, and what the
report must contain. It holds no code and names no kind.

| genre | question | subjects | you must set | verdict |
|---|---|---|---|---|
| `evaluate` | Does this subject meet the threshold? | one | `primary`, `threshold` | holds / fails / inconclusive |
| `compare` | Is the variant better than the control, or no worse? | one control, one or more variants | `primary`, `comparison`, and `margin` for non-inferiority and equivalence | holds / fails / inconclusive |
| `regress` | May this change ship against the baseline? | one control, one variant | `primary`, `margin` | holds / fails / inconclusive |
| `characterize` | How does this subject behave across the range? | one | `primary`, `factors` | none: estimates only |

`regress` extends `compare`. A genre extends at most one other, one level deep, and only by
narrowing: it may raise a minimum, lower a maximum, require more, allow fewer comparisons or
add report sections. It may not loosen its parent, change the analysis or add a parameter to
it, change the estimator or the verdict form, or add a role. A genre file that breaks this is
refused with a `genre_narrowing` problem for each place it does.

A genre's `design` (repeats, order, calibration, estimator) carries a note on its standing;
the built-in ones say they are provisional pending the experimentation canon.

## Kinds

A subject, a probe, a sensor and the environment each name a **kind**. A kind is declared as
data, the way a genre is: its options (with types, and which are required), its effects, the
facets it offers and needs, and for a sensor the metrics it provides and whether a model
judges them. `ix kinds` lists them.

| family | kinds |
|---|---|
| subject | `process`, `http`, `agent` |
| probe | `prompt`, `http-load`, `inspect-task` |
| sensor | `timing`, `memory`, `activation`, `function-test`, `usage`, `judge` |
| environment | `none`, `process`, `compose` |

These are declarations. ix holds no code that starts a subject or takes a measurement.

`ix validate` holds an experiment to them:

- an option the kind does not take, a required one that is missing, or one of the wrong type
  is refused (`option_unknown`, `option_missing`, `option_invalid`); a key starting with
  `x-` is a note;
- a probe kind must be offered what it needs by every subject's kind (`kind_unfit`): a
  prompt cannot be put to a process;
- every metric named by `primary`, `guardrails` or `metrics:` must have exactly one sensor
  that provides it and can be fed here (`metric_unprovided`, `metric_ambiguous`); listing a
  sensor under `sensors:` settles an ambiguity or gives it options;
- **a model-judged metric never decides.** Whether a metric is judged is the sensor's
  declaration, not the author's. A judged metric is refused as `primary` and as a guardrail
  in every genre with a verdict (`judged_gate`); it can be reported under `metrics:`;
- a run that would spend money needs `spend.ceiling` (`spend_unbounded`).

A metric decision has two reductions: `statistic` reduces one trial's reading to a number
(the p99 of a latency histogram) and `estimator` combines the trials (their mean; the rate
of a boolean). Both default.

## Extending

Genres and kinds are data files. Put them in a package and name the package at the entry
point ix owns; ix registers its own the same way. A lab can also keep `genres/` and `kinds/`
directories beside its experiment.

```toml
[project.entry-points."ix.genre"]
acme = "acme_ix.genres"      # a package of *.yaml files; read, never called

[project.entry-points."ix.kind"]
acme = "acme_ix.kinds"
```

Two files that declare one name are both refused; a file that cannot be read does not affect
the others. `ix genres` and `ix kinds` list what was refused and why.

## Registration

`ix register` writes `<file-stem>.registration.json` beside the experiment. It stores the
design as resolved, what the file sets and what the genre supplies (comparison, margin,
threshold, guardrails, control, repeats, seed, order, calibration, estimators, the analysis
and its parameters, the sensors), with the digests of the probe set, the experiment and the
plan.

- `ix deviations <path>` lists each frozen value that changed, with what was registered and
  what it is now, and exits 9 when it finds any. A genre that changes after registration
  shows up here, because its values were frozen and not just its name.
- A comment, key order or a default written out is not a deviation. A reworded `question` or
  `rationale` changes the experiment's digest and not the plan's.
- `ix register` on a changed experiment is refused (exit 3) and the record is kept.
- `ix register <path> --amend --reason "<why>"` replaces the record on purpose. The
  superseded record, the reason and the deviations it accepted stay inside the new one.
- A registration written by a newer ix still loads: fields this ix does not know are
  ignored, and a value an older record never stored is not compared.

## Library

The commands are an adapter over the library; `import ix` gives the same thing. Each
function takes a path or an `ix.Experiment` built in memory.

```python
import ix

result = ix.validate("examples/proxy-streaming")  # ix.Validation: valid, complete, problems
plan = ix.plan("examples/proxy-streaming")  # ix.Plan; raises ix.InvalidExperimentError
design = ix.resolve("examples/proxy-streaming")  # ix.Design: every value, with its source
found = ix.deviations("examples/proxy-streaming")  # ix.Deviations; ix.NotFoundError if none
genres, kinds = ix.genres(), ix.kinds()  # ix.Genres, ix.Kinds

experiment = ix.load("examples/proxy-streaming")  # ix.Experiment
ix.plan(experiment.model_copy(update={"repeats": 9})).trials.total
```

`ix.__all__` is the declared API: `load`, `validate`, `resolve`, `plan`, `register`,
`deviations`, `genres`, `kinds`, `skill`, `error_document`; the documents they return
(`Validation`, `Problem`, `Design`, `Plan`, `Registered`, `Deviations`, `Genres`, `Kinds`);
the three records an author or an extension writes (`Experiment`, `Genre`, `Kind`); the
errors (`IxError`, `ConfigError`, `NotFoundError`, `InvalidExperimentError`,
`RegistrationConflictError`); and the tables `PROBLEM_KINDS` and `EXIT_CODES`. A record's
parts (`Subject`, `Probe`, `Margin`, ...) are in `ix.domain` and reachable as field types.
Importing `ix` loads no host runtime.

## Output

`--json` prints one document with a `schema` field: `ix.v1.validation`, `ix.v1.plan`,
`ix.v1.registered`, `ix.v1.deviations`, `ix.v1.genres`, `ix.v1.kinds`.

A command that fails prints one envelope on stderr, as one JSON line under `--json`:

```json
{"schema": "ix.v1.error", "error": {"kind": "config", "message": "...", "fix": "...", "problems": [], "deviations": []}}
```

A problem is `{path, kind, message, fix, allowed, got, source}`. `allowed` lists the legal
values where the set is closed, and `got` is what was written, so a caller need not parse
the message. A validation with `"complete": false` stopped at the file's shape: fix those
problems and validate again. `ix validate` on an invalid file and `ix deviations` that finds
any are not failures: each prints its own document on stdout and exits non-zero.

| Exit | Meaning |
|------|---------|
| 0 | done; for validate, the experiment is valid |
| 1 | a failure ix did not classify |
| 2 | usage: a bad flag or argument |
| 3 | config: the experiment is invalid, or register met a changed experiment |
| 4 | not found: no experiment file, or no registration |
| 9 | deviations: the experiment differs from its registration (ix deviations) |

## Documentation

| Document | What it covers |
|----------|----------------|
| `ix --skill` | The file, each genre, each kind and its options, the commands, the problem kinds |
| [CHANGELOG.md](CHANGELOG.md) | What each release changed |
| [SECURITY.md](SECURITY.md) | What ix reads, writes and never does |

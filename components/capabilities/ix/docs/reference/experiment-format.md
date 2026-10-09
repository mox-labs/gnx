# Experiment format

Every file ix reads and writes, and every key in them. Read off the loader
(`adapters/_out/filesystem_store.py`), the config models (`domain/models.py`,
`composition/__init__.py`), the sensors and engines, and the results model.

---

## Configuration tiers

ix reads matrix's config files. Later tiers win; mappings merge key by key, lists replace.

| Priority | Source | Holds |
|---|---|---|
| lowest | ix's schema defaults | `trials: 5`, `repeats: 1`, `engine: native` |
| | `~/.matrix/config.yaml` | your runtimes, models and agents, for every tool |
| | `~/.ix/config.yaml` | your ix defaults |
| | `./matrix.yaml` | the project's shared runtimes and agents |
| | `./ix.yaml` | the project's ix defaults |
| | `$MATRIX_CONFIG`, `$IX_CONFIG` | explicit files; each must exist if set |
| | `<experiment>/experiment.yaml` | this experiment |
| highest | command-line flags | `--trials`, `--repeats`, `--engine`, `--lab` |

`./` is the directory ix runs in. Any of the config files may hold a `matrix:` section and an
`ix:` section. `matrix config --tool ix --sources` lists the files consulted, in order.

### The `ix:` section

| Key | Type | Description |
|---|---|---|
| `lab` | string | the lab directory, used when `--lab` is not given |
| `trials` | int ≥ 1 | trials per probe, unless the experiment sets `trials` |
| `repeats` | int ≥ 1 | whole-run repeats, unless the experiment sets `repeats` |
| `engine` | string or mapping | `native`, `inspect`, an extension's type URL, or `{type, <options>}`, unless the experiment sets `engine` |

Any other key is an error. A value applies only where `experiment.yaml` is silent: a file that
says `trials: 5` keeps 5.

### The `matrix:` section

ix uses matrix's schema unchanged; matrix's documentation is the reference. The parts ix uses:

```yaml
matrix:
  models:                      # a hardline registry, for type: model runtimes
    default: qwen3-8b
    models:
      qwen3-8b: {backend: openai-compat, base_url: "http://127.0.0.1:8080/v1",
                 model: mlx-community/Qwen3-8B-4bit, family: qwen, local: true}
  runtimes:
    sdk:   {type: claude-sdk, permission_mode: default, setting_sources: []}
    local: {type: model}
  agents:
    reviewer: {runtime: sdk, system_prompt: "You review pull requests."}
  definitions: [agents/]       # *.md agent files; an agent named after one uses it
  observers: [otel]            # attached to every agent and to the native engine's flows
```

---

## Directory structure

```
<lab>/
└── <experiment>/
    ├── experiment.yaml
    ├── tasks/                 # probes, one per file (cases/ is read if tasks/ is absent)
    │   └── <probe>.md
    ├── subjects/              # optional; if it holds any .md file, `subjects:` in the YAML is ignored
    │   └── <subject>.md
    └── results/               # written by ix
        ├── <subject>/
        │   ├── <run_id>/
        │   │   └── trials.jsonl       # one TrialRecord per trial, every repeat
        │   ├── summary-<run_id>.json
        │   └── summary-latest.json    # the most recent run; what `ix results` reads
        └── inspect/                   # engine: inspect only; one .eval log per repeat
```

A lab is a directory holding at least one subdirectory with an `experiment.yaml`. ix finds it
from `--lab <name>` (relative to the project root, the nearest directory with `.git`), from
`lab:` in the `ix:` section, or by walking up from the working directory.

A subject name becomes a directory name with every character outside `[A-Za-z0-9._-]`
replaced by `-`; `reviewer@simulated` is stored in `results/reviewer-simulated/`.

---

## experiment.yaml

```yaml
name: review
description: Does the reviewer name the risks a careful human would?
engine: native                   # or inspect, or {type: inspect, max_samples: 2}
sensor: outcome                  # or sensors: [...]
trials: 3
repeats: 2
subjects:
  - name: reviewer
    config: {agent: reviewer}
  - name: inline
    config:
      system_prompt: Reply with one sentence.
      runtime: {type: mock, default: "The token expiry is the risk here."}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | directory name | experiment identifier |
| `description` | string | `""` | |
| `subjects` | list | `[]` | `{name, description?, config}`, or a bare name; see Subjects |
| `sensors` | list of mappings | `[{type: activation}]` | each `{type, <options>}`; several are combined and all their readings count |
| `sensor` | string or mapping | | shorthand for a one-element `sensors`; giving both is an error |
| `engine` | string or mapping | `ix:` default, else `native` | `{type, <options>}`; a bare name is `{type: <name>}` |
| `models` | mapping | the `matrix.models` section | a hardline registry that replaces `matrix.models` for this experiment |
| `trials` | int ≥ 1 | `ix:` default, else 5 | trials per probe per repeat |
| `repeats` | int ≥ 1 | `ix:` default, else 1 | whole-run repeats; the noise floor needs two or more |

Any other top-level key is an error naming the file and the legal keys.

### Subjects

A subject's `config` either names a configured agent or defines one inline.

| Key | Type | Description |
|-----|------|-------------|
| `agent` | string | a name under `matrix.agents`; brings that agent's definition and runtime |
| `system_prompt` | string | in `subjects/*.md`, the file body |
| `model` | string | interpreted by the runtime: an SDK model alias for `claude-sdk`, a registry name for `model` |
| `tools` | list or comma string | omit for the runtime's default; `[]` for no tools |
| `max_turns` | int ≥ 1 | inline subjects default to 1; a named agent keeps its own |
| `runtime` | mapping | `{type: <runtime>, <options>}`; inline subjects only |

With `agent`, any of `system_prompt`, `model`, `tools`, `max_turns` given here override the
agent's. Giving both `agent` and `runtime` is an error: the agent brings its own runtime. An
inline subject with no `runtime.type` is an error naming the registered runtimes. Any other key
is an error.

| `runtime.type` | options | notes |
|---|---|---|
| `claude-sdk` | `permission_mode`, `cwd` (default: the experiment directory), `setting_sources`, `plugins`, `fallback_model`, `agents` | relative plugin paths resolve against `cwd`; `validate` checks local ones exist |
| `model` | `models`, `default_model`, `temperature`, `max_tokens` | one model call through hardline; `models` defaults to the experiment's or matrix's `models` |
| `mock` | `responses` (task → reply), `default`, `family` | matrix's offline runtime; replies come from config |
| `simulated` | none | ix's simulator: a probe's `mock_response`, else a seeded 90/10 activation split |
| an extension's type URL | its own | any runtime registered at matrix's `runtime` point |

`matrix describe matrix.v1.runtime.<type>` prints a runtime's full option schema.
`--simulate` puts every subject on `simulated` for one run.

### Engines

| `engine.type` | options | runs a repeat as |
|---|---|---|
| `native` | `concurrency` (default 1): trials in flight at once; `trial_timeout_s` (default 600, `null` waits forever): a session still running after it is cut off as a `timeout` harness fault | one run of a matrix flow per trial, compiled once per repeat; trials come back in probe × trial order |
| `inspect` | `log_dir` (default `inspect/` under the experiment's `results/`), `max_samples` (default 1), `fail_on_error` (default false) | one Inspect AI task: probes are samples, trials are epochs; needs `ix[inspect]` |
| an extension's type URL | its own | any engine registered at the `engine` point |

`--engine` replaces the engine for one run. If it names a different engine than the file, the
file's engine options are dropped.

### Sensors

| `type` | options | probe keys it reads | judges |
|---|---|---|---|
| `activation` | `expected_skill` | `expectation`, `expected_skill` | whether the expected skill fired (or, for `should_not_trigger`, that none did) |
| `function-test` | `timeout` (seconds, default 30) | `function_name`, `test_cases` | runs the response's code against the test cases **in this process** (SECURITY.md); score = fraction passed |
| `tool-usage` | `expected_tool` (default `memex`) | `expected_command`, `expected_query`, `expected_args`, `expectation` | 1.0 right command and args, 0.5 right command only, 0.0 otherwise |
| `outcome` | `graders_module` (path relative to the experiment) | `expected_facts`, `expected_command` | grader functions from `GRADERS` in the module, else the fraction of `expected_facts` found; passes above 0.5 |
| `deepeval` | `metric` (default `answer_relevancy`), `threshold` (default 0.5), `judge`, `criteria` | `expected_output`, `context` | a DeepEval metric; needs `ix[deepeval]` |
| an extension's type URL | its own | its `truth_keys` | |

`deepeval`'s `judge` is the name of a configured agent (`matrix.agents`), or else a model name
run on matrix's `model` runtime. Without a judge, DeepEval uses its own default provider. Each
reading records `judge_family`, `subject_family` and `out_of_family` (`null` when either is
unknown).

---

## Probes: `tasks/*.md`

```markdown
---
id: auth-change
expected_facts: [token expiry, retry]
x-source: incident 412
---
Review this change: the auth client now caches tokens for 24 hours and retries failed calls forever.
```

The body is the prompt, sent verbatim. `id` defaults to the file stem and is always a string.
Every other key goes into the probe's `metadata`, and must be one of:

- a key one of the experiment's sensors reads (the table above);
- one of ix's own: `mock_response` (the simulator's reply), `tags`, `notes`, `title`,
  `description`;
- a key starting with `x-`, for notes.

Anything else is refused when the experiment loads, naming the probe and the keys the sensors
read. The check is skipped when a configured sensor does not declare the keys it reads.

`expectation` is `must_trigger` (the default), `should_not_trigger` or `acceptable`. Any other
value is an error, including an unquoted `no`, which YAML reads as `false`.

## Subjects: `subjects/*.md`

```markdown
---
name: terse
description: Answers in one word.
model: haiku
tools: []
runtime: {type: claude-sdk, setting_sources: []}
---
Answer every question with exactly one word.
```

The frontmatter is the subject's `config` (`name` and `description` lifted out); the body is
its `system_prompt`. A file with an empty body sets no system prompt, so a subject that names
an `agent` keeps that agent's prompt:

```markdown
---
name: filed
agent: reviewer
---
```

---

## Trials: `<run_id>/trials.jsonl`

One `TrialRecord` per line, for every trial of every repeat.

| Field | Description |
|-------|-------------|
| `run_id`, `run_index` | the run, and which repeat (0-based) |
| `probe_id`, `trial_index` | which probe, which trial of it |
| `response` | the agent's response, serialised: content, tool calls, token counts, `family`, `model` |
| `error` | set instead of `response` when the session failed |
| `readings` | the sensors' readings for this trial, each with `fault` when it was never judged |

## Results: `results/<subject>/summary-latest.json`

Written after every run of that subject, and archived as `summary-<run_id>.json`. With
`--format json`, `ix run` and `ix results` print one document at any subject count:

```json
{"schema": "ix.v1.results-list", "experiment": "routing",
 "results": [{"schema": "ix.v1.results", "subject": "baseline", "pass_rate": 1.0, ...}]}
```

| Field | Description |
|-------|-------------|
| `experiment_name`, `subject`, `run_id` | |
| `probe_results[]` | `probe_id`, `score` (mean trial score), `passed` (a majority of trials passed, by the sensor's verdict), `trial_scores`, `details` (at most 3 distinct strings; every trial's are in `trials.jsonl`) |
| `pass_rate`, `n_probes` | fraction of measured probes that passed, and how many that is |
| `mean_score`, `min_score`, `max_score` | over probe scores |
| `pass_rate_stderr`, `mean_score_stderr`, `stderr_method` | standard error over the probes (`clt-over-probes`); `null` below two probes |
| `repeats`, `per_run_pass_rates`, `per_run_mean_scores` | one value per repeat |
| `noise_floor_sd`, `score_noise_floor_sd` | standard deviation across repeats; `null` with one repeat |
| `confusion_matrix` | `{expected_skill: {activated_skill: count}}`, from activation readings |
| `sensor_faults` | readings the sensor never judged because it raised; scored as failures |
| `harness_faults` | trials that never had a fair chance (`rate_limited`, `unavailable`, `timeout`, `auth`, `incapable`, `engine`, `setup`, `unclassified`); left out of every score |
| `unmeasured_probes` | probes whose every trial was a harness fault; left out of `n_probes` and every rate |
| `stops` | how the completed sessions ended, counted: `{"completed": 45}`, or `max_turns` and the like when a limit cut one short (it was still judged) |
| `families` | the model families that answered, read off the responses |
| `measured_a_model` | computed: false when `families` is empty or only `simulated`/`mock` |
| `status` | computed: `unmeasured` when `measured_a_model` is false; else `excellent` (1.0), `good` (≥ 0.85), `needs_work` (≥ 0.5), `poor` |
| `engine`, `engine_artifacts` | the engine's name, and records it left (`inspect_log:<path>` per repeat) |
| `trials_log` | this run's `trials.jsonl`, relative to the experiment directory |
| `config_hash` | sha256 (first 16 hex) of the whole experiment config, probes included |
| `run_timestamp`, `ix_version`, `seed`, `simulated` | provenance |

## Comparison: `ix compare <experiment> A B`

Not saved; printed, or emitted with `--format json` as `"schema": "ix.v1.comparison"`.

| Field | Description |
|-------|-------------|
| `experiment`, `a`, `b`, `n` | the experiment, the two subjects, and how many probes they share |
| `run_id_a`, `run_id_b` | which run each side's summary came from |
| `pass_rate_a`, `pass_rate_b` | each side's pass rate over the shared probes |
| `mean_delta` | mean of (score B − score A) over the shared probes |
| `delta_stderr`, `ci95` | standard error of the paired differences over √n, and mean ± 1.96·SE; `null` below two shared probes |
| `a_only_passed`, `b_only_passed` | probes whose verdict flipped, each way |
| `noise_floor_sd` | the larger of the two sides' `score_noise_floor_sd`, where measured |
| `unmatched` | probe ids in only one side's results |
| `sensor_faults`, `harness_faults` | summed over both sides |
| `both_measured` | whether a real model answered on both sides (`measured_a_model` on each) |
| `warning` | set when either side measured no model, or has sensor or harness faults |
| `probes[]` | per shared probe: `score_a`, `score_b`, `passed_a`, `passed_b`, and `delta` |
| `verdict` | `b_better` / `a_better` only when `both_measured`, `ci95` excludes 0, `abs(mean_delta)` exceeds `noise_floor_sd` where measured, and there are no sensor or harness faults; otherwise `inconclusive` |

## Other JSON documents

| `schema` | Printed by |
|---|---|
| `ix.v1.results-list` | `ix run`, `ix results`: `experiment` and `results`, a list of `ix.v1.results` |
| `ix.v1.plan` | `ix run --plan`: per subject `runtime`, `permission_mode`, `live` (from the runtime's registered effects; unknown counts as live), `sessions`, `saved_as`; totals; `refused` |
| `ix.v1.experiment` | `ix experiment show` |
| `ix.v1.experiments` | `ix experiment list` |
| `ix.v1.validation` | `ix experiment validate`, when valid |
| `ix.v1.labs` | `ix lab list` |
| `ix.v1.init` | `ix lab init`, `ix experiment init`; `created` is false when it already existed |

# Experiment Format

An experiment is a directory: one `experiment.yaml`, markdown probes, optional markdown
subjects, and a `results/` directory ix writes. Everything here was read off the loader
(`adapters/_out/filesystem_store.py`), the config models (`eval/models.py`,
`composition/__init__.py`) and the results model.

---

## Directory structure

```
<lab>/
└── <experiment>/
    ├── experiment.yaml
    ├── tasks/                 # probes, one per file (cases/ is read if tasks/ is absent)
    │   └── <probe>.md
    ├── subjects/              # optional; if present, it replaces `subjects:` in the YAML
    │   └── <subject>.md
    └── results/               # written by ix
        ├── <subject>/
        │   ├── <run_id>/
        │   │   └── trials.jsonl       # one TrialRecord per trial, every repeat
        │   ├── summary-<run_id>.json
        │   └── summary-latest.json    # the most recent run's, what `ix results` reads
        └── inspect/                   # engine: inspect only — one .eval log per repeat
```

A lab is any directory containing at least one subdirectory with an `experiment.yaml`. ix
finds it from `--lab <name>` (relative to the project root) or by walking up from the working
directory.

Results are keyed by subject: running one subject's experiment never overwrites another
subject's `summary-latest.json`.

---

## experiment.yaml

```yaml
name: local-codegen
description: A local model writes small functions; function-test grades them.
engine: inspect                  # or native (default), or {type: inspect, max_samples: 1}
models:                          # a hardline registry section
  default: qwen3-8b
  models:
    qwen3-8b: {backend: openai-compat, base_url: "http://127.0.0.1:8080/v1",
               model: mlx-community/Qwen3-8B-4bit, family: qwen, local: true}
subjects:
  - name: local
    description: Qwen 8B on MLX
    config:
      system_prompt: Reply with only a Python code block.
      tools: []
      runtime: {type: model}
sensors:
  - type: function-test
    timeout: 5
trials: 5
repeats: 3
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | directory name | Experiment identifier; results live under it |
| `description` | string | `""` | |
| `engine` | string or mapping | `native` | `native` \| `inspect`, or `{type: ..., <engine options>}` |
| `models` | mapping | none | hardline registry for `model`-runtime subjects and judges; absent → hardline's own config tiers |
| `subjects` | list | `[]` | `{name, description?, config}` — see below |
| `sensors` | list | `[{type: activation}]` | Sensor configs; several are combined into one composite sensor |
| `sensor` | string or mapping | | Single-sensor shorthand, normalised to `sensors` |
| `trials` | int | `5` | Trials per probe per repeat; `--trials` overrides |
| `repeats` | int | `1` | Whole-run repeats; the noise floor is the spread of pass rates across them |

**Any other top-level key is an error** naming the file and the legal keys.

### Subjects

A subject's `config` is an agent definition plus the runtime that plays it.

| Key | Type | Description |
|-----|------|-------------|
| `system_prompt` | string | In `subjects/*.md`, the file body |
| `model` | string | Runtime-interpreted: an SDK model alias for `claude-sdk`, a registry name for `model` |
| `tools` | list or comma string | Omit for the runtime's default; `[]` for **no tools** |
| `max_turns` | int ≥ 1 | Default 1 |
| `runtime` | mapping | `{type: <runtime>, ...options}` |

Any other key is rejected — deployment settings belong under `runtime`, which says so in the
error. Runtime types and their options:

| `runtime.type` | options | notes |
|---|---|---|
| `claude-sdk` | `permission_mode` (default `default`), `cwd` (default: the experiment directory), `setting_sources`, `plugins`, `fallback_model`, `agents` | relative plugin paths resolve against `cwd` |
| `model` | `models`, `default_model`, `temperature`, `max_tokens` | `models` defaults to the experiment's `models` section; refuses subjects that declare tools |
| `simulated` | none | ix's simulator — canned `mock_response`, else a seeded 90/10 activation split |
| `mock` | none | matrix's own deterministic offline runtime — canned replies keyed by task, not ix's simulator |

`--simulate` replaces every subject's runtime with ix's simulator for that run (`--mock` is a
deprecated alias). A subject with no `runtime.type` fails, naming the registered types.

### Engines

| `engine` | options | executes a repeat as |
|---|---|---|
| `native` | `concurrency` (default 1, trials in flight at once) | one four-node matrix DAG per probe × trial |
| `inspect` | `log_dir` (default `results/inspect`), `max_samples` (default 1), `fail_on_error` (default false) | one Inspect AI task: probes → samples, trials → epochs; writes an `.eval` log |

`--engine` overrides the configured engine for a run; the other engine's options are dropped.

### Sensors

| `type` | options | measures |
|---|---|---|
| `activation` | `expected_skill` (probe metadata overrides) | whether the expected skill was invoked |
| `function-test` | `timeout` (seconds, default 30) | runs the response's code against the probe's `test_cases` **in-process** (SECURITY.md) |
| `tool-usage` | `expected_tool` | whether a tool was called |
| `outcome` | `graders_module` (path relative to the experiment) | custom grader functions |
| `deepeval` | `metric`, `threshold`, `judge` (a registry name), `criteria` | a DeepEval metric; records judge and subject families |

---

## Probes — `tasks/*.md`

```markdown
---
id: palindrome
function_name: is_palindrome
test_cases:
  - {input: "racecar", expected: true}
  - {input: "hello", expected: false}
---
Write a function `is_palindrome(s)` that returns True if the string reads the same both ways.
```

The body is the prompt, sent verbatim. `id` defaults to the file stem and is always coerced to
a string. Every other frontmatter key goes into the probe's `metadata`, which sensors read:
`expectation` (`must_trigger` | `should_not_trigger` | `acceptable`) and `expected_skill` for
activation, `function_name` and `test_cases` for function-test, `mock_response` for the
simulator.

## Subjects — `subjects/*.md`

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

Frontmatter is the subject's config (`name` and `description` lifted out); the body is its
`system_prompt`. When `subjects/` exists, `subjects:` in the YAML is ignored.

---

## Trials — `<run_id>/trials.jsonl`

One `TrialRecord` per line, for every trial of every repeat — written as the run goes, so a
summary's numbers can be audited against the evidence they were computed from.

| Field | Description |
|-------|-------------|
| `run_id`, `run_index` | the run this trial belongs to, and which repeat (0-based) |
| `probe_id`, `trial_index` | which probe, which trial of that probe |
| `response` | the agent's response, serialised — content, tool calls, usage, `family`, `model` |
| `error` | set instead of `response` when the agent raised |
| `readings` | the sensor's `Reading`s for this trial |

## Results — `results/<subject>/summary-latest.json`

Written after every run of that subject and archived as `summary-<run_id>.json` alongside it.
`ix results <experiment>` reads every subject's `summary-latest.json`; `ix results <experiment>
--subject <name>` reads one.

| Field | Description |
|-------|-------------|
| `experiment_name`, `subject`, `run_id` | |
| `probe_results[]` | `probe_id`, `score` (mean trial score), `passed` (a **majority of trials passed**, by the sensor's verdict — never re-derived from the score), `trial_scores`, `details` |
| `pass_rate`, `n_probes` | fraction of probes that passed, and how many probes that is over |
| `mean_score`, `min_score`, `max_score` | over probe scores |
| `pass_rate_stderr`, `mean_score_stderr` | CLT standard error over the `n_probes` probes sampled; `stderr_method` names it (`"clt-over-probes"`); `null` below two probes |
| `repeats`, `per_run_pass_rates`, `per_run_mean_scores` | one pass rate / mean score per repeat |
| `noise_floor_sd`, `score_noise_floor_sd` | standard deviation of `per_run_pass_rates` / `per_run_mean_scores` across repeats; `null` with one repeat |
| `confusion_matrix` | `{expected_skill: {activated_skill: count}}` from activation readings |
| `families` | the model families that actually answered, read off the trial responses — `("simulated",)` under `--simulate`, never asserted from config |
| `measured_a_model` | computed: `false` when `families` is empty or only `simulated`/`mock` — a harness check, not a measurement |
| `engine` | `native` or `inspect` |
| `engine_artifacts` | e.g. `inspect_log:<path to .eval>`, one per repeat |
| `trials_log` | this run's `trials.jsonl`, relative to the experiment directory |
| `config_hash`, `run_timestamp`, `ix_version` | provenance |
| `status` | computed from `pass_rate`: `excellent` (1.0), `good` (≥ 0.85), `needs_work` (≥ 0.5), `poor` |

## Comparison — `ix compare <experiment> A B`

Not persisted to disk; printed or emitted as JSON (`--format json`) from `compare_results(A, B)`
over the two subjects' latest `ExperimentResults`.

| Field | Description |
|-------|-------------|
| `experiment`, `a`, `b`, `n` | the experiment, the two subject names, and how many probes they share |
| `pass_rate_a`, `pass_rate_b` | each subject's pass rate over the shared probes |
| `mean_delta` | mean of (score B − score A) over shared probes |
| `delta_stderr`, `ci95` | standard error and 95% CI of `mean_delta` (paired, over √n); `null` below two shared probes |
| `a_only_passed`, `b_only_passed` | probes whose verdict flipped, counted each way |
| `noise_floor_sd` | the larger of the two subjects' `score_noise_floor_sd`, where measured |
| `unmatched` | probe ids present in only one subject's results |
| `warning` | set when either subject's results show `measured_a_model = false` |
| `probes[]` | per shared probe: `score_a`, `score_b`, `passed_a`, `passed_b`, and computed `delta` |
| `verdict` | computed: `"b_better"` / `"a_better"` only when the CI excludes 0 *and* `abs(mean_delta)` clears `noise_floor_sd`; otherwise `"inconclusive"` |

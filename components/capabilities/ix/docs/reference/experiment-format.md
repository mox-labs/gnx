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
        ├── summary-<UTC timestamp>.json
        ├── summary-latest.json
        └── inspect/           # engine: inspect only — one .eval log per repeat
```

A lab is any directory containing at least one subdirectory with an `experiment.yaml`. ix
finds it from `--lab <name>` (relative to the project root) or by walking up from the working
directory.

---

## experiment.yaml

```yaml
name: local-codegen
description: A local model writes small functions; function-test grades them.
engine: inspect                  # or native (default), or {type: inspect, max_samples: 1}
models:                          # a modelrt registry section
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
| `models` | mapping | none | modelrt registry for `model`-runtime subjects and judges; absent → modelrt's own config tiers |
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
| `mock` | none | ix's simulator — canned `mock_response`, else a seeded 90/10 activation split |

`--mock` replaces every subject's runtime with the simulator for that run. A subject with no
`runtime.type` fails, naming the registered types.

### Engines

| `engine` | options | executes a repeat as |
|---|---|---|
| `native` | none | one four-node matrix DAG per probe × trial |
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

## Results — `summary-latest.json`

Written after every run and archived as `summary-<timestamp>.json`.

| Field | Description |
|-------|-------------|
| `experiment_name`, `subject` | |
| `probe_results[]` | `probe_id`, `score` (mean trial score), `passed` (a **majority of trials passed**, by the sensor's verdict — never re-derived from the score), `trial_scores`, `details` |
| `pass_rate` | fraction of probes that passed |
| `mean_score`, `min_score`, `max_score` | over probe scores |
| `repeats`, `per_run_pass_rates` | one pass rate per repeat |
| `noise_floor_sd` | standard deviation of `per_run_pass_rates`; `null` with one repeat |
| `confusion_matrix` | `{expected_skill: {activated_skill: count}}` from activation readings |
| `engine` | `native` or `inspect` |
| `engine_artifacts` | e.g. `inspect_log:<path to .eval>`, one per repeat |
| `config_hash`, `run_timestamp`, `ix_version` | provenance |
| `status` | computed from `pass_rate`: `excellent` (1.0), `good` (≥ 0.85), `needs_work` (≥ 0.5), `poor` |

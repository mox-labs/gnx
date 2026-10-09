# Running experiments

From an empty directory to a comparison. Every command and file on this page was run as
written, on the simulator or matrix's `mock` runtime; neither calls a model.
`ix <command> --help` has the rest.

## 1. Create a lab and an experiment

```bash
ix lab init lab
ix experiment init routing --lab lab
```

```
lab/
└── routing/
    ├── experiment.yaml      # name, description, sensor
    ├── tasks/example.md     # an example probe: edit it, add more beside it
    └── subjects/agent.md    # a starter subject on the simulator
```

A lab is any directory holding experiment directories. `--lab <name>` is resolved against
the project root (the nearest directory with `.git`); without it, ix walks up from the
working directory, or uses `lab:` from your `ix:` config. Neither `init` overwrites anything.

The scaffold leaves `trials`, `repeats` and `engine` out of `experiment.yaml`, so your `ix:`
defaults apply (5 trials, 1 repeat and the native engine when you have none). A value written
in the experiment file wins over the defaults.

## 2. Write probes

One markdown file per probe in `tasks/`. The frontmatter is ground truth for the sensor; the
body is the prompt, sent verbatim.

```markdown
---
id: vague-ask
expectation: must_trigger
expected_skill: intent-hardening
---
I want to make our onboarding better somehow. Where do I start?
```

Write decoys too: `should_not_trigger` probes pitched near the skill's vocabulary without
needing it. A routing measurement with no decoys cannot tell a precise description from a
greedy one.

```markdown
---
id: decoy-typo
expectation: should_not_trigger
expected_skill: intent-hardening
x-why: near the skill's vocabulary ("spec") without needing it
---
Fix the typo in this spec heading: "Requirments".
```

Every frontmatter key must be one a configured sensor reads, one of ix's own (`mock_response`,
`tags`, `notes`, `title`, `description`), or start with `x-`. A misspelt key is refused when
the experiment loads:

```
Error: typo: 1 problem(s)
  - probe 'p1': key 'expectaton' is read by none of this experiment's sensors
(they read: description, expectation, expected_skill, mock_response, notes,
tags, title); fix the name, or prefix it x- if it is a note
```

## 3. Choose the subjects

Subjects go in `experiment.yaml`, or one per file in `subjects/`. If `subjects/` holds any
`.md` files, those are used and the YAML list is ignored, so delete the starter
`subjects/agent.md` when you list subjects in YAML.

```yaml
name: routing
description: Does the intent-hardening skill fire when it should, and only then?
sensor: activation
trials: 5
repeats: 3
subjects:
  - name: baseline
    config:
      runtime: {type: simulated}
  - name: candidate
    config:
      system_prompt: Use a skill only when the request is underspecified.
      runtime: {type: simulated}
```

For a real measurement, change `runtime` to `{type: claude-sdk, ...}` or `{type: model}`, or
name an agent you configured in matrix (step 7).

## 4. Validate and plan

```bash
ix experiment validate routing --lab lab
ix run routing --lab lab --plan
```

`validate` composes everything a run would (sensors, engine, every subject's agent) and runs
nothing. It reports every problem, not just the first, and exits 3 if there are any. It also
checks that local plugin paths a `claude-sdk` subject loads exist.

`--plan` (also `--dry-run` or `-n`) prints, per subject, the runtime, probes × trials ×
repeats = sessions, and whether those sessions are live:

```
┏━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Subject   ┃ Runtime   ┃ Sessions (probes × trials × repeats) ┃ Live    ┃
┡━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ baseline  │ simulated │ 3 × 5 × 3 = 45                       │ offline │
│ candidate │ simulated │ 3 × 5 × 3 = 45                       │ offline │
└───────────┴───────────┴──────────────────────────────────────┴─────────┘
Total: 90 sessions, 0 live
```

A runtime is live when its registered effects include `model` or `network`, or are unknown:
it may spend money and, on `claude-sdk`, run tools. ix's simulator and matrix's `mock` are
offline. For a live `claude-sdk` subject the column also shows its `permission_mode`, whether
set inline or on the named agent's runtime. With no `--subject`, a run that would start more than one subject
when any of them is live is refused (exit 3). Name a `--subject` (repeatable) or pass `--all`.

## 5. Run

```bash
ix run routing --lab lab --seed 42
```

Progress goes to stderr: a bar on a terminal, a line per repeat, a line per probe. The
results table goes to stdout. `--trials N` and `--repeats N` override the experiment for one
run; `--engine inspect` runs the same experiment on Inspect AI (it needs `ix[inspect]`).

`--simulate` runs every subject on the simulator for this run, whatever its runtime, and
saves the results as `<subject>@simulated` so they never replace that subject's real ones.
Use it to prove an experiment works before spending anything. `--seed` makes simulated draws
reproducible; two simulated subjects with the same seed draw identically.

Set `repeats: 3` or more before you compare subjects. One repeat gives no noise floor.

## 6. Read the results

```bash
ix results routing --lab lab
ix results routing --lab lab --subject candidate --format json
```

```
  Pass rate                       100.0% ± 0.0%
  Mean score                      91.1% ± 2.2%
  Min / max                       86.7% / 93.3%
  Probes                          3  (± is 1 standard error over probes)
  Per-repeat rates                100.0%, 100.0%, 100.0%
  Noise floor, pass rate (sd)     0.0%
  Noise floor, mean score (sd)    10.2%
  Engine                          native
  Answered by                     simulated
  Trials                          results/candidate/20261009T113309100237Z/tr…

┃ Probe         ┃ Verdict ┃  Score ┃ Trials          ┃ Details                 ┃
│ decoy-typo    │ PASS    │    87% │ █████▁███████▁█ │ expected=intent-harden… │
...
Status: harness only (EXCELLENT)
No real model answered: this checks the harness, not the subject.
```

| Line | Meaning |
|------|---------|
| Pass rate | fraction of probes where a majority of trials passed, by the sensor's verdict; ± one standard error over the probes |
| Mean / min / max score | over each probe's mean trial score |
| Noise floor | standard deviation of the per-repeat pass rate and mean score |
| Answered by | the model families that produced the responses, read off the responses |
| Harness faults | trials that never had a fair chance; **not** scored. Rerun them |
| Unmeasured probes | probes whose every trial was a harness fault; left out of every rate |
| Sensor faults | trials the sensor crashed on; scored as failures |
| Sessions cut short | sessions that ended other than `completed` (`max_turns`, ...), counted; they were judged as they stood |
| Trials | one bar per trial, its height the trial's score; the full record is in `trials.jsonl` |
| Confusion matrix | activation only: expected skill × the skill that fired |
| Status | a grade of the pass rate, or "harness only" (JSON `status: "unmeasured"`) when no real model answered |

When harness faults appear, the run is incomplete: the subject was not given every trial.
Rerun before reading the numbers. The trial's `error` in `trials.jsonl` says which kind
(`rate_limited`, `timeout`, `setup`, `unclassified`, ...). When sensor faults appear, the experiment has a bug; the
`details` and `trials.jsonl` say what the sensor raised.

Each run writes `results/<subject>/<run_id>/trials.jsonl`, one record per trial with the
response, error and readings, and `results/<subject>/summary-latest.json`, which is what
`ix results` reads.

## 7. Share runtimes and agents through matrix

Runtimes and agents live in matrix's config, so every tool built on matrix sees the same
ones. ix's own defaults live in an `ix:` section. Both are read from the directory you run ix
in (and from `~/.matrix/config.yaml`, `~/.ix/config.yaml`, `$MATRIX_CONFIG`, `$IX_CONFIG`).

`matrix.yaml`, defining two runtimes and two agents. The runtimes here are matrix's `mock`,
which returns a fixed reply; for a real measurement, swap in
`{type: claude-sdk, permission_mode: default, setting_sources: []}` or `{type: model}`.

```yaml
matrix:
  runtimes:
    canned:
      type: mock
      default: "Check the token expiry and the retry budget before merging."
    terse:
      type: mock
      default: "Looks fine."
  agents:
    reviewer:
      runtime: canned
      system_prompt: You review pull requests. Name the concrete risks.
    skimmer:
      runtime: terse
      system_prompt: You review pull requests quickly.
```

`ix.yaml`, your defaults:

```yaml
ix:
  lab: lab
  trials: 3
  repeats: 2
```

`lab/review/experiment.yaml`. It sets no `trials` or `repeats`, so the `ix:` defaults apply.
Two subjects name configured agents; the third is defined inline.

```yaml
name: review
description: Does the reviewer name the risks a careful human would?
sensor: outcome
subjects:
  - name: reviewer
    config: {agent: reviewer}
  - name: skimmer
    config: {agent: skimmer}
  - name: inline
    config:
      system_prompt: Reply with one sentence.
      runtime: {type: mock, default: "The token expiry is the risk here."}
```

`lab/review/tasks/auth-change.md`:

```markdown
---
id: auth-change
expected_facts: [token expiry, retry]
---
Review this change: the auth client now caches tokens for 24 hours and retries failed calls forever.
```

`lab/review/tasks/expiry-only.md`:

```markdown
---
id: expiry-only
expected_facts: [token expiry]
---
Review this change: session tokens no longer expire.
```

```bash
matrix config --tool ix --sources   # which files were read, lowest priority first
ix experiment validate review       # no --lab: ix.yaml names it
ix run review --plan                # reviewer, skimmer, inline: mock, 2 × 3 × 2 = 12 each
ix run review                       # all offline, so no --all is needed
ix compare review skimmer reviewer
```

```
  Shared probes                   2
  Pass rate                       skimmer 0.0% → reviewer 100.0%
  Mean score Δ (B−A)              +100.0% ± 0.0%  95% CI [+100.0%, +100.0%]
  Verdicts flipped                skimmer only: 0 · reviewer only: 2
  Noise floor, mean score (sd)    0.0%
Warning: 'skimmer' and 'reviewer' answered by no real model (simulator or mock):
this compares the harness, not the subjects

Verdict: inconclusive — the difference is within the uncertainty
```

A mock's fixed replies make the numbers look decisive, but no model answered on either side,
so `ix compare` names no winner. Point the agents at real runtimes to measure them.

A subject that names an agent may also override its definition fields
(`{agent: reviewer, model: haiku}`), but may not also give a `runtime`: the agent brings its
own. A subject defined inline gets `max_turns: 1` unless it sets one.

## 8. Compare two subjects

```bash
ix compare routing --lab lab baseline candidate
```

`ix compare` pairs the two subjects' latest results probe by probe and reports the mean
difference (B − A), its standard error, a 95% interval, the probes whose verdict flipped,
and the noise floor. The verdict is `b_better` or `a_better` only when a real model answered
on both sides, the interval excludes zero, and the difference clears the larger noise floor.
It is `inconclusive` otherwise, and always when either side has harness or sensor faults. Either name may be
`<subject>@simulated`.

## 9. Add a sensor

When none of the built-in sensors judges what you care about, write one in your own package.
A sensor is any object with a `name` and `measure(trial) -> list[Reading]`. It reads ground
truth from the probes once, when it is built.

```python
# acme_sensors/__init__.py
from pydantic import BaseModel, ConfigDict

from ix.domain.types import Reading


class BrevityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_chars: int = 280


class BrevitySensor:
    name = "brevity"

    def __init__(self, config: BrevityConfig, probes) -> None:
        self._limits = {p.id: p.metadata.get("max_chars", config.max_chars) for p in probes}

    def measure(self, trial):
        length = len(trial.response.content)
        limit = self._limits.get(trial.probe_id)
        passed = length <= limit
        return [
            Reading(
                sensor_name=self.name,
                probe_id=trial.probe_id,
                trial_index=trial.trial_index,
                passed=passed,
                score=1.0 if passed else 0.0,
                metrics={"chars": length, "limit": limit},
                details=f"{length} chars, limit {limit}",
            )
        ]


def build(config: BrevityConfig, *, probes):
    return BrevitySensor(config, probes)


build.truth_keys = frozenset({"max_chars"})  # the probe keys this sensor reads


def register(registry) -> None:
    registry.register(
        "sensor",
        "acme.v1.sensor.brevity",
        build,
        config=BrevityConfig,
        needs={"probes"},
        effects=set(),
        summary="Is the answer within the probe's max_chars?",
    )
```

```toml
# pyproject.toml
[project]
dependencies = ["ix @ git+https://github.com/mox-labs/gnx#subdirectory=components/capabilities/ix"]

[project.entry-points."matrix.extensions"]
acme-sensors = "acme_sensors:register"
```

Depend on ix by its git URL, not as a bare `ix`: that name on PyPI is an unrelated package
(SECURITY.md I-8).

- `config` is validated from the sensor's entry in `experiment.yaml`, minus `type`.
- `needs` names what composition hands `build`: `probes`, and `judge` if the sensor asks a
  model to grade. `judge(name)` returns a matrix agent: the configured agent of that name, or
  that model on matrix's `model` runtime.
- `effects` says what the sensor may touch (`model`, `network`, `subprocess`, `filesystem`);
  leave it out and it reads as unknown, never as none.
- `build.truth_keys` lets ix refuse probe keys no sensor reads. Without it, any key passes.
- The sensor never sees a trial whose session failed. The experiment records those as faults
  before it calls any sensor, so `trial.response` is always set. If `measure` raises, the
  reading becomes a sensor fault.

Install the package next to ix and name the sensor by its type URL:

```yaml
sensor: {type: acme.v1.sensor.brevity, max_chars: 120}
```

`matrix catalog --point sensor` lists every installed sensor; `matrix describe <type URL>`
prints one entry's config schema, needs and effects. An engine is added the same way, at
point `engine`, with `run(EngineRun) -> EngineOutcome` (see the domain models reference).

## Exit codes and errors

| Exit | Meaning | Next step |
|------|---------|-----------|
| 0 | success | |
| 1 | failure not otherwise classified (an engine error, a runtime error) | read the message |
| 2 | usage: a bad flag or argument | `ix <command> --help` |
| 3 | config: invalid experiment, failed validate, invalid experiment in `list`, refused live run | fix the file named, or name `--subject` / pass `--all` |
| 4 | not found: lab, experiment, subject or saved results | the `fix` lists what exists |
| 5 | transient: a retryable runtime failure (rate limit, outage, timeout) outside a trial | retry later |
| 6 | auth: the provider refused the credentials | fix the key; do not retry |

With `--format json` an error is one JSON line on stderr:

```json
{"error": {"kind": "not_found", "message": "experiment 'nope' not found in /…/lab", "fix": "ix experiment list --lab lab"}}
```

A session that fails inside a trial is not an error exit. It becomes a failed reading with a
fault, and the run finishes.

## When it disagrees with you

- **A must-trigger probe fails.** Read the confusion matrix. A different skill firing is a
  description collision; nothing firing is a description that does not say when to use it.
- **A decoy fires.** The description is greedy. Narrow its trigger phrasing.
- **Results swing between runs.** Raise `repeats` and compare against the noise floor before
  concluding anything.
- **Harness faults.** The provider throttled, timed out or refused credentials, or the
  runtime broke. Rerun; the score so far covers only the trials that ran fairly. A session
  that legitimately runs long needs a larger `trial_timeout_s` on the native engine
  (`engine: {type: native, trial_timeout_s: 1200}`; `null` waits forever).

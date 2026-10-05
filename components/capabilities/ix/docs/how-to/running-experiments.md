# Running experiments

From an empty directory to a result with an error bar. Every command here is ix's CLI as it
exists; `ix <command> --help` has the rest.

## 1. Create a lab and an experiment

```bash
ix lab init lab
ix experiment init routing --lab lab
```

```
lab/
└── routing/
    ├── experiment.yaml        # name, engine, sensor, trials
    ├── tasks/                 # probes go here
    └── subjects/agent.md      # a starter subject on the simulator (runtime: simulated)
```

A new experiment runs as soon as it has one probe, on the simulator, before any model is
configured.

## 2. Write probes

One markdown file per probe in `tasks/`. Frontmatter is metadata the sensor reads; the body is
the prompt, sent verbatim.

```markdown
---
id: vague-ask
expectation: must_trigger          # activation: must_trigger | should_not_trigger | acceptable
expected_skill: intent-hardening
---
I want to make our onboarding better somehow. Where do I start?
```

Write decoys too — `should_not_trigger` probes pitched near a skill's vocabulary without
needing it. A routing measurement with no decoys cannot tell a good description from a greedy
one.

## 3. Choose who answers

A subject is an agent definition plus the runtime that plays it. Edit `subjects/agent.md`, or
list subjects in `experiment.yaml`:

```yaml
subjects:
  - name: live
    config:
      max_turns: 1
      runtime:
        type: claude-sdk
        setting_sources: []                # hermetic: nothing from ~/.claude leaks in
        permission_mode: bypassPermissions # unattended — logged at WARNING
        plugins: [{type: local, path: ../../plugins/intent-hardening}]
  - name: local
    config:
      system_prompt: Answer briefly.
      tools: []
      runtime: {type: model}               # uses the experiment's `models:` registry
```

## 4. Validate, then run on the simulator

```bash
ix experiment validate routing --lab lab
ix run routing --lab lab --simulate --seed 42
```

`--simulate` swaps every subject onto the simulator: canned `mock_response`s where a probe has
one, otherwise a seeded 90/10 activation split (`--mock` still works as a deprecated alias).
**It proves the harness, not the thing.** A simulated pass rate says the pipeline works; it
says nothing about your catalog.

## 5. Run for real

```bash
ix run routing --lab lab --subject live --trials 1        # one trial per probe to start
ix run routing --lab lab --subject live                   # the configured trials × repeats
ix run routing --lab lab --subject local --engine inspect # same experiment on Inspect AI
ix run routing --lab lab                                  # every subject, in turn
```

Results are keyed by subject — running one subject never overwrites another's — so running
every subject is the normal way to set up a comparison.

Set `repeats: 3` or more before you compare two subjects. One run's pass rate has no error
bar; the **noise floor** — the spread of pass rates across repeats — is what says whether a
difference between two subjects is bigger than the run-to-run wobble.

While it runs, a terminal shows a bar that advances as each trial is measured, with a running
count of passes and fails; a line per repeat (`repeat 2/3: 87.5%`) and, at the end, a line per
probe (`stand-up-an-org: PASS (score=100%)`). All of that goes to stderr, so stdout carries only
the results. Piped, the bar is omitted and the lines remain.

In the results table, **Trials** draws one bar per trial — its height the trial's score — so
`█████▁███████▁█` is a probe that failed twice in fifteen. The trial log holds each one in full.

## 6. Read the results

```bash
ix results routing --lab lab
ix results routing --lab lab --format json
```

| Metric | Meaning |
|--------|---------|
| **Pass rate** | fraction of probes where a majority of trials passed, by the sensor's own verdict; reported with ± one standard error over the probes sampled |
| **Mean / min / max score** | over per-probe mean trial scores; mean score also carries its standard error |
| **Noise floor (sd)** | standard deviation of per-repeat pass rate / mean score — compare differences against it |
| **Answered by** | the model families that actually produced the responses measured, read off the responses — `simulated` under `--simulate`, never asserted from config |
| **Confusion matrix** | activation only: expected skill × the skill that actually fired |

Results live under `results/<subject>/<run_id>/trials.jsonl` (one `TrialRecord` per trial, every
repeat) and `results/<subject>/summary-latest.json` (the result `ix results` reads); the path to
that run's trials is in the summary's `trials_log`.

On the Inspect engine each repeat also leaves an `.eval` log in `results/inspect/` with every
prompt, response and score; `inspect view` opens it, and the path is recorded in the summary's
`engine_artifacts`.

## 7. Compare two subjects

```bash
ix compare routing live local
```

Pairs `live` and `local`'s latest results probe by probe and reports the mean delta (B − A),
its standard error, a 95% CI, and which probes' verdicts flipped. The verdict is
`inconclusive` unless the CI excludes zero *and* the delta clears the larger subject's noise
floor — a difference that looks real on `repeats: 1` is not, by construction, enough. If
either subject's results show no real model answered (`--simulate`, or matrix's `mock`
runtime), the comparison carries a warning: it checked the harness, not the subjects.

## When it disagrees with you

- **A must-trigger probe fails** — read the confusion matrix. A different skill firing is a
  description collision; nothing firing is a description that does not say when to use it.
- **A decoy fires** — the description is greedy. Narrow its trigger phrasing.
- **Results swing between runs** — raise `repeats` and look at the noise floor before
  concluding anything.

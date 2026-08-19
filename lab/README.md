# lab — experiments against the catalog

A **lab** is a directory of experiments; each experiment is a directory with an
`experiment.yaml` and a `tasks/` folder of probes. This lab is where gnx measures its own
claims instead of asserting them.

```
ix run catalog-routing  --lab lab --mock --seed 42
ix run sensor-integrity --lab lab --mock
ix results catalog-routing --lab lab
```

Run from the repo root, with the capabilities workspace:

```
uv --project components/capabilities run ix run catalog-routing --lab lab --mock --seed 42
```

## The experiments

| Experiment | Question | Sensor | Needs credentials |
|---|---|---|---|
| `catalog-routing` | Does the right skill trigger, and do decoys stay quiet? | `activation` | live subject only |
| `sensor-integrity` | Does the grading path itself grade correctly? | `function-test` | no |

The live subject needs no API key: the Claude Agent SDK drives the authenticated `claude`
CLI. `matrix`'s runtime pops `CLAUDECODE` for the duration of a call precisely so this works
from inside a Claude Code session.

```
ix run catalog-routing --lab lab --subject catalog-live --trials 1
```

### catalog-routing

The catalog's whole promise is that a description makes the right component fire at the
right moment. That is a measurable claim, and this is the measurement: `must_trigger`
probes name a real ask, `should_not_trigger` probes are decoys pitched near a skill's
vocabulary without needing it.

`--mock` exercises the full harness — DAG, store, aggregation, confusion matrix — on a
simulated 90/10 activation split. **It does not measure the catalog.** A mock run that
reports 90% tells you the plumbing works; only a live run tells you the descriptions do.
Both are useful and they are not the same claim.

### First live reading — 2026-08-18, 24 sessions

`--subject catalog-live --trials 1`, three repeats. **75.0% pass**, per-run 75.0 / 87.5 /
75.0, noise floor sd 0.072.

| probe | expected | fired | |
|---|---|---|---|
| `architecture-bottleneck` | `aces` | `antifragile:aces` | 3/3 |
| `vague-ask-needs-hardening` | `intent-hardening` | `intent-hardening:intent-hardening` | 3/3 |
| `security-review-agent-config` | `trust-boundaries` | `guild-arch:trust-boundaries` | **1/3** |
| `stand-up-an-org` | `dao` | — | **0/3** |
| all four decoys | — | — | 4/4 correct |

Two findings, and the second is the useful one:

**No false positives.** Every decoy stayed quiet, including the ones pitched deliberately
close to a skill's vocabulary. The catalog does not over-trigger.

**`dao` never fires** — on a probe whose wording ("Set up the harness for it… a bench…
a verification gate") is drawn from `dao`'s own description, which literally lists "set up
the harness for this project" and "give this project a bench" as triggers. The trigger
phrases are present and the skill still does not activate.

`max_turns` was ruled out as the cause: `stand-up-an-org` fires nothing at 1 turn or 3,
and `security-review` fires at both. So this is a description problem, not a measurement
artifact. `dao`'s description is 854 characters that open with trigger phrases and then
spend most of their length on the four-phase method and the eleven-component spec, closing
on a negation ("NOT for running work inside an already-standing dao"). `trust-boundaries`
is 859 and fires only a third of the time. `aces` is 909 and fires reliably, so length
alone is not the explanation — but these are the two worst offenders against the 260-char
catalog target and the two that fail, which is where I would look first.

**Follow-up, same day — `dao` only routes on its own jargon.** Four phrasings, one session
each, `max_turns: 1`:

| | prompt | fired |
|---|---|---|
| A | "Here's a new project. Set up the harness for it — I want a bench and a verification gate…" | — |
| B | "Set up this project with a harness." | — |
| C | "Set up a **dao** for this project." | `dao:dao` |
| D | "Stand up an **agent organization** for this project." | `dao:dao` |

The skill fires only when the asker already names it. Both plain-language forms fail —
including "set up the harness for this project", which is quoted verbatim in `dao`'s own
description as a trigger.

That is worse than a low pass rate. **A catalog entry that only routes for people who
already know its name is undiscoverable**, and discovery is the entire job of a catalog
description. The listed triggers are decoration; the jargon is doing all the work.

This is the reading the C14 listing-budget note predicted, now with behavioural evidence
rather than budget arithmetic, and it lands on exactly the pragmatic-terminology point:
`dao`'s description spends its length on the four-phase method and the eleven-component
spec, so the plain-language triggers at the front are buried under doctrine.

Not yet acted on. When it is, this experiment is how we find out whether the fix worked —
which is the reason to write the measurement before the fix, not after.

### sensor-integrity

Runs offline, and deliberately so. Each probe carries a `mock_response` holding the code a
subject "returned", so the experiment measures **the sensor** rather than a model:
extraction from markdown fences, execution under timeout, and scoring against test cases.

That path is also ix's sharpest trust boundary — it imports and runs model-generated
Python in-process (see `components/capabilities/ix/SECURITY.md`, boundary 1). An eval that
never exercises it leaves the riskiest code unproven, so the probe set includes code that
fails its tests and code that raises, not only code that passes.

## Adding an experiment

```
lab/<name>/
  experiment.yaml     # subjects, sensors, trials
  tasks/*.md          # one probe per file: frontmatter + prompt body
```

Probe frontmatter is untyped YAML read into `Probe.metadata`; each sensor reads the keys
it needs (`expectation`, `expected_skill`, `function_name`, `test_cases`, `mock_response`).

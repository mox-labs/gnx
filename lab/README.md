# lab — experiments against the catalog

A **lab** is a directory of experiments; each experiment is a directory with an
`experiment.yaml` and its probes. This lab is where gnx states its own claims as
measurements instead of assertions.

```
ix validate lab/catalog-routing      # every problem at once, nothing run
ix plan lab/catalog-routing          # the decision, the trials, the effects, the spend
ix register lab/catalog-routing      # freeze the design before a run
ix run lab/catalog-routing           # real agent sessions: this spends money
```

Run from the repo root, with the capabilities workspace:

```
uv --project components/capabilities run ix plan lab/catalog-routing
```

`just lab` validates and plans every experiment here, and CI runs it. Neither runs one: a
run starts real agent sessions on the `claude-sdk` runtime and spends up to the
experiment's ceiling. Runs are kept under each experiment's `.ix/runs/`, which git ignores.

## The experiments

| Experiment | Genre | Question | Decides on |
|---|---|---|---|
| `catalog-routing` | `evaluate` | Does the right skill trigger, and do decoys stay quiet? | `activated_correctly >= 0.9` |

### catalog-routing

> **Known gap (2026-09-24).** Five of the eight probes expect skills that left the catalog on
> 2026-08-19 — `aces`, `trust-boundaries` and `dao` are in `incubator/`. The subject loads
> the three plugins that ship, so a run would fail those probes by construction. The
> measurement below predates the cut.

The catalog's whole promise is that a description makes the right component fire at the
right moment. That is a measurable claim, and this is the measurement: `must_trigger`
probes name a real ask, `should_not_trigger` probes are decoys pitched near a skill's
vocabulary without needing it.

### First live reading — 2026-08-18, 24 sessions

Taken with the harness ix had at the time. One session per probe, three repeats. **75.0% pass**, per-run
75.0 / 87.5 / 75.0, noise floor sd 0.072.

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

### sensor-integrity (moved)

This experiment fed canned responses through the function-test grader to check
extraction, execution under a time limit and scoring. It tested the grader, not a subject,
so it lives with ix now: `components/capabilities/ix/examples/harness-integrity` gives
answers of known worth on matrix's `mock` runtime to the real `function-test` and `outcome`
sensors, spends nothing, and ix's tests run it and hold each reading to its grade.

## Adding an experiment

```
lab/<name>/
  experiment.yaml     # question, genre, subjects, probes, the metric that decides
  probes/*.md         # one probe per file: frontmatter is truth, the body is the prompt
```

`ix --skill -r experiment` and `ix --skill -r kinds` teach the file: every key, each kind and
its options, each sensor and the metrics it provides. A probe's frontmatter is its truth, the reference data a sensor
measures against; a key no sensor in use reads is refused by `ix validate`, so a typo does
not pass silently.

---
title: ix
summary: Decides a question about an AI agent or a service by experiment, and reports a verdict with its interval or says why it cannot.
kind: capability
---

## What it is

ix answers a question about an AI agent or a service by running an experiment. The answer
is a verdict, `holds`, `fails` or `inconclusive`, resting on a 95% interval, and the exit
code is the verdict.

## The problem

You change an agent's system prompt and it seems better: five tries, four good answers. Is
it better, or did you get lucky? Five tries cannot say. And once you have seen the numbers,
it is easy to move the bar ("80% was always the target"), retry until a run looks good, or
grade with a test that is itself wrong.

The same holds for a service: one benchmark run says the new build is 3% slower, on a
laptop that was also compiling something else.

## How it works

An **experiment** is one YAML file: the question, the systems under test (subjects), what is
done to them (probes), and the metric that decides. Its **genre** names the kind of decision:
`evaluate` holds one subject to a threshold, `compare` asks whether a variant is better or no
worse than a control, `regress` whether a change may ship, and `characterize` and
`mutation-score` estimate without deciding. The genre supplies what you do not set: how many
repeats, their order, the check that the machine held still.

A **trial** is one subject meeting one probe once. ix fixes the order of every trial before
anything runs, so `ix plan` shows exactly what `ix run` will do.

A **registration** freezes the design beside the file before the real run: every setting,
and the digest of each skill, plugin and fixture the subject names. A run of an experiment
that changed afterwards gives no gate result.

The **verdict** comes from the readings' 95% intervals, never a point estimate. When the
interval sits wholly on one side of the threshold or margin, it holds or fails; otherwise it
is `inconclusive`, and ix says so instead of guessing.

```
ix validate  →  ix plan  →  ix register  →  ix run  →  ix report
 every         every value    the design     the exit    from the record,
 problem       and its        frozen         code is     any time
 at once       origin                        the verdict
```

## Try it

From a clone, offline, with no key ([install](../../README.md#install)). The example grades
four canned replies of known worth, so it checks ix's grading, not a model:

```bash
cd components/capabilities/ix
uv --project .. run ix run examples/harness-integrity
```

```
Error (unregistered): examples/harness-integrity/experiment.yaml is not registered, so a run of it could not be held to a design decided beforehand
fix: ix register examples/harness-integrity/experiment.yaml, then run it; or pass --exploratory for a run that gives no verdict
```

It exits 15: a run that could give a verdict is refused until the design is frozen. Run it
as exploratory, which gives no verdict:

```bash
uv --project .. run ix run examples/harness-integrity --exploratory
```

```
Verdict:      none: Dry run on mock replies: checks the harness, not the agent
…
  canned: fails
    primary: tests_passed value (rate) >= 0.5: fails
      estimate 0.25 [0.1119, 0.4687] (95%, unadjusted), wilson, n 20, decided at 0.0253 (allowed 0.05)
…
Next: this checked the harness on canned replies: if each estimate is the grade its reply should get, …
```

One canned reply in four is correct, so 0.25 is the grade the sensor should give, and it
does. The estimate carries its interval and `n`, and the `Next:` line says what this run
calls for.

## What it gives an agent

| What an agent can do | Why it can trust it | The command |
|---|---|---|
| Learn ix for the installed version | the guide is generated from the genres and kinds installed, not written beside them | `ix --skill`, `ix --skill -r agents` |
| State a claim about another agent or a service as a question | the file is checked whole: every problem at once, each with its path, kind and fix, and `allowed` and `got` so nothing is parsed from prose | `ix validate <path> --json` |
| See every setting before anything runs | the plan lists each value with its origin (`experiment`, `genre`, `kind`, `ix`, or `runtime` when only the runtime knows), what the host must have, and an upper bound on model calls; it runs nothing | `ix plan <path> --json` (`provenance`, `requirements`, `work`) |
| Check the grading for nothing | a dry run puts every session on canned replies, gives no verdict, and a registered run that a mock answered exits 16, never 0 | `ix run <path> --dry-run` |
| Register the design before the numbers exist | the registration freezes every value and the digests of the subject's files; any edit after is a deviation, and a run of it gives no gate result | `ix register <path>`; `ix deviations <path>` exits 14 on any change |
| Get a verdict with an interval, or an honest refusal | intervals are 95%, the method is checked by simulation, and a model-judged metric can never decide (`judged_gate`); evidence too thin to decide reads `inconclusive` (exit 11), and a pass rate the run is too small to show says so before it runs | `ix run <path>`: exit 0 holds, 10 fails, 11 inconclusive |
| Run longer than its own tool call, and follow it | the run is a process of its own; its status is read from the record alone | `ix run <path> --detach`, `ix status <run>`, `ix events <run> --follow` |
| Trace a verdict to the model that answered | each reading names its trial, each trial the sessions behind it, each session the model and its usage; the run records the commit and the versions | `ix report <run> --json`, and the run's JSONL tables |
| Know what to do next | every exit carries what it means and the next step for that run; every error carries a `fix` | `exit` in every `--json` document; `error.fix` in the envelope |

## What it does not do

- **It does not make an agent correct.** A verdict is about the subject, the probes and the
  files as registered; a kill rate in mutation testing measures the suite's power to show
  seeded faults, never that the code is correct.
- **It does not judge quality by model.** A judge's score is reported; it never decides.
- **It does not sandbox.** A `process` subject runs the command the file gives; a sensor that
  runs code from an agent's reply does it in a child process with a time limit, not a
  sandbox ([SECURITY.md](../../SECURITY.md)).
- **It does not resume.** A stopped run is run again; the sensors cannot be re-read over a
  stored run.
- **Some kinds are declared only.** `inspect-task` validates and plans, and `ix run` refuses
  it. Trials of `process`, `http`, `http-load`, `timing` and `memory` run one at a time.
- **A real agent evaluation calls a paid model.** Offline, ix checks the grading and runs
  services and test suites; the agent itself needs its runtime and credentials.

## Next

- [README](../../README.md): install, a first run, reading a result, your own experiment.
- `ix --skill`: the guide an agent reads; `-r NAME` for its references.
- [Evaluating an agent](../how-to/evaluate-an-agent.md),
  [comparing two builds of a service](../how-to/compare-two-services.md),
  [testing a test suite](../how-to/test-a-test-suite.md).
- [The verdict, and the method behind it](the-verdict.md), and the
  [reference pages](../index.md#reference).

# Evaluating an agent

This page takes one agent evaluation from the question to the verdict: does one round of
self-review make an agent's code pass its tests more often? On the way it covers the
subject, probes kept as files, the metric that decides, a dry run that calls no model, a
pilot, and reading the report. The last sections cover the other ways to grade an agent:
whether a skill fires (a skill of your own included), whether it used a tool, a check of
the reply's text, your own graders, and a judge.

## What you need

```bash
uv tool install "ix[claude] @ git+https://github.com/mox-labs/gnx@ix-v0.5.0#subdirectory=components/capabilities/ix"
```

The `claude` extra brings the `claude-sdk` agent runtime, which runs each session through
the Claude Agent SDK. It needs the Claude Code CLI and its credentials on this machine.
matrix's `mock` runtimes need neither: they give canned replies and call no model, and the
dry run below puts every session on one.

## 1. The question and the genre

Choose the genre by the decision. Two agent setups and the question which is better (or
worse) is `compare` with `superiority`. One setup held to a pass rate is `evaluate`. A
change that must be no worse than what it replaces is `regress`, and its `margin`, the
largest loss that still ships, is the person's to set: `ix plan` prints it on a line of its
own and says it was set in the experiment.

Lay out a lab directory:

```
lab/parse-duration/
  experiment.yaml
  probes/
    plain-units.md
    bad-input.md
```

`experiment.yaml`:

```yaml
name: parse-duration
question: Does one self-review round make the agent's parse_duration correct more often?
genre: compare
subjects:
  single:
    kind: agent-session
    runtime: claude-sdk
    model: haiku
    tools: []
  reviewed:
    kind: agent-session
    runtime: claude-sdk
    model: haiku
    tools: []
    loop: {strategy: self-review, rounds: 2}
primary: {metric: tests_passed}
comparison: superiority
metrics: [tests_score, rounds, cost]
```

## 2. The subjects

An `agent-session` subject is a fresh session for every trial, in a new temporary working
directory outside the experiment, removed when the trial ends. Its options:

- `runtime` (required): the matrix agent runtime that plays the agent. `claude-sdk` is a
  Claude Agent SDK session, with tools, turns, plugins and skills, and calls a model
  provider. `mock` gives canned replies and calls no model. Any agent runtime installed is
  named the same way, by its type URL. There is no default, so a session never calls a paid
  model because a line was left out: without one, `ix validate` reports `option_missing`
  and lists the choices.
- `definition`: a Claude Code agent file. Its body is the system prompt, and its tools,
  model and `maxTurns` apply unless the options beside it say otherwise.
- `model`, `system_prompt`, `tools` (`[]` is none at all), `max_turns`.
- `runtime_options`: the runtime's own config, passed to it as written. For `claude-sdk`:
  `permission_mode`, `setting_sources` (`[]` keeps this machine's settings and plugins out
  of the session), `fallback_model`, the SDK's own `max_budget_usd`, and inline subagents
  (`agents`); `matrix describe matrix.v1.agent-runtime.claude-sdk` lists them all. `ix
  validate` refuses a key the runtime's config does not declare (`option_unknown`), and the
  runtime's own check refuses what it cannot play before anything starts.
- `plugins`: plugin folders, relative to the experiment (a `*` matches several), handed to
  the runtime as its `plugins` with every path absolute. A runtime whose config takes none
  refuses them (`option_inapplicable`).
- `skills`: skill folders, each copied into the session's `.claude/skills`.
- `fixtures`: files and folders copied into the working directory before the session
  starts; `setup`: a command run there after them, such as `[python3, seed.py]`.
- `loop`: more rounds after the first. `{strategy: self-review, rounds: 2}` asks again with
  the previous answer until it carries `SOLUTION_VERIFIED`; `{strategy: test-feedback}`
  shows the agent which of the probe's `feedback` tests failed, until none do. Tokens,
  turns and cost add up over the rounds.

In a comparison, the first subject is the control. Change one thing between the control and
a variant, here the `loop`, so a difference has one explanation; `ix validate` warns when
more than one option differs.

## 3. The probes, as files

Leave `probes:` out of the experiment and ix reads the `probes/` folder beside it, one file
per probe. In a markdown file the body is the prompt, and the frontmatter is the probe's
**truth**: the reference the sensors grade against, which never reaches the agent. The keys
`id` (default: the file's name), `kind` (default `prompt`), `options` and `effects` are not
truth.

`probes/plain-units.md`:

```markdown
---
function_name: parse_duration
test_cases:
  - {input: ["90s"], expected: 90, description: seconds}
  - {input: ["2m"], expected: 120, description: minutes}
  - {input: ["1h"], expected: 3600, description: hours}
---

Write a Python function `parse_duration(text)` that turns "90s", "2m" or "1h" into a
number of seconds.
```

`probes/bad-input.md` weighs its cases by tier, and two of them require the code to refuse
an input:

```markdown
---
function_name: parse_duration
tiers: {basic: 0.6, refusal: 0.4}
test_cases:
  - {input: ["15m"], expected: 900, tier: basic, description: minutes}
  - {input: ["soon"], raises: ValueError, tier: refusal, description: refuses words}
  - {input: [""], raises: ValueError, tier: refusal, description: refuses nothing}
---

Write a Python function `parse_duration(text)` that turns "90s", "15m" or "1h" into a
number of seconds, and raises ValueError for anything else.
```

A case's `input` is the list of arguments. A truth key that no sensor in use reads is
refused by `ix validate` (`truth_unclaimed`), so a misspelt key does not pass silently.

A probe's options go under `options:` in its file: `collect` (files the agent writes,
read back and kept for grading), `feedback` (the tests a test-feedback loop shows),
`mock_response` (the reply a stand-in runtime such as `mock` gives) and `timeout` (600s
when absent; a session that runs past it fails the trial as the subject's, `overran`, never
missing data).

## 4. The metric that decides

You name metrics; each names the sensor that provides it. `tests_passed` comes from
`function-test`, which takes the last Python block of the reply (or a file the agent wrote,
with the sensor option `file`), and runs the truth's test cases against it in a child
process. The child gets each case's input only; ix compares what each call returned or
raised with the truth in its own process, so code that prints passing results or rewrites
its cases still fails. It gives three metrics: `tests_passed` (the weighted score reached `pass_at`, every
test by default), `tests_pass_share` (cases passed out of cases run) and `tests_score` (the
share of the weight that passed).

| Metric | Sensor | What it says |
|---|---|---|
| `activated_correctly` | `activation` | the expected skill fired, or nothing fired for a decoy |
| `tool_used_correctly`, `tool_usage_score` | `tool-usage` | the agent used a tool with the expected command and arguments |
| `tests_passed`, `tests_pass_share`, `tests_score` | `function-test` | the code passed the truth's test cases |
| `outcome_score`, `outcome_passed` | `outcome` | your own graders' score |
| `text_matched` | `text-match` | the reply starts with, ends with, equals, contains, excludes or matches what the truth says |
| `tokens_total`, `tokens_input`, `tokens_output`, `cost`, `turns`, `tool_calls`, `duration`, `rounds`, `hit_limit` | `usage` | what the session consumed and how it ended; what the runtime did not report is left out |
| `judged_score`, `judged_pass`, `judge_agreement`, `judge_inconsistent`, `judge_in_family` | `judge` | a model's score against a rubric; never decides |

`primary` is the metric that decides. Further metrics under `metrics:` are measured and
reported. A pass rate is held by its interval, so the number of trials matters: 10 trials
that all pass show a rate above 0.72 and no higher. `ix plan` says when a run of this size
cannot show what the experiment asks.

## 5. Validate and plan

```bash
ix validate lab/parse-duration
ix plan lab/parse-duration
```

```
parse-duration  compare (ix.v1.genre.compare)  plan 707e60a99b51
Question:     Does one self-review round make the agent's parse_duration correct more often?
Decides:      reviewed is better than single on tests_passed
Comparison:   superiority
Verdict:      holds / fails / inconclusive
...
Trials:       2 subjects x 1 cell x 2 probes x 10 repeats = 40, + 20 A/A = 60
...
Effects:      tools: lets a model call tools  (subjects.single, subjects.reviewed)
              process: starts a process on this machine  (subjects.single, subjects.reviewed)
              filesystem: writes files outside the run's own record  (subjects.single, subjects.reviewed)
              code: executes generated code  (sensor function-test)
```

The control also runs against itself in every block (the A/A check), so the trials are 60,
not 40: 60 sessions, each a call to a paid model on `claude-sdk`. A plan that cannot run here ends with `Runnable: no` and says
why: a runtime that cannot start, a file the subject names that is not there.

`ix validate` also refuses a session its sensors could not see into (`unobservable`): a
skill with no `Skill` tool among `tools`, skills a session's `setting_sources` would never
read, files to `collect` that no tool may write. It does so whatever the agent runtime is
called. Such a run would report a confident verdict about a harness that could not observe
the agent.

## 6. A dry run

Before calling a paid model, check that the grading grades. Give each probe a `mock_response` whose
grade you know. For `bad-input`, a reply that parses but never refuses:

```markdown
---
function_name: parse_duration
tiers: {basic: 0.6, refusal: 0.4}
test_cases:
  - {input: ["15m"], expected: 900, tier: basic, description: minutes}
  - {input: ["soon"], raises: ValueError, tier: refusal, description: refuses words}
  - {input: [""], raises: ValueError, tier: refusal, description: refuses nothing}
options:
  mock_response: |
    ```python
    def parse_duration(text):
        units = {"s": 1, "m": 60, "h": 3600}
        try:
            return int(text[:-1]) * units[text[-1]]
        except (ValueError, KeyError, IndexError):
            return 0
    ```
---

Write a Python function `parse_duration(text)` that turns "90s", "15m" or "1h" into a
number of seconds, and raises ValueError for anything else.
```

```bash
ix run lab/parse-duration --dry-run
```

A dry run runs the experiment as written, with every session on matrix's `mock` agent
runtime replying with its probe's `mock_response`, and the real runtime's config
(`runtime_options` and `plugins`) set aside (the report lists them under `Stood in`).
Nothing is edited and nothing is registered. The sensors read the canned replies as they would a model's. This one should score 0.6 on
`bad-input` (the basic tier passes, both refusals fail) and fail `tests_passed`; the
reading's detail names each failed case (`refuses words: returned 0 where ValueError was to
be raised`). A grade you did not expect is a test case or a grader that is wrong, found for
nothing. The report says `Dry run on mock replies: checks the harness, not the agent`, gives
neither a verdict nor an indication, and exits 0; `ix runs` lists it as `dry`. The `mock` runtime
reports no cost, so there is no `cost` reading. Its report has no probe screen: canned
replies read every probe at its floor or ceiling, which says nothing. It replies with text
only, so a dry run cannot show a skill firing or a tool call.

## 7. A pilot

On the real runtime, run once without a registration, with the genre's fewest repeats:

```bash
ix run lab/parse-duration --exploratory --repeats 5
```

`--repeats` pilots with that many repeats in place of the file's, and leaves the file as it
will be registered; the report lists the change under `Changed`. A pilot gives no verdict.
One too small for even every trial passing to show a pass rate's threshold says so under
`Best case`: that is its size, not the agent. Read the screen at the end of the report. A probe that every trial passed (`ceiling`) or
every trial failed (`floor`), or one whose readings spread over more than 0.35 of the range
for one subject (`noisy`), cannot tell one subject from another: fix it or drop it before
the real run. `ix plan` now states the smallest difference a run of this size can show (`Sensitivity:`); a difference smaller than
that will rarely be shown.

## 8. Register and run

```bash
ix register lab/parse-duration
ix run lab/parse-duration
```

The exit code is the verdict: 0 holds, 10 fails, 11 inconclusive; after the report, stderr
says the code, what it means and the next step. `Shows:` says what the evidence shows in
plain words, in the direction it points (`b's tests_passed is higher than a's by 30pp [8pp,
49pp]; higher is better, so b is better`), whatever the verdict. Report the verdict as it
is. `--repeats` is refused here: a verdict rests on the registered design. Adding repeats after an `inconclusive`, or moving a margin after any result, raises the
chance of a false `holds`; if you decide to extend the experiment, amend the registration
with a reason that names the earlier result (`ix register <path> --amend --reason "<why>"`).

The registration also froze the digest of each skill, plugin, fixture and agent definition
the subject names. Editing one afterwards is a deviation named by what changed (`skill
review changed after registration`), and a run of it gives no verdict: the verdict is about
the files as registered. The report's `Contents` and `Ran from` say which files and which
commit it was about.

Ctrl-C stops the trial in flight and exits 12, with a report that gives no verdict. The
record is kept, and `ix report` reads it.

## Reading the report

The report states the question, the decision and the verdict first, then each finding: the
difference, its 95% interval, the method, and the level it was decided at. Beside them:

- Estimates per subject, and the A/A check (`Calibration`).
- `Failures`, by whose: the subject's (a session that broke or was refused counts against
  it), the harness's (a runtime that was unavailable or rate-limited is missing data), or a
  sensor's.
- For `activation` and `tool-usage`: the table of expected against observed, with
  precision and recall.
- Flags a sensor raised, such as a judge that disagreed with itself.
- The probes that cannot discriminate.

Every trial is on disk under `lab/parse-duration/.ix/runs/<run>/`: `ix.v1.interaction.jsonl`
keeps each trial's prompt, reply, tool calls, each round's transcript, the files collected,
how the session stopped, and its turns, tokens, cost and duration; `ix.v1.reading.jsonl`
holds each reading with the sensor's detail. Each interaction names its rows of
`matrix.v1.session.jsonl` (`sessions`), which hold the model that answered each session.

## Whether a skill fires

A routing evaluation asks whether the right skill fires for a real ask and stays quiet for a
decoy. The sensor is `activation`; its truth is an `expectation` (`must_trigger`,
`should_not_trigger` or `acceptable`) and an `expected_skill`:

```markdown
---
id: review-ask
expectation: must_trigger
expected_skill: review
---

Review this diff for correctness before I merge it.
```

```yaml
name: review-routing
question: Does the review skill fire for a review request and stay quiet for a rename?
genre: evaluate
subjects:
  catalog:
    kind: agent-session
    runtime: claude-sdk
    max_turns: 1
    plugins: [plugins/review]
    runtime_options:
      setting_sources: []
      permission_mode: bypassPermissions
primary: {metric: activated_correctly}
threshold: {op: ">=", value: 0.8}
repeats: 20
```

`max_turns: 1` stops each session after the turn in which a skill would be called.
`setting_sources: []` keeps this machine's own plugins out, so what is measured is the
plugins named. Skill names are compared whole: `expected_skill: review` is fired by
`review` or `acme:review`, never by `code-review-lite`, and `expected_skill: acme:review`
only by `acme:review`. The report tabulates expected against the skill that fired, with
precision and recall.

### A skill of your own

To evaluate a skill you wrote, put its folder beside the experiment and give it to the
session with `skills:`:

```
lab/review-skill/
  experiment.yaml
  skills/review/SKILL.md
  probes/review-ask.md       # the probe above
```

```markdown
---
name: review
description: >-
  Reviews a diff for correctness before a merge. Use when the user asks to
  review a change, a diff or a pull request.
---

Read the diff, then list what is wrong with it, the most serious first.
```

```yaml
name: review-skill
question: Does the review skill fire for a review request?
genre: evaluate
subjects:
  agent:
    kind: agent-session
    runtime: claude-sdk
    skills: [skills/review]
    tools: [Read, Grep, Skill]
    max_turns: 3
    runtime_options:
      setting_sources: [project]
primary: {metric: activated_correctly}
threshold: {op: ">=", value: 0.8}
repeats: 20
```

`SKILL.md` needs both frontmatter keys: `name` (lowercase letters, digits and hyphens, the
folder's name, and the `expected_skill` the probes name) and `description` (what the skill
does and when to use it: the agent decides whether to fire it from this alone). Each folder
in `skills:` is copied into the session's `.claude/skills`, which a session reads as its
`project` settings. `setting_sources: [project]` reads those and nothing else of this
machine: the working directory is the trial's own. `ix validate` refuses `skills` with
`setting_sources` that leave `project` out. A skill fires through the `Skill` tool: when
you list `tools`, list `Skill`, or `ix validate` refuses the experiment, since no run of it
could see the skill fire.

`tool-usage` asks the same of a tool: option `tool` names it (a tool's name, or a program
the agent runs through `Bash`), and the truth gives `expected_command` (compared whole,
ignoring case: `dig` is not `digest`) and, when it matters, `expected_args`. The command alone scores 0.5, the command and its arguments 1.

## Checking the reply's text

For a rule a sentence can state (the reply starts with `BANANA`, names a file, never says
"I cannot"), the `text-match` sensor needs no code: `starts_with`, `ends_with`, `equals`,
`contains` and `excludes` (a text or a list) and `matches` (a regular expression), each in
a probe's truth, or as the sensor's option for every probe. `text_matched` is true when
every check given passes; `ignore_case: true` compares without regard to case, and `strip`
(true when absent) ignores whitespace at the ends.

```markdown
---
id: greet
starts_with: BANANA
---

Greet me, and start your reply with the word BANANA.
```

`ix validate` reports a probe left with no check, and a regular expression that does not
compile.

## Grading with your own code

When test cases cannot say what a good answer is, write graders. The `outcome` sensor's
option `graders` names a Python module beside the experiment whose `GRADERS` maps a probe id
(or the name a probe's truth gives as `grader`) to a function of the reply's text, or of the
text and the trial, returning a score from 0 to 1:

```python
def _names_the_tradeoff(text: str) -> float:
    return 1.0 if "latency" in text and "memory" in text else 0.0


GRADERS = {"cache-design": _names_the_tradeoff}
```

```yaml
sensors:
  - {kind: outcome, graders: graders.py}
primary: {metric: outcome_passed}
```

A probe with no grader is scored by the share of its truth's `expected_facts` that the reply
contains, ignoring case. `outcome_passed` is a score above `pass_above` (0.5 when absent).

## A judge

The `judge` sensor asks a model to score the reply against your `rubric`, from 0 to 1. A
model's reading is a sample, not a measurement, so its metrics are reported and **never
decide**: `ix validate` refuses one as the primary or a guardrail of a genre with a verdict.
Report it under `metrics:` beside a metric that decides. With `repeats: 3` the judge reads
each reply three times, and `judge_agreement` is how often it agreed with itself. The report
flags a trial whose readings split on pass or fail (`judge_inconsistent`) and a judge of the
subject's own model family (`judge_in_family`).

The judge names its runtime with one of two options, and there is no default:

- `model_runtime`: a matrix model runtime, one call per reading. `hardline` calls a model
  provider through hardline's registry; `mock` does not.
- `agent_runtime`: a matrix agent runtime, a one-turn session with no tools per reading, in
  a fresh temporary directory. `claude-sdk` calls a model provider; `mock` does not. A
  runtime whose config takes `setting_sources` reads none unless the judge's
  `runtime_options` name some, so a CLAUDE.md, a hook or your own settings where ix runs
  cannot shape its scores.

`ix validate` refuses a judge that names neither, or both. What its runtime reported it
cost, where it reports one, is kept in the reading's detail, beside the judge's runtime and
model.

```yaml
sensors:
  - {kind: judge, model_runtime: hardline, rubric: Does the reply name the bug and where it is?}
metrics: [judged_score]
```

## A model call instead of a session

To measure a model rather than an agent (the same prompts, one call each, no tools), make
the subject a `model-call`:

```yaml
subjects:
  fast:
    kind: model-call
    runtime: hardline
    model: fast
    system_prompt: Answer with code only.
    runtime_options: {temperature: 0}
```

Its `runtime` is a matrix model runtime (`hardline`, `mock`, or any installed), with no
default; `model` is a name the runtime resolves, which `ix plan` checks before anything
starts; `runtime_options` is the runtime's own config, as for a session. Each trial is one
call: no turns, no tools, no working directory. The `prompt` probe applies as it does to a
session, without `collect`. `function-test` (on the reply's code), `outcome`, `text-match`,
`judge` and `usage` read the reply as they read a session's; `usage` also keeps what the
runtime reported of the call: the model that answered, the request id, retries, a fallback,
the finish reason and the cache's tokens. `activation` and `tool-usage` read tool calls,
which a call never makes, so `ix validate` refuses them on a `model-call` subject
(`unobservable`), as it does `collect` and a `function-test` `file`.

## What a run can touch

An agent session's tools act on this machine as the user who runs ix, as `permission_mode`
allows: the working directory is fresh, but nothing confines a tool to it. `function-test`
and `outcome` run code (the agent's, and your graders) in a child process with a time limit
and no credentials in its environment; that is not a sandbox. Run an experiment whose
subjects you would run yourself. [SECURITY.md](../../SECURITY.md) has the detail.

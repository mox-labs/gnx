# ix

ix decides a question about a system by experiment. The system is an AI agent or a service.
For an agent: does the right skill fire for a real ask and stay quiet for a decoy, does an
extra review round make its code pass the tests more often, does it pass them often enough
to be relied on. For a service: is the new build no slower than the old one, does it answer
99% of requests at 80 a second, how does its memory grow with the size of a request.

You write the question, the systems and the measurement that decides it in one YAML file.
ix checks the file, shows what a run would do, freezes the design before anything
runs, runs it one trial at a time and reports a verdict: `holds`, `fails` or
`inconclusive`. The exit code is the verdict, so a run can gate a merge or a release.

```bash
# the ix command
uv tool install "git+https://github.com/mox-labs/gnx@ix-v0.5.0#subdirectory=components/capabilities/ix"
# the same, with the claude-sdk agent runtime for agent sessions
uv tool install "ix[claude] @ git+https://github.com/mox-labs/gnx@ix-v0.5.0#subdirectory=components/capabilities/ix"
# the library, in a project
uv add "ix @ git+https://github.com/mox-labs/gnx@ix-v0.5.0#subdirectory=components/capabilities/ix"
```

ix is installed from its repository at the release's tag. It is on no package index, and the
project named `ix` on PyPI is another one. ix runs on matrix, a package of the same
repository: installed from the git URL, uv clones the repository, finds the workspace it
belongs to and takes matrix from there. The extras are `claude` (the `claude-sdk` agent
runtime: Claude Agent SDK sessions), `models` (the `hardline` model runtime: one model call
through hardline) and `otel` (export of spans over OTLP). A service experiment needs `k6` on `PATH`.

## The experiment model

| Term | What it is |
|---|---|
| **experiment** | One YAML file: the question, the genre, the subjects, the probes and the metric that decides. |
| **genre** | The kind of decision the answer informs: `evaluate` (does it meet a threshold?), `compare` (is the variant better than the control, or no worse?), `regress` (may this change ship against its baseline?), `characterize` (how does it behave across a range?). The genre says what you must set and supplies the rest: repeats, the order of trials, the A/A check, the estimator. |
| **subject** | A system under test: a program ix starts (`process`), an endpoint that is already up (`http`), an agent (`agent-session`), or a model (`model-call`). In a comparison one subject is the control. |
| **probe** | What is done to a subject in a trial: HTTP load (`http-load`), or a prompt put to an agent (`prompt`). A probe may carry **truth**, the reference a sensor grades against; the agent never sees it. |
| **sensor** | What is measured. You name metrics, and each metric names its sensor: `ttfb` comes from `timing`, `tests_passed` from `function-test`. |
| **factor** | A condition varied on purpose. Every combination of the factors' levels is a **cell**. |
| **trial** | One subject, one probe, one cell, one repeat. The plan fixes the order of the trials before anything runs. |
| **reading** | One metric of one trial. |
| **verdict** | What the readings say about the decision: `holds`, `fails` or `inconclusive`. It rests on 95% intervals, never on a point estimate. |

## The sequence

```bash
ix validate <path>     # every problem at once, nothing run; exits 3 when the file is invalid
ix plan <path>         # the decision, the trials, the effects; runs nothing
ix register <path>     # freeze the design beside the file, before any run
ix run <path>          # carry it out; the exit code is the verdict
ix report <path>       # the latest run's report again, computed from its record
```

`validate` and `plan` run nothing. Read the plan before you register: its `Trials` and
`Effects` are what a run will do. Register before you run: the registration is what the
verdict is held to, so a margin cannot move once the numbers are in. `ix run` refuses an
experiment that is not registered (exit 15); `--exploratory` runs it anyway and gives no
verdict. A run exits 0 when the verdict holds, 10 when it fails and 11 when it is
inconclusive.

The other commands: `ix deviations <path>` (what changed since registration; exit 14 when
anything did), `ix runs [path]` (each run kept beside the experiment, or with no path every
experiment's runs under the current directory: when it started,
whether it was gated, exploratory or a dry run, the commit it ran from, its verdict or
indication, its trials),
`ix genres` and
`ix kinds` (what is installed), and `ix --skill` (the skill an agent reads; `-r NAME` prints
one of its references). A path is the experiment file, a directory holding
`experiment.yaml`, or `-` for an experiment on standard input. Every command takes `--json`.

## A service comparison

`examples/delay-server` holds a small HTTP server that answers every request after a fixed
delay, and two experiments over it. `slower.yaml` asks whether a copy that waits 60 ms is
faster than one that waits 40:

```yaml
name: delay-slower
question: Is a server that waits 50% longer per request faster than the one it would replace?
genre: compare
subjects:
  baseline:
    kind: process
    command: [python3, server.py, --port, "18473", --delay-ms, "40"]
    url: "http://127.0.0.1:18473"
    ready: "http://127.0.0.1:18473/healthz"
  candidate:
    kind: process
    command: [python3, server.py, --port, "18473", --delay-ms, "60"]
    url: "http://127.0.0.1:18473"
    ready: "http://127.0.0.1:18473/healthz"
probes:
  - id: get
    kind: http-load
    target: "{url}/work"
    load_model: closed
    concurrency: 4
    duration: 3s
    warmup: 1s
primary: {metric: latency, statistic: p50}
comparison: superiority
```

The first subject is the control. Repeats, the order and the A/A check come from the genre,
and the sensor from the metric: `timing` provides `latency`. `ix plan` shows each of them and
where it came from, the decision first:

```
delay-slower  compare (ix.v1.genre.compare)  plan 377ee022f5a4
Question:     Is a server that waits 50% longer per request faster than the one it would replace?
Decides:      candidate is better than baseline on latency p50
Comparison:   superiority
Verdict:      holds / fails / inconclusive
...
Trials:       2 subjects x 1 cell x 1 probe x 10 repeats = 20, + 10 A/A = 30
Duration:     about 2m (30 trials, probe time only; setup and teardown add to it)
Effects:      process: starts a process on this machine  (subjects.baseline, subjects.candidate, probes.0)
              network: sends requests over the network  (probes.0)
Registration: none
```

```bash
ix register examples/delay-server/slower.yaml
ix run examples/delay-server/slower.yaml       # exits 10: the verdict is fails
```

It runs where `k6` and `python3` are on `PATH`, in a few minutes. Each trial starts the
subject's command, waits for `ready` to answer, puts load on it from k6 for a second of
warm-up and three measured seconds, and stops it. The baseline also runs against itself in
every block, as a check that the machine held still. The candidate's median latency is
about 40% higher and the interval of the difference is nowhere near 0, so the verdict is
`fails`. `identical.yaml` compares two copies of one server: under `superiority` that is
`inconclusive`, since nothing was shown either way.

To gate a release, use `regress` with a `margin`, the largest regression you would still
ship: the candidate holds when it is at most the margin worse than the baseline. The margin
is the person's decision; the plan prints it on a line of its own and says it was set in
the experiment. `regress` answers "may the candidate ship?", not "is it slower?": for that,
`compare` with `superiority`, where `fails` says the candidate is worse.
`examples/proxy-streaming` is a larger comparison, across a grid of sizes and rates, with
guardrails on throughput and memory. Its programs are not in this repository, so it
validates and plans, and `ix plan` says `Runnable: no`.

## An agent evaluation

Does an agent write a correct `merge(intervals)` at least 80% of the time?

```yaml
name: merge-function
question: Does the agent write a correct merge(intervals) at least 80% of the time?
genre: evaluate
subjects:
  agent:
    kind: agent-session
    runtime: claude-sdk
    model: haiku
    tools: []
probes:
  - id: merge
    kind: prompt
    prompt: Write a Python function merge(intervals) that merges overlapping intervals.
    truth:
      function_name: merge
      test_cases:
        - {input: [[[1, 3], [2, 6], [8, 10]]], expected: [[1, 6], [8, 10]]}
        - {input: [[[1, 4], [4, 5]]], expected: [[1, 5]]}
        - {input: [[]], expected: []}
primary: {metric: tests_passed}
threshold: {op: ">=", value: 0.8}
repeats: 20
metrics: [tests_score, cost]
```

Each trial is a fresh agent session (on the `claude-sdk` agent runtime, which the subject
must name: `runtime` has no default, so a model provider is never called implicitly) in a new
temporary working directory. The probe puts the prompt to it, and `function-test` runs the
truth's test cases against the code in the reply, in a child process. `tests_passed` is a
pass rate, so it is held to the threshold by its interval: 20 trials that all pass show a
rate above 0.8, and 10 could not. `cost` is what each session's runtime reported it cost,
read as a fact like its tokens; nothing limits a run by it.

Work in this order:

1. `ix validate` and `ix plan`. Validate also refuses a session its sensors could not see
   into (`unobservable`): a skill with no `Skill` tool among its `tools`, a tool-reading
   sensor on a `model-call` subject, files to collect that no tool can write. `Runnable` says
   whether the runtime can start here (`claude-sdk` needs `ix[claude]`, the Claude Code CLI
   and its credentials).
2. **Dry run.** Give the probe a `mock_response`, a reply whose grade you know, and run
   `ix run <path> --dry-run`: the file as written, with the session on the `mock` runtime
   and the real runtime's config (`runtime_options`, `plugins`) set aside (the report
   lists them under `Stood in`). The sensors read the canned reply as they would a
   model's, so a test case or a grader that grades wrong shows here, for nothing. The
   report says `Dry run on mock replies: checks the harness, not the agent`, and gives no
   verdict and no indication.
3. **Pilot.** On the real runtime, `ix run <path> --exploratory --repeats N` (N the genre's
   fewest repeats; the file stays as it will be registered), and read the screen at the
   end of the report: a probe that every trial passed, or every trial failed, cannot tell
   one subject from another. A pilot too small for even every trial passing to show a
   pass rate's threshold says so under `Best case`.
4. `ix register`, then `ix run`.

`examples/harness-integrity` is a dry run as checked in: four probes with canned answers of
known worth, graded by `function-test` and by `outcome` with the experiment's own graders.
`ix run examples/harness-integrity --exploratory` calls no model. A check of the reply's text
(it starts with, contains, matches) needs no graders: the `text-match` sensor reads it from
the probe's truth (`starts_with: BANANA`). `examples/catalog-routing`
asks whether the right skill fires for a real ask and stays quiet for a decoy, with its
probes as files in `probes/`. [Evaluating an agent](docs/how-to/evaluate-an-agent.md) walks
through an agent evaluation end to end.

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

`compare` offers three comparisons: `superiority` asks for a difference (the variant is
better), `non-inferiority` for no loss beyond the margin, `equivalence` for no difference
beyond it either way. `regress` is `compare` narrowed to one variant, with the comparison
fixed to `non-inferiority`: the candidate is at most the margin worse than the baseline. So two builds that do not differ are inconclusive under `superiority`, and hold under
`regress` once there are enough trials that the interval stays above `-margin`.

`regress` extends `compare`. A genre extends at most one other, one level deep, and only by
narrowing: it may raise a minimum, lower a maximum, require more, allow fewer comparisons or
add report sections. It may not loosen its parent, change the analysis or add a parameter to
it, change the estimator or the verdict form, or add a role. A genre file that breaks this is
refused with a `genre_narrowing` problem for each place it does.

A genre's `design` (repeats, order, calibration, estimator) carries a note on its standing.
The built-in genres need at least 5 repeats to decide (3 for `characterize`) and default to
10 (5); their methods are checked by simulation at those counts (see
[the method](#the-verdict-and-the-method-behind-it)). A genre that decides cannot use `min`
or `max` as its estimator.

## Kinds

A subject, a probe, a sensor and the environment each name a **kind**. A kind is declared as
data, the way a genre is: its options (with types, and which are required), its effects, the
facets it offers and needs, and for a sensor the metrics it provides and whether a model
judges them. `ix kinds` lists them.

| family | kinds |
|---|---|
| subject | `process`, `http`, `agent-session`, `model-call` |
| probe | `http-load`, `prompt`, `inspect-task` |
| sensor | `timing`, `memory`, `activation`, `tool-usage`, `function-test`, `outcome`, `text-match`, `usage`, `judge` |
| environment | `none`, `process`, `compose` |

A declaration is not an adapter. ix can **run** these:

| family | kind | what the adapter does |
|---|---|---|
| subject | `process` | starts the command for each trial in its own process group, waits for `ready` to answer (or `url` to accept a connection), and kills the group after the trial |
| subject | `http` | starts nothing: an endpoint that is already up, with optional `headers` and a bearer token read from `auth: env:NAME` |
| subject | `agent-session` | a fresh session per trial on the matrix agent runtime its required `runtime` names (`claude-sdk` calls a model provider; `mock` calls no model; any agent runtime installed, by type URL), in a new temporary working directory seeded from `fixtures`, `skills` and a `setup` command, removed after the trial; `runtime_options` are the runtime's config, passed through as written; a `loop` asks again for a self-review or with the tests that failed |
| subject | `model-call` | one call per trial on the matrix model runtime its required `runtime` names (`hardline` calls a model provider; `mock` calls no model; any model runtime installed), with its `model` resolved before anything starts; no tools, turns or working directory, so `activation`, `tool-usage`, `collect` and a `function-test` `file` are refused on it (`unobservable`) |
| probe | `http-load` | runs k6 for `warmup` then `duration`, with `load_model: closed` (`concurrency` clients) or `open` (an arrival `rate`); only the measured part is read, and each request sent in it is waited for until it is answered or its `timeout` (k6's 60s) runs out |
| probe | `prompt` | puts the prompt to the session or the call and keeps the reply; for a session also every tool call, each round's transcript and the files named in `collect` |
| sensor | `timing` | `latency`, `ttfb`, `throughput`, and `error_rate` as a count (failed requests and dropped arrivals, of those sent and dropped), from what the load probe recorded |
| sensor | `memory` | `memory_peak`: the highest resident memory of the subject's process group, sampled with `ps` every `interval` while the probe runs |
| sensor | `activation` | `activated_correctly`: the expected skill fired (a `Skill` tool call naming it whole: `review` is fired by `review` or `plugin:review`, never by `code-review-lite`), or nothing fired for a decoy |
| sensor | `tool-usage` | `tool_used_correctly`, `tool_usage_score`: the agent used a tool with the expected command (the whole word, ignoring case: `dig` is not `digest`) and arguments, called by name or run through `Bash` |
| sensor | `function-test` | `tests_passed`, `tests_pass_share`, `tests_score`: the truth's test cases run against the code in the reply, or a file the agent wrote, in a child process |
| sensor | `outcome` | `outcome_score`, `outcome_passed`: the experiment's own grader functions, run in a child process; without one, the share of the truth's `expected_facts` the reply holds |
| sensor | `text-match` | `text_matched`: the reply starts or ends with, equals, contains, excludes or matches (a regular expression) what the probe's truth or the sensor's options say; no code of your own |
| sensor | `usage` | tokens, `cost`, `turns`, `tool_calls`, `duration`, `rounds`, and `hit_limit`, as the runtime reported them; what it did not report (a model call's cost or turns) is left out |
| sensor | `judge` | a model scores the reply against a rubric, by one call on the model runtime `model_runtime` names or a one-turn session on the agent runtime `agent_runtime` names; its metrics are reported and never decide |
| environment | `none`, `process` | nothing; or programs started before the trials and stopped when the run ends |

`inspect-task` and `compose` are declared only. An experiment that uses one is still valid:
`ix validate` warns (`kind_unimplemented`), `ix plan` prints `Runnable: no` with the kinds,
and `ix run` refuses it before anything starts. `ix kinds` marks each kind.

An adapter plugs in through matrix's registry, as every extension does. A package declares
its kind as data at the `ix.kind` entry point, and registers the adapter from its
`matrix.extensions` entry point at ix's point for the family (`ix.subject`, `ix.probe`,
`ix.sensor`, `ix.environment`) under the kind's type URL. ix resolves every adapter it runs
that way, its own included, so a new one needs no edit to ix. An adapter declares what it
needs built: `launcher` to start processes, `runtimes` to run an agent or make a model call
on matrix's runtimes (at its `agent-runtime` or `model-runtime` point), `sessions` to share
an agent session or a model call between a subject and a probe, by the handle the subject
offers as its `respondent` facet.

```python
def register(registry):
    registry.register("ix.subject", "acme.v1.subject.echo", EchoSubject, needs={"launcher"})
```

`ix validate` holds an experiment to the declarations:

- an option the kind does not take, a required one that is missing, or one of the wrong type
  is refused (`option_unknown`, `option_missing`, `option_invalid`); a key starting with
  `x-` is a note;
- a probe kind must be offered what it needs by every subject's kind (`kind_unfit`): a
  prompt cannot be put to a process;
- every metric named by `primary`, `guardrails` or `metrics:` must have exactly one sensor
  that provides it and can be fed here (`metric_unprovided`, `metric_ambiguous`); listing a
  sensor under `sensors:` settles an ambiguity or gives it options;
- a probe's truth key that no sensor in use reads is refused (`truth_unclaimed`), so a typo
  does not pass silently;
- a `{name}` placeholder must be bound to what a trial fills it with
  (`unbound_placeholder`). A subject's options are filled with the cell's levels, so a
  factor binds them: `characterize` over `factors: {rounds: [1, 2, 3]}` with `loop:
  {strategy: self-review, rounds: "{rounds}"}` runs each level. A value that is one
  placeholder takes the level with its type (`3`, not `"3"`), and each cell's options are
  reviewed as its trials will have them. A probe's options, inline or from a file, are
  filled with the levels, the subject's options and its facets: a factor, an option every
  subject sets, or a facet every subject's kind offers (`{pid}`). A loop's `prompt` fills
  `{prompt}`, `{response}`, `{until}` and `{failures}` itself. `{{name}}` is the text
  `{name}`;
- **a model-judged metric never decides.** Whether a metric is judged is the sensor's
  declaration, not the author's. A judged metric is refused as `primary` and as a guardrail
  in every genre with a verdict (`judged_gate`); it can be reported under `metrics:`.

A metric decision has two reductions: `statistic` reduces one trial's reading to a number
(the p99 of a latency histogram) and `estimator` combines the trials (their mean; the rate
of a boolean or a count). Both default. A verdict rests on a mean, a median (6 blocks or
more) or a rate; `min` and `max` are measured, never decided on (`estimator_illegal`). A
rate's margin is in points (`10pp`) or a share, never percent (`unit_mismatch`), and below
the whole range (`1`, `100pp`); a rate's threshold is a share between 0 and 1, never 0 or 1
themselves (`invalid_value`).

## Extending

Genres and kinds are data files. Put them in a package and name the package at the entry
point ix owns; ix registers its own the same way. A lab can also keep `genres/` and `kinds/`
directories beside its experiment; `ix genres --lab <dir>` and `ix kinds --lab <dir>` list
them.

```toml
[project.entry-points."ix.genre"]
acme = "acme_ix.genres"      # a package of *.yaml files; read, never called

[project.entry-points."ix.kind"]
acme = "acme_ix.kinds"
```

A name belongs to the package that owns its namespace (`ix` owns `ix.*`): another file that
declares it is refused alone. Two other files that declare one name are both refused; a
file that cannot be read does not affect the others. `ix genres` and `ix kinds` list what
was refused and why.

## Registration

`ix register` writes `<file-stem>.registration.json` beside the experiment. It stores the
design as resolved, what the file sets and what the genre supplies (comparison, margin,
threshold, guardrails, control, repeats, seed, order, calibration, estimators, the analysis
and its parameters, the sensors), with the digests of the probe set, the experiment and the
plan, and the digest of each file and folder the subjects and sensors name: the skills,
plugins (each folder a `*` matches), fixtures and agent `definition`, a sensor's `graders`
or `rubric_file`, and any option a kind declares a `path` or `paths`. A `command` (a
`process` subject's, relative to its `cwd`; an agent session's `setup`) adds each item that
names a file: `[python3, server.py]` digests `server.py`, and `["./proxy", --config,
env/a.yaml]` the program and its config. A program found on PATH is not the experiment's.

- What a skill, plugin or definition holds is what the verdict is about. An edit to one
  after registration is a deviation named by what changed (`skill review changed after
  registration`), and a run of it gives no gate result, as an edited probe file does. A
  file's digest is the sha256 of its bytes; a folder's is over its files' relative paths and
  their digests, so a modification time, a permission or an empty folder is not a change,
  and neither is anything under `.git` or `__pycache__`, or a `.DS_Store`.

- `ix deviations <path>` lists each frozen value that changed, with what was registered and
  what it is now, and exits 14 when it finds any. A genre that changes after registration
  shows up here, because its values were frozen and not just its name.
- A comment, key order or a default written out is not a deviation. A reworded `question` or
  `rationale` changes the experiment's digest and not the plan's.
- `ix register` on a changed experiment is refused (exit 7, `conflict`) and the record is
  kept.
- `ix register <path> --amend --reason "<why>"` replaces the record on purpose. The
  superseded record, the reason and the deviations it accepted stay inside the new one.
- A registration written by a newer ix still loads: fields this ix does not know are
  ignored, and a value an older record never stored is not compared. A registration that
  holds no digests of the subject's files is not compared on them; delete it and register
  again to freeze them.

## Running

A run brings the environment up once, then carries out the trials one at a time in the
order the plan fixed (`plan` and `run` compute the same list, and the plan's digest covers
it). For each trial: start the subject, let the sensors that must watch begin, apply the
probe, stop the watchers, stop the subject. The sensors then read every trial that was
carried out. Progress goes to stderr (a bar on a terminal, one line per trial otherwise,
nothing under `--json`); the report goes to stdout.

- **Register first.** `ix run` refuses an unregistered experiment (exit 15).
  `--exploratory` runs it anyway; that run gives no verdict, its report shows what it found
  as an *indication*, and it exits 0.
- **The exit code is the verdict**: 0 holds, 10 fails, 11 inconclusive. After the report,
  stderr says the code, what it means and the next step; `exit` in `--json`. A run with no
  verdict exits 0 only when none was asked for (`--exploratory`, `--dry-run`).
- **The report says what the evidence shows**, beside the verdict and without changing it:
  `Shows: build30's latency p50 is higher than build20's by 29.6% [26.2%, 33.1%]; lower is
  better, so build30 is worse` (`shows`).
- **A pilot.** `--exploratory --repeats N` (or `--dry-run --repeats N`) runs N repeats in
  place of the file's, never below the genre's minimum, and the report lists the change
  (`overridden`); a run that gives a verdict refuses `--repeats` (exit 2). A run too small
  for even every trial passing to show a pass rate's threshold says so (`best_case`).
- **A runtime's warning is said once per run** (a `claude-sdk` subject with a permissive
  `permission_mode`): in the plan's and the report's `warnings`, and once on stderr.
- **A changed experiment runs ungated.** How it differs from its registration is recorded
  with the run and printed in the report; what it found is an *indication*, not a verdict,
  and it exits 14 (`deviations`), never 0, until the registration is amended with a
  reason. A gate cannot pass on a design moved after it was registered.
- **A dry run gives no verdict.** `ix run <path> --dry-run` runs the experiment as written
  with every agent session and model call on matrix's `mock` runtime of its port; a subject
  with no stand-in (a `process`) is refused. A run in which a stand-in (a runtime registered
  as one, as the mocks are) answered any trial measured the harness,
  not the subject: the report says `Dry run on mock replies: checks the harness, not the
  agent`, with neither a verdict nor an indication, nor a probe screen, and the run is not
  `gated`. A registered run in which a stand-in answered (`runtime: mock` written in) exits
  16 (`simulated`), never 0: a gate on the exit code cannot pass on it.
- **Stopping.** Ctrl-C (or SIGTERM) stops the trial in flight, kills the process groups the
  run started, and exits 12 with the partial report. A run the harness could not finish (an
  environment that did not come up, or went down between trials) exits 8. `ix report` of a
  run still being written exits 13.
- **Refused before it starts**: a subject command that is not there, an `auth` variable
  that is not set, a file an agent subject names that is not there, an agent runtime that
  cannot start or cannot play the agent as written, and a model runtime that cannot start
  or cannot resolve the subject's model. `ix plan` lists them under
  `Runnable: no` beforehand; they depend on the host, so they do not stop `ix register`.
- **Failures have an owner.** A trial the subject failed (it exited, it was not up in time,
  it answered no request successfully within the measured time, it returned a different body; an agent session that
  broke, was refused or ran past its probe's `timeout`) counts against the subject. A trial the harness failed (k6 missing
  or broken, no request sent, a timeout, a stop request, an environment that went down, a
  model runtime that was unavailable, rate-limited or refused its credentials) is missing
  data. Every request sent in the measured time is waited for, past it if need be, until
  it is answered or the probe's `timeout` (k6's 60s when absent) runs out. Latency and time
  to first byte count the requests the subject answered successfully, and throughput only
  those answered within the measured time. `error_rate` counts every request that failed,
  timed out or was never answered, of those sent. Under an open model, k6 starts every
  client before the load, so an arrival it drops found every client waiting on the
  subject: the subject was too slow for the rate, so each drop counts in `error_rate` as
  an error, and the trial is measured with its `dropped` count. A sensor that
  could not read is a reading with a failure. None of these stops a run, but a lost
  environment; the report counts each.

### The record

A run is one matrix run, and matrix's record is the only store. The composition root
builds it at matrix's `store` point: `jsonl`, a directory of tables per run (a program
that builds its own `Root` may name another store whose config takes `root`):

```
<experiment directory>/.ix/runs/<experiment>-<UTC time>-<4 hex>/
  run.json                  status, times, how each component ended
  ix.v1.run.jsonl           the manifest: experiment, design, registration, deviations,
                            the subject's digests, the commit, the versions
  ix.v1.trial.jsonl         the schedule: one row per planned trial
  ix.v1.bindings.jsonl      what the environment provided
  ix.v1.interaction.jsonl   what happened in each trial, with any failure and its owner
  ix.v1.reading.jsonl       one row per metric per trial
  matrix.v1.session.jsonl   one row per agent session or model call, failed ones included:
                            the runtime, the model that answered, usage, ids, attempts
  matrix.v1.ledger.jsonl    which component recorded each row, and when
  events.jsonl              the run's events
  writer.lock               locked by the process writing the run while it lives
```

The manifest records what the verdict is about and where the run ran from: the digest of
each file and folder the subjects and sensors name (`contents`, paths and digests, never what
the files hold); the git commit the experiment sits at and whether a tracked file differed
from it (`dirty`), or `not a repository`; the installed versions of ix, matrix and hardline;
and every agent and model runtime installed, by type URL, with the distribution that
registered it and its version, and each distribution it drives with that one's version (the
Claude Agent SDK, for `claude-sdk`); and where each setting it read came from
(`provenance.settings`: `ix.runs`, `matrix.observers` and `matrix.models`, each with the file
that set it or `default`, never the value). `ix report` prints the digests (`Contents`), the commit
(`Ran from`) and the versions, `ix runs` the commit, and `--json` carries all of it.

An agent trial's interaction keeps the prompt, the reply, every tool call, each round's
transcript, the files collected, how the session stopped, and its turns, tokens, cost and
duration. The run's events (`ix.trial.start`, `ix.trial.end`, ...) also reach the observers
matrix's config names for ix: `observers:` under `matrix:` in any tier
`matrix config --tool ix --sources` lists.

**From a verdict to the model that answered.** Each interaction names the rows of
`matrix.v1.session` behind its trial (`sessions`): every agent session (each round of a loop)
or model call the subject made, failed ones included. Each reading names the rows its sensor
made (`sessions`: a judge's calls), and joins its interaction on `seq`. A session row holds
the runtime, the model that answered and its family, the usage, the ids (the runtime's, and
the OpenTelemetry trace and span the session ran in), the attempts, and whether it failed and
why. A row's position in its file, from 0, is the number the others name. In DuckDB, with
`RUN` the run's directory, each judged reading beside the model that answered its trial:

<!-- reading-to-session -->
```sql
WITH sessions AS (
    SELECT ordinality - 1 AS session, runtime, model, family, status
    FROM read_json_auto('RUN/matrix.v1.session.jsonl') WITH ORDINALITY
),
answered AS (
    SELECT seq, unnest(sessions) AS session
    FROM read_json_auto('RUN/ix.v1.interaction.jsonl')
)
SELECT reading.seq, reading.metric, reading.value, runtime, model, family, status
FROM read_json_auto('RUN/ix.v1.reading.jsonl') AS reading
JOIN answered USING (seq)
JOIN sessions USING (session)
WHERE reading.metric = 'judged_score'
ORDER BY reading.seq;
```

Each file is one JSON document per line, readable with `jq` or DuckDB. An interaction is on
disk when its trial finishes, so a run that is killed outright keeps every trial it
finished. `ix report <run directory>` reads a run in any state. When a run ended before its
sensors read the interactions (it was stopped or killed), the report reads them from the
stored interactions and says `readings: derived`; nothing is written back. A report never
calls a model: when a sensor the run used does (a `judge`), it reads none and says so
(`unread`), and the readings come from running the experiment again. Keep `.ix/` out
of version control.

The work is three components that matrix wires by the kinds they exchange: `environment`
provides `ix.v1.bindings`; `trials` requires those and the schedule and provides
`ix.v1.interaction`; `sense` provides `ix.v1.reading`. Analysis and the report are pure
functions outside the run.

### The verdict, and the method behind it

One finding per variant per cell. The verdict holds only if every finding holds, and fails
if any fails. Every choice below, with its source and the simulation that checks it, is in
[the design record](docs/design/2026-10-19-record.md); the report prints the method with
every verdict.

1. Each trial's reading is reduced to a number by the `statistic`; the trials combine by
   the `estimator`. A comparison is made *in blocks*: one repeat of one probe in one cell,
   where every subject ran side by side in an order drawn from the seed.
2. The decision is made on a 95% interval, by the measurement:
   - a `mean` gets Student's t interval: of the per-block differences for a comparison in
     points or the metric's unit, and Fieller's interval for the ratio of the two means for
     a comparison in percent;
   - a `median` gets the order-statistic interval (the sign test inverted): of the
     per-block differences in a comparison. It needs 6 blocks;
   - a `boolean` is a rate over trials, with a Wilson score interval; a `count` (failed
     requests out of requests sent) is a rate pooled over every unit, with the Wilson
     interval at Korn and Graubard's effective sample size, the trial as the cluster. Zero
     failures in 10,000 requests bound the rate near 3.8/10,000, never at 0. A difference of
     two rates is Newcombe's interval;
   - `min` and `max` have no interval ix can stand behind: they are reported, never decided
     on.
3. With the gain being the variant minus the control, turned so that positive is better:
   `superiority` holds when the interval is above 0 and fails when it is below;
   `non-inferiority` holds when it is above `-margin` and fails when it is below (a
   one-sided test at 2.5%, as FDA's guidance on non-inferiority has it); `equivalence`
   holds inside `(-margin, margin)` and fails wholly outside (two one-sided tests at 2.5%
   each). A threshold holds or fails when the subject's own interval is wholly on one side.
   Anything else is inconclusive.
4. **More than one decision.** The verdict holds only if every decision (each variant, each
   cell, the primary and each guardrail) holds, so each is read at the full 0.05 and no
   correction applies: requiring all of them adds no chance of a false `holds` (an
   intersection-union test). The verdict fails if any decision fails, so the `fails` side
   is corrected by Holm's procedure across the whole family, one that missing data left
   undecided included. A decision's `decided_at` is the smallest level at which it would be
   made (its test's p-value), and `allowed` the level it was held to. The intervals printed
   are unadjusted.
5. **The A/A check.** Under A/A calibration the control runs twice in every block, and its
   second sample is compared with its first by the same method, at 1% shared out over every
   such check. A control that reads the same in both samples of every block held still,
   and passes. A control that differs from itself makes the run inconclusive: the
   environment did not hold still enough to compare in. So does a check that cannot be
   made, since nothing then shows that it did. The report says which. The check never
   changes an interval.
6. A subject that failed a trial of a cell fails that cell. In a comparison, a control that
   failed one makes the cell inconclusive instead, whatever the variant did. A model-judged
   metric never decides. ix's analyses take no parameters: a genre that gives one is warned
   about (`analysis_unimplemented`) and refused by `ix run`.

What the simulations show, on normal data, at the genres' minimum of 5 blocks and their
default of 10 (10,000 runs each): the 95% interval of a difference covers 94.7% of the
time at both; a variant worse by exactly the margin holds 2.5% of the time (nominal 2.5%);
a mean exactly on a threshold holds 2.4% and 2.8%; a stable environment fails the A/A check
1.1% and 1.0% of the time (stated 1%). An error rate exactly on its threshold, over 1,000 or
10,000 requests a trial whose failures cluster within trials, holds 1.9% to 3.9% of the time
(4,000 runs each).

What follows for two subjects that do not differ: under `superiority` the verdict is
**inconclusive** (nothing was shown either way); under `non-inferiority` it **holds** once
the interval is narrower than the margin (a loss beyond the margin was ruled out), and that
is the comparison `regress` fixes.

`Sensitivity:` in `ix plan` is what a run can detect. Once the experiment has run (an
exploratory run counts; a dry run on the `mock` runtime does not), it is the true difference
a run of this size shows 4 times in 5, from the spread of the per-block differences in the
latest run: a margin below it will rarely hold. For a pass rate held to a threshold, the plan also says when even every trial
passing cannot show it (30 trials can never show a rate above 0.9; 35 can).

Known limits: fewer than 3 usable blocks on a side decide nothing (6 for a median); every
trial reading the same number (on both sides, in a comparison) decides nothing; a one-sample t interval under-covers on
strongly skewed per-trial numbers (a median is the remedy); a relative difference has no
bounded interval when the control's mean is not distinguishable from zero; a difference of
two rates does not use the pairing, so it is wider than it might be; a rate whose rare
events come in bursts concentrated in a few trials is under-covered, and on its threshold
holds up to 3.9% of the time where 2.5% is stated; Wilson's one-sided
error oscillates with the number of trials, up to 3.9% where 2.5% is stated; the A/A check
sees a shift, not every kind of instability; the memory peak is sampled, so a spike
between two samples is missed.

### Not built

Trials run one at a time; there is no concurrency. A subject left running by a run that was
killed outright is not found and stopped later: in the next run, each trial of a subject
whose address already answers is recorded as a harness failure (`address_in_use`) instead
of being measured. The sensors cannot be run again over a stored run and written back.

## Settings and telemetry

ix's settings are the `ix:` section of matrix's config tiers, later tiers winning:
`~/.matrix/config.yaml`, `~/.ix/config.yaml`, `./matrix.yaml`, `./ix.yaml`, then the files
`$MATRIX_CONFIG` and `$IX_CONFIG` name. They hold what varies by where ix runs, not by
experiment:

```yaml
ix:
  runs: ~/ix-runs       # where every run is kept; absent: .ix/runs beside each experiment
```

An unknown key is refused, by its path and file. The same read gives the `matrix:` section:
the observers of a run, and the models a `hardline` model runtime is handed.

ix makes spans through the OpenTelemetry API, under the instrumentation scope `ix`:
`ix.plan`, `ix.run`, `ix.environment`, `ix.trial` and `ix.sensor`, with each reading as a
`gen_ai.evaluation.result` event and a failure's reason as `error.type`. The agent sessions inside a trial are matrix's spans. The
`ix` command installs the SDK from the standard `OTEL_*` variables when the `otel` extra is
installed; set without it, every `--json` document carries a warning. A program that
imports ix configures OpenTelemetry itself, or calls `ix.configure_telemetry()`.

## Library

The commands are an adapter over the library; `import ix` gives the same thing. Each
function takes a path or an `ix.Experiment` built in memory.

```python
import ix

result = ix.validate("examples/proxy-streaming")  # ix.Validation: valid, complete, problems
plan = ix.plan("examples/proxy-streaming")  # ix.Plan; raises ix.InvalidExperimentError
design = ix.resolve("examples/proxy-streaming")  # ix.Design: every value, with its source
genres, kinds = ix.genres(), ix.kinds()  # ix.Genres, ix.Kinds

slower = "examples/delay-server/slower.yaml"
ix.register(slower)  # ix.Registered; ix.RegistrationConflictError if the file changed since
found = ix.deviations(slower)  # ix.Deviations; ix.NotFoundError if it is not registered
report = ix.run(slower)  # ix.Report; blocks until the run ends
again = ix.report(report.location)  # the same report, from the record
listed = ix.runs(slower)  # ix.Runs

experiment = ix.load("examples/proxy-streaming")  # ix.Experiment
ix.plan(experiment.model_copy(update={"repeats": 9})).trials.total  # 243
```

`ix.__all__` is the declared API: `__version__`, `load`, `validate`, `resolve`, `plan`,
`register`, `deviations`, `run`, `report`, `runs`, `genres`, `kinds`, `skill`,
`configure_telemetry`, `error_document`; the documents they return (`Validation`,
`Problem`, `Design`, `Plan`, `Registered`, `Deviations`, `Report`, `Runs`, `Genres`,
`Kinds`); the three records an author or an extension writes (`Experiment`, `Genre`,
`Kind`); the errors (`IxError`, `ConfigError`, `NotFoundError`, `InvalidExperimentError`,
`RegistrationConflictError`, `UnregisteredError`, `UsageError`); and the tables
`PROBLEM_KINDS`, `ERROR_KINDS`, `EXIT_CODES`, `EXIT_MEANINGS` and `EXIT_NEXT`. Every part of
those records (`SubjectConfig`, `Margin`, `Decision`, `Interval`, ...) and every port an
extension implements (`Subject`, `Probe`, `Sensor`, `Environment`) is exported from
`ix.domain`, the one public path to them; the modules inside it are not public.

`ix.run` returns a report for every run that started, however it ended: `Report.status` is
`completed`, `interrupted` or `failed`, and `Report.exit_code` is what a command should exit
with. It raises only for what stops a run before it starts. It runs its own event loop, so
call it from synchronous code; `on_event=` receives progress events, which are telemetry.

Importing `ix` loads no host runtime: matrix is imported when a function is first called.
Only ix's composition root and its `matrix_*` adapters name matrix.

## Output

`--json` prints one document with a `schema` field: `ix.v1.validation`, `ix.v1.plan`,
`ix.v1.registered`, `ix.v1.deviations`, `ix.v1.report`, `ix.v1.runs`, `ix.v1.genres`,
`ix.v1.kinds`.

A command that fails prints one envelope on stderr, as one JSON line under `--json`:

```json
{"schema": "ix.v1.error", "error": {"kind": "config", "message": "...", "fix": "...", "complete": true, "details": {}, "problems": [], "deviations": []}}
```

A problem is `{path, kind, message, fix, allowed, got, source}`. `allowed` lists the legal
values where the set is closed, and `got` is what was written, so a caller need not parse
the message. `"complete": false`, in a validation or an error, means checking stopped at
the file's shape: fix those problems and check again. Warnings travel inside the document
(`warnings` in `ix.v1.validation` and `ix.v1.plan`). `ix validate` on an invalid file,
`ix deviations` that finds any, and `ix run` or `ix report` of a run that fails, is
inconclusive, was not held to a registration it moved away from, was answered by a stand-in
where a gate was asked for, was stopped, could not finish or is still running are not
failures: each prints its own document on stdout and
exits non-zero. The envelope and the shared exit codes are every gnx capability's; ix's own
kinds take 10 and up. `error.kind` is one of `unknown`, `usage`, `config`, `conflict`,
`not_found`, `unregistered` or `interrupted`. `details` holds the wrong value (`value`) and
where it is (`path`) when there is one, and a matrix error's own details when ix carries one
over.

| Exit | Kind | Meaning |
|------|------|---------|
| 0 | `ok` | done; for validate, the experiment is valid; for run and report, the verdict holds, or there is no verdict to give and none was asked for (exploratory or dry) |
| 1 | `unknown` | a failure ix did not classify |
| 2 | `usage` | a bad flag or argument |
| 3 | `config` | the experiment is invalid, or it cannot be carried out here as written (a kind with no adapter, a subject that cannot start, an unsupported analysis): read the message and the fix |
| 4 | `not_found` | no experiment file, no registration, or no run |
| 7 | `conflict` | register met an experiment that changed since its registration: restore it, or amend with a reason |
| 8 | `run_failed` | the harness could not finish the run; the record is kept |
| 10 | `fails` | the verdict is fails |
| 11 | `inconclusive` | the verdict is inconclusive |
| 12 | `run_interrupted` | the run stopped before it finished; the record is kept and it gives no verdict |
| 13 | `running` | the run has not finished yet; report it again when it has |
| 14 | `deviations` | the experiment differs from its registration (ix deviations); a run of it gave no gate result |
| 15 | `unregistered` | ix run needs a registration, or --exploratory |
| 16 | `simulated` | a stand-in (a mock runtime) answered a run held to its registration: it measured the harness, not the subject, and gives no gate result |
| 130 | `interrupted` | the command itself was interrupted (Ctrl-C) before it finished: run it again |

`run_interrupted` (12) is a run that was stopped and reported: `ix run` stopped by Ctrl-C
or SIGTERM exits 12 with its report. `interrupted` (130) is any other command
cut short by Ctrl-C, which prints the error envelope instead.

## Documentation

| Document | What it covers |
|----------|----------------|
| [Evaluating an agent](docs/how-to/evaluate-an-agent.md) | An agent evaluation end to end: the subject, probes in files, sensors, a dry run, a pilot, the judge, and reading the report |
| `ix --skill` | The skill an agent reads, with frontmatter so it is a `SKILL.md` as printed: what ix is for and when not to use it, install and prerequisites, a quick start that runs, the commands in order, the errors and exit codes with what to do next. Generated from what is installed |
| `ix --skill -r NAME` | One reference the skill lists: `experiment` (every key and value form), `genres`, `kinds`, `running` (registration, running, the verdict), `agents` (an agent evaluation end to end), `problems` |
| [Design record, 2026-10-19](docs/design/2026-10-19-record.md) | Why the verdict's statistics are what they are: each method with its source, and the simulations before and after |
| [CHANGELOG.md](CHANGELOG.md) | What each release changed |
| [SECURITY.md](SECURITY.md) | What ix reads, writes, starts, runs and sends |

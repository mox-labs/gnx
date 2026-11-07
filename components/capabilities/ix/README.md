# ix

ix decides a question about an AI agent or a service by experiment. You write the question,
the systems and the measurement that decides it in one YAML file; ix shows the plan, freezes
the design before anything runs, and reports a verdict with its interval: `holds`, `fails`
or `inconclusive`. The exit code is the verdict, so a run can gate a merge.

Questions it answers: does the right skill fire for a real ask and stay quiet for a decoy;
does an extra review round make an agent's code pass its tests more often; is the new build
no slower than the old one; do my tests fail when the code is wrong.

## Install

```bash
# the ix command
uv tool install "git+https://github.com/mox-labs/gnx@ix-v0.9.0#subdirectory=components/capabilities/ix"
# the library, in a project
uv add "ix @ git+https://github.com/mox-labs/gnx@ix-v0.9.0#subdirectory=components/capabilities/ix"
```

ix is installed from this repository at its release's tag. It is on no package index, and
the project named `ix` on PyPI is another one. uv takes matrix, which ix runs on, from the
same repository, and every runtime comes with it: `claude-sdk` and `pydantic-ai` agent
sessions, hardline's model calls through the `matrix-hardline` bridge, and the
OpenTelemetry SDK. There are no extras.

From a clone, run it in the capabilities workspace, with nothing installed globally:

```bash
git clone https://github.com/mox-labs/gnx && cd gnx
uv --project components/capabilities run ix --version     # ix, version 0.9.0
```

The first `uv --project` builds the workspace's environment in
`components/capabilities/.venv`, inside the clone.

What runs where:

| You want | Needs |
|---|---|
| The first run below, a dry run of your own experiment | nothing: no model is called |
| Testing a test suite (mutation) | the suite's pytest and pytest-cov, and [ix-cosmic-ray](../ix-cosmic-ray/README.md) |
| Comparing two builds of a service | `k6` on `PATH` |
| Evaluating a real agent | a runtime that calls a model: `claude-sdk` needs the Claude Code CLI and its credentials, and each session costs what the provider charges |

## A first run, offline

`examples/harness-integrity` checks ix's own grading: four probes, each with a canned reply
of known worth, graded by running test cases against the code in the reply. matrix's `mock`
runtime gives the replies, so no model answers. From the clone, prefix each command with
`uv --project components/capabilities run` and give the path from the repository's root
(`components/capabilities/ix/examples/harness-integrity`).

```bash
ix validate examples/harness-integrity
ix plan examples/harness-integrity
ix run examples/harness-integrity --exploratory
```

`plan` runs nothing. It states the decision, the trials, what the run needs here, and how
many model calls it may start:

```
harness-integrity  evaluate (ix.v1.genre.evaluate)  plan 6dd748c91fb1
Question:     Do the function-test and outcome sensors grade answers of known worth as they should?
Decides:      canned has tests_passed >= 0.5
Verdict:      holds / fails / inconclusive
...
Trials:       1 subject x 1 cell x 4 probes x 5 repeats = 20
...
Requires:     yes  the files the session names are there (fixtures, skills, plugins, definition)  (subjects.canned)
              yes  agent runtime 'mock' builds and can play the session  (subjects.canned)
Model calls:  none: each entry that calls one is on a stand-in
```

Below it, a `Design` table lists every value the run will use and where it came from: the
experiment, the genre, the kind, or ix. `--exploratory` runs an experiment that is not
registered and gives no verdict. The run takes a second, and its record lands beside the
experiment, in `examples/harness-integrity/.ix/runs/<run id>/` (every run of an experiment
goes to its `.ix/runs/`):

```
Verdict:      none: Dry run on mock replies: checks the harness, not the agent
Run:          completed; 20 of 20 carried out

Findings on mock replies (the harness, not the agent: hold each estimate to the grade its canned reply should get)
  canned: fails
    primary: tests_passed value (rate) >= 0.5: fails
      estimate 0.25 [0.1119, 0.4687] (95%, unadjusted), wilson, n 20, decided at 0.0253 (allowed 0.05)
…
│ canned  │ subject │ -    │ tests_passed value (rate)    │ 0.25         │ [0.1119, 0.4687] │ 20 │
│ canned  │ subject │ -    │ tests_score value (mean)     │ 0.375 ratio  │ [0.1759, 0.5741] │ 20 │
…
Exit 0 (ok): done; ...
Next: this checked the harness on canned replies: if each estimate is the grade its reply should get, ...
```

**`fails` is the right answer here.** One reply in four passes every test, so `tests_passed`
should read 0.25, under the threshold of 0.5; the four replies score 1, 0.5, 0 and 0, so
`tests_score` should read 0.375. Any other reading would be a sensor that grades wrong. That
is what a dry run is for: checking the grading before a model is paid for.

## Reading a result

| Line | What it says |
|---|---|
| `Decides` | the claim, in the experiment's words |
| `Verdict` | `holds`, `fails` or `inconclusive`; or `none` and why (a dry run, an exploratory run, a genre that gives estimates) |
| `Shows` | what the evidence shows in plain words, in the direction it points |
| `Findings` | each decision: the estimate or difference, its 95% interval, the method, `n`, and the level it was decided at |
| `Estimates`, `Calibration` | every metric per subject; the control compared with itself, as a check that the machine held still |
| `Failures` | trials that failed, by whose: the subject's counts against it, the harness's is missing data |
| `Warrant` | what the verdict rests on, and what weakens it |
| `Record` | the run's directory: every trial, reading and model session, one JSON line each |
| `Exit`, `Next` | the exit code, what it means, and what to do next for this run |

A gated run exits 0 when the verdict holds, 10 when it fails and 11 when it is inconclusive.
An unregistered run is refused (15) unless it is exploratory; a registered run that a mock
answered exits 16, never 0. [Output and exit codes](docs/reference/output.md) has every code.

## Your own experiment

An agent evaluation: does an agent write a correct `merge(intervals)` at least 80% of the
time? `mock_response` is the reply a dry run gives in place of the model's; here, a correct
one.

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
    mock_response: |
      def merge(intervals):
          merged = []
          for start, end in sorted(intervals):
              if merged and start <= merged[-1][1]:
                  merged[-1][1] = max(merged[-1][1], end)
              else:
                  merged.append([start, end])
          return merged
primary: {metric: tests_passed}
threshold: {op: ">=", value: 0.8}
repeats: 20
metrics: [tests_score]
```

Save it as `lab/merge/experiment.yaml` and work in this order:

```bash
ix validate lab/merge          # every problem at once, each with its fix
ix plan lab/merge              # Model calls: at most 20; runs nothing
ix run lab/merge --dry-run     # on the canned reply: tests_passed 1 [0.8389, 1]; no verdict
ix run lab/merge --exploratory --repeats 5   # a pilot on claude-sdk: calls the model
ix register lab/merge          # freeze the design before the real run
ix run lab/merge               # the exit code is the verdict
```

`runtime` has no default, so a model is never called because a line was left out. The dry
run puts the session on `mock` and calls nothing; every step after it calls the model.
[Evaluating an agent](docs/how-to/evaluate-an-agent.md) takes this further: probes as files,
whether a skill fires, tool use, your own graders and a judge.

## Where next

| Page | For |
|---|---|
| [What ix is](docs/explanation/overview.md) | the mental model, and what it gives an agent |
| [Evaluating an agent](docs/how-to/evaluate-an-agent.md) | an agent evaluation end to end |
| [Comparing two builds of a service](docs/how-to/compare-two-services.md) | latency under load, and gating a release with `regress` |
| [Testing a test suite](docs/how-to/test-a-test-suite.md) | mutation testing: do the tests fail when the code is wrong |
| [Documentation index](docs/index.md) | every reference page: terms and commands, genres, kinds, runs and the record, the verdict's method, settings, the library, output |
| `ix --skill` | the guide an agent reads, for the installed version; `-r NAME` prints one of its references |
| [CHANGELOG.md](CHANGELOG.md), [SECURITY.md](SECURITY.md), [CONTRACT.md](CONTRACT.md) | what changed; what ix reads, writes, starts and sends; what each published surface promises |

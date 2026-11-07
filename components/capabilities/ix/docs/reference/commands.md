# Terms and commands

The words ix's output uses, and every command it has. `ix <command> --help` gives each
command's arguments; every command takes `--json`.

## The terms

| Term | What it is |
|---|---|
| **experiment** | One YAML file: the question, the genre, the subjects, the probes and the metric that decides. |
| **genre** | The kind of decision the answer informs: `evaluate` (does it meet a threshold?), `compare` (is the variant better than the control, or no worse?), `regress` (may this change ship against its baseline?), `characterize` (how does it behave across a range?), and the others in [Genres](genres.md). The genre says what you must set and supplies the rest: repeats, the order of trials, the A/A check, the estimator. |
| **subject** | A system under test: a program ix starts (`process`), an endpoint that is already up (`http`), an agent (`agent-session`), a model (`model-call`), or a test suite (`test-suite`). In a comparison one subject is the control. |
| **probe** | What is done to a subject in a trial: HTTP load (`http-load`), a prompt put to an agent (`prompt`), a command (`command`), a mutant applied (`mutant`). A probe may carry **truth**, the reference a sensor grades against; the agent never sees it. |
| **sensor** | What is measured. You name metrics, and each metric names its sensor: `ttfb` comes from `timing`, `tests_passed` from `function-test`. |
| **factor** | A condition varied on purpose. Every combination of the factors' levels is a **cell**. |
| **trial** | One subject, one probe, one cell, one repeat. The plan fixes the order of the trials before anything runs. |
| **reading** | One metric of one trial. |
| **verdict** | What the readings say about the decision: `holds`, `fails` or `inconclusive`. It rests on 95% intervals, never on a point estimate. A run that is not held to a registration gives an **indication** in its place, which is information and never a gate result. |

## The sequence

```bash
ix validate <path>     # every problem at once, nothing run; exits 3 when the file is invalid
ix plan <path>         # the decision, the trials, what it needs here; runs nothing
ix register <path>     # freeze the design beside the file, before any run
ix run <path>          # carry it out; the exit code is the verdict
ix report <path>       # the latest run's report again, computed from its record
```

`validate` and `plan` run nothing. Read the plan before you register: its `Trials` are what
a run will do, and `Requires` what it needs of this host, each line `yes` or `NO`. When one
is `NO`, the plan ends with `Runnable: no` and the fix for each; otherwise there is no
`Runnable` line.

Register before you run: the registration is what the verdict is held to, so a margin
cannot move once the numbers are in. `ix run` refuses an experiment that is not registered
(exit 15); `--exploratory` runs it anyway and gives no verdict, and `--dry-run` runs it on
canned replies. A run exits 0 when the verdict holds, 10 when it fails and 11 when it is
inconclusive ([every code](output.md#exit-codes)).

## Every command

A path is the experiment file, a directory holding `experiment.yaml`, or `-` for an
experiment on standard input. A run is a run's directory, or an experiment for its latest
run.

| Command | What it does |
|---|---|
| `ix validate <path>` | Every problem at once, each with its path, kind and fix. |
| `ix plan <path>` | The decision, the trials, every design value with where it came from, what the run needs here, and the model calls it may start. Runs nothing. |
| `ix register <path>` | Freezes the design beside the file, before any run ([Registration](runs.md#registration)). |
| `ix deviations <path>` | What changed since registration; exit 14 when anything did. |
| `ix run <path>` | Carries the experiment out; the exit code is the verdict ([Running](runs.md#running)). `--dry-run`, `--exploratory`, `--repeats N`, `--trials-in-flight N`, `--detach`. |
| `ix report <run>` | A run's report again, computed from its record, in any state. |
| `ix runs [path]` | Each run kept beside the experiment, or with no path every experiment's runs under the current directory: when it started, whether it was gated, exploratory or a dry run, the commit it ran from, its verdict or indication, its trials. |
| `ix status <run>`, `ix events <run> --follow`, `ix stop <run>` | Where a run stands, its events as they are written, and stopping it ([Long runs](runs.md#long-runs)). |
| `ix mutants sample <path>`, `ix mutants map <path>`, `ix mutants read <run>` | Mutation testing: draw the mutants, make the selection map alone, read the survivors ([Testing a test suite](../how-to/test-a-test-suite.md)). |
| `ix genres`, `ix kinds` | What is installed, and what was refused and why. `--lab <dir>` adds a lab's own `genres/` and `kinds/`. |
| `ix --skill [-r NAME]` | The skill an agent reads, or one of its references. Generated from what is installed. |

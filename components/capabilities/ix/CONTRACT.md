# ix's contracts

What each published surface of ix that 0.8 or a later release changed hides, what it consists
of, its defaults, how it fails, and how far it is promised. The [README](README.md) shows each
in use; this file is what a change to one of them must keep. The surfaces none of them changed
(the experiment file, the report and the run's tables) are described in the README and are not
repeated here; a surface snapshot test of the whole package comes once its
refactor is done.

Stability is one of two words. **Stable**: changes only by addition; a break needs a major
release, or, before 1.0, a minor release whose changelog says so and names what a user must
change (a registration to amend, an import to move). **Public**: may change in a minor release,
said in the changelog.

## The extension contract, `ix.extension`

**Stability: public.** The module's name is provisional until the owner picks one (ruling
Q3). `JudgeModels` and `SpanReader` have one built-in user each and may change without a
deprecation until a second arrives; so may the mutant-source point and `MutantSources`, until a
second source registers there. Pinned by `tests/test_extension.py`.

**The decision it hides.** How ix builds, wires and drives an adapter: the registry, the
composition root, the run loop, the store. An adapter author writes against this module alone,
as ix's own adapters do, and never imports an `ix.adapters` module.

**Members.**

| Name | What |
|------|------|
| `POINTS` | the extension point of each family: `ix.subject`, `ix.probe`, `ix.sensor`, `ix.environment` |
| `PORTS` | the port each point holds a registration to: `Subject`, `Probe`, `Sensor`, `Environment` |
| `LAUNCHER`, `RUNTIMES`, `SESSIONS`, `JUDGE_MODELS`, `MUTANT_SOURCES` | the needs, each a keyword the adapter's factory takes |
| `Launcher` | given for `launcher`: `start(argv, **options)` a child in a group of its own, `stop(process, *, grace_s=5.0)` the whole group |
| `Runtimes` | given for `runtimes`: matrix's runtimes at `AGENT_RUNTIME` or `MODEL_RUNTIME` (`Point`): `build`, `stand_in`, `takes`, `drives`, `capabilities` |
| `Sessions`, `Respondent` | given for `sessions`: `open(respondent) -> handle`, `close(handle)`, `[handle]`; a respondent is `simulated` and `answer(prompt, options)` |
| `JudgeModels` | given for `judge_models`: `model(runtime, model, config)`, a pydantic-ai `Model` over a matrix model runtime |
| `MutantSources` | given for `mutant_sources`: `names()` of the sources installed, `source(name)` built, `drives(name)` with versions |
| `MUTANT_SOURCE`, `MutantSource`, `Mutation`, `Mutations`, `Unmade` | the mutant-source point, its port and its records: its own section below |
| optional roles | `Preflight` (`requirements`), `Review` (`review`), `StandIn` (`stand_in`), `ModelCalls` (`model_calls`), `SpanReader` (`reads_spans`), `Liveness` (`lost`); for a scope, `Traced`, `Recorded` and its `Attribution` |
| records and errors | `Target`, `Interaction`, `Bindings`, `Measured`, `Sensed`, `Requirement`, `Surroundings`, `StoodIn`, `Problem`, the entry configs, `SelectedSensor`; `SubjectError`, `HarnessError`, `TrialError` |
| helpers | `seconds`, `duration_of`, `arrivals`, `placeholders`, `problem` |

**Defaults.** An adapter that is none of the optional roles has nothing to say there, and the
run proceeds. A stand-in is given an entry's options as written, since its answer is written
in their place; a review, a preflight, `model_calls` and every member of the family ports
are given them as carried out, with each default the kind declares for what is left out,
inside a mapping the kind declares further too. A probe's and a sensor's options also have
each `path` option, and each directory a `command` is `within`, resolved against the
experiment's directory.

**Failure behaviour.**

- A class registered at a point that lacks one of its port's members is refused when it is
  registered; what a factory builds is held to the port when it is built. A need nothing
  supplies makes the adapter unbuildable: `ix kinds` lists it under `unbuildable`, with why.
- `reach` and `apply` raise `SubjectError` when the subject failed and `HarnessError` when the
  harness or its tool did; anything else is read as the harness's. A sensor that raises gives
  each of its metrics a sensor failure. None of these fails the run.
- A review, a preflight or `model_calls` that raises is reported, never escapes: a problem at
  the entry (`adapter_failed`), a requirement that does not hold, an entry the work does not
  count.

## The mutant-source point, `ix.mutant-source`

**Stability: public, provisional** (ruling R2) until a second source registers there; then it
grows by addition only. Pinned by `tests/test_mutation.py` and `tests/test_extension.py`.

**The decision it hides.** Which mutants exist: the operators, the language they read, how an
occurrence is counted. ix owns the point and ships no source (owner ruling HO1 (c′)); a source
is a package of its own, `ix-cosmic-ray` the first.

**Members** (`ix.extension`).

| Name | What |
|---|---|
| `MUTANT_SOURCE` | the point, `"ix.mutant-source"`, declared by ix's entry point; a source registers there under a type URL of its own namespace, from a `matrix.extensions` entry point named `ix.mutant-source` |
| `MutantSource` | the port: `mutations(text) -> Mutations`. Pure: it reads only `text`, starts and writes nothing, and gives the same mutants under the same keys for the same text |
| `Mutation` | `key` (the mutant among the source's of that text), `operator`, `start` and `end` (`(line, column)`, line from 1, column in characters from 0), `replacement` |
| `Mutations` | `made` and `unmade` |
| `Unmade` | `operator`, `count`, `reason`: what the source meant to make and could not |
| `MutantSources` | the `mutant_sources` need: the sources installed |

**What ix does with them.** It applies each mutation to the text and keeps a **candidate**: it
parses, and it differs from the original as Python's syntax tree. Every other is counted, by
why, in the sample's record (never dropped silently):

- `unparsable`, `unchanged` and `outside_text`;
- the design's exclusions: `annotation` (a type annotation only), `type_checking` (inside an
  `if TYPE_CHECKING:` block) and `marked` (a line carrying `# ix: no mutate`).

A mutant's id is `<file>:<key>`, pinned to the file's digest (`file_digest`).

**Failure behaviour.** None installed: `ix mutants sample` refuses, and validation's
`probes_empty` at a `test-suite` says to install `ix-cosmic-ray` beside ix. Several: `--source`
chooses, and its absence is refused, naming each. A source that raises refuses the file it
was given, saying what it raised.

## Mutation testing: `mutation-score`, `mutation-regress` and their kinds

**Stability: public** for the names (ruling R3: `mutation-score`, `mutation-regress`,
`test-suite`, `mutant`, `mutant-kill`, `killed`), stable after the acceptance run and one more
user. The `mutant` probe's options are the mutant data boundary, **public and provisional**
until a second generator writes them. Pinned by `tests/test_mutation.py`, `tests/test_kinds.py`
and `tests/test_genre.py`.

**The decision it hides.** How a mutant is isolated, applied, selected, timed and attributed.

**Members.**

- **`ix.v1.genre.mutation-score`** has the analysis `characterize`, no verdict and repeats 1. A
  Wilson interval of the kill rate over the sampled mutants.
- **`ix.v1.genre.mutation-regress`** has the analysis `compare`, non-inferiority only, a margin
  and repeats 1. The difference is paired over the blocks both suites read (below).
- **`ix.v1.subject.test-suite`**:
  - `root`, `command` (the suite's Python and pytest's arguments), `tests`, `scope` and `map`
    are required;
  - `deselect` defaults to none, and `exclude` to `.git .venv .ix __pycache__ .mypy_cache
    .ruff_cache .pytest_cache node_modules`;
  - `confirm` defaults to `uncovered` (also `all`, `none`; ruling R5);
  - `timeout_factor` defaults to 3 and `timeout_floor` to 10s.

  It offers `workspace`.
- **`ix.v1.probe.mutant`**:
  - `file`, `start`, `end`, `original`, `replacement` and `operator` are required;
  - `mutant` and `file_digest` are optional, and so is `equivalent`;
  - `budget` defaults to 30m.

  It offers `outcome`.
- **`ix.v1.sensor.mutant-kill`** needs `outcome` and gives the boolean `killed`. Its detail is
  the outcome, whole.

**The outcome facet** holds:

- `status` (`killed`, `survived`, `uncovered`) and `stage` (`selected`, `confirmed`, `none`);
- `covered`, `selected` (a count, or `all`), `ran`, and `lines`: the statement whose lines
  selected the tests (a simple statement whole, a compound one's header);
- `killers`, `unattributed`, `unreproduced` (failures that did not recur on the mutant, read
  as passes) and `covered_by`;
- `imported_from` (module to `copy` or `absent`) and `drifted`;
- `timeout_s` (one limit per run) and `duration_s`.

**Defaults** are the kinds' declarations, shown in the plan with their origin.
`ix mutants sample` draws 100 from seed 1 with an hour for the baseline, and its document says
which were given.

**Invariants**, with no option to turn them off:

- the tree is never written;
- every run is in a fresh copy, with the copy's import roots first on `PYTHONPATH`, and a
  temporary directory of its own (`TMPDIR`);
- a kill counts only when the failing test fails again run alone on the mutant, and passes
  run alone on the copy with the original restored.

**Failure behaviour.** Each of these is the harness's failure: unread, counted by reason, never
killed and never survived.

- `not_the_mutant`: a scope module was imported from anywhere but the copy;
- `timeout`: a run past `timeout_factor` times its tests' and the suite's start's baseline
  time, never under `timeout_floor`, the trial under `budget`;
- `unattributable`: only tests that also fail without the mutant failed;
- `mutant_inapplicable`: the tree does not hold `original`;
- `runner`: pytest's own exit (3, 4 or 5, or a signal);
- `equivalent`: the probe is marked so;
- `map`: the selection map cannot be read.

A plan refuses a map whose scope digests are not the scope's now. When the suite's Python
lacks coverage.py and pytest-cov, the plan says what to install.

## `ix mutants sample|map|read` and `ix.mutants`

**Stability: public, provisional** (ruling R2). Pinned by `tests/test_mutation.py`,
`tests/test_ix_cli.py` and `tests/test_layering.py`.

**The decision it hides.** How the population is drawn and the selection map made, and how a
run is read for its survivors and its pruning candidates.

| Command / function | Writes | Returns |
|---|---|---|
| `ix mutants sample <path> [--n] [--seed] [--exclude <experiment>] [--source] [--timeout]`, `ix.mutants.sample` | `probes/m-<id>.yaml`, each suite's map, `mutation/population.json` (`ix.v1.mutation-population`) | `ix.v1.mutation-sample`: the population by operator, `dropped`, `unmade`, `settings` (each value and whether it was given), per subject its baseline, uncovered mutants drawn, `estimated_s` (the test time a run takes, one trial in flight) and `worst_s` (with every covered mutant confirmed, under `confirm: all`) |
| `ix mutants map <path> [--timeout]`, `ix.mutants.map` | each suite's map | `ix.v1.mutation-maps` |
| `ix mutants read <run> [--all]`, `ix.mutants.read` | nothing | `ix.v1.mutants-reading`: per subject the kill rate and unread by reason; survivors (`assertion_gap` or `coverage_gap`, `at_import`); `pruning` (judged, keep, candidates with `kills`, `zero_kill`, `also_runs`, and `deselect`); caveats |

**Maps.** One map (`ix.v1.mutation-map`) belongs to each suite: its tree, command and scope.
Subjects of one suite share a baseline, and may name one file.

**Defaults.**

- `--n` is 100, `--seed` is 1 and `--timeout` is 3600 s.
- `read` lists 50 survivors and 50 candidates; `--all` lists every one.

**Failure behaviour.** Each refusal is `config`, with a `fix`, and **nothing is written**:

- no `test-suite` subject;
- no source installed, or several and none named;
- a `probes/` that holds files;
- different scopes across the subjects;
- coverage.py or pytest-cov missing in the suite's environment (install pytest-cov there);
- a baseline that does not pass, naming the failing tests;
- a baseline that imported the scope from outside its copy;
- every candidate excluded.

## The paired difference of two booleans in `compare`

**Stability: stable rule** (ruling R1), under `ix.v1.analysis.compare`; no id or digest
moved. Pinned by `tests/test_calibration.py` and `tests/test_analysis.py`.

- **The rule.** Two booleans read in the same blocks get Tango's score interval of the paired
  table of blocks, in place of Newcombe's unpaired method 10. Each end's level is the score
  test's p-value, in closed form.
- **What it changes.** An existing boolean `compare` or `regress` verdict may change.
- **What it keeps.** A count's difference keeps `newcombe (clustered wilson)`.
- **The named weakness.** Where only one side ever loses, a false `fails` reaches about 4%,
  where 2.5% is stated (design record §32).

## A kind option's default

**Stability: stable** (`ix.v1.kind`). Pinned by `tests/test_kind_defaults.py`.

**The decision it hides.** What an entry that leaves an option out is carried out with. The
declaration holds it, never the adapter, so the plan can show it and a registration can
freeze it.

**Members.** `Option.default`: a value of the option's type (and of its `allowed` values),
on an option that is not required; it holds no placeholder but those an option enclosing it
`binds`. `Option.default_by` (`DefaultBy`): a default that follows the value of an option
beside it, `{option, values}`. `Option.default_from`: the options beside it that may name the
runtime whose own value it is, the first one written deciding, or `subject.<option>` of the
subjects a probe or a sensor reads. `Option.left_out`: what leaving the option out means, for
an option whose absence is a behaviour rather than a value (`tools`: every tool the runtime
offers); the plan lists it from the kind, with no value and that meaning as its note. At most
one of the four. `Option.fields`: a mapping's
fixed keys, each declared as an option is; `Option.entries`: what each value of a mapping
whose keys the author names is. The rules beside an option hold at every level. The design holds what it fills as `defaults`, by entry key (`environment`,
`subjects.<name>`, `probes.<id>`, `sensors.<kind>`); the plan's provenance lists each, from
`kind` with the kind's type URL, and each value left to a runtime, from `runtime`, naming it.

**Defaults.** An option with none of the three has no value when it is left out: a feature
not used. Defaults fill inside a written mapping; what is written wins, key by key.

**Failure behaviour.** A kind file whose default does not fit its option, holds a
placeholder nothing binds, or follows or names an option that is not beside it, is refused
whole (`kind_invalid`). A written mapping is held to its declaration key by key
(`option_unknown`, `option_missing`, `option_invalid` at the nested path). An option left out
whose default follows one a cell fills must be written (`option_missing`). A
default a later ix declares otherwise is a deviation of every registration that relied on it
(`design.defaults.<entry>.<option>`), so the run is not gated until the registration is
amended.

## A kind's record, by family

**Stability: stable** (`ix.v1.kind`). Pinned by `tests/test_kinds.py`
(`test_a_kind_declares_only_what_its_family_has_and_says_which_family_does`,
`test_a_kind_built_in_code_is_its_familys_record`, `test_an_entry_names_a_kind_of_its_own_family`).

**The decision it hides.** Which keys a kind of each family has. The family set is closed
(`KIND_FAMILIES`), so a key one family needs is that family's record's, and no other record
carries it.

**Members.** `ix.Kind`: the union of `SubjectKind`, `ProbeKind`, `SensorKind` and
`EnvironmentKind` (each in `ix.domain`), discriminated by the family segment of `name`. Every
record declares `schema`, `name`, `summary`, `options`, `offers`, `needs` and `overlap`;
`ProbeKind` adds `duration`, `SensorKind` adds `metrics`, `truth` and `confusion`,
`EnvironmentKind` adds `service_keys`. `Kind.family` is the record's family. `Kinds.get(family,
name)` and `Kinds.of(family)` return that family's record.

**Defaults.** As each field declares: `overlap` `forbidden`, every list empty, `confusion`
none. A sensor's `metrics` has no default.

**Failure behaviour.** A kind file whose `name` names no kind's family is refused
(`kind_invalid`, at the file). A key the family's record does not have is refused: one another
family's record has is `kind_invalid` at that key, naming the family; any other is
`unknown_key`. A sensor with no `metrics` is `missing_key`, one with an empty list or a metric
named twice `kind_invalid`; so is a probe whose `duration` names an option that is not a
duration. A record built in code whose `name` names another family is a `ValidationError`.
`Kinds.get` returns None for a kind of another family, however it is named.

## The registered design and the plan digest

**Stability: stable** (`ix.v1.registered-design`, version 1). Pinned by
`tests/test_plan_digest.py`, which holds the digest of every checked-in example and of each
built-in genre's `--skill` example.

**The decision it hides.** How ix's own records are named. A registration persists for the
life of an experiment and its `plan_digest` gates every run of it, so both read a form built
key by key, never a record's field names.

**Members.** `design_form(design)`: `schema`, `experiment`, `genre`, `roles` (`subject`,
`role`, `kind`), `control`, `primary` (`metric`, `statistic`, `estimator`), `threshold` (`op`,
`value`, `unit`), `comparison`, `margin` (`value`, `basis`), `guardrails`, `metrics` (`name`,
`unit`, `direction`, `shape`, `judged`, `bounds`, `flag`), `sensors` (`kind`, `metrics`,
`options`, `origin`, `confusion`), `defaults`, `repeats`, `seed`, `trials_in_flight`,
`order`, `calibration`, `analysis` (`id`, `params`), `verdict_form`, `report`. The plan
digest is the sha256 of the canonical JSON of the experiment's operative digest, that form,
the probe set's digest, the trial count and the schedule's digest.

**Defaults.** A value that is absent (None) is left out; a key a later version adds is left
out when absent, so it moves no digest of a design that does not use it. Not in the form:
the hypothesis sentences, the provenance, a metric's description, and whether a metric is
contended (policy, read where it is used: it fails closed).

**Failure behaviour.** A registration whose `plan_digest` differs is a deviation, and the run
is ungated, its finding an indication. A release that moves a pinned digest updates the
literal and says in the changelog which registrations to amend.

## The work a plan counts

**Stability: public.** Pinned by `tests/test_model_calls.py`.

**The decision it hides.** Which adapters start model calls, and how many: each adapter
says, the plan adds them.

**Members.** `Plan.work` (`Work`): `model_calls`, at most the agent sessions, model calls and
judges' agent runs a run starts, each counted once however many model requests it makes;
`entries`, each a `ModelCallCount` (`entry`, `per_trial`, `trials`, `total`); `uncounted`,
each entry whose adapter may call a model (it needs `runtimes` or `judge_models`) and does not
say how many, or raised counting, with why. `text()` says it in one line. Never a price.

**Defaults.** An entry whose adapter is not a `ModelCalls` and needs neither is not listed: it
calls no model ix can see. A stand-in counts none.

**Failure behaviour.** It is not part of the digest, and nothing is refused on it.

## `check`, the `command` probe and the `exit-status` sensor

**Stability: stable** for the names and options (`ix.v1.genre.check`, `ix.v1.analysis.check`,
`ix.v1.probe.command`, `ix.v1.sensor.exit-status`, `exited_ok`, the problem kinds
`decision_inapplicable` and `metric_shape`); **public** for the words of the method a report
prints. Pinned by `tests/test_check.py`.

**The decision it hides.** Whether a predicate held, observed: no interval and no estimate.

**Members.** The genre: one subject, a boolean primary, `repeats` 1 by default and at least 1,
factors allowed, three-way. The analysis, for each subject in each cell: `holds` when the
primary read true on every trial; `fails` when false on one, or the subject failed one;
`inconclusive` only when a trial could not be read or none was carried out. Its method says
it is an observation, not an estimate; its notes give each subject's Wilson interval of what
it saw. The `command` probe: `command` (required, `within: cwd`), `cwd`, `env`, `timeout`;
offers `output` (`exit_code`, `stdout`, `stderr`, `truncated`). The `exit-status` sensor:
`code`; needs `output`; provides `exited_ok`.

**Defaults.** `cwd`: `.`; `timeout`: `60s`; `code`: 0; each stream kept: its last 64 KiB.

**Failure behaviour.** A threshold, a margin or a guardrail on a `check`, or on any genre
that gives no verdict (`characterize`), is refused (`decision_inapplicable`); a primary
that is not a boolean is refused (`metric_shape`). A
command that cannot start, or runs past its `timeout`, is the harness's failure, so the check
is inconclusive, never failed. A command that exits is an observation, whatever its code.

## An override on a run that gives a verdict

**Stability: stable.** Pinned by `tests/test_trials_in_flight.py`.

**The decision it hides.** None: it is a rule. A run that gives a verdict is held to the
design its registration froze, and `running.prepare` alone says so.

**Members.** `ix run --repeats N` and `--trials-in-flight N` (`ix.run(repeats=,
trials_in_flight=)`), allowed with `--exploratory` or `--dry-run` only.

**Failure behaviour.** Otherwise `UsageError` (kind `usage`, exit 2), its `details.path` the
first flag and `details.value` its value, its fix the pilot to run instead; refused before the
changed experiment is judged.

## Long runs: `ix run --detach`, `ix status`, `ix events`, `ix stop`

**Stability: public** (`ix.v1.status`, version 1, and the four commands). Pinned by
`tests/test_long_runs.py`. Built to the owner's ruling (panel 10-09 §6, reaffirmed as Q5):
long runs outlast a tool call, and the primary caller is an agent.

**The decision it hides.** How a run outlives the call that started it, and how anything
else learns where it stands. The run is a child process in a session of its own, and the
run's record is the only channel between it and every other command: no socket, no pid
file of ix's own. Whether its process is alive is matrix's store's to say (`writer.lock`).

**Members.**

- `ix run <path> --detach` (`ix.start(path, ...)`, the flags of `ix run` but `-`): refuses
  as `ix run` does, then starts `python -m ix run <path> ... --run-id <id> --json` detached
  and returns its `Status` once the child has opened the record. `--run-id ID`
  (`ix.run(run_id=)`, `ix.start(run_id=)`) names any run.
- `ix status <run>` (`ix.status(run)`): `Status`, read from the record alone.
- `ix events <run> [--follow]` (`ix.events(run, follow=)`): the run's `matrix.v1.event`
  documents, oldest first; with follow, each as it is written until the run ends, and
  refused at once for a run still written on another host.
- `ix stop <run>` (`ix.stop(run)`): SIGTERM to the process writing the run, then its
  `Status`.
- `Status` (`ix.v1.status`): `run`, `location`, `experiment`, `status` (`running`,
  `completed`, `interrupted`, `failed`), `mode`, `started_at`, `ended_at`, `planned`,
  `carried_out`, `pid` and `host` (the writer, `pid` only while it runs on this host),
  `events` (the event log), `log` (a detached run's standard error), `exit` (`code`, `kind`,
  `meaning`, `next`). `<run>` is a run's directory, or an experiment, meaning its latest run.

**Exit codes**, the same for every command that prints a `Status` (`--detach`, `status`,
`stop`, and `events` when it ends): 13 (`running`) while the run runs, 0 (`ok`) once it
completed, which is no verdict (`ix report` gives that), 12 (`run_interrupted`) for a run that
was stopped or whose process died, 8 (`run_failed`) for one the harness could not finish. No
new code: each is the one `ix report` already gives that state.

**Defaults.** A run's id is `<experiment>-<UTC time>-<4 hex digits>`. A detached child has
`STARTUP_S` (120s) to open its record; `ix stop` waits `STOP_WAIT_S` (30s) for the run to
end; a follower reads the record and the event log again every `POLL_S` (0.25s). Each is a
bound on the apparatus, not a choice of the experiment's, so none is configured. The child
reads nothing on standard input, its standard output is discarded (`ix report` computes the
report again), and its standard error is appended to `<runs directory>/<run id>.log`.

**Failure behaviour.**

- `--detach` refuses before starting anything, with the error and exit code `ix run` gives:
  an invalid or unregistered experiment, a requirement that does not hold, an override a
  gated run refuses, a `--run-id` that is taken or unsafe (`usage`), a path of `-`
  (`usage`). A child that exits before opening its record (a race: the parent checked first) is
  raised as the `ix.v1.error` it printed, as its kind where that is `usage`, `config`,
  `not_found` or `unregistered` and as `unknown` otherwise, with `details.log`; one that
  printed none is `unknown` with its log's end; one that has not opened it within
  `STARTUP_S` is stopped (SIGTERM, then SIGKILL after 5s) and the start is `config`.
- A run whose process died on this host reads as `interrupted` (matrix finishes its
  record), so `status` says so and a follower ends. A run still written on another host is
  read as its record says: `stop` and `events --follow` refuse it (`usage`), since whether
  its writer is alive cannot be read from here; `events` without follow reads its events as
  they stand. So every follower ends.
- `stop` on a run that has ended does nothing; on a process it may not signal, `config`. A
  run still releasing after `STOP_WAIT_S` is returned `running`: ask again.
- What a SIGKILLed run started (a subject, a probe) is not released: as for a run in the
  foreground, nothing reaps it later (README, Not built).


# ix's contracts

What each published surface of ix that 0.8 changed hides, what it consists of, its defaults,
how it fails, and how far it is promised. The [README](README.md) shows each in use; this file
is what a change to one of them must keep. The surfaces 0.8 did not change (the experiment
file, the CLI's commands and exit codes, the report and the run's tables) are described in the
README and are not repeated here; a surface snapshot test of the whole package comes once its
refactor is done.

Stability is one of two words. **Stable**: changes only by addition; a break needs a major
release, or, before 1.0, a minor release whose changelog says so and names what a user must
change (a registration to amend, an import to move). **Public**: may change in a minor release,
said in the changelog.

## The extension contract, `ix.extension`

**Stability: public.** The module's name is provisional until the owner picks one (ruling
Q3). `JudgeModels` and `SpanReader` have one built-in user each and may change without a
deprecation until a second arrives. Pinned by `tests/test_extension.py`.

**The decision it hides.** How ix builds, wires and drives an adapter: the registry, the
composition root, the run loop, the store. An adapter author writes against this module alone,
as ix's own adapters do, and never imports an `ix.adapters` module.

**Members.**

| Name | What |
|------|------|
| `POINTS` | the extension point of each family: `ix.subject`, `ix.probe`, `ix.sensor`, `ix.environment` |
| `PORTS` | the port each point holds a registration to: `Subject`, `Probe`, `Sensor`, `Environment` |
| `LAUNCHER`, `RUNTIMES`, `SESSIONS`, `JUDGE_MODELS` | the needs, each a keyword the adapter's factory takes |
| `Launcher` | given for `launcher`: `start(argv, **options)` a child in a group of its own, `stop(process, *, grace_s=5.0)` the whole group |
| `Runtimes` | given for `runtimes`: matrix's runtimes at `AGENT_RUNTIME` or `MODEL_RUNTIME` (`Point`): `build`, `stand_in`, `takes`, `drives`, `capabilities` |
| `Sessions`, `Respondent` | given for `sessions`: `open(respondent) -> handle`, `close(handle)`, `[handle]`; a respondent is `simulated` and `answer(prompt, options)` |
| `JudgeModels` | given for `judge_models`: `model(runtime, model, config)`, a pydantic-ai `Model` over a matrix model runtime |
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

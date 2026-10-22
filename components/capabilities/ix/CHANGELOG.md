# Changelog — ix

## Unreleased

### Added: pydantic-evals' evaluators as a sensor

The `evaluator` sensor runs one of pydantic-evals' evaluators over each trial, named by
`evaluator` with its `arguments`. ix keeps the loop: each trial is handed to the evaluator
as pydantic-evals' `EvaluatorContext` (the prompt, the reply, the probe's truth
`expected_output` and `metadata`, the probe's duration and its spans); `Dataset.evaluate` is
not used. Record section 24 has the reasons.

- Its assertions are `eval_passed` and its score `eval_score` (`result` names one of
  several); an evaluator that calls a model (`LLMJudge`, `GEval`) gives
  `judged_eval_passed` and `judged_eval_score`, which never decide. Labels and every result
  with its reason are in the reading's detail.
- The span evaluators (`ToolCorrectness`, `TrajectoryMatch`, `ArgumentCorrectness`,
  `MaxToolCalls`, `MaxModelRequests`, `HasMatchingSpan`) read the spans that ended in the
  trial, captured in process from the installed OpenTelemetry SDK and kept in the
  interaction (`watched`). Without them (no SDK, or matrix's `otel` observer not on the
  run) the reading fails and says why, rather than reading as no tool called.
- A judge evaluator's model comes from the matrix model runtime `model_runtime` names,
  through the root's new `judge_models` need, never from its arguments or pydantic-evals'
  default model; a stand-in runtime judges on pydantic-ai's `TestModel`. The root has no
  pydantic-ai model over a real runtime yet (hardline's), so such a judge is
  `Runnable: no` in the plan.
- `pydantic-evals` is a dependency, imported only inside the sensor's adapter.

### Added: an agent session on matrix's `pydantic-ai` runtime

An `agent-session` subject names matrix's second agent runtime, `runtime: pydantic-ai`, as it
names `claude-sdk`: its `runtime_options` are that runtime's config (`models`,
`default_model`, `tools`, ...), reviewed against what it declares, and each trial's session
row names the model that answered and what the runtime drives (`pydantic-ai-slim`,
`hardline`). Nothing in ix changed to allow it; the skill's prerequisites and the README name
it.

### Added: `environment.compose`

A docker compose project brought up once for a run and down after it, for experiments whose
subjects need several services. Record section 22 has the reasons.

- `docker compose up -d --wait` brings it up, healthchecks included; `down -v
  --remove-orphans` is deferred before `up`, so a stack that came up in part, or whose up
  was cancelled, still comes down. The environment's releases are given longer than
  matrix's default for it.
- Each running service is bound with its first published port (`{web.url}`, `.host`,
  `.port`) and the image id it runs, in the run's bindings row. The compose file and
  `env_file` are digested into the registration and the manifest.
- Without `project`, the project is named for the run, and a stack a killed run left is
  swept; a `project` written that already has containers is refused.
- Options: `file`, `project`, `services`, `wait_timeout`, `profiles`, `env_file`, `build`,
  `pull`. The kind's `file` is now a `path`.
- The preflight checks docker, its daemon, compose and the files; liveness is `compose ps`.
- A dry run says the environment is brought up for real (`stood_in`), for `process` too.
- `Scope` has `run_id`.

### Added: environment bindings reach the trials

- **A subject or a probe names a service by its bindings.** `{<service>.url}`, `.host` and
  `.port` in a subject's or a probe's options are filled from what the environment bound
  for that service, and `validate` accepts them for each service the environment's options
  name. An environment kind declares the keys it binds (`binds:`). Before, bindings were used
  only to ask whether the environment was still up.
- **`environment.process` binds where a service listens**: its new `url` option, or the
  address of its `ready` URL.
- **A run whose environment came up without a service the experiment names** fails before
  any trial (`unbound`), naming what it did bind.

### Fixed

- `environment.process` asks the process it started whether it is still up. It asked
  `os.kill(pid, 0)` of the stored pid, which read a pid the system had given to another
  process as the service still up, and raised out of the run for one another user owns.
- A sensor kind listed twice under `sensors:` is a problem (`name_taken`, at the second
  entry). A run keeps one entry of each sensor kind, so the second silently replaced the
  first one's options.

### Changed (breaking): requirements in place of effects

Effects were labels nothing checked (Q6). Record section 21 has the reasons.

- **Removed**: `effects` in kind files (a kind file that still declares them is refused),
  `effects:` beside an experiment's subject, probe, sensor or environment (now an option the
  kind does not take), `Plan.effects` and `Effect`, `EffectName` and `EFFECTS`, and the
  `effects` column of `ix kinds`.
- **Every adapter may say what it needs of this host.** `Preflight` was the subjects'
  alone (`unstartable`); it is now `requirements(options, *, base)`, optional for any
  adapter, each `Requirement` with `need`, `holds`, `why` and `fix`. `process`, `http`,
  `agent-session` and `model-call` check what they checked before; `http-load` checks `k6`
  on PATH, `environment.process` each service's program, and `judge` its runtime.
- **The plan lists them.** `Plan.requirements` (each `need`, `holds`, `why`, `fix` and the
  entries that need it) replaces `Plan.unstartable`; `ix plan` prints them under
  `Requires:`, and those that do not hold under `Runnable: no` with the fix. `ix run` refuses
  to start while one does not hold.
- **Registrations change digest.** The plan digest no longer covers effects, so every
  experiment registered before this release shows a `plan_digest` deviation and its next
  run is ungated: amend the registration (`ix register <path> --amend --reason "effects
  removed"`) to gate it again.

### Changed: an adapter is held to its port, and one that cannot be built cannot run

- **The kind ports are checked at registration.** `Subject`, `Probe`, `Sensor` and
  `Environment` are runtime-checkable, and each is the port of its family's point. A class
  registered there that lacks a member (a subject with no `reach`) is refused when it is
  registered, so its extension is quarantined, rather than validated and run with every trial
  a harness failure. What a factory builds is held to the port when it is built.
- **An adapter that cannot be built is not runnable.** `ix kinds` lists the kinds whose
  adapter builds here as `runnable`, and the rest under `unbuildable` with why (a need
  nothing supplies). `ix validate` warns of it (`kind_unimplemented`, with the reason), `ix
  plan` says `Runnable: no` with it, and `ix run` refuses it naming it.
- **Who keeps a kind or genre name does not depend on the order files are read.** An owner
  read after two impostors was refused as a third duplicate.

### Added: a verdict traces to the model that answered

`docs/design/2026-10-19-record.md` §19 has the reasons.

- **Each interaction and reading names the sessions behind it.** `Interaction.sessions` holds
  the rows of the run's `matrix.v1.session` table behind the trial: every agent session
  (each round of a loop) and model call the subject made, failed ones included.
  `Reading.sessions` holds the rows its sensor made to read the trial: a judge's calls or
  sessions. A reading joins its interaction on `seq`, and each row says which runtime and
  which model answered, its usage and ids, and whether it failed and why. The README shows
  the join in DuckDB, and `ix --skill -r running` with `jq`.
- **ix's own model calls are recorded.** The `model-call` subject and a judge on a model
  runtime made their calls outside matrix's session table; each call is now a row of it,
  failed or not, as a model step's is. Agent sessions, the judge's included, are recorded
  under their runtime's type URL.
- **Session rows carry the trace they ran in.** A run's executor is matrix's composed one, so
  with OpenTelemetry active each session row holds the trace and span ids of the trial or
  sensor it ran under.

### Added: a verdict is tied to what it judged

The experiment's digest covers its document, which names the subject's files by path. Now
what is at those paths is digested too, and a run records where it ran from.
`docs/design/2026-10-19-record.md` §18 has the reasons.

- **The subject's contents are frozen at registration.** The registration and each run's
  manifest hold the digest of every file and folder the subjects and sensors name:
  `skills`, `plugins` (each folder a `*` matches), `fixtures`, `definition`, `graders`,
  `rubric_file`, and any option a kind declares `path` or `paths` (`contents`: the path
  as written and the digest, never what the file holds). A folder's digest is its files'
  relative paths and bytes, never their times.
- **A changed subject is a deviation.** A skill, plugin, fixture or definition edited,
  deleted or added after registration is a deviation named by what changed (`skill review
  changed after registration`, field `subjects.agent.skills[skills/review]`). A run of it is
  not gated and exits 14, as one with an edited probe file does. A registration written
  before this release holds no contents and is not compared on them; delete it and
  register again to freeze them.
- **Where a run ran from.** The manifest's `provenance` holds the git commit the
  experiment sits at and `dirty` (a tracked file differs from it), or why there is none
  (`not a repository`); the installed versions of ix, matrix and hardline; and every agent
  and model runtime installed, by type URL, with the distribution that registered it and
  its version. `ix report` prints `Contents`, `Ran from` and `Versions`; `ix runs` prints
  each run's commit; `Report.contents`, `Report.provenance`, `RunSummary.commit` and
  `RunSummary.dirty` carry them in `--json`.
- **A kind option type `paths`**: a list of paths, each a string or a mapping with a
  `path`. `agent-session`'s `skills`, `plugins` and `fixtures` are declared with it, so an
  item that names no path is `option_invalid`.

### Changed (breaking): agent runtimes and model runtimes are two ports

matrix now has two runtime ports, `agent-runtime` and `model-runtime`, and ix moves onto
them. Nothing in ix branches on a runtime's name any more. `docs/design/2026-10-19-record.md`
§17 has the reasons.

- **`agent-session` takes agent runtimes only** (`claude-sdk`, `mock`, or any installed at
  matrix's `agent-runtime` point). `runtime: model` is gone from it; measure a model with
  `model-call` instead.
- **New subject kind `model-call`.** It takes model runtimes (`hardline`, `mock`, or any
  installed at `model-runtime`). Each trial is one model call through `ModelRuntime.call`,
  with `model`, `system_prompt` and `runtime_options`. The runtime resolves the `model`
  before anything starts. The interaction's `usage` keeps the call's facts: the model that
  answered, request id, attempts, retries, `fallback_from`, `finish_reason`, `refusal`,
  latency, and tokens with the cache's. `activation`, `tool-usage`, a `function-test`
  `file` and a probe's `collect` are refused on it (`unobservable`).
- **Runtime options pass through.** `permission_mode` and `setting_sources` are no longer
  options of `agent-session`. Write them under `runtime_options`, which is handed to the
  runtime as written; `validate` refuses a key the runtime's config does not declare
  (`option_unknown`). A runtime warning is reported at
  `subjects.<name>.runtime_options.<key>`. `plugins` stays ix's own option, its paths
  resolved and handed to the runtime as its `plugins`; on a runtime whose config takes
  none it is `option_inapplicable`. The SDK options on another runtime are no longer
  `option_inapplicable`.
- **Skills no longer change `setting_sources`.** ix added `project` to it for a session
  given `skills`. Now `validate` refuses `skills` with `setting_sources` that leave
  `project` out, and the fix says to add it.
- **The judge** names `model_runtime` (one model call per reading) or `agent_runtime` (a
  one-turn session with no tools per reading), not `runtime`, and `validate` refuses one
  that names neither or both. An agent-runtime judge gets `setting_sources: []` when its
  runtime's config declares the key, whatever the runtime is called. The reading's
  `detail.judge` holds `port` and the runtime's type URL.
- **Stand-ins come from the registration** (`Entry.stand_in`), not from effects or a name.
  A dry run puts each subject and judge on the `mock` of its own port and sets aside
  `runtime_options` and `plugins`. Skills are kept, as copied files. Any stand-in is
  handed each probe's `mock_response` as its config's `default`.
- **The `respondent` facet** replaces `agent`. Both subject kinds offer it, and `prompt`
  and `inspect-task` need it.
- `usage.runtime` is the runtime's type URL (`matrix.v1.agent-runtime.claude-sdk`). The
  `usage` sensor leaves out a metric whose fact the runtime did not report.
- The `runtimes` need an adapter declares is now built by point and name:
  `build(point, name, config, *, cwd=None)`, `stand_in(point, name)`, `takes(point, name)`.

### Removed (breaking)

- **Spend.** ix no longer limits or estimates what a run costs. The ceiling was checked only
  between trials, rested on whatever cost an adapter chose to report (the `model` runtime
  reports none), and counted calls matrix already accounts for; it is gone, and not
  replaced. `docs/design/2026-10-19-record.md` §11 has the reasons.
  - An experiment's `spend:` (`ceiling`, `per_trial`, `currency`) is an unknown key, and
    `spend_cap` in ix's settings is refused. Delete both.
  - The problems `spend_unbounded` and `spend_over_ceiling` are gone, and `spend` is no
    longer a key a genre's `must_set` may name.
  - The `spend` effect is gone: `agent-session` declares `tools, process, filesystem`, and
    `judge` declares none. A registration of an agent experiment made before this release
    differs from its plan, so amend it.
  - The plan has no `spend` (`SpendEstimate`), the report no `spend` (`Spent`), and
    `ix runs` no spent total (`RunSummary.spent`).
  - The run manifest has no `spends`, `spend_ceiling` or `spend_ceiling_source`; the
    interaction and the reading have no `cost_usd`; `TrialError` carries no cost; the spans
    have no `ix.trial.cost_usd` or `ix.sensor.cost_usd`, and the `ix.trial.end` event no
    `cost_usd`.
  - `Sensed.cost_usd` is gone from the `Sensor` port, and the port's spend contract with
    it.
  - `ix.domain` no longer exports `Spend`, `SpendEstimate`, `Spent` or `Halt`.
- **The halt.** Spend was its only cause, so a run no longer stops short of its schedule
  on its own: the `ix.v1.halt` table and its row, `RunRecord.halt`, `Report.halt`, the
  `ix.run.halted` event and the trials component's `provides` entry are gone. A run is
  `interrupted` (exit 12) only when it is stopped.

### Fixed

- **A subject with a mapping option was never preflighted.** Any `{` in an option's text
  read as a placeholder, so a session with `runtime_options` or a `loop` had its runtime
  neither built nor checked before the run. Only a `{placeholder}` defers the check now.
- **A count's threshold decision at many units per trial.** Over thousands of units the
  score test's level is exactly 0, and the decision stopped there instead of also reading
  the t test at Korn and Graubard's effective sample size. A count could then hold `< 0.2`
  while its own printed 95% interval contained 0.2. The level is now the larger of the two
  whenever the bound lies inside (0, 1), as the interval is.
- **A rate's margin and threshold are range-checked.** On a boolean or a count a bare
  margin is a share, so `margin: 5` allowed a loss of 500 points and held for a variant
  that lost 80; a threshold of `5`, or of 0 or 1, was passed by every rate or by none.
  `validate` now refuses a rate margin of the whole range or more (`1`, `100pp`) and a rate
  threshold outside (0, 1) as `invalid_value`. A rate threshold written in percent
  (`90%`) is still `unit_mismatch`, and its fix now says the share (`0.9`), not "a bare
  number".
- **A/A with a control that reads the same every time.** A deterministic control (a size,
  a count, a fixed suite's score) read the same number in both samples, the check found no
  spread, could not be made, and made the run inconclusive with the reason "the environment
  did not hold still enough". Such a control held still: the check passes. One whose second
  sample reads the same amount off in every block differs from itself. A check that cannot
  be made now says that nothing shows the environment held still, not that it moved.
- **"No spread" in a relative comparison.** It was tested as exact proportionality between
  the two sides, which constant readings such as 0.7 and 0.3 miss in binary; Fieller's
  interval then had zero width and decided at a level of 0. It is now zero variance: every
  block reading the same number on both sides decides nothing, as documented.
- **A registered run a stand-in answered exits 16 (`simulated`), never 0.** An experiment
  registered with `runtime: mock` written in ran held to its registration, gave no verdict
  and exited 0, the code for `holds`, so a gate on the exit code passed a run that measured
  nothing under test. It now exits with ix's own kind `simulated` (16, from 10 up as
  `exit-codes.json` asks), and the report, `ix runs` and `RunSummary` say it was not
  `gated` (`RunRecord.gated` and `RunRecord.stood_in` decide both). `--exploratory` and
  `--dry-run` still exit 0: no gate was asked for.
- **An agent session that runs past its probe's `timeout` is the subject's failure**
  (`overran`), not a harness timeout. As missing data, an agent that hung on its hard
  probes had exactly those trials left out, and its pass rate was computed over the probes
  it finished: hanging on one probe of two and passing the other held `>= 0.8`. A runtime's
  own `timeout` reason (a provider that did not answer) stays the harness's; ix's harness
  timeout is now a backstop 30 s past the session's limit, for a session that does not
  stop when its time is up.
- **A count's estimator is `rate`.** A `count` metric took the genre's estimator, so the
  plan, the registration and each decision said `mean` while the analysis pooled it as a
  rate. Like a boolean, it now resolves to `rate` (`source: ix`), from the one default the
  design, validation and the analysis share. A registration made before this release with
  a count it names no estimator for differs from its plan: amend it.
- **A genre's hypothesis sentence is read as `str.format` reads it.** The check saw only
  `{identifier}`, so a positional `{}` or a lone brace loaded, then raised a bare
  `ValueError` from every validate and plan of every experiment that named the genre. Such
  a genre file is now refused when it loads (`genre_invalid`); a literal brace is written
  doubled.
- **Factor levels that read the same in a cell are one level.** Levels were told apart by
  `repr` and cells by `str`, so `1` and `"1"` were accepted and their blocks merged into
  one cell, keeping one trial of each block silently. Such levels are refused
  (`invalid_value`).
- **A rate of part arrivals is refused, not rounded.** `2.5/s` ran as 2/s and `0.6/s` as
  1/s, with no notice. A `rate` is a whole number of arrivals per second or per minute
  (`option_invalid`, whose fix says `2.5/s` is `150/m`).
- **A reading outside its shape's domain is refused where it is made.** `Reading` accepted
  a boolean of 2, negative events, more events than units and a statistic that is not
  finite, and the analysis of such a row failed with a bare `math domain error`. Such a
  reading is now a `ValueError` when it is built, and a stored run holding one is a damaged
  record: `ix report` exits 3 (`config`) and names the file.
- **An empty run says why it is inconclusive**: "no trial was carried out, so nothing was
  measured", where the reason was empty.
- **The median's interval is computed in linear time.** The sign test's tail was summed
  again for every candidate order statistic, in big integers: one interval over 3,000
  values took 50 s. It is summed once; the interval and its level are unchanged.
- **`validate` refuses a run too thin for any interval.** It held a median to its 6 blocks
  but no decision to the 3 every interval needs, so a third-party genre with `repeats.min`
  1 validated and every decision of every run was then "too few". Fewer than 3 blocks
  (repeats x probes) in a cell is `repeats_below_min` in a genre that decides.

### Kept

- What a runtime reports a session cost stays a fact about the session: `cost_usd` in its
  `usage`, read by the `usage` sensor's `cost` metric. A judge's reported cost moves into
  its readings' `detail`, beside its runtime and model. Nothing adds either up.
- The Claude Agent SDK's own `max_budget_usd`, given in `runtime_options`, still ends a
  `claude-sdk` session (`stop: max_budget_usd`, read as `hit_limit`).

### Fixed

- **`activation` compares skill names whole.** The expected name was matched as a part of
  the fired one, so `code-review-lite` firing read as `review` firing, and its detail
  recorded `review` as fired; the confusion table, precision and recall followed. Now
  `expected_skill: review` is fired by `review` or `plugin:review` only, and
  `expected_skill: plugin:review` by `plugin:review` only. `fired` is the skill that did
  fire, written as the expected name is.
- **`tool-usage` compares `expected_command` whole**, ignoring case: `dig` no longer
  matches `digest`.
- **`http-load` counts the requests a subject never answers.** A request still waiting
  when the measured window closed was cut off and counted nowhere, and k6's 60s request
  timeout never fired inside a typical window, so a server that hung on one request in
  four read `error_rate` 0. Now every request sent in the window is waited for, past it if
  need be, until it is answered or times out, and one k6 cut off anyway still counts as
  sent and failed. The new option `timeout` sets how long a request may go unanswered
  (k6's 60s when absent), and a trial's time budget grows by it.
  - `error_rate` (and the facet's `error_rate`) also counts each arrival an open model
    dropped, because every client was waiting on the subject: `(failed + dropped) /
    (sent + dropped)`.
  - A subject that answered nothing inside the window failed the trial (`no_answer`).
    It was read as the harness sending nothing (`no_requests`), so the trial was left
    out as missing data.
  - Latency and time to first byte now include answers that came after the window
    closed, to requests sent inside it; throughput still counts only the answers inside
    the window.
- **`function-test`'s code no longer grades itself.** The code ran in the process that
  compared its answers and reported on the stdout it shared, so code that printed passing
  results and exited, rewrote the expected values, or returned a value claiming to equal
  anything read as passing. The child now gets only the inputs and reports what each call
  returned or raised, on a channel of its own with a per-run token; ix compares with the
  truth. A returned value is compared as JSON carries it (an integer- or float-like value
  as its number); one JSON cannot carry fails its case with its `repr`.
- **`text-match` refuses an empty list.** `contains: []` or `excludes: []` passed validate
  and then checked for the literal text `[]`. It is now `option_invalid` at validate, and a
  sensor failure if it reaches a trial.
- **A sensor that gives the wrong shape fails its readings, not the run's sensing.** A
  sensor that returned something other than a mapping of metrics to values, or a count
  with a number too large for a float, raised out of the sense step, so no reading of the
  run was written. Each is now a `sensor` failure (`shape`) of that sensor's readings.
- **The judge reads nothing from where ix runs.** On `claude-sdk` it was built with no
  working directory and no `setting_sources`, so the Claude Code CLI ran in ix's own
  directory with its default sources, and a CLAUDE.md, a hook or a user's settings there
  could shape its scores. It now runs in a fresh temporary directory, removed after, with
  `setting_sources: []` unless its `runtime_options` name some.

### Fixed: reading a run, its root, its placeholders

- **The "files are data" test can fail.** It used a YAML tag that an unsafe loader cannot
  construct either, so it would have passed over one. It now uses a tag that creates a file
  under an unsafe loader (and checks that it does), for a genre file, a kind file and the
  experiment file. SECURITY.md cites only these.
- **The telemetry conventions are named in the tracer only.** The run path wrote the GenAI
  conventions' `gen_ai.evaluation.*` names and `error.type` itself; it now says
  `Span.evaluated` and `Span.failed`, and `otel_tracing.py` names them. The spans are
  unchanged. A third-party `Traced` scope's spans implement the two methods in place of
  `event`.
- **Bringing the environment up is traced.** The environment component ran with matrix's
  plain context, so bring-up had no span of ix's. It is now `ix.environment`, under
  `ix.run`, with the environment's kind and the services it bound.
- **The run store is the composition root's choice.** The adapter that runs an experiment
  built matrix's `JsonlConstructStore` itself. The root now builds the store at matrix's
  `store` point (`Root(run_store=...)`, `jsonl` by default, whose config takes the `root`
  directory) and hands it in, for running, reporting and listing alike.
- **A report never calls a model.** `ix report` of a run that ended before its sensors read
  its interactions derived the readings then, so each report of a run with a `judge` asked
  the judge again: paid calls from a read command, and a stochastic judge's readings
  differing between two reports of one run. `ix run`'s own report of a failed run did the
  same. Readings are still derived at report time with sensors that call no model; a run
  that used one is reported with `readings: none` and `unread` naming the sensor and the
  fix (run the experiment again).
- **A lab's own kinds reach the run.** The run built its adapters with the installed kinds'
  declarations only, so a kind declared in `<lab>/kinds/` validated against its declaration
  and then ran without it: a sensor's `path` option reached its adapter as written, not
  resolved against the experiment's directory. The run, the preflight and a report now use
  the kinds the experiment was validated against.
- **The SDK a runtime drives is recorded with its version.** Each runtime in the manifest's
  `provenance.runtimes` now holds `drives`: each distribution its registration says it
  drives, with its installed version (`claude-agent-sdk` for `claude-sdk`, `hardline` for
  `hardline`). ix asks through its `Runtimes` port (`drives`), so a third party's runtime
  records its SDK the same way. Each session row of an agent session, a model call or a
  judge names the same distributions (`drives`).
- **A run records where each setting came from.** The manifest's `provenance.settings`
  holds each setting the run read (`ix.runs`, `matrix.observers`, `matrix.models`) with the
  config file that set it, `default`, or `given in code`; never the value. Before, only
  `matrix config --tool ix --sources` could say, and only for the config as it is now.
- **A process subject's program is part of what was tested.** Its `command` named the
  program and script by path, and nothing digested them, so a registered run of an edited
  server was still gated. A new option type, `command` (a non-empty list of text, with
  `within` naming the option its relative paths are relative to), digests each item that
  names a file: `process`'s `command` (relative to `cwd`) and `agent-session`'s `setup`
  (relative to the experiment) are now `command`s. An edit is a deviation (`file server.py
  changed after registration`). A program found on PATH and an argument that names no file
  are not digested, and `command: []` is refused at validate (it used to fail at preflight).
- **Factors drive an agent's settings.** A `characterize` sweep over a subject's settings
  (the loop's `rounds`, `max_turns`, a runtime option) ran wrongly or not at all:
  - a level reached the option as text (`"0.5"`, `"2"`); a value that is one placeholder
    now takes the level with its type;
  - a subject with a placeholder was neither reviewed nor preflighted; each cell's options
    now are, as its trials will have them, so `rounds: "{rounds}"` starts and a runtime
    option the runtime does not take is still `option_unknown`;
  - a placeholder in a subject's options, or in a probe file, was never checked, so
    `{sise}` ran as the text `{sise}`; both are now `unbound_placeholder` unless bound;
  - a probe's facet placeholder (`{pid}`) was refused; a facet every subject's kind offers
    now binds, and a kind option declares the placeholders its adapter fills itself
    (`binds`: a loop's `{prompt}` and `{response}`);
  - there was no way to write a literal `{name}`: `{{name}}` is that text.
  - A loop's `rounds` may be 1: the first answer alone, so a sweep can start from no loop.
  A probe file whose prompt holds a `{name}` meant as text is now refused until it is
  written `{{name}}`.
- **A registration is written whole or not at all.** It was written in place, so a crash
  mid-write left a file the next read refused as not JSON. It is now written beside the
  record and moved over it.
- **A reworded note leaves the plan alone.** An `x-` note beside a subject's or a probe's
  options was inside the plan's digest, so rewording one moved the plan and made a
  registered experiment deviate. Notes are now outside the plan and the probe set digests,
  as the question and descriptions are; they stay in the experiment's own digest.
- **A margin or threshold removed or added is named.** Design values were compared only on
  paths both records held, so a margin that became none (`margin.value` against
  `margin`) was no deviation, and only the digests said the plan changed. It is now
  `design.margin changed after registration`, with both values.
- **An address whose port is not one is refused at validate.** A `process` subject's `url`
  or `ready` with a port out of range raised out of the readiness check, so every trial
  became a harness `error`. The subject now reviews them (`option_invalid`).
- **A failure inside a session is told by its source.** Every `ValueError` in a session's
  rounds, or a model call, read "the runtime could not be built", including a runtime's
  own check of its reply. Only building and binding the runtime is that (`runtime`); any
  other error is the harness's `error`, named by its type.
- **`ix run`'s progress bar stops however the run ends.** Only an ix error stopped it, so a
  defect or a Ctrl-C outside the run printed over a live display.
- **`ix runs` with no path lists what its help says.** It read only `./.ix/runs`, so at a
  repository's root after `ix run lab/static` it said there were no runs. It now lists every
  experiment's runs under the current directory (each `.ix/runs` below it; hidden folders
  and `node_modules` are not entered), or those under the settings' `runs`.
- **Each run is read once.** Listing runs read every record in full to summarize it and
  then again to report it, and `ix plan` and `ix report <experiment>` did the same to find
  an experiment's runs. Each record is now read once, latest first where only the latest
  is wanted.
- **What a caller may rely on is listed.** README names the run layout and table names,
  the events and their fields, the span names and the exit kinds as contract, each pinned
  by a test, and span attributes, the conventions' names and the human text as not.

### Checks

- `layers.json` maps every module to a layer and runs on every test run: a new module no layer
  claims fails the suite, and each rule of the map has a planted violation that must be found.
- The registry scan finds a registry call whatever its receiver is called, with the verbs read
  from matrix's `Registry`.
- docker is named by the compose environment alone, and matrix's process groups are reached by
  the launcher alone.

## 0.5.0 — 2026-10-19

ix runs agent evaluations as well as service comparisons, and the statistics behind a
verdict are replaced: they were wrong at the trial counts ix runs.

Simulations calling ix's own analysis (`tests/test_calibration.py`) showed a nominal 95%
interval covering 84% of the time at 5 trials, `evaluate` falsely holding 8.6% of the time
against a stated 2.5%, an interval of zero width for any number of error-free trials, and a
noise-floor widening that let two identical builds pass a non-inferiority gate 4.5% of the
time. The choices, their sources and the simulations are in
`docs/design/2026-10-19-record.md`.

### Changed (breaking)

- **Intervals.** A mean gets Student's t interval (of the per-block differences in a
  comparison; Fieller's interval for a relative one); a median the order-statistic
  interval, which needs 6 blocks; a boolean a Wilson score interval; a difference of rates
  Newcombe's. The percentile bootstrap, its 5000 resamples, its seed and its resolution floor
  are gone, and with them `Decision.at_floor` and the bound of 125 decisions.
- **Counts.** A new metric shape, `count`: events out of units, kept as the two counts and
  pooled over trials with the Wilson interval at Korn and Graubard's effective sample size,
  the trial as the cluster. `error_rate` (sensor `timing`) and `tests_pass_share` (sensor
  `function-test`) are counts; `http-load` leaves `sent` and `failed`. Zero failures never
  give an interval of zero width.
- **Multiplicity.** `holds` is read at the full level (the verdict needs every decision, an
  intersection-union test); `fails` is corrected by Holm's procedure across the family.
  `Analysis.family` counts the fails side.
- **A/A calibration is a check.** The noise floor no longer widens intervals. The control is
  compared with its second sample at 1% shared out over the run's checks; one that differs,
  or a check that cannot be made, makes the run `inconclusive` with `Analysis.reason`.
  `Analysis.noise_floors` is `Analysis.calibration` (`CalibrationCheck`), the report section
  `noise_floor` is `calibration`, and `Decision.noise_floor` is gone; `Decision.method`
  names the interval method.
- **What can decide.** `min` and `max` never decide (`estimator_illegal`; a genre with a
  verdict cannot default to one). A rate's margin is in points or a share, never percent
  (`unit_mismatch`). A median decision with fewer than 6 blocks is `repeats_below_min`.
  Every trial reading the same number decides nothing.
- **Repeats.** `compare` and `regress` need 5 and default to 10 (were 3 and 5); `evaluate`
  needs 5 and defaults to 10 (were 1 and 3); `characterize` needs 3 and defaults to 5.
  The examples follow: `catalog-routing` sets 12 repeats (36 trials, the fewest of its three
  probes that can show a pass rate above 0.9), the lab's routing experiment 5, and the
  delay server's `slower` candidate waits 60 ms against 40 (was 44) so its verdict does not
  depend on a quiet machine. `proxy-streaming`'s memory guardrail is the mean, within 5%.
- **A run of a changed experiment is ungated.** A registered experiment that changed since
  registration runs as an exploratory one does: its report gives an indication, not a
  verdict, and it exits 14 (`deviations`), never 0, until the registration is amended with
  a reason. `RunManifest.exploratory` and `Report.exploratory` say when a run was asked to
  be exploratory.
- **Exit codes** follow `components/capabilities/exit-codes.json`: `conflict` is 7 (was 3),
  `deviations` 14 (was 9), `unregistered` 15 (was 7); `run_failed` stays 8, now shared.
- **The error envelope** carries `complete`, false when checking stopped at the file's
  shape. Problem kinds use the shared `name_unknown` (was `genre_unknown`, `kind_unknown`,
  `control_unknown`, `genre_extends_unknown`) and `name_taken` (was `genre_duplicate`,
  `kind_duplicate`, `probe_duplicate_id`).
- **An agent session and a judge name their runtime.** `runtime` is a required option of
  `agent-session` (it defaulted to `claude-sdk`) and of the `judge` sensor (it defaulted to
  `model`), with no default: twice an experiment or a test fixture that named none started
  paid Claude sessions. Left out, `ix validate` reports `option_missing` with a fix listing
  `claude-sdk` (spends), `model` (spends) and `mock` (spends nothing). Every example, the
  lab's experiment, the skill's references and the how-to name one. A kind
  option may declare `choices`: an open set of values, each with what it means, that the
  fix for a missing option lists (`allowed` stays the closed set).
- **Names.** The ports drop `Kind`: `Subject`, `Probe`, `Environment` (was `SubjectKind`,
  `ProbeKind`, `EnvironmentProvider`); `Sensor` stays. An experiment's records are configs:
  `SubjectConfig`, `ProbeConfig`, `EnvironmentConfig`, `SensorConfig` (was `Subject`,
  `Probe`, `Environment`, `SensorSelection`).

### Added

- **`Plan.sensitivity`** (`Sensitivity:` in `ix plan`): the true difference a run of this
  size shows 4 times in 5, from the spread of the latest finished run of the experiment, or
  `unknown` with how to learn it; for a pass rate held to a threshold, whether even every
  trial passing could show it.
- `Plan.warnings`: what `ix validate` warns of, in the plan document.
- `-` reads an experiment from standard input wherever a file is read.
- `ix.domain` exports every type reachable from a public result and every port, and is the
  one public path to them.
- Output off a terminal is not wrapped, so a path or a command can be copied.
- `pyproject.toml` names `README.md` as the package's readme.

### Added: agent evaluation

- **Agents run again.** `agent-session` (a fresh session per trial, in a temporary working
  directory seeded from `fixtures`, `skills` and a `setup` command, on matrix's `claude-sdk`,
  `model` or `mock` runtime), `prompt`, and the sensors `activation`, `tool-usage` (new),
  `function-test`, `outcome` (new), `usage` and `judge` have adapters. A subject's `loop`
  runs self-review or test-feedback rounds. Extras: `ix[claude]`, `ix[models]`, `ix[otel]`.
- **Reports** carry `simulated` (a dry run on the mock runtime gives no verdict), `halt`,
  `spend`, `flags`, `confusion` (expected against observed, with precision and recall) and
  `screen` (probes at floor, ceiling, noisy or failed). Readings carry `detail`.
- **Spend.** A run stops starting trials once the next could pass its ceiling (exit 12);
  `ix plan` estimates spend from what an earlier run's trials cost, or from the declared
  `per_trial` before any run has.
- **Settings**: the `ix:` section of matrix's config tiers (`runs`, `spend_cap`).
- **Telemetry**: spans `ix.plan`, `ix.run`, `ix.trial`, `ix.sensor` under scope `ix`; the
  command installs the SDK from `OTEL_*` and carries its warnings in every `--json`
  document. `ix.configure_telemetry()`.
- **Breaking.** Probes are written inline or are the files of the `probes/` folder beside
  the experiment: `probes: {source: ...}`, the `ix.probe-source` point, `ProbeSource`,
  `ProbeSourceError` and the `probe_source` problem kind are gone. Experiments, probes,
  genres and kinds are read through matrix's loader (`python-frontmatter` is no longer a
  dependency), so a file's problems carry matrix's kinds and its source. A value of the
  wrong type is `wrong_shape`. `Sensor.sense` may return `Sensed`.

### Added: what an agent driving ix from `--skill` alone needed

- **`ix run <path> --dry-run`** (`ix.run(..., dry_run=True)`) runs the experiment as written
  on stand-ins: each agent session on matrix's `mock` runtime, replying with its probe's
  `mock_response`; the options only `claude-sdk` takes set aside; a judge scoring every
  reply 0.5. It is exploratory, spends nothing and is held to no registration. Its report
  says `Dry run on mock replies: checks the harness, not the agent` and gives neither a
  verdict nor an indication; `Report.dry_run` and `Report.stood_in` (what was set aside)
  record it, as do `RunManifest.dry_run` and `stood_in`. A subject with no stand-in (a
  `process`) is refused. Before, a dry run needed an edited copy of the experiment.
- **The `text-match` sensor**: `text_matched`, plain checks of the reply (`starts_with`,
  `ends_with`, `equals`, `contains`, `excludes`, `matches` a regular expression; options
  `ignore_case`, `strip`), from a probe's truth or the sensor's options. A rule such as
  "the reply starts with BANANA" no longer needs a graders module.
- **`ix runs` says what each run found**: `RunSummary.mode` (`gated`, `exploratory` or
  `dry`), `verdict`, `indication`, `spent` and `ended_at`, in the text and in `--json`.
- **`ix plan` prices a run from what a trial cost** once a run has measured it, in place of
  the declared `per_trial`, and says which it used (`SpendEstimate.source`: `measured` or
  `declared`).
- **The margin says whose it is.** `ix plan` prints `Margin:` with where it was set and what
  it means, and the design's provenance carries it.
- Two optional ports, both in `ix.domain`: `Review` (an adapter tells `validate` what only
  it knows; `Surroundings` is what it is shown) and `StandIn` (`StoodIn`), which a dry run
  uses. The `agent-session` subject, the `prompt` probe and the `judge` and `text-match`
  sensors implement them.
- The skill's "When to use ix" maps the person's question to the genre ("is B slower than
  A?" is `compare` with `superiority`; "is B no worse?" is `regress`), and says the margin
  is the person's. Its `agents` reference shows a complete skill evaluation (the folder,
  `SKILL.md` and its `name` and `description`, the `Skill` tool, the settings ix has the
  session read), `text-match`, what validate checks, and the dry run and pilot in order.

### Added: what a second agent driving ix from `--skill` alone needed

- **A run says its exit code and the next step.** After the report, `ix run` and `ix report`
  print on stderr the code, what it means and what to do next (`EXIT_NEXT`), with the
  experiment's path and the run's directory filled in; `Report.exit` (`code`, `kind`,
  `meaning`, `next`) carries the same in `--json`, and `Report.exit_kind` names the kind.
  The agent had learned a run's exit code only from `ix report`'s.
- **`Report.shows`** (`Shows:`): what the evidence shows, in plain words and in the
  direction it points, one sentence per primary decision with an interval:
  `build30's latency p50 is higher than build20's by 29.6% [26.2%, 33.1%]; lower is
  better, so build30 is worse`, or, against a threshold, which side of it the interval
  lies on. The verdict is unchanged; before, a `fails` on "build30 is better than build20"
  never said build30 was slower. A run a stand-in answered shows nothing.
- **`ix run --repeats N`** (`ix.run(..., repeats=N)`) pilots with N repeats in place of the
  file's, never below the genre's minimum, with `--exploratory` or `--dry-run` only; a run
  that gives a verdict refuses it (exit 2: it is held to the registered design). The
  report lists the change (`Report.overridden`, `RunManifest.overridden`, `Changed:`). The
  agent had edited the registered file to pilot.
- **`Report.best_case`** (`Best case:`): a run too small for even every trial passing to
  show a pass rate's threshold says so at the size it ran, as `ix plan` does at the file's.
  The agent's plan was at the file's 10 repeats, where 40 trials can show `>= 0.9`, so it
  said nothing; its pilot at 5 repeats (20 trials, best case `>= 0.839`) came out
  inconclusive with nothing saying why. The plan's check fires at 5 repeats, as tested.
- **A runtime warning is said once per run.** The warnings of the runtimes a run's subjects
  build (claude-sdk's permissive `permission_mode`) are taken once, before the run, at
  `subjects.<name>.<option>`: in `Report.warnings` (`RunManifest.warnings`) and
  `Plan.warnings`, and once on stderr without `--json`. What the runtime logs while ix
  builds it is not let through; before, it was printed once per trial, 20 times in a pilot.
- **An option written as truth is said to be one.** A probe's truth key that is an option
  of its kind (`mock_response` beside the truth in a probe file) is `truth_unclaimed` with
  a fix that shows it under `options:`; before, the fix said to correct its spelling.
- The probe screen is left out of a report a stand-in answered: canned replies read every
  probe at its floor or ceiling, which said nothing.
- The skill's `agents` reference shows a probe file with `options:`, the pilot with
  `--repeats`, and how to show that a skill made the difference: `activated_correctly`
  beside `text_matched`, and `compare` against a control with no skill. `EXIT_NEXT["ok"]`
  says what follows a run with no verdict.

### Changed: wording

- The non-inferiority sentence reads `<variant> is at most <margin> worse than <control> on
  <metric>` (was `is no worse than <control> by more than <margin>`), and a margin in the
  metric's own unit names it (`at most 3 ms worse`). Guardrails and `Decision.claim` read
  the same way.
- A report a stand-in answered gives no `indication`: what it found is about the harness.

### Packaging

- ix is installed from the repository at the release's tag:
  `uv tool install "git+https://github.com/mox-labs/gnx@ix-v0.5.0#subdirectory=components/capabilities/ix"`.
  The README and `ix --skill` name the tag, and a test fails when the version and the tag
  differ.
- The dependency on matrix is bounded to the minor ix is built against (`matrix>=0.6,<0.7`);
  matrix comes from the same repository through the workspace's `[tool.uv.sources]`, from a
  checkout and from a git URL alike.
- The package carries a copy of the repository's `LICENSE`, its classifiers and its project
  URLs. The `dev` extra adds `pytest-asyncio`.

### Fixed

- **A harness that could not see the agent gave a confident verdict.** A session given
  `skills` and `tools: []` ran with no `Skill` tool, so the skill could never fire, and the
  registered run said `fails`, 0 of 18, with no warning. `ix validate` now refuses a
  session its sensors cannot observe (`unobservable`): skills or `activation` with a
  `tools` list (or a `definition`'s) that leaves out the `Skill` tool, `activation` or
  `tool-usage` on the `model` runtime, a `tool-usage` tool the session may not call, an
  `activation` session with no skill, plugin or settings source to fire from, and files to
  `collect` with no tool to write them or in `plan` mode.
- **`validate` missed options of another runtime.** `skills`, `plugins`,
  `permission_mode` and `setting_sources` on a subject that does not run on `claude-sdk`
  were found only by `plan` and `run`, with a generic fix. `validate` reports each
  (`option_inapplicable`), naming the options to remove and the runtime that takes them.
- An `agent-session` subject whose `plugins` pattern (`plugins/*`) matched nothing ran its
  sessions without the plugins the experiment meant to measure. `ix plan` now lists it
  under `Runnable: no`, and `ix run` refuses it before anything starts.
- `Sensitivity:` in `ix plan` read the spread of the latest run even when a stand-in
  answered its trials (a dry run), or when every trial read the same number, and stated a
  detectable difference from it (0, for a run that never varied). Such a run is passed over.

### Documentation

- The README says what ix is for, the experiment model, and the sequence from `validate` to
  `report`, with a service comparison and an agent evaluation. A how-to,
  `docs/how-to/evaluate-an-agent.md`, takes an agent evaluation from the question to the
  verdict. SECURITY.md covers agent sessions, the code `function-test` and `outcome` run,
  and spend.
- The `catalog-routing` example loads the plugins this repository ships, from a checkout,
  and probes the skills they hold.

## 0.4.0 — 2026-10-13

ix runs an experiment. One run is one matrix run: three components wired by kind carry out the trials, matrix records them as they finish, and the analysis and the report are pure functions over that record.

### Added

- **`ix run <path> [--exploratory] [--json]`** carries out an experiment one trial at a time, in the order the plan fixed, and prints its report. It refuses an invalid experiment, a kind with no adapter, and (exit 7) an unregistered experiment. `--exploratory` runs an unregistered one and gives no verdict. A changed experiment runs, and its deviations are printed in the report.
- **`ix report <run>`** makes a run's report again from its record: `<run>` is a run directory, or an experiment, meaning its latest run. It works on a run in any state, one killed outright included.
- **`ix runs [path]`** lists the runs kept beside an experiment.
- **The record**: `<experiment directory>/.ix/runs/<experiment>-<UTC time>-<4 hex>/`, matrix's JSONL store, one file per kind. New kinds: `ix.v1.run` (the manifest: experiment, design, the registration held at run time, deviations), `ix.v1.trial` (the schedule), `ix.v1.bindings`, `ix.v1.interaction` (one row per trial, on disk when the trial finishes) and `ix.v1.reading`. ix keeps no file of its own.
- **Adapters** behind kinds that were only declared: subjects `process` (own process group, readiness check, the group killed after each trial) and `http`; the probe kind `http-load`, driven by k6 (closed and open load models, warm-up, body verification); sensors `timing` and `memory` (peak resident memory of the subject's process group, sampled with `ps` while the probe runs); environments `none` and `process`.
- **A verdict**: `holds`, `fails` or `inconclusive`, one finding per variant per cell and an overall verdict that holds only if every finding does. Intervals are a percentile bootstrap over blocks (seeded, so a report is reproducible) or a Wilson/Newcombe score interval for a rate; every comparison interval is widened by the A/A noise floor; decisions are corrected together by Holm's procedure; a model-judged metric never decides. **The method is provisional** and every report says so.
- **Failures with an owner**: `subject`, `harness` or `sensor`. A trial the subject failed counts against it; one the harness failed is missing data. A subject found dead when it is released is the cause of whatever the harness reported during the probe. Neither stops a run. A scalar reading that is NaN or infinite is a sensor failure (`not_finite`), never a value.
- **Exit codes** 7 (unregistered), 8 (the run failed), 10 (verdict fails), 11 (inconclusive), 12 (`run_interrupted`: a run that was stopped, with its report), 13 (`ix report` of a run that is still running), 130 (`interrupted`: any other command cut short by Ctrl-C, with the `ix.v1.error` envelope, as every capability CLI does). Ctrl-C and SIGTERM stop the trial in flight through matrix's stop path, kill what the run started, and leave a partial run that `ix report` reads.
- **What cannot be run says so early.** `ix validate` warns `kind_unimplemented` (and `analysis_unimplemented`) for a kind or analysis this ix has no code for; `ix plan` prints `Runnable: no`; `ix kinds` and `--skill` mark each kind; `Kinds.runnable` lists the type URLs with an adapter. `agent-session`, `prompt`, `inspect-task`, `activation`, `function-test`, `usage`, `judge` and `compose` are declared only.
- **`ix --skill` is a skill**: frontmatter (`name`, `description`, `metadata.version`) makes
  the printed text a `SKILL.md`. It holds what ix is for and when not to use it, install and
  prerequisites, a quick start that runs where `python3` and k6 are, the commands in order,
  and the errors and exit codes with what to do next. The genres, the kinds, the file's keys,
  running and the problem kinds moved to references: `ix --skill -r NAME`, where an unknown
  NAME is a usage error (exit 2). `ix.skill(reference)` is the same, and `ix.EXIT_NEXT` is the
  exit table's "what to do next" column.
- **Library**: `ix.run`, `ix.report`, `ix.runs`, the documents `ix.Report` (`ix.v1.report`) and `ix.Runs` (`ix.v1.runs`), and `ix.UnregisteredError`.
- `Plan.schedule_digest`: the digest of the list of trials in order. `Plan.unimplemented`. `Plan.runnable` (in `ix.v1.plan` as `runnable`): false when a kind has no adapter or a subject cannot start on this host; `ix plan` still exits 0.
- The example `examples/delay-server`: a small HTTP server with a configurable delay and two experiments over it.
- **Adapters plug in through matrix's registry.** ix's `matrix.extensions` entry point declares its points (`ix.subject`, `ix.probe`, `ix.sensor`, `ix.environment`, `ix.probe-source`) and registers its three components and its adapters under their kinds' type URLs; the composition root builds what an experiment names from the registry. A third party's adapter for its own kind is an entry point and no edit to ix. `matrix catalog` lists them all.
- A decision reports `decided_at`, the smallest significance level at which it would be made (none when no level makes it), and `allowed`, the level Holm's procedure gave it. Each interval method resolves levels down to a floor of its own: a percentile bootstrap of 5000 resamples to 2/4999, a score interval to 1e-6. A level at that floor is a bound, and Holm's procedure reads the bound. The family Holm's procedure corrects is fixed by the design: a decision that missing data left uncomputable stays in it, undecided.
- `http-load`: `requests`, `throughput`, `latency_ms` and `ttfb_ms` count only requests the subject answered successfully; a subject that answered none fails the trial (`no_answer`), and so does a first response with no body under `verify_body`. No request sent is the harness's (`no_requests`).
- **The run refuses what it can tell before starting**: a subject command that is not there, and an `http` subject whose `auth` names no set variable. A subject kind tells it by also being a `Preflight` (`unstartable`); one that is not has nothing to check. Likewise an environment provider that is a `Liveness` (`lost`) is asked after each trial whether its services are still up. Both ports are optional, so an adapter need not answer what it cannot tell. `Plan.unstartable` lists the same subjects before registration, and `ix plan` prints them under `Runnable: no`.
- A run's events reach the observers matrix's config names for ix (`observers:` under `matrix:`, in any tier `load_config` reads for the tool `ix`).
- `Decision.at_floor`, in the report document too: true when `decided_at` is the floor of the decision's interval method, a bound rather than a level found.
- The `ix.v1.error` envelope carries `details`: the wrong value (`value`) and where it is (`path`) when there is one, and a matrix error's own details when ix carries one over. `IxError.details` holds the same.
- `ix -h` is `ix --help`. A failure ix did not classify prints the `ix.v1.error` envelope with kind `unknown` (a traceback too, without `--json`). A failing command prints `error (<kind>): <message>` and `fix: <fix>` on stderr, as `matrix` does. Between trials it asks the environment whether its services are still up; one that went down fails the run, and the trial in flight is the harness's (`environment_lost`). A deferred release that failed or was abandoned is in the report's errors.

### Changed (breaking)

- The subject kind `agent` is `agent-session`: `agent` is a reserved segment in the registry's type-URL grammar, so `ix.v1.subject.agent` could never be registered. A genre or kind whose name that grammar refuses is refused (`genre_invalid`, `kind_invalid`) with the grammar's reason; ix adds only the `<namespace>.v<N>.<family>.<name>` structure on top.
- Text output: the `Next:` hints, the count and list of a validation's warnings, the "incomplete" note and the table footnotes of `ix genres` and `ix kinds` are commentary and go to stderr; stdout holds the result alone.
- **ix depends on `matrix`.** Importing `ix` still loads no matrix; only `ix.composition` and `ix/adapters/*/matrix_*.py` import it, and a test holds that.
- ix depends on `httpx`, with which a started program's `ready` URL is asked whether it answers.
- **The plan digest covers the order of the trials**, so every plan digest differs from 0.3.0's and an experiment registered under 0.3.0 shows a `plan_digest` deviation.
- Subject kind `http`: the option `base_url` is `url`, so `{url}` in a probe means the same thing for every subject kind; it offers `headers` as well as `url`. `auth` is `env:NAME`, a bearer token.
- Sensor `memory`: the option `source` is gone; its metric is resident memory of the subject's process group.
- Probe kind `http-load`: `rate` is a rate option (`80/s`, `600/m`), required when `load_model` is `open`, and `ix validate` checks both; `concurrency` bounds the load tool's clients under an open model; `verify_body` means every body matches the first one fetched; the option `tool` is gone. Under an open model, every client is started before the load, so arrivals k6 drops are measured as the subject's (it was too slow for the rate). `throughput` counts only requests answered within the measured time: a request still in flight when it ends is cut off, not counted.
- A kind option may be `required_when` another option has a value.
- The error kind for a failure ix did not classify is `unknown`, as in matrix and recon; a usage error is a `UsageError` (kind `usage`). `ix.EXIT_CODES` maps every kind and outcome to its code, and `ix.EXIT_MEANINGS` says what each means.
- `ix validate` on an agent experiment now carries `kind_unimplemented` warnings. It is still valid and still exits 0.
- Exit code 0, 3 and 4 gained meanings (see the table); the new problem kinds are `kind_unimplemented` and `analysis_unimplemented`.
- The `compare` genre's analysis takes no parameters (`pairing` and `correction` are gone): decisions are always corrected by Holm's procedure. A genre that gives one of ix's analyses a parameter is `analysis_unimplemented`, and `ix run` refuses it.

### Not built

- Trials run one at a time. No spend ceiling is enforced during a run. No leftover-subject sweep after a run is killed outright (a later trial whose subject's address already answers is a harness failure, `address_in_use`, not a measurement). The sensors cannot be re-run over a stored run and written back.

## 0.3.0 — 2026-10-13

ix is now the experiment model and nothing else: one file that names its genre, and commands that check it, plan it and register it. The agent-eval harness is removed. **ix does not run an experiment in this release**; there is no `ix run`.

### Removed (breaking)

- **The agent-eval harness**: the commands `ix run`, `ix results`, `ix compare`, `ix experiment` and `ix lab`; the engines (native and Inspect AI), the sensors' implementations, the simulated runtime, the results store, the config tiers (`ix.yaml`, `~/.ix/config.yaml`), and its documents (`ix.v1.results`, `ix.v1.results-list`, `ix.v1.comparison`, `ix.v1.labs`, `ix.v1.init`).
- The dependencies on `matrix`, `hardline` and `claude-agent-sdk`, the `inspect` and `deepeval` extras, and the `matrix.extensions` entry point.
- The earlier `experiment.yaml` format (`sensors:`/`sensor:`, `trials:`, `engine:`, `subjects:` as a list). A file in that format is refused key by key like any other unknown key; nothing converts it.

### Added

- **The experiment file** (`ix.v1.experiment`): `name`, `question`, `rationale`, `genre`, `subjects` (name to `{kind, options}`), `control`, `probes` (a list, or `{source: directory, path}`), `factors`, `environment`, `sensors`, `metrics`, `primary`, `threshold`, `comparison`, `margin`, `guardrails`, `repeats`, `seed`, `spend`. Unknown keys are refused at the top of the file and beside every `kind`.
- **Genres as data** (`ix.v1.genre`): `evaluate`, `compare`, `characterize` and `regress` (which extends `compare`). A genre declares `roles`, the factor policy, `must_set`, `comparisons` (each with the sentence it claims), `design` (`repeats: {default, min}`, `order`, `calibration`, `estimator`, and a note on its standing), the analysis, `verdict_form`, the report sections and `guidance`. Extension is narrowing only, one level deep; a child cannot add or change an analysis parameter.
- **Kinds as data** (`ix.v1.kind`): subject kinds `process`, `http`, `agent`; probe kinds `prompt`, `http-load`, `inspect-task`; sensors `timing`, `memory`, `activation`, `function-test`, `usage`, `judge`; environment kinds `none`, `process`, `compose`. Each declares its options (types, required), effects, the facets it offers and needs, and for a sensor its metrics (with whether a model judges them) and the probe truth it reads. They are declarations: nothing here starts or measures anything.
- **Entry points `ix.genre` and `ix.kind`**: a package registers a package of YAML files; ix registers its own the same way. A lab keeps `genres/` and `kinds/` beside its experiment. A name belongs to the package that owns its namespace (`ix` owns `ix.*`), and another file declaring it is refused alone; two other files declaring one name are both refused.
- **`ix validate`**: every problem in one batch, each with `path`, `kind`, `message`, `fix`, `allowed` and `got`. It checks the experiment against its genre; every kind's options (`kind_unknown`, `option_unknown`, `option_missing`, `option_invalid`); that probe kinds fit subject kinds (`kind_unfit`); that every named metric has exactly one provider (`metric_unprovided`, `metric_ambiguous`, `metric_mismatch`); probe truth no sensor reads (`truth_unclaimed`); and spend (`spend_unbounded`, `spend_over_ceiling`). `complete: false` when the file did not load and the later checks did not run.
- **A model-judged metric never decides.** `judged` is declared by the sensor and is not a key of the experiment file. A judged metric is refused as `primary` and as a guardrail in any genre with a verdict (`judged_gate`).
- **Two reductions**: `statistic` reduces one trial's reading (`p99` of a histogram); `estimator` combines the trials (`mean`; `rate` for a boolean), defaulted by the genre.
- **`ix plan`**: the question, the hypothesis rendered from the genre (`Decides:`), the guardrails and the form of the verdict; then subjects and roles, the trial arithmetic, the estimated duration where probe kinds declare one, the order, the sensors, the effects (from the kinds, plus any the author added), the spend, the registration status, and every design value with its source. The digest is over meaning: stable against a comment, key order, a default written out, reworded prose and a field a later ix adds.
- **`ix register`**: writes `<file-stem>.registration.json` with the design as resolved (what the file sets and what the genre supplies) and the digests of the probe set, the experiment and the plan. A changed experiment is refused (exit 3). `--amend --reason "<why>"` replaces the record and keeps the superseded one, with the reason and the deviations accepted, inside it. A stored record tolerates fields this ix does not know.
- **`ix deviations`**: lists each frozen value that changed; exits 9 when any did.
- **`ix genres`, `ix kinds`, `ix --skill`**: the installed genres and kinds, and a document generated from them that teaches how to write an experiment, with a minimal example per built-in genre.
- **One error envelope** (`ix.v1.error`) for every failing command, usage errors included, always with `problems` and `deviations`. One exit-code table (`ix.EXIT_CODES`): 0, 1, 2 usage, 3 config, 4 not found, 9 deviations.
- **A declared library API** (`ix.__all__`): `load`, `validate`, `resolve`, `plan`, `register`, `deviations`, `genres`, `kinds`, `skill`, `error_document`, the documents they return, `Experiment`, `Genre`, `Kind`, the errors, `PROBLEM_KINDS` and `EXIT_CODES`. Each function takes a path or an `Experiment` built in memory.
- Examples: `examples/proxy-streaming` (a service comparison) and `examples/catalog-routing` (an agent evaluation with probes in files).
- Tests that scan imports: `ix.domain` and `ix.application` import only from an allowlist; only `ix.composition` imports an outbound adapter; the command line imports only `ix`; nothing imports a host.

### Changed

- Every document id is `ix.v1.*`.
- SECURITY.md describes what ix now reads and writes.

# Kinds

The subjects, probes, sensors and environments ix runs: how a kind is declared, what each built-in adapter does, what `ix validate` holds an experiment to, and how a package adds its own. `ix kinds` lists what is installed; `ix --skill -r kinds` shows each option's default.

A subject, a probe, a sensor and the environment each name a **kind**. A kind is declared as
data, the way a genre is: its options (with types, and which are required), the facets it
offers and needs, for a sensor the metrics it provides and whether a model judges them,
and for an environment the keys each service it brings up is bound with (`service_keys:
[host, port, url]`, named `{<service>.<key>}`). `ix kinds` lists them.

Every family has its own record, chosen by the family segment of the kind's `name`
(`acme.v1.sensor.x` is a sensor kind). All four declare `name`, `summary`, `options`,
`offers`, `needs` and `overlap`; a probe kind adds `duration` (the duration options that add
up to one trial), a sensor kind `metrics` (at least one), `truth` and `confusion`, an
environment kind `service_keys`. A key another family's record has is refused, naming the
family that declares it. In code, `ix.Kind` is the union of the four, and each record
(`SubjectKind`, `ProbeKind`, `SensorKind`, `EnvironmentKind`) is exported from `ix.domain`.

Every kind also says whether two trials may use it at once (`overlap: allowed`, or
`forbidden`, which a kind that says nothing is), and each metric whether what it reads moves
with whatever runs beside the trial (`contended: true`: a time, a throughput, a memory
peak). Of the built-ins, `agent-session`, `model-call`, `prompt`, `none` and every sensor
that reads what a trial stored allow overlap; `process` and `http` subjects, `http-load`,
`command`, `inspect-task` (an Inspect task may bring up sandboxes its samples share), `timing`,
`memory` and the `process` and `compose` environments forbid it.
Every metric of `timing` and `memory` is contended, and `usage`'s `duration` and the
evaluator's `eval_duration_passed`. `ix kinds` shows both: each kind's `overlap`, and each
metric's `judged` and `contended`.

| family | kinds |
|---|---|
| subject | `process`, `http`, `agent-session`, `model-call` |
| probe | `http-load`, `prompt`, `command`, `inspect-task` |
| sensor | `timing`, `memory`, `activation`, `tool-usage`, `function-test`, `outcome`, `text-match`, `exit-status`, `usage`, `judge`, `evaluator` |
| environment | `none`, `process`, `compose` |

An option a kind declares a `default` for has that value when an entry leaves it out (a
`prompt` probe's `timeout` is `600s`, a `process` subject's `startup_timeout` `30s`): the
declaration holds it, not the adapter, so `ix plan` lists it under the entry
(`probes.<id>.timeout`, from `kind`), a registration freezes it, and a later ix that changes
it shows a deviation (`design.defaults.probes.<id>.timeout`) rather than moving a
registered verdict unseen. An option whose value, left out, is its runtime's own (an agent
session's `model` and `max_turns`, a judge's `model`) is listed from `runtime`, naming the
runtime: ix cannot know that value. A mapping option may be declared key by key (an agent
session's `loop`: `rounds` 3, `until`, `test_timeout`, and a `prompt` for each strategy; each
service of a `process` environment: `cwd`, `startup_timeout` 30s), its defaults filled inside
what is written; and a default may follow another option (`http-load`'s `concurrency`: 1
client for a `closed` model, 100 for an `open` one). `ix kinds --json` and
`ix --skill -r kinds` show each default.

A declaration is not an adapter. ix can **run** these:

| family | kind | what the adapter does |
|---|---|---|
| subject | `process` | starts the command for each trial in its own process group, waits for `ready` to answer (or `url` to accept a connection), and kills the group after the trial |
| subject | `http` | starts nothing: an endpoint that is already up, with optional `headers` and a bearer token read from `auth: env:NAME` |
| subject | `agent-session` | a fresh session per trial on the matrix agent runtime its required `runtime` names (`claude-sdk` calls a model provider; `pydantic-ai` runs pydantic-ai's loop with every model call on the model runtime its `runtime_options.model_runtime` names, one of `matrix.model_runtimes`; `mock` calls no model; any agent runtime installed, by type URL), in a new temporary working directory seeded from `fixtures`, `skills` and a `setup` command, removed after the trial; `runtime_options` are the runtime's config, passed through as written; a `loop` asks again for a self-review or with the tests that failed |
| subject | `model-call` | one call per trial on the matrix model runtime its required `runtime` names (`matrix-hardline.v1.model-runtime` calls a model provider through hardline; `mock` calls no model; any model runtime installed, by type URL), with its `model` resolved before anything starts; no tools, turns or working directory, so `activation`, `tool-usage`, `collect` and a `function-test` `file` are refused on it (`unobservable`) |
| probe | `http-load` | runs k6 for `warmup` then `duration`, with `load_model: closed` (`concurrency` clients, 1 when left out) or `open` (an arrival `rate`, with `concurrency` clients started before the load, 100 when left out; an arrival when every one is waiting is dropped, and counted an error); only the measured part is read, and each request sent in it is waited for until it is answered or its `timeout` (60s when left out) runs out |
| probe | `prompt` | puts the prompt to the session or the call and keeps the reply; for a session also every tool call, each round's transcript and the files named in `collect` |
| probe | `command` | runs `command` once with the subject up, in `cwd` (the experiment's directory when left out) with `env` added, its `{url}` and every other placeholder filled as a probe's are, and keeps its exit code and the last 64 KiB of what it wrote to stdout and stderr; one that cannot start or runs past its `timeout` (60s when left out) is the harness's failure. The program and each file an argument names are digested as what was tested |
| sensor | `timing` | `latency`, `ttfb`, `throughput`, and `error_rate` as a count (failed requests and dropped arrivals, of those sent and dropped), from what the load probe recorded |
| sensor | `memory` | `memory_peak`: the highest resident memory of the subject's process group, sampled with `ps` every `interval` while the probe runs |
| sensor | `activation` | `activated_correctly`: the expected skill fired (a call of the tool the session's runtime says fires a skill, claude-sdk's `Skill`, or of the sensor's `tool`, naming it whole: `review` is fired by `review` or `plugin:review`, never by `code-review-lite`), or nothing fired for a decoy |
| sensor | `tool-usage` | `tool_used_correctly`, `tool_usage_score`: the agent used a tool with the expected command (the whole word, ignoring case: `dig` is not `digest`) and arguments, called by name or run through `Bash` |
| sensor | `function-test` | `tests_passed`, `tests_pass_share`, `tests_score`: the truth's test cases run against the code in the reply, or a file the agent wrote, in a child process |
| sensor | `outcome` | `outcome_score`, `outcome_passed`: the experiment's own grader functions, run in a child process; without one, the share of the truth's `expected_facts` the reply holds |
| sensor | `text-match` | `text_matched`: the reply starts or ends with, equals, contains, excludes or matches (a regular expression) what the probe's truth or the sensor's options say; no code of your own |
| sensor | `exit-status` | `exited_ok`: the command a probe ran exited with `code` (0 when left out) |
| sensor | `usage` | tokens, `cost`, `turns`, `tool_calls`, `duration`, `rounds`, and `hit_limit`, as the runtime reported them; what it did not report (a model call's cost or turns) is left out |
| sensor | `judge` | a model scores the reply against a rubric, by one call on the model runtime `model_runtime` names or a one-turn session on the agent runtime `agent_runtime` names; its metrics are reported and never decide |
| sensor | `evaluator` | one of pydantic-evals' evaluators, named by `evaluator` with its `arguments`, reads the trial: the reply, the probe's truth `expected_output` and `metadata`, how long the probe took, and the spans that ended in the trial (matrix's `execute_tool` spans, for `ToolCorrectness`, `TrajectoryMatch`, `MaxToolCalls`). Its assertions are `eval_passed` and its score `eval_score`, but `MaxDuration`'s is `eval_duration_passed`, a time and so contended; an evaluator that calls a model (`LLMJudge`, `GEval`) gives `judged_eval_passed` and `judged_eval_score`, on the model its `model_runtime` gives, never pydantic-evals' default. ix keeps the loop: `Dataset.evaluate` is not used |
| environment | `none`, `process` | nothing; or programs started before the trials and stopped when the run ends, each bound with where it listens (`{<service>.url}`, `.host`, `.port`), which a subject's or a probe's options name |
| environment | `compose` | a docker compose project brought up once for the run (`docker compose up -d --wait`, healthchecks included) and down after it (`down -v --remove-orphans`, deferred before `up`, so a stack that came up in part still comes down); each running service bound with its first published port (`{web.url}`) and the image id it runs, in the run's bindings row; a service found stopped or unhealthy after a trial fails the run. Without `project`, the project is named for the run, and a stack a killed run left is swept; a `project` written that already has containers is refused |

`inspect-task` is declared only. An experiment that uses one is still valid:
`ix validate` warns (`kind_unimplemented`), `ix plan` prints `Runnable: no` with the kinds,
and `ix run` refuses it before anything starts. `ix kinds` marks each kind. An adapter that
is registered but cannot be built here (a need nothing supplies) is treated the same way,
and `ix kinds` says why under `unbuildable`. An adapter whose review of its options raises
makes the experiment invalid (`adapter_failed`, naming the adapter and its error), and one
whose preflight raises is a requirement that does not hold, so `ix plan` prints
`Runnable: no` and `ix run` refuses it. A review and a preflight are asked each cell's
options as its trials will have them, with one difference: every escape is kept doubled
(`${{HOME}}` in a `setup` command, which a trial is given as `${HOME}`), so that a `{name}`
the adapter reads is always one a trial still fills (an environment's `{db.url}`), and an
adapter that skips its check on a placeholder still checks the rest.

An adapter plugs in through matrix's registry, as every extension does. A package declares
its kind as data at the `ix.kind` entry point, and registers the adapter from its
`matrix.extensions` entry point at ix's point for the family (`ix.extension.POINTS`:
`ix.subject`, `ix.probe`, `ix.sensor`, `ix.environment`) under the kind's type URL; the entry
point's name is that point (`"ix.sensor" = "acme.ix_ext:register"`), so only a session that
reads ix's points imports the package. ix
resolves every adapter it runs that way, its own included, so a new one needs no edit to ix.
Everything an adapter author writes against is in one public module, `ix.extension`, and
ix's own adapters import it as a third party's do. An adapter declares what it needs built,
each a keyword its factory takes, with the Protocol of what it is given:

| need | given | for |
|---|---|---|
| `launcher` (`LAUNCHER`) | a `Launcher` | starting processes, each in a group of its own that ends whole |
| `runtimes` (`RUNTIMES`) | `Runtimes` | running an agent or making a model call on matrix's runtimes, at its `agent-runtime` or `model-runtime` point |
| `sessions` (`SESSIONS`) | `Sessions` | sharing an agent session or a model call (a `Respondent`) between a subject and a probe, by the handle the subject offers as its `respondent` facet |
| `judge_models` (`JUDGE_MODELS`) | `JudgeModels` | a pydantic-ai `Model` that makes its calls through a matrix model runtime, as a pydantic-evals judge needs |

Each family's point holds what is registered there to its port (`ix.extension.PORTS`:
`Subject`, `Probe`, `Sensor`, `Environment`): a class that lacks one of its members is
refused at registration. What else an adapter can say is a separate optional Protocol, read
with `isinstance`: `Preflight`, `Review`, `StandIn`, `SpanReader`, `Liveness`, and for the
scope a run hands an environment, `Traced` and `Recorded` (with the `Attribution` it
yields). `JudgeModels` and `SpanReader` have one user each and are provisional.

```python
from ix.extension import LAUNCHER, POINTS


def register(registry):
    registry.register(POINTS["subject"], "acme.v1.subject.echo", EchoSubject, needs={LAUNCHER})
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
- `trials_in_flight` above 1 is refused (`trials_in_flight_refused`) where a kind in use
  or the genre forbids overlap, or a deciding metric is contended; the fix names
  each and says `trials_in_flight: 1`. A contended metric only reported is a warning
  (`trials_in_flight_contended`);
- a `{name}` placeholder must be bound to what a trial fills it with
  (`unbound_placeholder`). A subject's options are filled with the cell's levels, so a
  factor binds them: `characterize` over `factors: {rounds: [1, 2, 3]}` with `loop:
  {strategy: self-review, rounds: "{rounds}"}` runs each level. A value that is one
  placeholder takes the level with its type (`3`, not `"3"`), and each cell's options are
  reviewed as its trials will have them. A probe's options, inline or from a file, are
  filled with the levels, the subject's options and its facets: a factor, an option every
  subject sets, or a facet every subject's kind offers (`{pid}`). A loop's `prompt` fills
  `{prompt}`, `{response}`, `{until}` and `{failures}` itself. In prompt text, an option
  its kind declares `text: true` (a probe's `prompt`, a `system_prompt`, a `loop`, a
  judge's `rubric`), a name nothing fills is sent as written and is a warning, not a
  problem, so a prompt can hold code (`print(f"{user}")`); the warning names the
  placeholder a typo is likely to be (`{sise}` for the factor `size`), and `validate`,
  `plan`, `register` and `run` each print it, and a run's record keeps it. Nothing fills a
  sensor's options, so every `{name}` in a rubric is such a warning. A name a probe takes
  from some subjects and not others is refused, written `{name}` or `{{name}}`, since the
  subjects would be put different prompts. In prompt text `{{name}}`, where `name` is
  filled there, is the text `{name}`, and every other brace is sent as written, so a
  prompt holding JSON (`{"a": {"b": 1}}`) or a template (`{{ user.name }}`, or `{{user}}`
  where nothing fills `user`) reaches the agent unchanged. So a template's own `{{model}}`,
  `{{runtime}}` or `{{workdir}}` is the text `{model}` wherever something fills that name
  there, an option every subject sets or a facet as much as a factor; write it with spaces,
  `{{ model }}`, which is never a placeholder, to send it as written whatever fills the
  name. A doubled name nothing fills that is near one that is filled (`{{sise}}` for the
  factor `size`) is warned of as a likely typo. In a subject's or a probe's other options
  `{{name}}` is always the text `{name}`: `setup: [sh, -c, 'echo ${{HOME}}']` runs `echo
  ${HOME}`. Nothing fills an environment's options or a sensor's, nor un-doubles an escape
  in them: `{name}` and `{{name}}` are sent as written, so a shell there takes its own
  `${HOME}`. Validate warns of a `{name}` there that a trial fills elsewhere (a
  `text-match`'s `contains: '{size}'` is compared as the text `{size}`, whatever the cell's
  level) and of every `{{name}}`;
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

## Genres and kinds from a package

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

# Changelog

All notable changes to matrix. Versions follow semver; before 1.0 a minor may break.

## Unreleased

### Removed (breaking)

- **Spend.** matrix cannot measure what a run costs: it sees only what each runtime reports,
  and the `model` runtime reports tokens, not dollars. So the spend ceiling and everything
  built on it are gone, and nothing replaces them:
  - `limits.spend_usd`: a config that still sets it is refused at that key, not run uncapped;
  - the `spend` error kind and `SpendError` (`EXITS_AS` and `RunError.kind` no longer name it;
    spend had no exit code of its own);
  - the plan's `spend` record (`cost_usd_max`, `ceiling_usd`, `note`);
  - `spend_usd` on `Run`, in `run.json`, on `RunSummary` and in `matrix runs`;
  - the optional `budget_usd` runtime member, and the `claude-sdk` runtime's property of that
    name.
- What stays: each session's tokens and the cost a runtime reported (`AgentResponse.cost_usd`,
  the `matrix.agent.cost_usd` metric), as facts of that session. The `claude-sdk` runtime's
  `max_budget_usd` stays as the SDK's own option, passed as it stands; its stop is still
  recorded (`stop: max_budget_usd`).

### Changed (breaking)

- **Agent runtimes and model runtimes are two ports.** An agent session (a loop, tools,
  turns) and one model call had shared one port, one registry point and one reply, and the
  model runtime filled `num_turns=1` and `stop: completed` with constants. Now:
  - `AgentRuntime` (`check(definition)`, `run`) hosts sessions; `ModelRuntime`
    (`check(model)`, `call(ModelRequest) -> ModelCall`) makes one call.
  - The registry point `runtime` is two: `agent-runtime` and `model-runtime`, with type URLs
    `matrix.v1.agent-runtime.<name>` and `matrix.v1.model-runtime.<name>`.
    `runtime_type_url` and `RUNTIME_PREFIX` are `agent_runtime_type_url` and
    `model_runtime_type_url` (with `AGENT_RUNTIME_PREFIX`, `MODEL_RUNTIME_PREFIX`).
  - The config key `matrix.runtimes` is two: `matrix.agent_runtimes` (`claude-sdk`, `mock`)
    and `matrix.model_runtimes` (`hardline`, `mock`). A config that still writes `runtimes`
    is refused at that key. `agents.<name>.runtime` names an agent runtime; one that names a
    model runtime is refused, with the fix.
  - The `model` runtime is the `hardline` model runtime (`HardlineModelRuntime`, was
    `ModelAgentRuntime`). It runs no agents: a model call is a `matrix.v1.model-step`.
    `MockRuntime` is `MockAgentRuntime`, and `MockModelRuntime` is its model counterpart.
  - `Container.runtimes` and `Container.runtime(name)` are `agent_runtimes`,
    `agent_runtime(name)`, `model_runtimes` and `model_runtime(name)`.
  - `matrix.v1.check` has `agent_runtimes` and `model_runtimes` in place of `runtimes`;
    `matrix.v1.plan` has `agent_runtimes` and `model_runtimes` (each with `stand_in`) in
    place of `runtimes`, `model_calls_min` and `model_calls_max` beside the agent counts, and
    a component's `model` for a model step. `matrix check` and `matrix plan` print both.
  - A model call emits no `matrix.agent.*` events and no `invoke_agent` span: it is not an
    agent session. Its record is the step's `ModelCall` row.
  - Two steps that provide their reply as one kind are "steps" in the clash's message, agent
    and model steps alike.
- **What a `claude-sdk` session can be given is declared by its config, and checked.**
  `setting_sources` takes only `user`, `project` and `local`; each of `plugins` is
  `{type: local, path}`; `skills` (names, or `all`) is new, passed as the SDK's own option.
  An option outside the config fails at its key at composition. At binding, the runtime
  refuses a definition whose `tools` leave out `Skill` when it has `skills`, and any option
  the installed SDK's own dataclasses do not have, saying how to fix it.
- The agent-call counts move from the plan's `spend` record to the plan itself:
  `agent_calls_min`, `agent_calls_max`. `matrix plan` prints them as `agent calls: N to M`.
- **A component that returns after it was asked to stop ends `stopped`**, not `completed`.
  Another component's failure or the run's cancellation asks the running components to stop,
  and the protocol tells them to return what they have; that may be partial. Its rows are
  still recorded, with the stop's `reason` beside the status. `matrix run --from` (and
  `Container.reuse`) never reuses a `stopped` component, nor a `completed` ending that
  carries a stop's `reason`, which is how earlier records wrote one. A component that had
  already returned when the stop was requested is `completed`, with no reason. A consumer
  that reads component endings must tolerate the new state, as the vocabulary is open.

- **A model step fails on a reply that is not a whole answer**, where it would have
  provided it: a refusal, a reply cut at `max_tokens`, withheld by the provider, ended to ask
  for a tool or for a reason no one named, or empty. It raises `ModelRuntimeError`, reason
  `refused` (a refusal, `content_filter`) or `failed`, kind `component`, naming the
  `finish_reason` with a fix for it. A runtime that does not say why its reply ended
  (`finish_reason: null`) is not taken to have cut it.
- **A reply row keeps the reply; its session's facts move to the session table.** The
  `AgentResponse` an agent step provides and the `ModelCall` a model step provides keep the
  reply and what a consumer acts on (`content` or `text`, `transcript`, `tool_calls`, `stop`,
  `finish_reason`, `refusal`, `family`, the task or prompt as sent) and point at their row of
  `matrix.v1.session` (`session`); every fact that row holds is `null` on the reply row
  (`as_reply()`). What a runtime returns, and so what `BoundAgent.run` and a direct
  `ModelRuntime.call` give a caller, still carries every fact.
- `AgentResponse.tokens_input`, `tokens_output`, `duration_ms` and `num_turns` default to
  `None`, where they defaulted to 0: a constant is not a fact.
- Every run has a `matrix.v1.session` table, as it has a ledger: `Construct.kinds()`, the
  stores' `tables` and a run directory list it, with no rows when no session ran.
- The model step needs `model_runtime_entries` (each composed model runtime's registry entry),
  for its session rows.
- **The `claude-sdk` runtime's reply is the session's result.** `content` joined every text
  block of every turn and ignored the SDK's `ResultMessage.result`, so an agent that thought
  aloud and then answered in JSON broke its step's `output`. `content` is the result now (empty
  when the session stopped at a limit, which has none), and every text block is the new
  `transcript`.
- **`tokens_input` counts the prompt cache's tokens.** It was Anthropic's `input_tokens`,
  which leaves out the tokens read from and written to the cache: under Claude Code's caching,
  12 where the session read 90,000. It is their sum now, and `cache_read_tokens` and
  `cache_write_tokens` say how many of each.
- **`AgentResponse.model` is the model that answered**, as the SDK's `model_usage` names it
  (the one that wrote the final reply, when it names several), not the definition's `model`,
  which was the model asked for and `None` when the CLI's default answered.
- `ModelCall.usage`, `attempts`, `retries` and `latency_ms` default to `None`, where they
  defaulted to zeros and `attempts=1`: a constant is not a fact. The `mock` model runtime
  reports what is true of a canned reply: one attempt, no retries, zero tokens, its latency,
  and `finish_reason: stop`.

### Added

- **`matrix.v1.model-step`**: one model call as a component. It names a model runtime and a
  model, renders its `prompt` from its `inputs`, and provides a `ModelCall` (the prompt as
  sent, the reply, and the call's record, below) and, with `output`, the reply as a row of
  that kind: asked for as the kind's registered schema when there is one. New names:
  `ModelRuntime`, `ModelRequest`, `ModelCall`, `TokenUsage`, `ModelRuntimeError`,
  `ModelStep`, `ModelStepConfig`, `MODEL_STEP`, `MODEL_CALL`, `PROMPT`; the needs
  `model_runtimes` and `schemas`.
- **A `ModelCall` carries the call's whole record**, as hardline 0.2 reports it: why the reply
  ended (`finish_reason`: `stop`, `length`, `content_filter`, `refusal`, `tool_call`,
  `other`; with `native_finish_reason`, the provider's own value, and `refusal`, what the
  model said when it refused), the model that answered and `fallback_from`, usage with cache
  tokens summed over every attempt, `request_id`, `attempts`, `retries`, latency, `params`
  (what was sent: `max_tokens`, `temperature`, how a structure was asked for),
  `attempt_log` (every backend call, with why each failed), `turns_digest` and `row` (the
  registry row that answered: its digest and the tier that set each field). New names:
  `FinishReason`, `RequestParams`, `CallAttempt`, `RegistryRow`.
- **The session table, `matrix.v1.session`**: one row per agent session or model call a
  component runs inside a run, written through the Construct like any row, durably and in
  ledger order. A row holds the runtime's type URL and what it drives, the model that
  answered, how it stopped and why its reply ended, usage, the reported cost, the runtime's
  ids and the OpenTelemetry trace and span the session ended in (`Container.executor()` reads
  them; matrix mints none), fallback and attempts, and `sent`, a digest of what was sent. A
  `BoundAgent` and a model step record their own; a component that calls a model runtime
  itself records with `record_session(call_session(...))`. `LedgerEntry.sessions` names the
  session rows behind each row: those its component ran since its previous hand-over.
  `data-model.md` joins a board row to its sessions in DuckDB, and the tests run that query.
  New names: `Session`, `SESSION`, `digest`, `agent_session`, `call_session`,
  `record_session`; `BoundAgent(runtime_url=, drives=)`, `Executor(trace=)`.
- `AgentResponse` gains `transcript`, `cache_read_tokens`, `cache_write_tokens`,
  `finish_reason` and `native_finish_reason` (the SDK's `stop_reason`, read in the model
  call's vocabulary) and `session_id`, which the `claude-sdk` runtime fills.
- **`drives`** on a registry entry: the distributions it drives, by their installed names,
  declared at registration (`register(..., drives=("claude-agent-sdk",))`) and shown by
  `matrix catalog --json` and `matrix describe`. `claude-sdk` drives `claude-agent-sdk`, the
  `hardline` model runtime `hardline`. A tool that records the versions behind a run reads it,
  rather than keeping a table of runtime names. Optional: an entry that does not say drives
  nothing it declares.
- **`stand_in`** on a registry entry, declared at registration (`register(..., stand_in=True)`)
  and shown by `matrix catalog`, `matrix describe` and the plan's runtimes. Both mocks
  declare it, so a caller can tell canned answers from a model's without inferring it from
  `effects`.

### Fixed

- **The `claude-sdk` runtime closes the SDK's message stream** however reading it ends
  (`aclosing`), where closing it, and so the CLI process, was left to the garbage collector.
  Its error mapping is now tested: a missing CLI is `incapable`, a timeout `timeout`, and an
  SDK error after the result arrived is logged with the result kept.
- **A model step closes its runtime's connections.** hardline keeps one client per event loop
  and can close it only in that loop, and nothing called its `aclose()`: every run left its
  connections open, and the next loop to use the runtime logged that they had been. A model
  step now defers its runtime's `aclose()` (an optional `ModelRuntime` member, which the
  `hardline` runtime has) to the end of the run that made the calls, in that run's loop. The
  `models` extra now requires `hardline>=0.2,<0.3`.
- **A runtime that does not keep its port is refused, by name.** A third party's runtime that
  built something else made composition raise a bare `AttributeError` (the `unknown` defect,
  exit 1), and one with `check` but no `run` composed and failed only when an agent ran on it.
  The registry now holds whatever is registered at `agent-runtime` or `model-runtime` to that
  point's port: a class when it is registered, and what an entry builds when it is built.
  Either is a `config` error naming each member that is missing or not async. A package may
  give its own point a port: `add_point(point, port=...)`.
- **An unknown model is refused before anything runs.** It got through composition, `matrix
  check` and `matrix plan`, then failed at run time as `component` (exit 8). A model step now
  checks its model with its runtime when it is built: `config` (exit 3), naming the models
  the registry has.
- **The `claude-sdk` runtime's inline subagents work.** `agents` was forwarded as plain
  dicts, which the SDK converts with `dataclasses.asdict` once the CLI has started, so every
  session with a subagent failed. Each is now the SDK's `AgentDefinition`, with typed keys:
  `description`, `prompt`, `tools`, `disallowed_tools`, `model`, `skills`, `max_turns`.
- **A step's template is checked when the step is built.** A placeholder that
  names no input, an input the template never uses, a positional `{}` and unbalanced braces
  are each a `config` problem at `matrix check`, `matrix plan` and composition, with every
  other problem. Before, a typo composed, wired and planned, and failed only when the run
  reached the step, after its upstream work.
- **Reused rows reach downstream components in the type a fresh run gives them.** A row of a
  kind with a registered schema is now recorded as an instance of that schema, whether it was
  handed over as one, as a dict, or read back from a store by `--from`. A consumer written
  against a fresh run (`row.score`) used to fail only on resume, given a dict. The rows of
  such a kind in `Run.rows` and the memory store are models now, where a dict handed over
  used to stay a dict; on disk they are the schema's JSON, defaults included.
- **The JSONL store writes nested values as JSON.** A model inside a dict row, a set, a
  dataclass or a nested datetime was written as its `str()`, so jq, DuckDB, `load()` and reuse
  all read the wrong value. Rows are now written with pydantic's `to_jsonable_python`; a row
  holding anything it cannot write is refused with a `ContractError` naming where in the row
  it is, before its ledger entry or anything else is written, and the record goes on.
- **The reuse fingerprint covers every need a component is handed.** It held a component's
  type and config, and an agent step's definition and runtime config, so a run from a record
  reused old answers after `matrix.models` (or hardline's own tiers) pointed a name at another
  model or endpoint, after the working directory changed, or after the agents changed under a
  component that needs `agents`. It now digests each need: an agent step's agent and every
  agent for another component that needs them, each by definition, runtime config and needs,
  and what its runtime resolved (the `model` runtime's registry); `models`; `cwd` (the
  process's when none was given). Fingerprints change shape, so a record written before this
  release is not reused. Runtimes gain an optional `resolved` member for this.
- **A cancelled agent session ends `cancelled`.** `matrix.agent.end` carried no `status` when
  the session was cancelled, so its `invoke_agent` span ended without an error and the
  duration metric had no `error.type`. It now says `status: cancelled`, and the otel observer
  gives every ending that was cancelled (session, component, run) an `error.type`.
- **`Retry` honours `retry_after_s=0`.** An error's own hint of `0` ("retry now") was read as
  no hint, and the policy backed off instead (or took a different value from the error's
  details).
- **`process.start` keeps a pending cancellation.** Cancelled while a child was being started
  whose start then failed (no such program), the caller got the start's error in place of
  the cancellation.
- **The mock runtime reports no cost.** It reported `cost_usd: 0.0`. A reported cost is
  provenance, what a provider said, and a mock is not a provider, so it is now `None`.
- **A run's record no longer says `completed` when the executor raised.** A schema validator
  that raised something pydantic does not wrap (a `TypeError`) escaped the executor raw: the
  other components were left running, releases ran under them, and `run.json` said
  `completed`. Any error from checking or recording a component's rows is now a
  `ContractError` that fails that component, and whatever else escapes the run marks it
  `failed` before it reaches the caller.
- **After a store fails, `ctx.provide` raises every time.** A component still running
  after a failed append (under `isolate`, or within its grace) could hand over a row,
  have the call return normally, and end `completed`, though the row was only in memory.
  Every append after a failed write-through now raises `StoreError`.
- **A reused component whose rows are refused ends `failed` and is not run again.** Under
  `on_failure: isolate`, a component whose stored rows its kind's schema now refused was
  recorded as failed and then run again, and so were the reused components after it. It now
  ends `failed` (reason `from run <id>`, none of its rows recorded), a reused component that
  depends on it is skipped, and the other reused components are still reused.
- **An input row that cannot be copied is refused before the record opens**, as the other
  input checks are. It used to open the run and leave a `completed` record with no
  components.

### Security

- **No content leaves through OpenTelemetry by default.** The `otel` observer exported every
  event field as a span attribute, so each agent's task (`matrix.task`), error messages
  (`matrix.error`, and the span's status description) and every field a component passed to
  `ctx.span` went to whatever endpoint `OTEL_*` named. Span attributes now come from an
  allowlist: the GenAI conventions' names, matrix's statuses, reasons, error kinds, counts,
  times and ids, and a component's number and boolean span fields. `error.type` stays.
  Content is exported only with the observer's new `capture_content` option
  (`observers: [{type: otel, capture_content: true}]`), as the conventions ask. A
  component's string span fields are dropped unless it is set.

## 0.6.0

### Added

- **`matrix plan [CONFIG]` and `matrix run [CONFIG]`.** The command runs what a config
  declares. `plan` composes and wires, calling nothing, and prints `matrix.v1.plan`: the
  stages of work, each component's kinds, effects and attempts, the agents and runtimes in
  use, what a run could spend (agent calls; cost 0.0 when nothing calls a model, null when
  not knowable), where it would be recorded, every problem at once. `run` takes
  `--input KIND=PATH` (JSON lines, `-` for standard input), `--run-id`, and `--from RUN_ID`,
  which starts from a recorded run: its inputs, and the rows of every component that
  completed there unchanged (`reused`). It prints the run id and where the record is.
  `Container.run`, `Container.reuse`, `Container.location` and `plan_config` are the same
  from Python. Every command that reads config takes `CONFIG`, layered above the tiers, `-`
  for standard input.
- **One loading mechanism.** `matrix.load(model, location)` reads one YAML or markdown file,
  or a stream, as the caller's pydantic model; `matrix.load_each(model, *folders, key=,
  name_from_file=, suffixes=)` one model per file, a name defined twice refused;
  `load_config` merges the tiers through the same `Loader`. A markdown file is its
  frontmatter with its text under `body`; the type says what `body` means. A location may be
  a folder inside an installed package. Every problem is a `matrix.Problem` with its key path
  and its file, all at once. `BesideFile()` and `Accumulate()` mark fields whose relative
  paths read from beside their file and whose lists keep every tier's. A `DocumentSource`
  (`document`, `documents`, `beside`) puts any source behind the `Loader`.
- **The store from config.** `store` is a registry point beside `runtime`, `component`,
  `payload-type` and `observer`; the JSONL and memory stores are registered there
  (`matrix.v1.store.jsonl` with `root` and `fsync`, `matrix.v1.store.memory`), and
  `matrix.store: {type, ...options}` chooses one (default JSONL under `.matrix/runs`). A
  package adds a store by registering it.
- **`matrix.limits` in config**, and **retries**: `Limits.retry` and
  `ComponentLimits.retry` take a `Retry` (`attempts`, `backoff_s`, `multiplier`,
  `max_wait_s`, `errors`: `transient` or a reason). A component that failed transiently is run
  again after a backoff, or after the `Retry-After` the backend reported
  (`AgentRuntimeError.retry_after_s`, which the model runtime now passes on), capped; one that
  already handed rows over is not. `ComponentEnding.attempts` counts them, and each retry is a
  `matrix.component.retry` event.
- **Telemetry from the standard variables.** The OpenTelemetry API is a dependency; the
  `otel` extra is the SDK and both OTLP exporters. When `OTEL_EXPORTER_OTLP_ENDPOINT` (or
  `OTEL_TRACES_EXPORTER` / `OTEL_METRICS_EXPORTER`) asks for export, composition attaches the
  `otel` observer and `matrix run` installs the SDK (`configure_telemetry`, now returning
  `Telemetry` with `shutdown()`), honouring `OTEL_EXPORTER_OTLP_PROTOCOL`,
  `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES` and `OTEL_SDK_DISABLED`. As a library matrix
  never installs a provider. Asked for without the extra, every `--json` document carries an
  `extra_missing` warning. With nothing configured, a JSONL run keeps its events in its record,
  `events.jsonl` (`RunWriter.events()`, optional for a store).
- **The otel observer follows the GenAI semantic conventions** as read on 2026-10-19
  (`open-telemetry/semantic-conventions-genai`, status Development): `invoke_workflow` for a
  run, `invoke_agent {agent}` with `gen_ai.provider.name`, `gen_ai.request.model`,
  `gen_ai.response.model` and usage, `execute_tool {tool}` for each tool call the runtime
  timed (the Claude runtime times them now), spans timed by the events' own times; metrics
  `gen_ai.invoke_workflow.duration`, `gen_ai.invoke_agent.duration`,
  `gen_ai.invoke_agent.tool_calls`, `gen_ai.execute_tool.duration` and
  `gen_ai.client.inference.usage.*_tokens`. `OtelObserver` takes its own providers.
  `AgentResponse.provider` names who served a session.
- **Warnings inside the documents.** `matrix.v1.check`, `matrix.v1.agents`, `matrix.v1.plan`
  and `matrix.v1.run` carry `warnings`, problem records: a `claude-sdk` runtime with
  `bypassPermissions` or `dontAsk` (`permissive`), export asked for without its extra.
- `matrix --skill -r NAME` prints one of the skill's references: `config`, `agents`,
  `running`, `telemetry`, `loading`.
- `ComponentEnding`, `ComponentFailure`, `ReleaseOutcome`, `Retry`, `Reuse`, `StoreConfig`,
  `Plan`, `Telemetry`, `store_type_url` and `EXITS_AS` are exported. The README says which
  names are the stable public API.
- **A spend ceiling.** `matrix.limits.spend_usd` is the most a run's agent sessions may
  spend, as their runtimes report it. The executor keeps each run's spend, and an agent a
  component calls inside the run is held to it before its session starts: a session that
  could pass the ceiling is not started (`SpendError`, kind `spend`, exiting `run_failed`),
  and `Run.spend_usd` (`spend_usd` in `matrix.v1.run`) records what was spent. The
  `claude-sdk` runtime takes `max_budget_usd`, the Claude Agent SDK's own per-session limit,
  which it passes on and which the ceiling holds each session to (`budget_usd`, an optional
  runtime member). `matrix plan` states the bound: `spend.cost_usd_max` is every session's
  budget summed, or the ceiling when lower, and null, with the runtime to bound, while one
  that may call a model bounds no session; `spend.ceiling_usd` is the ceiling.
- **`matrix runs [CONFIG]...`** and `list_runs`: the runs the configured store keeps, oldest
  first, with status, times, location and spend (`Runs`, `matrix.v1.runs`). It builds only the
  store. `matrix run` prints each file of the record when it ends, and its `--json` document
  carries the record's `tables`.
- **`--only`**, on every command that reads config, reads the `CONFIG` files alone, none of
  the tiers; `CONFIG` may be given more than once, each layered above the last. The document
  functions take `config` as one location or several, and `only=`.
- **The task as sent is recorded**: an agent step's `AgentResponse` carries it
  (`AgentResponse.task`), and `matrix.agent.start` events carry it as `task`.
- The `mock` runtime replies by agent name (`by_agent`), after `responses`, which is keyed by
  the whole task as sent, and before `default`.
- `matrix --skill -r agents`: agent steps in full, the mock's replies, the models and budget
  each runtime takes. `matrix describe` shows the same for each runtime and the agent step in
  its config schema's `description`.

### Changed

- **Errors are the envelope the capability tools agree on**:
  `{kind, message, fix, complete, problems, details}`. Problems are records (`path`, `kind`,
  `message`, `fix`, `allowed`, `got`, `source`), not strings in `details["problems"]`, with
  the agreed kinds and matrix's own (`unprovided`, `cycle`, `extra_missing`, `permissive`).
  `fix` is never null. `complete` is false when a check stopped early.
- **Exit codes are the agreed table, one kind per code**: `run_failed` (8) for a run whose
  component raised (`component`) or broke its contract (`contract`), `store` (9), and
  `unknown` (1) only for a bug in matrix. `RunError.kind` is `run_failed`, or the cause's own
  kind when its remedy differs (`store`, `transient`, `auth`, `config`); `RunError.cause_kind`
  keeps the cause. An `AgentRuntimeError` whose reason is `refused`, `failed` or one matrix
  does not know is kind `component`, not `unknown`.
- **Composition reports every problem at once**: a bad runtime type no longer hides an agent
  naming a runtime that is not there. Each problem names its key path (`matrix.runtimes.m.type`)
  and the file that set it.
- `Run`, `ComponentFailure`, `ComponentEnding` and `ReleaseOutcome` are pydantic models;
  `Limits` and `ComponentLimits` too, so an invalid limit built in Python is pydantic's
  `ValidationError`. `ComponentStatus` adds `reused`.
- `AgentDefinition` reads a Claude Code agent file itself: `body` is its `system_prompt`,
  `maxTurns` its `max_turns`, a comma string its `tools`, other keys its `metadata`.
- `compose(..., definitions=...)` takes definitions the caller holds, and `loader=`.
- A missing extra's fix is the `uv` install from the repository at this release's tag
  (`uv add "matrix[EXTRA] @ git+...@matrix-v0.6.0#subdirectory=..."`, and the
  `uv tool install --with` form), never `pip install matrix`, which is another package. The
  README and the skill install the same way.
- `matrix --version` prints `matrix, version X`.
- The package carries its metadata for installing from the repository: readme, license
  file, classifiers, project URLs. The `models` extra is bounded to `hardline>=0.1,<0.2`.
- **Problems say how to fix them, where to fix them.** Two agent steps that both provide the
  default reply kind are one problem at the second's `config.response`, whose fix is to give
  each its own `response`. An agent defined in two tiers' `definitions` directories is one
  problem at `matrix.definitions` that says the directories accumulate and how to keep one
  config to itself (`--only`); the agents that would use it are left unchecked
  (`complete: false`) instead of reported as having no definition. A reply without the JSON
  an agent step's `output` asks for quotes the reply and says how to get JSON.
- `RunError.fix` is the cause's fix, so `matrix run` prints it. Without `--json`, a command's
  error lists each problem with its own `fix:` beneath it.
- **A permissive runtime is logged once per process**, not once per runtime: the
  `claude-sdk` runtime logs `bypassPermissions` or `dontAsk` at WARNING on the logger
  `matrix.runtime` (`RUNTIME_LOGGER`, exported) the first time a process builds one with
  that mode. ix builds a runtime per session, and a 20-trial pilot printed it 20 times. The
  `matrix` command quiets that logger while it runs, since every document's `warnings`
  already carries it (and, without `--json`, a `warning:` line on stderr); under `--json`
  stderr keeps to the error envelope.
- **The mock reports what it spent: nothing.** Each reply carries `cost_usd: 0.0`, so a run
  on it records `spend_usd: 0.0`, as `matrix plan`'s `$0.00` said; before, the record said
  `null`, the mark of a runtime that reports no cost. `null` is now that alone (the `model`
  runtime), and `matrix plan`'s `spend.note` says so for each such runtime.
- **`matrix runs` prints what each run spent** (`$0.0123`, or `-` when no session reported
  a cost), between the start and the location, as the skill said it listed `spend_usd`.
- The skill's `--only` example is a config run on its own
  (`matrix run live.yaml --only --input KIND=PATH`), and says the record still goes to the
  configured store, by default `.matrix/runs` in the working directory. Its Python example
  gives `plan_config`'s signature, `(tool="matrix", config=None, *, only=False)`: the first
  argument is the tool, and a path goes in `config`.

### Removed

- `ConfigSource` and `DefinitionSource`, and the YAML and markdown adapters behind them: one
  `DocumentSource` and the `Loader` replace both. `load_config(sources=...)` takes locations
  (paths, package files, streams); `compose(definition_sources=...)` is
  `compose(definitions=...)`.
- The metrics `matrix.run.duration` and `matrix.agent.tokens`, replaced by the conventions'
  `gen_ai.invoke_workflow.duration` and `gen_ai.client.inference.usage.*_tokens`.

## 0.5.0

### Added

- **`matrix.process.start(argv, **options)`** starts a child in its own process group and
  defers nothing. With `kill_group` it is the pair for a component that starts and stops a
  child for each repetition of its work: nothing accumulates in the run's releases, and the
  component defers one release of its own for whatever is open if it is abandoned. A caller
  cancelled while the child is being started has the child killed before the cancellation
  goes on.

- `matrix.EXIT_MEANINGS`: what each error kind means, and `matrix.EXIT_NEXT`: what to do
  next. `matrix --help` lists every exit code from them, and `matrix -h` is `matrix --help`.
- **`matrix --skill`** and `matrix.skill()`: the skill an agent reads, with frontmatter
  (`name`, `description`, `metadata.version`) so the printed text is a `SKILL.md`. It covers
  what matrix is for, the component protocol, writing and registering a component, the
  config tiers and keys, the commands, and the exit codes with what to do next. `EXIT_CODES` holds
  `ok` (0) too, and the command reads its success code from it.
- **The error kind `interrupted`** (exit 130): Ctrl-C during a `matrix` command prints the
  `matrix.v1.error` envelope with that kind, as every capability CLI does.
- **The documents the `matrix` command prints are library records**: `matrix.catalog`,
  `describe_extension`, `config_sources`, `config_view`, `check_config` and `list_agents`
  return `Catalog`, `Extensions`, `ConfigSources`, `ConfigView`, `CheckReport` and `Agents`,
  whose `document()` is the `--json` document. A catalog failure's fields are named
  (`extension`, `origin`, `error`).
- `Run.failures`, `Run.components` and `Run.releases` hold `ComponentFailure`,
  `ComponentEnding` and `ReleaseOutcome` records; `Run.document()` (`matrix.v1.run`) is
  unchanged.

### Fixed

- `process.kill_group` cancelled while waiting out its grace sends SIGKILL to the group
  before the cancellation goes on, so a child that ignores SIGTERM does not outlive a
  cancelled kill.
- `Registry.register_payload` without a `summary` takes the first line of the model's
  docstring, not the whole of it.
- A run whose writer was killed reads `interrupted` even when its pid has been reused: the
  writer holds an `flock` on `<run>/writer.lock` while it lives, and `describe` tests the lock.
- `matrix catalog --point <unknown>` is a `usage` error listing the points, not an empty
  catalog.
- `configure_telemetry` without `matrix[otel]` raises `ConfigError` with a fix, not
  `ImportError`.

### Changed

- **A failure matrix did not classify is still one envelope**: the `matrix` command prints
  `matrix.v1.error` with kind `unknown` (a traceback too, without `--json`). The envelope's
  `fix` is always present, null when there is none.
- **The `matrix` command is built on rich-click**, as `ix` is. Its commands, `--json`, exit
  codes and error document are unchanged; matrix depends on `rich-click`.
- **`process.spawn` takes anything with `defer`** as its first argument (`process.Deferring`),
  not only a `RunContext`, so code that holds a narrow view of its context can spawn. It is
  now `start` plus one deferred `kill_group`.
- **The domain does no host I/O.** `Registry.load(extensions)` registers the extensions it
  is given, keeping the all-or-nothing and quarantine rules; `matrix.load_extensions(registry)`,
  in the composition layer, finds them at the `matrix.extensions` entry point and hands them
  to it. That pair replaces `Registry.discover`. `default_registry(discover=False)` loads the
  built-ins under the origin `matrix`, never as a direct registration. A quarantined impostor
  is named by its entry point.
- **Signals are the host's.** `Executor(..., interrupts=matrix.cancel_on_signals)` turns
  SIGTERM and SIGINT into the run's cancellation; `Container.executor()` passes it. An
  executor given none leaves signals alone (`signals=` is gone).
- **`matrix.v1.error` nests structured fields under `details`**, as ix and recon do, so a
  detail can never overwrite `kind`, `message` or `fix`. A wrong value carries `path`,
  `value` and, where the legal values are a closed set, `allowed`.
- **What is registered or wired is versioned**: `Registry.register` and `wire` refuse a type
  URL without a `v<N>` segment, as the skill says.
- `config_env_var` is no longer exported.
- **The `usage` error kind** (exit 2, `matrix.UsageError`). `matrix` with no command prints
  its help and exits 0; a usage error under `--json` is the error document. The error
  document carries `"schema": "matrix.v1.error"`.

## 0.4.0

matrix is components wired by kind, a registry and config. The flow grammar is gone, a row is
the record itself, and rows land in the store while a component is still running.

### Removed

- **The flow grammar**: `Flow`, `Member`, `Port`, bindings, topics, `after`, `many`,
  `optional`, `compile_flow`, `CompiledFlow`, levels, and `Container.compile`. Nothing
  replaces them one for one; see "Added".
- **`Artifact`**, and with it `Construct.values`, `Construct.by_type`, `Construct.topics` and
  `Run.outputs`. A table's rows are the records.
- **`episode`**, on `Run`, `RunContext` and rows. Repeating is the caller's.
- `ConstructStore.save`. A run is written as it goes, not afterwards.
- `Run.flow`, `inputs.context`, and the `flow` field of run events.

### Added

- **Components wired by kind.** A component declares `requires` and `provides` as sets of
  kinds (type URLs) and implements `run(inputs, ctx)`. `wire(components, inputs=...)` infers
  the wiring and returns a `Wiring`: a component depends on the provider of each kind it
  requires, each kind has one provider, and the kinds the caller supplies are run inputs. It
  reports every problem at once: a kind nobody provides, two providers, a cycle.
- **`ctx.provide(kind, row)`** hands a row over while the component runs. When it returns the
  row is validated, in the record, and durable in the store. A component may also return rows,
  as before.
- **Readiness scheduling.** A component starts when the providers of what it requires have
  finished; unrelated components run side by side.
- **The ledger.** Provenance is a table of its own, kind `matrix.v1.ledger` (`LedgerEntry`:
  `seq`, `kind`, `row`, `component`, `time`). An entry points at its row by `(kind, row)` and
  is written first, so a kill leaves at worst an entry with no row, which a reader reports in
  `Construct.torn`.
- **A store that is written as the run goes.** `ConstructStore.open(run) -> RunWriter`,
  `RunWriter.append(kind, row)`, `RunWriter.finish(run)`; `Executor.run(..., store=)`. A run
  can be read while it is running and after it was killed: `describe` reports `running`, or
  `interrupted` when the writing process is gone.
- **`StoreError`**, error kind `store` (exit 1). A store that cannot keep a row, or finish a
  run, fails the run.
- **`Container.wire`** builds `{name, type, config}` entries through the registry and wires
  them; `matrix.components` and `matrix.inputs` carry them in config, and `matrix check` wires
  them.
- **`matrix.v1.ref`** (`Ref`): a row that stands for a value too large to be one.
- `Run.rows`, `Run.document`, `Construct.provenance`, `Construct.kinds`, `Construct.restore`,
  `Inputs.one`, `Wiring.order`.

### Changed

- **`Component.run` takes `(inputs, ctx)`**, and `Inputs` maps each required kind to a tuple
  of its rows.
- **Renamed, member to component**: `Run.members` is `Run.components`; `Limits.members` and
  `MemberLimits` are `Limits.components` and `ComponentLimits`; `MemberStatus` is
  `ComponentStatus`; `RunContext.member`, `Event.member` and `RunError.member` are
  `.component`; failure and release records say `component`; the events are
  `matrix.component.start`, `.stopping`, `.end` and `.late`.
- `matrix.artifact.append` is `matrix.row.append`, with fields `kind`, `row`, `seq`.
  `matrix.run.start` carries `components`; `matrix.run.end` carries `rows`.
- `Limits.concurrency` defaults to `None` (no cap); it was 1.
- The record keeps its own copy of each row, taken when the row is handed over.
- `matrix.v1.agent-step`: `inputs` maps placeholders to the kinds it requires; the reply is
  provided under the kind `response` names (two agent steps in one run each name their own);
  `output` is a kind.
- Opening a run id that exists is a `StoreError`; it was a `ConfigError` from `save`.
- On disk, a table file is named for its kind and each line is the row itself; `run.json`
  gains `components` and `releases` and drops `flow` and `episode`. Records written by 0.3 or
  earlier are not readable.
- OTel: `matrix.member.*` instruments and attributes are `matrix.component.*`;
  `matrix.run.duration` no longer has a `matrix.flow` attribute.

## 0.3.0

A run now always ends, and always cleans up; and it can be watched without being slowed.

### Added

- **One stop path.** A member is stopped the same way whatever the cause (its deadline,
  another member's failure, cancellation, SIGTERM, Ctrl-C): stop request, grace, cancel.
  `ctx.stopping` is the request; a member that returns within `grace_s` keeps what it returns.
- **Deferred releases.** `ctx.defer(fn)` registers something to undo. Every release is called
  when the run ends, last registered first, each bounded by `cleanup_s`, whether the run
  completed, failed or was cancelled. One that raises or overruns is in `Run.releases`.
- **Terminal states.** `Run.members` records how each member ended: `completed`, `failed`,
  `cancelled` or `skipped`, with the reason. A member that swallows cancellation is abandoned
  after `cleanup_s` and marked `abandoned`.
- **Cancelled runs.** Cancelling the task that awaits `Executor.run` raises `RunCancelled`
  (a `CancelledError`) carrying the partial `Run`, after the members stopped and the releases
  ran. `Run.status` may be `cancelled`.
- **Signals.** While a run is in flight, a SIGTERM or SIGINT that nothing else handles
  cancels it.
- **`Limits`**: `deadline_s` per member, `Limits.members` (`MemberLimits`) to override it by
  alias, `grace_s`, `cleanup_s`, and `on_failure: "fast" | "isolate"`.
- **`matrix.process.spawn`** starts a child in its own process group and defers killing the
  group.
- **Component telemetry.** `ctx.emit`, `ctx.measure` and `ctx.span`.
- **Observers.** `console` (progress on stderr) and `jsonl` (a file or stderr); `otel` records
  metrics as well as spans. `matrix.observers` accepts `{type, ...options}`.
- `Event.member`, `Event.document()`, `EventPolicy`, `Run.events_dropped`.

### Changed

- **A failure stops the run by default.** With `on_failure="fast"` the first failure asks
  every running member to stop and skips the rest; before, the rest of the level ran to its
  end. `on_failure="isolate"` skips only the members that depend on the failed one.
- **Events are delivered from a bounded queue**, not on the emitting task. An observer can no
  longer stall a run; under load events are dropped and counted.
- **Event names are namespaced**: `run.start` is `matrix.run.start`, and likewise
  `matrix.run.end`, `matrix.member.start`, `matrix.member.end`, `matrix.artifact.append`,
  `matrix.agent.start`, `matrix.agent.end`. `matrix.member.end` reports the member's terminal
  state in `status` (it was `ok` or an error kind), and the error kind in `kind`.
- A member past its deadline gets a stop request and a grace before it is cancelled.
- `RunContext` is no longer a frozen dataclass.
- `RecordingObserver` keeps its last 10 000 events rather than all of them.
- An invalid flow input is refused before the run starts, so no events are emitted for it.
- matrix depends on `rich`. The `otel` extra needs opentelemetry 1.23 or later.

### Removed

- `Limits.member_timeout_s`: use `deadline_s`.
- The `EVENTS` tuple in `matrix.domain.observer`.

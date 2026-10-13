# API reference

Every public name exported from `matrix`, the config it reads, the errors it raises, and the
`matrix` command.

```python
from matrix import (
    # type URLs
    TypeUrl, check_type_url, is_type_url, parse_type_url, type_url,
    # components and wiring
    Component, Inputs, RunContext, Wiring, wire,
    # execution
    Executor, Limits, ComponentLimits, ComponentStatus, Run, process,
    # the Construct and its stores
    Construct, LedgerEntry, LEDGER, Ref, REF,
    ConstructStore, RunWriter, JsonlConstructStore, MemoryConstructStore,
    # agents
    Agent, AgentDefinition, AgentResponse, AgentRuntime, BoundAgent, DefinitionSource,
    AgentStep, AgentStepConfig, AGENT_STEP, AGENT_RESPONSE, TASK,
    # registry
    Registry, Entry, Failure, default_registry, runtime_type_url, observer_type_url,
    # observers
    Event, EventPolicy, Observer, RecordingObserver, configure_telemetry,
    # config and composition
    Config, MatrixConfig, RuntimeConfig, ObserverConfig, AgentConfig, ComponentConfig,
    ConfigSource, load_config, discover_sources, config_env_var, compose, Container,
    # errors
    MatrixError, ConfigError, CompilationError, NotFoundError, ContractError, ComponentError,
    StoreError, AgentRuntimeError, RunError, RunCancelled, ErrorKind, RuntimeReason,
    EXIT_CODES,
    __version__,
)
```

---

## Type URLs

Everything is registered and typed under a type URL: dot-separated segments, each lowercase
kebab-case (`[a-z0-9]+(-[a-z0-9]+)*`), compared by equality and never dereferenced.

```
matrix.v1.runtime.claude-sdk     ix.v1.sensor.activation     matrix.v1.agent-response
```

- `/` is not part of the grammar.
- One of slick's component-kind names (`capability`, `skill`, `flow`, `agent`) is never a
  whole segment.
- `slick.*` is reserved; `type_url` refuses to mint into it.
- A URL matrix mints carries one version segment (`v1`, `v1alpha1`, `v2beta1`) after at least
  one namespace segment and before the resource.

A **kind**, in matrix, is the type URL of some data. Kinds are checked with the grammar when
components are wired; registry entries are checked the same way when they register.

| Name | Signature | Behaviour |
|------|-----------|-----------|
| `is_type_url` | `(url: str) -> bool` | True if `url` is valid under the grammar |
| `check_type_url` | `(url: str, *, where: str = "") -> str` | Returns `url`, or raises `ConfigError` saying why it is invalid, prefixed with `where` |
| `parse_type_url` | `(url: str) -> TypeUrl` | Splits at the version segment; `ConfigError` if there is none |
| `type_url` | `(namespace: str, version: str \| int, resource: str) -> str` | Builds and validates; an `int` version becomes `v<N>`; refuses the `slick` namespace |
| `TypeUrl` | `NamedTuple(namespace, version, resource)` | `str()` joins it back |

```python
from matrix import ConfigError, is_type_url, parse_type_url, type_url

type_url("ix", 1, "sensor.activation")          # 'ix.v1.sensor.activation'
parse_type_url("matrix.v1.runtime.claude-sdk")   # TypeUrl(namespace='matrix', version='v1', resource='runtime.claude-sdk')
is_type_url("matrix.v1/runtime.mock")            # False: '/' is not part of the grammar
is_type_url("acme.v1.agent.reviewer")            # False: 'agent' is one of slick's kind names
```

---

## Components and wiring

### `Component` (Protocol)

Structural and `runtime_checkable`: implement the shape, no base class.

| Name | Type |
|------|------|
| `requires` | a collection of kinds: the data it reads |
| `provides` | a collection of kinds: the data it writes |
| `run` | `async (inputs: Inputs, ctx: RunContext) -> Mapping[str, Any] \| None` |

`run` returns a mapping of kind to rows: a `list` or `tuple` is several rows, anything else is
one row. It may return `None`. It may also hand rows over while it runs, with `ctx.provide`,
and may do both. Configuration arrives when the component is built, never through `run`.

A component's name is the name it is given when it is wired, so one class can be used under
several names.

### `Inputs`

A read-only `Mapping[str, tuple[Any, ...]]`: each kind the component requires, to a tuple of
that kind's rows in the order they were recorded (empty when the provider handed over none).
Nothing else is in it. `inputs.one(kind)` returns the single row and raises `LookupError`
unless there is exactly one. A kind the component does not require raises `KeyError` listing
those it does.

### `RunContext`

What a component may know about its run, and ask of it. It says nothing about other
components.

| Attribute / method | Meaning |
|--------------------|---------|
| `run_id`, `component` | the run, and this component's name in it |
| `deadline` | a `time.monotonic()` deadline when the component has a `deadline_s`, else `None` |
| `stopping` | an `asyncio.Event`, set when the component is asked to stop |
| `provide(kind, row)` | hand over one row of a kind the component provides, now |
| `defer(release)` | register a sync or async callable to be called when the run ends |
| `emit(name, **fields)` | emit one event |
| `measure(name, value, *, unit="", kind="gauge", **attrs)` | record a number as a `matrix.measure` event; `kind` is `gauge`, `counter` or `histogram` |
| `span(name, **fields)` | an async context manager emitting `<name>.start` and `<name>.end` (`status`, `duration_ms`) |

`provide` is synchronous. When it returns, the row has been validated against the kind's
registered schema, appended to the Construct, and is durable in the run's store. It raises
`ContractError` for a kind the component does not provide or a row that fails the schema, and
`StoreError` when the store could not keep the row (the run then fails with kind `store`,
whatever the component does with the exception).

A name given to `emit`, `measure` or `span` is dotted lower-case with at least two segments,
and not under `matrix.*`; anything else is a `ContractError`. After the component has ended
every one of these calls is ignored, and the first is reported as `matrix.component.late`. A
`RunContext` built by hand (`RunContext("run", "name")`, for testing a component alone)
accepts every call and does nothing with it.

### `wire`

```python
wire(
    components: Mapping[str, Component] | Iterable[Component],
    *,
    inputs: Iterable[str] = (),
    schemas: Mapping[str, type[BaseModel]] | None = None,
    effects: Mapping[str, frozenset[str] | None] | None = None,
) -> Wiring
```

- `components` maps each instance's name to the component, or is a sequence of components
  that each carry a `name` attribute. A name matches `^[A-Za-z0-9][A-Za-z0-9._-]*$`.
- `inputs` are the kinds the caller will supply to every run.
- `schemas` maps kinds to the models their rows must validate against.
- `effects` maps names to what each component declares it may touch (`None` for unknown).

Raises `CompilationError` (kind `config`) whose message lists every problem, also available as
`error.details["problems"]`. The checks: names; every kind is a type URL and none is
`matrix.v1.ledger`; a component declares `requires` and `provides` as collections and has a
`run`; a required kind is provided or is a run input; a kind has one provider, and a run input
has none; no component requires what it provides; no cycle, which is reported naming its
components and the kinds it runs through.

```python
from matrix import CompilationError, wire

try:
    wire({"one": One(), "two": Two(), "reader": Reader()})
except CompilationError as e:
    for problem in e.details["problems"]:
        print(problem)
# kind 'acme.v1.a' is provided by one, two; a kind has one provider (two streams of the same shape are two kinds)
# components.reader.requires: nothing provides 'acme.v1.b' and it is not a run input
```

### `Wiring`

Frozen and reusable.

| Field / method | Type | Meaning |
|----------------|------|---------|
| `components` | `Mapping[str, Component]` | name to component, in the order given |
| `inputs` | `frozenset[str]` | the run-input kinds |
| `providers` | `Mapping[str, str]` | kind to the name of its one provider |
| `depends` | `Mapping[str, tuple[str, ...]]` | name to the components it waits for |
| `schemas` | `Mapping[str, type[BaseModel]]` | kind to the model its rows validate against |
| `effects` | `Mapping[str, frozenset[str] \| None]` | name to declared effects; `None` is unknown |
| `kinds` | `tuple[str, ...]` | every kind a run has a table for: inputs, then provided |
| `order()` | `-> tuple[str, ...]` | the components in one valid order of work; a view, not the schedule |
| `declared_effects()` | `-> frozenset[str] \| None` | every declared effect; `None` if any component's are unknown |

---

## Execution

### `Limits`

Frozen. An invalid value raises `ConfigError`.

| Field | Default | Meaning |
|-------|---------|---------|
| `concurrency` | `None` | how many components run at once (`>= 1`); `None` leaves it to the wiring |
| `deadline_s` | `None` | bounds each component separately, from when it starts (`> 0`) |
| `grace_s` | `5.0` | how long a component has to return after a stop request before it is cancelled (`>= 0`) |
| `cleanup_s` | `30.0` | how long a cancelled component, or one deferred release, has to finish before it is abandoned (`> 0`) |
| `on_failure` | `"fast"` | `fast`: the first failure stops every running component and skips the rest. `isolate`: only components that depend on the failed one are skipped |
| `components` | `{}` | name to `ComponentLimits(deadline_s, grace_s, cleanup_s)`; a field left `None` inherits the run's |

### `Executor`

`Executor(observers: Sequence[Observer] = (), *, events: EventPolicy | None = None,
signals: bool = True)`. Stateless between runs; safe to share. `events` bounds the event
queue (see [Observers](#observers)). With `signals=True`, while a run is in flight a SIGTERM
or SIGINT that nothing else handles cancels the run, so a signalled run still releases; a
host that installed its own handler keeps it. A second signal does what it always did.

```python
async Executor.run(
    wiring: Wiring,
    inputs: Mapping[str, Any] | None = None,
    *,
    limits: Limits | None = None,
    store: ConstructStore | None = None,
    run_id: str | None = None,
) -> Run
```

1. `inputs` must hold exactly the declared run inputs, or `ConfigError` names the missing and
   unknown ones. Each maps a kind to one row, or to a list or tuple of rows. Each row is
   validated against its kind's schema, if one is registered (`ContractError` otherwise).
   A name in `limits.components` that is not in the wiring is a `ConfigError`. All of this is
   checked before anything runs.
2. With a `store`, the run's record is opened there. A run id that already exists is a
   `StoreError`, raised before anything runs. The inputs are then recorded, by `$input`.
3. Scheduling is by readiness: a component starts when the providers of every kind it requires
   have finished, subject to `limits.concurrency`. It gets `Inputs` for those kinds, each row
   a deep copy. A row that cannot be copied is a `ContractError`: rows must be data, not live
   handles.
4. Rows a component hands over with `ctx.provide` are recorded then. What it returns is
   recorded when it ends: every returned row is checked first, and if one fails none is
   recorded. A row of an undeclared kind, a return value that is not a mapping, or a row that
   fails its kind's schema is a `ContractError`.
5. A component that raises becomes a `ComponentError` (a `MatrixError` it raises keeps its own
   kind); a component past its deadline is a `ComponentError` saying so, with
   `details["reason"] == "timeout"`. Each failure is recorded in `run.failures`, and
   `RunError` is raised from the first. A component whose provider did not complete is
   `skipped`.
6. Stopping a component is one sequence, whatever the cause (`deadline`, `run_failed`,
   `cancelled`): `ctx.stopping` is set; the component has `grace_s` to return, and what it
   returns is recorded; then its task is cancelled; if it is still running `cleanup_s` later
   it is abandoned and its record says `abandoned`. A component past its deadline is `failed`
   even when it returned within the grace.
7. A store that cannot keep a row, or cannot finish the run, fails the run: `RunError` with
   kind `store`, whatever `on_failure` says. Nothing more is written to that record.
8. When the run ends, every release registered with `ctx.defer` is called, last registered
   first, each bounded by `cleanup_s`, whatever the run's status. A synchronous release runs
   on the event loop and is not bounded. Then the store's record is finished.

Cancelling the task that awaits `run` (directly, or through Ctrl-C or SIGTERM) stops every
running component by the same sequence, skips the rest, calls the releases, and then raises
`RunCancelled`, a `CancelledError` whose `run` is the partial Run.

### `Run`

| Field / method | Type | Meaning |
|----------------|------|---------|
| `run_id` | `str` | |
| `construct` | `Construct` | every row recorded |
| `status` | `"running" \| "completed" \| "failed" \| "cancelled"` | |
| `started` / `ended` | `datetime` / `datetime \| None` | UTC |
| `failures` | `list[dict]` | `{"component", "kind", "message"}` per failed component. `component` is `$input` or `$store` when it was the record, not a component, that failed |
| `components` | `dict[str, dict]` | name to how it ended: `status` (`ComponentStatus`: `completed`, `failed`, `cancelled`, `skipped`), `reason` when it was stopped (`deadline`, `run_failed`, `cancelled`) or skipped (`upstream: <name>`, `policy: fast`, `cancelled`, `store failed`), `duration_ms`, and `abandoned: true` when it ignored cancellation |
| `releases` | `list[dict]` | `{"component", "status", "error"}` per deferred release, in the order called; `status` is `released`, `failed` or `abandoned` |
| `events_dropped` | `int` | events no observer received |
| `rows(kind)` | `-> tuple[Any, ...]` | the rows of `kind` |
| `document()` | `-> dict` | the run as a `matrix.v1.run` document, without its tables |

The component and run states are open vocabularies: a reader must tolerate one it does not
know.

### `process`

`await process.spawn(ctx, argv, *, grace_s=5.0, **options) -> asyncio.subprocess.Process`
starts `argv` in its own process group and defers killing that group with the run: SIGTERM,
then SIGKILL once `grace_s` has passed with the group still alive. `options` go to
`asyncio.create_subprocess_exec`. `await process.kill_group(proc, grace_s=5.0)` does the kill
directly; only the first call for a process signals. A child that moves itself to another
session is not reached.

---

## The Construct

One run's record: a table per kind, plus the ledger. See
[the data model](../explanation/data-model.md) for the reasoning.

### `Construct`

| Method | Returns |
|--------|---------|
| `rows(kind)` | `tuple[Any, ...]`: the rows of `kind`, in order; `()` if none. `rows("matrix.v1.ledger")` is the ledger |
| `last(kind)` | the most recent row; `NotFoundError` listing the kinds that have rows |
| `provenance(kind)` | `tuple[LedgerEntry, ...]`: the entries for that kind's rows, in row order |
| `kinds()` | `tuple[str, ...]`: every table, the ledger's last |
| `ledger` | `tuple[LedgerEntry, ...]`: every row's entry, in the order rows were appended |
| `torn` | `tuple[LedgerEntry, ...]`: entries whose row never landed; always `()` for a live run |
| `kind in construct` | `True` if the kind has at least one row |
| `len(construct)` | number of rows |
| `Construct.restore(tables)` | a Construct from stored tables (`{kind: rows}`, the ledger's among them); `StoreError` if the record contradicts itself |

`append(kind, row, *, component)` and `write_through(sink)` are the executor's.

### `LedgerEntry` and `LEDGER`

`LEDGER = "matrix.v1.ledger"`. `LedgerEntry` is a frozen pydantic model, registered as that
kind's payload: `seq: int` (the row's place in the run's order), `kind: str`, `row: int` (its
0-based position in the kind's table), `component: str` (who handed it over, or `$input`),
`time: datetime`. An entry is written before the row it points at.

### `Ref` and `REF`

`REF = "matrix.v1.ref"`. `Ref(uri: str, media_type: str = "", size: int | None = None,
digest: str | None = None)`, frozen, unknown fields rejected: a small row that stands for a
large or external value. matrix records it and does nothing else with it.

### `ConstructStore` and `RunWriter` (Protocols)

| Method | Contract |
|--------|----------|
| `store.open(run) -> RunWriter` | creates the run's record with status `running`. An existing run id is a `StoreError` |
| `writer.append(kind, row)` | adds `row` to `kind`'s table; durable before it returns. The executor appends each row's ledger entry, then the row |
| `writer.finish(run) -> str` | records the final state atomically; returns where the record is |
| `store.load(run_id) -> Construct` | the record of a run in any state; `torn` reports entries with no row |
| `store.describe(run_id) -> dict` | the `matrix.v1.run` document with each table's size. A run that never finished reads `running`, or `interrupted` when the store can tell its writer is gone |
| `store.runs() -> list[str]` | run ids, oldest first |

A missing run is a `NotFoundError` naming those that exist. Anything a store cannot write or
read back is a `StoreError`; the executor turns any other exception a store raises into one.

`JsonlConstructStore(root, *, fsync=False)` keeps a directory per run: `run.json`, and one
`<kind>.jsonl` per table whose lines are the rows. `fsync=True` flushes each row to the disk.
`MemoryConstructStore()` keeps runs in a dict, rows as given.

---

## Agents

### `AgentDefinition`

What an agent is. Frozen pydantic model, unknown fields rejected.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `name` | `str` | required | `^[A-Za-z0-9][A-Za-z0-9._-]*$` |
| `description` | `str` | `""` | |
| `system_prompt` | `str` | `""` | |
| `model` | `str \| None` | `None` | Interpreted by the runtime: an SDK alias or model id for `claude-sdk`, a hardline registry name for `model`. `None` is the runtime's default |
| `tools` | `tuple[str, ...] \| None` | `None` | `None` is the runtime's default toolset; `()` is **no tools**, never collapsed into the default |
| `max_turns` | `int \| None` | `None` | `>= 1`; `None` is the runtime's default |
| `metadata` | `dict[str, Any]` | `{}` | Keys a source carried that no runtime interprets; kept, never read for behaviour |

### `AgentResponse`

The runtime-neutral record of one session. Frozen.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `content` | `str` | `""` | |
| `tool_calls` | `tuple[dict, ...]` | `()` | `{name, input}` per call |
| `tokens_input`, `tokens_output` | `int` | `0` | |
| `duration_ms` | `int` | `0` | |
| `cost_usd` | `float \| None` | `None` | when the backend reports it |
| `num_turns` | `int` | `0` | |
| `family` | `str \| None` | `None` | model family that answered (`claude`, `qwen`, ...), stamped by the runtime |
| `model` | `str \| None` | `None` | model id or registry name, when known |
| `stop` | `str \| None` | `None` | `completed`, or the limit reached (`max_turns`, `max_budget_usd`, ...). A limit is not a failure |

### `AgentRuntime` (Protocol)

| Method | Signature | Contract |
|--------|-----------|----------|
| `check` | `(definition) -> None` | **Required.** Raises `ConfigError` for a definition the runtime cannot honour. A runtime that accepts everything implements it as a no-op |
| `run` | `async (definition, task: str) -> AgentResponse` | Returns a response whose `stop` says why the session ended, or raises `AgentRuntimeError` with a `reason`. Never an empty normal response for a failed session |

### `Agent` (Protocol) and `BoundAgent`

`Agent` is what callers use: `name` and `async run(task: str) -> AgentResponse`.

`BoundAgent(definition, runtime, *, observers=())` satisfies it. Construction calls
`runtime.check(definition)`, so a refusal happens at binding. Properties `name`,
`definition`, `runtime`. `run(task)` emits `agent.start` and `agent.end`.

### Built-in runtimes

Registered at the `runtime` point. In config, `type:` takes the short name.

| `type` | Type URL | Config fields | Needs | Notes |
|--------|----------|---------------|-------|-------|
| `claude-sdk` | `matrix.v1.runtime.claude-sdk` | `permission_mode` (`default`, `acceptEdits`, `plan`, `bypassPermissions`, `dontAsk`, `auto`; default `default`), `cwd`, `setting_sources` (`[]` is hermetic), `plugins` (relative `path` resolved against `cwd`), `fallback_model`, `agents` | `cwd` | Extra `[claude]`. Accepts every definition. `bypassPermissions` and `dontAsk` log a WARNING. Stamps `family="claude"` |
| `model` | `matrix.v1.runtime.model` | `models` (a hardline registry section; filled from `matrix.models` when absent), `default_model`, `temperature`, `max_tokens` | `models` | Extra `[models]`. One call, no tool loop: `check` refuses a definition that declares tools |
| `mock` | `matrix.v1.runtime.mock` | `responses` (task to reply), `default` (reply for an unmatched task; `None` echoes `mock:<agent>:<task>`), `family` (default `mock`) | | Offline. Accepts every definition. Records `calls` |

Classes: `matrix.adapters._out.runtime.claude_sdk.ClaudeSdkRuntime`,
`matrix.adapters._out.runtime.model.ModelAgentRuntime`,
`matrix.adapters._out.runtime.mock.MockRuntime`.

### `DefinitionSource` (Protocol)

`load() -> list[AgentDefinition]`, `describe() -> str`.
`matrix.adapters._out.definitions.markdown.MarkdownDefinitionSource(directory)` reads every
`*.md` in a directory, in Claude Code's agent-file format:

```markdown
---
name: reviewer                 # defaults to the file stem
description: Reviews a diff for correctness.
tools: Read, Grep, Glob        # comma string or YAML list; omit for the runtime default
model: sonnet                  # optional
max_turns: 3                   # optional; maxTurns also accepted
color: blue                    # anything else is kept in metadata
---
You are a careful reviewer...  (the body is the system prompt)
```

Errors name the file. A missing directory is a `ConfigError`.

### `matrix.v1.agent-step`

A registered `component` that runs one composed agent. Constants:
`AGENT_STEP = "matrix.v1.agent-step"`, `AGENT_RESPONSE = "matrix.v1.agent-response"`,
`TASK = "matrix.v1.task"`.

`AgentStepConfig` (unknown keys rejected):

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `agent` | `str` | required | a composed agent's name |
| `inputs` | `dict[str, str]` | `{"task": "matrix.v1.task"}` | placeholder name to kind; the kinds are the step's `requires` |
| `task` | `str \| None` | `None` | template with `{placeholder}`s (`{{ }}` for literal braces). `None` is allowed only with exactly one input, whose rows are the task |
| `response` | `str` | `matrix.v1.agent-response` | the kind the `AgentResponse` is provided as |
| `output` | `str \| None` | `None` | a kind; the JSON the agent returned is provided as one row of it |

It provides `response`, and `output` when set. A kind has one provider, so two agent steps in
one run each name their own `response`. Rows are rendered as text: one row as itself (a string
as is, a pydantic model as indented JSON, anything else through `json.dumps`), several rows as
a JSON list; a required kind with no rows is a `ContractError`. The `output` row is the JSON
document in the reply, either the whole reply or a fenced `json` block; a reply with no JSON
document is a `ContractError`, and so is one that fails the kind's schema.

---

## The registry

### Extension points and entries

Points matrix declares: `runtime`, `component`, `payload-type`, `observer`. Other packages
declare their own with `add_point` (ix declares `sensor` and `engine`).

`Entry` (frozen):

| Field | Type | Meaning |
|-------|------|---------|
| `point` | `str` | |
| `type_url` | `str` | |
| `build` | `Callable` | called as `build(validated_config, **needs)`, or `build(**needs)` when `config` is `None`. For `payload-type`, the model itself |
| `config` | `type[BaseModel] \| None` | the model its settings validate against |
| `needs` | `frozenset[str]` | shared things composition must hand it: `agents`, `models`, `cwd`, or keys of `compose(context=...)` |
| `effects` | `frozenset[str] \| None` | what it may touch: `network`, `subprocess`, `filesystem`, `model`. `None` is unknown, not none |
| `summary` | `str` | |
| `stability` | `"stable" \| "beta" \| "experimental"` | default `beta` |
| `origin` | `str` | the distribution that registered it; `(direct)` for a direct call |

`entry.describe()` returns all of it as a JSON-ready dict, with `config` as a JSON schema.

`Failure(extension, origin, error)` records an extension that failed to load or a refused
registration.

### `Registry`

| Method | Signature | Behaviour |
|--------|-----------|-----------|
| `add_point` | `(point) -> Registry` | Declares an extension point |
| `register` | `(point, type_url, build, *, config=None, needs=(), effects=None, summary="", stability="beta") -> Registry` | `ConfigError` for an undeclared point, an invalid type URL, or a type URL already registered at that point (naming both origins), unless namespace ownership resolves it (see below) |
| `register_payload` | `(type_url, model, summary="") -> Registry` | Registers a pydantic model at `payload-type`; summary defaults to the model's docstring |
| `entry` | `(point, type_url) -> Entry` | `NotFoundError` listing what is registered at that point |
| `entries` | `(point=None) -> list[Entry]` | Sorted by point, then type URL |
| `__contains__` | `((point, type_url)) -> bool` | |
| `create` | `(point, type_url, config=None, *, needs=None, where="<config>") -> Any` | Validates `config` and builds the entry with the needs it declared. `ConfigError` for a missing need, for config given to an untyped entry, or for invalid config (each problem as a key path under `where`) |
| `payload_schemas` | `() -> dict[str, type[BaseModel]]` | Every `payload-type` entry that is a pydantic model |
| `discover` | `(group="matrix.extensions") -> Registry` | Loads every entry point in the group, in name order (see below) |

Attributes: `points: set[str]`, `failures: list[Failure]`.

`discover` rules:

- Each entry point's value is `register(registry) -> None`.
- Registration is all-or-nothing per extension: if `register` raises part way, none of its
  entries or points stay, and the failure is recorded in `failures`.
- An extension that registers at an undeclared point is retried once every other extension
  has loaded; if the point is still undeclared, it is recorded as a failure.
- Namespaces have owners: a distribution owns the type URLs whose root segment is its own
  name (`matrix` owns `matrix.*`, `ix` owns `ix.*`). When two extensions register the same
  type URL at one point, the owner keeps it whichever loads first, and the other extension is
  quarantined whole, so an extension cannot replace a built-in by sorting before it. Between
  two non-owners, the first in entry-point name order keeps it and the second fails, naming
  both. Code calling `register` directly is treated as the owner.

`default_registry(*, discover=True) -> Registry` returns a registry with every
`matrix.extensions` entry point loaded. `discover=False` registers only matrix's built-ins.

`runtime_type_url(name)` maps `claude-sdk` to `matrix.v1.runtime.claude-sdk`;
`observer_type_url(name)` maps `otel` to `matrix.v1.observer.otel`. A name containing `.` is
returned unchanged.

Matrix's own entries:

| Point | Type URL | Needs | Effects |
|-------|----------|-------|---------|
| `runtime` | `matrix.v1.runtime.claude-sdk` | `cwd` | `model`, `network`, `subprocess`, `filesystem` |
| `runtime` | `matrix.v1.runtime.model` | `models` | `model`, `network` |
| `runtime` | `matrix.v1.runtime.mock` | | none |
| `component` | `matrix.v1.agent-step` | `agents` | unknown |
| `payload-type` | `matrix.v1.agent-response` | | |
| `payload-type` | `matrix.v1.task` | | |
| `payload-type` | `matrix.v1.ledger` | | |
| `payload-type` | `matrix.v1.ref` | | |
| `observer` | `matrix.v1.observer.console` | | none |
| `observer` | `matrix.v1.observer.jsonl` | | `filesystem` |
| `observer` | `matrix.v1.observer.otel` | | `network` |

A third-party extension:

```toml
[project.entry-points."matrix.extensions"]
acme = "acme.matrix_ext:register"
```

```python
from pydantic import BaseModel


class Review(BaseModel):
    """A review verdict."""

    verdict: str
    score: int


class ScorerConfig(BaseModel):
    threshold: int = 3


class Scorer:
    requires = {"acme.v1.diff"}
    provides = {"acme.v1.review"}

    def __init__(self, config: ScorerConfig) -> None:
        self.threshold = config.threshold

    async def run(self, inputs, ctx):
        n = len(inputs.one("acme.v1.diff"))
        verdict = "ok" if n < self.threshold else "long"
        return {"acme.v1.review": {"verdict": verdict, "score": n}}


def register(registry):
    registry.register(
        "component",
        "acme.v1.scorer",
        Scorer,
        config=ScorerConfig,
        effects=set(),
        summary="Scores a diff by length",
        stability="experimental",
    )
    registry.register_payload("acme.v1.review", Review)
```

---

## Observers

### `Event`

Frozen: `name`, `span`, `run_id: str | None`, `parent: str | None`, `fields: dict`,
`component: str | None`, `time: datetime` (UTC), `schema: str = "matrix.v1.event"`.
`document()` is the JSON-ready form every sink writes:
`{schema, name, time, run_id, span, parent, component, fields}`. Within `matrix.v1.event` fields
are only ever added.

Names are namespaced, root first. matrix owns `matrix.*`; a component emits under its own
root, and an observer treats a name it does not know as opaque. A `*.start`/`*.end` pair
shares a `span`. An agent called from inside a component reports that component's span as its
`parent` and its run id as its `run_id`; called outside a run, both are `None`.

| Event | `span` / `parent` | `fields` |
|-------|-------------------|----------|
| `matrix.run.start` | run id / none | `components` |
| `matrix.run.end` | run id / none | `status`, `rows`, `duration_ms` |
| `matrix.component.start` | new id / run id | |
| `matrix.component.stopping` | the component's / run id | `reason` (`deadline`, `run_failed`, `cancelled`) |
| `matrix.component.end` | the component's / run id | `status`, and when they apply `reason`, `duration_ms`, `kind`, `abandoned`. Exactly one per component; a skipped component has no start |
| `matrix.component.late` | new id / run id | `call`: the context method used after the component ended |
| `matrix.row.append` | new id / run id | `kind`, `row`, `seq`: the row's ledger pointer and order |
| `matrix.release.start` | new id / run id | |
| `matrix.release.end` | same / run id | `status` (`released`, `failed`, `abandoned`), `error` |
| `matrix.agent.start` | new id / the calling span | `agent`, `model` |
| `matrix.agent.end` | same / the calling span | `agent`, `duration_ms`, then `status` (`stop`), `tokens_input`, `tokens_output`, `cost_usd`, `model`, `family`; or `status="error"`, `error`, `reason` |
| `matrix.measure` | new id / the calling span | `name`, `value`, `unit`, `kind`, `attrs` |
| `matrix.events.dropped` | new id / none | `count` |
| `<name>.start`, `<name>.end` | from `ctx.span` | the caller's fields; the end has `status` (`ok`, `error`) and `duration_ms` |

Events about a component carry its name in `component`.

### `Observer` (Protocol), `EventPolicy` and `RecordingObserver`

`Observer` is `on_event(event: Event)`, a plain function or a coroutine function.

Events are delivered by a dispatcher task from a bounded queue, never on the emitting task.
`EventPolicy(queue: int = 4096, drain_s: float = 10.0)` sets the bound, and how long the
executor waits at the end of a run for the queue to empty. What follows from that:

- emitting never waits. When the queue is full the event is dropped and counted; the next
  event delivered is preceded by `matrix.events.dropped`, and `Run.events_dropped` has the
  total;
- an observer that raises is logged and skipped;
- an observer that never returns holds up the dispatcher, not the run: later events are
  dropped, and the run returns after at most `drain_s`;
- a synchronous observer that blocks the event loop is not bounded. One that does slow I/O
  must be `async`.

Outside a run (a `BoundAgent` called directly) there is no queue, and observers are called
inline.

`RecordingObserver(limit: int = 10_000)` keeps the last `limit` events in `events`;
`names()` lists their names.

### The `console` observer

`matrix.v1.observer.console`. One line on stderr for each run starting and ending, each
component starting, being asked to stop and ending, each release that did not go cleanly, and
dropped events. On a terminal the components still running are shown live with their elapsed time
instead of a "started" line; `NO_COLOR` and `TERM=dumb` are honoured.

### The `jsonl` observer

`matrix.v1.observer.jsonl`, config `path` (omitted or `-` for stderr; a file is appended to).
One event document per line, flushed as written, so the stream can be followed while the run
is going.

### The `otel` observer

`matrix.v1.observer.otel`, enabled with `observers: [otel]`. Requires `[otel]`.

Spans: one per `*.start`/`*.end` pair, parented as the events are, so `matrix.run` holds
`matrix.component` holds whatever the component opened with `ctx.span`. An agent session is
`invoke_agent {agent}` (`gen_ai.operation.name=invoke_agent`). Fields become attributes:
`agent`, `model`, `tokens_input` and `tokens_output` map to `gen_ai.agent.name`,
`gen_ai.request.model`, `gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens`; other
scalar fields become `<root>.<field>` under the event's root (`matrix.status`). A failed
component or run, or an agent or span ending in error, sets the span status to ERROR.

Metrics, derived from the same events:

| Instrument | Kind | Attributes |
|------------|------|------------|
| `matrix.run.duration` | histogram, s | `matrix.status` |
| `matrix.component.duration` | histogram, s | `matrix.component`, `matrix.status` |
| `matrix.component.count` | counter | `matrix.component`, `matrix.status` |
| `matrix.agent.tokens` | counter | `gen_ai.agent.name`, `gen_ai.token.type` |
| `matrix.agent.cost_usd` | counter | `gen_ai.agent.name` |
| `matrix.events.dropped` | counter | |
| the name given to `ctx.measure` | the measure's `kind` | the measure's scalar `attrs`, `matrix.component` |

### `configure_telemetry`

`configure_telemetry(*, service_name="matrix", endpoint=None, console=False) -> None`
configures the OpenTelemetry SDK's span exporter. `endpoint` is an OTLP gRPC endpoint and
falls back to `OTEL_EXPORTER_OTLP_ENDPOINT`; `console=True` prints spans. With neither, it
does nothing. It does not set up a metrics exporter: configure a `MeterProvider` directly.
Requires `[otel]`.

---

## Configuration

### Tiers

`load_config` reads these files, lowest priority first, and merges them before validating:

| Tier | File | For |
|------|------|-----|
| 1 | `~/.matrix/config.yaml` | your runtimes, models and agents, for every tool |
| 2 | `~/.<tool>/config.yaml` | your defaults for this tool |
| 3 | `./matrix.yaml` | the project's shared runtimes and agents |
| 4 | `./<tool>.yaml` | the project's settings for this tool |
| 5 | `$MATRIX_CONFIG` | an explicit shared file; must exist |
| 6 | `$<TOOL>_CONFIG` | an explicit tool file; must exist |

Schema defaults sit below tier 1; a tool's command-line flags sit above tier 6. Absent files
read as empty. A file that exists but is not a YAML mapping is a `ConfigError` naming it.

Merge rules: mappings merge key by key, later tiers winning; lists replace, except
`matrix.definitions`, whose directories accumulate across tiers, each relative directory
resolved against the file that declared it.

### `MatrixConfig`

The `matrix:` section. Frozen, unknown keys rejected.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `definitions` | `tuple[str, ...]` | `()` | directories of `*.md` agent files |
| `runtimes` | `dict[str, RuntimeConfig]` | `{}` | named runtimes |
| `observers` | `tuple[str \| ObserverConfig, ...]` | `()` | each a short name (`console`, `jsonl`, `otel`) or type URL, or `{type, ...options}` for one that takes config |
| `agents` | `dict[str, AgentConfig]` | `{}` | named agents |
| `components` | `tuple[ComponentConfig, ...]` | `()` | the components of a run, each `{name, type, config}`. Their order of work is inferred; the order here means nothing |
| `inputs` | `tuple[str, ...]` | `()` | the kinds a caller supplies when it runs `components` |
| `models` | `dict \| None` | `None` | a hardline registry section (`{default?, models: {...}}`) handed to every `model` runtime that has none. `None` lets hardline read its own tiers |

`RuntimeConfig`: `type` (a built-in short name or a full type URL) plus that runtime's own
options as extra keys, validated by the runtime's config model.

`ComponentConfig` (unknown keys rejected): `name` (this instance's name in the run), `type`
(the type URL of a registered `component`), `config` (that component's own settings,
validated by its config model).

`AgentConfig` (unknown keys rejected): `runtime` (required, a name under `runtimes`),
`definition` (a loaded definition's name; defaults to the agent's own key), and overrides
`description`, `system_prompt`, `model`, `tools`, `max_turns`. An agent with no loaded
definition must give `system_prompt` inline.

```yaml
matrix:
  definitions: [agents/]
  models:
    default: qwen3-8b
    models:
      qwen3-8b: {backend: openai-compat, base_url: "http://127.0.0.1:8080/v1",
                 model: mlx-community/Qwen3-8B-4bit, family: qwen, local: true}
  runtimes:
    sdk:   {type: claude-sdk, permission_mode: default, setting_sources: []}
    local: {type: model}
  observers:
    - otel
    - {type: jsonl, path: runs/events.jsonl}
  agents:
    reviewer: {runtime: sdk}                                    # agents/reviewer.md
    triage:   {runtime: local, definition: reviewer, tools: [], model: qwen3-8b}
    greeter:  {runtime: local, system_prompt: "Say hello."}
mytool:
  trials: 3
```

### `Config[C]`

Frozen, generic over a tool's pydantic model: `matrix: MatrixConfig`, `client: C`, and
`sources: tuple[str, ...]`, every file consulted (lowest priority first, absent ones marked
`(absent)`).

### `load_config`

```python
load_config(client_type=None, client_key="matrix", sources=None) -> Config
```

- `client_type`: the tool's pydantic model for its own section.
- `client_key`: the tool's name: its section key and the `<tool>` in the tiers.
- `sources`: paths (or `ConfigSource` objects) lowest priority first; `None` uses
  `discover_sources(client_key)`.

Raises `ConfigError` naming every bad key path (`matrix.agents.a.runtim`) and every file
consulted.

```python
from pydantic import BaseModel

from matrix import compose, load_config


class MyToolConfig(BaseModel):
    trials: int = 5


config = load_config(MyToolConfig, "mytool")   # reads matrix: and mytool: from every tier
config.client.trials
container = compose(config)
```

`discover_sources(tool, project_root=None) -> list[Path]` returns the tier paths
(`project_root` defaults to the working directory); a set but missing `$MATRIX_CONFIG` or
`$<TOOL>_CONFIG` is a `ConfigError`. `config_env_var(tool)` gives the variable name:
`"my-tool"` becomes `MY_TOOL_CONFIG`.

`ConfigSource` (Protocol): `read() -> dict` (empty when absent, no validation) and
`describe() -> str`.

---

## Composition

### `compose`

```python
compose(
    config: Config | MatrixConfig,
    *,
    registry: Registry | None = None,
    base_dir: Path | str | None = None,
    source: str = "matrix",
    definition_sources: Sequence[DefinitionSource] = (),
    context: Mapping[str, Any] | None = None,
) -> Container
```

Builds, in order: observers, runtimes (each resolved by type through the registry, options
validated by its config model), definitions (every `*.md` in each `definitions` directory,
plus `definition_sources`), and agents (each runtime's `check` runs here). Makes no model or
tool call.

- `registry` defaults to `default_registry()`.
- `base_dir` anchors relative `definitions` directories that `load_config` did not already
  resolve; defaults to the working directory.
- `source` prefixes error messages (`mytool.yaml: agents.triage.runtime: ...`).
- `context` supplies shared needs, such as `{"cwd": "/path"}`, to extensions that declare them.

Every failure is a `ConfigError` naming the key path and the legal values; one definition name
in two directories is also a `ConfigError`.

### `Container`

| Name | Returns |
|------|---------|
| `config`, `registry` | what it was composed from |
| `runtimes`, `definitions`, `agents` | name to object |
| `observers` | `tuple[Observer, ...]` |
| `agent(name)` | `BoundAgent`; `NotFoundError` listing configured agents |
| `runtime(name)` | `AgentRuntime`; `NotFoundError` listing configured runtimes |
| `bind(definition, runtime)` | a `BoundAgent` on the configured runtime named `runtime`, with the container's observers |
| `needs()` | the shared needs: `agents`, `models`, `cwd`, plus `context` keys |
| `wire(components=None, *, inputs=None)` | a `Wiring`. Each `{name, type, config}` entry (a `ComponentConfig` or a mapping) is built from the registry's `component` entries and the result wired, with the registry's payload schemas and each component's declared `effects` (an `agent-step` has its agent's runtime's). Both arguments default to the config's `matrix.components` and `matrix.inputs`. Entries that cannot be built and wiring problems are reported together, in one `CompilationError`. A component that declares the need `name` is handed its own |
| `executor()` | an `Executor` with the container's observers |

---

## Errors

Every error matrix raises deliberately is a `MatrixError`. Each carries a `kind` (what the
caller can do about it), an `exit_code`, an optional `fix` (one actionable sentence), and
`details` (structured fields). `payload()` renders
`{"error": {"kind", "message", "fix", ...details}}`.

| Kind | Exit | Meaning, and the next move |
|------|------|----------------------------|
| `config` | 3 | a config, definition, wiring or argument is invalid: fix it |
| `not_found` | 4 | a name or type URL is not registered or configured |
| `contract` | 1 | a component broke what it declares (a bug in it) |
| `component` | 1 | a component raised while running |
| `store` | 1 | the run's record could not be written or read: fix the store |
| `transient` | 5 | unavailable, rate-limited or timed out: retry later |
| `auth` | 6 | credentials missing or refused |
| `unknown` | 1 | anything else that went wrong |

`EXIT_CODES` is this mapping; `ErrorKind` is the literal type of the kinds.

| Class | Kind | Also | Raised when |
|-------|------|------|-------------|
| `MatrixError` | `unknown` | `Exception` | base class |
| `ConfigError` | `config` | `ValueError` | a config, definition, type URL or argument is invalid |
| `CompilationError` | `config` | `ConfigError` | components cannot be wired; `details["problems"]` lists every problem |
| `NotFoundError` | `not_found` | `KeyError` | a name, type URL, kind with rows or run does not exist; the message lists those that do |
| `ContractError` | `contract` | | a row is of a kind its component does not provide, or fails its kind's schema; or a run input does |
| `ComponentError` | `component` | | a component raised or passed its deadline; the original exception is the cause |
| `StoreError` | `store` | | a row or a run could not be kept, a run id is taken, or a stored record contradicts itself |
| `AgentRuntimeError` | from `reason` | | an agent session failed |
| `RunError` | the first failure's | | a run failed; `run` is the partial `Run`, `component` the name of the first to fail, `construct` the partial Construct |

`RunCancelled` is not a `MatrixError`: it is an `asyncio.CancelledError` (so asyncio and
callers treat it as the cancellation it is) with one addition, `run`, the partial `Run`. Its
`run.status` is `cancelled`, or `completed`/`failed` when the cancellation arrived as the run
was already ending.

`AgentRuntimeError(message, *, reason="failed", fix=None, **details)` maps its `reason`
(`RuntimeReason`) to a kind; a reason matrix does not know maps to `unknown`. `retryable` is
true for the transient ones:

| `reason` | Kind | Meaning |
|----------|------|---------|
| `unavailable` | `transient` | backend down |
| `rate_limited` | `transient` | |
| `timeout` | `transient` | |
| `auth` | `auth` | credentials missing or refused |
| `incapable` | `config` | the runtime cannot honour the definition (or is not installed) |
| `refused` | `unknown` | the model declined |
| `failed` | `unknown` | the session ran and broke |

```python
from matrix import AgentRuntimeError

e = AgentRuntimeError("agent 'x': HTTP 429", reason="rate_limited")
e.kind, e.retryable, e.exit_code   # ('transient', True, 5)
```

---

## The `matrix` command

Read-only: it inspects extensions and configs and never runs a component or calls a model. Data
goes to stdout, diagnostics to stderr.

| Command | Output (`--json` schema) |
|---------|--------------------------|
| `matrix catalog [--point P]` | every registered entry (point, type URL, stability, summary, origin, needs, effects, whether configurable) and every failed extension (`matrix.v1.catalog`) |
| `matrix describe TYPE_URL` | the full entry, including its config JSON schema (`matrix.v1.extension`); `not_found` if nothing is registered under it |
| `matrix config [--tool T] [--sources]` | the merged `matrix` section and the files consulted (`matrix.v1.config`); with `--sources`, only the files (`matrix.v1.config-sources`) |
| `matrix check [--tool T]` | composes runtimes and agents, wires the configured components, and reports what composed (`matrix.v1.check`) |
| `matrix agents [--tool T]` | each agent's name, runtime, runtime type, model, tools and description (`matrix.v1.agents`) |
| `matrix --version` | the installed version |

`--tool` (default `matrix`) selects whose config tiers to read. With `--json`, an error is the
`payload()` document on stderr; without it, `error (<kind>): <message>` and `fix: <fix>`.

| Exit | Meaning |
|------|---------|
| 0 | ok |
| 1 | failure (`contract`, `component`, `store`, `unknown`) |
| 2 | usage |
| 3 | config |
| 4 | not found |
| 5 | transient |
| 6 | auth |

```console
$ matrix check
error (config): matrix: agents.greeter.runtime: 'sdk' is not a configured runtime. Configured: offline
fix: name a runtime defined under matrix.runtimes
$ echo $?
3
```

# API reference

Every public name exported from `matrix`, the config it reads, the errors it raises, and the
`matrix` command.

```python
from matrix import (
    # type URLs
    TypeUrl,
    check_type_url,
    is_type_url,
    parse_type_url,
    type_url,
    # components and wiring
    Component,
    Inputs,
    RunContext,
    Wiring,
    wire,
    # execution
    Executor,
    Limits,
    ComponentLimits,
    Retry,
    Reuse,
    ComponentStatus,
    Run,
    ComponentEnding,
    ComponentFailure,
    ReleaseOutcome,
    process,
    cancel_on_signals,
    # the Construct and its stores
    Construct,
    LedgerEntry,
    LEDGER,
    Session,
    SESSION,
    digest,
    agent_session,
    call_sent,
    call_session,
    record_session,
    Ref,
    REF,
    ConstructStore,
    RunWriter,
    JsonlConstructStore,
    MemoryConstructStore,
    # agents
    Agent,
    AgentDefinition,
    AgentResponse,
    AgentRuntime,
    RUNTIME_LOGGER,
    BoundAgent,
    AgentStep,
    AgentStepConfig,
    AGENT_STEP,
    AGENT_RESPONSE,
    TASK,
    # model calls
    ModelRuntime,
    ModelRequest,
    ModelCall,
    FinishReason,
    TokenUsage,
    RequestParams,
    CallAttempt,
    RegistryRow,
    ModelStep,
    ModelStepConfig,
    MODEL_STEP,
    MODEL_CALL,
    PROMPT,
    # registry
    Registry,
    Entry,
    Extension,
    Failure,
    default_registry,
    load_extensions,
    agent_runtime_type_url,
    model_runtime_type_url,
    observer_type_url,
    store_type_url,
    # observers
    Event,
    EventPolicy,
    Observer,
    RecordingObserver,
    configure_telemetry,
    Telemetry,
    # loading documents as your own types
    load,
    load_each,
    Loader,
    Document,
    DocumentSource,
    BesideFile,
    Accumulate,
    Problem,
    # config and composition
    Config,
    MatrixConfig,
    RuntimeConfig,
    ObserverConfig,
    AgentConfig,
    ComponentConfig,
    StoreConfig,
    load_config,
    discover_sources,
    compose,
    Container,
    # what the matrix command prints
    catalog,
    describe_extension,
    config_sources,
    config_view,
    check_config,
    list_agents,
    plan_config,
    list_runs,
    Catalog,
    Extensions,
    ConfigSources,
    ConfigView,
    CheckReport,
    Agents,
    Plan,
    Runs,
    # errors
    MatrixError,
    UsageError,
    ConfigError,
    CompilationError,
    NotFoundError,
    ContractError,
    ComponentError,
    StoreError,
    AgentRuntimeError,
    ModelRuntimeError,
    RunError,
    RunCancelled,
    ErrorKind,
    RuntimeReason,
    EXIT_CODES,
    EXITS_AS,
    EXIT_MEANINGS,
    EXIT_NEXT,
    # the skill and the version
    skill,
    __version__,
)
```

---

## Type URLs

Everything is registered and typed under a type URL: dot-separated segments, each lowercase
kebab-case (`[a-z0-9]+(-[a-z0-9]+)*`), compared by equality and never dereferenced.

```
matrix.v1.agent-runtime.claude-sdk     ix.v1.sensor.activation     matrix.v1.agent-response
```

- `/` is not part of the grammar.
- One of slick's component-kind names (`capability`, `skill`, `flow`, `agent`) is never a
  whole segment.
- `slick.*` is reserved: `type_url` refuses to mint into it, and `check_type_url` (so the
  registry) refuses it.
- A URL matrix mints carries one version segment (`v1`, `v1alpha1`, `v2beta1`) after at least
  one namespace segment and before the resource.

A **kind**, in matrix, is the type URL of some data. `wire` and `Registry.register` hold
what they are given to the grammar and to the version segment: a kind or an entry without
`v<N>` is refused. `is_type_url` checks the grammar alone.

| Name | Signature | Behaviour |
|------|-----------|-----------|
| `is_type_url` | `(url: str) -> bool` | True if `url` is valid under the grammar |
| `check_type_url` | `(url: str, *, where: str = "") -> str` | Returns `url`, or raises `ConfigError` saying why it is invalid, prefixed with `where`. Refuses the `slick` namespace, and so does everything that checks a type URL (`parse_type_url`, the registry) |
| `parse_type_url` | `(url: str, *, where: str = "") -> TypeUrl` | Splits at the version segment; `ConfigError` if there is none |
| `type_url` | `(namespace: str, version: str \| int, resource: str) -> str` | Builds and validates; an `int` version becomes `v<N>`; refuses the `slick` namespace |
| `TypeUrl` | `NamedTuple(namespace, version, resource)` | `str()` joins it back |

```python
from matrix import ConfigError, is_type_url, parse_type_url, type_url

type_url("ix", 1, "sensor.activation")  # 'ix.v1.sensor.activation'
parse_type_url(
    "matrix.v1.agent-runtime.claude-sdk"
)  # TypeUrl(namespace='matrix', version='v1', resource='agent-runtime.claude-sdk')
is_type_url("matrix.v1/agent-runtime.mock")  # False: '/' is not part of the grammar
is_type_url("acme.v1.agent.reviewer")  # False: 'agent' is one of slick's kind names
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
| `span(name, **fields)` | an async context manager emitting `<name>.start` and `<name>.end` (`status`, `duration_ms`); OpenTelemetry gets only its number and boolean fields unless content is asked for ([the `otel` observer](#the-otel-observer)) |
| `sessions()` | the rows of `matrix.v1.session` the component has run since it last handed a row over, failed ones included: what the ledger entry of its next row will name. A row that should name its sessions itself reads them here. `()` outside a run |

`provide` is synchronous. When it returns, the row has been validated against the kind's
registered schema, appended to the Construct, and is durable in the run's store. It raises
`ContractError` for a kind the component does not provide or a row that fails the schema, and
`StoreError` when the store could not keep the row (the run then fails with kind `store`,
whatever the component does with the exception). Once the store has failed, every later
`provide` in the run raises `StoreError` too, so no call returns for a row the record lacks.

A name given to `emit`, `measure` or `span` is dotted lower-case with at least two segments,
and not under `matrix.*`; anything else is a `ContractError`. After the component has ended
every one of these calls is ignored, and the first is reported as `matrix.component.late`. A
`RunContext` built by hand (`RunContext("run", "name")`, for testing a component alone)
keeps nothing, but refuses what a run refuses: a bad event, measure or span name, a measure
`kind` outside `gauge`, `counter`, `histogram`, or a value that is not a number, each a
`ContractError`. So a component's unit test catches what the run would.

### `wire`

```python
wire(
    components: Mapping[str, Component] | Iterable[Component],
    *,
    inputs: Iterable[str] = (),
    schemas: Mapping[str, type[BaseModel]] | None = None,
    fingerprints: Mapping[str, str] | None = None,
) -> Wiring
```

- `components` maps each instance's name to the component, or is a sequence of components
  that each carry a `name` attribute. A name matches `^[A-Za-z0-9][A-Za-z0-9._-]*$`.
- `inputs` are the kinds the caller will supply to every run.
- `schemas` maps kinds to the models their rows must validate against.
- `fingerprints` maps names to a digest of what each component was built from; a later run
  reuses a component's rows only when its digest is the same (`Container.wire` sets them).

Raises `CompilationError` (kind `config`) whose message lists every problem, each also a
`Problem` in `error.problems` with the path of the component or kind it is about. The checks: names; every kind is a versioned type URL and none
is `matrix.v1.ledger`; a component declares `requires` and `provides` as collections and has a
`run`; a required kind is provided or is a run input; a kind has one provider, and a run input
has none; no component requires what it provides; no cycle, which is reported naming its
components and the kinds it runs through.

```python
from matrix import CompilationError, wire

try:
    wire({"one": One(), "two": Two(), "reader": Reader()})
except CompilationError as e:
    for problem in e.problems:
        print(f"{problem.path} ({problem.kind}): {problem.message}")
# components.two.provides (name_taken): kind 'acme.v1.a' is provided by one, two; a kind has one provider (two streams of the same shape are two kinds)
# components.reader.requires (unprovided): nothing provides 'acme.v1.b' and it is not a run input
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
| `kinds` | `tuple[str, ...]` | every kind a run has a table for: inputs, then provided |
| `order()` | `-> tuple[str, ...]` | the components in one valid order of work; a view, not the schedule |
| `fingerprints` | `Mapping[str, str]` | name to a digest of what the component was built from, when the caller knows it |

---

## Execution

### `Limits`, `ComponentLimits` and `Retry`

Frozen pydantic models: `matrix.limits` in config is a `Limits`. An invalid value raises
pydantic's `ValidationError` when built in Python, and is a problem at its key path when read
from config.

| Field | Default | Meaning |
|-------|---------|---------|
| `concurrency` | `None` | how many components run at once (`>= 1`); `None` leaves it to the wiring |
| `deadline_s` | `None` | bounds each component separately, from when it starts (`> 0`); each attempt has the whole of it |
| `grace_s` | `5.0` | how long a component has to return after a stop request before it is cancelled (`>= 0`) |
| `cleanup_s` | `30.0` | how long a cancelled component, or one deferred release, has to finish before it is abandoned (`> 0`) |
| `on_failure` | `"fast"` | `fast`: the first failure stops every running component and skips the rest. `isolate`: only components that depend on the failed one are skipped |
| `retry` | `Retry()` | the retry policy of every component; the default runs each once |
| `components` | `{}` | name to `ComponentLimits(deadline_s, grace_s, cleanup_s, retry)`; a field left `None` inherits the run's |

`limits.of(name)` is `(deadline_s, grace_s, cleanup_s)` for a component and
`limits.retry_of(name)` its `Retry`.

`Retry(attempts=1, backoff_s=1.0, multiplier=2.0, max_wait_s=60.0, errors=("transient",))`.
A component that failed is run again when its error matches `errors` (its kind,
`transient`, or its reason: `unavailable`, `rate_limited`, `timeout`), fewer than
`attempts` attempts have been made, the run has not been asked to stop, and the failed
attempt handed over no rows (a second attempt would add to the record, not replace what is
there). It waits `backoff_s * multiplier ** (n - 1)` after attempt `n`, or the
`retry_after_s` the error reported (an `AgentRuntimeError` carries the backend's
`Retry-After`; `0` means retry now), never more than `max_wait_s`. A wait ends early when the run stops. Each
retry is a `matrix.component.retry` event, and `ComponentEnding.attempts` counts them. The
field is `errors`, not `on`: YAML reads a bare `on` key as `true`.

### `Executor`

`Executor(observers: Sequence[Observer] = (), *, events: EventPolicy | None = None,
interrupts: Callable[[], AbstractContextManager[None]] = nullcontext, trace: Callable[[],
tuple[str, str] | None] | None = None)`. Stateless between runs; safe to share. `trace`
returns the trace and span ids (hex) of the tracing context current in the calling task, or
`None`; each session row records what it returned when the session ended.
`Container.executor()` passes one that reads OpenTelemetry's; without one, session rows carry
no trace ids. `events` bounds the event queue (see [Observers](#observers)).
`interrupts` is entered around each run in the task awaiting it. With
`interrupts=matrix.cancel_on_signals`, while a run is in flight a SIGTERM or SIGINT that
nothing else handles cancels the run, so a signalled run still releases; a host that
installed its own handler keeps it, and a second signal does what it always did.
`Container.executor()` passes it; an executor given none leaves signals alone.

```python
async Executor.run(
    wiring: Wiring,
    inputs: Mapping[str, Any] | None = None,
    *,
    limits: Limits | None = None,
    store: ConstructStore | None = None,
    run_id: str | None = None,
    reuse: Reuse | None = None,
) -> Run
```

1. `inputs` must hold exactly the declared run inputs, or `ConfigError` lists a problem for
   each missing (`inputs.<kind>`, `missing_key`) and unknown (`unknown_key`) one. Each maps a kind to one row, or to a list or tuple of rows. Each row is
   validated against its kind's schema, if one is registered, and copied: a row its schema
   refuses, or one that cannot be copied, is a `ContractError`.
   A name in `limits.components` that is not in the wiring is a `ConfigError`
   (`limits.components.<name>`, `name_unknown`). A `reuse` whose components depend on one
   that is not reused is a `ConfigError`. All of this is checked before anything runs.
2. With a `store`, the run's record is opened there. A run id that already exists is a
   `StoreError`, raised before anything runs. The inputs are then recorded, by `$input`,
   and the rows of each reused component, by that component, which ends `reused`. A reused
   component whose rows its kind's schema now refuses ends `failed` (reason `from run <id>`,
   none of its rows recorded) and is not run again; a reused component that depends on it is
   skipped, and `on_failure` decides the rest. When the store's writer keeps events (the JSONL store writes `events.jsonl`), it is attached as one
   more observer for the run.
3. Scheduling is by readiness: a component starts when the providers of every kind it requires
   have finished, subject to `limits.concurrency`. It gets `Inputs` for those kinds, each row
   a deep copy. A row that cannot be copied is a `ContractError`: rows must be data, not live
   handles.
4. Rows a component hands over with `ctx.provide` are recorded then. What it returns is
   recorded when it ends: every returned row is checked first, and if one fails none is
   recorded. A row of an undeclared kind, a return value that is not a mapping, or a row that
   fails its kind's schema (whatever its validator raises) is a `ContractError`. A row of a
   kind with a schema is recorded as an instance of the schema, whether it was handed over as
   one or as a dict; a row of a reused component is too, so its consumers get the type a fresh
   run gives them. A row of a kind without a schema is recorded as given.
5. A component that raises becomes a `ComponentError` (a `MatrixError` it raises keeps its own
   kind); a component past its deadline is a `ComponentError` saying so, with
   `details["reason"] == "timeout"`. A failure its retry policy covers is run again first.
   Each failure is recorded in `run.failures`, and `RunError` is raised from the first: its
   `kind` is `run_failed` when a component raised or broke its contract (`cause_kind` keeps
   `component` or `contract`), and the cause's own kind otherwise (`store`, `transient`, `auth`, `config`); its
   `fix` is the cause's. A component whose provider did not complete is `skipped`.
6. Stopping a component is one sequence, whatever the cause (`deadline`, `run_failed`,
   `cancelled`): `ctx.stopping` is set; the component has `grace_s` to return, and what it
   returns is recorded; then its task is cancelled; if it is still running `cleanup_s` later
   it is abandoned and its record says `abandoned`. A component that returns within the grace
   ends `stopped`, with the stop's `reason`: what it returned may be partial, and it is never
   reused. A component past its deadline is `failed` even when it returned within the grace.
   One that had already returned when the stop was requested is `completed`.
7. A store that cannot keep a row, or cannot finish the run, fails the run: `RunError` with
   kind `store`, whatever `on_failure` says. No row is appended to that record after the
   failed one, nor kept in the run's Construct: a later `ctx.provide` raises `StoreError`.
8. When the run ends, every release registered with `ctx.defer` is called, last registered
   first, an async one bounded by `cleanup_s`, whatever the run's status. A synchronous
   release runs on the event loop and is not bounded. A release that raises anything,
   `SystemExit` included, is recorded as failed and the rest are still called; one that is
   not an `Exception` is raised again once the record is finished. Then the store's record is
   finished. An error that
   escapes the executor itself (a defect, or a store's error that is not a `StoreError`)
   reaches the caller after the same steps, and the record says `failed`, never
   `completed`.

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
| `failures` | `list[ComponentFailure]` | `component`, `kind`, `message` per failed component. `component` is `$input` or `$store` when it was the record, not a component, that failed |
| `components` | `dict[str, ComponentEnding]` | name to how it ended: `status` (`ComponentStatus`: `completed`, `stopped`, `failed`, `cancelled`, `skipped`, `reused`), `reason` when it was stopped (`deadline`, `run_failed`, `cancelled`), skipped (`upstream: <name>`, `policy: fast`, `cancelled`, `store failed`), or reused or failed while being reused (`from run <id>`), `duration_ms` (`None` when it never started), `abandoned` when it ignored cancellation, and `attempts` |
| `reused_from` | `str \| None` | the run whose rows this one reused |
| `fingerprints` | `dict[str, str]` | name to a digest of what the component was built from, when the wiring knows it |
| `releases` | `list[ReleaseOutcome]` | `component`, `status`, `error` per deferred release, in the order called; `status` is `released`, `failed` or `abandoned` |
| `events_dropped` | `int` | events no observer received; in `document()`, and so in `run.json`: the record is finished after the queue is drained, so it counts them all |
| `rows(kind)` | `-> tuple[Any, ...]` | the rows of `kind` |
| `document()` | `-> dict` | the run as a `matrix.v1.run` document, without its tables |

`Run` and its three parts are pydantic models, exported from `matrix`. The parts are frozen,
and each `document()` is the dict `run.document()` holds for it (a key of `ComponentEnding`
is left out when it says nothing; `reused_from` and `fingerprints` appear in the run's
document when set). The component and run states are open vocabularies: a reader must
tolerate one it does not know.

### `Reuse`

`Reuse(run_id: str, rows: dict[str, dict[str, tuple[Any, ...]]])`: rows carried over from the
run `run_id`, by component and kind. `Container.reuse(run_id)` builds it from a record: each
component that completed there (`completed` or `reused`, never `stopped`; a `completed`
ending that carries a stop's `reason` is read as `stopped`), was built from the same
fingerprint, and whose providers are reused too. The fingerprint is a digest of the
component's type, config and each need it is handed (`Container.wire` above). `Container.run(reuse_from=...)` and `matrix run --from` use it.

### `process`

`await process.spawn(ctx, argv, *, grace_s=5.0, **options) -> asyncio.subprocess.Process`
starts `argv` in its own process group and defers killing that group with the run: SIGTERM,
then SIGKILL once `grace_s` has passed with the group still alive. `options` go to
`asyncio.create_subprocess_exec`. `ctx` is the component's `RunContext`, or anything with its
`defer` (`process.Deferring`). `await process.kill_group(proc, grace_s=5.0)` does the kill
directly; only the first call for a process signals. A child that moves itself to another
session is not reached.

`await process.start(argv, **options) -> asyncio.subprocess.Process` starts `argv` in its
own process group and defers nothing: ending it is the caller's, with `kill_group`. If the
caller is cancelled while the child is being started, the child is killed and the caller gets
the cancellation, even when the start itself failed. Use the
pair when a component starts and stops a child for each repetition of its work, so the run's
releases do not grow with the repetitions:

```python
server = await process.start(["my-server"])
try:
    await measure(server)
finally:
    await process.kill_group(server)
```

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
| `kinds()` | `tuple[str, ...]`: every table, `matrix.v1.session` among them (every run has one), the ledger's last |
| `ledger` | `tuple[LedgerEntry, ...]`: every row's entry, in the order rows were appended |
| `torn` | `tuple[LedgerEntry, ...]`: entries whose row never landed; always `()` for a live run |
| `kind in construct` | `True` if the kind has at least one row |
| `len(construct)` | number of rows |
| `Construct.restore(tables)` | a Construct from stored tables (`{kind: rows}`, the ledger's among them); `StoreError` if the record contradicts itself |

`append(kind, row, *, component, sessions=())` and `write_through(sink)` are the executor's.

### `LedgerEntry` and `LEDGER`

`LEDGER = "matrix.v1.ledger"`. `LedgerEntry` is a frozen pydantic model, registered as that
kind's payload: `seq: int` (the row's place in the run's order), `kind: str`, `row: int` (its
0-based position in the kind's table), `component: str` (who handed it over, or `$input`),
`time: datetime`, `sessions: tuple[int, ...]` (the rows of `matrix.v1.session` behind it: the
sessions its component ran since its previous hand-over; `()` by default). An entry is written
before the row it points at.

### `Session`, `SESSION` and `digest`

`SESSION = "matrix.v1.session"`: every run's table of agent sessions and model calls, one
`Session` row each, matrix's own like the ledger (no component may provide or require it).
`Session` is a frozen pydantic model; its fields, and how a reader joins a board row to its
sessions without matrix, are in [the data model](../explanation/data-model.md#the-session-table).
A fact the runtime did not report is `None`.

| Name | Signature | Does |
|------|-----------|------|
| `digest` | `(value) -> str` | `sha256:<hex>` over canonical JSON (keys sorted, no whitespace, UTF-8): the session row's `sent` |
| `agent_session` | `(response, *, agent, sent, runtime=None, drives=(), error=None) -> Session` | the session row of an agent session, from its `AgentResponse`. For a session that failed, `error` is the exception and `response` what it carried (`AgentRuntimeError.response`, or `None`): the row is `status: failed` with the error's `reason` and message. For one that was cancelled, `error` is the `CancelledError`: the row is `status: cancelled`, reason `cancelled` |
| `call_session` | `(call, *, sent, runtime=None, drives=(), error=None) -> Session` | the session row of a model call, from its `ModelCall`. For a call that failed, `error` is the exception and `call` what it carried (`ModelRuntimeError.call`, or `None`) |
| `call_sent` | `(request: ModelRequest) -> str` | the `sent` digest of a model call's request, as a model step computes it: of its prompt, system prompt, model and output schema |
| `record_session` | `(session) -> int \| None` | records it in the run of the component running in this task, durably and in ledger order; its row, or `None` outside a run. The rows that component hands over next name it |

A `BoundAgent` and a model step record their sessions themselves, the failed ones included. A
component that calls a `ModelRuntime` itself records its calls, failed or not:

```python
from matrix import ModelRequest, ModelRuntimeError, call_sent, call_session, record_session

request = ModelRequest(prompt="Rate this.", model="haiku")
sent = call_sent(request)
try:
    call = await runtime.call(request)
except ModelRuntimeError as e:
    record_session(call_session(e.call, sent=sent, error=e))
    raise
row = record_session(call_session(call, sent=sent))  # the row a reply can name
```

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
| `store.runs() -> list[str]` | run ids, oldest first. The JSONL store skips, and logs on `matrix.store`, a directory whose `run.json` cannot be read |
| `writer.events() -> Observer \| None` | optional: a sink for the run's events kept with its record; the executor closes it after the run |
| `store.location(run_id) -> str` | optional: where the run's record is, or would be |

A missing run is a `NotFoundError` naming those that exist. Anything a store cannot write or
read back is a `StoreError`; the executor turns any other exception a store raises into one.
The two optional members came later: a store without them keeps working, keeps no events,
and is located by the run id.

`JsonlConstructStore(root, *, fsync=False)` keeps a directory per run: `run.json`, one
`<kind>.jsonl` per table whose lines are the rows, and `events.jsonl`. `fsync=True` flushes
each row to the disk, and the run's directory when its files are made and when `run.json` is
renamed in. A write the disk cuts short is taken back before the `StoreError`. A run whose
writer, on this host, is gone is finished by the first `describe` or `load` that finds it so:
each file's cut-short last line moved aside to `<file>.torn`, and `run.json` replaced with
`status: interrupted`, the row counts and `torn`, so `interrupted` is on disk and every file
reads whole without matrix (`data-model.md`, "Keeping a run"). Each row is written as pydantic's `to_jsonable_python` makes it (nested
models, dataclasses, sets, tuples, datetimes); a row holding anything else is refused with a
`ContractError` naming where in the row, before anything is written for it, so the record
goes on. `MemoryConstructStore()` keeps runs in a dict, rows as given. Both are
registered at the `store` point (`matrix.v1.store.jsonl` with `root` and `fsync`,
`matrix.v1.store.memory`), and `matrix.store` in config chooses one; a package adds its own
by registering there.

---

## Agents

### `AgentDefinition`

What an agent is. Frozen pydantic model, unknown fields rejected.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `name` | `str` | required | `^[A-Za-z0-9][A-Za-z0-9._-]*$` |
| `description` | `str` | `""` | |
| `system_prompt` | `str` | `""` | |
| `model` | `str \| None` | `None` | Interpreted by the runtime: an SDK alias or model id for `claude-sdk`, a hardline registry name for `pydantic-ai`. `None` is the runtime's default |
| `tools` | `tuple[str, ...] \| None` | `None` | `None` is the runtime's default toolset; `()` is **no tools**, never collapsed into the default |
| `max_turns` | `int \| None` | `None` | `>= 1`; `None` is the runtime's default |
| `metadata` | `dict[str, Any]` | `{}` | Keys a source carried that no runtime interprets; kept, never read for behaviour |

### `AgentResponse`

The runtime-neutral record of one session: what a runtime returns. Frozen. A fact the
runtime did not report is `None`.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `content` | `str` | `""` | the session's reply: its final result, not every turn's text |
| `transcript` | `tuple[str, ...]` | `()` | each text block the agent wrote, in order |
| `tool_calls` | `tuple[dict, ...]` | `()` | `{name, input}` per call; a runtime that sees calls happen adds `id`, `started`, `ended` (ISO times) and `error`, and each becomes an `execute_tool` span |
| `stop` | `str \| None` | `None` | `completed`, or the limit reached (`max_turns`, `max_budget_usd`, ...). A limit is not a failure |
| `finish_reason` | `FinishReason \| None` | `None` | why the session's last reply ended, in the model call's vocabulary; `None` when the runtime does not say |
| `family` | `str \| None` | `None` | model family that answered (`claude`, `qwen`, ...), stamped by the runtime |
| `task` | `str \| None` | `None` | the task as sent; an `agent-step` records it, after substituting its inputs |
| `session` | `int \| None` | `None` | the position of the session's row in the run's `matrix.v1.session`, when a run recorded it (for a reused row, the run `reused_from` names); `None` outside a run |
| `tokens_input`, `tokens_output` | `int \| None` | `None` | `tokens_input` counts every input token, those read from and written to the prompt cache included |
| `cache_read_tokens`, `cache_write_tokens` | `int \| None` | `None` | how many of `tokens_input` the cache served, and wrote |
| `duration_ms` | `int \| None` | `None` | |
| `cost_usd` | `float \| None` | `None` | the cost the backend reported, when it reports one: a fact of the session, which matrix neither limits nor estimates |
| `num_turns` | `int \| None` | `None` | |
| `provider` | `str \| None` | `None` | who served it, by the GenAI conventions' provider names (`anthropic`), when known |
| `model` | `str \| None` | `None` | the model id or registry name that answered, when known: not the one the definition asked for |
| `native_finish_reason` | `str \| None` | `None` | the provider's own value, verbatim |
| `session_id` | `str \| None` | `None` | the runtime's id for the session |

`as_reply()` is the response as an agent step provides it: with `session` set, every field
from `tokens_input` down is `None`, since the session row holds it; the reply keeps the rest.
Without `session` nothing else holds them, and they stay.

### `AgentRuntime` (Protocol)

| Method | Signature | Contract |
|--------|-----------|----------|
| `check` | `(definition) -> None` | **Required.** Raises `ConfigError` for a definition the runtime cannot play. A runtime that plays everything implements it as a no-op |
| `run` | `async (definition, task: str) -> AgentResponse` | Returns a response whose `stop` says why the session ended, or raises `AgentRuntimeError` with a `reason`. Never an empty normal response for a failed session |

Three members are optional, read with `getattr`: `warnings` (problems a reader of its config
should see); `resolved`, JSON data for what the runtime settled when it was built, beyond
its own config, that decides how it answers, which composition digests into the reuse
fingerprint of every component that runs an agent on the runtime; and `usage_counted_by`, the
name of what already counts the inference tokens of the runtime's model calls (`pydantic-ai`
declares `hardline`). `BoundAgent` puts it on `matrix.agent.end`, and the `otel` observer then
adds the session's tokens to no counter; its span and its session row keep them.

A runtime that also logs what its `warnings` say logs it at WARNING on the logger named
`RUNTIME_LOGGER` (`matrix.runtime`), once per process, so a caller that builds a runtime per
session is not told once per session. A program that reports `warnings` itself quiets that
logger: the `matrix` command does, and so does ix.

### `Agent` (Protocol) and `BoundAgent`

`Agent` is what callers use: `name` and `async run(task: str) -> AgentResponse`.

`BoundAgent(definition, runtime, *, observers=(), runtime_url=None, drives=())` satisfies it.
Construction calls `runtime.check(definition)`, so a refusal happens at binding. Properties
`name`, `definition`, `runtime`. `run(task)` emits `matrix.agent.start` (with the `task` as
sent) and `matrix.agent.end`, and a `matrix.tool.start` / `matrix.tool.end` pair, at the
runtime's own times, for each tool call it timed. Inside a run it records the session in
`matrix.v1.session` (with `runtime_url` and `drives`, which composition passes) and returns the
response pointing at that row (`session`); outside one it records nothing.

### Built-in agent runtimes

Registered at the `agent-runtime` point. In config, under `matrix.agent_runtimes`, `type:`
takes the short name.

| `type` | Type URL | Config fields | Needs | Notes |
|--------|----------|---------------|-------|-------|
| `claude-sdk` | `matrix.v1.agent-runtime.claude-sdk` | `permission_mode` (`default`, `acceptEdits`, `plan`, `bypassPermissions`, `dontAsk`, `auto`; default `default`), `cwd`, `setting_sources` (each `user`, `project` or `local`; `[]` is hermetic), `plugins` (each `{type: local, path}`; a relative `path` resolved against `cwd`), `skills` (names, or `all`), `fallback_model`, `max_budget_usd` (the SDK's own option, passed as it stands: the SDK ends a session once it has spent that many USD, `stop: max_budget_usd`), `agents` (inline subagents by name, each `description`, `prompt`, `tools`, `disallowed_tools`, `model`, `skills`, `max_turns`, passed as the SDK's `AgentDefinition`) | `cwd` | Extra `[claude]`. The config is the whole of what a session can be given; an option outside it fails at its key. `check` refuses a definition whose `tools` leave out `Skill` when the runtime has `skills`, and any option the installed SDK's own dataclasses lack, with the remedy. An agent's `model` is an alias the Claude Code CLI knows (`haiku`, `sonnet`, `opus`) or a full model id (as `claude-sonnet-4-5`); unset, the CLI's default. `bypassPermissions` and `dontAsk` log a WARNING on `RUNTIME_LOGGER`, once per process, and are a `permissive` warning in every document the `matrix` command prints. Stamps `family="claude"`, `provider="anthropic"`, and times each tool call. The reply is the session's result, every text block its `transcript`. `model` is the one the SDK's `model_usage` names, or, when it names several, the one that wrote the final reply. `tokens_input` adds the cache's tokens to Anthropic's `input_tokens`, which leaves them out. Keeps the session id and the SDK's `stop_reason` (`native_finish_reason`, mapped to `finish_reason`). With OpenTelemetry active, hands the CLI the trace context current when the session starts (`TRACEPARENT`, `TRACESTATE`, through the SDK's `env`), so the CLI's spans join the run's trace under the session's `invoke_agent` |
| `pydantic-ai` | `matrix.v1.agent-runtime.pydantic-ai` | `models` (a hardline registry section; filled from `matrix.models` when absent), `default_model`, `tools` (tool name to `module:function`, a Python function imported when the runtime is built), `temperature`, `max_tokens` | `models` | A pydantic-ai `Agent` per session, its model a hardline row: every model call goes through hardline, with the row's retries, fallbacks, limits, record and telemetry. The definition's `system_prompt` is the agent's instructions; its `tools` choose from the runtime's (`None`: all, `()`: none); `max_turns` bounds its model calls (`stop: max_turns` when reached; pydantic-ai's own default bound otherwise). An agent's `model` is a hardline registry name; unset, `default_model`, then the registry's default. `check` refuses a tool the runtime does not have, a model the registry cannot resolve, and tools on a row (or a fallback) whose backend cannot call them. The reply is the run's output, every text the model wrote its `transcript`; each tool call is timed from the response that asked for it to the request that carried its result, `error` when the tool failed or its arguments were refused. `model`, `family`, `finish_reason` and `native_finish_reason` are those of the last call; usage is summed over every call; `num_turns` counts the calls; `session_id` is pydantic-ai's run id; no cost. A hardline failure is an `AgentRuntimeError` with its reason mapped, carrying what the session had spent (`response`). `usage_counted_by: "hardline"`. pydantic-ai's instrumentation is switched off on every agent. Has `aclose()`; hardline's connections in a loop close when the last session running there ends. Imports pydantic-ai and hardline only when built |
| `mock` | `matrix.v1.agent-runtime.mock` | `responses` (the whole task as sent, to a reply), `by_agent` (agent name to a reply), `default` (reply for an unmatched task and agent; `None` echoes `mock:<agent>:<task>`), `family` (default `mock`) | | A stand-in (`stand_in`). Offline. Plays every definition; an agent's `model` is recorded, never used. The first of `responses`, `by_agent`, `default` that matches replies. Each reply reports no cost (`cost_usd` is `None`): no provider reported one. Records `calls`, each `(agent, task)` |

Classes: `matrix.adapters._out.runtime.claude_sdk.ClaudeSdkRuntime`,
`matrix.adapters._out.runtime.mock.MockAgentRuntime`.

### Agent definition files

An `AgentDefinition` reads a Claude Code agent file as it stands, through the loader
(`matrix.load(AgentDefinition, path)`, or the `matrix.definitions` directories, read with
`load_each(..., name_from_file=True, suffixes=(".md",))`):

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

The model, not the reader, says what each key means: `system_prompt` takes the file's
`body` by alias, `max_turns` takes `maxTurns`, `tools` splits a comma string, and keys it does
not interpret go to `metadata`. Every problem names its key and its file; a name defined
twice, in one directory or across them, is a `name_taken` problem naming both files.

### `matrix.v1.agent-step`

A registered `component` that runs one composed agent. Constants:
`AGENT_STEP = "matrix.v1.agent-step"`, `AGENT_RESPONSE = "matrix.v1.agent-response"`,
`TASK = "matrix.v1.task"`.

`AgentStepConfig` (unknown keys rejected):

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `agent` | `str` | required | a composed agent's name |
| `inputs` | `dict[str, str]` | `{"task": "matrix.v1.task"}` | placeholder name to kind; the kinds are the step's `requires` |
| `task` | `str \| None` | `None` | template with `{placeholder}`s (`{{ }}` for literal braces). Each placeholder names an input and each input has one, checked when the step is built (`ConfigError`). `None` is allowed only with exactly one input, whose rows are the task |
| `response` | `str` | `matrix.v1.agent-response` | the kind the `AgentResponse` is provided as; one step per kind in a run |
| `output` | `str \| None` | `None` | a kind; the reply, which must then be one JSON document, is provided as one row of it |

It provides `response`, and `output` when set. The `AgentResponse` carries the task as sent
(`task`). A kind has one provider, so two agent steps in one run each name their own
`response`; two on the default are a `name_taken` problem at the second step's
`components.<name>.config.response`, whose fix says so. With `output`, ask for the JSON in the
agent's prompt or the task, and give the mock that JSON. Rows are rendered as text: one row as itself (a string
as is, a pydantic model as indented JSON, anything else through `json.dumps`), several rows as
a JSON list; a required kind with no rows is a `ContractError`. The `output` row is the JSON
document in the reply, either the whole reply or a fenced code block; a reply with no JSON
document is a `ContractError` quoting the start of the reply, with a `fix` that says how to
get JSON, and so is one that fails the kind's schema.

---

## Model calls

A model call is one request to one model: no loop, no tools, no turns. It has its own port,
record and step, beside the agent's.

### `ModelRequest`

What a caller asks. Frozen, unknown fields rejected.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `prompt` | `str` | required | |
| `model` | `str \| None` | `None` | a name the runtime resolves (a hardline registry name); `None` is its default |
| `system` | `str \| None` | `None` | |
| `output` | `type[BaseModel] \| None` | `None` | the structure the reply must have; the runtime validates the reply against it and returns it as `value`, or raises |

### `ModelCall`

The runtime-neutral record of one call. Frozen. A fact the runtime does not report is `None`,
never a constant.

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `text` | `str` | `""` | the reply as the model wrote it |
| `value` | `Any` | `None` | the reply validated against the request's `output`, as JSON data |
| `finish_reason` | `FinishReason \| None` | `None` | why the reply ended: `stop` (the model ended its turn), `length` (cut at `max_tokens`), `content_filter` (the provider withheld or cut it), `refusal`, `tool_call`, `other`; `None` when the runtime does not say |
| `native_finish_reason` | `str \| None` | `None` | the provider's own value, verbatim |
| `refusal` | `str \| None` | `None` | what the model said when it refused |
| `name` | `str \| None` | `None` | the registry name of the model that answered |
| `model` | `str \| None` | `None` | the provider's id of the model that answered |
| `family` | `str \| None` | `None` | the family that answered (`claude`, `qwen`, ...) |
| `backend` | `str \| None` | `None` | the backend that served it |
| `provider` | `str \| None` | `None` | by the GenAI conventions' provider names (`anthropic`), when known |
| `usage` | `TokenUsage \| None` | `None` | `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, summed over every backend call that reported usage |
| `request_id` | `str \| None` | `None` | the provider's id for the request that answered |
| `attempts` | `int \| None` | `None` | calls made for a structured reply: 1, plus each repair |
| `retries` | `int \| None` | `None` | transient failures retried, across every attempt and model |
| `fallback_from` | `str \| None` | `None` | the model asked for, when a fallback answered instead |
| `latency_ms` | `int \| None` | `None` | |
| `params` | `RequestParams \| None` | `None` | what was sent to the model that answered: `max_tokens`, `temperature`, `structured` (how a structure was asked for; `None` for a plain call) |
| `attempt_log` | `tuple[CallAttempt, ...] \| None` | `None` | every backend call made, in order: `name` (the row asked), `reason` (why it failed; `None` for the one that answered), `usage`, `latency_ms` |
| `turns_digest` | `str \| None` | `None` | `sha256:<hex>` of the turns the answering call was sent, schema instruction and repair turns included |
| `row` | `RegistryRow \| None` | `None` | the registry row that answered: `digest` (`sha256:<hex>` of its whole spec) and `rungs` (for each field a configuration set, the tier that set it) |
| `prompt` | `str \| None` | `None` | the prompt as sent; a `model-step` records it, after filling its inputs |
| `session` | `int \| None` | `None` | the position of the call's row in the run's `matrix.v1.session`, when a run recorded it (for a reused row, the run `reused_from` names); `None` outside a run |

`as_reply()` is the call as a model step provides it: with `session` set, every field the
session row holds (`native_finish_reason`, `name` through `latency_ms`, `params`,
`attempt_log`, `turns_digest`, `row`) is `None`; the reply keeps `text`, `value`,
`finish_reason`, `refusal`, `family`, `prompt` and `session`.

### `ModelRuntime` (Protocol)

| Method | Signature | Contract |
|--------|-----------|----------|
| `check` | `(model: str \| None) -> None` | **Required.** Raises `ConfigError` for a model name the runtime cannot resolve (an unknown one, or none where there is no default), so a step that names it is refused when it is built |
| `call` | `async (request: ModelRequest) -> ModelCall` | Returns the call's record, or raises `ModelRuntimeError` with a `reason`. With `output`, `value` is the validated reply, or it raises |

The optional members `warnings` and `resolved` are read as on an agent runtime; the `hardline`
runtime's `resolved` is its registry (`default` and each model's row, without `api_key`), read
from `matrix.models` or hardline's own tiers, and is digested into the fingerprint of every
component that calls it. A third, `aclose()`, is async and closes the connections the
runtime's calls opened in the running event loop; a connection can be closed only in its own
loop, so a model step defers it to the end of the run that made the calls. The runtime stays
usable after it. The `hardline` runtime has it.

### Built-in model runtimes

Registered at the `model-runtime` point. In config, under `matrix.model_runtimes`, `type:`
takes the short name.

| `type` | Type URL | Config fields | Needs | Notes |
|--------|----------|---------------|-------|-------|
| `hardline` | `matrix.v1.model-runtime.hardline` | `models` (a hardline registry section; filled from `matrix.models` when absent), `default_model`, `temperature`, `max_tokens` | `models` | Extra `[models]`. A request's `model` is a hardline registry name; unset, `default_model`, then the registry's default. `check` resolves it. With `output`, the call is hardline's `extract`, which repairs a reply that does not fit within its bound. Carries hardline's whole record of the call: why it ended, the model that answered, `fallback_from`, usage with cache tokens, the request id, attempts, retries, latency, `params`, `attempt_log`, `turns_digest` and `row`. A reply cut short or refused is returned and said for what it is. Reports tokens, no cost. Has `aclose()` |
| `mock` | `matrix.v1.model-runtime.mock` | `responses` (the whole prompt as sent, to a reply), `by_model` (model name to a reply), `default` (reply for an unmatched prompt and model; `None` echoes `mock:<model>:<prompt>`), `family` (default `mock`) | | A stand-in (`stand_in`). Offline. Accepts every model name. Validates a reply against the request's `output` as a real runtime does. A call reports one attempt, no retries, no tokens, its latency and `finish_reason: stop`, and no cost. Records `calls`, each `(model, prompt)` |

Classes: `matrix.adapters._out.runtime.model.HardlineModelRuntime`,
`matrix.adapters._out.runtime.mock.MockModelRuntime`.

### `matrix.v1.model-step`

A registered `component` that makes one call on a configured model runtime. Constants:
`MODEL_STEP = "matrix.v1.model-step"`, `MODEL_CALL = "matrix.v1.model-call"`,
`PROMPT = "matrix.v1.prompt"`. Needs `model_runtimes`, `schemas` and `model_runtime_entries`
(its runtime's type URL and what it drives, for the session row).

`ModelStepConfig` (unknown keys rejected):

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `runtime` | `str` | required | a name under `matrix.model_runtimes` |
| `model` | `str \| None` | `None` | a name the runtime resolves, checked with its `check` when the step is built; `None` is its default |
| `system` | `str \| None` | `None` | the system prompt, as written |
| `inputs` | `dict[str, str]` | `{"prompt": "matrix.v1.prompt"}` | placeholder name to kind; the kinds are the step's `requires` |
| `prompt` | `str \| None` | `None` | template with `{placeholder}`s, checked against `inputs` when the step is built, as an agent step's `task`. `None` is allowed only with exactly one input, whose rows are the prompt |
| `response` | `str` | `matrix.v1.model-call` | the kind the `ModelCall` is provided as; one step per kind in a run |
| `output` | `str \| None` | `None` | a kind; the reply, provided as one row of it |

It provides `response`, and `output` when set. It records the call in `matrix.v1.session`,
whether or not its reply is whole, and the `ModelCall` it provides is the reply row
(`as_reply()`): the prompt as sent (`prompt`), the reply, and `session`, the call's row. With `output`, when the registry holds a schema for the kind, the request names it
as `output` and the row is the validated `value`; without one, the reply must be one JSON
document, as an agent step's, and a reply with none is a `ContractError` quoting it. Rows are
rendered as an agent step renders them. A model step emits no agent events: its call is a
`matrix.model.start` / `matrix.model.end` pair, and the `otel` observer's `matrix.model`
span, current while the runtime works.

A reply that is not a whole answer is never provided: one with a `refusal`, a `finish_reason`
other than `stop` and `None`, or empty `text`. The step raises `ModelRuntimeError`, reason
`refused` for a refusal or `content_filter`, `failed` for the rest (`length`, `tool_call`,
`other`, empty), with `finish_reason` and `native_finish_reason` in its details and a fix for
that reason; both reasons are kind `component`, not retried. A runtime that does not say why
its reply ended (`finish_reason` `None`) is not taken to have cut it.

---

## The registry

### Extension points and entries

Points matrix declares: `agent-runtime`, `model-runtime`, `component`, `payload-type`,
`observer`, `store`. Other packages
declare their own with `add_point`, named under their own root (ix declares `ix.sensor`).

`Entry` (frozen):

| Field | Type | Meaning |
|-------|------|---------|
| `point` | `str` | |
| `type_url` | `str` | |
| `build` | `Callable` | called as `build(validated_config, **needs)`, or `build(**needs)` when `config` is `None`. For `payload-type`, the model itself |
| `config` | `type[BaseModel] \| None` | the model its settings validate against |
| `needs` | `frozenset[str]` | shared things composition must hand it: `agents`, `model_runtimes`, `model_runtime_entries` (each composed model runtime's registry entry, by name), `schemas` (kind to its registered model), `models`, `cwd`, keys of `compose(context=...)`, and for a component `name`, its name in the run |
| `summary` | `str` | |
| `stability` | `"stable" \| "beta" \| "experimental"` | default `beta` |
| `origin` | `str` | the distribution that registered it; `(direct)` for a direct call |
| `stand_in` | `bool` | whether it stands in for the real thing (the mock runtimes): its answers are canned, not a model's. Declared, never inferred. Default `False` |
| `drives` | `tuple[str, ...]` | the distributions it drives, by their installed names: `claude-sdk` drives `claude-agent-sdk`, `pydantic-ai` drives `pydantic-ai-slim` and `hardline`, the `hardline` model runtime `hardline`. What a record of the versions behind a run reads, rather than a table of runtime names. Default `()` |

`entry.describe()` returns all of it as a JSON-ready dict, with `config` as a JSON schema.

`Failure(*, extension, origin, error)` records an extension that failed to load or a
refused registration: `extension` is its entry point's name, `origin` its distribution.

`Extension(*, name, origin, register)` is one extension to load: `register(registry)` is
called with the registry, and anything it raises, importing included, quarantines it.

### `Registry`

| Method | Signature | Behaviour |
|--------|-----------|-----------|
| `add_point` | `(point, *, port=None) -> Registry` | Declares an extension point, owned by the distribution declaring it; with `port`, a Protocol whatever is registered there must keep. `ConfigError` when another distribution owns the point already |
| `register` | `(point, type_url, build, *, config=None, needs=(), summary="", stability="beta", stand_in=False, drives=()) -> Registry` | `ConfigError` for an undeclared point, an invalid type URL or one without a version segment, a class `build` that does not keep its point's port (naming each member it lacks), or a type URL already registered at that point (naming both origins), unless namespace ownership resolves it (see below) |
| `register_payload` | `(type_url, model, summary="") -> Registry` | Registers a pydantic model at `payload-type`; summary defaults to the first line of the model's docstring |
| `entry` | `(point, type_url) -> Entry` | `NotFoundError` listing what is registered at that point |
| `entries` | `(point=None) -> list[Entry]` | Sorted by point, then type URL |
| `__contains__` | `((point, type_url)) -> bool` | |
| `create` | `(point, type_url, config=None, *, needs=None, where="<config>") -> Any` | Validates `config` and builds the entry with the needs it declared. `NotFoundError` for an unregistered type URL; `ConfigError` for a missing need, for config given to an untyped entry, for invalid config (each problem as a key path under `where`), or when what it built does not keep its point's port |
| `payload_schemas` | `() -> dict[str, type[BaseModel]]` | Every `payload-type` entry that is a pydantic model |
| `load` | `(extensions: Iterable[Extension]) -> Registry` | Loads the extensions, in name order (see below) |

Attributes: `points: set[str]`, `ports: dict[str, type]` (point to the Protocol its entries
keep: `agent-runtime` to `AgentRuntime`, `model-runtime` to `ModelRuntime`, `store` to
`ConstructStore`, `observer` to `Observer`), `owners: dict[str, str]` (point to the
distribution that declared it: `matrix` for matrix's own, `(direct)` for one declared by the
code composing the registry), `failures: list[Failure]`. A port is held by its required
members: each must be there and callable, and one the port awaits must be a coroutine
function; a port's optional members (an observer's `around`, a store's `events` and
`location`) are not required. A point under a distribution's name (`ix.subject`) belongs to
that distribution whoever declared it first: an impostor that did is quarantined whole, as for
type URLs. Any other point belongs to the distribution that declared it first. Another that
declares an owned point again, with or without a port, is refused and quarantined whole; the
owner, and code composing the registry directly, may.

`load_extensions(registry, group="matrix.extensions") -> Registry` loads every entry point in
the group into `registry`: each entry point's value is `register(registry) -> None`.

`load` rules:

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
`matrix.extensions` entry point loaded. `discover=False` loads only matrix's built-ins, under
the origin `matrix`.

`agent_runtime_type_url(name)` maps `claude-sdk` to `matrix.v1.agent-runtime.claude-sdk`;
`model_runtime_type_url(name)` maps `hardline` to `matrix.v1.model-runtime.hardline`;
`observer_type_url(name)` maps `otel` to `matrix.v1.observer.otel`. A name containing `.` is
returned unchanged.

Matrix's own entries:

| Point | Type URL | Needs |
|-------|----------|-------|
| `agent-runtime` | `matrix.v1.agent-runtime.claude-sdk` | `cwd` |
| `agent-runtime` | `matrix.v1.agent-runtime.pydantic-ai` | `models` |
| `agent-runtime` | `matrix.v1.agent-runtime.mock` (stand-in) | |
| `model-runtime` | `matrix.v1.model-runtime.hardline` | `models` |
| `model-runtime` | `matrix.v1.model-runtime.mock` (stand-in) | |
| `component` | `matrix.v1.agent-step` | `agents` |
| `component` | `matrix.v1.model-step` | `model_runtimes`, `schemas`, `model_runtime_entries` |
| `payload-type` | `matrix.v1.agent-response` | |
| `payload-type` | `matrix.v1.model-call` | |
| `payload-type` | `matrix.v1.task` | |
| `payload-type` | `matrix.v1.prompt` | |
| `payload-type` | `matrix.v1.ledger` | |
| `payload-type` | `matrix.v1.ref` | |
| `observer` | `matrix.v1.observer.console` | |
| `observer` | `matrix.v1.observer.jsonl` | |
| `observer` | `matrix.v1.observer.otel` | |
| `store` | `matrix.v1.store.jsonl` | |
| `store` | `matrix.v1.store.memory` | |

A third-party extension:

```toml
[project.entry-points."matrix.extensions"]
acme = "acme.matrix_ext:register"
```

```python
from pydantic import BaseModel, ConfigDict


class Review(BaseModel):
    """A review verdict."""

    verdict: str
    score: int


class ScorerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")  # an unknown key is refused, by its path

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
matrix emits for a span of work shares a `span`; an event a component emits with `ctx.emit`
is one event, whatever its name ends with. An agent called from inside a component reports
that component's span as its `parent` and its run id as its `run_id`; called outside a run,
both are `None`.

| Event | `span` / `parent` | `fields` |
|-------|-------------------|----------|
| `matrix.run.start` | run id / none | `components` |
| `matrix.run.end` | run id / none | `status`, `rows`, `duration_ms` |
| `matrix.component.start` | new id / run id | |
| `matrix.component.stopping` | the component's / run id | `reason` (`deadline`, `run_failed`, `cancelled`) |
| `matrix.component.end` | the component's / run id | `status`, and when they apply `reason`, `duration_ms`, `kind`, `abandoned`, `attempts`. Exactly one per component; a skipped component has no start |
| `matrix.component.late` | new id / run id | `call`: the context method used after the component ended |
| `matrix.component.retry` | the component's / run id | `attempt` (the one about to start), `kind`, `error`, `wait_s` |
| `matrix.row.append` | new id / run id | `kind`, `row`, `seq`: the row's ledger pointer and order |
| `matrix.release.start` | new id / run id | |
| `matrix.release.end` | same / run id | `status` (`released`, `failed`, `abandoned`), `error` |
| `matrix.agent.start` | new id / the calling span | `agent`, `model`, `task` (the task as sent) |
| `matrix.agent.end` | same / the calling span | `agent`, `duration_ms`, then `status` (`stop`), `tokens_input`, `tokens_output`, `cost_usd`, `model`, `family`, `provider`, `tool_calls`, and `usage_counted_by` when the runtime declares it; or `status="error"`, `error`, `reason`, `kind`; or, when the session was cancelled, `status="cancelled"`, `reason="cancelled"` |
| `matrix.model.start` | new id / the calling span | `runtime` (the step's model runtime), `model` (as asked; `None` for the runtime's default) |
| `matrix.model.end` | same / the calling span | `runtime`, `duration_ms`, then `status="completed"`, `name`, `model`, `family`, `provider`, `finish_reason` (the model that answered); or `status="failed"`, `error`, `reason`, `kind`; or `status="cancelled"`, `reason="cancelled"`. No usage: the session row holds it |
| `matrix.tool.start` | new id / the agent's span | `agent`, `tool`, `call_id`; timed when the runtime saw the call start |
| `matrix.tool.end` | same / the agent's span | `agent`, `tool`, `call_id`, `status` (`ok`, `error`), `duration_ms`; timed when the runtime saw its result |
| `matrix.measure` | new id / the calling span | `name`, `value`, `unit`, `kind`, `attrs` |
| `matrix.events.dropped` | new id / none | `count` |
| `<name>.start`, `<name>.end` | from `ctx.span` | the caller's fields; the end has `status` (`ok`, `error`) and `duration_ms` |

Events about a component carry its name in `component`.

### `Observer` (Protocol), `EventPolicy` and `RecordingObserver`

`Observer` is `on_event(event: Event)`, a plain function or a coroutine function, and one
optional member, read with `getattr` so an observer without it works unedited:

```python
def around(self, event: Event) -> AbstractContextManager[Callable[[Event], None] | None] | None: ...
```

matrix enters `around` **synchronously, in the task doing the work**, around each span of
work it starts: a run, a component (all its attempts), a `ctx.span`, an agent session, a
model step's call, a tool call a runtime timed, and a deferred release. `event` is the
span's start event. What entering it gives back, when callable, is handed the span's end
event before the block exits. The start and the end also reach `on_event` through the queue.
This is how the `otel` observer makes its span current, so a span a library opens inside the
work nests under it, and ends every span it opened whatever the queue drops. Since it runs in
the work's task, `around` must not block; what it raises entering, ending or leaving is logged
and skipped, and it cannot swallow the work's own exception. It is the one part of an observer
that can slow a run.

Events are delivered by a dispatcher task from a bounded queue, never on the emitting task.
`EventPolicy(queue: int = 4096, drain_s: float = 10.0)` sets the bound, and how long the
executor waits at the end of a run for the queue to empty. What follows from that, for `on_event`:

- emitting never waits. When the queue is full the event is dropped and counted; the next
  event delivered is preceded by `matrix.events.dropped`, and `Run.events_dropped` (and
  `run.json`'s `events_dropped`) has the
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
is going. A file is opened, with its folders, when the first event arrives (building the
observer touches nothing, so `matrix check` and `matrix plan` write nothing), and closed once a
run's `matrix.run.end` is written; the next event opens it again. `closed` says whether it
holds the file open; `close()` closes it now.

### The `otel` observer

`matrix.v1.observer.otel`. Attached by `observers: [otel]`, and to every composed container
when the standard `OTEL_*` variables ask for export (below). It speaks only the OpenTelemetry
API, a dependency of matrix, which does nothing until an application installs an SDK.
`OtelObserver(*, tracer_provider=None, meter_provider=None, capture_content=False)` takes
providers of the caller's own; by default it uses the global ones. Instrumentation scope
`matrix`, with matrix's version. Config: `capture_content` (default `false`), as in
`observers: [{type: otel, capture_content: true}]`; the observer the `OTEL_*` variables
attach takes the default.

**No content leaves by default.** Span attributes come from an allowlist:

- the conventions' names in the table below;
- matrix's own fields that carry no content, as `matrix.<field>`: `status`, `reason`,
  `kind`, `abandoned`, `attempts`, `rows`, `duration_ms`, `reused_from`, `family`,
  `tool_calls`, `cost_usd`, `usage_counted_by`, a model call's `runtime`, `name` and
  `finish_reason`, and the ids
  `matrix.run.id` and `matrix.component`;
- of the fields a component passes to `ctx.span`, the numbers and booleans, under its root
  (`acme.rows`), and the `ok`/`error` status matrix gives the span at its end
  (`acme.status`). A string can hold anything, so a component's string field is content.

Content is an agent's `task`, any `error` message, and a component's string fields. It
leaves only with `capture_content`, as `matrix.task`, `matrix.error` and `<root>.<field>`;
and only then is an error's message a span's status description, which is otherwise its
`error.type`. `error.type` is set on every failure either way. The GenAI conventions ask
for exactly this: instrumentations "SHOULD NOT capture them by default, but SHOULD provide
an option for users to opt in" (`docs/gen-ai/gen-ai-spans.md`, "Full (buffered) content").
They name `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` only as an example, not as a
defined variable, so matrix takes the opt-in from its config, not from the environment.

A `ctx.measure`'s `attrs` are exported as given, strings included: they are the dimensions
the component chose for that metric.

**Conventions.** The OpenTelemetry GenAI semantic conventions, as read on 2026-10-19 from
their repository, `open-telemetry/semantic-conventions-genai`: no release tagged there yet,
every name at status Development, building on semantic conventions v1.44.0. The repository
gives no schema URL yet, so matrix's tracer sets none. When the conventions are released,
the names here are re-checked against that release.

Spans, one per span of work, opened through `around` (above): in the task doing the work,
**current** there while it goes on, and timed by the events' own times. So a span a library
opens through the OpenTelemetry API inside the work (an instrumented HTTP client, a model
runtime's own spans) is its child, in the run's trace, and the run's span is the child of
whatever span was current where the run was awaited. A span ends with its work, from the end
event matrix hands `around`: an end event the queue drops, or a queue the run leaves before it
drained, leaves no span open. `on_event` makes metrics only, and an event a component emits
with `ctx.emit` opens no span, whatever its name ends with.

| Span | Kind | Attributes |
|------|------|------------|
| `invoke_workflow` (a run) | INTERNAL | `gen_ai.operation.name=invoke_workflow`, `matrix.run.id`, `matrix.status`, `matrix.rows`, `matrix.duration_ms`, `matrix.reused_from`; `error.type` when it failed or was cancelled |
| `matrix.component` | INTERNAL | `matrix.component`, `matrix.status`, `matrix.reason`, `matrix.duration_ms`, `matrix.attempts`, `matrix.abandoned`; `matrix.kind` and `error.type` (the error's kind, or the stop's reason for one cancelled) when it failed or was cancelled. The conventions have no step |
| `matrix.release` | INTERNAL | `matrix.component`, `matrix.status`; `error.type` when it failed. With content: `matrix.error` |
| `invoke_agent {agent}` | INTERNAL: the agent is invoked in this process | `gen_ai.operation.name=invoke_agent`, `gen_ai.agent.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.provider.name`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `matrix.component`, `matrix.status`, `matrix.duration_ms`, `matrix.family`, `matrix.tool_calls`, `matrix.cost_usd`; `matrix.reason`, `matrix.kind` and `error.type` (the runtime's `reason`) when it failed, and `error.type=cancelled` when the session was cancelled. With content: `matrix.task`, `matrix.error` |
| `matrix.model` (a model step's call) | INTERNAL | `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.provider.name`, `matrix.component`, `matrix.runtime`, `matrix.name` (the registry row that answered), `matrix.family`, `matrix.finish_reason`, `matrix.status`, `matrix.duration_ms`; `matrix.reason`, `matrix.kind` and `error.type` when it failed or was cancelled. With content: `matrix.error`. The conventions have no operation for one logical call, which may span retries and fallbacks; it carries no usage, since each attempt's inference span is the model runtime's own and nests under it |
| `execute_tool {tool}` | INTERNAL | `gen_ai.operation.name=execute_tool`, `gen_ai.tool.name`, `gen_ai.tool.call.id`, `gen_ai.agent.name`, `matrix.status`, `matrix.duration_ms`; `error.type=tool_error` when the tool reported an error |
| a component's `ctx.span(name)` | INTERNAL | `matrix.component`; its number and boolean fields and its status under its root (`acme.rows`, `acme.status`, `acme.duration_ms`). With content: its string fields too |

Metrics, derived from the same events, so an event log replays into the numbers an exporter
saw. The conventions' names where they define one:

| Instrument | Kind, unit | Attributes |
|------------|------------|------------|
| `gen_ai.invoke_workflow.duration` | histogram, s | `gen_ai.operation.name`, `matrix.status`, `error.type` |
| `gen_ai.invoke_agent.duration` | histogram, s | `gen_ai.agent.name`, `gen_ai.provider.name`, `gen_ai.response.model`, `error.type` |
| `gen_ai.invoke_agent.tool_calls` | histogram, `{tool_call}` | `gen_ai.agent.name`, ... |
| `gen_ai.execute_tool.duration` | histogram, s | `gen_ai.tool.name`, `gen_ai.agent.name`, `error.type` |
| `gen_ai.client.inference.usage.input_tokens`, `..._output_tokens` | counter, `{token}` | `gen_ai.agent.name`, `gen_ai.provider.name`, `gen_ai.response.model`, `gen_ai.token.modality=unknown` (a session's total mixes whatever it exchanged). Agent sessions only: see below |

and matrix's own where they define none: `matrix.component.duration` (histogram, s),
`matrix.component.count`, `matrix.component.retries`, `matrix.agent.cost_usd` (USD, as runtimes reported it),
`matrix.events.dropped`, each by `matrix.component` and `matrix.status` where they apply;
and the name given to each `ctx.measure`, of the measure's kind, with its scalar `attrs` and
`matrix.component`.

**One owner per token.** matrix's inference counters count agent sessions only, whose tokens
no model runtime's call carries. A model call's tokens are counted by the model runtime that
made it (hardline counts each attempt, on the same instruments), never by matrix: the call's
session row holds them, and the `matrix.model` span carries no usage. An agent runtime whose
model calls are counted where they are made declares it (`usage_counted_by`, which
`pydantic-ai` sets to `hardline`): its sessions' tokens stay on their `invoke_agent` span and
session row, and are added to no counter here. So nothing is counted twice, and no runtime is
told apart by its name.

### `configure_telemetry` and the `OTEL_*` variables

OpenTelemetry's rule holds: a library uses the API, an application installs the SDK.
Composing, wiring and running never install a provider. An application entry point does:

```python
configure_telemetry(*, service_name=None, endpoint=None, console=False) -> Telemetry
```

`matrix run` calls it, and so may a tool built on matrix; a program that configures
OpenTelemetry itself does not. With no arguments the standard variables decide:

| Variable | What matrix does |
|----------|------------------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` (and `_TRACES_`, `_METRICS_` forms) | set: export is asked for; the OTLP exporters read it |
| `OTEL_TRACES_EXPORTER`, `OTEL_METRICS_EXPORTER` | `otlp` (the default once export is asked for), `console` (to stderr: stdout is the command's document), `none`; a comma list; another value is a warning |
| `OTEL_EXPORTER_OTLP_PROTOCOL` (and `_TRACES_`) | `http/protobuf` (the spec's default) or `grpc`; another value is a warning |
| `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES` | the resource; `service.name` is `matrix` when neither sets it (or `service_name`, which wins) |
| `OTEL_SDK_DISABLED` | `true`: nothing is installed and no observer attached |

When the variables ask for export, composition also attaches the `otel` observer to every
container. `endpoint` exports OTLP there and `console=True` to stderr, whatever the variables
say; without `[otel]` either raises `ConfigError` naming the install. When only the variables
asked and `[otel]` is missing, nothing is exported and the returned `Telemetry`, the
container's `warnings` and every `--json` document carry an `extra_missing` warning.

`Telemetry`: `enabled`, `exporters` (`traces:otlp`, ...), `warnings`, and `shutdown()`, which
flushes and stops what was installed. Call it when the work is done.

**With nothing configured**, a run recorded by the JSONL store writes its events into its
record, `events.jsonl`: the local sink.

---

## Loading documents

One loader reads every document matrix reads, and any document a caller names a pydantic type
for. A **source** reads plain data and knows nothing of types; the **loader** validates it as
the caller's model, once, and reports every problem.

```python
load(model: type[M], location) -> M
load_each(model: type[M], *locations, key="name", name_from_file=False,
          suffixes=(".yaml", ".yml", ".md")) -> tuple[M, ...]
load_config(client_type=None, client_key="matrix", sources=None, *, loader=None) -> Config
```

- A **location** is a path (`str` or `os.PathLike`), a folder or file inside an installed
  package (`importlib.resources.files("acme") / "kinds"`), or an open text stream
  (`sys.stdin`), which is read as YAML.
- A **YAML file** is its mapping as it stands. A **markdown file** (`.md`, `.markdown`) is its
  frontmatter mapping with the text after the frontmatter, exactly as written, under the key
  `body`; with no frontmatter it is all body. What `body` means is the model's business: a
  field takes it by alias (`prompt: str = Field("", alias="body")`).
- `load` reads one document. Nothing there is a `NotFoundError`; a folder is a problem.
- `load_each` reads every file whose suffix is in `suffixes`, in each folder, in file-name
  order (a file is a folder of one). `key` names the field two documents may not share: a
  repeat is a `name_taken` problem naming both files. `name_from_file` fills `key` from the
  file's stem where a document leaves it out. One unreadable file hides nothing.
- `load_config` merges the tiers (below), the `matrix:` section and the tool's validated in
  one pass, so both sections' problems arrive together.

Every problem is a `Problem` with the key `path` dotted from the document's root, a `kind`,
the `message`, a `fix`, `allowed` where the set is closed, `got` when what was written is a
scalar, and `source`, the file. Pydantic's errors map to the agreed kinds: an unknown key is
`unknown_key`, a missing one `missing_key`, a value of the wrong type `wrong_shape`, a value of
the right type the schema refuses `invalid_value`, a `schema` literal it does not read
`schema_unsupported`; a file that does not parse is `yaml_syntax`. All arrive in one
`ConfigError` (exit 3); `complete` is false when a file could not be read, since what it would
have set was never checked.

**Markers.** Two field annotations change how a value is placed, never what it means:
`BesideFile()` resolves a relative path in the field (a string, or each string of a list)
against the folder of the file that wrote it (a default no file wrote stays as it is);
`Accumulate()` keeps a list from every tier
when merging, in tier order and each once, instead of the last tier's.
`matrix.definitions` carries both. They apply to a model's top-level fields.

**Any source.** `Loader(source)` validates what any `DocumentSource` reads:
`document(location) -> Document | None`, `documents(location, suffixes) -> tuple[Document,
...] | None` and `beside(document, path) -> str`. A `Document` is `source` (as messages show
it), `stem`, `data` (the mapping, or None) and `problem` (why there is none), and `folder`.
`Loader.one`, `Loader.each` and `Loader.merge(sections, locations)` are the three
operations; `load_config(..., loader=...)` and `compose(..., loader=...)` take one.

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

Schema defaults sit below tier 1; the files the command line names (`matrix plan CONFIG...`)
sit above tier 6, in the order named, and with `--only` they are read alone, none of the
tiers. Absent files read as empty. Every problem names the file that set the value it is
about.

Merge rules: mappings merge key by key, later tiers winning, so `agent_runtimes`,
`model_runtimes` and `agents` hold every tier's entries; lists replace, except `matrix.definitions`, whose directories
accumulate across tiers, each relative directory resolved against the file that declared it,
and a problem with one names the file that listed it. A tier that writes `definitions` as
anything but a list is a `wrong_shape` problem at its file, not overwritten by the others.
An agent defined in two of them is one `name_taken` problem at `matrix.definitions`, and an
agent that would use it is not checked (`complete` false).

### `MatrixConfig`

The `matrix:` section. Frozen, unknown keys rejected.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `definitions` | `tuple[str, ...]` | `()` | directories of `*.md` agent files |
| `agent_runtimes` | `dict[str, RuntimeConfig]` | `{}` | named agent runtimes: sessions with a loop and tools, what `agents` bind to |
| `model_runtimes` | `dict[str, RuntimeConfig]` | `{}` | named model runtimes: one call each, what a `model-step` names |
| `observers` | `tuple[str \| ObserverConfig, ...]` | `()` | each a short name (`console`, `jsonl`, `otel`) or type URL, or `{type, ...options}` for one that takes config |
| `agents` | `dict[str, AgentConfig]` | `{}` | named agents |
| `components` | `tuple[ComponentConfig, ...]` | `()` | the components of a run, each `{name, type, config}`. Their order of work is inferred; the order here means nothing |
| `inputs` | `tuple[str, ...]` | `()` | the kinds a caller supplies when it runs `components` |
| `store` | `StoreConfig` | `{type: jsonl}` | where runs are recorded: `type` (`jsonl`, `memory`, or a registered store's type URL) plus that store's options; `jsonl` keeps runs under `root` (default `.matrix/runs`, relative to the working directory) |
| `limits` | `Limits` | `Limits()` | the bounds and retry policy every run keeps; see [`Limits`](#limits-componentlimits-and-retry) |
| `models` | `dict \| None` | `None` | a hardline registry section (`{default?, models: {...}}`) handed to every `hardline` model runtime that has none. `None` lets hardline read its own tiers |

`RuntimeConfig` and `StoreConfig`: `type` (a built-in short name or a full type URL) plus that
extension's own options as extra keys, validated by its config model.

`ComponentConfig` (unknown keys rejected): `name` (this instance's name in the run), `type`
(the type URL of a registered `component`), `config` (that component's own settings,
validated by its config model).

`AgentConfig` (unknown keys rejected): `runtime` (required, a name under `agent_runtimes`;
one under `model_runtimes` is refused, with the fix: a model call is a `model-step`),
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
  agent_runtimes:
    sdk:   {type: claude-sdk, permission_mode: default, setting_sources: []}
  model_runtimes:
    local: {type: hardline}
  observers:
    - {type: jsonl, path: runs/events.jsonl}
  agents:
    reviewer: {runtime: sdk}                                    # agents/reviewer.md
  inputs: [acme.v1.diff]
  components:
    - {name: review, type: matrix.v1.agent-step,
       config: {agent: reviewer, inputs: {diff: acme.v1.diff}, task: "Review:\n{diff}"}}
    - {name: triage, type: matrix.v1.model-step,
       config: {runtime: local, model: qwen3-8b, inputs: {diff: acme.v1.diff},
                prompt: "Is this change risky? {diff}"}}
  store: {type: jsonl, root: runs}
  limits:
    deadline_s: 900
    retry: {attempts: 3, errors: [transient]}
mytool:
  trials: 3
```

### `Config[C]`

Frozen, generic over a tool's pydantic model: `matrix: MatrixConfig`, `client: C`,
`sources: tuple[str, ...]`, every file consulted (lowest priority first, absent ones marked
`(absent)`, empty ones `(empty)`), and `origins`, each key path's file.

### `load_config`

```python
load_config(client_type=None, client_key="matrix", sources=None, *, loader=None) -> Config
```

- `client_type`: the tool's pydantic model for its own section.
- `client_key`: the tool's name: its section key and the `<tool>` in the tiers.
- `sources`: locations lowest priority first (paths, package files, streams); `None` uses
  `discover_sources(client_key)`.
- `loader`: a `Loader` over another source.

Raises `ConfigError` with a problem for every bad key path (`matrix.agents.a.runtim`) in
either section, each naming the file that set it; `details["sources"]` lists every file
consulted.

```python
from pydantic import BaseModel

from matrix import compose, load_config


class MyToolConfig(BaseModel):
    trials: int = 5


config = load_config(MyToolConfig, "mytool")  # reads matrix: and mytool: from every tier
config.client.trials
container = compose(config)
```

`discover_sources(tool, project_root=None) -> list[Path]` returns the tier paths
(`project_root` defaults to the working directory); a set but missing `$MATRIX_CONFIG` or
`$<TOOL>_CONFIG` is a `ConfigError`. The variable is the tool's name upper-cased, with `-`
and `.` as `_`: `"my-tool"` reads `MY_TOOL_CONFIG`.

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
    definitions: Iterable[AgentDefinition] = (),
    context: Mapping[str, Any] | None = None,
    loader: Loader | None = None,
) -> Container
```

Builds, in order: observers (and `otel` when the variables ask for export), agent runtimes
and model runtimes (each resolved by type at its own point, options validated by its config
model), the store, definitions (every `*.md` in each `definitions` directory, read by the
loader, plus `definitions`), and agents (each agent runtime's `check` runs here). A model
step's model is checked by its model runtime when `Container.wire` builds the step. Makes no model or tool call,
and installs no telemetry provider.

- `registry` defaults to `default_registry()`.
- `base_dir` anchors relative `definitions` directories that `load_config` did not already
  resolve; defaults to the working directory.
- `source` is the key every problem's path starts with (`matrix.agents.triage.runtime`).
- `context` supplies shared needs, such as `{"cwd": "/path"}`, to extensions that declare them.

Every problem is reported at once, in one `ConfigError`, each with its key path, the legal
values and, for a config from `load_config`, the file that set it. An agent whose agent
runtime is configured but could not be built is not checked, and the error's `complete` is false. A
registry without matrix's built-ins has no default store: the container then has none unless
the config names one.

### `Container`

| Name | Returns |
|------|---------|
| `config`, `registry` | what it was composed from |
| `agent_runtimes`, `model_runtimes`, `definitions`, `agents` | name to object |
| `observers` | `tuple[Observer, ...]` |
| `store` | the `ConstructStore` `matrix.store` built; `ConfigError` when there is none |
| `limits` | `matrix.limits`, a `Limits` |
| `warnings` | `tuple[Problem, ...]`: a permissive runtime, export asked for without its extra |
| `agent(name)` | `BoundAgent`; `NotFoundError` listing configured agents |
| `agent_runtime(name)` | `AgentRuntime`; `NotFoundError` listing configured agent runtimes |
| `model_runtime(name)` | `ModelRuntime`; `NotFoundError` listing configured model runtimes |
| `bind(definition, runtime)` | a `BoundAgent` on the configured agent runtime named `runtime`, with the container's observers |
| `needs()` | the shared needs: `agents`, `model_runtimes`, `model_runtime_entries`, `schemas`, `models`, `cwd`, plus `context` keys |
| `wire(components=None, *, inputs=None)` | a `Wiring`. Each `{name, type, config}` entry (a `ComponentConfig` or a mapping) is built from the registry's `component` entries and the result wired, with the registry's payload schemas and each component's `fingerprint`: a digest of its type, its config and each need it is handed (an `agent-step`'s own agent, a `model-step`'s own model runtime, every agent or model runtime for another component that needs them, `models`, and `cwd`, the process's when none was given), where an agent counts by its definition, its runtime's config and needs, and the runtime's optional `resolved`, and a model runtime by its config, needs and `resolved` (the `hardline` runtime's registry, from `matrix.models` or hardline's own tiers). A need a host adds through `context` is not in it, nor are `schemas` or the code. Both arguments default to the config's `matrix.components` and `matrix.inputs`, and then problem paths start `matrix.`. Entries that cannot be built and wiring problems are reported together, in one `CompilationError`. A component that declares the need `name` is handed its own |
| `executor(observers=())` | an `Executor` with the container's observers (and these), given `cancel_on_signals`, and a `trace` that reads OpenTelemetry's current span for each session row |
| `async run(inputs=None, *, run_id=None, reuse_from=None, observers=())` | runs the configured components in the configured store within the configured limits; `run_id` defaults to `<UTC time>-<4 hex digits>`. `reuse_from` takes that run's inputs and reuses what it can |
| `reuse(run_id, wiring=None)` | the `Reuse` a run of `wiring` may take from the recorded run |
| `inputs_of(run_id, wiring=None)` | the run inputs that run recorded |
| `location(run_id)` | where the store keeps, or would keep, the run |

### What the `matrix` command prints

Each function returns a frozen record whose `document()` is the `--json` document of the
matching command; the command prints exactly that. None runs a component or calls a model.
Each that reads a config takes `tool` (whose tiers), `config` (a location, or several,
layered above them in order) and `only` (keyword: read the `config` locations alone, no
tier; `UsageError` when there are none).

| Function | Record (`schema`) | Raises |
|----------|-------------------|--------|
| `catalog(point=None, *, registry=None)` | `Catalog` (`matrix.v1.catalog`): `entries` (point, type_url, summary, stability, origin, needs, stand_in, drives, configurable) and `failures` (extension, origin, error) | `UsageError` when `point` is not a declared point, with the points in `details["allowed"]` |
| `describe_extension(type_url, *, registry=None)` | `Extensions` (`matrix.v1.extension`): one entry per point it is registered at, with its `config` JSON schema | `NotFoundError` listing the registered type URLs |
| `config_sources(tool="matrix", config=None, *, only=False)` | `ConfigSources` (`matrix.v1.config-sources`): `sources`, lowest priority first | `ConfigError` |
| `config_view(tool="matrix", config=None, *, only=False)` | `ConfigView` (`matrix.v1.config`): `tool`, `sources`, the merged `matrix` section | `ConfigError` |
| `check_config(tool="matrix", config=None, *, only=False)` | `CheckReport` (`matrix.v1.check`): `ok`, `agent_runtimes`, `model_runtimes`, `agents`, `observers` (a count), `components` in one valid order of work, `sources`, `warnings` | what `compose` and `wire` raise; `complete` is false when the config did not compose and has components to wire |
| `list_agents(tool="matrix", config=None, *, only=False)` | `Agents` (`matrix.v1.agents`): each agent's name, runtime, runtime_type, model, tools, description; `warnings` | what `compose` raises |
| `plan_config(tool="matrix", config=None, *, only=False)` | `Plan` (`matrix.v1.plan`): `tool`, `sources`, `inputs`, `stages` (what starts together), `components` (each `name`, `type`, `requires`, `provides`, `after`, `agent`, `runtime`, `model` (a model step's), `attempts`), `agents`, `agent_runtimes` and `model_runtimes` in use (each `name`, `type`, `stand_in`), `agent_calls_min` and `model_calls_min` (one per agent step, one per model step) and `agent_calls_max` and `model_calls_max` (one per attempt; null when a component that is neither needs `agents` or `model_runtimes`, and so may make sessions of its own), `store` (`type`, `location` with `RUN_ID` for the id), `limits` (what differs from the defaults), `warnings` | what `compose` and `wire` raise, and a problem for each `limits.components` name that is not a component |
| `list_runs(tool="matrix", config=None, *, only=False)` | `Runs` (`matrix.v1.runs`): `store` (its type URL) and `runs`, oldest first, each `run_id`, `status` (`interrupted` for one whose writer is gone), `started`, `ended`, `location`. Builds only the store: runtimes and agents are not built | `ConfigError` when the store cannot be built, `StoreError` when it cannot be read |

`registry` defaults to `default_registry()`.

---

## Errors

Every error matrix raises deliberately is a `MatrixError`. Each carries a `kind` (what the
caller can do about it), an `exit_code`, a `fix` (one actionable sentence, or None),
`problems` (each a `Problem`: what is wrong, at which key, in which file), `complete` (false
when checking stopped before every problem was found) and `details` (the error's own facts).
`payload()` renders the `matrix.v1.error` envelope, the shape the capability tools agree on:

```json
{"schema": "matrix.v1.error",
 "error": {"kind": "config", "message": "...", "fix": "...", "complete": true,
           "problems": [{"path": "matrix.agent_runtimes.m.type", "kind": "name_unknown",
                         "message": "...", "fix": "...", "allowed": ["mock"],
                         "got": "nope", "source": "./matrix.yaml"}],
           "details": {}}}
```

Every key is present; `fix` falls back to what to do next on the error's kind; `problems` and
`details` are empty when there are none.

Problem kinds: the agreed `yaml_syntax`, `schema_unsupported`, `wrong_shape`, `unknown_key`,
`missing_key`, `invalid_value`, `name_unknown`, `name_taken`; and matrix's own `unprovided` (a
required kind nothing provides), `cycle`, `extra_missing` and `permissive` (warnings carry
these two). A consumer tolerates a kind it does not know.

The exit codes are the ones the capability tools agree on, one kind per code:

| Kind | Exit | Meaning, and the next move |
|------|------|----------------------------|
| `unknown` | 1 | a failure matrix did not classify: a bug in matrix; report it |
| `usage` | 2 | a bad command, flag or argument: correct the command |
| `config` | 3 | a config, definition, wiring, input or argument is invalid: fix each problem |
| `not_found` | 4 | a name, type URL, file or run that is not there |
| `transient` | 5 | unavailable, rate-limited or timed out: retry later, or give the component a retry policy |
| `auth` | 6 | credentials missing or refused |
| `run_failed` | 8 | the run ran and a component failed: it raised or broke what it declares |
| `store` | 9 | the run's record could not be written or read: fix the store |
| `interrupted` | 130 | interrupted (Ctrl-C, SIGTERM) before it finished: run it again |

`EXIT_CODES` is this mapping, plus `ok` (0) for success; `EXIT_MEANINGS` says what each
means and `EXIT_NEXT` what to do next. Two error kinds are finer than an exit: `contract` and
`component` exit as `run_failed` (`EXITS_AS`). `ErrorKind` is the literal type of the kinds.

| Class | Kind | Also | Raised when |
|-------|------|------|-------------|
| `MatrixError` | `unknown` | `Exception` | base class |
| `UsageError` | `usage` | | the `matrix` command was given a command, flag or argument it does not take |
| `ConfigError` | `config` | `ValueError` | a config, definition, document, type URL, input or argument is invalid (every problem in `problems`); or what the config selects needs an extra that is not installed (an `extra_missing` problem whose `fix` is the `uv` install) |
| `CompilationError` | `config` | `ConfigError` | components cannot be wired; `problems` lists every problem |
| `NotFoundError` | `not_found` | `KeyError` | a name, type URL, file, kind with rows or run does not exist; the message lists those that do |
| `ContractError` | `contract` | | a row is of a kind its component does not provide, or fails its kind's schema; or a run input does |
| `ComponentError` | `component` | | a component raised or passed its deadline; the original exception is the cause |
| `StoreError` | `store` | | a row or a run could not be kept, a run id is taken, or a stored record contradicts itself |
| `AgentRuntimeError` | from `reason` | | an agent session failed |
| `ModelRuntimeError` | from `reason` | | a model call failed |
| `RunError` | `run_failed`, or the cause's | | a run failed; `run` is the partial `Run`, `component` the name of the first to fail, `cause_kind` its error's kind (`details["cause"]`), `construct` the partial Construct. `kind` is `run_failed` when the cause is `contract`, `component` or `unknown`, and the cause's kind otherwise (`store`, `transient`, `auth`, `config`); `fix` is the cause's |

`RunCancelled` is not a `MatrixError`: it is an `asyncio.CancelledError` (so asyncio and
callers treat it as the cancellation it is) with one addition, `run`, the partial `Run`. Its
`run.status` is `cancelled`, or `completed`/`failed` when the cancellation arrived as the run
was already ending.

`AgentRuntimeError(message, *, reason="failed", fix=None, retry_after_s=None, response=None,
**details)`, and `ModelRuntimeError` with the same arguments and `call=None` in place of
`response`, maps its `reason` (`RuntimeReason`) to a kind; a reason matrix does not know maps to
`component`. `retryable` is true for the transient ones; `retry_after_s` is the backend's own
hint, which a retry policy honours. `response` (an `AgentResponse`) and `call` (a `ModelCall`)
are what the runtime knew of the session or call that failed, when it knew anything (`hardline`:
every attempt and the usage they reported; `claude-sdk`: a failed result's id, usage, cost and
turns); the session row of the failure is built from them:

| `reason` | Kind | Meaning |
|----------|------|---------|
| `unavailable` | `transient` | backend down |
| `rate_limited` | `transient` | |
| `timeout` | `transient` | |
| `auth` | `auth` | credentials missing or refused |
| `incapable` | `config` | the runtime cannot honour the definition or the request (a model it cannot resolve), or a program it runs is not installed (the Claude Code CLI) |
| `refused` | `component` | the model declined |
| `failed` | `component` | the session or call ran and broke |

```python
from matrix import AgentRuntimeError

e = AgentRuntimeError("agent 'x': HTTP 429", reason="rate_limited", retry_after_s=20)
e.kind, e.retryable, e.exit_code  # ('transient', True, 5)
```

---

## The `matrix` command

Data goes to stdout, diagnostics to stderr. Nothing prompts.

| Command | Output (`--json` schema) |
|---------|--------------------------|
| `matrix catalog [--point P]` | every registered entry (point, type URL, stability, summary, origin, needs, whether configurable) and every failed extension (`matrix.v1.catalog`); a `--point` that is not a declared point is a `usage` error listing the points |
| `matrix describe TYPE_URL` | the full entry, including its config JSON schema (`matrix.v1.extension`); `not_found` if nothing is registered under it |
| `matrix config [CONFIG]... [--sources]` | the merged `matrix` section and the files consulted (`matrix.v1.config`); with `--sources`, only the files (`matrix.v1.config-sources`) |
| `matrix check [CONFIG]...` | composes runtimes and agents, wires the configured components, and reports what composed (`matrix.v1.check`) |
| `matrix agents [CONFIG]...` | each agent's name, runtime, runtime type, model, tools and description (`matrix.v1.agents`) |
| `matrix plan [CONFIG]...` | what a run would do, with its agent and model calls, every problem at once; runs nothing (`matrix.v1.plan`) |
| `matrix run [CONFIG]... [--input KIND=PATH]... [--run-id ID] [--from RUN_ID]` | runs the configured components; prints the run id, where it is recorded and, for a store that keeps files, each file of the record (`matrix.v1.run`, with `location`, `warnings` and the record's `tables`) |
| `matrix runs [CONFIG]...` | the runs the configured store keeps, oldest first: id, status, start, location (`matrix.v1.runs`) |
| `matrix --version` | `matrix, version X` |
| `matrix --skill [-r NAME]` | the skill an agent reads (`matrix.skill()`), or one of its references (`config`, `agents`, `running`, `telemetry`, `loading`) |

Every command that reads config takes `--tool T` (default `matrix`, whose tiers to read),
`CONFIG`, files layered above the tiers in the order named (`-` reads one from standard
input), and `--only`, which reads the `CONFIG` files alone, none of the tiers. `run` reads each
`--input` file as JSON lines, one row each (`-` is standard input; it can be read once). With
`--json`, each document carries its `warnings`, and an error, a usage error included, is the
`payload()` envelope on stderr; without it, warnings are `warning: <path>: <message>` lines
and an error is `error (<kind>): <message>`, then each problem (`<path>: <message>`, with its
`fix:` beneath it), then the error's own `fix: <fix>` when no problem already said it. A run
that fails prints its
`matrix.v1.run` document on stdout and the envelope on stderr. `matrix run` installs the
OpenTelemetry SDK when the `OTEL_*` variables ask for export, and on a terminal shows
progress on stderr. `matrix` with no command prints its help and exits 0.

Plan before you run: `plan` calls nothing and reports every problem at once; `run` may call
models.

```console
$ matrix plan
error (config): the config does not compose (2 problems):
  matrix.agent_runtimes.m.type: 'nope' is not a registered agent-runtime (/work/matrix.yaml)
    fix: use one of the registered agent-runtimes, or install the package providing it
  matrix.agents.greeter.runtime: 'sdk' is not a configured agent runtime (/work/matrix.yaml)
    fix: name an agent runtime defined under matrix.agent_runtimes
fix: fix each problem listed: its path names the key, its fix what to write
$ echo $?
3
```

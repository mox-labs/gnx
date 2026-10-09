# API reference

Every public name exported from `matrix`, the config it reads, the errors it raises, and the
`matrix` command.

```python
from matrix import (
    # type URLs
    TypeUrl, check_type_url, is_type_url, parse_type_url, type_url,
    # flows
    Component, Port, Member, Flow, compile_flow, CompiledFlow, Inputs, RunContext,
    # execution
    Executor, Limits, Run,
    # the Construct
    Artifact, Construct, ConstructStore, JsonlConstructStore, MemoryConstructStore,
    # agents
    Agent, AgentDefinition, AgentResponse, AgentRuntime, BoundAgent, DefinitionSource,
    AgentStep, AgentStepConfig, AGENT_STEP, AGENT_RESPONSE, TASK,
    # registry
    Registry, Entry, Failure, default_registry, runtime_type_url, observer_type_url,
    # observers
    Event, Observer, RecordingObserver, configure_telemetry,
    # config and composition
    Config, MatrixConfig, RuntimeConfig, AgentConfig, ConfigSource,
    load_config, discover_sources, config_env_var, compose, Container,
    # errors
    MatrixError, ConfigError, CompilationError, NotFoundError, ContractError, ComponentError,
    AgentRuntimeError, RunError, ErrorKind, RuntimeReason, EXIT_CODES,
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
- A component kind name (`capability`, `skill`, `flow`, `agent`) is never a whole segment.
- `slick.*` is reserved; `type_url` refuses to mint into it.
- A URL matrix mints carries one version segment (`v1`, `v1alpha1`, `v2beta1`) after at least
  one namespace segment and before the resource.

Ports and payload types are checked with the grammar only; registry entries are checked the
same way when they register.

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
is_type_url("acme.v1.agent.reviewer")            # False: 'agent' is a kind name
```

---

## Flows

### `Port`

`Port(type_url: str, many: bool = False, optional: bool = False)`, frozen.

| Field | On a required port | On a provided port |
|-------|--------------------|--------------------|
| `many` | receives every value on its topic as a tuple (possibly empty); needed when a topic has several producers | (no effect) |
| `optional` | may be left unbound; the component then sees no value | may be absent from the component's result |

Anywhere a port is declared, a bare type URL string means `Port(type_url)`.

### `Component` (Protocol)

Structural and `runtime_checkable`: implement the shape, no base class.

| Member | Type |
|--------|------|
| `requires` | `Mapping[str, str \| Port]`: port name to type |
| `provides` | `Mapping[str, str \| Port]`: port name to type |
| `run` | `async (inputs: Inputs) -> Mapping[str, Any]`: one value per provide port, keyed by port name |

Configuration arrives when the component is built, never through `run`. A port name may not
appear in both maps.

### `Inputs`

A read-only `Mapping[str, Any]` of port name to value, plus `inputs.context`, a `RunContext`.
An ordinary port maps to the last value on its topic; a `many` port to a tuple of every value;
an unbound optional port is absent. A missing key raises `KeyError` listing the bound ports.

### `RunContext`

Frozen: `run_id: str`, `member: str` (this member's alias), `episode: int = 0`,
`deadline: float | None` (a `time.monotonic()` deadline when `Limits.member_timeout_s` is set).
It says nothing about other members.

### `Member`

`Member(alias, component, bindings={}, config=None, after=())`, frozen.

| Field | Type | Meaning |
|-------|------|---------|
| `alias` | `str` | unique in the flow; `^[A-Za-z0-9][A-Za-z0-9_-]*$` |
| `component` | `Component \| str` | a built component, or the type URL of a registered `component` entry |
| `bindings` | `Mapping[str, str]` | port name to topic name |
| `config` | `Mapping[str, Any] \| None` | settings for a component given by type URL; an error for a built one |
| `after` | `tuple[str, ...]` | aliases this member runs after, without exchanging data |

### `Flow`

`Flow(name, members, inputs={})`, frozen. `members: tuple[Member, ...]`;
`inputs: Mapping[str, str]` maps each input topic to its type URL. A run never changes it.

### `compile_flow`

```python
compile_flow(flow: Flow, *, resolve=None, schemas=None) -> CompiledFlow
```

- `resolve(extension_id, config, where) -> Component` builds members given by type URL.
  `Container.compile` supplies one backed by the registry; without it, such a member is a
  problem.
- `schemas: Mapping[str, type[BaseModel]]` maps payload type URLs to the models values of that
  type must validate against.

Raises `CompilationError` (kind `config`) whose message lists every problem, also available as
`error.details["problems"]`. The checks: alias syntax and uniqueness; every port's type URL;
no port both required and provided; every binding names a declared port; every required,
non-optional port is bound; every topic carries one type; every consumed topic has a producer
or is a flow input; nothing provides onto a flow input; a topic fed only by optional outputs
feeds only optional or `many` ports; a topic with several producers feeds only `many` ports;
every `after` names a member; no cycle. A cycle is reported after the other
checks pass, naming the members and the topics it runs through.

```python
from matrix import CompilationError, compile_flow

try:
    compile_flow(broken)
except CompilationError as e:
    for problem in e.details["problems"]:
        print(problem)
# members.upper.bindings.txt: no such port; declared: shout, text
# members.upper.requires.text: required port is not bound
# topic 'size' carries 'demo.v1.text' (from members.count.text) but members.count.length is 'demo.v1.count'
```

### `CompiledFlow`

Frozen and reusable.

| Field | Type | Meaning |
|-------|------|---------|
| `name` | `str` | |
| `members` | `Mapping[str, CompiledMember]` | resolved component, normalised ports, bindings, `after` |
| `inputs` | `Mapping[str, str]` | input topic to type |
| `topics` | `Mapping[str, str]` | every topic to its one type |
| `producers` | `Mapping[str, tuple[tuple[str, str], ...]]` | topic to `(alias, port)` producers, in declaration order |
| `levels` | `tuple[tuple[str, ...], ...]` | aliases grouped so each level depends only on earlier ones; declaration order within a level |
| `schemas` | `Mapping[str, type[BaseModel]]` | payload schemas outputs are validated against |
| `effects` | `Mapping[str, frozenset[str] \| None]` | member alias to the effects its registration declares; `None` is unknown. Filled by `Container.compile`; empty from `compile_flow` |

`declared_effects() -> frozenset[str] | None` is the union of every member's effects, or `None`
if any member's are unknown. A member built outside the registry, or registered without
`effects`, makes the whole flow's effects unknown; unknown is never read as none.

---

## Execution

### `Limits`

`Limits(concurrency: int = 1, member_timeout_s: float | None = None)`, frozen.
`concurrency` caps how many members of one level run at once (`>= 1`); `member_timeout_s`
bounds each member separately (`> 0`). An invalid value raises `ConfigError`.

### `Executor`

`Executor(observers: Sequence[Observer] = ())`. Stateless between runs; safe to share.

```python
async Executor.run(
    flow: CompiledFlow,
    inputs: Mapping[str, Any] | None = None,
    *,
    limits: Limits | None = None,
    run_id: str | None = None,
    episode: int = 0,
) -> Run
```

1. `inputs` must hold exactly the flow's declared inputs, or `ConfigError` names the missing
   and unknown ones. Each input is validated against its type's schema, if one is registered
   (`ContractError` otherwise), and recorded with producer `$input`.
2. Levels run in order. Each member gets `Inputs` for the topics it bound, each value a deep
   copy, so a component that mutates what it read changes neither the recorded artifact nor
   what a sibling sees. A value that cannot be copied is a `ContractError`: values between
   members must be data, not live handles. A required, non-`many` port whose topic its
   producers left empty is a `ContractError`.
3. A member's result must be a mapping holding every non-optional provide port and no other
   key; each value must validate against its type's schema when one is registered. Otherwise
   `ContractError`. Values on bound ports are appended to the Construct in declaration order;
   values on unbound provide ports are dropped.
4. A member that raises becomes a `ComponentError` (a `MatrixError` it raises keeps its own
   kind); a timeout is a `ComponentError` saying so. The rest of the level finishes, each
   failure is recorded in `run.failures`, and `RunError` is raised from the first failure.
   A timeout's `ComponentError` carries `details["reason"] == "timeout"`.

Cancellation propagates untouched.

### `Run`

| Field / method | Type | Meaning |
|----------------|------|---------|
| `run_id` | `str` | |
| `flow` | `str` | the flow's name |
| `construct` | `Construct` | every value recorded |
| `status` | `"running" \| "completed" \| "failed"` | |
| `started` / `ended` | `datetime` / `datetime \| None` | UTC |
| `failures` | `list[dict]` | `{"member", "kind", "message"}` per failed member |
| `episode` | `int` | |
| `outputs(topic)` | `-> tuple[Any, ...]` | the values on `topic` |

---

## The Construct

See [The data model](../explanation/data-model.md) for the model and the on-disk layout.

### `Artifact`

Frozen pydantic model: `id`, `run_id`, `topic`, `type_url`, `producer` (alias, or `$input`),
`port`, `value`, `episode`, `timestamp`. `Artifact.create(*, run_id, topic, type_url,
producer, port, value, episode=0)` stamps `id` and a UTC `timestamp`.

### `Construct`

`Construct(topics: dict[str, str] | None = None)`, topic to type.

| Method | Signature | Behaviour |
|--------|-----------|-----------|
| `append` | `(artifact) -> None` | Adds a row. `ValueError` if the topic already carries another type. Only the executor and stores call it |
| `rows` | `(topic) -> tuple[Artifact, ...]` | In landing order; `()` if none |
| `values` | `(topic) -> tuple[Any, ...]` | |
| `last` | `(topic) -> Artifact` | `NotFoundError` listing topics with rows |
| `by_type` | `(type_url) -> tuple[Artifact, ...]` | Across topics, ledger order |
| `topics` | `() -> dict[str, str]` | Every topic declared or seen, with its type |
| `ledger` | property `-> tuple[Artifact, ...]` | Every artifact, append order |
| `__contains__` | `(topic) -> bool` | True if the topic has a row |
| `__len__` | `() -> int` | Number of artifacts |

### `ConstructStore` (Protocol)

| Method | Signature | Contract |
|--------|-----------|----------|
| `save` | `(run: Run) -> str` | Persists the run; returns where it went. Never rewrites a saved run (`ConfigError`) |
| `load` | `(run_id) -> Construct` | The Construct as saved |
| `describe` | `(run_id) -> dict` | The run record: `schema` (`matrix.v1.run`), flow, status, failures, `tables` |
| `runs` | `() -> list[str]` | Saved run ids, oldest first |

A missing run is `NotFoundError` naming the runs that exist.

`JsonlConstructStore(root: Path | str)` writes `<root>/<run_id>/run.json` and one
`<topic>.jsonl` per topic; values reload as JSON data. A run id must match `[A-Za-z0-9._-]+`
and not be `.` or `..`. `MemoryConstructStore()` keeps `Run` objects in memory, values as
given.

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

A registered `component` that runs one composed agent as a flow member. Constants:
`AGENT_STEP = "matrix.v1.agent-step"`, `AGENT_RESPONSE = "matrix.v1.agent-response"`,
`TASK = "matrix.v1.task"`.

`AgentStepConfig` (unknown keys rejected):

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `agent` | `str` | required | a composed agent's name |
| `inputs` | `dict[str, str]` | `{"task": "matrix.v1.task"}` | port name to type URL; these are the step's `requires` |
| `task` | `str \| None` | `None` | template with `{port}` placeholders (`{{ }}` for literal braces). `None` is allowed only with exactly one input, whose value is the task |
| `output` | `str \| None` | `None` | a payload type URL; adds the `result` port |

Ports provided: `response` (`matrix.v1.agent-response`), and `result` (the `output` type) when
`output` is set. Input values are rendered as text: a string as is, a pydantic model as
indented JSON, anything else through `json.dumps`. `result` is the JSON document in the reply,
either the whole reply or a fenced `json` block; a reply with no JSON document is a
`ContractError`. An unknown agent name, like any member that cannot be built, is reported
among the flow's compile problems.

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
    requires = {"text": "acme.v1.diff"}
    provides = {"review": "acme.v1.review"}

    def __init__(self, config: ScorerConfig) -> None:
        self.threshold = config.threshold

    async def run(self, inputs):
        n = len(inputs["text"])
        return {"review": {"verdict": "ok" if n < self.threshold else "long", "score": n}}


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
`time: datetime` (UTC), `schema: str = "matrix.v1.event"`. A `*.start`/`*.end` pair shares a
`span`. An agent called from inside a flow member reports that member's span as its `parent`
and the member's run id as its `run_id`; called outside a flow, both are `None`.

| Event | Emitted by | `span` / `parent` | `fields` |
|-------|-----------|-------------------|----------|
| `run.start` | executor | run id / none | `flow` |
| `run.end` | executor | run id / none | `flow`, `status`, `artifacts` |
| `member.start` | executor | new id / run id | `member` |
| `member.end` | executor | same / run id | `member`, `status` (`ok` or an error kind), `duration_ms` |
| `artifact.append` | executor | artifact id / run id | `topic`, `type_url`, `producer`, `port` |
| `agent.start` | `BoundAgent` | new id / the calling member's span | `agent`, `model` |
| `agent.end` | `BoundAgent` | same / the calling member's span | `agent`, `duration_ms`, then `status` (`stop`), `tokens_input`, `tokens_output`, `cost_usd`, `model`, `family`; or `status="error"`, `error`, `reason` |

### `Observer` (Protocol) and `RecordingObserver`

`Observer` is `on_event(event: Event) -> None`. An observer that raises is logged and skipped;
it cannot stop a run. `RecordingObserver()` keeps `events: list[Event]`; `names()` lists their
names.

### The `otel` observer

`matrix.v1.observer.otel`, enabled with `observers: [otel]`. Requires `[otel]`. Spans:
`matrix.run` for a run, `matrix.member` under it for each member, and `invoke_agent {agent}`
for each agent session (`gen_ai.operation.name=invoke_agent`). Fields become attributes:
`agent`, `model`, `tokens_input` and `tokens_output` map to `gen_ai.agent.name`,
`gen_ai.request.model`, `gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens`; other
scalar fields become `matrix.<field>`. A failing member or run, or an agent ending in error,
sets the span status to ERROR.

### `configure_telemetry`

`configure_telemetry(*, service_name="matrix", endpoint=None, console=False) -> None`
configures the OpenTelemetry SDK exporter. `endpoint` is an OTLP gRPC endpoint and falls back
to `OTEL_EXPORTER_OTLP_ENDPOINT`; `console=True` prints spans. With neither, it does nothing.
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
| `observers` | `tuple[str, ...]` | `()` | observer short names (`otel`) or type URLs |
| `agents` | `dict[str, AgentConfig]` | `{}` | named agents |
| `models` | `dict \| None` | `None` | a hardline registry section (`{default?, models: {...}}`) handed to every `model` runtime that has none. `None` lets hardline read its own tiers |

`RuntimeConfig`: `type` (a built-in short name or a full type URL) plus that runtime's own
options as extra keys, validated by the runtime's config model.

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
  observers: [otel]
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

| Member | Returns |
|--------|---------|
| `config`, `registry` | what it was composed from |
| `runtimes`, `definitions`, `agents` | name to object |
| `observers` | `tuple[Observer, ...]` |
| `agent(name)` | `BoundAgent`; `NotFoundError` listing configured agents |
| `runtime(name)` | `AgentRuntime`; `NotFoundError` listing configured runtimes |
| `bind(definition, runtime)` | a `BoundAgent` on the configured runtime named `runtime`, with the container's observers |
| `needs()` | the shared needs: `agents`, `models`, `cwd`, plus `context` keys |
| `compile(flow)` | `CompiledFlow`, building members given by type URL from the registry's `component` entries, with the registry's payload schemas and each member's declared `effects` (an `agent-step` member has its agent's runtime's effects) |
| `executor()` | an `Executor` with the container's observers |

---

## Errors

Every error matrix raises deliberately is a `MatrixError`. Each carries a `kind` (what the
caller can do about it), an `exit_code`, an optional `fix` (one actionable sentence), and
`details` (structured fields). `payload()` renders
`{"error": {"kind", "message", "fix", ...details}}`.

| Kind | Exit | Meaning, and the next move |
|------|------|----------------------------|
| `config` | 3 | a config, definition, flow or argument is invalid: fix it |
| `not_found` | 4 | a name or type URL is not registered or configured |
| `contract` | 1 | a component broke its declared ports (a bug in it) |
| `component` | 1 | a component raised while running |
| `transient` | 5 | unavailable, rate-limited or timed out: retry later |
| `auth` | 6 | credentials missing or refused |
| `unknown` | 1 | anything else that went wrong |

`EXIT_CODES` is this mapping; `ErrorKind` is the literal type of the kinds.

| Class | Kind | Also | Raised when |
|-------|------|------|-------------|
| `MatrixError` | `unknown` | `Exception` | base class |
| `ConfigError` | `config` | `ValueError` | a config, definition, flow, type URL or argument is invalid |
| `CompilationError` | `config` | `ConfigError` | a flow is miswired; `details["problems"]` lists every problem |
| `NotFoundError` | `not_found` | `KeyError` | a name, type URL, topic or saved run does not exist; the message lists those that do |
| `ContractError` | `contract` | | a member's output or a flow input breaks its declared type or ports |
| `ComponentError` | `component` | | a member raised or timed out; the original exception is the cause |
| `AgentRuntimeError` | from `reason` | | an agent session failed |
| `RunError` | the failing member's | | a flow run failed; `run` is the partial `Run`, `member` the alias, `construct` the partial Construct |

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

Read-only: it inspects extensions and configs and never runs a flow or calls a model. Data
goes to stdout, diagnostics to stderr.

| Command | Output (`--json` schema) |
|---------|--------------------------|
| `matrix catalog [--point P]` | every registered entry (point, type URL, stability, summary, origin, needs, effects, whether configurable) and every failed extension (`matrix.v1.catalog`) |
| `matrix describe TYPE_URL` | the full entry, including its config JSON schema (`matrix.v1.extension`); `not_found` if nothing is registered under it |
| `matrix config [--tool T] [--sources]` | the merged `matrix` section and the files consulted (`matrix.v1.config`); with `--sources`, only the files (`matrix.v1.config-sources`) |
| `matrix check [--tool T]` | composes runtimes and agents and reports what composed (`matrix.v1.check`) |
| `matrix agents [--tool T]` | each agent's name, runtime, runtime type, model, tools and description (`matrix.v1.agents`) |
| `matrix --version` | the installed version |

`--tool` (default `matrix`) selects whose config tiers to read. With `--json`, an error is the
`payload()` document on stderr; without it, `error (<kind>): <message>` and `fix: <fix>`.

| Exit | Meaning |
|------|---------|
| 0 | ok |
| 1 | failure (`contract`, `component`, `unknown`) |
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

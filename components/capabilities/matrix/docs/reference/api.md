# API Reference

All public types exported from `matrix`.

> Regenerated 2026-08-17 from source at gnx intake. Every signature below was read
> off the code, not carried over from the previous revision — which taught a contract
> matrix had already abandoned (`requires`/`provides` as `consumes`/`produces`, an
> `Artifact.kind` field, a frozen generic `Construct[S]` with a `subject`, and
> `run(subject)`). If you are porting code written against those docs, see
> [Migrating from the pre-intake contract](#migrating-from-the-pre-intake-contract).

---

```python
from matrix import (
    Agent,
    AgentResponse,
    Artifact,
    CompilationError,
    Component,
    ComponentRegistry,
    Config,
    configure_telemetry,
    Construct,
    ContractError,
    DagCompiler,
    DagScheduler,
    MatrixConfig,
    Orchestrator,
    TypedStruct,
    deep_merge,
    discover_sources,
    load_config,
)
```

## Core Types

### `Component` (Protocol)

The contract every DAG node must satisfy. `@runtime_checkable` structural typing — implement it without importing matrix.

| Attribute | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Unique identifier within a DAG |
| `requires` | `frozenset[str]` | Artifact `type_url`s this component reads |
| `provides` | `str` | Artifact `type_url` this component writes |

| Method | Signature | Description |
|--------|-----------|-------------|
| `run` | `async (construct: Construct) -> TypedStruct` | Read upstream artifacts, do work, return self-describing output |

`run` returns a `TypedStruct`, **not** a bare value. The Orchestrator checks the returned
`type_url` against the declared `provides` and raises `ContractError` on mismatch — the
double-entry bookkeeping that makes a component's declaration binding rather than advisory.

```python
from matrix import Construct, TypedStruct

class MyComponent:
    name = "my-component"
    requires = frozenset({"upstream.data"})
    provides = "my-component.output"

    async def run(self, construct: Construct) -> TypedStruct:
        upstream = construct.last("upstream.data")   # -> Artifact
        return TypedStruct(
            type_url="my-component.output",          # must equal self.provides
            value=process(upstream.data),
        )
```

### `TypedStruct`

Self-describing output. A `NamedTuple` — zero overhead, no import needed by external consumers.

| Field | Type | Description |
|-------|------|-------------|
| `type_url` | `str` | What the value is |
| `value` | `Any` | The data |

### `Artifact`

Immutable fact produced by a component. Frozen Pydantic model.

| Field | Type | Description |
|-------|------|-------------|
| `type_url` | `str` | Artifact type (must equal the producing component's `provides`) |
| `producer` | `str` | Name of the component that created it |
| `data` | `Any` | The actual value |
| `id` | `str` | UUID4 string |
| `timestamp` | `datetime` | UTC creation time |

| Method | Signature | Description |
|--------|-----------|-------------|
| `create` | `static (*, type_url: str, producer: str, data: Any) -> Artifact` | Factory that stamps `id` (uuid4) and `timestamp` (UTC now) |

`id` and `timestamp` are required fields with no defaults — construct via `Artifact.create()`
rather than the initialiser unless you are deliberately supplying your own.

`type_url` convention: `<namespace>.v<version>/<resource>` — e.g. `matrix.v1/agent.response`,
`ix.v1/eval.readings`.

### `Construct`

Append-only artifact ledger for one DAG execution. A plain mutable class — **not** frozen,
**not** generic, and it carries no `subject`. Constructed with no arguments.

| Method | Signature | Description |
|--------|-----------|-------------|
| `append` | `(artifact: Artifact) -> None` | Append to the ledger. Mutates in place; returns nothing |
| `query` | `(type_url: str) -> list[Artifact]` | All artifacts of the type, in append order. Empty list if none |
| `last` | `(type_url: str) -> Artifact` | Most recent artifact of the type. Raises `LookupError` (message lists available types) |
| `ledger` | `property -> tuple[Artifact, ...]` | Immutable snapshot of the full ledger |
| `kinds` | `() -> frozenset[str]` | Every `type_url` present |
| `__getitem__` | `(type_url: str) -> Any` | Backward-compat shorthand for `last(type_url).data` |
| `__contains__` | `(type_url: str) -> bool` | Whether any artifact of the type exists |
| `__len__` | `() -> int` | **Number of distinct `type_url`s — not the artifact count.** For artifacts, use `len(construct.ledger)` |

`last()` returns the `Artifact`, not its `data`. Reach through to `.data`, or use the
`construct["type_url"]` shorthand.

The DagCompiler guarantees that if a component's `requires` are satisfiable at compile time,
those artifacts exist by the time it runs — so `last()` on a declared requirement will not raise.

### `ContractError`

Raised by the Orchestrator when a component's returned `type_url` doesn't match its declared
`provides`.

---

## Orchestration

### `Orchestrator`

Compiles and executes a DAG of components sequentially.

```python
orch = Orchestrator([probe, sensor, scorer])
construct = await orch.run()          # no arguments
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(components: list[Any], on_node: NodeCallback \| None = None)` | Compile topology immediately; optional per-node progress callback |
| `run` | `async () -> Construct` | Execute the DAG, return the Construct holding every artifact |

`run()` takes **no** arguments. External input enters through component constructors
(factory-closure config), not through the run call. Roots declare `requires = frozenset()`.

`on_node` is called as `on_node(name, "start")` as each component begins.

`__init__` calls `DagCompiler.compile()`, so topology errors surface at construction, not at run.

Execution is sequential, batch by batch. Matrix refuses persistence, retries, and parallelism
by design — dump `construct.ledger` on the caller side if you need durability.

### `DagCompiler`

Static topology validation. Call directly when you need edges without execution.

```python
registry, edges = DagCompiler.compile([probe, sensor, scorer])
# registry: {"probe": <Probe>, "sensor": <Sensor>, "scorer": <Scorer>}
# edges:    {"probe": set(), "sensor": {"probe"}, "scorer": {"sensor"}}
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `compile` | `static (components: list[Any]) -> tuple[dict[str, Any], dict[str, set[str]]]` | Validate and return (registry, edges) |

Raises `CompilationError` on:
- **Missing producer** — a component `requires` a type nobody `provides`
- **Duplicate output** — two components declare the same `provides`
- **Duplicate name** — two components share a `name`
- **Cycle** — any cycle in the dependency graph (detected via `graphlib`)

### `DagScheduler`

Yields topological execution batches. Components within a batch are mutually independent.

```python
scheduler = DagScheduler(registry, edges)
for batch in scheduler.batches():
    for component in batch:
        ...
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(registry: dict, edges: dict)` | Accept compiled topology |
| `batches` | `() -> Iterator[tuple[Any, ...]]` | Yield batches in topological order |

### `CompilationError`

Raised by `DagCompiler.compile()` when topology validation fails.

---

## Agent Execution

### `Agent` (Protocol)

Backend-agnostic agent execution. Adapters implement this for Claude, Google ADK, Ollama, etc.
The Agent already holds its system prompt — callers send only a prompt.

| Method | Signature | Description |
|--------|-----------|-------------|
| `run` | `async (prompt: str) -> AgentResponse` | Execute a prompt, return a structured response |

### `AgentResponse`

Structured result of an agent execution. Frozen Pydantic model — everything the SDK returns,
kept flat for DataFrame compatibility.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `content` | `str` | `""` | The response text |
| `tool_calls` | `tuple[dict, ...]` | `()` | Plain `{name, input}` dicts — no wrapper type |
| `tokens_input` | `int` | `0` | Input tokens consumed |
| `tokens_output` | `int` | `0` | Output tokens produced |
| `duration_ms` | `int` | `0` | Wall-clock duration |
| `cost_usd` | `float \| None` | `None` | Cost when the backend reports it |
| `num_turns` | `int` | `0` | Turns taken |

---

## Configuration

Each consumer (ix, memex, radix) defines its own config model. Matrix owns the platform section
and supplies the loading/merging machinery.

### `MatrixConfig`

Platform settings Matrix owns. Frozen Pydantic model.

| Field | Type | Default |
|-------|------|---------|
| `runtime` | `RuntimeSettings` | `RuntimeSettings()` |
| `runtime.model` | `str` | `"claude-sonnet-4-5-20250929"` |
| `runtime.max_tokens` | `int` | `2048` |

### `Config[C]`

Composes Matrix platform settings with one client's settings. Frozen; generic over the client's
Pydantic model type.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `matrix` | `MatrixConfig` | `MatrixConfig()` | Platform-level settings |
| `client` | `C` | required | Client-provided Pydantic model |

```python
from matrix import load_config, Config, MatrixConfig
from pydantic import BaseModel, ConfigDict

class IxConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    default_trials: int = 5

config = load_config(IxConfig, client_key="ix")
config.matrix.runtime.model    # "claude-sonnet-4-5-20250929"
config.client.default_trials   # 5
```

### `load_config`

Reads YAML sources, merges tiers, validates both sections.

| Parameter | Type | Description |
|-----------|------|-------------|
| `client_type` | `type[C]` | Pydantic model class for the client section |
| `client_key` | `str` | YAML key for the client section (**required**, positional-or-keyword) |
| `sources` | `list[Path] \| None` | Paths in priority order. `None` → `discover_sources(client_key)` |

```python
config = load_config(
    client_type=IxConfig,
    client_key="ix",
    sources=[Path("ix.yaml")],   # omit for 3-tier discovery
)
```

### `discover_sources`

| Parameter | Type | Description |
|-----------|------|-------------|
| `tool` | `str` | Tool name — owns the config location |
| `project_root` | `Path \| None` | Defaults to `Path.cwd()` |

Returns paths in priority order (first = lowest precedence):

1. Pydantic model defaults — no file; built into the schema
2. User-level — `~/.{tool}/config.yaml`
3. Project-level — `{project_root}/{tool}.yaml`

Later tiers override earlier ones. Missing files are skipped, not errors.

### `deep_merge`

`(base: dict, override: dict) -> dict` — recursive merge. Override wins. **Lists replace
entirely**; they are not concatenated or merged element-wise.

### Multi-consumer YAML

One file can serve several consumers. Each reads the shared `matrix:` section plus its own:

```yaml
matrix:
  runtime:
    model: claude-sonnet-4-5-20250929
ix:
  default_trials: 5
memex:
  chunk_size: 512
```

```python
ix_config    = load_config(IxConfig, "ix")         # reads matrix: + ix:
memex_config = load_config(MemexConfig, "memex")   # reads matrix: + memex:
```

### `configure_telemetry`

`(**kwargs)` — configures the OpenTelemetry SDK. Requires the extra: `uv add matrix[otel]`.
Imported lazily, so matrix runs without the SDK installed (the API package alone is enough —
spans become no-ops).

Spans emitted:

| Span | Attributes |
|------|-----------|
| `matrix.dag.run` | `matrix.dag.artifact_count` |
| `matrix.component.run` | `matrix.component.name`, `matrix.component.provides` |

---

## Component Registry

### `ComponentRegistry`

Type URL to factory mapping, for Container-based DI. Follows the xDS typed-config registry
pattern.

```python
registry = (
    ComponentRegistry()
    .register("app.probe", make_probe)
    .register("app.sensor", make_sensor)
)

component = registry.create("app.probe", {"name": "custom"})
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `register` | `(type_url: str, factory) -> ComponentRegistry` | Register a factory. Returns self (chainable). Raises `ValueError` on duplicate |
| `create` | `(type_url: str, config: dict \| None = None) -> Any` | Create a component. Raises `KeyError` if unknown |
| `types` | `() -> frozenset[str]` | All registered type URLs |
| `__contains__` | `(type_url: str) -> bool` | Whether a type URL is registered |
| `__len__` | `() -> int` | Number of registered factories |

A factory is any callable accepting `**config` keyword arguments and returning a component.
Per-component Pydantic validation of the config dict is not implemented — `create()` passes
the dict straight through as kwargs.

---

## Migrating from the pre-intake contract

If you wrote against the previous docs, these are the breaks:

| Old (documented, non-existent) | Current (in code) |
|---|---|
| `consumes` / `produces` | `requires` / `provides` |
| `async run(...) -> Any` | `async run(...) -> TypedStruct` |
| `Artifact.kind` | `Artifact.type_url` |
| `Artifact.created_at: float` (monotonic) | `Artifact.timestamp: datetime` (UTC) |
| `Construct[S]`, frozen dataclass, `subject: S` | `Construct`, mutable class, no subject, no generic |
| `construct.append(a) -> Construct[S]` | `construct.append(a) -> None` (mutates) |
| `construct.last(kind) -> Any` | `construct.last(type_url) -> Artifact` |
| `construct.all(kind)` | `construct.query(type_url)` |
| `await orch.run("my-subject")` | `await orch.run()` |

The `consumes` → `requires` and `produces` → `provides` rename landed at gnx intake
(2026-08-17), aligning the runtime's vocabulary with slick's Manifest field names. Everything
else in this table was already true in the code and merely mis-documented.

# API Reference

All public types exported from `matrix`.

> Regenerated 2026-09-24 from source after the definition/runtime split. Every signature
> below was read off the code, not carried over from a previous revision. The 2026-08-17
> revision replaced one that — which taught a contract
> matrix had already abandoned (`requires`/`provides` as `consumes`/`produces`, an
> `Artifact.kind` field, a frozen generic `Construct[S]` with a `subject`, and
> `run(subject)`). If you are porting code written against those docs, see
> [Migrating from the pre-intake contract](#migrating-from-the-pre-intake-contract).

---

```python
from matrix import (
    # DAG
    Artifact, Component, CompilationError, Construct, ConstructReader, ConstructView,
    ContractError, DagCompiler, DagScheduler, Orchestrator, TypedStruct,
    # agents
    Agent, AgentDefinition, AgentResponse, AgentRuntime, BoundAgent, DefinitionSource,
    # composition
    AgentConfig, ComponentRegistry, Config, ConfigError, Container, MatrixConfig, RuntimeConfig,
    bind_agents, build_runtimes, compose, default_registry, load_definitions,
    register_builtin_runtimes, runtime_type_url,
    # config loading, telemetry
    configure_telemetry, deep_merge, discover_sources, load_config,
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
| `run` | `async (construct: ConstructReader) -> TypedStruct` | Read upstream artifacts, do work, return self-describing output |

`run` returns a `TypedStruct`, **not** a bare value. The Orchestrator checks the returned
`type_url` against the declared `provides` and raises `ContractError` on mismatch — the
double-entry bookkeeping that makes a component's declaration binding rather than advisory.

Reads are enforced the same way: the Orchestrator hands `run` a `ConstructView` restricted to
`requires`, and reading any other kind raises `ContractError`.

```python
from matrix import ConstructReader, TypedStruct


class MyComponent:
    name = "my-component"
    requires = frozenset({"demo.v1/upstream.data"})
    provides = "demo.v1/my-component.output"

    async def run(self, construct: ConstructReader) -> TypedStruct:
        upstream = construct.last("demo.v1/upstream.data")  # -> Artifact
        return TypedStruct(
            type_url=self.provides,  # must equal self.provides
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

`type_url` convention: `<namespace>.v<version>/<resource>` — e.g. `matrix.v1/runtime.model`,
`ix.v1/trial.observation`, `hardline.v1/completion`.

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

### `ConstructReader` (Protocol) and `ConstructView`

`ConstructReader` is the read side of a Construct — `query`, `last`, `__getitem__`,
`__contains__`, `kinds` — and is what `Component.run` receives. A `Construct` satisfies it.

`ConstructView(construct, *, reader, allowed)` is the Orchestrator's implementation: every
accessor raises `ContractError` for a kind outside `allowed` (the component's `requires`),
naming the component, the kind, and what it declared. `kinds()` returns only allowed kinds.

### `ContractError`

Raised by the Orchestrator when a component's returned `type_url` doesn't match its declared
`provides`.

---

## Orchestration

### `Orchestrator`

Compiles and executes a DAG of components sequentially.

```python
orch = Orchestrator([probe, sensor, scorer])
construct = await orch.run()  # no arguments
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

## Agents

An agent is composition: an **AgentDefinition** (what it is) bound to an **AgentRuntime**
(where it runs) gives a **BoundAgent**, which satisfies the `Agent` port.

### `AgentDefinition`

Frozen Pydantic model, `extra="forbid"`. The same shape as a Claude Code agent file.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | required | Slug |
| `description` | `str` | `""` | |
| `system_prompt` | `str` | `""` | A markdown file's body |
| `model` | `str \| None` | `None` | Interpreted by the runtime: SDK alias/id, or a hardline registry name |
| `tools` | `tuple[str, ...] \| None` | `None` | `None` = runtime default; `()` = **no tools** (never collapsed — SECURITY.md M-2) |
| `max_turns` | `int` | `1` | ≥ 1 |
| `metadata` | `dict` | `{}` | Keys a source carried that no runtime interprets |

### `AgentRuntime` (Protocol)

| Method | Signature |
|--------|-----------|
| `run` | `async (definition: AgentDefinition, task: str) -> AgentResponse` |

Built-ins, registered as `matrix.v1/runtime.<type>`:

| type | config fields | notes |
|------|---------------|-------|
| `claude-sdk` | `permission_mode` (default `"default"`), `cwd`, `setting_sources`, `plugins`, `fallback_model`, `agents` | stamps `family="claude"`; relative plugin paths resolve against `cwd` |
| `model` | `models` (a hardline registry section), `default_model`, `temperature`, `max_tokens` | one call; refuses definitions with tools; stamps the answering model's family |
| `mock` | `responses`, `default`, `family` | offline; records `calls` |

### `BoundAgent`

`BoundAgent(definition, runtime)`. Properties `name`, `definition`, `runtime`;
`async run(prompt) -> AgentResponse` delegates to `runtime.run(definition, prompt)`.

### `Agent` (Protocol)

`async run(prompt: str) -> AgentResponse`. What a consumer calls.

### `AgentResponse`

Frozen Pydantic model, flat for DataFrame compatibility.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `content` | `str` | `""` | The response text |
| `tool_calls` | `tuple[dict, ...]` | `()` | Plain `{name, input}` dicts |
| `tokens_input` | `int` | `0` | |
| `tokens_output` | `int` | `0` | |
| `duration_ms` | `int` | `0` | |
| `cost_usd` | `float \| None` | `None` | When the backend reports it |
| `num_turns` | `int` | `0` | |
| `family` | `str \| None` | `None` | Model family that produced it — what out-of-family checks read |
| `model` | `str \| None` | `None` | Model id or registry name, when known |

### `DefinitionSource` (Protocol)

`load() -> list[AgentDefinition]`, `describe() -> str`. Built-in:
`MarkdownDefinitionSource(directory)` reads `*.md` (frontmatter `name`, `description`,
`tools` as a comma string or list, `model`, `max_turns`/`maxTurns`; body = system prompt;
other keys → `metadata`). Errors name the file.

---

## Configuration

Each consumer (ix, memex, radix) defines its own config model. Matrix owns the platform section
and supplies the loading/merging machinery.

### `MatrixConfig`

The `matrix:` section. Frozen, `extra="forbid"`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `definitions` | `tuple[str, ...]` | `()` | Directories of `*.md` agent files, relative to `compose(base_dir=...)` |
| `runtimes` | `dict[str, RuntimeConfig]` | `{}` | Named runtimes: `{type: <runtime type>, ...options}` |
| `agents` | `dict[str, AgentConfig]` | `{}` | `{runtime, definition?, description?, system_prompt?, model?, tools?, max_turns?}` |
| `models` | `dict \| None` | `None` | A hardline registry section, handed to `type: model` runtimes that carry none |

### `compose`

`compose(config, *, registry=None, base_dir=None, source="matrix", definition_sources=()) -> Container`

Builds runtimes (each resolved by `runtime_type_url(type)` through the registry, options
validated by that runtime's typed config), loads definitions, binds agents. Every failure
is a `ConfigError` naming the key path and the legal values. `registry` defaults to
`default_registry()` — built-in runtimes plus every `matrix.components` entry point.

### `Container`

`agent(name) -> BoundAgent`, `runtime(name) -> AgentRuntime`, properties `agents`,
`runtimes`, `definitions`, `config`, `registry`; `build_orchestrator(specs)` resolves
`(type_url, config)` pairs into an `Orchestrator`.

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
config.matrix.runtimes        # {} unless a tier declares some
config.client.default_trials  # 5
```

### `load_config`

Reads YAML sources, merges tiers, validates both sections.

| Parameter | Type | Description |
|-----------|------|-------------|
| `client_type` | `type[C]` | Pydantic model class for the client section |
| `client_key` | `str` | YAML key for the client section (**required**, positional-or-keyword) |
| `sources` | `list[Path] \| None` | Paths in priority order. `None` → `discover_sources(client_key)` |

A validation failure raises `ConfigError` naming each bad key path (`matrix.agents.a.runtim`)
and every file consulted. A tier that exists but is not a YAML mapping is an error naming the
file, not an empty tier.

```python
config = load_config(
    client_type=IxConfig,
    client_key="ix",
    sources=[Path("ix.yaml")],  # omit for 3-tier discovery
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
ix_config = load_config(IxConfig, "ix")  # reads matrix: + ix:
memex_config = load_config(MemexConfig, "memex")  # reads matrix: + memex:
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

Type URL → factory, with typed config and discovery. The xDS typed-config registry pattern.

```python
registry = (
    ComponentRegistry()
    .register("app.v1/probe", make_probe)                              # factory(**config)
    .register_typed("app.v1/sensor", SensorConfig, lambda c: Sensor(c)) # validated first
)
component = registry.create("app.v1/sensor", {"threshold": 3}, source="app.yaml")
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `register` | `(type_url, factory) -> ComponentRegistry` | `factory(**config)`. An unaccepted or missing key raises `ConfigError` naming the type URL and the keys, not a bare `TypeError`. Raises `ValueError` on duplicate |
| `register_typed` | `(type_url, config_cls, build) -> ComponentRegistry` | Config validated through `config_cls` (pydantic) first; failures raise `ConfigError` with `source`, type URL and key path |
| `create` | `(type_url, config=None, *, source="<config>") -> Any` | Create. `KeyError` lists registered type URLs when unknown |
| `discover` | `(group="matrix.components") -> ComponentRegistry` | Load every entry point in `group`; each is `register(registry) -> None` |
| `types` | `() -> frozenset[str]` | All registered type URLs |
| `__contains__` / `__len__` | | |

`default_registry(discover=True)` is a registry with the built-in runtimes, plus discovered
extensions. `runtime_type_url("claude-sdk")` → `"matrix.v1/runtime.claude-sdk"`.

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

### From the 2026-08-17 contract (breaks on 2026-09-24)

| Before | After |
|--------|-------|
| `ClaudeAgent(system_prompt=..., max_turns=..., allowed_tools=..., permission_mode=...)` | `AgentDefinition(system_prompt=..., max_turns=..., tools=...)` bound to `ClaudeSdkRuntime(ClaudeSdkRuntimeConfig(permission_mode=...))` — or config: `runtimes: {sdk: {type: claude-sdk}}` |
| `AnthropicAgent` | removed; a single model call is the `model` runtime, through hardline, for any family |
| `MockRuntime.invoke(system, messages) -> str` | `MockRuntime.run(definition, task) -> AgentResponse` — now satisfies the port |
| `MatrixConfig.runtime.model` / `.max_tokens` | removed (nothing consumed them); `MatrixConfig` declares `definitions`, `runtimes`, `agents`, `models` |
| `Component.run(construct: Construct)` | `run(construct: ConstructReader)`; undeclared reads raise `ContractError` |
| registry keys `matrix.agent.<type>` | `matrix.v1/runtime.<type>` |

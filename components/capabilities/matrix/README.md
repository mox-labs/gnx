# Matrix

Two things, one registry: a **DAG runtime** where components declare what they read and
write, and the **composition of agents** from a definition and a runtime.

```bash
uv add "matrix @ git+https://github.com/mox-labs/gnx#subdirectory=components/capabilities/matrix"
# extras: [claude] the Claude Agent SDK runtime · [models] the model runtime (via modelrt)
```

## Components and the DAG

A component declares the artifact kinds it `requires` and the one kind it `provides`. Matrix
derives the graph from those declarations, rejects malformed topologies before anything runs,
executes in order, and ledgers every result.

```python
from matrix import ConstructReader, Orchestrator, TypedStruct

class Probe:
    name = "probe"
    requires = frozenset()                       # a root: seed input comes via the constructor
    provides = "demo.v1/probe.response"

    async def run(self, construct: ConstructReader) -> TypedStruct:
        return TypedStruct(type_url=self.provides, value="hello")

class Sensor:
    name = "sensor"
    requires = frozenset({"demo.v1/probe.response"})
    provides = "demo.v1/sensor.grade"

    async def run(self, construct: ConstructReader) -> TypedStruct:
        return TypedStruct(self.provides, construct["demo.v1/probe.response"] == "hello")

construct = await Orchestrator([Probe(), Sensor()]).run()
construct["demo.v1/sensor.grade"]                # True
```

The declarations are **enforced**, not advisory:

- **Writes.** `run` returns a `TypedStruct` whose `type_url` must equal `provides`, or the
  Orchestrator raises `ContractError`.
- **Reads.** Each component is handed a view of the ledger restricted to its `requires`. Reading
  anything else raises `ContractError` naming the component, the kind, and what it declared —
  so the compiler's edges are the true data dependencies.

Structural typing throughout: implement the shape, no base class.

## Agents: definition + runtime

An **AgentDefinition** is what the agent *is* — prompt, tools, model, turn budget. It is the
same shape as a Claude Code agent file, so a plugin's `agents/*.md` load without translation.

An **AgentRuntime** is *where and how* it runs:

| type | type URL | what it runs |
|---|---|---|
| `claude-sdk` | `matrix.v1/runtime.claude-sdk` | Claude Agent SDK sessions — tools, turns, plugins, permission mode |
| `model` | `matrix.v1/runtime.model` | one call to any model modelrt has a registry row for; refuses definitions that declare tools |
| `mock` | `matrix.v1/runtime.mock` | deterministic, offline |

Binding one to the other gives a **BoundAgent** (`run(prompt) -> AgentResponse`). Every response
carries the `family` of the model that produced it.

## Configuration

`compose` turns a `matrix:` config section into running objects:

```yaml
matrix:
  definitions: [agents/]                # *.md agent files
  models:                               # a modelrt registry, for type: model runtimes
    default: qwen3-8b
    models:
      qwen3-8b: {backend: openai-compat, base_url: "http://127.0.0.1:8080/v1",
                 model: mlx-community/Qwen3-8B-4bit, family: qwen, local: true}
  runtimes:
    sdk:   {type: claude-sdk, permission_mode: default, setting_sources: []}
    local: {type: model}
  agents:
    reviewer:       {runtime: sdk}                                  # agents/reviewer.md
    reviewer-local: {runtime: local, definition: reviewer, model: qwen3-8b, tools: []}
    greeter:        {runtime: local, system_prompt: "Say hello."}   # fully inline
```

```python
from matrix import compose, load_config

config = load_config(MyToolConfig, client_key="mytool")       # ~/.mytool/config.yaml < ./mytool.yaml
container = compose(config, base_dir=project_root, source="mytool.yaml")
response = await container.agent("reviewer-local").run("Review this diff: ...")
```

A bad value fails at composition, naming the key path and the legal set:

```
matrix.yaml: agents.triage.runtime: 'sdk' is not a configured runtime. Configured: local, mock
matrix.yaml: runtimes.sdk: matrix.v1/runtime.claude-sdk: permission_mode: Input should be 'default', 'acceptEdits', ...
```

## Extending

Everything resolves through one `ComponentRegistry` keyed by type URL
(`<namespace>.v<version>/<resource>`). An extension registers without editing matrix — one
entry point, a callable `register(registry) -> None`:

```toml
[project.entry-points."matrix.components"]
strands = "my_pkg.strands:register"
```

```python
def register(registry):
    registry.register_typed(runtime_type_url("strands"), StrandsConfig, StrandsRuntime)
```

`register_typed` validates config through a pydantic model before building, so a typo names
the type URL and the key. A duplicate type URL raises; discovery never overrides silently.

## Documentation

| Document | Description |
|----------|-------------|
| [What is Matrix?](docs/explanation/what-is-matrix.md) | Why it exists, design decisions, what it's not |
| [The Data Model](docs/explanation/data-model.md) | Construct and Artifact — the append-only execution ledger |
| [API Reference](docs/reference/api.md) | All public types |
| [SECURITY.md](SECURITY.md) | Agent authority, config as a capability grant, open findings |

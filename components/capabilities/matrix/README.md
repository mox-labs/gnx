# matrix

matrix runs flows of typed components and composes agents from config.

- A **component** declares named ports, each typed by a type URL. A **flow** wires members'
  ports to **topics**. matrix checks the wiring before anything runs, reports every problem
  at once, runs the members in dependency order, and records every value in a **Construct**.
- An **agent** is a definition (prompt, tools, model) bound to a runtime (Claude Agent SDK,
  one model call through hardline, or a mock). The binding is checked when it is made, and
  `matrix.v1.agent-step` puts an agent into a flow as an ordinary member.
- One **registry** holds every extension: runtimes, components, payload types, observers.
  matrix's built-ins arrive through the same `matrix.extensions` entry point as anyone else's.

```bash
uv add "matrix @ git+https://github.com/mox-labs/gnx#subdirectory=components/capabilities/matrix"
# extras: [claude] Claude Agent SDK runtime · [models] model runtime (hardline) · [otel] tracing
```

## A flow

```python
import asyncio

from matrix import Executor, Flow, Member, compile_flow


class Upper:
    requires = {"text": "demo.v1.text"}
    provides = {"shout": "demo.v1.text"}

    async def run(self, inputs):
        return {"shout": inputs["text"].upper()}


class Count:
    requires = {"text": "demo.v1.text"}
    provides = {"length": "demo.v1.count"}

    async def run(self, inputs):
        return {"length": len(inputs["text"])}


flow = Flow(
    name="shout-and-count",
    inputs={"line": "demo.v1.text"},
    members=(
        Member("upper", Upper(), bindings={"text": "line", "shout": "loud"}),
        Member("count", Count(), bindings={"text": "loud", "length": "size"}),
    ),
)

compiled = compile_flow(flow)
run = asyncio.run(Executor().run(compiled, {"line": "hello"}))

print(run.status)              # completed
print(run.outputs("loud"))     # ('HELLO',)
print(run.outputs("size"))     # (5,)
print(compiled.levels)         # (('upper',), ('count',))
```

Components are structural: a class with `requires`, `provides` and an async `run` is a
component without importing matrix. `run` receives `Inputs` (only the values it bound) and
returns one value per provide port, keyed by port name. matrix holds it to that: an extra or
missing port, or a value that fails its type's registered schema, is a `ContractError`.

`compile_flow` raises one `CompilationError` listing every wiring problem: an unknown port, an
unbound required port, a topic carrying two types, a topic nobody produces, a required port
fed only by optional outputs, several producers feeding a port not declared `many=True`, and
cycles. A compiled flow runs any number of times with different inputs.

## Agents

```python
import asyncio

from matrix import Flow, MatrixConfig, Member, compose

config = MatrixConfig.model_validate(
    {
        "runtimes": {"offline": {"type": "mock", "default": '{"verdict": "ok"}'}},
        "agents": {
            "reviewer": {"runtime": "offline", "system_prompt": "Review the change."},
        },
    }
)
container = compose(config)

# An agent on its own.
response = asyncio.run(container.agent("reviewer").run("Review: rename x to y"))
print(response.content, response.stop, response.family)   # {"verdict": "ok"} completed mock

# The same agent as a flow member, through matrix.v1.agent-step.
flow = Flow(
    name="review",
    inputs={"diff": "acme.v1.diff"},
    members=(
        Member(
            "review",
            "matrix.v1.agent-step",
            config={
                "agent": "reviewer",
                "inputs": {"diff": "acme.v1.diff"},
                "task": "Review this change:\n{diff}",
                "output": "acme.v1.review",
            },
            bindings={"diff": "diff", "result": "review"},
        ),
    ),
)
compiled = container.compile(flow)
run = asyncio.run(container.executor().run(compiled, {"diff": "- x\n+ y"}))
print(run.outputs("review"))   # ({'verdict': 'ok'},)
```

In a project the same composition usually comes from `./matrix.yaml` (or the `matrix:`
section of any tool's config), read by `load_config`:

```yaml
matrix:
  definitions: [agents/]                 # *.md agent files, Claude Code format
  runtimes:
    sdk:     {type: claude-sdk, permission_mode: default, setting_sources: []}
    local:   {type: model}
    offline: {type: mock}
  observers: [otel]                      # optional
  agents:
    reviewer: {runtime: sdk}                                    # agents/reviewer.md
    triage:   {runtime: local, definition: reviewer, tools: []} # same prompt, no tools
    greeter:  {runtime: offline, system_prompt: "Say hello."}   # fully inline
```

A runtime that cannot honour a definition refuses it at composition: the `model` runtime makes
one call with no tool loop, so an agent bound to it that declares tools is a `ConfigError`,
not a quiet downgrade.

## Extending

An extension is a package with an entry point in the `matrix.extensions` group whose value is
`register(registry) -> None`:

```toml
[project.entry-points."matrix.extensions"]
acme = "acme.matrix_ext:register"
```

```python
def register(registry):
    registry.register(
        "component",
        "acme.v1.scorer",
        Scorer,                     # called as Scorer(validated_config, **needs)
        config=ScorerConfig,        # a pydantic model; unknown keys fail naming the type URL
        effects=set(),              # what it may touch; None means unknown
        summary="Scores a diff by length",
        stability="experimental",
    )
    registry.register_payload("acme.v1.review", Review)   # outputs of this type are validated
```

The [API reference](docs/reference/api.md#the-registry) has the complete extension, with
`Scorer`, its config and the `Review` payload. An extension that fails to import or register
is quarantined: its entries are dropped, the failure is recorded, and the others load. A
package owns the type URLs under its own name (`acme`
owns `acme.*`): when two extensions register the same type URL, the owner keeps it and the
other is quarantined, so nothing can replace a built-in by loading first.

## The `matrix` command

Read-only: it inspects what is installed and whether a config composes, and never runs a flow
or calls a model.

```bash
matrix catalog [--point P] [--json]           # every registered extension, and any that failed
matrix describe TYPE_URL [--json]             # config schema, needs, effects, origin
matrix config [--tool T] [--sources] [--json] # the merged config and the files it came from
matrix check [--tool T] [--json]              # compose runtimes and agents; call nothing
matrix agents [--tool T] [--json]             # the agents a config defines
```

Exit codes: 0 ok, 1 failure, 2 usage, 3 config, 4 not found, 5 transient, 6 auth.

## Documentation

| Document | What it covers |
|----------|----------------|
| [What is matrix?](docs/explanation/what-is-matrix.md) | The model: components, ports, topics, flows, runs, agents, the registry |
| [The data model](docs/explanation/data-model.md) | Artifact, Construct, Run, and how a run is saved |
| [API reference](docs/reference/api.md) | Every public name, config key, error kind, exit code and CLI command |
| [SECURITY.md](SECURITY.md) | Agent authority, config as a capability grant, findings |

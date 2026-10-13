# matrix

matrix runs components wired by the kind of data they exchange, and composes agents from
config.

- A **component** declares the kinds of data it requires and the kinds it provides. A **kind**
  is a type URL (`acme.v1.review`). Nobody draws an edge: a component waits for the provider
  of each kind it requires. matrix checks that wiring before anything runs, reports every
  problem at once, starts each component as soon as what it requires is ready, and records
  every row in a **Construct**, one table per kind.
- A run is **recorded as it goes**. A component can hand a row over while it is still
  running, and the row is on disk before that call returns. A run that is killed leaves
  everything it had recorded, readable, with a status.
- An **agent** is a definition (prompt, tools, model) bound to a runtime (Claude Agent SDK,
  one model call through hardline, or a mock). The binding is checked when it is made, and
  `matrix.v1.agent-step` runs an agent as an ordinary component.
- One **registry** holds every extension: runtimes, components, payload types, observers.
  matrix's built-ins arrive through the same `matrix.extensions` entry point as anyone else's.

```bash
uv add "matrix @ git+https://github.com/mox-labs/gnx#subdirectory=components/capabilities/matrix"
# extras: [claude] Claude Agent SDK runtime · [models] model runtime (hardline) · [otel] spans and metrics
```

## Components, wired by kind

```python
import asyncio

from matrix import Executor, wire

TEXT, SHOUT, COUNT = "demo.v1.text", "demo.v1.shout", "demo.v1.count"


class Upper:
    requires = {TEXT}
    provides = {SHOUT}

    async def run(self, inputs, ctx):
        return {SHOUT: [line.upper() for line in inputs[TEXT]]}


class Count:
    requires = {SHOUT}
    provides = {COUNT}

    async def run(self, inputs, ctx):
        return {COUNT: sum(len(line) for line in inputs[SHOUT])}


wiring = wire({"count": Count(), "upper": Upper()}, inputs=[TEXT])
run = asyncio.run(Executor().run(wiring, {TEXT: ["hello", "world"]}))

print(wiring.order())      # ('upper', 'count'): inferred, whatever order they were given in
print(run.status)          # completed
print(run.rows(SHOUT))     # ('HELLO', 'WORLD')
print(run.rows(COUNT))     # (10,)
```

A component is structural: a class with `requires`, `provides` and an async
`run(inputs, ctx)` is one, without importing matrix. Its **name** is the name it is given in
the run, so one class can be used twice.

- `inputs` holds the rows of the kinds the component requires, by kind, and nothing else. It
  never sees the Construct or another component's rows.
- `run` returns a mapping of kind to one row, or to a list of rows. It may return nothing.
- matrix holds it to what it declared: a row of a kind it does not provide, or one that fails
  the kind's registered schema, is a `ContractError`.

`wire` raises one `CompilationError` listing every problem: a required kind that nothing
provides and that is not a run input, a kind with two providers, a cycle. Each kind has
exactly one provider; two streams of the same shape are two kinds. The kinds the caller
supplies when the run starts are declared as `inputs`. A wiring runs any number of times.

## A record that survives the run

```python
from matrix import Executor, JsonlConstructStore, wire

TRIAL = "demo.v1.trial"


class Trials:
    requires = set()
    provides = {TRIAL}

    async def run(self, inputs, ctx):
        for seq in range(1000):
            if ctx.stopping.is_set():
                break
            result = await one_trial(seq)                # minutes each
            ctx.provide(TRIAL, {"seq": seq, **result})   # recorded and on disk, now


store = JsonlConstructStore("runs")
run = await Executor().run(wire({"trials": Trials()}), store=store, run_id="soak-1")
```

`ctx.provide(kind, row)` hands over one row while the component is still running. When it
returns, the row has been checked against the kind's schema, is part of the run's record, and
is durable in the store. Kill the process at trial 700 and 700 trials are there:

```python
store.describe("soak-1")["status"]      # 'interrupted' (it was 'running' while it ran)
store.load("soak-1").rows(TRIAL)        # the 700 rows
```

On disk a run is a directory: `run.json`, and one JSONL file per kind whose lines are the
rows themselves, readable with `jq` or DuckDB without matrix. Provenance is one more table,
the ledger (`matrix.v1.ledger`): which component handed each row over, when, and in what
order. A ledger entry is written before its row, so a kill can leave an entry with no row but
never a row nothing accounts for; a reader reports such an entry as torn.

A store that cannot keep the record fails the run (error kind `store`). A value too large to
be a row (a sample series, a capture) stays where it is and the row carries a
`matrix.v1.ref` to it.

## Stopping, and cleaning up

A run ends `completed`, `failed` or `cancelled`, and every component in it ends `completed`,
`failed`, `cancelled` or `skipped` (`run.components`). However a component comes to be
stopped (its deadline passed, another component failed, the caller cancelled, the process got
SIGTERM or Ctrl-C), it is stopped the same way: `ctx.stopping` is set, it has `grace_s` to
return what it has, then it is cancelled. When the run ends, everything components handed to
`ctx.defer` is called, last first.

```python
from matrix import ComponentLimits, Limits, process


class Load:
    requires = set()
    provides = {"demo.v1.report"}

    async def run(self, inputs, ctx):
        server = await process.spawn(ctx, ["my-server", "--port", "8080"])  # its kill is deferred
        ctx.defer(lambda: print("released"))        # runs on success, failure and cancel alike
        while not ctx.stopping.is_set():
            ctx.measure("demo.requests", await poke(), kind="counter")
        return {"demo.v1.report": "stopped when asked"}


limits = Limits(
    deadline_s=60,                                   # each component, from when it starts
    on_failure="fast",                               # or "isolate": only dependents are skipped
    components={"soak": ComponentLimits(deadline_s=4 * 3600)},
)
```

A failed run raises `RunError` and a cancelled one `RunCancelled` (a `CancelledError`); both
carry the partial `Run`, and both are raised only after the components stopped and the
releases ran. `run.releases` says what became of each release; one that raises or overruns
`cleanup_s` is reported there and does not stop the others. A component that swallows
cancellation is abandoned after `cleanup_s` and marked so. `process.spawn` starts a child in
its own process group and defers killing the group, so a tool that forks leaves nothing
behind.

Two things this cannot do: stop a component that blocks the event loop with synchronous work,
and run a release after `kill -9` of the run itself. What `kill -9` does not take is the
record.

## Watching a run

Runs narrate themselves as events (`matrix.run.start`, `matrix.component.end`,
`matrix.row.append`, ...), and a component adds its own with `ctx.emit`, `ctx.measure` and
`ctx.span`, under its own root. Three observers ship: `console` (progress on stderr), `jsonl`
(one JSON document per event, to a file or stderr) and `otel` (spans and metrics).

```python
from matrix import Executor, default_registry

events = default_registry().create(
    "observer", "matrix.v1.observer.jsonl", {"path": "run.events.jsonl"}
)
run = await Executor([events]).run(wiring, {TEXT: ["hello"]})
```

In config they go under `matrix.observers`, and `compose(...).executor()` has them attached.

Events are telemetry, not data. They pass through a bounded queue, so an observer that is
slow, stuck or broken cannot hold a run up; when the queue is full events are dropped and
counted (`run.events_dropped`, and a `matrix.events.dropped` event). Read results from the
`Run` or the store, never from the stream.

## Agents

```python
import asyncio

from matrix import MatrixConfig, compose

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

# The same agent as a component, through matrix.v1.agent-step.
wiring = container.wire(
    [
        {
            "name": "review",
            "type": "matrix.v1.agent-step",
            "config": {
                "agent": "reviewer",
                "inputs": {"diff": "acme.v1.diff"},       # it requires acme.v1.diff
                "task": "Review this change:\n{diff}",
                "output": "acme.v1.review",                # and provides acme.v1.review
            },
        }
    ],
    inputs=["acme.v1.diff"],
)
run = asyncio.run(container.executor().run(wiring, {"acme.v1.diff": "- x\n+ y"}))
print(run.rows("acme.v1.review"))   # ({'verdict': 'ok'},)
```

`Container.wire` takes a list of `{name, type, config}` entries, builds each through the
registry, and wires them. In a project the list, like the rest of the composition, usually
comes from `./matrix.yaml` (or the `matrix:` section of any tool's config), read by
`load_config`:

```yaml
matrix:
  definitions: [agents/]                 # *.md agent files, Claude Code format
  runtimes:
    sdk:     {type: claude-sdk, permission_mode: default, setting_sources: []}
    local:   {type: model}
    offline: {type: mock}
  observers:                             # optional
    - console
    - {type: jsonl, path: runs/events.jsonl}
  agents:
    reviewer: {runtime: sdk}                                    # agents/reviewer.md
    triage:   {runtime: local, definition: reviewer, tools: []} # same prompt, no tools
    greeter:  {runtime: offline, system_prompt: "Say hello."}   # fully inline
  inputs: [acme.v1.diff]                 # the kinds whoever runs this supplies
  components:                            # wired by what they require and provide
    - name: review
      type: matrix.v1.agent-step
      config: {agent: reviewer, inputs: {diff: acme.v1.diff}, output: acme.v1.review}
```

`container.wire()` with no arguments wires what the config lists.

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
    registry.register_payload("acme.v1.review", Review)   # rows of this kind are validated
```

The [API reference](docs/reference/api.md#the-registry) has the complete extension, with
`Scorer`, its config and the `Review` payload. An extension that fails to import or register
is quarantined: its entries are dropped, the failure is recorded, and the others load. A
package owns the type URLs under its own name (`acme`
owns `acme.*`): when two extensions register the same type URL, the owner keeps it and the
other is quarantined, so nothing can replace a built-in by loading first.

## The `matrix` command

Read-only: it inspects what is installed and whether a config composes, and never runs a
component or calls a model.

```bash
matrix catalog [--point P] [--json]           # every registered extension, and any that failed
matrix describe TYPE_URL [--json]             # config schema, needs, effects, origin
matrix config [--tool T] [--sources] [--json] # the merged config and the files it came from
matrix check [--tool T] [--json]              # compose runtimes and agents, wire the components; call nothing
matrix agents [--tool T] [--json]             # the agents a config defines
```

Exit codes: 0 ok, 1 failure, 2 usage, 3 config, 4 not found, 5 transient, 6 auth.

## Documentation

| Document | What it covers |
|----------|----------------|
| [What is matrix?](docs/explanation/what-is-matrix.md) | The model: components, kinds, wiring, runs, agents, the registry |
| [The data model](docs/explanation/data-model.md) | Construct, ledger, Run, and how a run is kept on disk |
| [API reference](docs/reference/api.md) | Every public name, config key, error kind, exit code and CLI command |
| [SECURITY.md](SECURITY.md) | Agent authority, config as a capability grant, findings |

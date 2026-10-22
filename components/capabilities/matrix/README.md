# matrix

matrix runs components wired by the kind of data they exchange, composes agents from config,
and runs what a config declares from the command line: `matrix plan`, then `matrix run`.

- A **component** declares the kinds of data it requires and the kinds it provides. A **kind**
  is a type URL (`acme.v1.review`). Nobody draws an edge: a component waits for the provider
  of each kind it requires. matrix checks that wiring before anything runs, reports every
  problem at once, starts each component as soon as what it requires is ready, and records
  every row in a **Construct**, one table per kind.
- A run is **recorded as it goes**. A component can hand a row over while it is still
  running, and the row is on disk before that call returns. A run that is killed leaves
  everything it had recorded, readable, with a status.
- An **agent** is a definition (prompt, tools, model) bound to an agent runtime: a session
  with a loop and tools (the Claude Agent SDK, or a mock). The binding is checked when it is
  made, and `matrix.v1.agent-step` runs an agent as an ordinary component.
- A **model call** is one request to one model, with no loop and no tools, on a model runtime
  (hardline, or a mock). `matrix.v1.model-step` makes one an ordinary component, and records
  the model that answered, its usage and its request id.
- One **registry** holds every extension: agent and model runtimes, components, payload
  types, observers, stores. matrix's built-ins arrive through the same `matrix.extensions` entry point as
  anyone else's.
- One **loader** reads every document matrix reads, and any YAML or markdown document you
  name a pydantic type for, reporting every problem with its key path and its file.

```bash
# the library, in a project
uv add "matrix @ git+https://github.com/mox-labs/gnx@matrix-v0.6.0#subdirectory=components/capabilities/matrix"
# the matrix command
uv tool install "git+https://github.com/mox-labs/gnx@matrix-v0.6.0#subdirectory=components/capabilities/matrix"
```

matrix is installed from its repository at the release's tag; it is not on a package index,
and `pip install matrix` installs an unrelated project. The extras are `claude` (the Claude
Agent SDK agent runtime), `models` (the hardline model runtime) and `otel` (the
OpenTelemetry SDK and OTLP exporters). Add one the same way,
`uv add "matrix[otel] @ git+https://github.com/mox-labs/gnx@matrix-v0.6.0#subdirectory=components/capabilities/matrix"`,
or to an installed tool with `uv tool install --with "matrix[otel] @ git+..." <tool>`.

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

print(wiring.order())  # ('upper', 'count'): inferred, whatever order they were given in
print(run.status)  # completed
print(run.rows(SHOUT))  # ('HELLO', 'WORLD')
print(run.rows(COUNT))  # (10,)
```

A component is structural: a class with `requires`, `provides` and an async
`run(inputs, ctx)` is one, without importing matrix. Its **name** is the name it is given in
the run, so one class can be used twice.

- `inputs` holds the rows of the kinds the component requires, by kind, and nothing else. It
  never sees the Construct or another component's rows.
- `run` returns a mapping of kind to one row, or to a list of rows. It may return nothing.
- matrix holds it to what it declared: a row of a kind it does not provide, or one that fails
  the kind's schema (when the wiring carries one), is a `ContractError`. A row of a kind with
  a schema is recorded as an instance of it, so a dict handed over reaches the components
  that require the kind as that model, on a fresh run and on one that reuses the rows.

`wire` raises one `CompilationError` listing every problem: a required kind that nothing
provides and that is not a run input, a kind with two providers, a cycle. Each kind has
exactly one provider; two streams of the same shape are two kinds. The kinds the caller
supplies when the run starts are declared as `inputs`. A wiring runs any number of times.

## A record that survives the run

```python
import asyncio

from matrix import Executor, JsonlConstructStore, wire

TRIAL = "demo.v1.trial"


async def one_trial(seq):  # stands for minutes of work
    await asyncio.sleep(1)
    return {"ok": True}


class Trials:
    requires = set()
    provides = {TRIAL}

    async def run(self, inputs, ctx):
        for seq in range(1000):
            if ctx.stopping.is_set():
                break
            result = await one_trial(seq)
            ctx.provide(TRIAL, {"seq": seq, **result})  # recorded and on disk, now


store = JsonlConstructStore("runs")
run = asyncio.run(Executor().run(wire({"trials": Trials()}), store=store, run_id="soak-1"))
```

`ctx.provide(kind, row)` hands over one row while the component is still running. When it
returns, the row has been checked against the kind's schema, is part of the run's record, and
is durable in the store. Kill the process at trial 700 and 700 trials are there:

```python
store.describe("soak-1")["status"]  # 'interrupted' (it was 'running' while it ran)
store.load("soak-1").rows(TRIAL)  # the 700 rows
```

On disk a run is a directory: `run.json`, one JSONL file per kind whose lines are the rows
themselves (readable with `jq` or DuckDB without matrix), and the `writer.lock` its writer
holds while it lives. Provenance is one more table,
the ledger (`matrix.v1.ledger`): which component handed each row over, when, and in what
order. A ledger entry is written before its row, so a kill can leave an entry with no row but
never a row nothing accounts for; a reader reports such an entry as torn.

A store that cannot keep the record fails the run (error kind `store`). A value too large to
be a row (a sample series, a capture) stays where it is and the row carries a
`matrix.v1.ref` to it.

## Stopping, and cleaning up

A run ends `completed`, `failed` or `cancelled`, and every component in it ends `completed`,
`stopped`, `failed`, `cancelled`, `skipped` or `reused` (`run.components`). However a component
comes to be stopped (its deadline passed, another component failed, the caller cancelled, the
process got SIGTERM or Ctrl-C), it is stopped the same way: `ctx.stopping` is set, it has
`grace_s` to return what it has, then it is cancelled. What it returns is recorded, and it
ends `stopped` (`failed` past its deadline): the rows may be partial, so `matrix run --from`
runs it again rather than reuse them. When the run ends, everything components handed to
`ctx.defer` is called, last first.

```python
from matrix import ComponentLimits, Limits, Retry, process


class Load:
    requires = set()
    provides = {"demo.v1.report"}

    async def run(self, inputs, ctx):
        server = await process.spawn(ctx, ["my-server", "--port", "8080"])  # its kill is deferred
        ctx.defer(lambda: print("released"))  # runs on success, failure and cancel alike
        while not ctx.stopping.is_set():
            ctx.measure("demo.requests", await poke(), kind="counter")
        return {"demo.v1.report": "stopped when asked"}


limits = Limits(
    deadline_s=60,  # each component, from when it starts
    on_failure="fast",  # or "isolate": only dependents are skipped
    retry=Retry(attempts=3, errors=("transient",)),  # rate limits, timeouts, outages
    components={"soak": ComponentLimits(deadline_s=4 * 3600)},
)
```

A retry runs a failed component again when its error is transient (or the reason the policy
names), after a backoff or the `Retry-After` the backend reported, capped. A component that
already handed rows over is not retried: its rows are part of the record.

A failed run raises `RunError` and a cancelled one `RunCancelled` (a `CancelledError`); both
carry the partial `Run`, and both are raised only after the components stopped and the
releases ran. `run.releases` says what became of each release; one that raises or overruns
`cleanup_s` is reported there and does not stop the others. A component that swallows
cancellation is abandoned after `cleanup_s` and marked so. `process.spawn` starts a child in
its own process group and defers killing the group, so a tool that forks leaves nothing
behind. A component that starts a child per repetition of its work uses `process.start` and
`process.kill_group` around each one instead, and defers nothing per child.

Two things this cannot do: stop a component that blocks the event loop with synchronous work,
and run a release after `kill -9` of the run itself. What `kill -9` does not take is the
record.

## Watching a run

Runs narrate themselves as events (`matrix.run.start`, `matrix.component.end`,
`matrix.row.append`, ...), and a component adds its own with `ctx.emit`, `ctx.measure` and
`ctx.span`, under its own root. A run kept by the JSONL store writes them into its record,
`events.jsonl`, with nothing configured. The built-in observers are `console` (progress on
stderr), `jsonl` (one JSON document per event, to a file or stderr) and `otel` (spans and
metrics on the OpenTelemetry GenAI conventions: `invoke_workflow`, `invoke_agent`,
`execute_tool`).

For OpenTelemetry, install the `otel` extra and set the standard variables: an
`OTEL_EXPORTER_OTLP_ENDPOINT`, or `OTEL_TRACES_EXPORTER=console`, is enough for `matrix run`
to export. matrix as a library only uses the API and installs no provider: a program calls
`matrix.configure_telemetry()` once (and `.shutdown()` on what it returns), or configures
OpenTelemetry itself. Export asked for without the extra is a warning in every `--json`
document, never silence. Spans carry no task, error message or component text unless the
config sets `observers: [{type: otel, capture_content: true}]`.

```python
from matrix import Executor, default_registry

events = default_registry().create(
    "observer", "matrix.v1.observer.jsonl", {"path": "run.events.jsonl"}
)
run = asyncio.run(Executor([events]).run(wiring, {TEXT: ["hello"]}))  # the wiring above
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
        "agent_runtimes": {"offline": {"type": "mock", "default": '{"verdict": "ok"}'}},
        "agents": {
            "reviewer": {"runtime": "offline", "system_prompt": "Review the change."},
        },
    }
)
container = compose(config)

# An agent on its own.
response = asyncio.run(container.agent("reviewer").run("Review: rename x to y"))
print(response.content, response.stop, response.family)  # {"verdict": "ok"} completed mock

# The same agent as a component, through matrix.v1.agent-step.
wiring = container.wire(
    [
        {
            "name": "review",
            "type": "matrix.v1.agent-step",
            "config": {
                "agent": "reviewer",
                "inputs": {"diff": "acme.v1.diff"},  # it requires acme.v1.diff
                "task": "Review this change:\n{diff}",
                "output": "acme.v1.review",  # and provides acme.v1.review
            },
        }
    ],
    inputs=["acme.v1.diff"],
)
run = asyncio.run(container.executor().run(wiring, {"acme.v1.diff": "- x\n+ y"}))
print(run.rows("acme.v1.review"))  # ({'verdict': 'ok'},)
```

`Container.wire` takes a list of `{name, type, config}` entries, builds each through the
registry, and wires them. In a project the list, like the rest of the composition, usually
comes from `./matrix.yaml` (or the `matrix:` section of any tool's config), read by
`load_config`:

```yaml
matrix:
  definitions: [agents/]                 # *.md agent files, Claude Code format
  models:                                # a hardline registry, for `type: hardline` runtimes
    default: qwen3-8b
    models:
      qwen3-8b: {backend: openai-compat, base_url: "http://127.0.0.1:8080/v1",
                 model: mlx-community/Qwen3-8B-4bit, family: qwen, local: true}
  agent_runtimes:                        # sessions, with a loop and tools
    sdk:     {type: claude-sdk, permission_mode: default, setting_sources: []}
    offline: {type: mock}
  model_runtimes:                        # one model call each
    local:   {type: hardline}
  observers:                             # optional
    - console
    - {type: jsonl, path: runs/events.jsonl}
  agents:
    reviewer: {runtime: sdk}                                    # agents/reviewer.md
    greeter:  {runtime: offline, system_prompt: "Say hello."}   # fully inline
  inputs: [acme.v1.diff]                 # the kinds whoever runs this supplies
  components:                            # wired by what they require and provide
    - name: review
      type: matrix.v1.agent-step
      config: {agent: reviewer, inputs: {diff: acme.v1.diff}, output: acme.v1.review}
    - name: triage                       # one call, no tools
      type: matrix.v1.model-step
      config: {runtime: local, model: qwen3-8b, inputs: {diff: acme.v1.diff},
               prompt: "Is this change risky? Answer yes or no.\n{diff}"}
```

`container.wire()` with no arguments wires what the config lists.

Two steps in one run each name their own `response` kind: a kind has one provider, and each
default (`matrix.v1.agent-response`, `matrix.v1.model-call`) serves one step. An agent step
with `output` needs the reply to be one JSON document, so say so in the agent's prompt or the
task; a model step asks its runtime for the kind's registered schema when there is one. An
agent's `model` is its runtime's to read: on `claude-sdk` an alias (`haiku`, `sonnet`,
`opus`) or a full model id. A model step's `model` is a hardline registry name, checked when
the step is built. A model step never provides a reply that is not a whole answer: one cut at
`max_tokens`, refused, withheld or empty fails the step, naming its `finish_reason`. The `mock` runtimes are stand-ins (`stand_in` in the catalog and the
plan); the agent one replies by the whole task as sent (`responses`), then by agent name
(`by_agent`), then `default` (`matrix --skill -r agents` has the detail).

Each runtime refuses what it cannot play before anything runs (`matrix check` and `matrix
plan` say so), with the fix: an agent bound
to a model runtime (a call has no loop and no tools), a model name hardline cannot resolve, a
`claude-sdk` agent whose tools leave out `Skill` when the runtime gives it skills, an option
the installed SDK lacks. Nothing is quietly downgraded.

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
        Scorer,  # called as Scorer(validated_config, **needs)
        config=ScorerConfig,  # a pydantic model the config is validated against
        effects=set(),  # what it may touch; None means unknown
        summary="Scores a diff by length",
        stability="experimental",
    )
    registry.register_payload("acme.v1.review", Review)  # rows of this kind are validated
```

The [API reference](docs/reference/api.md#the-registry) has the complete extension, with
`Scorer`, its config and the `Review` payload. An extension that fails to import or register
is quarantined: its entries are dropped, the failure is recorded, and the others load. A
package owns the type URLs under its own name (`acme`
owns `acme.*`): when two extensions register the same type URL, the owner keeps it and the
other is quarantined, so nothing can replace a built-in by loading first.

## Running a config

The config declares a run: its `components`, its `inputs`, where it is recorded (`store`,
default JSONL under `.matrix/runs`) and its `limits` (deadlines, concurrency, retries).
Plan it, then run it:

```bash
matrix plan --json                                  # order of work, effects, agents, calls; runs nothing
matrix run --input acme.v1.diff=diffs.jsonl --json  # one JSON row a line; - is standard input
matrix runs                                         # every recorded run: id, status, start, where
matrix run --from 20261019T101500Z-3f2a             # its inputs, reusing what completed unchanged
matrix plan live.yaml --only                        # one config on its own, none of the tiers
matrix run live.yaml --only --input acme.v1.diff=diffs.jsonl  # recorded in its store, .matrix/runs by default
```

`plan` composes and wires and reports every problem at once, each with its key path and the
file that set it, and how many agent sessions and model calls a run makes
(`agent_calls_min`, `agent_calls_max`, `model_calls_min`, `model_calls_max`). matrix states no cost and keeps no spending ceiling: a run's cost is not
something it can measure, so it does not pretend to. `run` prints the run id, where the record is, and its
files: `run.json`, one `<kind>.jsonl` per kind (an agent step's reply row carries the task as
sent, a model step's the prompt), `matrix.v1.ledger.jsonl` and `events.jsonl`. Each reply row keeps its tokens, and the cost
the runtime reported when it reported one (`cost_usd`), as facts of that session. From
Python it is `compose(load_config()).run(inputs)`, and
`plan_config(tool="matrix", config=None, *, only=False)` the plan: the first argument is the
tool, a path goes in `config`.

## Loading documents as your own types

```python
from typing import Annotated

from pydantic import BaseModel, Field

import matrix


class Probe(BaseModel):
    id: str
    prompt: str = Field("", alias="body")  # a markdown file's text arrives as `body`


class Experiment(BaseModel):
    name: str
    probes: Annotated[str, matrix.BesideFile()]  # a relative path is read from beside the file


experiment = matrix.load(Experiment, "lab/experiment.yaml")
probes = matrix.load_each(Probe, experiment.probes, key="id", name_from_file=True)
```

A YAML file is its mapping; a markdown file is its frontmatter with its text under `body`; a
folder is one document per file, in name order, a name defined twice refused. Every problem
in every file arrives at once, as `matrix.Problem` records (`path`, `kind`, `message`, `fix`,
`allowed`, `got`, `source`) in one `ConfigError`. `load_config` reads the config tiers the
same way.

## The `matrix` command

```bash
matrix --skill [-r NAME]                      # the skill an agent reads, or one of its references
matrix catalog [--point P] [--json]           # every registered extension, and any that failed
matrix describe TYPE_URL [--json]             # config schema, needs, effects, origin
matrix config [CONFIG]... [--sources] [--json]  # the merged config and the files it came from
matrix check [CONFIG]... [--json]           # compose runtimes and agents, wire the components; call nothing
matrix agents [CONFIG]... [--json]          # the agents a config defines
matrix plan [CONFIG]... [--json]            # what a run would do; runs nothing
matrix run [CONFIG]... [--input KIND=PATH]... [--run-id ID] [--from RUN_ID] [--json]
matrix runs [CONFIG]... [--json]            # the recorded runs, oldest first
```

Each reads the tiers of `--tool T` (default `matrix`) with each `CONFIG` above them, in order
(`-` is standard input); `--only` reads the `CONFIG` files alone. Mappings merge across tiers
and `definitions` directories accumulate, so a second config read on top of a project's
shares its runtimes, agents and definitions; `--only` keeps it to itself. Exit codes, as the capability tools agree: 0 ok, 1 a bug in matrix, 2 usage,
3 config, 4 not found, 5 transient, 6 auth, 8 a run failed, 9 store, 130 interrupted.
`matrix --help` lists each with what to do next. Under `--json` a failure is one
`matrix.v1.error` envelope on stderr, its problems structured.

## The stable public API

Before 1.0 a minor release may break anything; from this release, what a minor release
changes only by addition, with a changelog entry, is:

- **Components and runs**: `Component`, `Inputs`, `RunContext`, `wire`, `Wiring`, `Executor`,
  `Limits`, `ComponentLimits`, `Retry`, `Run`, `ComponentEnding`, `ComponentFailure`,
  `ReleaseOutcome`, `Construct`, `LedgerEntry`, `LEDGER`, `Ref`, `REF`, `process`.
- **Agents**: `AgentDefinition`, `AgentResponse`, `AgentRuntime`, `Agent`, `BoundAgent`,
  `AGENT_STEP`, `AGENT_RESPONSE`, `TASK`.
- **Config and composition**: `load_config`, `discover_sources`, `MatrixConfig`, `Config`,
  `compose`, `Container`, `default_registry`, `Registry`.
- **Loading**: `load`, `load_each`, `Problem`.
- **Errors**: `MatrixError` and its subclasses, `RunCancelled`, `EXIT_CODES`.
- **On the wire**: the `matrix.v1.*` document schemas, the `matrix.extensions` entry-point
  group, the type-URL grammar, the config tiers and keys, the run directory layout.

Every other exported name is public but may still change in a minor release, said in the
changelog: the extension-author surface (`Entry`, `Extension`, `Failure`, `Loader`,
`DocumentSource`, `Document`, `BesideFile`, `Accumulate`, `ConstructStore`, `RunWriter`,
the stores, `Reuse`, the observers' types), the command's document records (`Catalog`,
`Plan`, ...) and telemetry (`configure_telemetry`, `Telemetry`). Names under
`matrix.domain`, `matrix.adapters` and `matrix.composition` are internal.

## Documentation

| Document | What it covers |
|----------|----------------|
| [What is matrix?](docs/explanation/what-is-matrix.md) | The model: components, kinds, wiring, runs, agents, the registry |
| [The data model](docs/explanation/data-model.md) | Construct, ledger, Run, and how a run is kept on disk |
| [API reference](docs/reference/api.md) | Every public name, config key, error kind, exit code and CLI command |
| [SECURITY.md](SECURITY.md) | Agent authority, config as a capability grant, findings |
| [Design record](docs/design/2026-10-19-record.md) | The judgments behind 0.6.0, each with its test and evidence |

# Using matrix

How to write components, keep a run's record, compose agents and model calls, and run a
config. The [README](../README.md) has the install, the terms and a first run; the
[API reference](reference/api.md) has every name in full.

## Components, wired by kind

The README's [first run](../README.md#a-first-run) wires two components. The rules it
followed:

- A component is structural: a class with `requires`, `provides` and an async
  `run(inputs, ctx)` is one, without importing matrix.
- Its **name** is the name it is given in the run, so one class can be used twice.
- `inputs` holds the rows of the kinds the component requires, by kind, and nothing else. It
  never sees the Construct or another component's rows.
- `run` returns a mapping of kind to one row, or to a list of rows. It may return nothing.

What a row is depends on whether its kind has a **payload type**, a pydantic model
registered for the kind (`registry.register_payload`, below):

| the kind | a row reaches `run` as | read a field |
|---|---|---|
| has a payload type | an instance of that model, even when the provider handed over a dict | `review.verdict` |
| has none | the JSON data the provider handed over: a string, a dict, a list | `review["verdict"]` |

On disk both are JSON. So a component that requires a registered kind reads attributes:
`review["verdict"]` on a `Review` is `TypeError: 'Review' object is not subscriptable`.

matrix holds a component to what it declared. A row of a kind it does not provide, or one
that fails the kind's payload type, is a `ContractError`. The same holds on a run that reuses
recorded rows (`matrix run --from`).

`wire` raises one `CompilationError` listing every problem: a required kind that nothing
provides and that is not a run input, a kind with two providers, a cycle. Each kind has
exactly one provider; two streams of the same shape are two kinds. The kinds the caller
supplies when the run starts are declared as `inputs`. A wiring runs any number of times.

## Your own component in a config

In Python, you hand `wire` instances of your classes, as the first run does. A config names a
component by its **type URL** instead (`type: acme.v1.scorer`), and matrix finds it in its
registry. A component reaches the registry one way: from an installed package with a
`matrix.extensions` entry point. Until it is installed, `matrix check` says so:

```
error (config): the components cannot be wired (1 problem):
  matrix.components.score.type: no component 'acme.v1.scorer' is registered. Registered: matrix.v1.agent-step, matrix.v1.model-step (…/matrix.yaml)
    fix: install the extension that provides it, or fix the type URL
```

The whole path, as a package named `acme`:

```
acme/
  pyproject.toml
  src/acme/__init__.py        # empty
  src/acme/matrix_ext.py
matrix.yaml
diffs.jsonl
```

`acme/pyproject.toml`. The extension imports nothing of matrix, so for a local try it need not
depend on it; [publish your extension](#publish-your-extension) says how to declare the
matrix it was built against.

```toml
[project]
name = "acme"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["pydantic>=2"]

# One line per point the extension registers at, each naming its register function.
[project.entry-points."matrix.extensions"]
component = "acme.matrix_ext:register"
payload-type = "acme.matrix_ext:register"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/acme"]
```

`acme/src/acme/matrix_ext.py`:

```python
from pydantic import BaseModel, ConfigDict


class Review(BaseModel):
    """A review verdict."""

    verdict: str
    score: int


class ScorerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")  # an unknown key is refused, by its path

    threshold: int = 200


class Scorer:
    requires = {"acme.v1.diff"}
    provides = {"acme.v1.review"}

    def __init__(self, config: ScorerConfig) -> None:  # built with its validated config
        self.threshold = config.threshold

    async def run(self, inputs, ctx):
        reviews = []
        for diff in inputs["acme.v1.diff"]:
            verdict = "ok" if len(diff) < self.threshold else "long"
            reviews.append({"verdict": verdict, "score": len(diff)})
        return {"acme.v1.review": reviews}


class Tally:
    requires = {"acme.v1.review"}
    provides = {"acme.v1.tally"}

    async def run(self, inputs, ctx):
        reviews = inputs["acme.v1.review"]  # Review instances: the kind has a payload type
        return {"acme.v1.tally": {"long": sum(r.verdict == "long" for r in reviews)}}


def register(registry):
    registry.register(
        "component",
        "acme.v1.scorer",
        Scorer,  # called as Scorer(validated_config, **needs)
        config=ScorerConfig,  # what the config's `config:` mapping is validated against
        summary="Scores a diff by length",
        stability="experimental",
    )
    registry.register("component", "acme.v1.tally", Tally, summary="Counts long diffs")
    registry.register_payload("acme.v1.review", Review)  # rows of this kind are Review instances
```

`matrix.yaml` and `diffs.jsonl`, one JSON row a line:

```yaml
matrix:
  inputs: [acme.v1.diff]
  components:
    - name: score
      type: acme.v1.scorer
      config: {threshold: 10}
    - name: tally                  # requires what score provides, so it runs after it
      type: acme.v1.tally
```

```bash
printf '%s\n' '"- x\n+ y"' '"- a long line that was here\n+ a long line that replaced it"' > diffs.jsonl
```

Install the package into the environment matrix runs in, then run it:

```bash
uv pip install -e ./acme           # into the venv that has matrix
# or, for the matrix command installed as a uv tool:
uv tool install --with-editable ./acme "git+https://github.com/mox-labs/gnx@matrix-v0.10.0#subdirectory=components/capabilities/matrix"
# or, from a clone of gnx (C=gnx/components/capabilities), with the bridge for model calls:
uv pip install $C/matrix $C/hardline $C/matrix-hardline -e ./acme                    # a venv
uv tool install $C/matrix --with $C/hardline --with $C/matrix-hardline --with-editable ./acme

matrix catalog --point component   # the extension loaded
matrix plan
matrix run --input acme.v1.diff=diffs.jsonl
```

```
component     acme.v1.scorer                       experimental Scores a diff by length
component     acme.v1.tally                        beta         Counts long diffs
component     matrix.v1.agent-step                 beta         An agent as a component: …
component     matrix.v1.model-step                 beta         One model call as a component: …

run 20261107T214959Z-fe58 completed: .matrix/runs/20261107T214959Z-fe58
  acme.v1.review.jsonl     2 rows of acme.v1.review
  acme.v1.tally.jsonl      1 row of acme.v1.tally
  …
```

`.matrix/runs/<run>/acme.v1.review.jsonl` holds `{"verdict": "ok", "score": 7}` and
`{"verdict": "long", "score": 58}`, and `acme.v1.tally.jsonl` holds `{"long": 1}`: `Tally`
read each review as a `Review`. `plan` lists `threshold = 10` with the file that set it.

What the names must be:

- A type URL is `<namespace>.v<N>.<name>`: `acme.v1.scorer`, never `acme.scorer`.
- A package owns the type URLs under its own name: `acme` owns `acme.*`. When two extensions
  register the same type URL, the owner keeps it and the other is quarantined, so nothing can
  replace a built-in by loading first.
- An entry point's name is a point its extension registers at: `component`, `payload-type`,
  `agent-runtime`, `model-runtime`, `observer`, `store`. A session loads only the extensions
  that declare a point it reads.

An extension that fails to import or register is quarantined: its entries are dropped, the
failure is recorded (`matrix catalog` lists it as `FAILED`), and the others load. So is one
that registers at a point its entry points do not name; the message says which line to add.

### Publish your extension

Nobody needs a package index to use your extension: push it to a git repository, tag a
release, and install from the tag. matrix itself is installed the same way, and is on no
index: the names `matrix` and `hardline` on PyPI belong to unrelated projects.

Declare the matrix you built against. Before 1.0 a matrix minor release may break the
component-author contract ([CONTRACT.md](../CONTRACT.md)), so bound it to the minor you
tested, and tell uv where matrix comes from:

```toml
[project]
dependencies = [
    "pydantic>=2",
    "matrix>=0.10,<0.11",  # before 1.0 a minor may break the contract
]

# matrix is on no package index: resolve it from its repository, at its release tag
[tool.uv.sources]
matrix = { git = "https://github.com/mox-labs/gnx", rev = "matrix-v0.10.0", subdirectory = "components/capabilities/matrix" }
```

Give the tag as `rev =`, so it matches the `@tag` of the install line below: written another
way (`branch =`, tried) uv reports "conflicting URLs for package `matrix`". A user then
installs matrix with your extension beside it (add the bridge's `--with` line for model
calls):

```bash
uv tool install "git+https://github.com/mox-labs/gnx@matrix-v0.10.0#subdirectory=components/capabilities/matrix" \
  --with "acme @ git+https://github.com/you/acme@v0.1.0"
# or into a project or venv: matrix resolves through acme's [tool.uv.sources]
uv pip install "acme @ git+https://github.com/you/acme@v0.1.0"
```

Name your type URLs under your package's own name (`acme.*`, the rules above). A name under
another package's root loses to that package whenever both register it, and your whole
extension is quarantined. `matrix catalog` on the user's machine shows whether it loaded.

## A record that survives the run

```python
import asyncio

from matrix import Executor, JsonlConstructStore, Limits, wire

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
soak = Limits(deadline_s=None)  # a soak run: no deadline (unset, each component has an hour)
run = asyncio.run(
    Executor().run(wire({"trials": Trials()}), store=store, run_id="soak-1", limits=soak)
)
```

`ctx.provide(kind, row)` hands over one row while the component is still running. When it
returns, the row has been checked against the kind's schema, is part of the run's record, and
is durable in the store. Kill the process at trial 700 and 700 trials are there:

```python
store.describe("soak-1")["status"]  # 'interrupted' (it was 'running' while it ran)
store.load("soak-1").rows(TRIAL)  # the 700 rows
```

On disk a run is a directory:

- `run.json`: the run's status, how each component ended, its failures;
- one JSONL file per kind, whose lines are the rows themselves, readable with `jq` or DuckDB
  without matrix;
- `writer.lock`, which its writer holds while it lives.

Two more tables say where each row came from:

- **The ledger** (`matrix.v1.ledger`): which component handed each row over, when, and in
  what order. An entry is written before its row, so a kill can leave an entry with no row but
  never a row nothing accounts for; a reader reports such an entry as torn.
- **The session table** (`matrix.v1.session`): every agent session and model call a
  component runs, a failed one included. Each row says whether it failed and why, the
  runtime, the model that answered, why it stopped, usage, ids (the runtime's, and the
  OpenTelemetry trace and span it ended in), fallback and attempts, and a digest of what was
  sent. A failed or cancelled one keeps the task or prompt as sent, in the record only.

A step's reply row points at its session row (`session`), and the ledger entry of every row a
component hands over names the sessions behind it. [The data model](explanation/data-model.md)
joins the three in DuckDB.

A store that cannot keep the record fails the run (error kind `store`). A value too large to
be a row (a sample series, a capture) stays where it is, and the row carries a `matrix.v1.ref`
to it.

## Stopping, and cleaning up

A run ends `completed`, `failed` or `cancelled`, and every component in it ends `completed`,
`stopped`, `failed`, `cancelled`, `skipped` or `reused` (`run.components`).

However a component comes to be stopped (its deadline passed, another component failed, the
caller cancelled, the process got SIGTERM or Ctrl-C), it is stopped the same way:
`ctx.stopping` is set, it has `grace_s` to return what it has, then it is cancelled. What it
returns is recorded, and it ends `stopped` (`failed` past its deadline). The rows may be
partial, so `matrix run --from` runs it again rather than reuse them. When the run ends,
everything components handed to `ctx.defer` is called, last first.

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
    deadline_s=60,  # each component, from when it starts; unset, an hour; None, no deadline
    on_failure="fast",  # or "isolate": only dependents are skipped
    retry=Retry(attempts=3, errors=("transient",)),  # rate limits, timeouts, outages
    components={"soak": ComponentLimits(deadline_s=4 * 3600)},  # None: this one has none
)
```

A component past its deadline is stopped through the stop path and fails. Its failure names
the key that set the deadline (`matrix.limits.deadline_s`, or the component's own
`matrix.limits.components.<name>.deadline_s`), to raise or to set to null.

A retry runs a failed component again when its error is transient (or the reason the policy
names), after a backoff or the `Retry-After` the backend reported, capped. A component that
already handed rows over is not retried: its rows are part of the record.

A failed run raises `RunError` and a cancelled one `RunCancelled` (a `CancelledError`). Both
carry the partial `Run`, and both are raised only after the components stopped and the
releases ran:

- `run.releases` says what became of each release; one that raises or overruns `cleanup_s`
  is reported there and does not stop the others.
- A component that swallows cancellation is abandoned after `cleanup_s` and marked so.
- `process.spawn` starts a child in its own process group and defers killing the group, so a
  tool that forks leaves nothing behind. A component that starts a child per repetition of
  its work uses `process.start` and `process.kill_group` around each one instead.

Two things this cannot do: stop a component that blocks the event loop with synchronous work,
and run a release after `kill -9` of the run itself. What `kill -9` does not take is the
record.

## Watching a run

Runs narrate themselves as events (`matrix.run.start`, `matrix.component.end`,
`matrix.row.append`, ...), and a component adds its own with `ctx.emit`, `ctx.measure` and
`ctx.span`, under its own root. A run kept by the JSONL store writes them into its record,
`events.jsonl`, with nothing configured.

The built-in observers:

| observer | what it does |
|---|---|
| `console` | progress on stderr |
| `jsonl` | one JSON document per event, to a file or stderr |
| `otel` | spans and metrics on the OpenTelemetry GenAI conventions (`invoke_workflow`, `invoke_agent`, `execute_tool`); a span a library opens inside a component, an agent session or a model call nests under matrix's |

For OpenTelemetry, set the standard variables: an `OTEL_EXPORTER_OTLP_ENDPOINT`, or
`OTEL_TRACES_EXPORTER=console`, is enough for `matrix run` to export. matrix as a library uses
only the API and installs no provider: a program calls `matrix.configure_telemetry()` once
(and `.shutdown()` on what it returns), or configures OpenTelemetry itself. Export asked for
in an install that lacks the SDK is a warning in every `--json` document, never silence.

Spans carry no task, tool arguments, error message or component text unless the config sets
`observers: [{type: otel, capture_content: true}]`.

```python
from matrix import Executor, default_registry

events = default_registry().create(
    "observer", "matrix.v1.observer.jsonl", {"path": "run.events.jsonl"}
)
run = asyncio.run(Executor([events]).run(wiring, {TEXT: ["hello"]}))  # the README's wiring
```

In config they go under `matrix.observers`, and `compose(...).executor()` has them attached.

Events are telemetry, not data: **lossy by design**. They pass through a bounded queue, so an
observer that is slow, stuck or broken cannot hold a run up. When the queue is full, events
are dropped and counted (`run.events_dropped`, `events_dropped` in `run.json`, and a
`matrix.events.dropped` event, which can itself be lost). Read every fact from the `Run` or
the store, never from the stream. An observer's optional `around`, entered in the task doing
the work, is the one part that can slow a run, so it must not block.

## Agents

An **agent** is a definition (its prompt, tools and model) bound to an **agent runtime**: a
session with a loop of model calls and tool calls. matrix ships three agent runtimes:

| `type` | what runs the session |
|---|---|
| `claude-sdk` | the Claude Agent SDK: a Claude Code session, with its tools and permissions; needs Claude credentials |
| `pydantic-ai` | the pydantic-ai library's agent loop, every model call of it made on a model runtime ([below](#the-pydantic-ai-agent-runtime)) |
| `mock` | a stand-in: replies from config, offline |

The binding is checked when it is made, and `matrix.v1.agent-step` runs an agent as an
ordinary component:

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
  agent_runtimes:                        # sessions, with a loop and tools
    sdk:     {type: claude-sdk, permission_mode: default, setting_sources: []}
    offline: {type: mock}
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
```

`container.wire()` with no arguments wires what the config lists.

What to know about agent steps:

- Two steps in one run each name their own `response` kind: a kind has one provider, and the
  default (`matrix.v1.agent-response`) serves one step.
- A step with `output` needs the reply to be one JSON document, so say so in the agent's
  prompt or the task.
- An agent's `model` is its runtime's to read: on `claude-sdk` an alias (`haiku`, `sonnet`,
  `opus`) or a full model id, on `pydantic-ai` a name its model runtime resolves. The reply
  records the model that answered, which may not be the one asked for.
- One step, one session: every row of a required kind is rendered into the one task, several
  as a JSON list ([one call per row](#one-call-per-row) when you want a session each).
- An agent's reply is its session's final result; what it wrote along the way is the reply's
  `transcript`.
- The `mock` agent runtime replies by the whole task as sent (`responses`), then by agent
  name (`by_agent`), then `default`. It is a stand-in (`stand_in` in the catalog and the
  plan); `matrix --skill -r agents` has the detail.

## Model calls

A **model call** is one request to one model: its turns, the tools offered, an output type,
and no loop. It runs on a **model runtime**, and `matrix.v1.model-step` makes one an ordinary
component. The step records the model that answered, its usage, its request id and the
runtime's own record of the call.

matrix ships one model runtime, the `mock`, and depends on no runtime that calls a model.
One that does is an extension: [matrix-hardline](../../matrix-hardline/README.md) makes
hardline one, so a step can call any model family hardline has a row for.

| you have | `type` under `model_runtimes` |
|---|---|
| nothing running: try the wiring, the plan, the record | `mock` (the README's [first config](../README.md#a-first-config)) |
| a local model server (ollama, mlx_lm.server, vLLM, llama.cpp) or a hosted key | `matrix-hardline.v1.model-runtime`, with `matrix-hardline` installed |

A local model on ollama, serving `qwen3:8b` on its default port:

```yaml
matrix:
  model_runtimes:
    local:
      type: matrix-hardline.v1.model-runtime
      models:                            # a hardline registry; unset, hardline's own config files
        default: qwen
        models:
          qwen: {backend: openai-compat, base_url: "http://127.0.0.1:11434/v1",
                 model: "qwen3:8b", family: qwen, provider: ollama, local: true,
                 retries: 0}             # local dev: fail at once, not after three tries
  inputs: [acme.v1.diff]
  components:
    - name: triage                       # one call, no tools
      type: matrix.v1.model-step
      config: {runtime: local, model: qwen, inputs: {diff: acme.v1.diff},
               prompt: "Is this change risky? Answer yes or no.\n{diff}"}
```

For mlx_lm.server the row is `base_url: "http://127.0.0.1:8080/v1"` and
`model: mlx-community/Qwen3-8B-4bit`; [hardline's README](../../hardline/README.md) has the
fields. `matrix check` and `matrix plan` build the runtime and resolve the model name but
call nothing, so they pass with the server down. `matrix run` with nothing listening fails
with the fix:

```
error (transient): run 20261107T211457Z-aea7 failed at 'triage': model '(default)': model 'qwen': transient failures exhausted retries. Last: model 'qwen' (openai-compat → http://127.0.0.1:11434/v1): ConnectError: All connection attempts failed
fix: check that a server is listening at http://127.0.0.1:11434/v1 (start it, for a local model server), or point models.qwen.base_url at one that is
```

What to know about model steps:

- **One step, one call.** Every row of a required kind is rendered into the one prompt: one
  row as itself, several as one JSON list. Two diffs make one call, its `{diff}` the list of
  both, and one `matrix.v1.model-call` row. An agent step is the same: one session per
  step. For one call per row, see
  [one call per row](#one-call-per-row).
- Each names its own `response` kind when a run has two (the default is
  `matrix.v1.model-call`).
- A step's `model` is a name its model runtime resolves, checked when the step is built.
- With `output`, a step asks its runtime for the kind's registered schema when there is one.
- A step never provides a reply that is not a whole answer: one cut at `max_tokens`, refused,
  withheld or empty fails the step, naming its `finish_reason`.
- The `mock` model runtime replies by the whole prompt (`responses`), then by model name
  (`by_model`), then `default`; with none, it echoes `mock:<model>:<prompt>`.
- `matrix.pydantic_ai_model(runtime)` makes any model runtime a pydantic-ai `Model`, for a
  caller that runs pydantic-ai itself.

### One call per row

Neither step maps over rows. A component of your own does: it declares the need `agents`
(every agent the config binds, by name) and runs one session per row. Each session is a row
of the session table, as a step's is:

```python
class ReviewEach:
    requires = {"acme.v1.diff"}
    provides = {"acme.v1.note"}

    def __init__(self, agents) -> None:  # the `agents` need
        self.reviewer = agents["reviewer"]

    async def run(self, inputs, ctx):
        notes = []
        for diff in inputs["acme.v1.diff"]:  # one session per row
            response = await self.reviewer.run(f"Review this change:\n{diff}")
            notes.append(response.content)
        return {"acme.v1.note": notes}


# in register(registry):
registry.register(
    "component",
    "acme.v1.review-each",
    ReviewEach,
    needs=("agents",),
    summary="Runs the reviewer once per diff",
)
```

With two diffs and a `reviewer` agent on the mock agent runtime, the run records two
`acme.v1.note` rows and two sessions. For model calls, declare `model_runtimes` instead and
make each call with `matrix.call_model` ([the API reference](reference/api.md#matrixv1model-step)).
A component handed `agents` or `model_runtimes` makes calls `plan` cannot count, so its
maximum is null.

### Refused before anything runs

Each runtime refuses what it cannot play before anything runs, and `matrix check` and
`matrix plan` say so, with the fix. Nothing is quietly downgraded or dropped. Refused:

- an agent bound to a model runtime (a call has no loop);
- a model name its model runtime cannot resolve;
- a `claude-sdk` agent whose tools leave out `Skill` when the runtime gives it skills, or an
  option the installed SDK lacks;
- a `pydantic-ai` agent that names a tool its runtime does not have, or is given tools on a
  model its model runtime cannot offer them to;
- a pydantic-ai setting a model call cannot carry.

### The `pydantic-ai` agent runtime

A session is pydantic-ai's agent loop, and every model call in it is one call on the model
runtime its `model_runtime` names, through matrix's `ModelRuntime` port. The session runs on
whatever that runtime resolves a name to, with its retries, fallbacks, record and telemetry.

```yaml
matrix:
  model_runtimes:
    offline: {type: mock, default: "ossify: to turn into bone"}   # or `local`, above
  agent_runtimes:
    loop: {type: pydantic-ai, model_runtime: offline,    # its calls are made on `offline`
           tools: {lookup: acme.tools:lookup}}           # module:function, importable
  agents:
    helper: {runtime: loop, system_prompt: "Answer from the dictionary.", max_turns: 4}
  inputs: [acme.v1.question]
  components:
    - name: answer
      type: matrix.v1.agent-step
      config: {agent: helper, inputs: {q: acme.v1.question}}
```

Here `acme/tools.py` holds `def lookup(word: str) -> str`, with a docstring, on the
`PYTHONPATH`. On the mock the session completes offline; with `model_runtime: local` its
calls go to the ollama row instead.

- The port carries `max_tokens` and `temperature`; any other pydantic-ai setting is refused,
  never dropped.
- The runtime's `tools` are Python functions named `module:function`. Each module is found
  when the runtime is built, without running it, and imported when a session first offers the
  tool. A module whose import fails fails that session and every later one that offers the
  tool.
- Naming a tool in config is a grant to run that code with the process's authority (owner
  ruling 2026-10-22; [SECURITY.md](../SECURITY.md)).
- A function's typed parameters and docstring are what the model is told of it. An agent's
  `tools` choose from them (unset: all of them; `[]`: none).
- `max_turns` bounds its model calls: a session that reaches it ends with `stop: max_turns`.

Its reply is the run's output. Its session row names the model that answered last, its
finish reason, the usage summed over every call and pydantic-ai's run id. Its tokens are
counted once, by its model runtime, which counts every call it makes. pydantic-ai's own
instrumentation is switched off on every agent, so it records no prompt or reply
([the design record](design/2026-10-19-record.md) §18).

## Running a config

The config declares a run: its `components`, its `inputs`, where it is recorded (`store`,
default JSONL under `.matrix/runs`) and its `limits` (deadlines, concurrency, retries). Plan
it, then run it:

```bash
matrix plan --json                                  # order of work, agents, calls; runs nothing
matrix run --input acme.v1.diff=diffs.jsonl --json  # one JSON row a line; - is standard input
matrix runs                                         # every recorded run: id, status, start, where
matrix run --from 20261019T101500Z-3f2a             # its inputs, reusing what completed unchanged
matrix plan live.yaml --only                        # one config on its own, none of the tiers
matrix run live.yaml --only --input acme.v1.diff=diffs.jsonl  # recorded in its store, .matrix/runs by default
```

A config file is named positionally, as `live.yaml` is above; there is no `--config`.
Without `--only` it is read on top of the config tiers, `./matrix.yaml` among them.

`plan` composes and wires, and reports every problem at once, each with its key path and the
file that set it. It also says:

- how many agent sessions and model calls a run makes (`agent_calls_min`, `agent_calls_max`,
  `model_calls_min`, `model_calls_max`). A maximum is null when a component other than an
  agent or a model step needs `agents` or `model_runtimes`, and so may make sessions of its
  own.
- its `settings`: the effective value of every key the run is made from, each with who set
  it: a config file (named), an agent definition, an extension's own default, or matrix's. A
  plan of a config that sets no `deadline_s` shows matrix's hour for each component, and one
  that writes `deadline_s: null` says the run has no deadline.
- which runtimes are stand-ins (`stand_in`), so canned replies are never mistaken for a
  model's.

matrix states no cost and keeps no spending ceiling: a run's cost is not something it can
measure, so it does not pretend to. The per-call token cap stays (owner ruling 2026-10-22):
`max_tokens` bounds each reply, not a run's cost. Each session row keeps its tokens, and the
cost the runtime reported when it reported one (`cost_usd`), as facts of that session.

`run` prints the run id, where the record is, and its files: `run.json`, one `<kind>.jsonl`
per kind (an agent step's reply row carries the task as sent, a model step's the prompt),
`matrix.v1.session.jsonl`, `matrix.v1.ledger.jsonl` and `events.jsonl` (telemetry: lossy,
never the result).

From Python it is `compose(load_config()).run(inputs)`, and
`plan_config(tool="matrix", config=None, *, only=False)` the plan: the first argument is the
tool, a path goes in `config`.

## Extending further

Components are one point. A runtime, store or observer registers at its own point the same
way, and is held to that point's port when it is registered and when it is built. What only
some implementations do is a protocol of its own (`SupportsWarnings`, `SupportsAclose`,
`SupportsLocation`, `SupportsAround`, ...), which matrix checks with `isinstance`, so nothing
is found by its name.

`matrix.testing` has a conformance suite for each port; run the one for yours:

```python
from matrix.testing import ConstructStoreConformance


class TestMyStore(ConstructStoreConformance):
    def store(self):
        return MyStore()
```

[The registry](reference/api.md#the-registry) in the API reference has every point, the
needs a build may declare, and the loading rules.

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
matrix describe TYPE_URL [--json]             # config schema, needs, origin
matrix config [CONFIG]... [--sources] [--json]  # the merged config and the files it came from
matrix check [CONFIG]... [--json]           # compose runtimes and agents, wire the components; call nothing
matrix agents [CONFIG]... [--json]          # the agents a config defines
matrix plan [CONFIG]... [--json]            # what a run would do; runs nothing
matrix run [CONFIG]... [--input KIND=PATH]... [--run-id ID] [--from RUN_ID] [--json]
matrix runs [CONFIG]... [--json]            # the recorded runs, oldest first
```

Each reads the config tiers of `--tool T` (default `matrix`), with each `CONFIG` above them,
in order (`-` is standard input); `--only` reads the `CONFIG` files alone. Mappings merge
across tiers and `definitions` directories accumulate, so a second config read on top of a
project's shares its runtimes, agents and definitions; `--only` keeps it to itself.

Exit codes, as the capability tools agree: 0 ok, 1 a bug in matrix, 2 usage, 3 config, 4 not
found, 5 transient, 6 auth, 8 a run failed, 9 store, 130 interrupted. `matrix --help` lists
each with what to do next. Under `--json` a failure is one `matrix.v1.error` envelope on
stderr, its problems structured.

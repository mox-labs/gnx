# What is matrix?

matrix is an executor. It takes components that declare the kinds of data they require and
provide, and the rows a caller supplies; it infers who waits on whom, checks that wiring,
runs each component when what it requires is ready, and records every row they produce, as
they produce it. It also composes agents from config, so an agent can be run on its own or as
one component among others.

matrix knows nothing about what a run is for. An eval harness, a review pipeline and a data
transform are the same thing to it: components, the kinds they exchange, a registry to find
them in, and config that says which to use. It refuses the rest: it does not repeat a run,
score anything, or carry any client's vocabulary.

The type URL grammar is slick's; matrix carries its own copy.

---

## The model

### A kind is the type URL of some data

`acme.v1.review` names a kind of data: reviews. A kind is compared by equality and never
dereferenced. It is the only thing two components need to agree on, and it is the schema of
the table its rows are kept in. When the wiring carries a model for a kind, every row of that
kind is validated against it: `Container.wire` carries every registered payload type, and
`wire` takes them as `schemas`.

"Kind" here always means the kind of data. (slick uses the word for kinds of component; that
is a different thing and stays with slick.)

### A component declares what it requires and provides

```python
class Count:
    requires = {"demo.v1.shout"}
    provides = {"demo.v1.count"}

    async def run(self, inputs, ctx):
        return {"demo.v1.count": sum(len(line) for line in inputs["demo.v1.shout"])}
```

`requires` and `provides` are sets of kinds, and `run(inputs, ctx)` does the work. That is
the whole protocol. `Component` is a `typing.Protocol`, so the class above is a component
without importing matrix. Configuration reaches a component when it is built, never through
`run`.

A component's **name** is the name of the configured instance in a run, not of its class, so
one class can be used twice under two names.

### Wiring is inferred

Nobody declares an edge. `wire(components, inputs=...)` reads the declarations and derives
the wiring:

- a component depends on the provider of each kind it requires;
- a kind the caller supplies when the run starts is a **run input** and has no provider;
- each kind has exactly one provider.

It raises one `CompilationError` listing every problem it found, so a broken set of
components is fixed in one pass rather than one error at a time:

1. every kind is a versioned type URL (`<namespace>.v<N>.<resource>`), and none is
   `matrix.v1.ledger` or `matrix.v1.session`, the run's own tables;
2. every required kind is provided by some component or is a run input;
3. no kind has two providers, and a run input has none;
4. no component requires a kind it provides;
5. the dependencies have no cycle.

One provider per kind is what lets the wiring be inferred at all: with two, a component that
requires the kind would not know whom to wait for. Two streams of the same shape are two
kinds. There is no way to wire a loop; work that must repeat is repeated by whoever runs the
wiring again, or inside one component.

The result, a `Wiring`, is reusable: wire once, run many times with different inputs.
`wiring.order()` shows one valid order of work, for a reader; the executor does not use it.

### Running produces a Run

`Executor.run(wiring, inputs)` checks that the caller supplied exactly the declared run
inputs, records them, and then schedules **by readiness**: a component starts as soon as the
providers of every kind it requires have finished. Components that do not depend on each
other run side by side (`Limits.concurrency` caps how many, when set), so a four-hour soak
does not hold up an unrelated component that could have finished in a second.

Each component receives `Inputs`: for each kind it requires, a tuple of that kind's rows, and
nothing else. It never sees the run's record or its neighbours, and each row is its own deep
copy, so mutating what it read changes neither the record nor what another component sees.
Rows must therefore be data that can be copied, not live handles.

Rows of the kinds it provides leave a component two ways, and it may use both:

- **returned** from `run`, as a mapping of kind to one row or to a list of rows;
- **handed over while it runs**, with `ctx.provide(kind, row)`.

Either way the executor holds the component to what it declared: a row of a kind it does not
provide is a `ContractError`, and so is a row that fails the schema registered for its kind.

Handing rows over as they are made is what makes a long-running component safe to kill. When
`ctx.provide` returns, the row is part of the run's record and durable in the run's store; a
component that returns everything at the end has nothing recorded until then. The components
that require a kind still start only when its provider has finished, so they see all of its
rows, however they were handed over.

The result is a `Run`: the observed state of one execution, with its status, how each
component ended, its failures, and a `Construct` holding every row. See
[the data model](data-model.md) for the Construct, its ledger, and how a run is kept on disk.

### Every run ends, and cleans up

A run ends `completed`, `failed` or `cancelled`; each component ends `completed`, `stopped`,
`failed`, `cancelled`, `skipped` or `reused`. What makes that true is that there is one way to
stop a component, whatever the reason:

1. **Stop request.** `ctx.stopping` is set. A component that returns now keeps what it
   returns, and ends `stopped` (`failed`, past its deadline): its rows are recorded but may
   be partial, so a later run never reuses them.
2. **Grace.** The executor waits `grace_s` for that.
3. **Cancel.** The component's task is cancelled and its own `finally` blocks run. A
   component that swallows the cancellation is abandoned after `cleanup_s` and reported as
   such.
4. **Deferred release.** At the end of the run, everything components handed to `ctx.defer`
   is called, last registered first, each bounded by `cleanup_s`.

The reasons are a component's own deadline (`Limits.deadline_s`, overridden per component by
`Limits.components`), another component's failure, and cancellation of the run, which is also
what SIGTERM and Ctrl-C become while a run is in flight.

When a component fails, `Limits.on_failure` decides what happens to the others. `fast` (the
default) stops every running component and skips the rest: the caller is usually paying for
each one, and a failed provider makes its dependents pointless. `isolate` skips only the
components that depend on the failed one and lets the others finish. Either way `RunError` is
raised carrying the partial Run, and a cancelled run raises `RunCancelled` the same way.
Nothing recorded before the end is lost.

A release exists because the thing to undo usually outlives the component that made it: one
component provisions, others use, and a teardown written as a component is skipped exactly
when it is needed. `matrix.process.spawn` is the common case made to carry itself: the child
gets its own process group, and killing the group is deferred. (`process.start` and
`process.kill_group` are the two halves, for a component that starts a child per repetition
and ends each one itself.) A component that needs
something running beside its own work (a sampler beside a load) starts it itself and defers
its stop; matrix has no notion of a sidecar.

Two limits are real and stated. matrix cannot preempt in-process Python, so a component that
blocks the event loop stalls every deadline until it yields. And no release runs after
`kill -9` of the run itself, so a component that provisions something expensive keeps its own
record of it; what does survive is every row handed over before the kill.

## Agents are composed, not constructed

An agent has two owners. What it *is* (its prompt, tools, model and turn bound) belongs to
whoever wrote it. *Where it runs* (the Claude Agent SDK with a permission mode and a working
directory, or a mock) belongs to whoever deploys it. matrix keeps them apart:

- an `AgentDefinition` is the what. It is data, the same shape as a Claude Code agent file, so
  a plugin's `agents/*.md` load as definitions without translation;
- an `AgentRuntime` is the where: a session with a loop and tools. It has two calls:
  `check(definition)` and `run(definition, task)`;
- a `BoundAgent` pairs one of each, and calls `check` when it is made.

`check` is how a runtime refuses a definition it would run as a different agent, at binding,
before anything has spent a token. What a runtime can be given is data: its typed config is
the whole of it, so `claude-sdk` takes `permission_mode`, `setting_sources`, `plugins`,
`skills` and inline subagents and refuses any other option at its key; and its `check`
refuses a definition whose tools leave out `Skill` when it has skills, which could then never
run, and any option the installed SDK's own dataclasses do not have.

A runtime reports how a session ended (`AgentResponse.stop`: `completed`, or the limit it
reached, such as `max_turns`) and raises `AgentRuntimeError` when the session failed. The
error's `reason` says whose problem it is (`rate_limited`, `auth`, `incapable`, ...) so a
harness can keep an infrastructure failure out of an agent's score.

`compose(config)` builds agent and model runtimes, loads definitions and binds agents from a
`matrix:` config section, and returns a `Container`. Composition makes no model call and no
tool call.

### A model call is not an agent session

One call to one model (a triage, an extraction, a judge) has no loop, no tools and no turns,
so it is not played on an agent runtime. It has its own port, `ModelRuntime`, with two calls:
`check(model)`, which resolves the model name when a step is built, so an unknown one is a
`config` problem before anything runs; and `call(request)`, which returns a `ModelCall`. The
record carries what the runtime reports of the call: why the reply ended (`finish_reason`,
the provider's own value, and any `refusal`), the model that answered (a fallback may make it
differ from the one asked for, and `fallback_from` says so), usage with cache tokens, the
request id, attempts, retries and latency, what was sent, every attempt made and the
registry row that answered. A fact the runtime does not report stays empty rather than being
filled with a constant: a call has no `num_turns`.

The two ports are separate because their contracts differ. A runtime that makes one call
cannot honour a definition with tools; behind one port its `check` would refuse what another
runtime of the same port accepts, and its replies would carry turns and a stop reason it
never had. A caller that can use either, as a judge can, names which port it wants.

Each port has its own registry point (`agent-runtime`, `model-runtime`) and its own config
section (`agent_runtimes`, `model_runtimes`). An agent binds only to an agent runtime. The
mocks of both declare `stand_in`, so a caller can tell canned answers from a model's without
reading anything into a runtime's name.

### An agent as a component

`matrix.v1.agent-step` is a registered component that runs one composed agent. What it
requires and provides comes from its config: it requires the kinds its `inputs` name, and
their rows are rendered into a task template. It provides the agent's `AgentResponse` under
the kind `response` names and, when its config names an `output` kind, the JSON the agent
returned as one row of that kind. The executor validates that row against the kind's schema
like any other, so an agent that does not return the promised structure breaks its contract
and the run says so. The wiring knows nothing about agents. A task template's placeholders
are checked against its inputs when the step is built.

`matrix.v1.model-step` is the same for one model call. It names a model runtime and a model,
renders its prompt from its inputs, and provides the `ModelCall`, with the prompt as sent,
under its `response` kind. With an `output` kind that has a registered schema, it asks the
runtime for that structure, which hardline validates and repairs; the row is the validated
reply. A reply cut at `max_tokens`, refused, withheld or empty is not provided at all: the step
fails, saying which. A component downstream takes whatever row it is given as an answer, and
nothing in the wiring could tell it otherwise.

A reply is a sample, so the record keeps where each one came from. Every agent session and
model call a component runs inside a run is a row of `matrix.v1.session`: the runtime, the
model that answered (not the one asked for), why it stopped, its usage, the runtime's ids and
the OpenTelemetry trace and span it ended in, any fallback and attempt, and a digest of what
was sent. A step's reply row keeps what a consumer acts on (the reply, how it ended, the
family) and points at that row; the ledger entry of each row a component hands over names the
sessions behind it. So a reply is no longer a mix of an answer and its provenance, and a new
fact about sessions is a field of the session row, not a change to every reply.

## One registry for every extension

The `Registry` holds **extension points** (`agent-runtime`, `model-runtime`, `component`,
`payload-type`, `observer`, `store`; other packages add their own, named under their own root, as ix adds
`ix.sensor`) and the **entries** registered at each, keyed by type URL. An entry carries
what a caller, human or agent, needs to decide whether to use it:

- `config`: the pydantic model its settings validate against;
- `needs`: shared things composition hands it (`agents`, `model_runtimes`, `schemas`,
  `models`, `cwd`, and for a component its own `name`), declared rather than guessed from
  field names;
- `stand_in`: whether it stands in for the real thing, as the mock runtimes do;
- `summary`, `stability` and `origin` (the package that registered it).

Every extension, matrix's built-ins included, is found the same way: an entry point in the
`matrix.extensions` group. An extension that fails to import or register is quarantined: none
of its entries stay, the failure is recorded, `matrix catalog` reports it, and the others load.
An extension that registers at a point not declared yet is retried after the others have
loaded.

Namespaces have owners: a package owns the type URLs whose first segment is its own name
(`matrix` owns `matrix.*`, `ix` owns `ix.*`). When two extensions register one type URL at
one point, the owner keeps it whichever loads first and the other extension is quarantined
whole, so nothing replaces a built-in by sorting before it. Between two packages that do not
own the name, the first in entry-point name order keeps it and the second is refused, naming
both. Install order never decides.

`Container.wire` is where the registry meets the model: it takes a list of
`{name, type, config}` entries (the config's `matrix.components`, or the caller's own), builds
each through the registry, and wires them.

Because entries declare their needs, a plan can bound the sessions a run makes: only an
agent step, a model step, or a component handed `agents` or `model_runtimes` can reach a
runtime. An entry declares no effects: a label nothing checked said less than its needs do,
and what an extension needs of the host is its own to check before it runs.

## Observers, not built-in tracing

The executor and bound agents emit `Event`s (`matrix.run.start`, `matrix.component.end`,
`matrix.row.append`, `matrix.agent.end`, ...), and a component adds its own through its
context: `ctx.emit` for an event, `ctx.measure` for a number, `ctx.span` for a timed block.
Names are namespaced like type URLs; `matrix.*` is matrix's, and a component uses its own
root. Progress, logging, tracing and metrics are observers of those events, so the core
imports none of them. An agent called from inside a component reports that component as its
parent, so the `otel` observer nests an agent's span under the component that ran it, and a
tool call the runtime timed under the agent: `invoke_workflow` over `matrix.component` over
`invoke_agent` over `execute_tool`, the OpenTelemetry GenAI conventions' names.

Two observers need no choosing. A run kept by the JSONL store writes its events into its own
record, `events.jsonl`. And when the standard `OTEL_*` variables ask for export, every
composed run gets the `otel` observer. That observer speaks only the OpenTelemetry API: matrix
is a library, and installing an SDK, which decides where telemetry goes for the whole process,
is the application's. `matrix run` is an application, so it does, from the same variables.

Telemetry is not data. Events reach observers through a bounded queue and a dispatcher, never
on the task that emitted them, so an observer cannot stall a run: one that raises is skipped,
one that is slow makes the queue fill and events drop (counted, and reported), and one that
never returns costs the run a bounded wait at its end. Nothing a run produces depends on an
event arriving; results are read from the `Run` or its store. For the same reason observers are not an
audit channel: an audit hook would have to be able to stop the operation.

## Configuration is shared

A tool built on matrix (ix, or matrix's own CLI) names itself, and `load_config` reads the
same tiers for every tool: `~/.matrix/config.yaml`, `~/.<tool>/config.yaml`, `./matrix.yaml`,
`./<tool>.yaml`, then the files named by `$MATRIX_CONFIG` and `$<TOOL>_CONFIG`. Your runtimes
and agents are declared once and every tool sees them. That cuts both ways: mappings merge
and `definitions` directories accumulate, so a second config read on top of a project's shares
its runtimes, agents and definitions, and an agent defined in both is a `name_taken` problem.
`--only` reads the named files and no tier, for a config that should run on its own; its
runs are still recorded in the store it names (`.matrix/runs` in the working directory by
default).
`matrix config --sources` lists the files consulted, and every problem names the file that
set the value it is about.

The config also declares a run: its components and inputs, where it is recorded (`store`,
chosen from the registry like a runtime) and the limits it keeps. `matrix plan` composes and
wires it and says what a run would do, calling nothing; `matrix run` runs it, and
`matrix runs` lists what was recorded. matrix keeps no spending ceiling and states no cost
for a run: what a run costs depends on providers matrix does not see, so it cannot measure
it. What a session reports is kept as a fact of that session (its tokens, and `cost_usd`
when the runtime reports one), and a runtime may bound its own sessions with its own option
(the `claude-sdk` runtime passes the SDK's `max_budget_usd`).

## One loader

matrix reads several kinds of document: tiers of settings, folders of agent files, and, for
the tools built on it, experiment files, probe folders and declarations. One mechanism reads
them all. A source reads what is at a location into plain data and knows nothing of types; a
markdown file is its frontmatter with its text under `body`. The loader takes the type from
its caller and either merges the documents as tiers or collects one model per file, validates
once, and reports every problem with its key path and its file. What `body` means is the
type's business: an `AgentDefinition` takes it as its system prompt, an ix probe as its
prompt.

## What matrix is not

- **Not a scheduler or a service.** It is a library and a CLI that runs one config at a time.
  There is no daemon.
- **Not a retry engine.** A retry policy runs a component again after a transient failure,
  within the run, and never one that has already handed rows over. A failed run raises with
  its partial state; running it again is the caller's decision, and `matrix run --from`
  reuses what completed.
- **Not a sandbox.** Components are in-process Python. Holding a component to its kinds is a
  correctness boundary, not a trust boundary. Agent sessions get whatever authority their runtime's
  config grants; the package's security notes (`SECURITY.md`) say what that means.
- **Not domain-aware.** It has no notion of probes, sensors or hypotheses. Those are the
  vocabulary of the packages that register components.

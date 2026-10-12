# What is matrix?

matrix is an executor. It takes components that declare typed ports, a flow that wires those
ports together through topics, and the values a caller supplies; it checks the wiring, runs
the members in dependency order, and records every value they produce. It also composes
agents from config, so an agent can be run on its own or as one member of a flow.

matrix knows nothing about what a flow is for. An eval harness, a review pipeline and a data
transform are the same thing to it: components with ports, wired through topics.

The wiring rules and the type URL grammar are slick's. matrix carries its own copy of both
until it depends on slick.

---

## The model

### Components declare ports

A component declares two maps of **ports**: `requires` (what it reads) and `provides` (what it
writes). Each port has a name and a type, given as a type URL such as `acme.v1.diff`:

```python
class Count:
    requires = {"text": "demo.v1.text"}
    provides = {"length": "demo.v1.count"}

    async def run(self, inputs):
        return {"length": len(inputs["text"])}
```

`Component` is a `typing.Protocol`, so the class above is a component without importing
matrix. A port can be given as a `Port` instead of a bare type URL, to mark it:

- `Port(url, many=True)` on a required port: it receives every value on its topic, as a
  tuple. This is how a member takes input from several producers (fan-in).
- `Port(url, optional=True)` on a required port: it may be left unbound, and the component
  then sees no value for it. On a provided port: the component may leave it out of its result.

A port name cannot be both required and provided by one component.

### Members bind ports to topics

A **member** is one use of a component in a flow, under an alias. Its `bindings` map port
names to **topic** names. A topic is a named rendezvous: whoever provides onto `loud` and
whoever requires from `loud` are connected. There are no edges to declare; matrix derives
them from the bindings. `after` adds an ordering between members that exchange no data.

The same component can appear as several members, each with its own bindings.

### Flows take named inputs

A **flow** is a set of members plus **inputs**: topics, each with a type, that the caller
supplies when it runs the flow. This is where outside data enters. A component never closes
over the subject of a run; it reads it from a bound topic like any other value.

A flow is desired state. Running it never changes it.

### Compiling checks the wiring

`compile_flow(flow)` checks every rule and raises one `CompilationError` listing every
problem it found, so a broken flow is fixed in one pass rather than one error at a time:

1. every binding names a port the component declares;
2. every required port is bound, unless it is optional;
3. every topic carries exactly one type;
4. every topic someone reads has a producer or is a flow input, and nothing produces onto a
   flow input;
5. a topic fed only by optional outputs may stay empty, so it feeds only optional or `many`
   ports;
6. a topic with several producers feeds only ports declared `many`; their values arrive in the
   producers' declaration order;
7. no cycle, through topics or `after`.

The last rule is a placeholder, and the error says so. A flow that must repeat does so by
running an acyclic flow again under a bound, never by wiring a loop.

The result, a `CompiledFlow`, groups members into **levels**: every member of a level depends
only on earlier levels. It is reusable; compile once, run many times.

### Running produces a Run

`Executor.run(compiled, inputs)` checks that the caller supplied exactly the declared inputs,
then runs the flow level by level. Within a level, up to `Limits.concurrency` members run at
once, and `Limits.deadline_s` bounds each one (`Limits.members` overrides it per member).

Each member receives `Inputs`: the values on the topics it bound, by port name, plus a
`RunContext`: its run id, alias, episode and deadline, the `stopping` flag, and `defer`, `emit`,
`measure` and `span`. It never sees the run's state or its
neighbours, and each value is its own deep copy, so mutating what it read changes neither the
record nor what a sibling sees. Values that flow between members must therefore be data that
can be copied, not live handles. It returns a mapping of provide-port name to value, and the executor holds it to
that: an undeclared or missing port is a `ContractError`, and so is a value that fails the
schema registered for its type.

The result is a `Run`: the observed state of one execution, with its status, how each member
ended, its failures, and a `Construct` holding every value produced.

### Every run ends, and cleans up

A run ends `completed`, `failed` or `cancelled`; each member ends `completed`, `failed`,
`cancelled` or `skipped`. What makes that true is that there is one way to stop a member,
whatever the reason:

1. **Stop request.** `ctx.stopping` is set. A member that returns now keeps what it returns.
2. **Grace.** The executor waits `grace_s` for that.
3. **Cancel.** The member's task is cancelled and its own `finally` blocks run. A member that
   swallows the cancellation is abandoned after `cleanup_s` and reported as such.
4. **Deferred release.** At the end of the run, everything members handed to `ctx.defer` is
   called, last registered first, each bounded by `cleanup_s`.

The reasons are a member's own deadline, another member's failure, and cancellation of the
run, which is also what SIGTERM and Ctrl-C become while a run is in flight.

When a member fails, `Limits.on_failure` decides what happens to the others. `fast` (the
default) stops every running member and skips the rest: the caller is usually paying for each
one, and a failed upstream makes the downstream pointless. `isolate` skips only the members
that depend on the failed one and lets the others finish. Either way `RunError` is raised
carrying the partial Run, and a cancelled run raises `RunCancelled` the same way. Nothing
produced before the end is lost.

A release exists because the thing to undo usually outlives the member that made it: one
member provisions, others use, and a teardown written as a member is skipped exactly when it
is needed. `matrix.process.spawn` is the common case made to carry itself: the child gets its
own process group, and killing the group is deferred.

Two limits are real and stated. matrix cannot preempt in-process Python, so a member that
blocks the event loop stalls every deadline until it yields; and nothing in-process survives
`kill -9`, so a component that provisions something expensive keeps its own record of it.

## Agents are composed, not constructed

An agent has two owners. What it *is* (its prompt, tools, model and turn bound) belongs to
whoever wrote it. *Where it runs* (the Claude Agent SDK with a permission mode and a working
directory, one call through hardline, a mock) belongs to whoever deploys it. matrix keeps
them apart:

- an `AgentDefinition` is the what. It is data, the same shape as a Claude Code agent file, so
  a plugin's `agents/*.md` load as definitions without translation;
- an `AgentRuntime` is the where. It has two calls: `check(definition)` and
  `run(definition, task)`;
- a `BoundAgent` pairs one of each, and calls `check` when it is made.

`check` is how a runtime refuses a definition it would run as a different agent. The `model`
runtime makes one call with no tool loop, so a definition that declares tools is refused at
binding, before anything has spent a token, rather than run without them.

A runtime reports how a session ended (`AgentResponse.stop`: `completed`, or the limit it
reached, such as `max_turns`) and raises `AgentRuntimeError` when the session failed. The
error's `reason` says whose problem it is (`rate_limited`, `auth`, `incapable`, ...) so a
harness can keep an infrastructure failure out of an agent's score.

`compose(config)` builds runtimes, loads definitions and binds agents from a `matrix:` config
section, and returns a `Container`. Composition makes no model call and no tool call.

### An agent in a flow

`matrix.v1.agent-step` is a registered component that runs one composed agent. Its required
ports are whatever its config declares (`inputs`); their values are rendered into a task
template. It provides `response` (the `AgentResponse`) and, when its config names an `output`
type, `result`: the JSON the agent returned. The executor validates `result` against that
type's schema like any other output, so an agent that does not return the promised structure
breaks its contract and the run says so. The compiler knows nothing about agents.

## One registry for every extension

The `Registry` holds **extension points** (`runtime`, `component`, `payload-type`,
`observer`; other packages add their own, as ix adds `sensor` and `engine`) and the
**entries** registered at each, keyed by type URL. An entry carries what a caller, human or
agent, needs to decide whether to use it:

- `config`: the pydantic model its settings validate against;
- `needs`: shared things composition hands it (`agents`, `models`, `cwd`), declared rather
  than guessed from field names;
- `effects`: what it may touch (`network`, `subprocess`, `filesystem`, `model`). `None` means
  unknown, which is never read as "none";
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

Because entries declare their effects, a compiled flow can say what it may touch.
`Container.compile` records each member's effects (an `agent-step` member has its agent's
runtime's), and `CompiledFlow.declared_effects()` is their union, or `None` when any member's
effects are unknown.

## Observers, not built-in tracing

The executor and bound agents emit `Event`s (`matrix.run.start`, `matrix.member.end`,
`matrix.artifact.append`, `matrix.agent.end`, ...), and a component adds its own through its
context: `ctx.emit` for an event, `ctx.measure` for a number, `ctx.span` for a timed block.
Names are namespaced like type URLs; `matrix.*` is matrix's, and a component uses its own
root. Progress, logging, tracing and metrics are observers of those events, so the core
imports none of them. An agent called from inside a member reports that member as its parent,
so the `otel` observer nests an agent's span under the member that ran it.

Telemetry is not data. Events reach observers through a bounded queue and a dispatcher, never
on the task that emitted them, so an observer cannot stall a run: one that raises is skipped,
one that is slow makes the queue fill and events drop (counted, and reported), and one that
never returns costs the run a bounded wait at its end. Nothing a run produces depends on an
event arriving; results are read from the `Run`. For the same reason observers are not an
audit channel: an audit hook would have to be able to stop the operation.

## Configuration is shared

A tool built on matrix (ix, or matrix's own CLI) names itself, and `load_config` reads the
same tiers for every tool: `~/.matrix/config.yaml`, `~/.<tool>/config.yaml`, `./matrix.yaml`,
`./<tool>.yaml`, then the files named by `$MATRIX_CONFIG` and `$<TOOL>_CONFIG`. Your runtimes
and agents are declared once and every tool sees them. `matrix config --sources` lists the
files consulted, so which file set a value is never a guess.

## What matrix is not

- **Not a scheduler or a service.** It is a library and a read-only CLI. There is no daemon.
- **Not a retry engine.** A failed run raises with its partial state; repeating is the
  caller's decision. The executor stops, cleans up and reports; it never tries again.
- **Not a sandbox.** Components are in-process Python. Port checking is a correctness
  boundary, not a trust boundary. Agent sessions get whatever authority their runtime's
  config grants; see [SECURITY.md](../../SECURITY.md).
- **Not domain-aware.** It has no notion of probes, sensors or hypotheses. Those are the
  vocabulary of the packages that register components.

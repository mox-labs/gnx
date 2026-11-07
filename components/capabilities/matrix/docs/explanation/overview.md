---
title: matrix
summary: Runs components wired by the kinds of data they exchange, and records every row as the run goes.
kind: capability
---

## What it is

matrix runs a set of components. Each one says what kind of data it needs and what kind it
makes; matrix works out the order, checks it before anything runs, and keeps every row the
components make, on disk, as they make it. Agents and model calls are components too,
composed from config.

## The problem

A review pipeline: one step collects diffs, an agent reviews each, a model call scores the
review. Glued together by a script, the order lives in the script. A renamed field is found
by the step that reads it, after the tokens were spent. A crash at hour three leaves whatever
the script had not written yet, and which model wrote review 412, on what prompt, is gone.

## How it works

A **kind** is the type of some data, named by a type URL: `acme.v1.review`. It is the one
thing two components agree on.

A **component** declares the kinds it requires and the kinds it provides, and does its work
in an async `run`. It never names another component.

The **wiring** is inferred from those declarations: a component waits for the one provider of
each kind it requires. matrix checks the wiring before anything runs and reports every
problem at once: a kind nothing provides, a kind with two providers, a cycle.

```
acme.v1.diff ──▶ review ──▶ acme.v1.review ──▶ score ──▶ acme.v1.score
                (an agent)                   (a model call)
```

The **record** of a run is one table of rows per kind, plus a ledger of which component
handed over each row and a session table of every agent session and model call. A row is on
disk before the call that handed it over returns, so a killed run keeps everything it had.

A **runtime** is where an agent or a model call runs. An agent runtime runs a session: a loop
of model calls and tool calls (the Claude Agent SDK, pydantic-ai). A model runtime makes one
call: one request, one reply. Config chooses them; a component never imports one.

## Try it

Offline and free: a model call on the `mock` model runtime, which replies from config.
Install matrix as the [README](../../README.md#install) says, then in an empty directory:

```yaml
# matrix.yaml
matrix:
  model_runtimes:
    offline: {type: mock, default: "no"}   # a stand-in: replies from config, no server
  inputs: [acme.v1.diff]
  components:
    - name: triage
      type: matrix.v1.model-step
      config:
        runtime: offline
        inputs: {diff: acme.v1.diff}
        prompt: "Is this change risky? Answer yes or no.\n{diff}"
```

```bash
printf '%s\n' '"- x\n+ y"' > diffs.jsonl
matrix plan
```

```
inputs: acme.v1.diff
stage 1: triage
  triage               matrix.v1.model-step (model default on offline)
agent calls: 0 to 0
model calls: 1 to 1
record: .matrix/runs/RUN_ID
settings (each effective value, and who set it):
  matrix.limits.deadline_s = 3600.0  (matrix; each component's, from when it starts: …)
  …
  matrix.model_runtimes.offline.default = "no"  (config …/matrix.yaml)
  matrix.model_runtimes.offline.family = "mock"  (extension)
```

```bash
matrix run --input acme.v1.diff=diffs.jsonl
jq -c '{runtime, family, finish_reason}' .matrix/runs/*/matrix.v1.session.jsonl
jq -c '{text, prompt}' .matrix/runs/*/matrix.v1.model-call.jsonl
```

```
run 20261107T211803Z-c76c completed: .matrix/runs/20261107T211803Z-c76c
  …
{"runtime":"matrix.v1.model-runtime.mock","family":"mock","finish_reason":"stop"}
{"text":"no","prompt":"Is this change risky? Answer yes or no.\n- x\n+ y"}
```

The plan named every setting and who set it before anything ran; the record kept the prompt
as sent, the reply, and which runtime and family answered.

## What it gives an agent

| what an agent can do | why it can trust it | the command or call |
|---|---|---|
| check a pipeline before spending anything | the wiring is checked by the kinds of data, every problem reported at once with its key path, its file and a fix; nothing is called | `matrix check`, `wire(...)` |
| see every setting a run will use | `plan` lists each effective value with who set it (a named config file, an agent definition, an extension's default, matrix's), marks stand-in runtimes, and bounds the agent sessions and model calls | `matrix plan --json` |
| read results while a run goes, or after it dies | every row is on disk when it is handed over; a killed run keeps its rows and a status; the ledger names the component behind each row | `matrix runs`, `.matrix/runs/<id>/<kind>.jsonl` |
| call any model family through one port | a model step calls whatever model runtime config names; each call's session row records the runtime, the model and family that answered, why it stopped, and its usage | `matrix.v1.model-step`, with [matrix-hardline](../../../matrix-hardline/docs/explanation/overview.md) for real models |
| add its own component to a config | a component is registered by an installed package and listed by the catalog, its config schema shown | `matrix catalog`, `matrix describe TYPE_URL` |
| act on a refusal | each error has a kind, an exit code and a fix; under `--json` it is one structured envelope | `matrix check --json` |
| learn matrix for the version installed | the guide is printed by the installed package, so it matches it | `matrix --skill [-r NAME]` |

## What it does not do

- **It calls no model by itself.** Its only model runtime is the `mock`; a real one is an
  extension ([matrix-hardline](../../../matrix-hardline/README.md)).
- **No scheduler, no service.** It is a library and a command that runs one config at a time;
  there is no daemon. Nothing repeats a run: `matrix run --from` reuses what completed.
- **Not a sandbox.** Components are in-process Python. Holding a component to its kinds is a
  correctness boundary, not a trust boundary. Agent sessions get whatever authority their
  runtime's config grants ([SECURITY.md](../../SECURITY.md)).
- **No cost.** It states no cost for a run and keeps no spending ceiling. It keeps the tokens
  each session reported, and a cost when a runtime reported one.
- **No domain.** It has no notion of probes, sensors or verdicts; packages built on it bring
  those.
- **Two stops it cannot make:** a component that blocks the event loop with synchronous work,
  and a release after `kill -9` of the run itself. The record survives both.
- **Events are lossy.** Progress and telemetry pass through a bounded queue and can drop;
  facts are read from the record, never from the event stream.

## Next

- [README](../../README.md): install, terms, a first run and a first config.
- [Using matrix](../guide.md): your own component in a config, model calls, agents, the
  command.
- [How matrix works](how-matrix-works.md) and [the data model](data-model.md): the model in
  depth.
- [API reference](../reference/api.md) and [CONTRACT.md](../../CONTRACT.md).
- `matrix --skill`: the guide an agent reads.

# matrix

matrix runs components wired by the kinds of data they exchange, and records every row as the
run goes. It also composes agents and model calls from config, so they run as components too.

## Install

matrix is not on a package index, and `pip install matrix` installs an unrelated project.
Install it from this repository:

```bash
# the matrix command, at the release's tag
uv tool install "git+https://github.com/mox-labs/gnx@matrix-v0.10.0#subdirectory=components/capabilities/matrix"
# the library, in a uv project
uv add "matrix @ git+https://github.com/mox-labs/gnx@matrix-v0.10.0#subdirectory=components/capabilities/matrix"
```

From a clone:

```bash
git clone https://github.com/mox-labs/gnx && cd gnx
uv tool install ./components/capabilities/matrix
```

Every agent runtime and the telemetry come with it; there are no extras. matrix makes no
model call by itself: to call models through hardline, install
[matrix-hardline](../matrix-hardline/README.md#install) beside it.

## Terms

| term | what it means here |
|---|---|
| **kind** | the type of some data, named by a type URL: `acme.v1.review` |
| **component** | a class that declares the kinds it `requires` and `provides`, and does its work in an async `run` |
| **run**, **Construct** | one execution of wired components; its record, one table of rows per kind, written as the run goes |
| **model runtime** | where one model call is made: one request, one reply, no loop. matrix's own is the `mock` |
| **agent runtime** | where an agent session runs: a loop of model calls and tool calls. matrix's own: `claude-sdk` (the Claude Agent SDK), `pydantic-ai` (the pydantic-ai library's agent loop, its calls made on a model runtime), `mock` |
| **extension** | an installed package that registers components, runtimes, stores or observers through the `matrix.extensions` entry point |
| **config tier** | one of the config files matrix reads, lowest first: `~/.matrix/config.yaml`, then `./matrix.yaml`, and others ([the list](docs/reference/api.md#tiers)) |
| **skill** | the guide an AI agent reads to use a tool; `matrix --skill` prints matrix's, for the version installed |

## A first run

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

Nobody drew an edge: `count` requires what `upper` provides, so it waits for it. Remove
`Upper` and `wire` refuses before anything runs:

```
the components cannot be wired (1 problem):
  components.count.requires: nothing provides 'demo.v1.shout' and it is not a run input
```

## A first config

A config (`matrix.yaml`) declares a run: its runtimes, its components, the kinds it takes as
input. It names each component by type URL. matrix's own are `matrix.v1.model-step` and
`matrix.v1.agent-step`; one you wrote must first be packaged as an extension and installed
([the guide](docs/guide.md#your-own-component-in-a-config)). This one makes one model call on the `mock` model runtime, which replies from config,
so it runs with no model server and no key:

```yaml
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
printf '%s\n' '"- x\n+ y"' > diffs.jsonl         # one JSON row a line
matrix check                                        # composes and wires; calls nothing
matrix plan                                         # the order of work, and every setting with who set it
matrix run --input acme.v1.diff=diffs.jsonl
```

```
run 20261107T211803Z-c76c completed: .matrix/runs/20261107T211803Z-c76c
  run.json                    the run: status, how each component ended, failures
  acme.v1.diff.jsonl          1 row of acme.v1.diff
  matrix.v1.model-call.jsonl  1 row of matrix.v1.model-call
  matrix.v1.session.jsonl     1 row of matrix.v1.session
  …
```

The step makes one call for the run, every row of `acme.v1.diff` rendered into its prompt
(one call per row is a component of your own: [the guide](docs/guide.md#one-call-per-row)).
`check` and `plan` call nothing, so they need no model server either.
[Model calls](docs/guide.md#model-calls) swaps the mock for a local model, and
[running a config](docs/guide.md#running-a-config) names a config other than `./matrix.yaml`.

## Next

| | |
|---|---|
| [Overview](docs/explanation/overview.md) | what matrix is for, and what it gives an agent |
| [Using matrix](docs/guide.md) | the record, stopping, agents, model calls, extensions, the `matrix` command |
| [How matrix works](docs/explanation/how-matrix-works.md) | the model behind it: kinds, wiring, runs, the two runtime ports, the registry |
| [The data model](docs/explanation/data-model.md) | the Construct, the ledger and the session table, and how a run is kept on disk |
| [API reference](docs/reference/api.md) | every public name, config key, error kind, exit code and command |
| [CONTRACT.md](CONTRACT.md) | what is promised: the component-author contract, and how far each surface is |
| [SECURITY.md](SECURITY.md) | agent authority, config as a capability grant, findings |
| [CHANGELOG.md](CHANGELOG.md) | what changed in each release |
| `matrix --skill` | the guide an agent reads, for the installed version |

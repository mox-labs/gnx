---
title: matrix-hardline
summary: The bridge that makes hardline a matrix model runtime, so a matrix config can call any model family hardline has a row for.
kind: capability
---

## What it is

A small package with one adapter in it. Installed beside matrix, it registers hardline as a
matrix model runtime, so a model step or a pydantic-ai agent in a matrix config can call any
model hardline is configured for.

## The problem

matrix runs model calls but makes none itself: it ships only a mock, so installing it never
pulls in a model client you did not choose. hardline calls any model family but knows nothing
of matrix. Joined inside either one, each would carry the other's dependencies and change
whenever the other did, and a matrix user who wanted a different model runtime would carry
hardline anyway.

## How it works

A **model runtime** is matrix's port for one model call: `check` a model name when a step is
built, `call` it when the step runs.

The **adapter** keeps that port on a hardline runtime. A request's turns, tools and overrides
become one hardline `complete`, or one `extract` when the step asks for a structure; the
reply comes back as matrix's `ModelCall`.

The **registry** is hardline's: the `models` section of the runtime's config, or, when it is
unset, hardline's own config files. Each row is a model, its backend and its family.

**Provenance** is what only hardline knows of a call (attempts, retries, fallback, the row
that answered and where each of its values came from), kept whole in the session row under
`hardline.v1.call`. matrix's own fields stay neutral, so any model runtime fills them alike.

## Try it

Offline and free: a hardline `mock` row, called through the bridge. Install the three
packages as the [README](../../README.md#install) says, then in an empty directory:

```yaml
# matrix.yaml
matrix:
  model_runtimes:
    local:
      type: matrix-hardline.v1.model-runtime
      models:
        default: offline
        models:
          offline: {backend: mock, model: echo, family: mock, options: {default: "no"}}
  inputs: [acme.v1.diff]
  components:
    - name: triage
      type: matrix.v1.model-step
      config:
        runtime: local
        inputs: {diff: acme.v1.diff}
        prompt: "Is this change risky? Answer yes or no.\n{diff}"
```

```bash
printf '%s\n' '"- x\n+ y"' > diffs.jsonl
matrix plan | grep model_runtimes
matrix run --input acme.v1.diff=diffs.jsonl
jq -c '{runtime, drives, name, family, backend: .provenance["hardline.v1.call"].backend}' .matrix/runs/*/matrix.v1.session.jsonl
```

```
  matrix.model_runtimes.local.type = "matrix-hardline.v1.model-runtime"  (config …/matrix.yaml)
  matrix.model_runtimes.local.models = {"default": "offline", "models": {…}}  (config …/matrix.yaml)
  matrix.model_runtimes.local.default_model = null  (extension)
  …
run 20261107T212855Z-9573 completed: .matrix/runs/20261107T212855Z-9573
…
{"runtime":"matrix-hardline.v1.model-runtime","drives":["hardline"],"name":"offline","family":"mock","backend":"mock"}
```

The plan named the registry and who set it; the session row names the runtime, the package
it drives, and the row and family that answered.

## What it gives an agent

| what an agent can do | why it can trust it | the command or call |
|---|---|---|
| call any model family hardline has a row for, from a matrix config | one port: a model step or a pydantic-ai agent names the runtime, never the provider | `type: matrix-hardline.v1.model-runtime` |
| catch a bad model name or an impossible call before spending anything | a name the registry does not resolve, tools on a backend that cannot call them, or tools with a structured output, is a `config` problem when the step is built | `matrix check`, `matrix plan` |
| see the registry and every option a run will use | the plan lists `models`, `default_model`, `max_tokens` and `temperature` with who set each, and hardline's registry warnings | `matrix plan --json` |
| know which model answered, after a fallback too | the session row names the row, family and provider that answered; hardline's record says which row it fell back from | `matrix.v1.session.jsonl`, `provenance["hardline.v1.call"]` |
| read the schema of what is recorded | both documents' schemas are registered | `matrix describe hardline.v1.call` |

## What it does not do

- **It adds no model client.** Every call is a hardline call; what hardline cannot do (stream
  a reply, send anything but text), this cannot either. Tools are run by the agent loop above
  it, never here.
- **No other model runtime.** It bridges hardline alone; another model runtime would be
  another extension.
- **The registry decides where prompts go.** A `models` section in a config you did not write
  can send prompts anywhere ([SECURITY.md](../../SECURITY.md)).
- **Its name is provisional.** A rename would move its type URLs, which stored session rows
  carry.

## Next

- [README](../../README.md): why a bridge, install, a local model, the options.
- [matrix's overview](../../../matrix/docs/explanation/overview.md) and
  [hardline's](../../../hardline/docs/explanation/overview.md).
- [CONTRACT.md](../../CONTRACT.md): what each surface hides, its defaults and failures.

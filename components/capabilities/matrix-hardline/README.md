# matrix-hardline

hardline as a [matrix](../matrix/README.md) model runtime: install it, and a matrix config can
send its model calls to any model family hardline has a row for.

## Why a bridge

- **matrix** runs components and agents. A model call in matrix runs on a **model runtime**, a
  port of two calls: `check` a model name, `call` it. matrix ships only the `mock`, and
  depends on no package that calls a model, so installing matrix never installs a model
  client you did not choose.
- **[hardline](../hardline/README.md)** makes one call to any model family, and knows nothing
  of matrix.
- **matrix-hardline** depends on both and holds the one adapter between them, registered as a
  matrix extension: `matrix-hardline.v1.model-runtime`.

Each side changes on its own, and neither imports the other. Without this package, a config
that names `matrix-hardline.v1.model-runtime` is refused by `matrix check`, and the fix says
what to install.

## Install

It is on no package index; install it beside matrix, from this repository. uv resolves its
`hardline` dependency from the same commit of the repository, so it comes along:

```bash
# the matrix command, with the bridge, at the releases' tags
uv tool install "git+https://github.com/mox-labs/gnx@matrix-v0.10.0#subdirectory=components/capabilities/matrix" \
  --with "matrix-hardline @ git+https://github.com/mox-labs/gnx@matrix-hardline-v0.1.0#subdirectory=components/capabilities/matrix-hardline"
```

The names `matrix` and `hardline` on PyPI belong to unrelated projects, so an installer that
does not read uv's workspace sources (pip) needs every package named by its git URL.

From a clone:

```bash
git clone https://github.com/mox-labs/gnx && cd gnx
uv tool install ./components/capabilities/matrix \
  --with ./components/capabilities/hardline --with ./components/capabilities/matrix-hardline
```

`matrix catalog --point model-runtime` then lists `matrix-hardline.v1.model-runtime` beside
the mock.

## Try it offline

A hardline registry inside the runtime's config, with one `mock` row, which replies from its
options. Nothing is running and nothing is paid for; the call goes through hardline all the
same:

```yaml
# matrix.yaml
matrix:
  model_runtimes:
    local:
      type: matrix-hardline.v1.model-runtime
      models:                      # a hardline registry; unset, hardline's own config files
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
matrix check
matrix run --input acme.v1.diff=diffs.jsonl
jq -c '{runtime, drives, name, family, backend: .provenance["hardline.v1.call"].backend}' .matrix/runs/*/matrix.v1.session.jsonl
```

```
ok: 0 agent runtimes, 1 model runtimes, 0 agents compose, 1 components wire (nothing was called)
run 20261107T212855Z-9573 completed: .matrix/runs/20261107T212855Z-9573
…
{"runtime":"matrix-hardline.v1.model-runtime","drives":["hardline"],"name":"offline","family":"mock","backend":"mock"}
```

## A local model

Swap the row for one that names a server. ollama, serving `qwen3:8b` on its default port:

```yaml
        default: qwen
        models:
          qwen: {backend: openai-compat, base_url: "http://127.0.0.1:11434/v1",
                 model: "qwen3:8b", family: qwen, provider: ollama, local: true,
                 retries: 0}       # local dev: fail at once, not after three tries
```

`matrix check` and `matrix plan` resolve the row without calling it, so they pass with the
server down. `matrix run` with nothing listening fails, and its fix names the `base_url` to
check. [hardline's guide](../hardline/docs/guide.md#configure) has every field of a row, and
mlx_lm.server and hosted rows.

A `pydantic-ai` agent runtime with `model_runtime: local` makes every model call of its
sessions on this runtime, and `matrix.pydantic_ai_model(runtime)` gives a pydantic-ai `Model`
over it, for a caller that runs pydantic-ai itself.

## Reference

- **Options** (`matrix describe matrix-hardline.v1.model-runtime`):

  | key | default | what |
  |---|---|---|
  | `models` | none: hardline's own config files, its tiers (`~/.hardline/config.yaml`, `./hardline.yaml`, `$HARDLINE_CONFIG`) | a hardline registry section, `{default?, models: {...}}` |
  | `default_model` | none: the registry's default | the row a request that names none is sent to |
  | `max_tokens` | none: the row's | used where a request gives none |
  | `temperature` | none: the row's, else the provider's | used where a request gives none |

- **The reply** is matrix's neutral `ModelCall`. What only hardline knows of the call (its
  backend, attempts, retries, fallback, what it sent, the registry row and where each of its
  values came from) is hardline's own record, carried whole in
  `provenance["hardline.v1.call"]`. A call that failed carries what hardline's error said,
  `provenance["matrix-hardline.v1.failed-call"]`. Both schemas are registered, so
  `matrix describe hardline.v1.call` prints one.
- **Refused at binding**: a model the registry does not resolve; tools on a row (or a
  fallback of it) whose backend cannot call them; tools and an output structure in one call
  (hardline's `extract` takes no tools). Each is a `config` problem when the step or agent is
  built, before anything runs.
- **Warnings** hardline finds in the registry (a fallback that shares its primary's fate) are
  in `matrix plan`.

## Next

| | |
|---|---|
| [Overview](docs/explanation/overview.md) | what the bridge is for, and what it gives an agent |
| [CONTRACT.md](CONTRACT.md) | what each surface hides, its defaults and failures |
| [SECURITY.md](SECURITY.md) | a model runtime's config decides where prompts go |
| [CHANGELOG.md](CHANGELOG.md) | what changed in each release |

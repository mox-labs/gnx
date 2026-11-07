# matrix-hardline

hardline as a [matrix](../matrix/README.md) model runtime.

matrix ships no model runtime that calls a model, and depends on none; [hardline](../hardline/README.md)
is one model call to any model family, and knows nothing of matrix. This package depends on both and
holds the one adapter between them. Install it, and a matrix config can name hardline as a model
runtime:

```yaml
matrix:
  model_runtimes:
    local:
      type: matrix-hardline.v1.model-runtime
      default_model: qwen
      models:                       # a hardline registry; leave it out for hardline's own tiers
        default: qwen
        models:
          qwen: {backend: openai-compat, base_url: "http://127.0.0.1:8080/v1", model: qwen3-8b, family: qwen}
  agent_runtimes:
    loop: {type: pydantic-ai, model_runtime: local}
```

A `matrix.v1.model-step` on `local` is one hardline call; a `pydantic-ai` agent on `loop` makes every
model call of its session on `local`; `matrix.pydantic_ai_model(runtime)` gives a pydantic-ai `Model`
over it for a caller that runs pydantic-ai itself.

## What it is

- **The runtime**, `matrix-hardline.v1.model-runtime`, keeps matrix's `ModelRuntime` port on a hardline
  runtime. Its options (`matrix describe matrix-hardline.v1.model-runtime`):

  | key | default | what |
  |---|---|---|
  | `models` | none: hardline's own tiers (`~/.hardline/config.yaml`, `./hardline.yaml`, `$HARDLINE_CONFIG`) | a hardline registry section, `{default?, models: {...}}` |
  | `default_model` | none: the registry's default | the row a request that names none is sent to |
  | `max_tokens` | none: the row's | used where a request gives none |
  | `temperature` | none: the row's, else the provider's | used where a request gives none |

- **The reply** is matrix's neutral `ModelCall`. What only hardline knows of the call (its backend,
  attempts, retries, fallback, what it sent, the registry row and where each of its values came from)
  is hardline's own record, carried whole in `provenance["hardline.v1.call"]`. A call that failed
  carries what hardline's error said, `provenance["matrix-hardline.v1.failed-call"]`. Both schemas are
  registered, so `matrix describe hardline.v1.call` prints one, and a session row reads with `jq`:

  ```sh
  jq '.provenance["hardline.v1.call"].fallback_from' .matrix/runs/<run>/matrix.v1.session.jsonl
  ```

- **Refused at binding**: a model the registry does not resolve; tools on a row (or a fallback of it)
  whose backend cannot call them; tools and an output structure in one call (hardline's `extract` takes
  no tools). Each is a `config` problem when the step or agent is built, before anything runs.
- **Warnings** hardline finds in the registry (a fallback that shares its primary's fate) are in
  `matrix plan`.

[CONTRACT.md](CONTRACT.md) states what each surface hides, its defaults and failures;
[CHANGELOG.md](CHANGELOG.md) what changed.

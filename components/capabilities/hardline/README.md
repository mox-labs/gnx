# hardline

One port for every model family. Messages in; text or a validated object out; tagged with
the family that answered, what it cost, and how long it took.

```bash
uvx --from "git+https://github.com/mox-labs/gnx#subdirectory=components/capabilities/hardline" hardline models
```

## What it is, and what it is not

A **model runtime**: stateless calls to a model, with a registry that makes a model a row of
configuration rather than a class. It is **not** an agent runtime — no tool loop, no
permissions, no trajectory. Agents live one layer up, in matrix, and reach models through
this port.

## Configure

Models are registry rows. Tiers merge field by field, lowest first:
`~/.hardline/config.yaml` < `./hardline.yaml` < `$HARDLINE_CONFIG`.

```yaml
default: qwen3-8b
models:
  qwen3-8b:                              # a local model: mlx_lm.server speaks the OpenAI protocol
    backend: openai-compat
    base_url: http://127.0.0.1:8080/v1
    model: mlx-community/Qwen3-8B-4bit
    family: qwen                          # required; never inferred
    local: true
  gemini-flash:
    backend: openai-compat
    base_url: https://generativelanguage.googleapis.com/v1beta/openai/
    model: gemini-2.5-flash
    family: gemini
    api_key: env:GEMINI_API_KEY          # a reference; a literal key is rejected
    structured: json_object
  haiku:
    backend: anthropic
    model: claude-haiku-4-5-20251001
    family: claude
    api_key: file:~/.secrets/claude-api
```

Fields: `backend`, `model`, `family` (required) · `base_url` · `api_key` (`env:`/`file:` only) ·
`local` · `structured` (`prompt` | `json_object` | `json_schema`) · `max_tokens` · `temperature` ·
`timeout_s` · `options` (backend-specific, e.g. `token_param: max_completion_tokens` for
OpenAI's newer models).

A bad row fails at composition, naming the file and the key path:

```
hardline: invalid model registry:
  ./hardline.yaml: models.qwen3-8b.famly: Extra inputs are not permitted
```

## Use

```python
from hardline import build_runtime

rt = build_runtime()                                   # discovers the tiers above
c = await rt.complete("qwen3-8b", "Summarise in one line: ...")
c.text, c.family, c.usage, c.latency_ms

s = await rt.extract("haiku", "Is this claim supported? ...", output=Verdict)
s.value          # a validated Verdict
s.completion     # the Completion that produced it, attempts included
```

`extract` states the schema in an instruction (and hands it to the provider when the row's
`structured` mode allows), validates the reply, and on failure shows the model its own reply
and the validation error once before raising `SchemaError`. A provider's "structured" mode is
an optimisation; validation here is the guarantee.

Another composition root wires hardline from a section of its own config:

```python
rt = build_runtime(config=ix_config["models"], label="ix.yaml#models")
```

CLI: `hardline models` · `hardline check` · `hardline complete <model> "<prompt>" [--json]` ·
`hardline --skill` (the text an agent reads).

## Extend

A backend is two members — a `structured_modes` frozenset and an async `complete(spec,
request, api_key) -> RawCompletion` — and one entry point:

```toml
[project.entry-points."hardline.backends"]
bedrock = "my_pkg.bedrock:BedrockBackend"
```

The entry-point name is what a registry row puts in `backend:`. It may not shadow a built-in.
`tests/test_hardline_backends.py::test_every_backend_satisfies_the_port` runs over every
installed backend.

## Built-in backends

| backend | protocol | covers |
|---|---|---|
| `openai-compat` | OpenAI chat completions | mlx_lm.server, ollama, vLLM, llama.cpp, Gemini compat, OpenAI |
| `anthropic` | Anthropic Messages | Claude (structured: `prompt` only) |
| `mock` | none | offline; `options.script` plants failures |

## Type URLs

`hardline.v1/completion` (the `Completion` artifact) · `hardline.v1/backend.<name>` (registry
keys) — the matrix convention `<namespace>.v<version>/<resource>`.

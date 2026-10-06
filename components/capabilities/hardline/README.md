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
`timeout_s` · `retries` (transient failures only, default 2, full-jitter backoff, honours a
provider's Retry-After) · `fallbacks` (registry names tried once retries are spent) ·
`options` (backend-specific, e.g. `token_param: max_completion_tokens` for OpenAI's newer
models).

A bad row fails at composition, naming the file and the key path:

```
hardline: invalid model registry:
  ./hardline.yaml: models.qwen3-8b.famly: Extra inputs are not permitted
```

## Use

```python
from hardline import build_runtime

rt = build_runtime()  # discovers the tiers above
c = await rt.complete("qwen3-8b", "Summarise in one line: ...")
c.text, c.family, c.usage, c.latency_ms

s = await rt.extract("haiku", "Is this claim supported? ...", output=Verdict)
s.value  # a validated Verdict
s.completion  # the Completion that produced it, attempts included
```

`extract` states the schema in an instruction (and hands it to the provider when the row's
`structured` mode allows), validates the reply, and on failure shows the model its own reply
and the validation error once before raising `SchemaError`. A provider's "structured" mode is
an optimisation; validation here is the guarantee.

Another composition root wires hardline from a section of its own config:

```python
rt = build_runtime(config=ix_config["models"], label="ix.yaml#models")
```

CLI: `hardline models [--json]` · `hardline check [--json]` ·
`hardline complete <model> "<prompt>" [--json]` · `hardline --skill` (the text an agent reads) ·
`hardline --version`. `--max-tokens` must be > 0 and `--temperature` >= 0, the same bounds as a
registry row; anything else is a usage error before a call is spent.

### Output contract

Every command that prints a result takes `--json`. The keys below are stable; new keys may be
added, none removed or renamed without a version bump.

| command | stdout JSON |
|---|---|
| `models --json` | `{"default": str\|null, "models": [ModelSpec without api_key, options]}` |
| `check --json` | `{"ok": bool, "tiers": [{"source": str, "present": bool}], "models": [{"name", "backend", "family", "ok": bool, "problem": str\|null}]}` |
| `complete --json` | the `Completion` without `raw`: `type_url, name, text, family, model, backend, local, usage, request_id, latency_ms, attempts, retries, fallback_from` |

`check` resolves each row's `api_key` reference and reports one that is unset or unreadable as
that row's `problem` (never the value), and exits 3. Its report is printed before the exit, so
`check --json` gives both the per-row report on stdout and the error line on stderr.

### Exit codes and errors

Shared with ix. A failure under `--json` is one line on stderr,
`{"error": {"kind", "message", "retryable", "retry_after", "fix"}}`; without `--json` the same
message is prose. `retry_after` is the provider's hint in seconds, or `null`. `fix` is a next
step that can succeed, or `null`.

| exit | meaning | `kind` | raised from |
|---|---|---|---|
| 0 | success | — | — |
| 1 | failure not otherwise classified | `unknown` | `BackendError(reason="unknown")`, `ContractError`, `SchemaError` |
| 2 | usage error (bad flags or arguments) | — | argparse |
| 3 | config: change the input | `config`, `bad_request` | `ConfigError`, `UnknownModelError`, `SecretError`, `BackendError(reason="bad_request")` |
| 4 | not found | `not_found` | reserved; hardline raises none today |
| 5 | transient: retrying later may succeed | `rate_limit`, `timeout`, `unavailable` | `BackendError` with `retryable` true |
| 6 | auth: the provider rejected the key | `auth` | `BackendError(reason="auth")` |

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

# hardline

One port for every model family. Messages in; text or a validated object out; tagged with
the family that answered, what it cost, and how long it took.

```bash
uvx --from "git+https://github.com/mox-labs/gnx#subdirectory=components/capabilities/hardline" hardline models
```

hardline depends on pydantic, pyyaml and httpx2, and on no provider SDK: it speaks both wire
protocols over HTTP itself.

## What it is, and what it is not

A **model runtime**: stateless calls to a model, with a registry that makes a model a row of
configuration rather than a class. It is **not** an agent runtime — no tool loop, no
permissions, no trajectory. Agents live one layer up, in matrix, and reach models through
this port.

## Configure

Models are registry rows. Tiers merge field by field, lowest first:
`~/.hardline/config.yaml` < `./hardline.yaml` < `$HARDLINE_CONFIG`. The second is found through
the working directory and the third through the environment, so each tier is named with how it
was found (`user`, `cwd`, `$HARDLINE_CONFIG`) wherever it is named: in errors, in
`hardline check`, and in each row's record. `$HARDLINE_CONFIG` must name a file.

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
provider's Retry-After) · `fallbacks` (distinct registry names tried once retries are spent) ·
`options` (the backend's own; see [Built-in backends](#built-in-backends)).

**Where a row sends is in the row.** A row with no `base_url` is given its backend's default
when the runtime is composed (`https://api.openai.com/v1`, `https://api.anthropic.com`), so
`hardline models` shows it and a call's record names it. Nothing is read from the environment
to decide it: not `OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL` or `OPENAI_ORG_ID`, nor a proxy
(`HTTPS_PROXY` and the like) or certificate variable (`SSL_CERT_FILE`). TLS is verified against
the operating system's trust store. A redirect is reported, never followed, since following it
would send the row's key along.

A bad row fails at composition, naming the file and the key path:

```
hardline: invalid model registry:
  cwd ./hardline.yaml: models.qwen3-8b.famly: Extra inputs are not permitted
```

So does a row its backend cannot serve: an option the backend does not read or a bad value for
one, a `base_url` that is not an http(s) URL, an `anthropic` row with no `api_key`. `hardline
check` exits 0 only when no row would fail that way, and every key reference resolves.

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
and the validation error once before raising `SchemaError`. The repair is sent to the row that
answered, never back to a row before it in the fallback chain that has just failed. Each JSON value in the reply is
tried in turn, so bracketed prose before the answer ("see [1]") does not hide it. A provider's
"structured" mode is an optimisation; validation here is the guarantee.

### What a call returns

`complete` returns whatever the provider sent, and says what it is. A reply cut at
`max_tokens`, refused, withheld by the provider or empty is never raised and never looks like a
whole answer:

```python
c = await rt.complete("haiku", "...")
if not c.answered:  # the model ended its turn, did not refuse, and said something
    why = c.finish_reason  # length, content_filter, refusal, tool_call, other, or None
    said = c.refusal  # what the model said when it refused
```

`finish_reason` is one vocabulary across providers; `native_finish_reason` keeps the provider's
own value. Beside the text, every `Completion` carries the record of the call, which only
hardline knows:

| field | what it holds |
|---|---|
| `name`, `family`, `model`, `backend`, `local` | the row that **answered**; `fallback_from` names the one asked for, when a fallback answered |
| `params` | what that row was sent: `max_tokens`, `temperature`, the structured mode (`None` for `complete`) |
| `attempt_log` | every backend call, in order: the row asked, why it failed (`None` for the answer), the usage it reported, its latency |
| `usage` | the sum of the usage in `attempt_log`; a failed call reports none, so it adds nothing. `input_tokens` is every input token, the cache's included, on every backend; `cache_read_tokens` and `cache_write_tokens` say how many of them the provider's cache served or took |
| `turns_digest` | the digest of the turns the answering call was sent, `extract`'s schema instruction and repair turns included |
| `row` | the answering row's digest, and for each field a configuration set, the tier that set it (`cwd ./hardline.yaml`, `matrix.models`, `default of backend openai-compat`) |
| `request_id`, `latency_ms`, `attempts`, `retries` | the provider's id for the answer; wall time; schema attempts; transient retries |

A digest is `sha256:` and the hex SHA-256 of canonical JSON (UTF-8, no whitespace, keys
sorted): of `[[role, content], ...]` for the turns, of the row's `ModelSpec` for the row. A
reader can recompute either without hardline. A `BackendError` or `SchemaError` raised by the
runtime carries `attempt_log` and `usage` too: what the failed call cost.

A runtime's connections belong to the event loop that opened them, and are pooled within that
loop. Close them in that loop before it ends, with `async with rt:` or `await rt.aclose()`.
The runtime stays usable afterwards, so one runtime can serve several `asyncio.run` calls:

```python
async def main() -> None:
    async with rt:
        c = await rt.complete("qwen3-8b", "...")


asyncio.run(main())
asyncio.run(main())  # the same runtime: new connections in the new loop
```

A runtime called from a new loop never reuses a connection from another loop. If that other
loop ended without `aclose()`, its connections can no longer be closed. They stay open until
garbage collection, and hardline logs a warning that names the fix.

Another composition root wires hardline from a section of its own config:

```python
rt = build_runtime(config=ix_config["models"], label="ix.yaml#models")
```

CLI: `hardline models [--json]` · `hardline check [--json]` ·
`hardline complete <model> "<prompt>" [--json]` · `hardline --skill` (the text an agent reads) ·
`hardline --version`. `--max-tokens` must be > 0 and `--temperature` >= 0, the same bounds as a
registry row; anything else is a usage error before a call is spent. The library holds a call's
`max_tokens` and `temperature` to the same bounds, as a `ConfigError`. Without `--json`,
`complete` prints the text, and a note on stderr when it is not a whole answer.

### Output contract

Every command that prints a result takes `--json`. The keys below are stable; new keys may be
added, none removed or renamed without a version bump.

| command | stdout JSON |
|---|---|
| `models --json` | `{"default": str\|null, "models": [ModelSpec without api_key, options]}`, each row's `base_url` filled |
| `check --json` | `{"ok": bool, "tiers": [{"source": str, "present": bool}], "models": [{"name", "backend", "family", "ok": bool, "problem": str\|null}]}` |
| `complete --json` | the `Completion` without `raw`: `type_url, name, text, family, model, backend, local, usage, request_id, latency_ms, attempts, retries, fallback_from, finish_reason, native_finish_reason, refusal, params, attempt_log, turns_digest, row` |

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
| 3 | config: change the input | `config`, `bad_request` | `ConfigError` (a row its backend cannot serve, a broken backend plugin the row names, a bad call argument), `UnknownModelError`, `SecretError`, `BackendError(reason="bad_request")` |
| 4 | not found | `not_found` | reserved; hardline raises none today |
| 5 | transient: retrying later may succeed | `rate_limit`, `timeout`, `unavailable` | `BackendError` with `retryable` true |
| 6 | auth: the provider rejected the key | `auth` | `BackendError(reason="auth")` |

## Extend

A backend is two members — a `structured_modes` frozenset and an async `complete(spec,
request, api_key) -> RawCompletion` — and one entry point. Three more are optional:

- `check(spec) -> Sequence[str]`: what is wrong with a row for this backend, each problem
  starting with its key (`options.region: ...`). The runtime calls it for every row when it is
  built, so a row that would fail its first call fails composition and `hardline check`.
- `aclose()`: closes the connections opened in the running loop. A runtime's `aclose()` calls it.
- `default_base_url`: where a row that names none sends. The composition root writes it into the
  row.

`RawCompletion`'s `finish_reason`, `native_finish_reason` and `refusal` default to `None`, so a
backend that does not set them still satisfies the port; its replies are then never `answered`.

```toml
[project.entry-points."hardline.backends"]
bedrock = "my_pkg.bedrock:BedrockBackend"
```

The entry-point name is what a registry row puts in `backend:`. hardline's own backends are
entry points too. Each is loaded on its own: one that cannot be loaded, is not a backend, or
shares its name with another fails the rows that name it, with a `ConfigError` saying why, and
no other row. `tests/test_hardline_backends.py::test_every_backend_satisfies_the_port` runs
over every installed backend.

## Built-in backends

| backend | protocol | default `base_url` | `options` | covers |
|---|---|---|---|---|
| `openai-compat` | OpenAI chat completions, `POST <base_url>/chat/completions` | `https://api.openai.com/v1` | `token_param`: `max_tokens` (default) or `max_completion_tokens` | mlx_lm.server, ollama, vLLM, llama.cpp, Gemini compat, OpenAI |
| `anthropic` | Anthropic Messages, `POST <base_url>/v1/messages` | `https://api.anthropic.com` | none | Claude (structured: `prompt` only; `api_key` required) |
| `mock` | none | none | `responses`, `script`, `default`, `finish_reason`, `refusal` | offline; `script` plants failures, `finish_reason` and `refusal` plant replies that are not whole answers |

A row's `timeout_s` bounds each phase of a request (connecting, sending, waiting for the reply).
A row's `retries` is the only retry policy: the HTTP transport retries nothing itself.

## Type URLs

`hardline.v1/completion`, the `Completion` artifact, in the matrix convention
`<namespace>.v<version>/<resource>`. A backend has no type URL: a registry row names it by its
entry-point name.

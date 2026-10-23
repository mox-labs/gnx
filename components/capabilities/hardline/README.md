# hardline

One port for every model family. Messages in; text or a validated object out; tagged with
the family that answered, what it cost, and how long it took.

```bash
uvx --from "git+https://github.com/mox-labs/gnx@hardline-v0.2.0#subdirectory=components/capabilities/hardline" hardline models
```

hardline depends on no provider SDK: it speaks both wire protocols over HTTP itself, on httpx2.
Its other dependencies are pydantic, pyyaml, the OpenTelemetry API (the library records
through it alone), and, for the `hardline` command, rich-click and the OpenTelemetry SDK with
its OTLP exporter.

## What it is, and what it is not

A **model runtime**: stateless calls to a model, with a registry that makes a model a row of
configuration rather than a class. It is **not** an agent runtime — no tool loop, no
permissions, no trajectory. A call may offer the model tools and return the calls it asks for,
but hardline runs none of them. Agents live one layer up, in matrix, and reach models through
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

Fields: `backend`, `model`, `family` (required) · `provider` (who serves the row, as the GenAI
conventions name providers: `openai`, `ollama`; what telemetry calls it, the backend's name when
unset) · `base_url` · `proxy` (an http(s) proxy, no
credentials in it) · `api_key` (`env:`/`file:` only) · `local` · `structured` (`prompt` |
`json_object` | `json_schema`) · `max_tokens` · `temperature` · `timeout_s` · `retries` (transient failures only, default 2, full-jitter backoff, honours a
provider's Retry-After) · `fallbacks` (distinct registry names tried once retries are spent) ·
`limits` (bounds on a call, below) · `options` (the backend's own; see
[Built-in backends](#built-in-backends)).

**A call has its own bound.** One `extract` can make (schema repairs + 1) × the sum over its
chain of (`retries` + 1) backend calls, each paid for in tokens; `timeout_s` bounds each phase of
one (connecting, sending, each read), not the call as a whole.
`limits` bounds the whole call, every retry, fallback and repair included, and is unset by
default:

```yaml
models:
  haiku:
    # ... the row's other fields
    limits:
      attempts: 4      # backend calls, at most
      seconds: 300     # wall time from the call's start; a running attempt is cut when it passes
      tokens: 20000    # input + output tokens reported, checked between calls
```

The row asked for sets the bounds; a fallback's own `limits` apply only when it is asked for by
name. A call a limit stops raises what it would have raised anyway, with `limit` set to the bound
that stopped it (`attempts`, `seconds` or `tokens`): a `BackendError` with the last failure's
reason (`timeout` when a running attempt was cut), or, between schema repairs, a `SchemaError`.

**Where a row sends is in the row.** A row with no `base_url` is given its backend's default
when the runtime is composed (`https://api.openai.com/v1`, `https://api.anthropic.com`), so
`hardline models` shows it and a call's record names it. Nothing is read from the environment
to decide it: not `OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL` or `OPENAI_ORG_ID`, nor a proxy
(`HTTPS_PROXY` and the like) or certificate variable (`SSL_CERT_FILE`). A row behind a proxy
names it (`proxy: http://proxy.internal:3128`). TLS is verified against the operating system's
trust store, the hop to an `https://` proxy included. A redirect is never followed, since following it would send the row's key along:
it is a config error (exit 3) whose fix says where to point `base_url`.

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
and the validation error once before raising `SchemaError`; an empty reply, which has nothing to
show, is followed by a turn saying it was empty instead. The repair is sent to the row that
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

A reply that asks for tools is a whole answer when the call offered them, and only then (see
[Tools](#tools)). `finish_reason` is one vocabulary across providers; `native_finish_reason`
keeps the provider's own value. Beside the text, every `Completion` carries the record of the call, which only
hardline knows:

| field | what it holds |
|---|---|
| `name`, `family`, `model`, `backend`, `local` | the row that **answered**; `fallback_from` names the one asked for, when a fallback answered |
| `params` | what that row was sent: `max_tokens`, `temperature`, the structured mode (`None` for `complete`), the names of the `tools` offered |
| `tool_calls` | the calls the model asked for, in order: each `id`, `name` and `arguments` (the JSON text the model wrote). Empty when it asked for none |
| `attempt_log` | every backend call, in order: the row asked, why it failed (`None` for the answer), the usage it reported, its latency |
| `usage` | the sum of the usage in `attempt_log`; a failed call reports none, so it adds nothing. `input_tokens` is every input token, the cache's included, on every backend; `cache_read_tokens` and `cache_write_tokens` say how many of them the provider's cache served or took |
| `turns_digest` | the digest of the turns the answering call was sent, `extract`'s schema instruction and repair turns included |
| `row` | the answering row's digest, and for each field a configuration set, the tier that set it (`cwd ./hardline.yaml`, `matrix.models`, `default of backend openai-compat`) |
| `request_id`, `latency_ms`, `attempts`, `retries` | the provider's id for the answer; wall time; schema attempts; transient retries |

A digest is `sha256:` and the hex SHA-256 of canonical JSON (UTF-8, no whitespace, keys
sorted): of `[[role, content], ...]` for the turns, of the row's `ModelSpec` for the row. A turn
with tool parts has a third element, an object with those it has: `tool_calls` as
`[[id, name, arguments], ...]`, `tool_call_id`, `is_error`. A
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

### Tools

A call may offer the model tools. hardline sends their definitions and returns the calls the
model asked for; the caller runs them, and sends each result back as a `tool` turn:

```python
from hardline import Message, ToolSpec

lookup = ToolSpec(
    name="lookup",
    description="Find a word in the dictionary.",
    parameters={"type": "object", "properties": {"word": {"type": "string"}}},
)
c = await rt.complete("haiku", "What does ossify mean?", tools=[lookup])
if c.tool_calls:  # finish_reason "tool_call": a whole answer, since tools were offered
    call = c.tool_calls[0]  # call.id, call.name, call.arguments (JSON text)
    turns = [
        Message(role="user", content="What does ossify mean?"),
        Message(role="assistant", content=c.text, tool_calls=c.tool_calls),
        Message(role="tool", tool_call_id=call.id, content="to turn into bone"),
    ]
    c = await rt.complete("haiku", turns, tools=[lookup])
```

`arguments` is the JSON text the model wrote: it may not parse, or not fit the schema, and the
caller validates it. A `tool` turn with `is_error=True` tells the model the tool failed or its
arguments were refused. Both protocols carry tools: OpenAI's `tools` and `tool_calls` with the
`tool` role, and the Messages API's `tools`, `tool_use` and `tool_result` blocks, every result
answering one assistant turn sent as one user turn.

A plain `complete` keeps its promise: a reply that ended on a tool call is not `answered` when
the call offered no tools. Every row a call with tools may reach, the fallbacks included, must
be served by a backend that can call tools, or the call is a `ConfigError` before anything is
sent; `runtime.check_tools(name)` asks the same question ahead of a call. The built-in backends
all can. The CLI offers no tools.

### Observing a call

What a call does between being asked and answering (each attempt that failed, each fallback)
is told to an observer, as it happens:

```python
from hardline import CallEvent, FellBack, build_runtime


class Fallbacks:
    def observe(self, event: CallEvent) -> None:
        if isinstance(event, FellBack):
            print(f"{event.requested}: {event.name} failed ({event.reason}); asking {event.to}")


rt = build_runtime(observer=Fallbacks())
```

The events are `AttemptFailed` (the row asked, why it failed, and `retry_in_s`, the wait before
it is asked again, or `None`), `FellBack` (`name` → `to`, and the reason) and `Answered` (the
`Completion`). An observer ignores a kind it does not know, is called on the call's own task
and must not block; one that raises is logged and ignored. With none, nothing is told: the
`Completion` still carries `fallback_from` and the `attempt_log`. `build_runtime` passes one
that counts retries and fallbacks in OpenTelemetry, below, unless it is handed another.

### Telemetry

`build_runtime` records OpenTelemetry on the GenAI semantic conventions (as read at
`open-telemetry/semantic-conventions-genai` `9ffc7e0`, status Development), through the API
only: nothing is recorded until the application installs an SDK, and the library installs none.
A host hands its own providers as `build_runtime(tracer_provider=..., meter_provider=...)`.

The `hardline` command is an application, and installs the SDK from the standard `OTEL_*`
variables, as `matrix` and `ix` do (`hardline.configure_telemetry()`, for another entry point of
one's own): `OTEL_TRACES_EXPORTER` and `OTEL_METRICS_EXPORTER` (`otlp`, the default, or `console`
on stderr), OTLP over http/protobuf alone (`OTEL_EXPORTER_OTLP_PROTOCOL=grpc` is refused with a
warning, and nothing is exported over it) to the endpoint the `OTEL_EXPORTER_OTLP_*` variables
name, and
`service.name` `hardline` unless `OTEL_SERVICE_NAME` says otherwise. With none of them set,
nothing is installed. What it cannot install is a `hardline: warning:` line, and under `--json`
one of `check`'s `warnings`.

| signal | what | attributes |
|---|---|---|
| span `chat {model}`, kind CLIENT | one per backend call, under the caller's current span: a retry or a fallback is a sibling | `gen_ai.operation.name`, `gen_ai.provider.name` (the row's `provider`, else the backend's name), `gen_ai.request.model`, `.max_tokens`, `.temperature`, `gen_ai.output.type` (`json` in a structured mode), `server.address`, `server.port`, `hardline.row`; on an answer `gen_ai.response.model`, `.id`, `.finish_reasons` (the provider's own) and `gen_ai.usage.*`; on a failure `error.type` (the `BackendError` reason) and status `ERROR` |
| `gen_ai.client.inference.duration` (s) | every backend call, failed ones included | operation, provider, request and response model, server, `error.type` |
| `gen_ai.client.inference.usage.input_tokens`, `.output_tokens`, `.cache_read.input_tokens`, `.cache_write.input_tokens` | each strictly positive count a reply reports | the same, with `gen_ai.token.modality: text` |
| `hardline.retries`, `hardline.fallbacks` | each retry, and each move down a fallback chain | `hardline.row`, `hardline.fallback`, `error.type` |

hardline counts the inference tokens of the calls it makes; a host counts its own sessions,
not the model calls inside them. **No content is recorded**: no prompt, system prompt, reply,
refusal or error message, as an attribute, an event or a span status. There is no option to
record it. The names above (spans, metrics, attribute keys) are not contract and may change
without notice; that no content is recorded is.

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
added, none removed or renamed without a version bump. A program that validates `complete
--json` output, or stored `Completion` JSON, back into a `Completion` refuses a key it does not
know, so it upgrades hardline before the program that writes the JSON does.

| command | stdout JSON |
|---|---|
| `models --json` | `{"default": str\|null, "models": [ModelSpec without api_key, options]}`, each row's `base_url` filled |
| `check --json` | `{"ok": bool, "tiers": [{"source": str, "present": bool}], "models": [{"name", "backend", "family", "ok": bool, "problem": str\|null}], "warnings": [problem]}` |
| `complete --json` | the `Completion` without `raw`: `type_url, name, text, family, model, backend, local, usage, request_id, latency_ms, attempts, retries, fallback_from, finish_reason, native_finish_reason, refusal, tool_calls, params, attempt_log, turns_digest, row` |

`complete --json` carries no hardline version: it is a `Completion`, and validates back into
one, which a key of the CLI's own would break. A program that ledgers the version reads
`hardline --version` (or `importlib.metadata.version("hardline")` in process).

A `problem` is the record the capability CLIs share: `{"path", "kind", "message", "fix",
"allowed", "got", "source"}`. `check` warns of a fallback that sends where its primary does
(`shared_fate`: the same backend and `base_url`), since the server going down takes both; the
row is legal, so `check` still exits 0. A library caller reads the same list as
`runtime.warnings`, and each is logged once on the `hardline` logger when the runtime is built.

`check` resolves each row's `api_key` reference and reports one that is unset or unreadable as
that row's `problem` (never the value), and exits 3. Its report is printed before the exit, so
`check --json` gives both the per-row report on stdout and the error line on stderr.

### Exit codes and errors

The codes and the error envelope are those the capability CLIs agree, in the workspace's
`exit-codes.json` and `error-envelope.json`. With `--json`, every failure, a usage error and a
defect included, is one line on stderr:

```json
{"schema": "hardline.v1.error",
 "error": {"kind": "transient", "message": "...", "fix": "...", "complete": true,
           "problems": [], "details": {"reason": "rate_limit", "retry_after": 7.0}}}
```

`fix` is always a next step that can succeed. `problems` holds one record per bad key, as
`{"path", "kind", "message", "fix", "allowed", "got", "source"}`, with `source` the tier it came
from. `details.reason` is the `BackendError` reason, with `retry_after` (the provider's hint in
seconds) and `limit` (the one of the row's `limits` that stopped the call) when set. Without
`--json`, stderr carries `hardline: error (<kind>): <message>` and the fix as prose, and what
the library logs while the command runs (a broken backend plugin, say) as `hardline: warning:`
lines; under `--json`, stderr holds the envelope and nothing else.

| exit | `kind` | meaning | raised from |
|---|---|---|---|
| 0 | `ok` | success | — |
| 1 | `unknown` | a failure hardline did not classify | `BackendError(reason="unknown")`, `ContractError`, `SchemaError`, and any exception it did not mean to raise |
| 2 | `usage` | bad flags or arguments | the command line |
| 3 | `config` | change the input | `ConfigError` (a row its backend cannot serve, a broken backend plugin the row names, a bad call argument, a redirect), `UnknownModelError`, `SecretError`, `BackendError(reason="bad_request")` |
| 5 | `transient` | retrying later may succeed | `BackendError` with `retryable` true: `rate_limit`, `timeout`, `unavailable` |
| 6 | `auth` | the provider rejected the key | `BackendError(reason="auth")` |
| 130 | `interrupted` | Ctrl-C before the command finished | — |

`hardline` has no kind of its own, so no code from 10 up. Problem kinds are the shared ones
(`yaml_syntax`, `wrong_shape`, `unknown_key`, `missing_key`, `invalid_value`, `name_unknown`)
and two of hardline's: `shared_fate` (a warning) and `secret_unresolved`.

## Extend

A backend is two members — a `structured_modes` frozenset that holds `prompt`, and an async
`complete(endpoint, request, api_key) -> RawCompletion` — and one entry point. An `Endpoint` is
the registry row as a backend sees it: `name`, `backend`, `model`, `provider`, `base_url`,
`proxy`, `timeout_s`, `structured`, `options`, and `has_key` (whether the row names a key). The
runtime's retries, fallbacks and limits, the row's family and where its key lives are not in
it; the key, resolved, is `api_key`. `ModelSpec.endpoint()` makes one. Four more members are
optional:

- `check(endpoint) -> Sequence[str]`: what is wrong with a row for this backend, each problem
  starting with its key (`options.region: ...`). The runtime calls it for every row when it is
  built, so a row that would fail its first call fails composition and `hardline check`.
- `aclose()`: closes the connections opened in the running loop. A runtime's `aclose()` calls it.
- `default_base_url`: where a row that names none sends. The composition root writes it into the
  row.
- `supports_tools = True`: the backend sends a request's `tools` and its turns' tool parts on its
  wire, and returns the calls asked for as `RawCompletion.tool_calls`. Without it, a call that
  offers tools or sends tool turns to its rows is refused before anything is sent, so a backend
  written before tool calling never drops them silently.

`RawCompletion`'s `finish_reason`, `native_finish_reason` and `refusal` default to `None`, so a
backend that does not set them still satisfies the port's types; its replies are then never
`answered`.

hardline ships the port's requirements as a check, to run in a backend's own tests against the
installed hardline:

```python
from hardline import ModelSpec, answer_problems, backend_problems


def test_conforms() -> None:
    assert backend_problems(BedrockBackend()) == []


async def test_answers() -> None:
    spec = ModelSpec(
        name="t", backend="bedrock", model="m", family="x", base_url="http://127.0.0.1:9"
    )
    assert await answer_problems(BedrockBackend(transport=...), spec) == []
```

`backend_problems` reads the members: `structured_modes` a frozenset of known modes with
`prompt`, `complete` async, and `check`, `aclose` and `default_base_url` well formed when
present (`supports_tools` a bool). `answer_problems` makes one call for a row the backend can serve, and requires its
`check` to accept the row and `complete` to return a `RawCompletion` with a `finish_reason`.
Each returns what is wrong, empty when nothing is.

```toml
[project.entry-points."hardline.backends"]
bedrock = "my_pkg.bedrock:BedrockBackend"
```

The entry-point name is what a registry row puts in `backend:`. hardline's own backends are
entry points too. Each is loaded on its own: one that cannot be loaded, is not a backend, or
shares its name with another fails the rows that name it, with a `ConfigError` saying why, and
no other row. hardline's own tests hold every installed backend to `backend_problems`.

## Built-in backends

| backend | protocol | default `base_url` | `options` | covers |
|---|---|---|---|---|
| `openai-compat` | OpenAI chat completions, `POST <base_url>/chat/completions` | `https://api.openai.com/v1` | `token_param`: `max_tokens` (default) or `max_completion_tokens` | mlx_lm.server, ollama, vLLM, llama.cpp, Gemini compat, OpenAI |
| `anthropic` | Anthropic Messages, `POST <base_url>/v1/messages` | `https://api.anthropic.com` | none | Claude (structured: `prompt` only; `api_key` required) |
| `mock` | none | none | `responses`, `script`, `default`, `finish_reason`, `refusal` | offline; `script` plants failures, `finish_reason` and `refusal` plant replies that are not whole answers |

A row's `timeout_s` bounds each phase of a request (connecting, sending, waiting for the reply).
A row's `retries` is the only retry policy: the HTTP transport retries nothing itself. A 429,
a 408, a 5xx and a failure to connect or to get a reply in time are transient: retried, then
fallen back from.
Two 5xx are not, since no retry changes them: 501 (not implemented) and 505 (HTTP version not
supported) mean the row points somewhere that cannot serve it, and fail as `bad_request`.

## Type URLs

`hardline.v1/completion`, the `Completion` artifact, in the matrix convention
`<namespace>.v<version>/<resource>`. A backend has no type URL: a registry row names it by its
entry-point name.

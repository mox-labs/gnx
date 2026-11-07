# Changelog — hardline

## 0.3.1 — 2026-11-07

### Security

- floors raised past known advisories: httpx2 >= 2.12 (GHSA-8xx6-hgc6-gc2m, GHSA-h4x7-gw46-3wm6, GHSA-pf96-p4fj-6566)
- floors raised past known advisories: opentelemetry-api >= 1.45.1 (GHSA-xcq7-p3pf-p88h)

### Fixed

- **A failed tool's result is no longer sent as a success on `openai-compat`.** The chat-completions `tool` role has no error flag, and `Message.is_error` was dropped there, so a model was told a tool that failed had answered. The result's content is now marked, `[tool error] <content>`, the one encoding `CONTRACT.md` declares (ruling HO7: what a backend cannot carry is never dropped silently). `anthropic` already sent the Messages API's own `is_error`.
- **A refused connection says where to look.** A row whose server is not running (a local
  ollama or mlx_lm.server not started) failed with the transient kind's "retry later", which no
  wait fixes. A `ConnectError` now carries a fix naming the row's `base_url` and the key that
  sets it, and the error a caller sees once retries are spent keeps the last failure's fix: the
  `hardline` command prints it, and so does a matrix run through matrix-hardline.
  `BackendError` takes `fix=`, as every `HardlineError` does.
- **A wheel built from this directory carries the license.** `LICENSE` is a copy of the
  repository's, named by `license-files`; the package metadata gains its README and project URLs.

### Documentation

- The README is the entry: install, a first call offline, from Python. Everything else it held
  is in `docs/guide.md`, and `docs/explanation/overview.md` says what hardline is for.

## 0.3.0 — 2026-11-06

A call's reply and hardline's record of it are two documents; what a backend can do beyond the
port is a declared protocol; every default of a row is in the row and its record. Each entry
compares with 0.2.0. matrix 0.9.0, which reads the 0.2 `Completion`, does not yet work with this
release: its hardline adapter moves to a bridge distribution next.

### Removed (breaking)

- **`Completion.raw` and `RawCompletion.raw` are gone.** Each held a provider's reply minus its content, in that provider's own shape, with no schema, inside a versioned contract; nothing read it, and the CLI left it out. A backend that passes `raw=` now fails validation.
- **`hardline.NAMESPACE` is gone.** It said every hardline type URL is `hardline.v1`, which the reply's no longer is. `COMPLETION_TYPE_URL` and `CALL_TYPE_URL` remain, each the whole type URL.
- **`build_runtime(schema_retries=...)` and `ModelRuntime(schema_retries=...)` are gone.** The repair count is the row's `schema_retries` (below).
- **`TracedBackend` declares no member but the port's two.** It defined `check`, `aclose`, `supports_tools` and `default_base_url` on every instance and forwarded each by name, so every composed backend satisfied every optional protocol. It takes `(inner, telemetry)`, without the backend's name, which it read only for the provider.

### Changed (breaking)

- **`Completion` is the reply, `hardline.v2.completion`, and hardline's record of the call is `Completion.record`, a `CallRecord`, `hardline.v1.call`.** The reply keeps what any model runtime can state: `name`, `family`, `model`, `provider` (new), `local`, `text`, `tool_calls`, `finish_reason`, `native_finish_reason`, `refusal`, `usage`, `request_id`, `latency_ms`. The record holds what only hardline knows: `backend`, `attempts`, `retries`, `fallback_from`, `params`, `attempt_log`, `turns_digest`, `row`. Read `c.record.fallback_from` where it was `c.fallback_from`, and so on. Every reply key is now required, `type_url` aside. The record's major is its own: what hardline records grows there, and the reply's major need not move with it.
- **Type URLs are dotted**, `<package>.v<major>.<resource>`, as matrix, ix and recon spell theirs and hardline its error envelope (`hardline.v1.error`). `hardline.v1/completion` claimed to follow "the matrix convention" with a slash that no other package used. A `type_url` is checked: a reply or record with another one is refused.
- **`complete --json` prints the new shape**: the reply's keys, `answered`, and `record`. README "Output contract" and the skill say so.
- **`RowRecord.rungs` names every field of the row but its name**, `default` for each that no tier set, where it named only the fields a tier set and left the rest to be inferred.
- **`RequestParams.temperature` is `provider_default` when none was sent**, where it was `null`, which a reader could take for zero or for unrecorded (ruling HO6). It is `float | "provider_default"`.
- **Every row's digest changes**: a row's spec now holds `schema_retries`, and the digest is over the whole spec, defaults included. A digest from 0.2 does not match the same row under 0.3.
- **The parts of the record ignore keys they do not know**: `CallRecord`, `RequestParams`, `Attempt` and `RowRecord` are `extra="ignore"`, where they refused them. A record a newer 0.x wrote reads in an older one (MRC:c35); the reply itself still refuses an unknown key, so a reader of stored replies upgrades first.
- **`backend_problems` reads the optional members through their protocols**: a backend that declares `default_base_url = None` is now told it is not an http(s) URL, where `None` passed as "not declared".

### Added

- **`Completion.provider`**: who served the reply, the answering row's `provider`, else its backend's entry-point name, the rule hardline's telemetry already used for `gen_ai.provider.name`. A caller no longer asks the registry again to name it. `Endpoint.provider_name` holds the rule, once.
- **`answered` is in the JSON** (ruling HO8), a computed field: `complete --json` and `model_dump` carry it, so an agent reads it instead of recomputing it. JSON whose `answered` contradicts the reply's own keys is refused.
- **Optional backend capabilities are declared protocols**, each `runtime_checkable` and exported: `CheckedBackend`, `ClosableBackend`, `ToolCallingBackend` (`supports_tools: Literal[True]`) and `LocatedBackend` (`default_base_url: str`). The runtime reads each with `isinstance`, once, from the backend as loaded, before the composition root wraps it for telemetry; the composition root reads `LocatedBackend` the same way. No member is read by name with `getattr`. A built-in or third-party backend that already set these members needs no edit. New exports: the four protocols, `CallRecord`, `CALL_TYPE_URL`.
- **`schema_retries` is a row key**, default 1, at most 10: the repairs `extract` makes on the row, which the row asked for sets, whichever row of its chain answers, as for `limits`. It is in the row's digest, `models --json`, `worst_case` and the record's rungs.
- **`check --json` shows each row's `rungs`**: where each of its values came from, before any call.
- **`CONTRACT.md`**: what each published surface hides, its members, defaults, failure behaviour and stability; `tests/test_hardline_contract.py` pins the reply, the record, the registry row, the backend port and `hardline.__all__`.

## 0.2.0 — 2026-10-22

The first release with a changelog: each entry compares with 0.1.0.

### Removed (breaking)

- **`backend_type_url` is gone**, and with it `hardline.v1/backend.<name>`. The README called them registry keys, but registry rows and the composition root key on a backend's entry-point name, and nothing read them.
- **No `dev` extra.** hardline has no optional extras; the workspace's `dev` dependency group carries the test tools.

### Changed (breaking)

- **A backend is handed an `Endpoint`, not the whole `ModelSpec`.** `ModelBackend.complete(endpoint, request, api_key)` and `check(endpoint)` receive the registry row as a backend needs it (`name`, `backend`, `model`, `provider`, `base_url`, `proxy`, `timeout_s`, `structured`, `options`, and `has_key`), where every backend saw the runtime's retries, fallbacks and limits, the row's family and its key reference, and could come to depend on them. `ModelSpec.endpoint()` makes one; `answer_problems` still takes a row. New export: `Endpoint`.
- **No provider SDK.** Both wire protocols are spoken directly over httpx2: `openai-compat` (chat completions) and `anthropic` (the Messages API). hardline depends on no provider SDK; the `openai` and `anthropic` packages are no longer installed with it.
- **A row's location comes from its config only.** With no `base_url`, the SDKs read `OPENAI_BASE_URL` or `ANTHROPIC_BASE_URL`, and the OpenAI client sent `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` as headers, so a row's key could go to a host no config named. Nothing reads them now. The composition root writes each backend's default (`https://api.openai.com/v1`, `https://api.anthropic.com`) into a row that names none, so `hardline models` and every row record where they send. A backend that serves one provider declares `default_base_url`.
- **No proxy or certificate variable changes where a request goes or what it trusts**: clients ignore `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `SSL_CERT_FILE` and `SSL_CERT_DIR`. TLS is verified against the system trust store (truststore), on the hop to an `https://` proxy too.
- **A keyless `openai-compat` row sends no `Authorization` header**, where the SDK needed a placeholder key.
- **A redirect is a config error, not followed**: a `ConfigError` (exit 3) whose `fix` names where to point the row's `base_url`, derived from the `Location`. Following it would send the row's key along. Before, it was `unknown` (exit 1, "report a bug").
- **A `base_url` must be an http:// or https:// URL**, checked when the runtime is composed.
- **hardline's own backends register through its own `hardline.backends` entry points**, like a third party's, and the hard-coded table is gone. `discover_backends()` no longer takes `include_entry_points`; it returns exactly the installed entry points.
- **A broken backend plugin fails only the rows that name it.** An entry point that cannot be loaded, does not produce a `ModelBackend`, or shares its name with another used to abort composition for every row, the first two with an exception that was not a `HardlineError`. Each is now loaded on its own and kept as a stand-in that names the fault: a row naming it fails composition with a `ConfigError`, and a warning is logged.
- **Backend options are checked when the runtime is composed.** A backend may define `check(endpoint)`, returning what is wrong with a row; the runtime calls it for every row. Each built-in backend reads its `options` through a model of its own and refuses any key it does not read, so `hardline check` fails a row that would fail its first call. A mock `script` naming an unknown error reason is refused there too.
- **A keyless `anthropic` row is a config problem.** It used to pass `hardline check` and then fail its call as `auth` ("the provider rejected the key", exit 6) when no provider had been asked. It now fails composition, naming `models.<name>.api_key` (exit 3).
- **A 501 or 505 is not retried.** Both say the server cannot serve the request at all (not implemented; HTTP version not supported), so they are `bad_request` (exit 3) rather than `unavailable`, retried and then sent to every fallback. Every other 5xx is still transient.
- **The CLI prints the error envelope the capability CLIs share, and exits with their codes.** Under `--json` a failure is `{"schema": "hardline.v1.error", "error": {"kind", "message", "fix", "complete", "problems", "details"}}`, where it was `{"error": {"kind", "message", "retryable", "retry_after", "fix"}}`. The kinds are the agreed ones: a rate limit, timeout or unavailable provider is `transient` (exit 5) with `details.reason`, a malformed request is `config` (exit 3) with `details.reason: bad_request`, and `retry_after` and `limit` are in `details`. `fix` is never null. A registry error lists each bad key in `problems` (`path`, `kind`, `message`, `source`, ...). Ctrl-C is `interrupted` (130). Exit 4 (`not_found`, which hardline never raised) is gone.
- **Every failure is the envelope**: a usage error (exit 2) under `--json` is one too, and so is an exception hardline did not mean to raise (`unknown`, exit 1, with the traceback on stderr without `--json`), where both were prose or a traceback alone.
- **The CLI is rich-click**, the workspace's one stack, where it was argparse. A usage error is an exit code (2) returned from `main`, not `SystemExit`. Without `--json` an error is `hardline: error (<kind>): <message>` and a `hardline: fix:` line.
- `merge_tiers` returns, per model, the tier that set each field (`{field: source}`), and `ModelRegistry.from_config` takes `origins` in that shape.

### Changed

- **Telemetry names are not contract** (owner ruling 2026-10-22): a span's or a metric's name and an attribute's key may change without notice, the GenAI conventions' ones included. That no content is recorded is.
- Provider errors carry the provider's own message (`error.message`), with the key scrubbed, and name the row's location; `api.openai.com` is no longer named for a row that does not send there.
- **Each discovered tier is named with how it was found**: `user <path>`, `cwd <path>`, `$HARDLINE_CONFIG <path>`, in errors, in `hardline check`, and in each row's record.

### Added

- **Why a reply ended, and a refusal kept apart from an answer.** `RawCompletion` and `Completion` gain `finish_reason` (`stop`, `length`, `content_filter`, `refusal`, `tool_call`, `other`, or `None` when the provider did not say), `native_finish_reason` (the provider's own value, verbatim) and `refusal` (what the model said when it refused). Each protocol's own reasons are mapped: OpenAI's `finish_reason` and `message.refusal`, Anthropic's `stop_reason`. All three default, so a backend written before them works unedited.
- **The promise for `complete`, written down**: a reply cut at `max_tokens`, refused, withheld or empty is returned and said for what it is, never raised and never looking like a whole answer. `Completion.answered` is true only when the model ended its turn, did not refuse, and said something. `hardline complete` without `--json` prints a note on stderr beside such a reply, and still exits 0.
- `Completion.raw` keeps each choice's index and finish reason (OpenAI-compatible), where it dropped `choices` whole; message content is still never in it.
- The mock backend takes `finish_reason` and `refusal` options, to plant a truncated or refused reply.
- **Every call carries its whole record.** `Completion` gains, each defaulted:
  - `params`: what was sent to the model that answered, after its row's defaults (`max_tokens`, `temperature`, and the structured mode, `None` for a plain completion);
  - `attempt_log`: every backend call made, in order, each with the row asked, why it failed (`None` for the one that answered), the usage it reported and its latency;
  - `turns_digest`: the digest of the turns the answering call was sent, for `extract` with the schema instruction and repair turns;
  - `row`: the answering registry row's digest, and for each field a configuration set, the tier that set it (`cwd ./hardline.yaml`, `matrix.models`, `default of backend openai-compat`).
  A digest is `sha256:` over canonical JSON, so a reader can recompute it without hardline. New exports: `Attempt`, `RequestParams`, `RowRecord`, `FinishReason`.
- A `BackendError` the runtime raises carries `attempt_log` and `usage`, and a `SchemaError` too: what the failed call cost.
- **`ModelRuntime.aclose()`, and `async with runtime`.** Either closes the connections that the backends opened in the running event loop. The runtime stays usable afterwards. A backend that holds connections defines its own async `aclose()`, which the runtime calls; a backend without one is left alone.
- **A call has its own bound: `limits`.** A row may set `limits: {attempts, seconds, tokens}`, which bound a call that asks for it across every retry, fallback and schema repair: backend calls, wall time from the call's start (a running attempt is cut when it passes, and a wait for a retry that would pass it is not started), and tokens reported. Each is unset by default (owner ruling 2026-10-22), and `hardline check` shows what one call on each row can cost at worst (`worst_case` in each `check --json` row; new export `CallBound`, from `ModelRuntime.worst_case`). The token bound is kept as it is (owner ruling 2026-10-22): it bounds tokens, not spend, and is checked between calls. A call a limit stops raises the error it would have raised, with `limit` naming the bound; the message names the setting (`limits.attempts=4`). New export: `CallLimits`.
- **A fallback that shares its primary's fate is warned of.** A fallback on the same backend and `base_url` as its row fails with it when that server is down. `ModelRuntime.warnings` lists each as a `Problem` of kind `shared_fate`, the runtime logs it once when built, and `hardline check` prints it (`warnings` in `check --json`). New export: `Problem`.
- **A call's failed attempts, fallbacks and answer are told to an observer.** `build_runtime(observer=...)` takes a `CallObserver`, whose `observe(event)` hears `AttemptFailed`, `FellBack` and `Answered` as each happens. It replaces the `hardline` logger's `WARNING` on a fallback and `INFO` on a retry, which no handler of hardline's reached. New exports: `CallObserver`, `CallEvent`, `AttemptFailed`, `FellBack`, `Answered`.
- **OpenTelemetry, on the GenAI semantic conventions.** `build_runtime` wraps every backend so that each backend call is one `chat {model}` span (CLIENT, under the caller's span, so a retry or a fallback is a sibling) and records `gen_ai.client.inference.duration` and the `gen_ai.client.inference.usage.*` token counters; its default observer counts `hardline.retries` and `hardline.fallbacks`. hardline owns the inference counters for the calls it makes. No content is recorded, and there is no option to record it. The API only: nothing is recorded until the application installs an SDK. `build_runtime` takes `tracer_provider` and `meter_provider`. hardline now depends on `opentelemetry-api`.
- **A backend's author can check it against the installed hardline**: `backend_problems(backend)` reads the port's members (`structured_modes` a frozenset of known modes holding `prompt`, an async `complete`, and the optional `check`, `aclose` and `default_base_url` well formed), and `await answer_problems(backend, spec)` makes one call and requires a `RawCompletion` with a `finish_reason`. The requirements were a test in hardline's repository, which no wheel carries, and it checked only that the members existed. The port's docstring now states them.
- **Who upgrades first is stated**: a `Completion` refuses JSON keys it does not know, and a minor release may add keys, so a reader of stored `Completion` JSON upgrades hardline before any writer does (README, `Completion`).
- **A row names its proxy**: `proxy: http://proxy.internal:3128`. Nothing in the environment chooses one, so a user behind a proxy had no route; a proxy URL carrying credentials is refused. Errors may carry their own `fix`, as `HardlineError.fix`.
- **The `hardline` command exports its telemetry** when the standard `OTEL_*` variables ask, as `matrix` and `ix` do: it installs the OpenTelemetry SDK, with OTLP over http/protobuf (`OTEL_EXPORTER_OTLP_PROTOCOL=grpc` is refused with a warning naming what to set, and nothing is exported over it; owner ruling 2026-10-22: no gRPC) or console exporters, and `service.name` `hardline`. With none set it installs nothing. Before, `hardline complete` exported nothing whatever they said. New export: `configure_telemetry`, for another application's entry point. hardline now depends on `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http`; the library never imports them.
- **A row names its provider**: `provider: ollama`, as the GenAI conventions name providers, is the `chat` span's and the inference metrics' `gen_ai.provider.name`. An OpenAI-compatible backend cannot tell which server answered, so a row that names none is still told by its backend's name (`openai-compat`). Like every field, it is in the row's digest.
- **Tool calling.** A call may offer the model tools, `complete(..., tools=[ToolSpec(...)])`, and a `Completion` carries the calls the model asked for as `tool_calls` (each `id`, `name`, and `arguments` as the JSON text the model wrote). The caller runs them and sends each result back as a `tool` turn (`Message(role="tool", tool_call_id=..., content=..., is_error=...)`); an assistant turn carries its `tool_calls`. hardline runs no tool. Both protocols carry them: OpenAI's `tools`, `tool_calls` and `tool` role, and the Messages API's `tools`, `tool_use` and `tool_result` blocks. A reply that asks for tools is `answered` when the call offered them, and never otherwise, so a plain `complete` keeps its promise. `params.tools` names the tools offered, and the turns digest covers a turn's tool parts (a turn without them digests as before). A backend that can call tools declares `supports_tools = True`, as the built-ins do; a call that offers tools or sends tool turns to a row whose backend, or a fallback's, does not is a `ConfigError` before anything is sent, and `ModelRuntime.check_tools(name)` asks ahead. The mock's `script` takes a `{tool_calls: [...]}` entry. New exports: `ToolSpec`, `ToolCall`.
- `HardlineError` takes `fix` and `problems`; `ConfigError`s from the registry, the runtime's validation and the YAML source carry their problems. `load_registry` and `get_skill` join `hardline.__all__`, and the CLI reaches hardline through `__all__` alone.

### Fixed

- **`Usage.input_tokens` means one thing on every backend: every input token, the cache's included.** The `anthropic` backend reported the Messages API's `input_tokens`, which leaves out the tokens read from and written to the prompt cache, while `openai-compat`'s `prompt_tokens` counts them, so a call served mostly from the cache reported a small part of what it was sent. It now adds `cache_read_tokens` and `cache_write_tokens` in, as the GenAI semantic conventions ask of `gen_ai.usage.input_tokens`. The two cache counts are kept beside it.
- **`Completion.usage` is what it says.** It was documented as summing failed and retried calls, and summed only the answering call of each schema attempt; when an `extract`'s chain was exhausted, the usage of its earlier repairs was lost. It is now the sum over `attempt_log`, and the documentation says what that is: a failed call reports no usage, so it adds nothing.
- **A registry error names the tier that set the bad field**, where it named the last tier that touched the row.
- **A tier that cannot be read is a `ConfigError`.** `$HARDLINE_CONFIG` naming a directory passed discovery and raised `IsADirectoryError`; a tier that is not UTF-8 raised `UnicodeDecodeError`. Discovery now requires `$HARDLINE_CONFIG` to name a file, and a tier that cannot be read or decoded names itself in a `ConfigError`.
- **A runtime used from a second event loop no longer duplicates the request.** Each backend kept one provider client for the runtime's whole life, and that client's connections were bound to the first loop. The second `asyncio.run` therefore got "Connection error" after the provider had already received the request. With retries the request was sent again, and with `retries: 0` the caller was told the provider was down. Clients are now pooled within one loop and never shared across loops. A loop that ends without `aclose()` leaves its connections open, and hardline logs a warning that names the fix.
- **Everything the library raises on purpose is a `HardlineError`.** An empty message list, `max_tokens` of 0 or less, a negative or `nan` temperature, and a negative `schema_retries` were a bare `ValueError` or a pydantic `ValidationError`, and a negative temperature was sent to the provider. Each is now a `ConfigError` raised before anything is sent.
- **A `file:` secret that is not UTF-8 text is a `SecretError`**, naming the file and quoting none of it, where it raised `UnicodeDecodeError`, which is not a `HardlineError`.
- **A registry row may not name the same fallback twice.** `fallbacks: [b, b]` asked `b` again in full after it had just failed. It is refused at validation, naming the row and the repeated name.
- The mock backend's docstring said it echoed the last user message; it echoes the first, and keys `responses` on the last user turn, else the first. Its `request_id` is the row's call ordinal counted from 1, computed as such rather than read from the counter after the reply was picked.
- **A schema repair goes on from the row that answered.** Each `extract` attempt restarted the fallback chain at the requested row, so when a fallback had answered, the repair asked the primary that had just failed again, paid its retries again, and could mix two models' replies in one conversation.
- **An empty reply is repaired without an empty assistant turn.** `extract` echoed every failed reply back before its repair instruction, an empty one included, and the Messages API refuses an assistant turn with no text: the repair of a reply cut at `max_tokens` before any text came back a 400 (`bad_request`, exit 3) where a `SchemaError` was owed. An empty reply is now followed by a repair turn saying it was empty, and the `SchemaError` says `the reply was empty`. The `anthropic` backend also leaves out any assistant turn that has neither text nor tool calls, as a caller's own conversation can hold one, and a text of whitespace alone beside a tool call (a model that writes `"\n\n"` before it asks for a tool), which the API refuses as a text block with no text.
- **`extract` finds the answer after bracketed prose.** A reply such as `Answer [1] below: {...}` had `[1]` taken as the answer and the valid object discarded, which cost a repair turn. Every balanced value that parses as JSON is now tried against the schema in turn.

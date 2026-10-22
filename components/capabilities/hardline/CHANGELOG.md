# Changelog — hardline

## Unreleased

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

### Changed (breaking)

- **No provider SDK.** Both wire protocols are spoken directly over httpx2: `openai-compat` (chat completions) and `anthropic` (the Messages API). hardline depends on pydantic, pyyaml and httpx2 only; the `openai` and `anthropic` packages are no longer installed with it.
- **No `dev` extra.** hardline has no optional extras; the workspace's `dev` dependency group carries the test tools.
- **A row's location comes from its config only.** With no `base_url`, the SDKs read `OPENAI_BASE_URL` or `ANTHROPIC_BASE_URL`, and the OpenAI client sent `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` as headers, so a row's key could go to a host no config named. Nothing reads them now. The composition root writes each backend's default (`https://api.openai.com/v1`, `https://api.anthropic.com`) into a row that names none, so `hardline models` and every row record where they send. A backend that serves one provider declares `default_base_url`.
- **No proxy or certificate variable changes where a request goes or what it trusts**: clients ignore `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `SSL_CERT_FILE` and `SSL_CERT_DIR`. TLS is verified against the system trust store (truststore).
- **A keyless `openai-compat` row sends no `Authorization` header**, where the SDK needed a placeholder key.
- **A redirect is reported, not followed**, as a `BackendError` naming the `Location`: following it would send the row's key along.
- **A `base_url` must be an http:// or https:// URL**, checked when the runtime is composed.
- Provider errors carry the provider's own message (`error.message`), with the key scrubbed, and name the row's location; `api.openai.com` is no longer named for a row that does not send there.
- **hardline's own backends register through its own `hardline.backends` entry points**, like a third party's, and the hard-coded table is gone. `discover_backends()` no longer takes `include_entry_points`; it returns exactly the installed entry points.
- **A broken backend plugin fails only the rows that name it.** An entry point that cannot be loaded, does not produce a `ModelBackend`, or shares its name with another used to abort composition for every row, the first two with an exception that was not a `HardlineError`. Each is now loaded on its own and kept as a stand-in that names the fault: a row naming it fails composition with a `ConfigError`, and a warning is logged.
- **Backend options are checked when the runtime is composed.** A backend may define `check(spec)`, returning what is wrong with a row; the runtime calls it for every row. Each built-in backend reads its `options` through a model of its own and refuses any key it does not read, so `hardline check` fails a row that would fail its first call. A mock `script` naming an unknown error reason is refused there too.
- **A keyless `anthropic` row is a config problem.** It used to pass `hardline check` and then fail its call as `auth` ("the provider rejected the key", exit 6) when no provider had been asked. It now fails composition, naming `models.<name>.api_key` (exit 3).
- **Each discovered tier is named with how it was found**: `user <path>`, `cwd <path>`, `$HARDLINE_CONFIG <path>`, in errors, in `hardline check`, and in each row's record.
- **`backend_type_url` is gone**, and with it `hardline.v1/backend.<name>`. The README called them registry keys, but registry rows and the composition root key on a backend's entry-point name, and nothing read them.
- `merge_tiers` returns, per model, the tier that set each field (`{field: source}`), and `ModelRegistry.from_config` takes `origins` in that shape.

### Fixed

- **`Usage.input_tokens` means one thing on every backend: every input token, the cache's included.** The `anthropic` backend reported the Messages API's `input_tokens`, which leaves out the tokens read from and written to the prompt cache, while `openai-compat`'s `prompt_tokens` counts them, so a call served mostly from the cache reported a small part of what it was sent. It now adds `cache_read_tokens` and `cache_write_tokens` in, as the GenAI semantic conventions ask of `gen_ai.usage.input_tokens` (registry notes [42], [43] and [47]). The two cache counts are kept beside it.
- **`Completion.usage` is what it says.** It was documented as summing failed and retried calls, and summed only the answering call of each schema attempt; when an `extract`'s chain was exhausted, the usage of its earlier repairs was lost. It is now the sum over `attempt_log`, and the documentation says what that is: a failed call reports no usage, so it adds nothing. The two tests named for the summation, which passed on `> 0`, assert the exact sums.
- **A registry error names the tier that set the bad field**, where it named the last tier that touched the row.
- **A tier that cannot be read is a `ConfigError`.** `$HARDLINE_CONFIG` naming a directory passed discovery and raised `IsADirectoryError`; a tier that is not UTF-8 raised `UnicodeDecodeError`. Discovery now requires `$HARDLINE_CONFIG` to name a file, and a tier that cannot be read or decoded names itself in a `ConfigError`.
- **A runtime used from a second event loop no longer duplicates the request.** Each backend kept one provider client for the runtime's whole life, and that client's connections were bound to the first loop. The second `asyncio.run` therefore got "Connection error" after the provider had already received the request. With retries the request was sent again, and with `retries: 0` the caller was told the provider was down. Clients are now pooled within one loop and never shared across loops. A loop that ends without `aclose()` leaves its connections open, and hardline logs a warning that names the fix.
- **Everything the library raises on purpose is a `HardlineError`.** An empty message list, `max_tokens` of 0 or less, a negative or `nan` temperature, and a negative `schema_retries` were a bare `ValueError` or a pydantic `ValidationError`, and a negative temperature was sent to the provider. Each is now a `ConfigError` raised before anything is sent.
- **A registry row may not name the same fallback twice.** `fallbacks: [b, b]` asked `b` again in full after it had just failed. It is refused at validation, naming the row and the repeated name.
- The mock backend's docstring said it echoed the last user message; it echoes the first, and keys `responses` on the last user turn, else the first. Its `request_id` is the row's call ordinal counted from 1, computed as such rather than read from the counter after the reply was picked.
- **A schema repair goes on from the row that answered.** Each `extract` attempt restarted the fallback chain at the requested row, so when a fallback had answered, the repair asked the primary that had just failed again, paid its retries again, and could mix two models' replies in one conversation.
- **`extract` finds the answer after bracketed prose.** A reply such as `Answer [1] below: {...}` had `[1]` taken as the answer and the valid object discarded, which cost a repair turn. Every balanced value that parses as JSON is now tried against the schema in turn.

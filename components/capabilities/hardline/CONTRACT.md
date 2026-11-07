# hardline's contracts

What each published surface of hardline hides, what it consists of, its defaults, how it fails,
and how far it is promised. The [guide](docs/guide.md) shows how to use each; this file is what a
change to one of them must keep. `tests/test_hardline_contract.py` pins the surfaces marked
**pinned**.

hardline imports nothing of matrix and is usable on its own: as a library, and as the `hardline`
command (`uvx hardline`). A host that wraps it (matrix's model runtime, through its own adapter)
is a consumer of these surfaces like any other.

Stability is one of four words:

- **Versioned**: a document with a type URL. Within a major, keys are only added, each with a
  default. A removal or a rename moves the major, and the CHANGELOG says so under "Removed
  (breaking)".
- **Stable**: changes only by addition. Before 1.0, a break is a minor release whose CHANGELOG
  says so under "Removed (breaking)" or "Changed (breaking)".
- **Public**: may change in a minor release, said in the CHANGELOG.
- **Not contract**: may change without notice.

## The call: `ModelRuntime` and `build_runtime`

**Stability: public.**

**The decision it hides.** How one logical call to a named model is made: which wire protocol,
the retries of a transient failure with full-jitter backoff, the fallbacks once retries are
spent, the call's own limits, the contract check on what a backend returned, and, for
`extract`, schema validation with bounded repair from the row that answered. A caller names a
row and gets a reply; it never sees an attempt.

**Members.**

| Name | What |
|------|------|
| `build_runtime(config=None, *, sources, backends, secrets, label, sleep, rng, observer, tracer_provider, meter_provider)` | the composition root: tiers discovered from disk, or a host's mapping (`config`, labelled `label`); backends from the `hardline.backends` entry points |
| `complete(model, messages, *, system, max_tokens, temperature, tools)` | one call, text out; `model=None` is the registry default |
| `extract(model, messages, output, *, system, max_tokens, temperature)` | a validated `output` and its `Completion` (`Structured`) |
| `check_tools(model)` | raises unless every row the call may reach can call tools |
| `worst_case(model)` | `CallBound`: the most one call on the row can cost |
| `warnings` | registry problems that are legal but likely unmeant (`shared_fate`) |
| `secret_problems()` | rows whose key reference does not resolve now |
| `registry`, `aclose()`, `async with` | the rows; closing the running loop's connections |

The per-call parameters are `system`, `max_tokens`, `temperature` and `tools`, and no others
(ruling HO7). A setting hardline cannot carry is refused, never dropped: Python refuses an
unknown keyword, and the CLI an unknown flag.

**Defaults.** `max_tokens` and `temperature` come from the row when the call gives none; an
unset temperature sends none, and the record says `provider_default`. `tools` is empty.
`observer` is OpenTelemetry's counter of retries and fallbacks under `build_runtime`, and hears
nothing under `ModelRuntime` built by hand.

**Failure behaviour.** Everything raised on purpose is a `HardlineError`:

| Error | When | Retried or fallen back from |
|-------|------|------------------------------|
| `ConfigError` | a row its backend cannot serve, a broken backend plugin a row names, a bad call argument, tools offered to a row that cannot call them, a redirect | never: it is raised before anything is sent, or at once |
| `UnknownModelError` | a name not in the registry, or none and no default | never |
| `SecretError` | a key reference that does not resolve | never |
| `BackendError` | a provider call failed, with `reason`: `rate_limit`, `timeout`, `unavailable` (transient), `auth`, `bad_request`, `unknown` | transient ones are retried, then fallen back from; the rest are raised at once, since another try would hide a fault someone has to fix |
| `SchemaError` | `extract` found no valid `output` after the row's repairs | each repair is a logical call of its own |
| `ContractError` | a backend returned something other than a `RawCompletion` | never |

A `BackendError` or `SchemaError` raised by the runtime carries `attempt_log` and `usage`, what
the failed call cost, and `limit` when one of the row's `limits` stopped it. A reply that is not
a whole answer (cut, refused, withheld, empty) is returned, never raised. An observer that
raises is logged and cannot fail the call.

## The reply: `hardline.v2.completion`

**Stability: versioned. Pinned.**

**The decision it hides.** Nothing of hardline's own: it is the part of a call any model runtime
can state, so a host maps it without learning hardline.

**Members.** `type_url`, `name`, `family`, `model`, `provider` (the row's `provider`, else its
backend's entry-point name), `local`, `text`, `tool_calls`, `finish_reason`,
`native_finish_reason`, `refusal`, `usage`, `request_id`, `latency_ms`, `record`, and `answered`,
derived and written to JSON. `name`, `family`, `model` and `provider` are those of the row that
answered.

**Defaults.** None: a reply states every key.

**Failure behaviour.** `extra="forbid"`: JSON with a key the reader does not know is refused, so
a reader of stored replies upgrades hardline first. JSON whose `answered` contradicts its other
keys is refused. Another type URL is refused.

## The record: `hardline.v1.call`

**Stability: versioned. Pinned.**

**The decision it hides.** How hardline made the call: its retries, fallbacks and repairs, what
it sent, and the row as configured. It is the likeliest thing in hardline to change, so it grows
here and not in the reply.

**Members.** `type_url`, `backend`, `attempts` (schema attempts), `retries`, `fallback_from`,
`params` (`max_tokens`, `temperature`, `structured`, `tools`), `attempt_log` (each backend call:
`name`, `reason`, `usage`, `latency_ms`), `turns_digest`, and `row` (`digest`, and `rungs`: for
every field of the row but its name, where its value came from).

**Defaults.** `params.temperature` is `provider_default` when none was sent. A rung is
`default` for a field no tier set, and `default of backend <name>` for a location the backend
supplied.

**Failure behaviour.** A key the reader does not know is ignored, in the record and in each of
its parts (MRC:c35), so a record a newer 0.x wrote reads in an older one. Another major's
`type_url` is refused. A digest is `sha256:` over canonical JSON, recomputable without hardline
([the guide](docs/guide.md#what-a-call-returns)).

## The registry row

**Stability: stable. Pinned.**

**The decision it hides.** Which concrete model, endpoint, key and fallback chain answer a name.
A model is a row of configuration, not a class.

**Members.** `backend`, `model`, `family` (required), `provider`, `base_url`, `proxy`, `api_key`
(`env:` or `file:` only), `local`, `structured`, `max_tokens`, `temperature`, `timeout_s`,
`retries`, `schema_retries`, `fallbacks`, `limits` (`attempts`, `seconds`, `tokens`), `options`
(the backend's own). Tiers: `~/.hardline/config.yaml` < `./hardline.yaml` < `$HARDLINE_CONFIG`,
or a host's mapping.

**Defaults.** `structured: prompt`, `max_tokens: 1024`, `temperature` unset (the provider's),
`timeout_s: 120`, `retries: 2` (at most 10), `schema_retries: 1` (at most 10), no fallbacks,
`limits` unset (owner ruling 2026-10-22: `check` shows the worst case instead), `local: false`.
A row with no `base_url` gets its backend's `default_base_url`. Each effective value is in
`models --json`; where each came from is in `check --json` and every record (`rungs`).

**Defaults not yet shown.** The backoff (0.5 s base, 8 s cap, a provider's Retry-After honoured
to 60 s), the Anthropic `anthropic-version` header, and the instruction and repair texts of
`extract` are code constants, in no row, record or `check` output (review F-5, D5: after this
release).

**Failure behaviour.** A bad row fails composition as a `ConfigError` with one problem per bad
key, each naming its path and the tier that set it. A row is never repaired or guessed.

## The backend port: `ModelBackend` and its optional protocols

**Stability: public. Pinned.** Every implementation is hardline's own (`openai-compat`,
`anthropic`, `mock`), so the rule of three is not met and the port is not frozen.

**The decision it hides.** One wire protocol: paths, headers, body shape, the reply's shape and
its finish reasons. A backend makes one attempt; the runtime owns every policy.

**Members.** Required: `structured_modes`, a frozenset holding `prompt`, and async
`complete(endpoint, request, api_key) -> RawCompletion`. Optional, each a `runtime_checkable`
protocol read with `isinstance`, once, from the backend as loaded, before the composition root
wraps it for telemetry (the wrapper declares none of them):

| Protocol | Member | Without it |
|----------|--------|------------|
| `CheckedBackend` | `check(endpoint) -> Sequence[str]` | every row the backend's modes allow is accepted |
| `ClosableBackend` | async `aclose()` | nothing to close |
| `ToolCallingBackend` | `supports_tools`, read as `is True` | a call offering tools or sending tool turns to its rows is refused before anything is sent |
| `LocatedBackend` | `default_base_url`, an http(s) URL | a row names its own location, or has none |

A backend is handed an `Endpoint` (the row as one attempt needs it), a `Request`, and the
resolved key. A backend carries every part of a turn it is sent, or refuses the call: none is
dropped (ruling HO7). Where its protocol has no field for one, it carries it in-band, in one
documented encoding: `openai-compat` sends a failed tool's result (`Message.is_error`) in the
`tool` role with its content marked, `[tool error] <content>`, since that role has no error
flag; `anthropic` sends the Messages API's own `is_error`. It registers as a `hardline.backends` entry point; the name is what a row puts in
`backend:`. `backend_problems` and `answer_problems` check a backend against these, by the same
protocols.

**Defaults.** `RawCompletion`'s `finish_reason`, `native_finish_reason`, `refusal` default to
None and `tool_calls` to none, so a backend that sets none of them satisfies the types; its
replies are then never `answered`.

**Failure behaviour.** A backend raises `BackendError` with a reason, or `ConfigError` for a row
it cannot serve. An entry point that cannot be loaded, is not a `ModelBackend`, or shares its
name fails only the rows that name it.

## The driven ports: `SecretResolver`, `ConfigSource`, `CallObserver`

**Stability: public.**

| Port | Decision it hides | Members |
|------|-------------------|---------|
| `SecretResolver` | where a key lives | `resolve(reference) -> str`, raising `SecretError` |
| `ConfigSource` | where a tier is read from | `read() -> dict`, `describe() -> str` |
| `CallObserver` | what is done with a call's progress | `observe(event)`, over `AttemptFailed`, `FellBack`, `Answered`; a kind it does not know is ignored |

## The command: `hardline`

**Stability: stable** for the `--json` documents ([the guide](docs/guide.md#output-contract)), the exit codes and
the `hardline.v1.error` envelope, which the capability CLIs agree in `exit-codes.json` and
`error-envelope.json`. **Public** for the prose output.

**Members.** `models [--json]`, `check [--json]`, `complete MODEL PROMPT [--json] [--system]
[--max-tokens] [--temperature]`, `--skill`, `--version`. `complete --json` is the
`Completion`, record and `answered` included.

**Failure behaviour.** Exit 0 ok, 1 unknown, 2 usage, 3 config, 5 transient, 6 auth, 130
interrupted. Under `--json` every failure is one envelope line on stderr, and nothing else is.

## Telemetry

**Not contract**, owner ruling 2026-10-22: span and metric names and attribute keys may change
without notice. **Stable**: no content is recorded (no prompt, reply, refusal or error message),
and there is no option to record it.

The 0.2 design record (`docs/design/2026-10-22-record.md`) is history: where it names a
`Completion` field that moved to the record, this file and the CHANGELOG are current.

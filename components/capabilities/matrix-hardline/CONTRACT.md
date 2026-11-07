# matrix-hardline's contracts

What each published surface of matrix-hardline hides, what it consists of, its defaults, how it
fails, and how far it is promised. The [README](README.md) shows how to use it; this file is what a
change to one of them must keep. `tests/test_bridge.py` holds them.

Stability is one of three words. **Versioned**: a document with a type URL; within a major, keys are
only added, each with a default, and a removal or rename moves the major. **Public**: may change in a
minor release, said in the CHANGELOG. **Not contract**: may change without notice.

The package is the one place that knows both of the packages it bridges. It depends on `matrix` and
`hardline`, each bounded to the minor it is built against; neither depends on it, and hardline imports
nothing of matrix.

## The model runtime: `matrix-hardline.v1.model-runtime`

**Stability: public.** Registered at matrix's `model-runtime` point from this distribution's
`matrix.extensions` entry point, as any third party's model runtime is.

**The decision it hides.** How matrix's model-runtime port is met by hardline: how turns, tools and
overrides become one `complete` or `extract`, how hardline's reply, record and failures read in
matrix's vocabulary. A caller of the port learns nothing of hardline.

**Members.** It keeps matrix's `ModelRuntime` port (`check(model, *, uses)`, `call(request)`), and
the optional members `SupportsWarnings`, `SupportsResolved`, `SupportsAclose`.

| Member | What |
|---|---|
| `check(model, *, uses=frozenset())` | refuses, as a `ConfigError`: a name the registry does not resolve; `"tools"` on a row, or any fallback of it, whose backend cannot call them; `"tools"` and `"output"` together |
| `call(request)` | one `complete` (no `output`) or one `extract` (an `output` type), the request's turns sent as hardline's messages |
| `warnings` | hardline's registry warnings (`shared_fate`), as matrix `Problem`s at `models.<path>` when the registry is the config's |
| `resolved` | the registry it resolved, `{default, models}`, each row without its `api_key` |
| `aclose()` | closes the connections hardline opened in the running event loop |

**Config** (`HardlineModelRuntimeConfig`, `extra="forbid"`): `models`, `default_model`, `max_tokens`,
`temperature`.

**Defaults.** `models` unset: hardline discovers its own tiers. `default_model` unset: the registry's
default. `max_tokens` and `temperature` unset: the request's, else the row's (an unset temperature is
the provider's). Every value is in `matrix plan`'s settings, with where it came from.

**Failure behaviour.** A registry hardline refuses fails composition as a `ConfigError` with one
problem per bad key, at `models.<path>` for the config's registry, in its file for a tier's. A call
raises `ModelRuntimeError`, its `reason` from one mapping (`reason()`):

| hardline | reason |
|---|---|
| `BackendError` `rate_limit`, `timeout`, `unavailable`, `auth`, `bad_request` | `rate_limited`, `timeout`, `unavailable`, `auth`, `incapable` |
| `BackendError` `unknown` | `failed` |
| `SecretError` | `auth` |
| `UnknownModelError`, `ConfigError` | `incapable` |
| `SchemaError`, `ContractError`, anything else | `failed` |

A backend error keeps its `Retry-After` (`retry_after_s`). A request with tools and an output type is
refused at the call as `incapable`, before anything is sent, as `check` refuses it at binding. A reply
that is not a whole answer (cut, refused, withheld) is returned and said for what it is, as hardline
returns it.

## The reply: matrix's `ModelCall`

**Stability: public**, as matrix's `ModelCall` is.

Each neutral field is hardline's reply's of the same name: `text`, `tool_calls`, `finish_reason` (the
same six words, mapped one by one), `native_finish_reason`, `refusal`, `name`, `model`, `family`,
`provider` (the row's `provider`, else its backend's name), `usage`, `request_id`, `latency_ms`; and
`value`, the `output` validated, for an `extract`.

## Provenance: `hardline.v1.call` and `matrix-hardline.v1.failed-call`

**Stability: versioned.** Both schemas are registered at matrix's `payload-type` point, so matrix
validates each document against its schema when a session row is recorded, and `matrix describe`
prints it.

| Key | When | Document |
|---|---|---|
| `hardline.v1.call` | a call that replied | hardline's `CallRecord`, whole, as `model_dump(mode="json")`: `backend`, `attempts`, `retries`, `fallback_from`, `params`, `attempt_log`, `turns_digest`, `row`. hardline owns it: its fields and major are hardline's (hardline's CONTRACT.md, "The record") |
| `matrix-hardline.v1.failed-call` | a call that failed after it sent something | `FailedCall`: `attempts` (an `extract`'s schema attempts, else null), `attempt_log` (hardline's `Attempt`s), `limit` (the `limits` key that stopped it, else null). The call's usage is the `ModelCall`'s `usage` |

A key the reader does not know is ignored, in both documents (`extra="ignore"`). A failed call that
sent nothing (a refused name, a key that does not resolve) carries no document.

## The entry point and `register`

**Stability: public.** `matrix_hardline.runtime:register` registers the runtime and the two payload
schemas, from two entry points named for the points it registers at, `model-runtime` and
`payload-type`, so only a session that reads one of them imports it. Registering imports hardline's types (pydantic models; no HTTP client); building the runtime
reads hardline's registry; nothing calls a model until `call`.

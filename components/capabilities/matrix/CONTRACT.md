# matrix's contracts

What each published surface of matrix hides, what it consists of, its defaults, how it fails,
and how far it is promised. The [API reference](docs/reference/api.md) has every name in
full; this file is what a change to one of them must keep.

Stability is one of two words. **Stable**: changes only by addition; a break needs a major
release, or, before 1.0, a minor release whose changelog says so and that migrates every
consumer in this repository in the same change. **Public**: may change in a minor release,
said in the changelog, when matrix and its consumers in this repository change together.

## The component-author contract

**Stability: stable.** Pinned by `tests/test_contract.py`.

**The decision it hides.** How a run is executed, recorded and stopped: the executor, the
store, the scheduling, the order of work. A component declares what it requires and
provides and does its work; matrix decides when, keeps what it hands over, and stops it.

**Members.**

| Name | What |
|------|------|
| `Component` | a protocol: `requires` and `provides`, collections of kinds, and `async run(inputs, ctx)`, returning a mapping of kind to one row or a list of rows, or nothing |
| `Context` | a protocol: what `run` may ask of its run. `run_id`, `component`, `deadline`, `stopping`, `provide(kind, row, /, *, sessions=None)`, `defer(release)`, `emit(name, /, **fields)`, `measure(name, value, /, *, unit="", kind="gauge", **attrs)`, `span(name, /, **fields)`, `attributed()` |
| `Inputs` | the rows of each required kind, a read-only mapping of kind to a tuple of rows; `one(kind)` |
| `Attribution` | what `attributed()` gives: `sessions()`, the session rows the block made |
| kinds | `<namespace>.v<N>.<name>`, checked by `check_type_url`; each kind has exactly one provider, or is a run input |
| `Registry.register` | `(point, type_url, build, *, config=None, needs=(), summary="", stability="beta", stand_in=False, drives=(), capabilities=None)`; `capabilities` only for an agent runtime with a config |
| `Registry.register_payload` | `(type_url, model, summary="")`: the schema rows of a kind validate against |
| `matrix.extensions` | the entry-point group: an entry point's name is a point its extension registers at or declares, and its value the extension's `register(registry) -> None`; one line per point, all naming one `register`. A session loads only the extensions that declare a point it reads |
| `NEEDS` | the shared needs, each a `Need` with its type, all stable: `agents`, `model_runtimes`, `schemas`, `cwd`, `name`. `model_runtimes` is a `ModelRuntimes`, a read-only mapping of each model runtime by name with `type_url(name)` and `drives(name)`, what each was built as, which a session row made on it names; a host that hands it itself hands one. An agent runtime that declares `model_runtimes` makes its sessions' model calls on them |
| the stop protocol | `ctx.stopping` is set; the component has `grace_s` to return; then it is cancelled. Every `ctx.defer` release is called once at the end of the run, last first |
| `ContractError` | what breaking the contract raises (kind `contract`) |

`RunContext` is the executor's `Context`. Built by hand (`RunContext("run", "name")`), it is a
context outside any run for a component's unit test: it keeps nothing and refuses what a run
refuses. It is public, not part of the contract: annotate against `Context`.

**Defaults.** A row's ledger entry names every session the component ran that no row it
handed over has named yet; `provide(..., sessions=...)` names exactly those given. A
registration is `stability="beta"`, `stand_in=False`, with no needs. `measure` records a
`gauge` without a unit.

**Failure behaviour.**

- A row of a kind the component does not provide, a row its kind's schema refuses, or a
  session named in `provide` that the component did not run: `ContractError`, and the row is
  not recorded.
- An event, measure or span name that is not dotted lower-case, or is under `matrix.*`; a
  measure kind outside `gauge`, `counter`, `histogram`; a value that is not a number:
  `ContractError`, in a run and outside one.
- A store that cannot keep a handed-over row: `StoreError` from `provide`, and the run fails.
- After the component has ended, every context call is ignored, and the first is reported as
  `matrix.component.late`.
- A registration at an undeclared point, under an invalid type URL, or of a class that does
  not keep its point's port, or a registration or declaration at a point the extension's entry
  points do not name: `ConfigError`. A build that names a need nobody supplied:
  `ConfigError` naming it. An extension whose `register` raises is quarantined whole.
- A `compose(context=...)` key that names one of matrix's needs other than `cwd`:
  `ConfigError`, since composition supplies those.

## The ports and their optional members

**Stability: public.** Each port is a `runtime_checkable` Protocol in
`matrix.domain.ports._out` (the observer's in `matrix.domain.observer`), exported from
`matrix`. The registry holds what is registered at a point to its port's required members,
when it registers and when it is built.

| Port | Decision it hides | Required members |
|------|-------------------|------------------|
| `AgentRuntime` | which agent framework runs a session, and how | `check(definition)`, `async run(definition, task)` |
| `ModelRuntime` | who makes a model call (which provider, endpoint, retries, fallbacks) | `check(model, *, uses=frozenset())`, `async call(request)` |
| `ConstructStore`, `RunWriter` | where a run's record is kept, and in what form | `open`, `load`, `describe`, `runs`; `append`, `finish` |
| `Observer` | what is done with a run's events | `on_event(event)` |
| `DocumentSource` | where documents are read from, and their formats | `document`, `documents`, `beside` |

Each port module states its failure behaviour: an agent session ends in a response whose
`stop` says why, or an `AgentRuntimeError` with a `reason`; a model call ends in a `ModelCall`,
or a `ModelRuntimeError` with a `reason`, and a part of the request the runtime cannot carry
is refused (`incapable`), never dropped; a store's missing run is a
`NotFoundError` and anything it cannot keep a `StoreError`; an observer that raises is logged
and skipped, and cannot stall a run; a document that cannot be read is a `Document` with its
`problem`, a location that cannot be read at all a `ConfigError`.

**Optional members.** What only some implementations do is a protocol of its own, checked
with `isinstance` where it is read. The rule each meets: its absence has a stated meaning that
changes nothing another part computes. A fact whose absence would change a result is a
required member, a registration field or a port of its own.

| Protocol | Kept by | Default, without it |
|----------|---------|---------------------|
| `SupportsWarnings` | agent and model runtimes | no warnings shown |
| `SupportsResolved` | agent and model runtimes | the reuse fingerprint is the config and needs alone |
| `SupportsAclose` | agent and model runtimes | nothing to close |
| `SupportsEvents` | a store's writer | the run keeps no events |
| `SupportsLocation` | a store | located by the run id |
| `SupportsDamaged` | a store | nothing reported skipped |
| `SupportsAround` | an observer | it sees spans through the queue alone |
| `SupportsClose` | an observer | nothing to close |

**Facts about a type are declared when it registers**: `stand_in`, `drives`, and an agent
runtime's `capabilities`, a pure function of its validated config giving
`AgentCapabilities`, which `Entry.capabilities(config)` asks without building the runtime.
`AgentCapabilities` is the agent-runtime point's own extension; its skill fields are Claude
Code's mechanics, and a runtime without skills answers `False` or None. `capabilities` at
another point, or without a config, is a `ConfigError`.

**Nothing is read by name.** Who counts an agent session's tokens is composition's to know:
an agent runtime that declares the need `model_runtimes` makes its calls on them, and matrix
counts none of its sessions' tokens (the model runtime counts its calls).

## The model call: `ModelRequest`, `ModelCall`, `ModelRuntime`, `pydantic_ai_model`

**Stability: public.** Every implementor is in this repository (matrix's mock, the bridge's
hardline runtime, the tests' stand-ins), so the port is not frozen; it is the last cheap
moment to change it, and a change is said in the changelog with every implementor migrated.

**The decision it hides.** Who makes a model call, and how: the provider, the endpoint, the
retries and fallbacks, how a structure is asked for. A caller (a model step, an agent
framework, a judge) names a model and sends one call in full; it never learns which runtime
answered beyond what the reply says.

**Members.** `ModelRequest`: `turns` (`Turn`: `role`, `content`, `tool_calls`,
`tool_call_id`, `is_error`), `model`, `tools` (`ToolSpec`), `output`, `max_tokens`,
`temperature`; `from_prompt(...)`, and the derived `prompt` and `uses`. `ModelCall`: `text`,
`value`, `tool_calls`, `finish_reason` (matrix's vocabulary), `native_finish_reason`,
`refusal`, `name`, `model`, `family`, `provider`, `usage`, `request_id`, `latency_ms`,
`provenance`, `prompt`, `session`. `ModelRuntime`: `check(model, *, uses)`, `call(request)`.
`pydantic_ai_model(runtime, model=None, *, settings=None, runtime_url=None, drives=())`:
pydantic-ai's `Model` over any `ModelRuntime`.

**Defaults.** A request's `max_tokens` and `temperature` unset are the runtime's. `uses` empty
is a plain call. `provenance` empty is a runtime that knows nothing beyond the reply.

**Failure behaviour.** `check` raises `ConfigError` for a name the runtime cannot resolve, and
for a use it cannot honour on that model. A `provenance` key that is not a versioned type URL
is refused when the call is built; a document its registered schema refuses is a
`ContractError` when the session row is recorded. `pydantic_ai_model` refuses, as a
`ConfigError`, any pydantic-ai setting but `max_tokens` and `temperature`: when given to it,
else at the request that carries it, before anything is sent.

**The typed provenance slot.** What only one runtime knows of a call is a document keyed by a
type URL of its own (`hardline.v1.call`), never a column of `ModelCall` or `Session`: a new
fact a runtime records grows its own document, not matrix's record. The runtime's package
registers the document's schema (`register_payload`), and matrix validates against it.

**matrix privileges no model runtime** (owner ruling HO1, 2026-11-06). It depends on none,
ships only the `mock` stand-in, and names none in its code (`tests/test_architecture.py`). A
config that names a model runtime nobody registered is refused when it is composed, and the
fix says what to install.

## Conformance suites: `matrix.testing`

**Stability: public.**

**The decision it hides.** How each port's promises are checked: a third party runs the
suite for its port and learns whether it keeps it, without reading matrix.

**Members.** `AgentRuntimeConformance` (define `runtime()`), `ModelRuntimeConformance`
(`runtime()`; `model`, `prompt`, `unknown` say what it is asked), `ConstructStoreConformance`
(`store()`, empty), `ObserverConformance` (`observer()`), `DocumentSourceConformance`
(`source()`, `present()`, `absent()`), `ContextConformance` (`async within(check)`),
`ComponentConformance` (`component()`, and `inputs()` when it requires kinds). Each is a mixin
of synchronous tests: subclass it in a test class, and pytest collects them.

**Defaults.** An agent runtime's session is `AgentDefinition(name="conformance",
system_prompt="Answer in one word.")` given the task `"Say hello."`; a document source is read
as a folder with the suffixes `.yaml` and `.md`; a component is run with no inputs.

**Failure behaviour.** Each broken promise is an `AssertionError` saying what was promised,
raised explicitly, so it holds under `python -O`. matrix runs each suite against each of its
own implementations, and against every stand-in in its tests and ix's that keeps the port; a
stand-in that breaks a port on purpose, to test what matrix does with a broken one, is
outside it.

## The plan: `matrix.v1.plan`

**Stability: public.** The document `matrix plan --json` prints and `plan_config` returns.

**The decision it hides.** How composition resolves a run's settings from the config tiers,
the agent definitions and each extension's typed config. A reader (often an agent deciding
whether to run) sees every value the run will use, and where it came from, without reading
any of those.

**Members.** `tool`, `sources`, `inputs`, `stages`, `components`, `agents`,
`agent_runtimes`, `model_runtimes`, the call counts (`agent_calls_min`, `agent_calls_max`,
`model_calls_min`, `model_calls_max`), `store`, `settings` and `warnings`. Each of `settings`
is `{path, value, origin, file, note}`: the key path; the value as composition will use it;
`origin`, one of `config` (with the `file` that set it), `definition`, `extension` and
`matrix`; and a `note` where a value's meaning is not in the value (`deadline_s: null` is no
deadline).

**Defaults.** None of its own: a plan reports defaults, and makes none. Every limit is
listed, the run's and each component's, as are every option of the store, of each runtime and
observer, of each component's config, and each agent's `model`, `tools` and `max_turns`. The
run's `deadline_s` defaults to 3600, an hour for each component (ruling M-O7), and the plan
says so; written as null it is no deadline, on the run or on one component. A model step's
`runtime` and the pydantic-ai runtime's `model_runtime`, left unset, are the one model runtime
the config composes (ruling M-RT): the plan gives its name, origin `matrix`, note `the only
model runtime composed`.

**Failure behaviour.** A config that does not compose or wire is the error envelope with
every problem, and no plan; a `limits.components` name that is no component is one of them,
and so is a model step's `runtime` or a pydantic-ai runtime's `model_runtime` left unset when
the config composes no model runtime, or several (`missing_key`, listing them).
Nothing is run or called. Each component's limits in `settings` are those the executor runs
it under (`tests/test_plan_and_run.py` rebuilds them from the plan and compares).

**What it cannot say.** The model an agent's runtime picks when the agent names none: the
plan reports the agent's `model` as unset, left to the runtime, and the runtime's own options
(its `default_model`, where it has one) with their sources. A model runtime's registry
default is its own to resolve; what it warns of (a fallback sharing its primary's fate) is in
`warnings`.

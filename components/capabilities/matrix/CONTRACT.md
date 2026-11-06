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
| `matrix.extensions` | the entry-point group; each entry is `register(registry) -> None` |
| `NEEDS` | the shared needs, each a `Need` with its type: `agents`, `model_runtimes`, `schemas`, `cwd`, `name` stable; `models`, `model_runtime_entries` beta |
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
  not keep its point's port: `ConfigError`. A build that names a need nobody supplied:
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
| `ConstructStore`, `RunWriter` | where a run's record is kept, and in what form | `open`, `load`, `describe`, `runs`; `append`, `finish` |
| `Observer` | what is done with a run's events | `on_event(event)` |
| `DocumentSource` | where documents are read from, and their formats | `document`, `documents`, `beside` |

Each port module states its failure behaviour: an agent session ends in a response whose
`stop` says why, or an `AgentRuntimeError` with a `reason`; a store's missing run is a
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

**Read by name.** Two members are not declared protocols: an agent runtime's
`usage_counted_by` (who counts its model calls' tokens) and a model runtime's
`pydantic_ai_model`. Both belong to the seam between agent runtimes and model runtimes, which
this release does not change; the `ModelRuntime` port has no conformance suite for the same
reason.

## Conformance suites: `matrix.testing`

**Stability: public.**

**The decision it hides.** How each port's promises are checked: a third party runs the
suite for its port and learns whether it keeps it, without reading matrix.

**Members.** `AgentRuntimeConformance` (define `runtime()`), `ConstructStoreConformance`
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

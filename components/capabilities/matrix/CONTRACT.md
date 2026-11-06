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
| `Registry.register` | `(point, type_url, build, *, config=None, needs=(), summary="", stability="beta", stand_in=False, drives=())` |
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

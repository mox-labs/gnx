# matrix

matrix runs components wired by the kind of data they exchange, and composes agents from
config. A component declares the kinds it requires and provides; matrix infers the wiring,
checks it, runs each component when what it requires is ready, and records every row in a
Construct as it lands. Agents are definitions bound to runtimes, and run alone or as
components. `matrix plan` and `matrix run` run what a config declares; `matrix runs` lists
what was recorded.

Start with the README: working components, a recorded run and a working agent.

---

## Explanation

| Document | What it covers |
|----------|----------------|
| [What is matrix?](explanation/what-is-matrix.md) | Components, kinds, inferred wiring and runs; agents as definition plus runtime; the registry; observers and telemetry; shared config; the one loader |
| [The data model](explanation/data-model.md) | Construct, ledger and Run, and how a run is kept as typed JSONL tables while it runs |

## Reference

| Document | What it covers |
|----------|----------------|
| [API reference](reference/api.md) | Every public name and signature, config keys, error kinds, exit codes, and the `matrix` command |
| [API reference: loading](reference/api.md#loading-documents) | `load`, `load_each`, the loader and its problem records |

## Design

| Document | What it covers |
|----------|----------------|
| [Design record, 2026-10-19](design/2026-10-19-record.md) | The judgments behind 0.6.0: the one loader, the store as a registry point, telemetry on the standard variables, plan and run, and what an agent's first drive from the skill changed (spend, `--only`, the record); each with its test and evidence |

# matrix

matrix runs components wired by the kind of data they exchange, and composes agents from
config. A component declares the kinds it requires and provides; matrix infers the wiring,
checks it, runs each component when what it requires is ready, and records every row in a
Construct as it lands. Agents are definitions bound to runtimes, and run alone or as
components.

Start with the [README](../README.md) for working components and a working agent.

---

## Explanation

| Document | What it covers |
|----------|----------------|
| [What is matrix?](explanation/what-is-matrix.md) | Components, kinds, inferred wiring and runs; agents as definition plus runtime; the registry; observers; shared config |
| [The data model](explanation/data-model.md) | Construct, ledger and Run, and how a run is kept as typed JSONL tables while it runs |

## Reference

| Document | What it covers |
|----------|----------------|
| [API reference](reference/api.md) | Every public name and signature, config keys, error kinds, exit codes, and the `matrix` command |

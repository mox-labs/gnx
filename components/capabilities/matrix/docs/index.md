# matrix

matrix runs components wired by the kinds of data they exchange, and records every row as the
run goes. It also composes agents and model calls from config, so they run as components too.
Start with the [README](../README.md): install, terms, a first run and a first config.

---

## Explanation

| Document | What it covers |
|----------|----------------|
| [Overview](explanation/overview.md) | What matrix is for, how it works in five terms, a run you can try offline, what it gives an agent, what it does not do |
| [How matrix works](explanation/how-matrix-works.md) | Components, kinds, inferred wiring and runs; agents as definition plus runtime; model calls on their own port; the registry; observers and telemetry; shared config; the one loader |
| [The data model](explanation/data-model.md) | Construct, ledger and Run, and how a run is kept as typed JSONL tables while it runs |

## Guide

| Document | What it covers |
|----------|----------------|
| [Using matrix](guide.md) | Your own component in a config, the record, stopping, observers, agents, model calls (mock, ollama, hardline), running a config, the loader, the `matrix` command |

## Reference

| Document | What it covers |
|----------|----------------|
| [API reference](reference/api.md) | Every public name and signature, config keys, error kinds, exit codes, and the `matrix` command |
| [API reference: loading](reference/api.md#loading-documents) | `load`, `load_each`, the loader and its problem records |
| [CONTRACT.md](../CONTRACT.md) | What each surface promises: the component-author contract, stable; the rest public |

## Design

| Document | What it covers |
|----------|----------------|
| [Design record, 2026-10-19](design/2026-10-19-record.md) | The judgments behind 0.6.0 and 0.7.0, with matrix's claims: the one loader, the store as a registry point, telemetry on the standard variables, plan and run, the agent and model runtime ports, and what an agent's first drive from the skill changed (`--only`, the record); each with its test and evidence |
| [Design record, 2026-11-06](design/2026-11-06-record.md) | The judgments behind 0.10.0: the model-runtime seam, the bridge that carries hardline across it, extension discovery, and a deadline that terminates |

# matrix

matrix runs flows of typed components and composes agents from config. Components declare
named, typed ports; a flow binds them to topics; matrix checks the wiring, runs the members in
dependency order, and records every value in a Construct. Agents are definitions bound to
runtimes, and run alone or as flow members.

Start with the [README](../README.md) for a working flow and a working agent.

---

## Explanation

| Document | What it covers |
|----------|----------------|
| [What is matrix?](explanation/what-is-matrix.md) | Components, ports, topics, flows and runs; agents as definition plus runtime; the registry; observers; shared config |
| [The data model](explanation/data-model.md) | Artifact, Construct and Run, and how a run is saved as typed JSONL tables |

## Reference

| Document | What it covers |
|----------|----------------|
| [API reference](reference/api.md) | Every public name and signature, config keys, error kinds, exit codes, and the `matrix` command |

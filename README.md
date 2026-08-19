# gnx

A marketplace of composable components for agents.

Eleven plugins are installable today, cut from 76 components in this repository.

```
/plugin marketplace add mox-labs/gnx
/plugin install guild-arch@gnx
```

Browse the catalog: **[mox-labs.github.io/gnx/catalog](https://mox-labs.github.io/gnx/catalog)**

## What's here

| | | |
|---|---|---|
| **Skills** | 35 | a practice, read into context when its trigger matches |
| **Agents** | 36 | a named reasoning role with a distinct method |
| **Capabilities** | 5 | runnable Python packages — `matrix`, `ix`, `recon`, `dao`, `gnx` |
| **Plugins** | 11 | the installable bundles those components are cut into |

A plugin is a bundle; a component is the unit. One component can ship in several plugins,
because bundling is a projection decision made in `components/bundles.yaml` — separate from
authoring. `plugins/` is generated output: committed, never hand-edited, and checked by
`just projection`.

## Where this is

**Generation 0 — the composition layer — is upcoming.** What exists now is the catalog those
compositions will draw from, plus the four capabilities that make it work.

Manifests are deliberately thin: 3 of 76 components carry one. Manifest v1's shape is being
settled in [slick](https://github.com/mox-labs/slick), and minting 76 manifests against a
moving spec would mean rewriting 76. The three that exist are the pilot that tests the
grammar. Until the rest follow, a component is installable but not yet composable — see
`docs/content/reference/0-status.md` for what is claimed versus what is built.

## The capabilities

| Package | Does | Docs |
|---|---|---|
| `matrix` | composes agent runtimes and runs component DAGs | [SECURITY](components/capabilities/matrix/SECURITY.md) |
| `ix` | runs experiments — evals, benchmarks, QoS | [SECURITY](components/capabilities/ix/SECURITY.md) |
| `recon` | heterogeneous sources in, structured JSONL out | [SECURITY](components/capabilities/recon/SECURITY.md) |
| `dao` | stands up and audits a project's agent organization | [SECURITY](components/capabilities/dao/SECURITY.md) |

All five are `mypy --strict` clean with 407 tests. Each `SECURITY.md` names the package's
trust boundaries and the findings already fixed — `matrix` and `ix` both execute things, and
the boundary that isn't written down is the one nobody reviews.

## Stack placement

| Layer | Repo | Role |
|---|---|---|
| Grammar | [slick](https://github.com/mox-labs/slick) | what a component looks like — `Manifest`, `TypedStruct`, `TypedRegistry` |
| **Catalog** | **gnx** *(this repo)* | the components themselves, and the marketplace that ships them |
| Execution | [geist.sh](https://github.com/mox-labs/geist.sh) | governed runtime that hosts components and intercepts tool calls |

Components are platform-agnostic at the architecture level; Claude Code is the current
distribution surface, not the only possible one.

## Working here

```
just              # list every gate
just check        # what a commit must pass (the pre-commit hook calls this)
just ci           # the full gate, mirrored by .github/workflows/ci.yml
just evals        # run the lab's experiments in mock mode
```

One `justfile` is the single source of truth for gates, so a local check and the CI check
cannot drift. See [CONTRIBUTING.md](CONTRIBUTING.md), and `lab/README.md` for how the
experiments distinguish "the harness works" from "the catalog works".

## License

[MIT](LICENSE) — Copyright (c) 2025 Mox Labs.

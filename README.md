# gnx

A marketplace of composable components for agents.

**Under construction.** gnx is pre-generation-0.

Three plugins are installable today, cut from 7 components.

```
/plugin marketplace add mox-labs/gnx
/plugin install rational-inquiry@gnx
```

Browse the catalog: **[mox-labs.github.io/gnx/catalog](https://mox-labs.github.io/gnx/catalog)**

## What's here

| | | |
|---|---|---|
| **Skills** | 2 | `intent-hardening`, `rational-inquiry` |
| **Capabilities** | 5 | Python packages — `matrix`, `ix`, `recon`, `dao`, `gnx` |
| **Plugins** | 3 | `intent-hardening`, `rational-inquiry`, `recon` |

**Two distribution channels, not one.** Skills and agents install as Claude Code plugins.
Capabilities are Python packages and install with `uv`/`pip` — they are not plugins and are
not reached through the marketplace. The `recon` plugin sits across the seam: it ships the
skill, while `recon` the CLI is the package, which is why that skill opens by saying so.

Neither is published to PyPI: `matrix`, `ix`, `recon` and `dao` are all taken there by
unrelated projects, so a capability installs from this repository today.

A plugin is a bundle; a component is the unit. Bundling is a projection decision made in
`components/bundles.yaml`, separate from authoring. `plugins/` is generated output:
committed, never hand-edited, and checked by `just projection`.

### The catalog is small on purpose

**69 components moved to `incubator/` on 2026-08-19** — 33 skills and 36 agents, intact and
versioned, out of the catalog and not projected.

The reason is evidence. A live routing measurement (`lab/catalog-routing`, 24 real agent
sessions) found the `dao` skill failing to activate on trigger phrases quoted verbatim in
its own description, and 49 of the 76 descriptions ran past the length at which a
description still reads as one. Shipping all of them would have asserted a quality nobody
had checked. Graduating a component back is a directory move plus a `bundles.yaml` entry.

## Where this is

**Generation 0 — the composition layer — is upcoming.** What exists now is the catalog those
compositions will draw from, plus the capabilities that make it work.

Components install and run; they do not yet compose. The declared-identity layer that a
composer would resolve is deferred until its shape settles in
[slick](https://github.com/mox-labs/slick) — see `docs/content/reference/0-status.md` for
what is claimed versus what is built.

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

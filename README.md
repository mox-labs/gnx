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

| | |
|---|---|
| **Skills** and **agents** | install as Claude Code plugins through the marketplace |
| **Capabilities** | configuration-driven Python packages, installed with `uv` from this repository |
| **Plugins** | bundles of components, declared in `components/bundles.yaml` |

[The catalog](https://mox-labs.github.io/gnx/catalog) lists what exists, read from source —
this README does not repeat counts that would go stale.

A plugin is a bundle; a component is the unit. Bundling is a projection decision made in
`components/bundles.yaml`, separate from authoring. `plugins/` is generated output:
committed, never hand-edited, and checked by `just projection`.

### The catalog holds what has been evaluated

A component enters the catalog when a measurement says it works — a routing evaluation for a
skill, its tests and standalone install for a capability. Components not yet evaluated live in
`incubator/`, intact and versioned, and are not projected. Graduating one is a directory move
plus a `bundles.yaml` entry.

## The capabilities

| Package | Does | Docs |
|---|---|---|
| `hardline` | one port for every model family — local MLX, ollama, Gemini, Claude — tagged with the family that answered | [README](components/capabilities/hardline/README.md) · [SECURITY](components/capabilities/hardline/SECURITY.md) |
| `matrix` | runs component DAGs with enforced reads, and composes agents from a definition plus a runtime | [README](components/capabilities/matrix/README.md) · [SECURITY](components/capabilities/matrix/SECURITY.md) |
| `ix` | runs experiments — evals, benchmarks, QoS — on a native engine or Inspect AI | [README](components/capabilities/ix/README.md) · [SECURITY](components/capabilities/ix/SECURITY.md) |
| `recon` | heterogeneous sources in, structured JSONL out | [SECURITY](components/capabilities/recon/SECURITY.md) |
| `dao` | stands up and audits a project's agent organization | [SECURITY](components/capabilities/dao/SECURITY.md) |

Each is `mypy --strict` clean, tested, and installable on its own from only its declared
dependencies — `just ci` enforces all three. Each `SECURITY.md` names the package's trust
boundaries and the findings already fixed — `matrix` and `ix` both execute things, and the
boundary that isn't written down is the one nobody reviews.

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
just evals        # run the lab's experiments offline, on both engines
```

One `justfile` is the single source of truth for gates, so a local check and the CI check
cannot drift. See [CONTRIBUTING.md](CONTRIBUTING.md), and `lab/README.md` for how the
experiments distinguish "the harness works" from "the catalog works".

## License

[MIT](LICENSE) — Copyright (c) 2025 Mox Labs.

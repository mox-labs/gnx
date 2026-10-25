# gnx

A marketplace of composable components for agents.

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
| `matrix` | runs components wired by the kinds of data they exchange, records every row as a run goes, and composes agents from a definition plus a runtime | [README](components/capabilities/matrix/README.md) · [SECURITY](components/capabilities/matrix/SECURITY.md) |
| `ix` | decides a question about an AI agent or a service by experiment — an agent evaluation, a service comparison — written as one file with a genre: validated, planned, registered before it runs, and reported as a verdict | [README](components/capabilities/ix/README.md) · [SECURITY](components/capabilities/ix/SECURITY.md) |
| `recon` | heterogeneous sources in, structured JSONL out, queried with SQL | [README](components/capabilities/recon/README.md) · [SECURITY](components/capabilities/recon/SECURITY.md) |
| `dao` | stands up and audits a project's agent organization | [README](components/capabilities/dao/README.md) · [SECURITY](components/capabilities/dao/SECURITY.md) |

Each is `mypy --strict` clean, tested, and installable from this repository from only its
declared dependencies — `just ci` enforces all three. Each `SECURITY.md` names the
package's trust boundaries and the findings already fixed — `matrix` and `ix` execute
things, and the boundary that isn't written down is the one nobody reviews.

A capability is installed from this repository at its release's tag; none is on a package
index, where the names `matrix` and `ix` belong to other projects:

```
uv tool install "git+https://github.com/mox-labs/gnx@matrix-v0.8.0#subdirectory=components/capabilities/matrix"
uv tool install "git+https://github.com/mox-labs/gnx@ix-v0.7.0#subdirectory=components/capabilities/ix"
uv tool install "git+https://github.com/mox-labs/gnx@recon-v0.15.0#subdirectory=components/capabilities/recon"
uv tool install "git+https://github.com/mox-labs/gnx@hardline-v0.2.0#subdirectory=components/capabilities/hardline"
```

`ix` depends on `matrix`, and `matrix` on `hardline`; installed from the git URL, uv takes each from the same repository.
Each package's README has its library install line. No package has extras: each brings every runtime and telemetry it uses.

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
just lab          # validate and plan the lab's experiments (nothing is run)
```

One `justfile` is the single source of truth for gates, so a local check and the CI check
cannot drift. See [CONTRIBUTING.md](CONTRIBUTING.md), and `lab/README.md` for how the
experiments state the catalog's claims as measurements.

## License

[MIT](LICENSE) — Copyright (c) 2025 Mox Labs.

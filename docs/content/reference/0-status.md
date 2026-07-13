---
title: What's real vs planned
section: reference
mode: reference
status: shipped
register: public
fidelity: tarmac
---

# What's real vs planned

**shipped** runs today. **planned** is designed; build starting. **proposed** is a settled or near-settled design decision with no build behind it yet. No page on this site describes a planned capability as if it shipped.

---

## Shipped — runs today

| What | Detail |
|------|--------|
| **slick** grammar (v0.2.0) | The five-field `Manifest` type — `type_url`, `source`, `requires`, `provides`, `relations`. Ships as the `slickit` Rust crate, behind the `manifest` feature. In-memory JSON; Apache-2.0. |
| **Two plugins** | `intent-hardening` and `rational-inquiry`, projected as Claude Code plugins and installable today via `extraKnownMarketplaces` + `enabledPlugins`. |
| **The directory marketplace** | The catalog's `.claude-plugin/marketplace.json` at the repo root; the one runnable install path. |

The slick crate ships the manifest *type*; it does not yet enforce much beyond structure. A real validator (`gnx validate` / `slick validate`) is designed. A Python binding for `slickit` is in progress — not yet shipped.

### The components on disk

| Component | Kind | `maturity` | State |
|-----------|------|-----------|-------|
| `intent-hardening` | Skill | `shipped` | Projected as a plugin; installs today |
| `rational-inquiry` | Skill | `shipped` | Projected as a plugin; installs today |
| `radix` | Processor | `design` | Design-stage; not projected — the `maturity` field keeps it honest about that |

### Installed plugin versions

| Plugin | Version |
|--------|---------|
| `intent-hardening` | 0.1.0 |
| `rational-inquiry` | 0.1.0 |

These are the `plugin.json` versions — the cache key Claude Code resolves on. This table is the canonical version record; other pages link here rather than repeating it. The wider set — craft-rhetoric, ci-scaffolds, guild-arch, and the rest — is being ported into the catalog as components.

---

## Planned — designed, build starting

| What | What it will do |
|------|-----------------|
| gnx CLI — `gnx init` | SDK interview → scaffold `claude/ + dao/ + .gnx/` → install a curated set |
| gnx CLI — `gnx component init` | Scaffold a new component (manifest + skill + tests), kind-aware |
| gnx CLI — `gnx add` / `rm` / `update` | Install / remove / update components into a project or user scope |
| gnx CLI — `gnx search` / `inspect` | Agent-facing discovery over the `provides` surface |
| gnx CLI — `gnx validate` | The strict registration gate (manifest, shape rule, ports, kind rules, namespace) |
| gnx CLI — `gnx build` | Project target surfaces; generate plugin dirs + the marketplace entry from one source |
| gnx **registry** | Validation-at-registration, the `provides` index, accreditation |
| **Projection pipeline** | The tool that generates installable plugins from the catalog; the two shipped plugins are its committed output, but the tool that regenerates them is being rebuilt |
| Component **authoring loop** | `component init` → `validate` → `build` → registration |
| Ported component set | craft-rhetoric, ci-scaffolds, guild-arch, and the rest, re-decomposed into catalog components |
| Eval **harness** | Probe, subject, trial, and sensor as composable components with declared ports |
| `api/` **schemas** | Typed config and payload schemas keyed by `type_url`; nothing populates `api/` yet — every config is opaque today |

---

## Proposed — settled design, no build behind it

| What | Where it is decided |
|------|---------------------|
| **Identity grammar** | Dotted, version-medial `type_url`, kebab-case resources, catalog-minted namespaces, `kind` never in a segment — ruled; enforcement designed |
| **The shape rule** | A `provides` entry that parses as a `type_url` is a port; a dotted-lowercase phrase is a discovery tag — ratified; enforcement designed |
| **Flow composition** | A `members` list of `(type_url, config)` pairs with derived topology — proposed |
| **The on-disk overlay** | `kind` and `maturity` (on disk today) plus a proposed `version` field, with a projection gate — proposed |

These are open design decisions tracked in the public GEP register.

---

## See also

- **[What gnx is](/docs/what-is-gnx)** — the shipped/designed line, in prose.
- **[Install a plugin](/docs/install-a-plugin)** — install the plugins.
- **[gnx CLI reference](/docs/cli-reference)** — the designed command surface in full.

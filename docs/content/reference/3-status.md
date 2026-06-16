---
title: What's real vs planned
section: reference
mode: reference
status: shipped
register: public
fidelity: tarmac
---

# What's real vs planned

The maturity lookup for the whole catalog. **shipped** runs today; **planned** is designed, build starting; **proposed** has no build behind it. No page on this site commands a planned capability as if it ran.

---

## Shipped — runs today

| Capability | Detail |
|------------|--------|
| **slick** grammar (v0.2.0) | The 5-field Manifest, TypedStruct, TypedRegistry. Ships as the `slickit` crate (Rust) and Python package — the types every component is written against. |
| **cix plugin family** | Installs into Claude Code today via `extraKnownMarketplaces`. See the versions below. |

### The cix plugins

| Plugin | Version | Gives you |
|--------|---------|-----------|
| `craft-rhetoric` | 0.3.0 | Comprehension, rhetoric, explanation |
| `ci-scaffolds` | 0.6.0 | Collaboration scaffolds; claim verification |
| `guild-arch` | 0.2.0 | Architectural reasoning; design review |
| `antifragile` | 0.1.0 | ACES boundary review |
| `craft-extensions` | 0.1.0 | Lexicon / concept growth |
| `craft-evals` | 0.3.0 | Evaluation suites, rubrics |
| `craft-research` | 0.3.0 | Research synthesis |
| `recon` | 0.7.0 | Reconnaissance over a codebase |

Versions are the installed (`plugin.json`) versions — the cache key Claude Code resolves on.

---

## Planned — designed, build starting

| Capability | What it will do |
|------------|-----------------|
| gnx CLI — `gnx init` | Genesis: SDK interview → scaffold `claude + dao + .gnx/` → install a curated set |
| gnx CLI — `gnx component init` | Scaffold a new component (manifest + skill + tests), kind-aware |
| gnx CLI — `gnx add` / `rm` / `update` | Install/remove/update components into a project or user scope |
| gnx CLI — `gnx search` / `inspect` | Agent-facing discovery over the `provides` surface |
| gnx CLI — `gnx validate` | Strict registration gate (manifest, ports, kind rules, namespace) |
| gnx CLI — `gnx build` | Project Target surfaces; generate plugin dirs + `marketplace.json` from one source |
| gnx **registry** | Validation-at-registration, the `provides` index, accreditation |
| gnx **marketplace** projection | The catalog generated into installable Claude Code plugins (slick's own plugin face included) |
| Component **authoring loop** | `component init` → `validate` → `build` → registration |

---

## See also

- **[What gnx is](/docs/what-is-gnx)** — the shipped/designed line, in prose.
- **[Install a plugin](/docs/install-a-plugin)** — install the cix family.
- **[gnx CLI reference](/docs/cli-reference)** — the planned command surface in full.

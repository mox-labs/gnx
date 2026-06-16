---
title: gnx
section: start
mode: explanation
status: planned
register: public
fidelity: tarmac
---

# gnx

**gnx is where your agent gets its capabilities** — reusable, composable cognitive extensions for an agent to find, install, and assemble, instead of rebuilding the same ones every project.

**Designed for any agent. Claude Code today.**

The shape of it in one line: **your agent composes capability from the catalog, and you govern what it composes from.** Capability stops being trapped in the project that first built it; your tooling stops being welded to one runtime; and what your agent assembles is drawn from a catalog a person curates, not one an agent quietly rewrites.

## cix and slick ship today; gnx is designed

The cix plugin family — craft-rhetoric, ci-scaffolds, guild-arch, and more — installs into Claude Code now, as the catalog's first cognitive extensions. **slick** (v0.2.0), the grammar every component is written in, ships too — as the `slickit` crate (Rust) and Python package, the types underneath everything. Both are usable today.

The `gnx` CLI, the registry, and the marketplace projection — including slick's own plugin face — are designed, build starting. The line between shipped and designed is load-bearing, and marked on every page.

## One catalog, three ways in

A **registry** declares, validates, and finds components; a **marketplace** projects them into installable Claude Code plugins; a **CLI** (`gnx`) sets up projects and scaffolds new components. The grammar underneath is slick — its five-field manifest ships today (v0.2.0), and the four kinds (Capability, Agent, Skill, Flow) are its established vocabulary. The three functions above it are designed.

## Where to go

- **[What gnx is](/docs/what-is-gnx)** — the affordance in depth: what you can do with the catalog, what it replaces, where the shipped/designed line sits.
- **[How components work](/docs/how-components-work)** — the grammar an agent reads to compose: the four kinds, the manifest, what a component declares about itself.
- **[Authoring a component](/docs/author-a-component)** — the designed loop for building and registering your own.
- **[gnx CLI reference](/docs/cli-reference)** — the full designed command surface, command by command.

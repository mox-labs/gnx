---
title: What gnx is
section: start
mode: explanation
status: planned
register: public
fidelity: tarmac
---

# What gnx is

**gnx is a catalog your agent builds from.** Every project rebuilds the same things — the same skills, the same review panels, the same scaffolds and guardrails. gnx makes them reusable: a library of **cognitive extensions** that an agent finds and composes, instead of rebuilding from scratch each time.

The hard part was never storing capability. It is letting an agent **assemble capabilities it has never run** — from what each one declares about itself, before any of them execute. That is the one thing gnx is built to do: make a capability legible enough to compose without running it first.

Two roles, kept separate: **your agent composes capability from the catalog; you govern what goes into it.** The catalog grows through agents — and stays trustworthy because a person, not an agent, mints what it is allowed to compose from.

## What you can do with it

- **Set up a project.** `gnx init` stands up a Claude Code project with a curated set of agents, skills, and scaffolds — chosen in conversation, not picked off a fixed menu. *(planned)*
- **Compose capabilities.** An agent searches the catalog for what it needs and assembles a working set from declared surfaces — no trial-and-error execution. *(planned)*
- **Author and register your own.** Build a capability, register it, and it becomes discoverable and composable — by any developer, by any agent. *(planned)*
- **Install what exists today.** The cix plugin family — craft-rhetoric, ci-scaffolds, guild-arch, and more — installs now as Claude Code plugins. These are the first cognitive extensions in the catalog. *(shipped)*

## Why it matters

You stop paying for two things.

**Rebuilding.** Capability stops being trapped in one project. Distill an agent skill, a review discipline, a scaffold once — then compose it into any project that declares it needs it. The catalog is how distilled work travels.

**Lock-in.** gnx is vendor-neutral by structure, not by good intentions. A capability is described in a way no single agent runtime owns. Claude Code is the Target that ships today; a second runtime is a new adapter, not a rewrite. Your tooling is not welded to an SDK that gets deprecated next quarter.

## How it's organized

One catalog, three ways in:

- the **registry** — where a component is declared, validated, and found;
- the **marketplace** — the catalog projected into installable Claude Code plugins;
- the **CLI** (`gnx`) — the tool that sets up projects and scaffolds new components.

The grammar each component is written in — **slick** — ships today (v0.2.0). The registry, marketplace, and CLI are designed; build is starting.

| What | Status |
|---|---|
| slick grammar — the 4 kinds, the manifest | **shipped** · v0.2.0 |
| cix plugin family — installable as components | **shipped** |
| gnx CLI, registry, marketplace | **planned** · build starting |

No page on this site describes a command as runnable unless it runs today.

## Where to go from here

- **[How components work](/docs/how-components-work)** — the grammar an agent reads to compose: the four kinds, the manifest, what a component declares about itself.
- **[Authoring a component](/docs/author-a-component)** — the designed loop for building and registering your own.
- **[gnx CLI reference](/docs/cli-reference)** — the full designed command surface.

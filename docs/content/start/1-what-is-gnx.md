---
title: What gnx is
section: start
mode: explanation
status: mixed
register: public
fidelity: tarmac
---

# What gnx is

**gnx is a mech suit for your agent** — *generative noetic extensions*, said in full once and then left plain. Your agent puts the suit on and stops being a bare generalist. It wears a governed catalog of extensions: skills, capabilities, agents, whole workflows. Two moves, named plainly:

- **Wear an extension** and the agent gains one new thing it can do.
- **Compose extensions** and the working sets it can assemble multiply — more than the sum of the parts.

Adding parts extends what your agent *can do*; composing parts widens what it *can reach for*. The suit does not replace you. It hands you parts — inspectable, swappable, checked before anything bolts on — and you decide which ones it wears.

## The suit is generative

This is the loop gnx exists for. Your agent meets a task, composes what fits from the catalog, and when nothing fits it **builds the missing part**. What it builds folds back into the catalog **through you**. The loop is not self-producing — it closes through human governance, and that is the design, not a shortfall. A part your agent builds for itself today, once you let it in, is discoverable by every agent tomorrow.

Two roles stay structurally separate: **your agent composes and builds; you govern what enters and what is trusted.** The catalog grows through agents yet stays trustworthy because the machine only checks structure and only a person mints trust. An agent can author and submit a well-formed component; it cannot accredit one. Why that split holds is [why a catalog](/docs/why-a-catalog); how a component gets registered is [authoring a component](/docs/author-a-component). The suit never certifies its own parts.

## The hard problem is legibility

The hard problem underneath is not storage — any package manager stores things. It is **legibility**: whether an agent can decide two components fit from what they *declare*, before running either. That bet — composition from declaration, not from execution — is what the catalog is built on. [Why a catalog](/docs/why-a-catalog) makes the case; [how components work](/docs/how-components-work) shows the mechanism.

## What you do with it

- **Install from the catalog.** Two components — intent-hardening and rational-inquiry — are projected as plugins and install into Claude Code today. The wider set is being ported into the [catalog](https://github.com/mox-labs/gnx) as components.
- **Set up a project.** `gnx init` (designed) stands up a Claude Code project with a curated set of agents, skills, and scaffolds — chosen in conversation with you, not off a fixed menu.
- **Compose capabilities** (designed). An agent searches the catalog and assembles a working set from declared surfaces — no trial-and-error execution.
- **Extend the suit** (designed; authoring works today, the registration tooling is planned). Build the part that is missing, register it, and it becomes discoverable and composable — by any developer, by any agent, including the one that built it.

## Why it matters

The two moves compound. Each part your agent builds — once you let it in — becomes a part every later agent composes without rebuilding it, and because reach comes from composition, a catalog of parts is worth more than the parts summed. Two costs fall away as the catalog grows:

**Rebuilding.** A part distilled once composes into any project that declares it needs it — the argument in [why a catalog](/docs/why-a-catalog).

**Lock-in.** A component is described in a form no runtime owns; a second runtime is an adapter, not a rewrite — [vendor-neutral by structure](/docs/vendor-neutral-by-structure).

## How it is organized

Three functions, one catalog:

- the **registry** — where a component is declared, validated, and found;
- the **marketplace** — the catalog projected into installable Claude Code plugins;
- the **CLI** (`gnx`) — the tool that initializes projects and scaffolds new components.

The manifest grammar each component is written in — **slick** — ships today (the `slickit` crate, v0.2.0). The registry, marketplace pipeline, and CLI are designed; build is starting. [What's real vs planned](/docs/status) is the canonical record.

## Where to go from here

- **[Install a plugin](/docs/install-a-plugin)** — the part that runs today.
- **[How components work](/docs/how-components-work)** — the grammar an agent reads to compose: the kinds, the manifest, what a component declares about itself.
- **[Authoring a component](/docs/author-a-component)** — the loop for building and registering your own.
- **[gnx CLI reference](/docs/cli-reference)** — the full designed command surface.

---
title: gnx
section: start
mode: explanation
status: mixed
register: public
---

# gnx

**gnx is a mech suit for your agent.** It is a governed catalog of parts — skills, capabilities, agents, whole workflows — that your agent wears to do more, and composes into working sets that reach further than any part alone. When the catalog has nothing that fits, your agent builds the missing part, and what it builds folds back into the catalog only when you decide it belongs there. The next project starts with the parts the last one built. gnx is short for *generative noetic extensions*.

[What gnx is](/docs/what-is-gnx) unpacks the framing and the self-extension loop. For the line between what runs today and what is designed, see **[what's real vs planned](/docs/status)**.

## Three functions, one catalog

**Registry** — where a component is declared, validated at registration, and indexed so it can be found. This is the surface an agent searches when it composes.

**Marketplace** — the Claude Code face of the registry: catalog components projected into installable plugin directories. Two plugins are projected and installable today; the projection tool that generates them is designed.

**CLI** — `gnx` bootstraps projects, scaffolds new catalog entries, and serves discovery and registration. The command surface is designed; the runnable path today is installing a plugin.

Every component is declared in the **slick** manifest grammar (the `slickit` crate, v0.2.0, shipped). gnx consumes that grammar and adds a catalog layer on top of it; it never redefines it.

## Where to go

- **[Install a plugin](/docs/install-a-plugin)** — the one path that runs today: register the gnx marketplace and install a working plugin into Claude Code.
- **[Set up a project with gnx init](/docs/gnx-init)** — bootstrap a Claude Code project through a curated conversation (designed).
- **[What's real vs planned](/docs/status)** — the shipped-versus-designed line, in one table.
- **[What gnx is](/docs/what-is-gnx)** — the catalog in depth: what governed composition means and what you do with it.
- **[How components work](/docs/how-components-work)** — the kinds, the manifest, and what a component declares about itself.
- **[Authoring a component](/docs/author-a-component)** — the loop for building and registering your own.
- **[gnx CLI reference](/docs/cli-reference)** — the designed command surface in full.

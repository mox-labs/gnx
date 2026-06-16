---
title: Start a project with gnx init
section: guides
mode: how-to
status: planned
register: public
fidelity: tarmac
---

# Start a project with gnx init

**`gnx init` stands up a project and picks its starting capabilities through a conversation — not a fixed menu.** The CLI is planned, not yet built. Until it ships, [install a plugin](/docs/install-a-plugin) through Claude Code's marketplace is the path that runs today.

---

## gnx init scaffolds claude, dao, and .gnx/

`gnx init` will scaffold three pieces of a project:

```
claude/         # the agent layer: a CLAUDE.md constitution seeded with project context
dao/            # the agentic organization: charter, guild roles, ratchet
.gnx/           # the tooling layer: the components it installed, and their projection state
```

Plus a curated set of components — agents, skills, scaffolds — installed into `.gnx/`, chosen for *this* project.

---

## The interview picks your core set

The curation is a conversation, run by the Claude Agent SDK inside the CLI. It asks what you're building and what discipline the project needs, then proposes a set — a review panel here, a research scaffold there. You refine the proposal; it installs what you agree on.

A fixed menu forces every project into the same shape; the core set is *selected*, not defaulted. Two projects that need different disciplines get different sets from the same catalog.

One boundary holds throughout: **the SDK session proposes; deterministic code installs.** The agentic phase ends at the proposal, and validated, auditable code does the install — so the catalog stays a surface the agent reads, not one it can quietly rewrite.

---

## Two modes, one binary

`gnx init` will run from a bare terminal — before Claude Code is configured — and equally as a tool Claude Code drives from inside an existing project. The caller changes; the behavior doesn't. The Agent SDK runs the interview in both modes.

---

[Composing](/docs/compose-components) and [authoring](/docs/author-a-component) work against the set `init` curated. `claude/CLAUDE.md` is what a fresh session reads first.

---

## Where to go next

- **[gnx CLI reference](/docs/cli-reference)** — the full designed command surface, `init` included.
- **[Install a plugin](/docs/install-a-plugin)** — the path that runs today, while the CLI is built.

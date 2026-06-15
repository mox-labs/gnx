---
title: Start a project with gnx init
section: guides
mode: how-to
status: planned
register: public
fidelity: tarmac
---

# Start a project with gnx init

**`gnx init` stands up a project and picks its starting capabilities through a conversation — not a fixed menu.** This is the designed genesis flow; the CLI is planned, not yet built. Until it ships, [install a plugin](/docs/install-a-plugin) through Claude Code's marketplace is the path that runs today. The shape below is what `gnx init` will do.

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

The curation is a conversation, run by the Claude Agent SDK inside the CLI. It asks what you're building and what discipline the project needs, then proposes a set — a review panel here, a research scaffold there, the continuity and guardrail components that fit. You refine the proposal; it installs what you agree on.

A fixed menu would force every project into the same shape. The interview does the opposite: the core set is *selected*, not defaulted. Two projects that need different disciplines get different sets from the same catalog.

One boundary holds throughout: **the SDK session proposes; deterministic code installs.** The conversation never writes to the registry directly. The agentic phase ends at the proposal, and validated, auditable code does the install — so the catalog stays a surface the agent reads, not one it can quietly rewrite.

---

## Two modes, one binary

`gnx init` will run from a bare terminal — before Claude Code is configured — and equally as a tool Claude Code drives from inside an existing project. The caller changes; the behavior doesn't. The Agent SDK handles the interview in both modes, so genesis works whether a human types the command or an agent reaches for it.

---

From there, [composing](/docs/compose-components) and [authoring](/docs/author-a-component) work against the set `init` curated. The `claude/CLAUDE.md` constitution is the orientation a fresh session reads first.

---

## Where to go next

- **[gnx CLI reference](/docs/cli-reference)** — the full designed command surface, `init` included.
- **[Install a plugin](/docs/install-a-plugin)** — the path that runs today, while the CLI is built.

---
title: Start a project with gnx init
section: guides
mode: explanation
status: planned
register: public
---

# Start a project with gnx init

`gnx init` suits up a whole project for an agent, from nothing: a context layer, an agentic working set, and tooling — all chosen through a conversation, not off a fixed menu. Reach for it at the start of a new project, before there is a setup to add anything to; once one exists, [installing a plugin](/docs/install-a-plugin) covers adding individual pieces to it. The command is **designed**; this page describes what it will do.

---

## What init produces

Running `gnx init` in a fresh directory scaffolds three things:

```
claude/         # agent layer — a CLAUDE.md seeded with project context
dao/            # agentic organization — charter, agent roles
.gnx/           # tooling layer — installed components and their state
```

`claude/CLAUDE.md` is what every new session reads first. `dao/` is the project's agentic organization — its charter and the agent roles that review and steward the work. `.gnx/` holds the components `init` installed and tracks their projection state.

---

## How components get selected

Before installing anything, `gnx init` runs an interview. The Claude Agent SDK drives the conversation: it asks what you are building and what discipline the project needs, then proposes a component set — agents, skills, scaffolds — drawn from the catalog. You push back on what doesn't fit; when you agree, `init` installs what you confirmed.

No two projects need the same set — a research tool and a web service start from different components even when both use `gnx init`. The boundary between the interview and the install is hard: **the SDK session proposes; deterministic code installs.** The agentic phase ends at the proposal, and validated, auditable code does the write — so the catalog stays a surface agents read, not one they can quietly rewrite.

---

## Two modes, one command

`gnx init` works from a bare terminal before Claude Code is configured, and equally as a command Claude Code drives from inside an existing project. The caller changes; the behaviour does not. The Agent SDK handles the interview in either mode, and `--defaults` gives a deterministic, interview-free path for automation.

---

## After init

[Composing](/docs/compose-components) and [authoring](/docs/author-a-component) work against the component set `init` curated.

---

## Where to go next

- **[gnx CLI reference](/docs/cli-reference)** — the full designed command surface, `init` included.
- **[Install a plugin](/docs/install-a-plugin)** — the one path that runs today.

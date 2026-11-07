---
title: What gnx is
section: start
mode: explanation
status: shipped
register: public
---

# What gnx is

Components that make agents more effective, and their work inspectable.

Most of them are Python tools an agent drives from the command line: evaluate an agent, collect
data, compose agents and components, call a model. The rest are skills, installed into Claude
Code as plugins.

## What the tools give an agent

**More effective.** An agent doesn't have to guess how to use a tool. `ix --skill` prints the
guide for the version installed, and `matrix`, `recon` and `hardline` do the same. Every setting
has a default, and the plan shows each value and who set it. A refusal says what to do next:
`ix run` on an unregistered experiment exits with the command that registers it.

**More inspectable.** A plan before every run, so you see what will happen before anything
happens. Every run recorded as it goes, with what produced each row. ix answers a question with
a 95% interval and gives no verdict the evidence can't carry: an interval that straddles the
threshold reads `inconclusive`, and a dry run gives no verdict at all.

## Where to start

| You want to | Use |
|---|---|
| evaluate an agent or a service by experiment | **ix** |
| collect data from APIs, CLIs, web pages and files, then query it with SQL | **recon** |
| compose components and agents, with every row recorded | **matrix** |
| call any model family through one interface | **hardline** |
| check a project's agent organization | **dao**: `dao check <project>` audits one; it does not write one for you |
| install skills into Claude Code | the marketplace: [Install a plugin](/docs/install-a-plugin) |

Each tool's README, in the [repository](https://github.com/mox-labs/gnx), has its install line
and a first run. [The catalog](/catalog) lists every component, read from source.

## Component, plugin, catalog

A **component** is the unit of authorship: one skill, one agent, or one runnable capability, in
its own directory.

A **plugin** is a bundle of components, and it is what you install into Claude Code. Bundling is
declared apart from authoring, in `components/bundles.yaml`, so one component can ship in
several plugins.

The **catalog** is every component in the repository, whatever it is bundled into.

| kind | what it is |
|---|---|
| Skill | a practice, read into an agent's context when its trigger matches |
| Agent | a named reasoning role with a distinct method |
| Capability | a Python package, installed with `uv` rather than as a plugin |
| Flow | a declared composition of other components |

A capability is configuration-driven. What it composes (which models, which agent runtimes,
which sensors) is declared in a config file, so changing it is a config change, not a code
change.

## Next

- **[How gnx is built](/docs/how-gnx-is-built)**: who decides, and the evidence.
- **[Install a plugin](/docs/install-a-plugin)**: the two commands.
- **[The catalog](/catalog)**: every component, and which plugins ship it.

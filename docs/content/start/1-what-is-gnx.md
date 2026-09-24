---
title: What gnx is
section: start
mode: explanation
status: shipped
register: public
---

# What gnx is

A marketplace of composable components for Claude Code. [The catalog](/catalog) lists every
component and plugin, read from this repository's source at build time — so it is the
count, and this page never repeats one.

## Component, plugin, catalog

A **component** is the unit of authorship — one skill, one agent, or one runnable
capability, in its own directory.

A **plugin** is a bundle of components, and it is what you install. Bundling is declared
separately from authoring, in `components/bundles.yaml`, so one component can ship in
several plugins.

The **catalog** is every component in the repository, whatever it is bundled into.

| kind | what it is |
|---|---|
| Skill | a practice, read into context when its trigger matches |
| Agent | a named reasoning role with a distinct method |
| Capability | a Python package, installed with `uv` rather than as a plugin |
| Flow | a declared composition of other components |

## The catalog holds what has been evaluated

A component enters the catalog when a measurement says it works — a routing evaluation for
a skill, its test suite and standalone install for a capability. Components that have not
been evaluated live in `incubator/`, intact and versioned, and are not projected.
Graduating one is a directory move and one entry in `bundles.yaml`.

## Two ways in

Skills and agents install as **Claude Code plugins**, through the marketplace:
[Install a plugin](/docs/install-a-plugin).

Capabilities are **Python packages**. Each one installs from this repository with `uv`, and
its README carries the command. They are configuration-driven: what a capability composes —
which models, which agent runtimes, which sensors — is declared in a config file and wired
by its composition root, so changing it is a config change, not a code change.

## Next

- **[Install a plugin](/docs/install-a-plugin)** — the two commands.
- **[The catalog](/catalog)** — every component, and which plugins ship it.

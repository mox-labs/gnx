---
title: What gnx is
section: start
mode: explanation
status: shipped
register: public
---

# What gnx is

A marketplace of composable components for Claude Code.

**3 plugins** are installable today, cut from **7 components**. Browse them in
[the catalog](/catalog), which reads the component directories live rather than repeating
a count that would go stale.

## Component, plugin, catalog

A **component** is the unit of authorship — one skill, or one runnable capability, in its
own directory.

A **plugin** is a bundle of components, and it is what you install. Bundling is declared
separately from authoring, in `components/bundles.yaml`, so one component can ship in
several plugins.

The **catalog** is every component in the repository, whatever it is bundled into.

| kind | count | what it is |
|---|---|---|
| Skill | 2 | a practice, read into context when its trigger matches |
| Capability | 5 | a Python package, installed with `uv`/`pip` rather than as a plugin |
| Agent | 0 | a named reasoning role — none in the catalog today |
| Flow | 0 | a declared composition — arrives with generation 0 |

## The catalog is small on purpose

There were 76 components here. **69 moved to `incubator/`** on 2026-08-19 — intact and
versioned, but out of the catalog and not projected.

The reason is evidence, not tidiness. A live routing measurement found the `dao` skill
failing to activate on trigger phrases quoted verbatim in its own description, and 49 of
those 76 descriptions ran past the length at which a description still reads as a
description. Shipping all of them would have asserted a quality nobody had checked.

What remains is what has been evaluated. Graduating a component back is a directory move
and one entry in `bundles.yaml`.

## Two ways in

Skills and agents install as **Claude Code plugins**, through the marketplace.

Capabilities are **Python packages** — `matrix`, `ix`, `recon`, `dao`, `gnx`. They install
with `uv` or `pip`, not through the marketplace, and none is on PyPI (every one of those
names is taken there by an unrelated project), so today they install from this repository.

The `recon` plugin sits across that seam: the plugin ships the skill, and `recon` the CLI is
the package. The skill says so in its first paragraph rather than assuming a command that
may not be there.

## Where this is

**Generation 0 — the composition layer — is upcoming.** Components install and run; they
do not yet compose. The declared-identity layer a composer would resolve is deferred until
its shape settles in [slick](https://github.com/mox-labs/slick).

[What's real vs planned](/docs/status) is the record, written to be checkable rather than
encouraging.

## Next

- **[Install a plugin](/docs/install-a-plugin)** — the two commands.
- **[The catalog](/catalog)** — every component, and which plugins ship it.

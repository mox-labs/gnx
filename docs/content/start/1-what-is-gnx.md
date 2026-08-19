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
| Capability | 5 | a runnable Python package — the package itself is not a plugin |
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

## Where this is

**Generation 0 — the composition layer — is upcoming.** Components install and run; they
do not yet compose. Both catalog skills carry a manifest, the declared identity a composer
would resolve; the Manifest v1 shape is being settled in
[slick](https://github.com/mox-labs/slick).

[What's real vs planned](/docs/status) is the record, written to be checkable rather than
encouraging.

## Next

- **[Install a plugin](/docs/install-a-plugin)** — the two commands.
- **[The catalog](/catalog)** — every component, and which plugins ship it.

---
title: What gnx is
section: start
mode: explanation
status: shipped
register: public
---

# What gnx is

A marketplace of composable components for Claude Code.

**11 plugins** are installable today, cut from **76 components** in one repository. Browse
them in [the catalog](/catalog), which reads the component directories live rather than
repeating a count that would go stale.

## Component, plugin, catalog

A **component** is the unit of authorship — one skill, one agent, or one runnable
capability, in its own directory.

A **plugin** is a bundle of components, and it is what you install. Bundling is declared
separately from authoring, in `components/bundles.yaml`, so one component can ship in
several plugins. `trust-boundaries` is in two.

The **catalog** is every component in the repository, whatever it is bundled into.

| kind | count | what it is |
|---|---|---|
| Skill | 35 | a practice, read into context when its trigger matches |
| Agent | 36 | a named reasoning role with a distinct method |
| Capability | 5 | a runnable Python package — `matrix`, `ix`, `recon`, `dao`, `gnx` |
| Flow | 0 | a declared composition of other components — arrives with generation 0 |

## Where this is

**Generation 0 — the composition layer — is upcoming.** What exists now is the catalog
those compositions will draw from.

Concretely: components install and run, and they do not yet compose. **3 of 76 carry a
manifest**, the declared identity a composer would resolve. That is deliberate — the
Manifest v1 shape is being settled in [slick](https://github.com/mox-labs/slick), and
minting 76 against a moving spec would mean rewriting 76. The three that exist are the
pilot that tests the grammar.

[What's real vs planned](/docs/status) is the record, and it is written to be checkable
rather than encouraging.

## Next

- **[Install a plugin](/docs/install-a-plugin)** — the two commands.
- **[The catalog](/catalog)** — every component, and which plugins ship it.

---
title: Why a catalog
section: explanation
mode: explanation
status: planned
register: public
fidelity: tarmac
---

# Why a catalog

**Capability gets trapped in the project that first built it.** A review panel you tuned, a research scaffold you refined, a skill you distilled — they live in one repo, and the next project rebuilds them from scratch. The waste is not the first build. It's the rebuild, every time, because there was nowhere to put the thing so it could travel.

A catalog is the place to put it. That is the whole idea: distill a capability once, register it, and compose it into any project that declares it needs it.

## Why a registry, specifically

Storing capability is easy. The hard part is letting an agent **assemble capabilities it has never run** — decide that two components fit, from what each declares about itself, before either executes.

That is what a registry buys that a folder of plugins does not. Each entry declares its surface — what it offers, what it needs, how it's reached — in a form legible enough to reason over. An agent reads the declarations, decides what composes, and assembles a working set without trial-and-error execution. Legible surfaces are what make composition tractable. The registry exists to keep them legible.

## Why a marketplace on top

A registry is where components are declared and found. A marketplace is how they reach a runtime. For Claude Code, that means installable plugins — the catalog projected into the form Claude Code installs and loads. The registry holds the truth; the marketplace is a view of it, generated and shipped so an agent can install what it found.

## Why curated, not open-write

The catalog is human-curated, and that is a feature, not friction. An agent composes from what the catalog says — so what the catalog says has to be trustworthy. Accreditation in gnx is human-minted: a status an agent can read and build on, but cannot mint for itself. The catalog grows through agents; it stays trustworthy because the people who build what they compose from govern what enters it.

This is the split that keeps the catalog honest: it certifies **structure** — that a component is well-formed, that its ports satisfy, that its namespace is in scope — and leaves **trust** to be minted by a human. A component that declares it handles sensitive data and emits logs composes cleanly; whether that composition is *safe* is a judgment a person signs, not one the catalog fabricates.

## Standalone value is the premise

A component earns the catalog by being useful on its own — installable and worth installing without the rest of the stack. The cix plugin family installs into Claude Code today and does real work with nothing else present. That is the bar: each piece stands alone, and the catalog is how the standalone pieces find each other.

## Where to go next

- **[What gnx is](/docs/what-is-gnx)** — the catalog's three functions and where the shipped/designed line sits.
- **[Vendor-neutral by structure](/docs/vendor-neutral-by-structure)** — why a component is described in a way no single runtime owns.

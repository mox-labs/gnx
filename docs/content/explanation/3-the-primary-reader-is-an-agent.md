---
title: The primary reader is an agent
section: explanation
mode: explanation
status: planned
register: public
fidelity: tarmac
---

# The primary reader is an agent

**gnx is built for an agent to read first, and a human to browse second.** That single inversion shapes the rest. A package registry optimizes for a person scanning a list. gnx optimizes for a reasoner deciding, from declarations alone, what to compose and how to drive it — the primary consumer is Claude Code, reaching for capability mid-task, not a developer reading docs over coffee.

Take the inversion seriously and three design choices stop being optional.

## Surfaces have to be legible without execution

An agent decides whether two components fit before running either. So the surface a component declares — what it offers, what it needs, how it's reached — has to carry enough for that decision. `provides: ["session-memory"]` against a typed port is something to reason from. `provides: ["does stuff"]` is not. The catalog's job is to keep surfaces on the legible side of that line; a component that can't be reasoned over without running it doesn't belong.

## Every Capability describes itself in the agent's language

A human reads a man page; an agent reads a skill. So every Capability in the catalog ships an embedded skill and exposes `--skill` — it emits the SKILL.md an agent needs to drive it: what it does, how to invoke it, when to reach for it. A capability that can't describe itself to an agent isn't agent-operable. gnx holds itself to the rule: `gnx --skill` is its own front door.

## Discovery returns structure, not prose

When an agent searches the catalog, it gets back machine-parseable structure — the matching components and their declared surfaces — not a paragraph to skim. The same holds for the whole doc surface: an index an agent can fetch first to enumerate what exists, and a clean structured form per page.

## The test behind all three

One question decides whether a component meets the bar:

> Can a capable reasoner decide to compose this component with another, from its declared surface alone?

Not "is the README nice." Not "does it run." Can an agent *decide*, from the declaration. That test — applied to the manifest, the `--skill` surface, the discovery output — is why the catalog is shaped the way it is. The human-readable docs are the courtesy. The legible surface is the product.

## Where to go next

- **[How components work](/docs/how-components-work)** — the declared surface an agent reads: kinds, manifest, ports.
- **[Why a catalog](/docs/why-a-catalog)** — why legible declared surfaces are the thing worth building.

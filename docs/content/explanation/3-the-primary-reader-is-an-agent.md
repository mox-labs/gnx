---
title: The primary reader is an agent
section: explanation
status: mixed
register: public
---

# The primary reader is an agent

**gnx is built for an agent to read first, and a human to browse second.** That single inversion shapes the rest. A package registry optimizes for a person scanning a list; gnx optimizes for a reasoner deciding, from declarations alone, what to compose and how to drive it. The primary consumer is Claude Code, mid-task — not a developer reading docs over coffee.

The inversion has a mechanism. An agent cannot browse or skim — it reads a declared surface and either has enough to decide, or it does not. So the surface has to do all the work. That constraint makes three design choices non-optional.

## Surfaces have to be legible without execution

An agent decides whether two components fit from their declared surfaces, before running either — the bet [why a catalog](/docs/why-a-catalog) rests on. So the surface has to carry enough. A typed port against a typed port is something to reason from; `provides: ["does stuff"]` is not. The catalog's job is to keep surfaces on the legible side of that line — a component that cannot be reasoned over without running it does not belong.

## Every Capability describes itself in the agent's language

A human reads a man page; an agent reads a skill. So every Capability in the catalog must ship an embedded skill and expose `--skill` — it emits the `SKILL.md` an agent needs to drive it: what it does, how to invoke it, when to reach for it. A Capability without `--skill` is opaque to an agent: it would sit in the catalog with no way for an agent to reason about how to use it — reachable, but not operable. gnx holds itself to the same rule: `gnx --skill`, designed with the rest of the CLI, is its own front door. The convention itself already ships: Claude Code activates the two installed plugins by loading the skill inside each and driving behaviour from it.

## Discovery returns structure, not prose

When an agent searches the catalog, it needs machine-parseable output — the matching components and their declared surfaces — not a paragraph to skim. The same principle carries through to the documentation surface itself, and that part is **live today**: [/llms.txt](/llms.txt) is the index an agent fetches first to enumerate what exists, and every page has a markdown twin at `/raw/<slug>`. The surface carries its own maturity, too — each `/llms.txt` entry is tagged with the page's status marker (`shipped`, `planned`, `proposed`, or `mixed`), and every `/raw` twin opens with a status line. An agent should read those markers before treating anything a page describes as executable: a `planned` or `proposed` surface is something to reason about, not something to run. The doc surface already works the way the catalog's discovery tooling is designed to. This page is one of those twins right now: a `mixed` status line, an `/llms.txt` entry, a `/raw` copy at `/raw/the-primary-reader-is-an-agent` for whatever reads it next: built once, not re-explained by a person each time an agent needs it.

## The test behind all three

One question decides whether a component meets the bar:

> Can a capable reasoner decide to compose this component with another, from its declared surface alone?

Not "is the README nice." Not "does it run." Can an agent *decide*, from the declaration. That test — applied to the manifest, the `--skill` surface, the discovery output — is why the catalog is shaped the way it is. The human-readable docs are the courtesy; the legible surface is the product.

The test runs in both directions. An agent reads catalog surfaces to decide what to compose. When nothing fits, that same agent authors the missing piece and registers it — and what it writes has to pass the same test. The component an agent builds for itself today must be legible enough for any agent to compose tomorrow. The reader and the author are the same entity, separated by one loop.

## Where to go next

- **[How components work](/docs/how-components-work)** — the declared surface an agent reads: kinds, manifest, ports vs tags.
- **[Install a plugin](/docs/install-a-plugin)** — put the idea to work: install a component an agent can drive today.

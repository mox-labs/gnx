---
title: The three planes
section: explanation
register: public
status: proposed
---

# The three planes

**Every component in the catalog exists on three planes at once — Mechanics, Semantics, Governance.** Read a component on the wrong plane and it will look either trivial or impossible. Read it on all three and the catalog stops being a pile of YAML and becomes a thing you can reason about.

The planes are not layers stacked on top of each other. They are three questions you can ask of the same component, and the answers come from three different places:

| Plane | The question | Where the answer lives | Who can settle it |
|---|---|---|---|
| **Mechanics** | How does it connect? | the manifest — identity, ports, relations, transport | a machine, by checking |
| **Semantics** | What does it mean? | the description and the skill payload | an agent, by reading |
| **Governance** | Should we trust it? | the accreditation record | the collective, by ruling |

## Mechanics — the decidable skeleton

Mechanics is everything a machine can check without understanding anything.

A component has an identity that is permanent once minted. It has **ports** — the typed contracts it consumes and emits. It has relations to other components. It has a source, and a way to be reached. All of it is structure, and all of it is checkable: either the port types line up or they do not, either the identity is well-formed or it is not.

This is the plane that makes composition *safe*. When two components are wired together, mechanics answers whether the connection is even coherent — before anything runs, before any agent reasons about whether it is a good idea.

Mechanics is deliberately narrow. It is a skeleton, and skeletons do not carry meaning.

## Semantics — the part no type system can decide

Here is the honest limit, stated plainly: **whether two components should compose is not a question types can answer.**

Types can tell you that a component emitting `extraction-set` can feed a component consuming `extraction-set`. They cannot tell you whether *this* extraction belongs in *that* analysis, whether the combination is sound, whether it is the right tool for what you are actually doing. That judgment needs a reader who understands the domain.

So gnx does not pretend to close that gap. It **manages** it. The semantic half rides in payloads the machine passes through and the agent reads: a one-line description that makes a component findable, and a skill document that explains how the thing actually works and when to reach for it.

This is why the catalog is written for an agent to read, not just for a parser to validate. The parser handles the skeleton. The agent handles the meaning. Neither can do the other's job, and a system that claims otherwise is lying about one of them.

## Governance — what the collective has ruled

The third plane answers a question the first two cannot touch: has this been judged, by whom, and on what evidence?

Governance lives **outside** the manifest, and this is structural rather than stylistic. A component cannot certify itself. If a component's manifest carried its own trust rating, the author would be both the proposer and the judge — and the rating would be worth exactly nothing.

So accreditation is a separate record. Verdicts are evidence, never authority. The record accumulates: what was checked, what was found, what was decided, and by whom. It is **authored, accumulated, and contestable** — not a boolean stamp. Trust in the catalog is not a property a component claims; it is a history the collective builds.

This is also why gnx is better described as an **ecology than an economy** — a self-governed collective rather than a market of transactions. What accumulates is judgment, not price.

## Why the split is the whole design

The three planes are not an organizing metaphor laid over the system after the fact. They are a boundary that decides what goes where, and the boundary has one rule:

> **Structure the surface, never the reasoning.**

Mechanics structures the surface — the shape of the connection. Semantics is left to a reader who can actually reason. Governance records what was judged without dictating how to judge. Every time there is pressure to grow the core — to add a field that would encode meaning, to let a component assert its own quality — the answer is to route it to the plane it belongs on rather than collapse the distinction.

Systems that ignore this boundary fail in a recognizable way. They try to make meaning machine-checkable, which produces a schema nobody can satisfy honestly, and self-declared quality labels that are only as truthful as the least careful contributor. The three planes exist so that each question is answered by whoever can actually answer it.

## One component, read three ways

Here is a component that ships in the catalog today. The same twelve lines answer all three questions, and it is worth seeing which line answers which.

```yaml
type_url: gnx.dev.v1.claim-extraction
kind: Agent
maturity: shipped
source: ./agent.md
provides:
  - gnx.dev.v1.extraction-set      # port
  - research.claim-extraction      # tag
requires:
  - gnx.dev.v1.source-cache        # port
description: Claimify extraction — atomic claims with verbatim quotes, locations, and evidence tiers.
```

**Mechanics** is the identity, the kind, and the two ports. This component consumes a source cache and emits an extraction set. A machine can determine from those two lines alone that it can be wired downstream of anything producing `source-cache`, and upstream of anything consuming `extraction-set`. No understanding required.

**Semantics** is the `description` line and the `agent.md` it points at. "Atomic claims with verbatim quotes, locations, and evidence tiers" is what makes the component *findable* by someone who does not already know its name — and what tells a reader whether extraction is the operation they actually want. Nothing in the mechanics could tell you that.

**Governance** is `maturity: shipped` — and, more tellingly, what the manifest does *not* say. The component has no `relations` entry, and the authoring comment explains why: a verification edge exists in practice, but the component on the other end has not been minted yet, so the edge is not written. **No invented provenance.** A relation you cannot evidence is a relation you do not write, and that discipline is what makes the graph worth reading at all.

## Reading a component on all three planes

In practice, when you encounter a component you have not seen before:

1. **Mechanics first** — what does it consume, what does it emit, what does it depend on? This is fast, and it tells you where the component could possibly fit.
2. **Semantics second** — read the description, then the skill. This tells you whether it *should* fit, which is the part that needs you.
3. **Governance last** — check the record. This tells you what has been established already, so you are not re-deciding what has been decided.

Do them in that order and each stage narrows the next. Do them out of order and you will read a lot of prose about components that could never have connected in the first place.

## Where to go next

- **[How components work](/docs/how-components-work)** — the manifest and namespace that carry the mechanics plane.
- **[The primary reader is an agent](/docs/the-primary-reader-is-an-agent)** — why the semantics plane is written for a reader, not a parser.
- **[Vendor-neutral by structure](/docs/vendor-neutral-by-structure)** — how mechanics stays runtime-independent.

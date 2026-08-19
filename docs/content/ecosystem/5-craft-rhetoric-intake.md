---
title: "The craft-rhetoric intake"
section: ecosystem
status: proposed
mode: explanation
---

# The craft-rhetoric intake — the decision surface

*Five decisions gate this intake. Two are one-way doors. Nothing below is built or minted; every manifest shown is a draft serialized nowhere. This page is the pilot of the [dossier grammar](https://github.com/mox-labs/gnx) (research: `~/mox/research/drafts/dossier-grammar/` — synthesis audited SHIP 2026-07-18); it equips the rulings and directs none of them.*

## The queue, in gate order

| # | Decision | Gates on | Door | State |
|---|---|---|---|---|
| D-CR1 | Accept building on GEP-0003's draft Flow shape | — | two-way | open |
| D-CR2 | Ratify the grain: 18 units + 1 Flow, amendments A1–A4 binding | D-CR1 | two-way until mints | open |
| D-CR3 | The seven compound re-mints | D-CR2 | **one-way** | open |
| D-CR4 | The two boundary-case bare mints | D-CR2 | **one-way** | open |
| D-CR5 | Sequencing: what this intake does to the DAO-first order | — | two-way | open |

Rule in gate order: D-CR3 and D-CR4 serialize permanent identities (D13 — mint once, alias forever), so they wait on the grain, which waits on the shape it would serialize.

## Situation — what exists right now

| Thing | State |
|---|---|
| craft-rhetoric source | frozen at cix (2026-05-20): 9 agents, 9 skills, docs, plugin.json v0.3.0, 42 files |
| Guild deliberation | done 2026-07-15 — 4 seats + Lotfi + Ixian; ratchet entry written |
| The 19 manifests | **not written** — drafts below are illustrative |
| `gnx validate` | **does not exist** (bootstrap Phase 2 unbuilt) — hence A4: draft maturity until it does |
| The projector | prototype only; hardcodes version 0.1.0 and MIT (both wrong) — rebuild is Phase 2 item 3 |
| Shipped precedent | intent-hardening + rational-inquiry: bare-named Skills, 1:1 projections, installable today |

## Mechanism — how a unit becomes an installable plugin

```
components/agents/orwell/          components/skills/voicing/
  manifest.yaml  orwell.md           manifest.yaml  SKILL.md  references/
        │                                  │
        └────────── relations.uses ────────┘
                         │
              components/flows/craft-rhetoric/
                manifest.yaml        # members: the 18 units (GEP-0003 draft shape — D-CR1)
                         │
                    gnx build        # designed; pulls the transitive relations closure
                         │           # or refuses projection as incomplete (A1)
        ┌────────────────┴────────────────┐
  plugins/craft-rhetoric/          .claude-plugin/marketplace.json
  (self-contained, committed,      (one source generates both — no
   README + plugin.json GENERATED)  dual-manifest drift)
```

The drafts, as they would sit on disk. Every value below is reviewable; none is serialized.

```yaml
# components/agents/orwell/manifest.yaml — DRAFT, not minted
type_url: gnx.dev.v1.orwell
kind: Agent
source: ./orwell.md
provides:
  - voice.preservation          # tags — discovery only
  - voice.llm-tells
requires: []                    # Agents carry no ports today
relations:
  uses:                         # A1: the frontmatter coupling, declared
    - gnx.dev.v1.<rhetoric-remint>   # ← blocked on D-CR3
    - gnx.dev.v1.voicing             # ← blocked on D-CR4
  lineage:
    - craft-rhetoric@cix
maturity: draft                 # A4: stays draft until gnx validate exists
```

```yaml
# components/flows/craft-rhetoric/manifest.yaml — DRAFT, shape gated on D-CR1
type_url: gnx.dev.v1.craft-rhetoric
kind: Flow
source: ./manifest.yaml
provides:
  - content.rhetoric            # tag
requires: []
members:                        # GEP-0003 proposed shape — the flat bench is first-class
  - type_url: gnx.dev.v1.ebert
  - type_url: gnx.dev.v1.feynman
  # … 16 more
relations:
  lineage: [craft-rhetoric@cix]
maturity: draft
```

## The naming evidence — Lotfi's scoring, shown

```decision-matrix
{
  "title": "Three naming policies × seven dimensions (Lotfi, 2026-07-15)",
  "rows": [
    { "label": "Collision/permanence (D13)", "kind": "judgment", "weight": "0.25" },
    { "label": "Scaling to the 68-unit intake", "kind": "judgment", "weight": "0.20" },
    { "label": "Dependency direction (packaging in identity)", "kind": "judgment", "weight": "0.15" },
    { "label": "Agent-consumer legibility (join key read cold)", "kind": "judgment", "weight": "0.12" },
    { "label": "Payload/projector mechanics", "kind": "judgment", "weight": "0.10" },
    { "label": "Precedent fit (shipped mints)", "kind": "standard", "weight": "0.10" },
    { "label": "Human legibility (browse, errors)", "kind": "judgment", "weight": "0.08" }
  ],
  "cols": ["A: all bare", "B: split", "C: family-scoped"],
  "cells": [
    ["0.30", "0.85", "0.90"],
    ["0.25", "0.85", "0.60"],
    ["0.90", "0.85", "0.30"],
    ["0.50", "0.75", "0.90"],
    ["0.95", "0.80", "0.35"],
    ["0.90", "0.90", "0.20"],
    ["0.55", "0.75", "0.70"]
  ],
  "note": "All scores are one agent's calibrated judgment, not measurements. Lotfi's weighted aggregates — A 0.55, B 0.83, C 0.61 — are his synthesis, reported here as judgment rather than rendered as a totals row; compare by dimension. Policy B won on the observation that the axis is collision class: proper nouns bare, generic words compound-or-justify."
}
```

## The decisions

```decision
{
  "id": "gep-0003-acceptance",
  "title": "D-CR1 — Build on GEP-0003's draft Flow shape?",
  "context": "The craft-rhetoric Flow manifest serializes the members-list shape that GEP-0003 proposes but nothing has ratified. Building on it either hardens a draft silently — the thing the decisions log exists to prevent — or is accepted explicitly and logged.",
  "reversibility": "two-way",
  "calibration": { "confidence": "moderate", "basis": "GEP-0003's flat-bench ruling was derived from the validated multi-agent ancestor (karman's Phase-1 verdict); the GEP itself is unratified" },
  "options": [
    { "id": "accept-draft", "label": "Accept the draft, logged", "consequence": "Intake proceeds; the acceptance is a decisions-log entry; if the ratified GEP later diverges, the Flow manifest migrates via relations, and any divergence cost lands on this intake" },
    { "id": "ratify-first", "label": "Ratify GEP-0003 first", "consequence": "Strongest foundation; the intake waits on a GEP review cycle; the seven-plugin batch waits behind it" },
    { "id": "hold", "label": "Hold", "consequence": "D-CR2 through D-CR4 stay blocked; the intake stalls with the guild verdict unratified" }
  ],
  "assumptions": [
    { "text": "GEP-0003's flat-bench (port-less members) ruling survives ratification", "ifWrong": "the craft-rhetoric Flow is re-cut as a distribution artifact; members move out of the manifest; D-CR2's shape changes" }
  ],
  "indicators": ["GEP-0003 ratification or amendment", "a second Flow consumer appearing before ratification"]
}
```

```decision
{
  "id": "grain-ratification",
  "title": "D-CR2 — Ratify the grain: 18 units + 1 Flow, A1–A4 binding",
  "context": "The guild converged (no dissent on direction, four seats) on decomposition as re-authoring: A1 declared relations, A2 self-contained method descriptions, A3 docs triage + generated listing surfaces, A4 draft maturity until a validator exists. This settles the round-two grain question that was owed to you, and sets the template for the ~50-unit cix batch.",
  "reversibility": "two-way",
  "gates": ["gep-0003-acceptance"],
  "calibration": { "confidence": "high", "basis": "4 independent seats converged; amendments overlap across seats; Ixian's V1–V12 gates are pre-registered" },
  "options": [
    { "id": "ratify-amended", "label": "Ratify with A1–A4 binding", "consequence": "Re-authoring work starts (19 manifests at draft maturity, unit descriptions rewritten, docs triaged); the projector must meet A3 before anything projects; V1–V10 gate the mints" },
    { "id": "plugin-grain", "label": "Plugin-grain wrapper instead", "consequence": "Fast; no slick kind honestly fits a mixed bundle; defers the grain question the bootstrap already owes an answer" },
    { "id": "hold", "label": "Hold", "consequence": "The intake stalls; craft-rhetoric stays a cix artifact" }
  ],
  "assumptions": [
    { "text": "Units survive re-authoring without losing the ensemble context that makes them work", "ifWrong": "V12.3 fires (cold units underperform the ensemble beyond noise) — the re-authoring theory of decomposition fails and the grain gets re-examined before the batch" },
    { "text": "The rebuilt projector can satisfy A3 (sibling layout, generated README/plugin.json, transitive closure)", "ifWrong": "projection waits; the units still exist as authored components" }
  ],
  "indicators": ["V5 cold-surface test results", "V11 cross-Flow reuse R at the next intake batch (pre-registered: R<0.15 re-opens grain+naming)"],
  "recommendation": { "by": "fable", "lean": "ratify-amended", "basis": "every seat's objection was lifted by amendments already folded in; the alternative defers a decision the bootstrap plan owes and the batch will re-raise" }
}
```

```decision
{
  "id": "compound-re-mints",
  "title": "D-CR3 — The seven compound re-mints (ONE-WAY at serialization)",
  "context": "Seven generic gerunds re-mint compound-descriptive before first serialization: discourse, discovering, evaluating, mapping, staging, figures, rhetoric. Names serialize permanently (D13); the ledger's ~15-entry collision history is the price of getting this class wrong. The strawman compounds have no standing — they were drafted in minutes.",
  "reversibility": "one-way",
  "gates": ["grain-ratification"],
  "calibration": { "confidence": "moderate", "basis": "the re-mint SET is Lotfi-scored and three-seat supported; the specific strawman NAMES are unreviewed drafts — V2's prediction test has not run" },
  "options": [
    { "id": "ratify-strawmen", "label": "Ratify the strawman set as-is", "consequence": "content-discourse, source-comprehension, content-critique, source-mapping, experience-staging, explanatory-figures, writing-method serialize at mint time; any misfit name is permanent and alias-only forever" },
    { "id": "review-per-name", "label": "Review each name, then mint", "consequence": "Each compound runs the V2 prediction test (cold reader predicts scope from type_url alone) and gets your yes/amend per name before serialization; slower by one review pass" },
    { "id": "hold-census", "label": "Hold until the batch census", "consequence": "The remaining six cix plugins are swept for claimants first; strongest collision guarantee; the intake's naming waits on the sweep" }
  ],
  "assumptions": [
    { "text": "The compound rule (describe the concept, never the family) suffices to avoid the ledger's failure class", "ifWrong": "a compound still collides at the batch — caught pre-serialization by V1/V2 or permanent after" }
  ],
  "indicators": ["V2 prediction-test results per name", "claimants surfacing in the six-plugin census"],
  "recommendation": { "by": "fable", "lean": "review-per-name", "basis": "the set is settled by evidence; the names are my quick drafts on a one-way door — a prediction-test pass per name costs little against permanent misfits" }
}
```

```decision
{
  "id": "boundary-bare-mints",
  "title": "D-CR4 — Mint arranging and voicing bare (ONE-WAY at serialization)",
  "context": "Two generic gerunds pass the genericity gate conditionally: no doctrine homonym, no named claimant today. Lotfi's ruling requires the gate decision documented at mint, and it flips if a claimant surfaces before serialization.",
  "reversibility": "one-way",
  "gates": ["grain-ratification"],
  "calibration": { "confidence": "moderate", "basis": "conditional pass; the six-plugin census has not been swept for claimants" },
  "options": [
    { "id": "mint-bare", "label": "Mint bare, gate ruling documented", "consequence": "gnx.dev.v1.arranging and gnx.dev.v1.voicing serialize; a later claimant works around them forever" },
    { "id": "remint-compound", "label": "Fold into D-CR3's compound set", "consequence": "Nine re-mints instead of seven; maximal collision safety; two distinctive craft terms lose their plain names" },
    { "id": "hold-census", "label": "Hold for the census sweep", "consequence": "Same sweep as D-CR3's third option; near-free if that option is chosen there" }
  ],
  "assumptions": [
    { "text": "No claimant for either word exists in the remaining cix census", "ifWrong": "flip to compound before serialization — after it, the claimant aliases around the mint permanently" }
  ],
  "indicators": ["the census sweep (review/design/research are already known landgrabs in the batch — the sweep is owed regardless)"]
}
```

```decision
{
  "id": "sequencing",
  "title": "D-CR5 — What this intake does to the DAO-first order",
  "context": "The settled sequencing (2026-07-12) was DAO-first with cix intake re-timed later. This intake jumped the queue on your call — legitimate, but the bootstrap plan now disagrees with practice, and the next session inherits whichever is written down.",
  "reversibility": "two-way",
  "calibration": { "confidence": "low", "basis": "pure sequencing judgment; no new evidence bears on it" },
  "options": [
    { "id": "one-off", "label": "One-off exception, DAO-first stands", "consequence": "craft-rhetoric completes; the dao plugin is next; the remaining six plugins wait behind it; ledger notes the exception" },
    { "id": "retime", "label": "Re-time the bootstrap: intake track first", "consequence": "The seven-plugin batch proceeds on the proven template; DAO work re-queues behind it; the 2026-07-12 sequencing memory is superseded" },
    { "id": "revert", "label": "Pause intake after this ratification, back to DAO-first", "consequence": "Decisions here still land; build work switches tracks after the manifests exist at draft" }
  ],
  "assumptions": [
    { "text": "The two tracks stay independent enough that order is preference, not dependency", "ifWrong": "the dao plugin needs intake-track tooling (the projector) or vice versa — the dependency, once named, decides this for you" }
  ],
  "indicators": ["the first dao-plugin work item that needs the projector"]
}
```

## Validation — the pre-registered gates

Ixian's twelve gates stand unchanged from the deliberation (V1 collision sweep · V2 prediction test · V3/V4 relations closure, positive and negative · V5 cold-surface · V6 noise floor before V7 fidelity · V8 one-source-two-manifests with an observed CI failure · V9 permanence checklist · V10 grouped rendering · V11 pre-registered reuse metric · V12 flip conditions). They are the indicators layer for every card above; V9 is the wall in front of both one-way doors.

## Residue (not decisions)

The `rhetoric`/`craft-rhetoric` substring collision dissolves under D-CR3 (the hub re-mints; the Flow keeps the public name). Grouped rendering (`∈ craft-rhetoric` in search/browse) ships with the intake as a naming-policy condition, not a choice. Provenance: guild transcripts in the ratchet (`.claude/guild-ratchet.md`); dossier-grammar research at `~/mox/research/drafts/dossier-grammar/`; this page conforms to grammar v0.1 (G1 queue, G2 cards, G3 calibration chips, G5 matrix, G6 response menus, G7/G8 by construction).

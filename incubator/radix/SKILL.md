---
name: radix
description: |
  radix comprehends a heterogeneous artifact — text, code, image/video-scene — and produces
  grounded, judged Frames on the Construct, then STOPS. ONE comprehension ontology (Base /
  Relations / Models over a raw Surface) applies to any modality; only the instantiation differs.
  Every comprehension is traceable to source (grounding-chain) and judged as real integration
  rather than schema-shaped confabulation (judgement). radix does not rank, does not decay, does
  not delete — relevance (ilm) and eviction (REP-021) are downstream.

  Use when: an artifact must be comprehended into structured, provenance-carrying Frames that
  survive sessions; you need the one-ontology breakdown of a text / codebase / image; you are
  building the consolidated radix and need the authoritative charter, ontology, and temporal
  axis. NOT for ranking, retrieval-scoring, or memory-eviction — those are out of scope by
  ratified boundary (D-JUDGE).

  Status is honest and mixed: text modality BUILT (radix-text v2); code and vis BUILT as
  separate tracers; the consolidated gnx build is DESIGN-stage. Several load-bearing decisions
  are LEANING or OPEN (third-frame name OD-1; temporal axis; tiered Anchor). Read the context
  files before treating any of it as settled.
---

# radix — compositional comprehension across heterogeneous artifacts

> **Honesty markers used throughout.** **[DECIDED]** = ratified ground, state it flatly ·
> **[LEANING]** = principal's current lean, NOT closed — write it as a lean, record the
> alternative · **[OPEN]** = unlegislated, the build must not silently resolve it · **[BUILT]**
> = shipped in a prototype · **[UNBUILT]** = designed, no code. Authority order on any conflict:
> this-session principal input > `~/mox/research/meta/decisions.md` OD rulings > the 2026-06-09
> proposal > the 2026-06-02 common model > the prototypes.

radix takes an **Artifact** (text, code, image/video-scene) and produces **grounded, judged
Frames on the Construct** — comprehension, each Frame traceable to source and judged as real
integration rather than confabulation — **and stops.** [DECIDED, D-JUDGE]

That last clause is load-bearing. radix comprehends and stops. Relevance/ranking (ilm) and
decay/eviction (REP-021) are downstream; radix introduces no downstream vocabulary, but the
Construct interface must not preclude them. See `context/00-charter.md` §Boundary.

---

## What this component is

This directory is the **context substrate** for the FINAL, brand-new radix — the one
authoritative home the real build proceeds from. It consolidates the finalized discussions and
decisions. It is not itself the running radix; the three built prototypes live elsewhere
(paths below). `maturity: design` in the manifest is the single most important honesty marker:
the consolidated build is design-stage, not shipped.

**The one-ontology thesis (central).** ONE comprehension ontology comprehends any artifact in
any modality. The **same** breakdown — Surface → Base / Relations / Models, one grounded
settling — applies to text, code, and image/video; only the *instantiation* differs. This is
not asserted, it is **convergent**: the identical Frame shape, `arises_from` provenance
relation, grounding-chain integrity, and two gates already survive three built modalities.
[DECIDED as a common model; corroborated as-built in all three prototypes — but the
*consolidated* multimodal build is UNBUILT.]

---

## Read these, in order

The context files carry the authoritative detail. Do not act on the ontology from memory.

1. **`context/00-charter.md`** — what radix is, the one-ontology thesis, the wager it serves,
   the boundary in one line, and the two-gate discipline at a charter level.
2. **`context/01-ontology.md`** — the corrected Surface / Base / Relations / Models layers
   (this SUPERSEDES the older Kintsch surface-form/textbase/situation-model mapping), the
   Model-KINDS family, the 10 core entities, the tiered Anchor, the Status union, HonestFailure.
3. **`context/02-temporal-axis.md`** — the snapshot-vs-history orthogonal temporal axis
   (NEW this session): two distinct outputs (HISTORY frame + SNAPSHOT frame) over Base/
   Relations/Models, per-modality realization, and the net-new design work.

---

## The shape in one screen

- **Surface** [DECIDED] — the raw artifact content (text bytes, pixels, source code). The
  substrate UNDER Base; a field on the Artifact, **not** a frame.
- **Base** (tamas / substance) [DECIDED] — extraction of core claims / propositions /
  structures FROM the surface. NOT surface form.
- **Relations** (rajas / relation) [DECIDED] — how Base entities relate. Two scopes:
  intra-artifact binding, and cross-artifact (far end **grounded** or **dangling** — both
  represented; dangling is honest, not dropped).
- **Models** (sattva / integration) [LEANING name — OD-1 OPEN] — the integrated grounded
  comprehension, in whatever form fits (schema, situation model, programming model, principle).
  A structured object, never a bare English sentence.

**Two gates, both in radix, neither covers the other** [DECIDED, D-JUDGE]:
- **Gate 1 — grounding-chain** (part-existence; cheap; write-time; modality-blind). The chain
  Model → Relations/Edge → Base → Artifact must hold.
- **Gate 2 — judgement** (configuration-integrity; expensive; assay-time). Is the integration
  load-bearing, or schema-shaped confabulation?

**Snapshot vs History** [axis DECIDED this session; UNBUILT as an axis] — an orthogonal
temporal axis OVER Base/Relations/Models. For the same artifact, radix must produce BOTH a
HISTORY frame (how/why it became what it is) AND a SNAPSHOT frame (what it is now). NOT a fourth
frame. See `context/02-temporal-axis.md`.

---

## Boundary — what radix must NOT do [DECIDED, load-bearing]

| Concern | Owner | radix must not |
|---|---|---|
| Relevance / ranking | ilm (query-time) | rank, compute relevance, name `ResonanceRanker` |
| Decay / eviction | REP-021 | decay, evict, or **delete** — the ledger is immutable; eviction only moves tiers |
| Recency-as-ranking | ilm | compute or store a recency scalar |

radix introduces no downstream vocabulary, but the Construct interface must not preclude a
downstream ranking Component, shared-Construct reading, or a `recall_events` ledger.

---

## Prototype inventory (honest state)

| Prototype | Modality | State | Location |
|---|---|---|---|
| **radix-text v2** | text/conversation | **BUILT**, 81 tests — the conformance baseline | `~/mox/research/tools/radix-text/` |
| **radix-code** | code/Rust | **BUILT**, 391 frames — the only temporal exercise | `~/radix-workspaces/rust-mastery/tools/src/radix/` |
| **radix-vis** | image/video-scene | **BUILT** tracer, partial; explicitly NOT canonical | `~/mox/platform/mox.studio/tools/radix-vis/` |
| **vaani** | text parse engine | candidate, not a radix prototype; radix does not yet use it | (REP-019) |

Design authority: `~/mox/research/meta/designs/2026-06-02-radix-multimodal-common-model.md` and
`2026-06-09-radix-multimodal-proposal.md`. Ledger: `~/mox/research/meta/decisions.md` and
`~/mox/research/meta/missions/radix-od-queue.md`.

---

## Open decisions the build must not silently resolve

- **OD-1** — third-frame name. Leaning KEEP `Models`; NOT closed. The comprehension-ontology
  dossier recommends `Construal` (Langacker); `Interpretant` (Peirce) was #2; `Interpretations`
  was proposed 2026-06-09 and REVERTED this session. Recorded, not resolved.
- **OD-4 / D-B2** — content-hash terminus for the grounding chain / Anchor (pending confirmation).
- **OD-3** — claim→Frame rejection aggregation, Episode verdict enum, halt semantics.
- **OD-10** — ecosystem shape (L2 processor vs L3 component; gnx placement). Directly bears on
  this component's `kind` and manifest topology; UNRULED. Do not harden the manifest `kind`.
- **The temporal axis** (`snapshot|history` selector over B/R/M) — net-new; no OD covers it yet.

See `context/00-charter.md`, `context/01-ontology.md`, and `context/02-temporal-axis.md` for the
full open-question register.

---

## Provenance

Consolidated 2026-07-04 from the authoritative radix spec (integrator's consolidation of the
2026-06-02 common model + 2026-06-09 proposal + this-session principal input), the three built
prototypes (radix-text v2, radix-code, radix-vis), and `~/mox/research/meta/decisions.md`. The
Kintsch surface-form/textbase/situation-model mapping is explicitly retired (see
`context/01-ontology.md` §Kintsch retirement). Honest about status: multimodal unification is a
convergent common model corroborated across three prototypes, but the consolidated build is
DESIGN-stage; OD-1 is OPEN; the temporal axis is UNBUILT as an axis.

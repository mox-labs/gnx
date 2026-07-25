# 04 — The Construct

> Honesty markers: **[DECIDED]** · **[LEANING]** · **[OPEN]** · **[BUILT]** · **[UNBUILT]**.
> Source of truth: consolidated radix spec §4. Text modality is **[BUILT]** (radix-text v2, the conformance baseline); the tiered Anchor and event-sourced ledger are **[DECIDED-in-design, UNBUILT]**.

The Construct is where comprehension accumulates. It is a **typed blackboard**, not a bag of dicts, and its shape is what keeps the gates (`03-gates.md`) and the Compositor modality-blind.

---

## 4.1 The typed blackboard

A HEARSAY-II / BB1 blackboard. **Components compose through the board, never by calling each other.** The runner sequences them; each component reads the typed rows prior stages wrote and writes its own. [BUILT — radix-text, SQLite + in-memory backends, swap-test sound]

This inversion is load-bearing. A fused `extract()` (as in radix-vis) is a **degenerate one-shot board** — it cannot express *"the RelationExtractor reads what three modalities' Segmenters wrote,"* so it cannot do cross-modal in-Frame relations. The board is the mechanism that makes cross-modal relations expressible at all.

### Event-sourcing delta [DECIDED — proposal §4 delta 3, UNBUILT]

The Construct is an **append-only ledger** of typed frames plus per-domain gated projections. The mutable `ConstructStore` becomes a **rebuildable cache outside the provenance chain** — the common model's "SQLite typed tables" are re-cast as a *projection* of the ledger.

- Scheduling: Phases 1–4 run on the mutable store; the ledger lands as Phase 5b's substrate.
- Consequence: **nothing is ever deleted.** Eviction (downstream, REP-021) only moves tiers; the ledger is immutable.

### Two scopes of "the Construct" [OPEN — seam C6]

- **narrow** — radix's own append-only typed-frame blackboard. *This document's scope.*
- **wide** — the inhabited coordination-ledger of which radix is one Domain / private projection (`Artifact : Samsara :: Frame : radix-Construct`).

Merge-conflict ownership when geist (immutable-snapshot + merge) and radix (append-only) both touch one mark is **OPEN**. Do not resolve it here.

---

## 4.2 The ten entities

Typed rows. **Do not re-invent these — they are cross-cosmos types.** [DECIDED]

| Entity | Role | Modality axis |
|---|---|---|
| **Episode** | one bounded comprehension run | invariant |
| **Artifact** | content-hashed object of attention; **grounding terminus**; carries `decode_descriptor` / `parse_descriptor` when non-text | invariant shape |
| **Record** | heavy modality payload (Lab arrays, flow fields, CST, spectrograms); content-addressed **sidecar**; cost-tiered; **not in the grounding chain** except as a pinned T2/T3 witness target | invariant shape, polymorphic payload |
| **Frame** | one comprehension act — a **thin row** (scalar slots + FKs + `Status` + `meta`); structured slots live in their own tables | **invariant** |
| **BaseUnit** | addressable structural unit (turn / region / shot / file / AST-node); thin, **no payload** | polymorphic in `kind` |
| **Edge** | a Relation; typed (source / target / kind / anchors); queryable | invariant |
| **Model** | integrated comprehension — a **structured object** (`head · head_entity_id · model_type · schema_kind · discourse_mode · frame_elements · constraints · anchors · parent_model_id`) | polymorphic in `schema_kind` |
| **Entity** | the surfaced canonical subject a Model is *about* — **not a string** | invariant |
| **Anchor** | the grounding atom (§4.3) | polymorphic in `locator` |
| **FrameView** | query-time aggregate of Frame + slots; **never stored** | invariant |

**Headline invariant [DECIDED]:** the Frame is invariant and thin. Variance lives in exactly three typed, registered satellites — `BaseUnit.kind`, `Anchor.locator`, `Model.schema_kind` — referenced **through ports**, not inlined on the Frame. The right axis is *"stored on the Frame vs referenced through a typed port,"* not *"generic vs specific."*

### Storage [DECIDED]

Hybrid:

- SQLite typed tables for the queryable skeleton — `O(log N)`.
- Content-addressed binary **sidecar** for heavy payload; the Frame carries `record_hash`, never the array itself.
- Cost-tiered Records: `Record.tier ∈ {cheap, expensive, pinned}`, **cost-weighted eviction, not TTL.** This is verification-audit tiering — **not** downstream decay (that is REP-021; see `07-boundaries` in the framework doc).
- JSON-manifest `rglob` (`O(N)` per query) was **rejected.**
- Cross-artifact / cross-modal index is **not deferrable**: `O(N²) → O(N·k)` via a per-modality index (token inverted-index for text/code; metric / LSH for image).

---

## 4.3 The tiered Anchor — the keystone

[DECIDED target; **UNBUILT** — only the char-only `SpanRef` is built]

An Anchor is a **closed discriminated union over locators**, tier-tagged, witness-carrying. **Never a string. Never `Any`.**

```
Anchor:
  artifact_id : str        # I6: terminates at Artifact identity AND ∈ Frame.arises_from
  unit_id     : str | None
  locator     : Locator    # modality-polymorphic CLOSED sum, tier-tagged:

  T1 — byte-identity   (witness = source bytes alone; correct BY CONSTRUCTION)
    CharSpan  { char_start, char_end }
    RecordRef { record_hash, selector? }
    FrameRef  { frame_id, dotted_path }        # cross-frame structural grounding (radix-code path-(a))
  T2 — decode-relative (witness = bytes + PINNED content-hashed descriptor)
    BBox      { x, y, w, h, frame_index?, decode_descriptor_hash }
    TimeRange { t_start, t_end, decode_descriptor_hash }
    NodePath  { path, parse_record_hash }
  T3 — perceptual      (witness = bytes + phash + tolerance τ; ONLY when byte-hash unavailable)
    PerceptualRegion { ..., phash, tolerance }
```

**Three shared invariants:**

1. Terminates at `artifact_id`, and that id **∈ the Frame's `arises_from`** (this is **I6** — load-bearing the moment Frames go cross-artifact).
2. Carries its tier's witness.
3. `resolve(source) → verbatim excerpt | typed-failure` — **never resolves silently** to a wrong terminus.

**A single grounding boolean across tiers is FORBIDDEN.** [DECIDED]

- **T1** is correct by construction (byte-identity).
- **T2** is correct only *relative to a pinned content-hashed decode/parse descriptor*; a mismatch raises `UnresolvedReference` / `Stale`.
- **T3** reports its tier and the achieved perceptual distance; it **never masquerades as T1.**

### The circular-grounding hole this closes

radix-vis and radix-code ground by walking the **stored derived structure** (`base:dotted.path` into a mode-written dict). That proves a claim consistent with *itself*, not with source — **circular and unsound.** [DECIDED to fix]

An Anchor must denote a region of an **Artifact** (or a content-hashed Record pinning the Artifact), and the assay must **re-read and re-derive from source** — never walk the cached output.

### grounding ≠ composition [DECIDED]

radix-vis/code fused these into one `grounded_in: list[str]` (the circular defect). They are kept **separate**:

- Anchors **ground** — `Anchor → Artifact`.
- `parent_frame_id` + `Edge` **compose**.
- **`FrameRef` is the one Anchor variant that bridges** the two (it preserves radix-code's path-(a) grammar).
- `arises_from` is the **uniform composition relation.** Integration-across-sources is itself a Frame, recursively — within-modality cross-artifact *and* across-modality.

### Staging [DECIDED]

First second-modality Anchor is **code** (`NodePath`, one step off the char-span). **Image is deferred** behind `exp_anchor_tier_verifiability`: it adds a decode-descriptor *and* lossy hashing — two variables at once. T2 mismatch-detection must be **100%** or block; T3 needs τ at **≤1% false-accept** or its claims are excluded from the asserted set.

Anchor migration is rooted on `D-B2` (content-hash terminus), which is **pending confirmation via `OD-4`.**

---

## 4.4 The Status union — three states

[DECIDED — `meta/decisions.md` 2026-06-14, OD-2(b)]

```
Status =
  | Unjudged     # exists; grounding not established
  | Asserted     # grounding-chain AND judgement both pass — the ONLY readable-as-defended state
  | Dissolved    # judged and no integration survived — TERMINAL, RETAINED, never deleted
```

- **[BUILT]** exactly this: `frames.grounding ∈ {unjudged, asserted, dissolved}`; typed `GroundingStatus{UNJUDGED, ASSERTED, DISSOLVED}` (replaced `asserted:bool`; principal-entrenched 2026-06-07; 81 tests).
- **`Grounded` is a downstream (ilm) status, not radix's.** OD-2(b): radix introduces neither `Grounded` nor a fourth state; the three-state union is retained; `Grounded` is written **downstream.** This **supersedes** the common model's §2.6/§4 four-state union (CM §4 owed a superseded-by pointer).
- The union makes illegal states unrepresentable. radix-code's two `bool | None` flags admitted **9 states for a 4-state machine** — 5 of them illegal.
- `Dissolved` ≠ "we haven't looked yet," and is **never deleted.**
- **`Avowed` / `Warranted`** are downstream / unratified (samsara's Grounding ⊥ Warrant axes). radix introduces neither. Whether `Avowed` — a human's out-of-distribution conception — ever enters radix's union, or only via human authorship *outside* radix's gates, is **[OPEN]**, folded into OD-2's stated scope.

---

## 4.5 HonestFailure

[DECIDED; **[BUILT]** for text and vis]

**Doctrine: settle, don't reject the Frame.** *"The architecture refuses to hallucinate."* Motivated by ColorBench 2025 — VLMs cannot reliably extract `#ff6666` vs `#ff4477`, so a comprehension architecture must fail honestly rather than confabulate the difference.

Three-tier posture:

| Level | Failure | Posture |
|---|---|---|
| Mode / extractor | crashes mid-extract | `HonestFailure(DegradedBase)`, **continue with survivors** |
| Claim | ungrounded / training-producible | reject the **claim** into `rejected_claims` (with `would_have_grounded_in`); **the Frame survives** |
| Frame | grounded but no integrating structure | `Dissolved(reason)` — retained, **never deleted** |
| Cross-stream | parallel streams disagree on timeline | `HonestFailure(DesyncRealization)`, both retained (new; A/V) |

**Kind-enum FROZEN** at:

```
DegradedBase · UnresolvedReference · Stale · DissonantRealization · RejectedClaim · DesyncRealization
```

- **Sensor-specific failures go in `reason`, never as new kinds** — otherwise the N×M explosion creeps back. First concrete migration item: retire radix-vis's `DegradedColor` / `DegradedFlow` / `UnresolvedSymmetry`, demoting them to `reason` strings.
- **`UnresolvedReference` IS the canonical DANGLING cross-artifact-edge representation** — the honest form of an edge whose far end is not grounded (its far artifact ∉ `arises_from`).

---

## Provenance

- Ledger / OD rulings: `/Users/yza.vyas/mox/research/meta/decisions.md` (OD-2, OD-4, D-B2).
- Built domain model: radix-text v2 at `/Users/yza.vyas/mox/research/tools/radix-text/` (the conformance baseline).
- Design authority: `/Users/yza.vyas/mox/research/meta/designs/2026-06-02-radix-multimodal-common-model.md`, `2026-06-09-radix-multimodal-proposal.md`.
- The four-state union in the common model §2.6/§4 is **superseded** by the three-state union here (OD-2(b)).

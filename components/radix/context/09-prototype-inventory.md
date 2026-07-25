# 09 — Prototype Inventory

Three prototypes exist. **None is the canonical radix.** The consolidated `gnx/components/radix/` is a **design/context substrate** (`maturity: design`), built *from* these three, not one of them promoted. This file records, honestly, what each built, what it proves, and what it does and does not carry forward.

Status legend: **[BUILT]** shipped code · **[UNBUILT]** designed, no code · **[DECIDED]/[OPEN]** decision status.

Source paths:
- radix-code — `~/radix-workspaces/rust-mastery/tools/src/radix/`
- radix-text — `/Users/yza.vyas/mox/research/tools/radix-text/`
- radix-vis — `~/mox/platform/mox.studio/tools/radix-vis/`

---

## 9.1 radix-code (Rust) — the diachronic reference; the ONLY temporal exercise

**State: BUILT to corpus close.** [BUILT] 12/12 milestones (git `c4cee6a`). **391 frames on disk; 389 load** (2 dropped by a silent-skip loader — the 2026-06-09 proposal records this defect). The sub-breakdown (266 file / 69 crate / 43 cross-artifact / 9 milestone / **4 temporal**) is **recon-derived** (this-session read of the prototype's `framework.md` §6, logged in `meta/sessions/agent-timeline.jsonl`), **not a durable persisted count** — `radix.db` is 0 bytes and no frame JSONs remain on disk. It sums to 391 and the load-bearing sub-claims (43 cross-artifact, 4 temporal) are what the recon substantiated; treat the exact partition as indicative, not audited.

**Carries forward:**
- The **only** place the temporal axis was exercised on real artifacts — 4 gate-passing temporal frames.
- `archaeology.py` (`commit_at` / `tree_at` / `file_log` / `find_revert_oscillations`) + codemaat backfill. Snapshot identity = git **tree-hash**.
- **43 cross-artifact frames = grounded far ends; `UnresolvedReference` = dangling far end.** Both cross-artifact scopes (grounded and dangling) were built **here, and only here**.
- Both gates built exactly as **D-JUDGE** specifies.

**Does NOT carry as-is:**
- Temporal modeled as a **peer SCALE**, not an orthogonal axis — and the least-exercised scale (4 frames vs 266). It proved the diachronic material is gate-passable; it did **not** conceive snapshot + history as dual outputs of one axis. See `context/02-temporal-axis.md` §crux.
- `archaeology.py` operates on git commits/tags/versions **only** — PR-rationale entered via recon-ingested issue/PR artifacts, not through `archaeology.py`.
- `radix.db` is **0 bytes** — enrichment ran out-of-band. Rebuild the driver; carry the primitives.
- `Scale` is a hard-coded, modality-mixed enum → de-hardcode to a per-modality `supports(kind)` registry.
- Judgement redundancy heuristic is Rust-specific (the per-modality calibration seam, not a defect).
- **Models-as-sentence** (Anemic-Model defect, §10-D) — `model_claims` emit English sentences into the model layer.

---

## 9.2 radix-text v2 (conversation) — the reference implementation

**State: BUILD COMPLETE / SHIPPED.** [BUILT] M0′→M5 all PASS (2026-05-30); 77→**81+ tests green** (`asserted:bool` → typed `GroundingStatus`, principal-entrenched 2026-06-07). Branch `feat/radix-text-v2`. **This is the conformance baseline.**

**Carries forward** — the whole spine the finalized radix inherits:
- The domain model + the blackboard inversion (**HEARSAY-II / BB1**).
- Two-axis Source ⊥ Format design (**N + M, not N × M**).
- `ParseSidecar` **recompute-don't-persist** discipline.
- Structured **Model** (head + slots + constraints + anchors) — the non-anemic model done right.
- The **3-state Status slice** OD-2(b) retains (`{Unjudged, Asserted, Dissolved}`).
- The three v1 defects (**33× bloat, Anemic-Model, subject-as-string**) are structurally prevented and asserted in tests.

**Honest gaps:**
- The temporal snapshot/history axis is **entirely ABSENT** [UNBUILT]. `Frame.scale = turn/segment/conversation/corpus` — no diachronic frame; vaani parses a snapshot only.
- Cross-artifact grounded-vs-dangling far-ends are **NOT modeled** — `SpanRef` carries a single `artifact_id` terminus; a dijkstra pass flagged the cross-artifact span pipeline-unreachable (a future one-line assert).
- Anchor tiers are **UNBUILT** — only **T1 `CharSpan`** exists, built as `SpanRef`.

---

## 9.3 radix-vis (visual) — strongest one-ontology evidence + partial temporal

**State: tracer-bullet, installable CLI** [BUILT, partial] at `~/mox/platform/mox.studio/tools/radix-vis/`. **Explicitly NOT canonical radix.** Ships `version` / `status` / `distill` / `distill-sequence`.

**Carries forward:**
- The **identical `FrameMode{BASE, RELATIONS, MODELS}`** (NOT Kintsch) — the strongest evidence the ontology survives a radically non-textual modality.
- **Surface-UNDER-Base named explicitly** — its 4-layer chain `Models → Relations → Base → Substrate` is direct corroboration of the principal's SURFACE-under-BASE correction (radix-vis calls it "Substrate"; canonical word is **Surface**).
- The **adjacency dual-output**: `symmetry_emergence` (HISTORY) + `symmetry_terminal` (SNAPSHOT) from **one pass**, narrator preferring the terminal — the **closest prior art** to the dual-output temporal requirement (`context/02-temporal-axis.md`).
- 8-kind honest-failure + partial-settling.

**Honest gaps:**
- Cross-artifact composition is **UNBUILT** — `FrameKind.CROSS_ARTIFACT` is declared but there is no compose command/reducer; grounded-vs-dangling is only foreshadowed by `UnresolvedReference`.
- Temporal modeled as a sibling `ArtifactKind` / `FrameKind.SEQUENCE`; `FrameKind.TEMPORAL` is **declared but unmapped** in `_FRAME_KIND_BY_ARTIFACT` [UNBUILT].
- `GroundedClaim(claim: str)` is **anemic** (Models-as-sentence, §10-D).
- The corpus is **circular by the decided grounding standard** — grounding walks the stored derived structure, not the source (→ **OD-7**: regenerate vs retire).
- **Missing authority doc** — code cites `.radix/framework-amendments.md §A1-A7`, **but the file does not exist**. The amendments live only in code + SCOPE. The consolidation must **lift the content**, not cite the doc.

---

## 9.4 vaani — text parse engine (candidate, NOT a radix prototype)

A parse-stage engine for **TEXT only**. **radix does not currently use it** — radix-text has zero vaani imports; `RuleModelInstantiator` is a deterministic-regex placeholder. Ships **2 of 10** REP-019 §4 deterministic no-LLM primitives; the other 8 are UNBUILT-but-buildable (implementation, not inquiry). **D-B3** fixes its entry (parse-stage Component on the board → RecordStore, swappable behind `NlpProvider`; see `context/06-modality-registry.md`). ~70–80% engineering + a small design pass — a **BUILD track, not a research mission**. Silent on the temporal axis.

---

## 9.5 Carry-forward matrix

| Capability | code | text | vis | → gnx/radix as |
|---|---|---|---|---|
| Base/Relations/Models, one settling | ✅ | ✅ ref | ✅ | invariant core |
| Two gates (D-JUDGE) | ✅ | ✅ remove-constituent | doctrine, assay UNBUILT | invariant |
| Construct blackboard | partial | ✅ HEARSAY-II ref | partial | invariant (from text) |
| Surface-UNDER-Base | implicit | implicit | ✅ explicit | **corrected model confirmed** |
| Honest-failure / dangling | ✅ `UnresolvedReference` | edge `<no-binding>` | ✅ 8 kinds | invariant (frozen 6-kind enum) |
| Temporal snapshot + history | ✅ peer scale (4) | ❌ absent | ✅ both readings, sibling kind | **lift to orthogonal axis — net-new** |
| Cross-artifact grounded/dangling far-end | ✅ 43 frames | ❌ single terminus | ❌ declared, UNBUILT | **new machinery** |
| ONE ontology across modalities | code | text | visual | **thesis survives 3 modalities** |

**Read the matrix honestly.** The one-ontology thesis is **corroborated across three modalities** — that is real, and it is the central evidence. But the two net-new capabilities the finalized radix requires — the **orthogonal temporal axis** and **first-class cross-artifact grounded/dangling far-ends** — are each built in **at most one** prototype and never together. They are design-and-build work, not consolidation. Do not present the finalized multimodal radix as built. It is not.

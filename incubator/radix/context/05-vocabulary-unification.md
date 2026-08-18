# 05 — Vocabulary Unification & Rename Map

> Honesty markers: **[DECIDED]** · **[LEANING]** · **[OPEN]** · **[BUILT]** · **[UNBUILT]**.
> Source of truth: consolidated radix spec §5. The three prototypes each grew their own nouns; this is the single canonical set they migrate onto, plus the per-prototype rename map and the migration ordering. The third-frame name (`Models`) is **[LEANING], not [DECIDED]** — `OD-1` is open.

The prototypes converged on the same ontology under different names. Unification is mostly **mechanical rename** — but two things are not mechanical: the third-frame name (OD-1) and the KINDS ⊥ LENSES split. Both are called out below.

---

## 5.1 Canonical noun set

**Four frame-internal views (the ontology):**

- `Surface` — [DECIDED] the substrate *under* Base (raw bytes / pixels / source), a field on `Artifact`/`Record`, **not a frame**.
- `Base` — [DECIDED] extraction of core claims / propositions / structures from Surface.
- `Relations` — [DECIDED] how Base entities relate; intra-artifact and cross-artifact.
- `Models` — **[LEANING]** name (OD-1); the integrated grounded comprehension.

**Ten core entities:** `Episode · Artifact · Record · Frame · BaseUnit · Edge · Model · Entity · Anchor · FrameView` (see `04-construct.md`).

**Model-KINDS:** `Schema · Script · Plan · Scene · SituationModel · Pattern (+ Principle)` — lineage names kept **regardless of how OD-1 rules on the genus name.**

**Eight ports:** driving `ArtifactSource`; per-modality `Segmenter · RelationExtractor · ModelInstantiator`; invariant `Compositor · GroundingGate · JudgementGate`; cross-cutting `GroundingResolver · ConstructStore · RecordStore`. Registry-bound (NOT ports): `Narrator`, unit-floor declaration. (Detailed in `06-modality-registry.md`.)

**Status:** `{Unjudged, Asserted, Dissolved}`. **HonestFailure:** frozen 6-kind. **Grounding atom:** tiered `Anchor`. (All in `04-construct.md`.)

**Temporal-axis vocabulary — [OPEN], net-new, no canonical noun coined yet.** `snapshot` vs `history` is an orthogonal axis **over** B/R/M. The spec recommends a selector `temporal_view ∈ {snapshot, history}` on the Frame. radix-vis adjacency (`symmetry_emergence` = history, `symmetry_terminal` = snapshot) is the closest as-built realization. Do not present this as a coined noun — it is a recommendation. (See the temporal-axis context doc.)

---

## 5.2 OD-1 — the third-frame name

**Ruling: KEEP `Models`. [LEANING] — OD-1 is NOT formally closed.**

- The 2026-06-09 rename to `Interpretations` is **REVERSED this session.**
- Live ledger: OD-1 OPEN, gates the Phase 0 deposit PR (`/Users/yza.vyas/mox/research/meta/decisions.md:21`).

**Divergence recorded honestly (do not paper over it):**

- The comprehension-ontology dossier **recommends `Construal`** (Langacker) — ranked #1 as genus-clean and collision-free — with `Interpretant` (Peirce) at #2. It disqualified `Models` **only** on the ML-collision.
- The principal **leans `Models`.**

**Ground the lean stands on:**

- **Dual-root.** `Models` is the shared root of the field's two canonical terms — situation *model* (Kintsch) *and* mental *model* (Johnson-Laird). Its only real defect is the ML-collision; accept it.
- **As-built favors keeping.** All three prototypes already use `Models` / `model_type` in code. Keeping the name makes the third-mode vocabulary delta **zero** and confines OD-1's remaining work to mechanical port/field renames.

**Spec instruction:** record the third frame as `Models` (leaning); note OD-1 open; note `Interpretations` was proposed-and-reverted, and that `Construal` / `Interpretant` were the alternatives. Keep the **dynamical-interpretant** gloss (Peirce: a radix Model is revisable and gate-relative) in the docs whatever the final label.

---

## 5.3 Per-prototype rename maps → canonical

### radix-code  (`~/radix-workspaces/rust-mastery/tools/src/radix/`)

| Current | Canonical | Note |
|---|---|---|
| `ast.py` CST output | **Base** via `Segmenter` | |
| `cargo.py` graphs | **Relations** via `RelationExtractor` | grounded in cargo / rustdoc Records |
| `ModelClaim` / `model_type` | **Model** (structured) | keep name; `model_type` → name a KIND (§10-E) |
| `grounded_in: list[str]` (fused) | **Anchor** (grounds) + `Edge` / `parent_frame_id` (composes) | `FrameRef` bridges |
| `scale='temporal'` Frame | **`history` view** (orthogonal axis) | lift out of peer-scale |
| `judgement.py` | **JudgementGate** | `_REDUNDANT_PATTERNS` → per-modality calibration config |
| `validators.py` | **GroundingGate** | |
| `archaeology.py` | temporal-axis raw machinery (retained) | machinery, not a noun |
| `Scale` Literal | per-modality `supports(kind)` registry | de-hardcode |

### radix-text  (`/Users/yza.vyas/mox/research/tools/radix-text/`, BUILT — conformance baseline)

| Current | Canonical | Note |
|---|---|---|
| `BaseBuilder` | **Segmenter** | mechanical rename |
| `ParseSidecar` (port) | **RecordStore** | |
| `GroundingStatus` | **Status** (decided *inside* OD-1) | grounding-named field carries judgement verdicts — open sub-item of OD-1 |
| `SpanRef` (single `artifact_id`) | **Anchor** (tiered); char case → `CharSpan` | generalise |
| `RuleModelInstantiator` / `ModelInstantiator` | **ModelInstantiator** (rule + LLM, one port) | keep |
| `BaseUnit` / `Edge` / `Model` / `Entity` / `Frame` | unchanged | already canonical |
| Schema family | Model-**kind** `Schema` | kind, not genus |
| `Component` stages | canonical **port** stages | via blackboard `ctx` |
| `Frame.scale` = turn/segment/conversation/corpus | per-modality `supports(kind)` registry | |

### radix-vis  (`~/mox/platform/mox.studio/tools/radix-vis/`)

| Current | Canonical | Note |
|---|---|---|
| `structural.py` / `palette.py` extract | **Base** via `Segmenter` | CV primitives at coords |
| fused `extract()` | unfuse → **Segmenter + RelationExtractor + ModelInstantiator** | the fusion forecloses the rule ⇄ LLM swap |
| `GroundedClaim(claim:str)` | **structured Model** + `Narrator`-derived `abstract` | Anemic-Model defect |
| `MiningModeRole` / modes | **`supports(kind)` registry** per-STAGE | the pattern to keep |
| `narrators` | **Narrator** (registry-bound) | |
| `Substrate` (4th layer) | **Surface** | the principal's canonical word |
| `FrameMode.BASE/RELATIONS/MODELS` | unchanged | corroborates the triad in a non-text modality |
| `HonestFailure` 8 kinds | frozen 6-kind | 3 sensor-kinds → `reason` |
| `FrameKind.SEQUENCE` / `TEMPORAL` | **`history` / `snapshot` axis** over B/R/M | retire as a frame-kind; adjacency = axis realizer |
| `FrameKind.CROSS_ARTIFACT` | cross-artifact `Edge` + `FrameRef` Anchor | compose cmd unbuilt |
| `cortex` (downstream) | out of radix | boundary noun |

---

## 5.4 Mechanical rename inventory + migration ordering

[verbatim-authoritative — common-model §3 + proposal §5]

1. `BaseBuilder → Segmenter`
2. `ParseSidecar → RecordStore`
3. `GroundingStatus → Status?` (decided inside OD-1)
4. `SpanRef → Anchor`, char case → `CharSpan`
5. `GroundedClaim` / `model_claims → typed Model` + Narrator `abstract`
6. `grounded_in → Anchor + Edge / parent_frame_id`
7. config-`Component` / `_FRAME_KIND_BY_ARTIFACT → supports(kind)` registry
8. `Models → Interpretations` — **REVERTED; stays `Models`.**

**Migration ordering (Phase 1.1, by blast radius):**
renames → Status union → tiered Anchor T1 + `GroundingResolver` port → `supports(kind)` registry → `arises_from` → HonestFailure posture.

**Deposit discipline [DECIDED via OD-12]:** the OD-1 rename is deposited **by version, not in place** (framework v0.4 addendum). radix-code's **1,427 grounded claims** trace into the existing prose; an in-place rewrite would silently invalidate their termini. Code identifiers migrate only at the text-conformance step.

---

## 5.5 The KINDS ⊥ LENSES split (do not conflate)

Two [DECIDED] constraints on `Models`:

1. **"Schema" is a KIND, never the genus.** A schema is the prior-knowledge *type* that a situation model *instantiates* (type vs token). Neither `Schema` nor `Situation` may name the third frame.
2. **KINDS ⊥ LENSES — two orthogonal axes.**
   - A **KIND** (`Schema / Script / Plan / Scene / SituationModel / Pattern`) = the representational *form* — *"what shape?"*
   - A **LENS** (`system / programming / domain / deployment / operating` + rationale) = the *concern / projection* — *"which aspect?"*
   - "Situation" showing up in both lists is the symptom of the conflation.

**The lens taxonomy is explicitly [OPEN] and OFF the radix critical path** (routed to `drafts/mox-system-model`). **Do not block the ontology on it.**

**§10-E fix:** radix-code's `programming_model | design_model | deliberation_model` reads as a *lens / artifact-default*, not a representational KIND. Reconcile: `model_type` names a KIND from the KINDS set; `programming` / `design` / `deliberation` become a **lens or an artifact-kind default**, not a KIND.

---

## Provenance

- OD-1 ledger row: `/Users/yza.vyas/mox/research/meta/decisions.md:21`.
- Deposit-by-version: OD-12.
- Rename authority: `/Users/yza.vyas/mox/research/meta/designs/2026-06-02-radix-multimodal-common-model.md` §3 and `2026-06-09-radix-multimodal-proposal.md` §5.
- Comprehension-ontology dossier (recommends `Construal`): `drafts/comprehension-ontology/`.
- Built canonical baseline: radix-text v2 at `/Users/yza.vyas/mox/research/tools/radix-text/`.

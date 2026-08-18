# radix — The Ontology (Surface / Base / Relations / Models)

> Context substrate for the FINAL radix. Honesty markers: **[DECIDED]** · **[LEANING]** ·
> **[OPEN]** · **[BUILT]** · **[UNBUILT]**. Authority order on conflict: this-session principal
> input > `~/mox/research/meta/decisions.md` OD rulings > 2026-06-09 proposal > 2026-06-02 common
> model > prototypes.

---

## 1. The corrected layers — supersedes all Kintsch mappings [DECIDED, principal this session]

| Layer | Corrected definition | Guṇa | What it is NOT |
|---|---|---|---|
| **SURFACE** | The raw artifact content itself — text bytes, pixels, source code. The **substrate UNDER Base**, a field on `Artifact`/`Record`. **Not a frame, not a comprehension layer.** | — | Not Base; not Kintsch surface-form |
| **BASE** | Extraction of the core claims / propositions / structures **from** the surface. radix's stored *view* of the artifact. | tamas (substance) | **NOT surface form.** Not the raw bytes. |
| **RELATIONS** | How Base entities relate. Two scopes: **(a) intra-artifact** binding; **(b) cross-artifact** — Base-of-this relating to Base-of-other, far end **GROUNDED** or **DANGLING**. | rajas (relation) | Not intra-only |
| **MODELS** | The integrated grounded comprehension, in whatever form fits — schemas, situation model, programming model, principles. Multiple Model-kinds may apply. | sattva (integration) | **Not a bare English sentence** (Anemic-Model defect) |

**One settling, not three stages.** Base / Relations / Models are three co-cohering *views* of
one content-hashed object, produced together — not a three-pass pipeline. Every prototype carries
them as three slots of one Frame envelope. [DECIDED]

---

## 2. The Kintsch retirement — explicit scrub targets [DECIDED]

The consolidated spec **overwrites** the Kintsch surface-form / textbase / situation-model mapping
wherever it appears. Two named scrub targets in the research tree (do not edit them from this
component; recorded here so the build knows they are retired):

- `framework.md §1 line 52` — *"These map onto Kintsch's three Construction-Integration
  representations — surface form, textbase, situation model."* → **scrubbed.** SURFACE becomes
  substrate-under-Base; BASE becomes claim-extraction.
- `comprehension-ontology/synthesis.md L44-48` + Stream 1 table L30-34 — Base=surface-form/Record;
  Relations=textbase-microstructure (intra-text only); Models=situation-model. → **retired doubly**
  (Base was misplaced at surface-form; Relations was wrongly confined to intra-text).

**What survives and helps.** Rest radix's threeness on its **own** basis (Complementary Learning
Systems), citing Kintsch & Rawson p.210's own hedge ("convenient and conventional terms… we do
not claim… distinct or separate representations") rather than borrowing Kintsch's authority. The
situation model being defined as a-linguistic / modality-independent (Kintsch & Rawson p.211;
Zwaan & Radvansky) is independent landscape support for one-ontology-across-modalities.

---

## 3. Modality-instantiation table [reconciled from actual prototype realizations]

| | **BASE** (extraction from surface) | **RELATIONS** (intra + cross) | **MODELS** (integrated) |
|---|---|---|---|
| **TEXT / conversation** (radix-text, BUILT) | structural **BaseUnits** (turn/segment/heading/code_block/sub_artifact), char-range addressable; **no parse payload** (→ Record sidecar). ⚠ tension §7-A | Edges reply/entity_occurrence/mention/sub_artifact w/ grounding spans; **intra-only built**, cross-artifact far-end UNBUILT | Schema family (Definition/Enumeration/QA-Pair/OpenIssue), Fillmore-shaped structured Model. ✓ |
| **CODE / Rust** (radix-code, BUILT, 391 frames) | tree-sitter **CST** items + spans + metrics + grepped SAFETY/NOTE/TODO. ✓ | call/trait-impl/type-use/module graphs grounded in `cargo metadata` + rustdoc Records; **43 cross-artifact frames built**; dangling = `UnresolvedReference`. ✓ both scopes | `programming_model` claims. ⚠ Models-as-sentence (§7-D) |
| **IMAGE / video-scene** (radix-vis, TRACER) | CV structural primitives at coords: CIELAB palette, luminance peaks, polar-FFT symmetry, ridges, bloom. ✓ | symmetry-by-annulus, peak ordering, ΔE matrix (intra); cross-artifact **DESIGNED only** (no compose cmd/reducer) | `list[GroundedClaim]`. ⚠ anemic (§7-D); proto-Scene, unnamed |

### 3-A. What lands in Base for TEXT [OPEN — genuine tension, §10-A of the spec]

Principal's genus phrasing says text Base = "propositions / claims." As-built, radix-text's Base
is **structural BaseUnits** and propositions surface at Models (Schema). Code and image both align
with *structural* extraction. Text is where "propositions/claims" bites.

**The build must state which layer "extraction of core claims" means for text — do not leave it
ambiguous across two layers.** Two candidate resolutions, both live:
1. proposition-extraction belongs in **Base** (principal's genus phrasing); or
2. Base stays **thin-structural** and propositions emerge at **Models** (as-built, aligns with
   code + image).

Recorded as OPEN. Not silently resolved here.

---

## 4. The Model-KINDS family [family DECIDED; genus-name OPEN — OD-1]

Models is a **genus** parenting typed **KINDS** — the representational *form* the comprehension
takes:

| Kind | Lineage anchor | Status |
|---|---|---|
| **Schema** | DL/OWL, FrameNet (Fillmore), Bartlett → Rumelhart | BUILT (radix-text) |
| **Script** | SAM / MOPs (Schank) | designed, unbuilt |
| **Plan** | STRIPS / PDDL / HTN | designed, unbuilt |
| **Scene** | scene-graph | designed; plausible image/video kind (radix-vis GroundedClaims are proto-Scene) |
| **SituationModel** | Kintsch | designed, unbuilt |
| **Pattern / Principle** | radix-proprietary | radix-code `programming_model` is Pattern-adjacent |

Two DECIDED constraints:

1. **"Schema" is a KIND, never the genus.** A schema is the prior-knowledge *type* the situation
   model *instantiates* (type vs token). Schema / Situation must not name the third frame.
2. **KINDS ⊥ LENSES — two orthogonal axes.** A **KIND** (Schema / Script / Plan / Scene /
   SituationModel / Pattern) = representational form ("what shape?"). A **LENS** (system /
   programming / domain / deployment / operating + rationale) = concern / projection ("which
   aspect?"). "Situation" appearing in both lists is the symptom of conflation. **The lens
   taxonomy is explicitly OPEN and OFF the radix critical path** (routed to
   `drafts/mox-system-model`) — do not block the ontology on it.

**`model_type` conflates KIND and LENS today** [OPEN, §7-E]. radix-code's
`programming_model | design_model | deliberation_model` reads as a lens / artifact-default, not a
representational KIND. Reconcile: `model_type` names a KIND from the six above; programming /
design / deliberation → lens or artifact-kind default.

---

## 5. OD-1 — the third-frame name [LEANING — NOT closed]

**Ruling: KEEP `Models`. [LEANING] — OD-1 is NOT formally closed.**

- The 2026-06-09 rename to `Interpretations` is **REVERSED this session.**
- Live ledger: OD-1 OPEN, gates the Phase 0 deposit PR (`~/mox/research/meta/decisions.md:21`).
- **Divergence recorded honestly:** the comprehension-ontology dossier **recommends `Construal`**
  (Langacker; #1 genus-clean + collision-free), with `Interpretant` (Peirce) #2; it disqualified
  `Models` only on the ML-collision. Principal leans `Models`.
- **Ground the lean stands on:** dual-root — `Models` is the shared root of the field's two
  canonical terms (situation *model* / mental *model*, Kintsch AND Johnson-Laird). The only real
  defect is the ML-collision; accept it. As-built favors keeping: all three prototypes already use
  `Models` / `model_type` in code, so keeping it makes the third-mode vocabulary delta **zero** and
  confines OD-1's remaining work to mechanical port/field renames.
- **Instruction to the build:** record the third frame as `Models` (leaning); note OD-1 open; note
  `Interpretations` was proposed-and-reverted; note `Construal` / `Interpretant` were the
  alternatives. Keep the **dynamical-interpretant** gloss (Peirce: a radix Model is revisable,
  gate-relative) in the docs whatever the label ends up being.

---

## 6. The Construct and the 10 entities [DECIDED; text BUILT]

### 6.1 The Construct — a typed blackboard

A typed HEARSAY-II / BB1 blackboard: components compose **through the board**, never by calling
each other; the runner sequences them; each reads prior stages' typed rows and writes its own.
This inversion is what keeps the gates and Compositor modality-blind. A fused `extract()`
(radix-vis) is a degenerate one-shot board — it cannot express "the RelationExtractor reads what
three modalities' Segmenters wrote", i.e. cannot do cross-modal in-Frame relations. [BUILT:
radix-text, SQLite + in-memory backends, swap-test sound]

**Event-sourcing delta [DECIDED, proposal §4].** The Construct is an append-only ledger of typed
frames + per-domain gated projections; the mutable `ConstructStore` is a rebuildable cache
outside the provenance chain. The common model's "SQLite typed tables" become a *projection*.
Scheduling: Phases 1–4 run on the mutable store; the ledger lands as Phase 5b's substrate.

**Two scopes of "the Construct" [OPEN seam C6].** *narrow* = radix's own append-only typed-frame
blackboard (this component's scope); *wide* = the inhabited coordination-ledger of which radix is
one Domain / private-projection. Merge-conflict ownership when a snapshot-merge system and radix
(append-only) both touch one mark is **OPEN**.

### 6.2 The 10 entities (typed rows) [DECIDED — do NOT re-invent]

| Entity | Role | Modality axis |
|---|---|---|
| **Episode** | bounded comprehension run | invariant |
| **Artifact** | content-hashed object of attention; **grounding terminus**; carries `decode_descriptor` / `parse_descriptor` when non-text | invariant shape |
| **Record** | heavy modality payload (arrays, flow fields, CST, spectrograms); content-addressed **sidecar**; cost-tiered; **not in grounding chain** except as pinned T2/T3 witness target | invariant shape, polymorphic payload |
| **Frame** | one comprehension act — a **thin row** (scalar slots + FKs + `Status` + `meta`); structured slots in own tables | **invariant** |
| **BaseUnit** | addressable structural unit (turn / region / shot / file / AST-node); thin, **no payload** | polymorphic in `kind` |
| **Edge** | a Relation; typed (source / target / kind / anchors); queryable | invariant |
| **Model** | integrated comprehension — **structured object** (head · head_entity_id · model_type · schema_kind · discourse_mode · frame_elements · constraints · anchors · parent_model_id) | polymorphic in `schema_kind` |
| **Entity** | surfaced canonical subject a Model is *about* (not a string) | invariant |
| **Anchor** | the grounding atom (§8) | polymorphic in `locator` |
| **FrameView** | query-time aggregate of Frame + slots; **never stored** | invariant |

**Headline invariant.** The Frame is invariant and thin; variance lives in three typed,
registered satellites — `BaseUnit.kind`, `Anchor.locator`, `Model.schema_kind` — referenced
*through ports*, not inlined. The right axis is "stored on the Frame vs referenced through a typed
port", not "generic vs specific."

**Storage [DECIDED].** Hybrid — SQLite typed tables for the queryable skeleton (O(log N)); a
content-addressed binary sidecar for heavy payload (the Frame carries `record_hash`, never the
array); cost-tiered Records (`Record.tier ∈ {cheap, expensive, pinned}`, cost-weighted eviction
not TTL — this is **verification-audit tiering, not downstream decay**). A JSON-manifest `rglob`
O(N)/query scheme was rejected. The cross-artifact / cross-modal index is **not deferrable**
(O(N²) → O(N·k) via per-modality index: token inverted-index for text/code, metric/LSH for image).

---

## 7. The eight ports (the modality registry) [DECIDED]

No N×M explosion: a port is an interface (constant count); an adapter is a registration (linear
in modalities). Adding a modality = new adapter files behind existing ports.

- **Driving (1):** `ArtifactSource` — locator → `LoadedArtifact` (Artifact row + raw bytes/text +
  modality tag).
- **Driven per-modality (3):** `Segmenter` (≡ `BaseBuilder`; Artifact → BaseUnits + Entities +
  Record-refs; heavy payload → RecordStore), `RelationExtractor` (BaseUnits → Edge rows w/
  Anchors; may read several modalities' BaseUnits for a cross-modal Edge in ONE Frame),
  `ModelInstantiator` (Base + Relations → typed Model; rule-adapter and LLM-adapter behind the
  *same* port).
- **Driven invariant (3):** `Compositor` (unit-detection + ascent), `GroundingGate` (Gate 1,
  modality-blind), `JudgementGate` (Gate 2, pluggable strategy + per-modality calibration).
- **Cross-cutting (2 + 1):** `GroundingResolver` (registered by anchor-kind — the per-tier
  expensive witness recompute; **the genuinely-new multimodal port**), `ConstructStore`
  (typed-row persistence, SQLite ⇄ memory, modality-blind), `RecordStore` (≡ `ParseSidecar`
  renamed; content-addressed heavy sidecar, cost-tiered, never in grounding chain).
- **Registry-bound, NOT ports:** per-kind `Narrator` (renders `abstract` from typed Models),
  unit-floor declaration (moved out of the core reducer into the registry — closes radix-vis's
  `_FRAME_KIND_BY_ARTIFACT` core-leak).

### The four seams — where modality enters

| Seam | What varies | Port / locus |
|---|---|---|
| Segmenter (unit-floor) | pixels → regions / tokens → CST / turns / shots | `Segmenter` |
| Anchor + resolver | grounding atom + per-tier verifier | `Anchor.locator` + `GroundingResolver` |
| ModelInstantiator | comprehension organ (rule ⇄ LLM) | `ModelInstantiator` |
| Judgement **calibration** | thresholds / redundancy-patterns (gate *structure* invariant) | config behind `JudgementGate` |

**Falsifiable boundary [mechanically checkable].** Adding a 6th modality touches **zero** files in
`{runner, ConstructStore, GroundingGate, Compositor, JudgementGate, domain/Frame}` — only new
adapters + registrations. A checked-in invariant-path manifest asserts `git diff ∩ manifest = ∅`.

### vaani's place [D-B3 RESOLVED 2026-06-08, DECIDED]

vaani enters radix as a **text-parse-stage Component on the board** that deposits its parse to a
`RecordStore` Record; `ModelInstantiator` reads the typed parse from the Construct. The
substrate-library alternative (call vaani inside the instantiator) was **REJECTED** — it takes the
parse out of the provenance chain and couples the instantiator to vaani's API. Swap seam =
`NlpProvider` port, never an import of `ModelInstantiator`. vaani is **text-modality parse only**
(UDPipe CoNLL-U: tokens / lemmas / POS / dependency trees + statistical extractors); nothing for
other modalities. radix does NOT currently use vaani (radix-text has zero vaani imports;
`RuleModelInstantiator` is a deterministic-regex placeholder). This is a BUILD track (~70–80%
engineering + a small design pass), NOT a research mission. Gap: the consumption-contract typing
(`ParseSidecar.put(hash, payload:Any)` is untyped; `ModelInputs` has no parse field) is a DESIGN
pass unblocked by D-B3, not done.

---

## 8. The tiered Anchor — the keystone [DECIDED target; UNBUILT — `SpanRef` char-only BUILT]

A **closed discriminated union over locators**, tier-tagged, witness-carrying. **Never a string,
never `Any`.**

```
Anchor:
  artifact_id : str        # I6: terminates at Artifact identity AND ∈ Frame.arises_from
  unit_id     : str | None
  locator     : Locator    # modality-polymorphic CLOSED sum, tier-tagged:

  T1 — byte-identity (witness = source bytes alone; correct BY CONSTRUCTION)
    CharSpan  { char_start, char_end }
    RecordRef { record_hash, selector? }
    FrameRef  { frame_id, dotted_path }        # cross-frame structural grounding (radix-code path-(a))
  T2 — decode-relative (witness = bytes + PINNED content-hashed descriptor)
    BBox      { x, y, w, h, frame_index?, decode_descriptor_hash }
    TimeRange { t_start, t_end, decode_descriptor_hash }
    NodePath  { path, parse_record_hash }
  T3 — perceptual (witness = bytes + phash + tolerance τ; ONLY when byte-hash unavailable)
    PerceptualRegion { ..., phash, tolerance }
```

**Three shared invariants:** (a) terminates at `artifact_id`, and that id ∈ the Frame's
`arises_from` (**I6** — load-bearing the moment Frames go cross-artifact); (b) carries its tier's
witness; (c) `resolve(source) → verbatim excerpt | typed-failure` — **never resolves silently** to
a wrong terminus. **A single grounding boolean across tiers is FORBIDDEN.** T2 is correct only
relative to a pinned content-hashed decode/parse descriptor (mismatch raises
`UnresolvedReference` / `Stale`); T3 reports tier + achieved perceptual distance, **never
masquerades as T1**.

**Circular-grounding hole this closes.** radix-vis / radix-code ground by walking the *stored
derived structure* (`base:dotted.path` into a mode-written dict) — that proves a claim consistent
with *itself*, not with source. **Circular and unsound.** An Anchor must denote a region of an
*Artifact* (or a content-hashed Record pinning the Artifact); the assay must **re-read and
re-derive from source**, never walk the cached output.

**grounding ≠ composition [DECIDED].** radix-vis / code fused these into one `grounded_in:
list[str]` (circular). Kept **separate**: Anchors *ground* (Anchor → Artifact); `parent_frame_id`
+ `Edge` *compose*; **`FrameRef` is the ONE Anchor variant that bridges** (preserves radix-code's
path-(a) grammar). `arises_from` = the uniform composition relation; integration-across-sources is
itself a Frame, recursively (within-modality cross-artifact AND across-modality).

**Staging.** First second-modality = **code** (`NodePath`, one step off char-span). Image adds a
decode-descriptor *and* lossy hashing = two variables; deferred behind
`exp_anchor_tier_verifiability` (T2 mismatch-detection must be **100%** or block; T3 needs τ at
≤1% false-accept or the claims are excluded from the asserted set).

---

## 9. The Status union — 3-state [DECIDED, OD-2(b), `meta/decisions.md` 2026-06-14]

```
Status =
  | Unjudged     # exists; grounding not established
  | Asserted     # grounding-chain AND judgement both pass — the ONLY readable-as-defended state
  | Dissolved    # judged and no integration survived — TERMINAL, RETAINED, never deleted
```

- Built exactly this: `frames.grounding ∈ {unjudged, asserted, dissolved}`; typed
  `GroundingStatus{UNJUDGED, ASSERTED, DISSOLVED}` (replaced `asserted:bool`, principal-entrenched
  2026-06-07, 81 tests).
- **`Grounded` is a downstream (ilm) status, not radix's.** OD-2(b): radix introduces neither
  `Grounded` nor a 4th state; the 3-state union is retained; `Grounded` is written DOWNSTREAM.
  This **supersedes** the common model's earlier 4-state union.
- The union makes illegal states unrepresentable (radix-code's two `bool|None` flags admitted 9
  states for a 4-state machine, 5 illegal). `Dissolved` ≠ "we haven't looked", and is never
  deleted.
- **`Avowed` / `Warranted`** are downstream / unratified — radix introduces neither. `Avowed`'s
  scope (does the human's out-of-distribution conception ever enter radix's union, or only via
  human authorship outside radix's gates) is **OPEN, folded into OD-2's stated scope.**

---

## 10. HonestFailure — frozen enum + three-tier posture [DECIDED; BUILT text/vis]

Doctrine: **settle, don't reject the Frame.** "The architecture refuses to hallucinate" —
motivated by ColorBench 2025 (VLMs cannot reliably extract `#ff6666` vs `#ff4477`).

| Level | Failure | Posture |
|---|---|---|
| Mode / extractor | crashes mid-extract | `HonestFailure(DegradedBase)`, **continue with survivors** |
| Claim | ungrounded / training-producible | reject the **claim** into `rejected_claims` (w/ `would_have_grounded_in`); **Frame survives** |
| Frame | grounded but no integrating structure | `Dissolved(reason)` — retained, **never deleted** |
| Cross-stream | parallel streams disagree on timeline | `HonestFailure(DesyncRealization)`, both retained (A/V) |

**Kind-enum FROZEN** at: `DegradedBase · UnresolvedReference · Stale · DissonantRealization ·
RejectedClaim` **+** `DesyncRealization`. **Sensor-specific failures go in `reason`, never as new
kinds** (else N×M creeps back). Retire radix-vis's
`DegradedColor` / `DegradedFlow` / `UnresolvedSymmetry` → demote to `reason` strings (first
concrete migration item). **`UnresolvedReference` IS the canonical DANGLING cross-artifact-edge
representation.**

---

## 11. Cross-artifact grounded-vs-dangling [OPEN as a first-class dual — §10-B of the spec]

Relations has two scopes; the cross-artifact scope has two *far-end statuses*:

- **GROUNDED far end** — the other artifact is itself comprehended / part of this comprehension
  (far artifact ∈ `arises_from`).
- **DANGLING far end** — the edge exists but the far end is not grounded. Represented honestly via
  `UnresolvedReference`, **not dropped.**

Only **radix-code** realizes both (43 cross-artifact frames = grounded far ends; `UnresolvedReference`
= dangling). radix-text is UNBUILT here (`SpanRef` has a single artifact_id terminus). radix-vis is
DESIGNED-only. **The consolidated spec must give an Edge a grounded-or-dangling far-end status
keyed on whether the far artifact ∈ `arises_from`.** [OPEN — the machinery is new for text and vis.]

---

## 12. Open questions the build must NOT silently resolve

- **OD-1** — third-frame name; leaning `Models`, not closed; dossier recommends `Construal`.
- **OD-4 / D-B2** — content-hash terminus for grounding chain / Anchor (gates the Anchor migration
  root).
- **OD-3** — claim → Frame rejection aggregation + Episode verdict enum + halt semantics + 9→union
  migration map (5 illegal states).
- **§7-A** — what lands in Base for **text** (propositions in Base vs at Models). Stated above (§3-A).
- **§7-B** — cross-artifact grounded-vs-dangling as a first-class dual (§11).
- **§7-D** — Models-as-sentence (Anemic-Model). radix-vis `GroundedClaim(claim:str)` and radix-code
  `model_claims` emit English into the model layer; radix-text got it right (structured Model +
  derived Narrator `abstract`). Finalized rule: **Model is a structured object; the sentence is
  derived, never authored.**
- **§7-E** — `model_type` conflates KIND and LENS (§4).
- **Lens taxonomy** — OPEN, off the critical path (`drafts/mox-system-model`).
- **Image/video Model-KIND** — is it `Scene`? radix-vis GroundedClaims are proto-Scene, unnamed.
- **`Avowed` scope** — part of OD-2's stated scope.
- **OD-10** — ecosystem shape (L2 processor vs L3 component; gnx placement); directly bears on this
  component's manifest `kind`. UNRULED.
- **`supports(kind)` registry, tiered Anchor, RecordStore rename, typed vaani contract** — all
  DECIDED-in-design but **UNBUILT** (Phase 1.1–4 build items).
- **Construct dual-scope merge ownership (C6)** — append-only vs snapshot-merge; unspecified (§6.1).

---

## Provenance

Ontology consolidated 2026-07-04 from the authoritative radix spec, this-session principal input
(the corrected Surface / Base / Relations / Models layers), `~/mox/research/meta/decisions.md`
(OD-1, OD-2(b), OD-3, OD-4, D-B2, D-B3, D-JUDGE), the design authorities at
`~/mox/research/meta/designs/2026-06-02-radix-multimodal-common-model.md` and
`2026-06-09-radix-multimodal-proposal.md`, and the three built prototypes (radix-text v2,
radix-code, radix-vis). The Kintsch surface-form / textbase / situation-model mapping is retired
(§2). Rename map from prototype identifiers to canonical nouns lives in the spec's §5.3 and is
deposited **by version, not in place** (OD-12) so radix-code's grounded claims keep their termini.

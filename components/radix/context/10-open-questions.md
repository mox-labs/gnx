# 10 — Open Questions

These must **NOT** be silently resolved. If the build hits one, it stops and surfaces it — it does not guess. This is the explicit "do not silently resolve" list the boundary discipline depends on.

Status legend: **[DECIDED]** · **[LEANING]** lean, not closed · **[OPEN]** unlegislated · **[BUILT]/[UNBUILT]**.

---

## Named ODs still open (see `context/08-decision-ledger.md` for gating order)

- **OD-1 — third-frame name.** [LEANING / OPEN] Principal **leans KEEP `Models`**; NOT formally closed. The comprehension-ontology dossier **recommends `Construal`** (Langacker; #1 genus-clean + collision-free), with `Interpretant` (Peirce) #2. The 2026-06-09 rename to `Interpretations` was **reversed this session**. The divergence is **recorded, not resolved.** Gates the Phase 0 deposit PR and all vocabulary. Write `Models` as the lean, note the alternatives, do not write it as settled. Detail: `context/05-vocabulary-unification.md`.

- **OD-4 / D-B2 — content-hash terminus.** [OPEN] Confirming D-B2 (content-hash terminus for the grounding chain / Anchor) gates the **Anchor migration root**. D-B2 is "pending confirmation"; OD-4 confirms it. Until confirmed, the tiered-Anchor build cannot begin.

- **OD-3 — rejection aggregation + Episode verdict.** [OPEN] Claim→Frame rejection aggregation + the Episode verdict enum + halt semantics + the **9-state → union migration map** (radix-code's two `bool|None` flags admitted 9 states for a 4-state machine; 5 are illegal). Gates validator/runner + Phase 3b.

- **OD-7 — radix-vis corpus: regenerate vs retire.** [OPEN] Every existing visual chain is **circular by the decided grounding standard** (it walks the stored derived structure, not the source). Gates the Phase 4 build.

- **OD-5 — thematic-role boundary.** [OPEN] Where the line falls between structural-vaani and interpretive-consumer. Gates the vaani track.

- **OD-10 — ecosystem shape.** [OPEN, UNRULED] **L2 processor vs L3 component**; manifest convention; mint `type_urls`; **gnx placement.** This **directly bears on `gnx/components/radix/`** and is **unruled**. The `manifest.yaml` `kind:` field stays PROVISIONAL until it rules. Do not presume the L2/L3 shape.

- **OD-6 — D-ilm-1 scope amendment.** [OPEN] ilm's domain, out of radix scope. Surfacing source valid-time as a typed queryable column is gated here. radix must not preclude it (see `context/07-boundaries.md`).

---

## Design tensions the spec must state, not paper over

### §10-A — What lands in Base for text [OPEN — real tension]
Principal's genus phrasing: text Base = "**propositions / claims**." As-built (radix-text): Base = structural **BaseUnits** (turn/segment/heading/…), and propositions surface at **Models** (the Schema family). Code and image align with the **structural** reading (CST items; CV primitives at coords). Text is where "propositions/claims" bites.

**The question:** does proposition-extraction belong in **Base** (principal's genus phrasing) or does Base stay **thin-structural** with propositions emerging at **Models** (as-built)? **The spec must state which layer "extraction of core claims" means for text — do not leave it ambiguous across two layers.** This is the one place the corrected ontology (`context/01-ontology.md`) and the as-built baseline (`context/09-prototype-inventory.md` §9.2) genuinely pull apart.

### §10-B — Cross-artifact grounded-vs-dangling as a first-class dual [OPEN]
Only **radix-code** realizes both (43 grounded far ends; `UnresolvedReference` = dangling). Text is UNBUILT (single `artifact_id` terminus); vis is designed-only. The consolidated spec must give an **Edge** a grounded-or-dangling far-end status **keyed on whether the far artifact ∈ `arises_from`**. `UnresolvedReference` **IS** the canonical DANGLING representation. New machinery for text and vis.

### §10-C — Dual-output-per-axis temporal design [OPEN — net-new, no OD covers it]
The `snapshot | history` selector over Base/Relations/Models is **unspecified and unbuilt as an axis** in all three prototypes (code has it as a peer scale; vis as a sibling frame-kind; text absent). **The decision the spec must make explicit:** does every modality's Frame carry a `temporal_view ∈ {snapshot, history}` discriminator (the principal's "not a fourth frame") rather than a separate FrameKind/Scale? [Recommended framing; OPEN.] **Consequence to record:** a HISTORY frame's Anchors terminate in *multiple* time-point Artifacts linked by `parent-of` lineage, each a distinct content-hashed Artifact — this changes what a Frame's Status/Anchor must span. Detail: `context/02-temporal-axis.md`.

### §10-D — Models-as-sentence (Anemic-Model defect) [DECIDED direction; UNBUILT for code/vis]
radix-vis `GroundedClaim(claim: str)` and radix-code `model_claims` emit English **sentences** into the model layer. radix-text got it right: a **structured Model** (head + slots + constraints + anchors) with a Narrator-**derived** `abstract`. **Finalized rule:** the Model is a structured object; the sentence is **derived, never authored.** The fix is a build item for code and vis.

### §10-E — `model_type` conflates KIND and LENS [OPEN — reconcile]
radix-code `programming_model | design_model | deliberation_model` reads as a **lens / artifact-default**, not a representational **KIND**. Reconcile: `model_type` names a **KIND** from `{Schema, Script, Plan, Scene, SituationModel, Pattern}`; `programming / design / deliberation` are a **lens or artifact-kind default**, not a KIND. KINDS ⊥ LENSES are two orthogonal axes; "Situation" appearing in both lists is the symptom of the conflation. Detail: `context/05-vocabulary-unification.md`.

---

## Smaller open items (record, do not resolve)

- **Lens taxonomy** (system / programming / domain / deployment / operating + rationale) — [OPEN], **off the radix critical path**, routed to `drafts/mox-system-model`. Do **not** block the ontology on it.
- **Image/video Model-KIND — is it `Scene`?** [OPEN] radix-vis GroundedClaims are **proto-Scene, unnamed**.
- **`Avowed` scope** — [OPEN] does the human's out-of-distribution conception enter radix's Status union, or only via human authorship *outside* radix's gates? Folded into OD-2's stated scope. (`Avowed` / `Warranted` are downstream / unratified — radix introduces neither.)
- **Construct dual-scope merge ownership (C6)** — [OPEN] *narrow* Construct = radix's own append-only typed-frame blackboard; *wide* = the inhabited samsara coordination-ledger of which radix is one Domain/private-projection. Merge-conflict ownership when geist (immutable-snapshot + merge) and radix (append-only) both touch one mark is **unspecified**.
- **DECIDED-in-design but UNBUILT** — the `supports(kind)` registry, the tiered Anchor, the `RecordStore` rename, and the typed vaani consumption contract are all **design-settled but no code** (Phase 1.1–4 build items). Do not mistake designed for built.

---

## How to treat this list

Every item above is a place where the wrong move is to **guess and proceed**. The right move is to **name it, mark its status, and surface it** to the principal at the gate. The whole apparatus — gates, provenance, honest-failure — exists so that "we do not know yet" is representable and never silently converted into "we decided." An OPEN item written as DECIDED is a false foundation; false foundations compound.

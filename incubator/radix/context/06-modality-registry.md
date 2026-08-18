# 06 — Modality Registry & vaani's Place

> Honesty markers: **[DECIDED]** · **[LEANING]** · **[OPEN]** · **[BUILT]** · **[UNBUILT]**.
> Source of truth: consolidated radix spec §6. The one-ontology-across-modalities thesis is **convergent across three built prototypes** (text, code, visual) — but the *unified* multimodal radix under one set of ports is **[UNBUILT]**. Do not read "convergent" as "shipped as one system."

The design claim: adding a modality is a **linear** cost, not a combinatorial one. A **port** is an interface (constant count); an **adapter** is a registration (linear in modalities). Adding a modality = new adapter files behind existing ports. **No N×M explosion.** [DECIDED]

---

## The eight ports

### Driving (1)

- **`ArtifactSource`** — locator → `LoadedArtifact` (an Artifact row + raw bytes/text + a modality tag).

### Driven, per-modality (3) — these are where a new adapter registers

- **`Segmenter`** (≡ the old `BaseBuilder`) — Artifact → BaseUnits + Entities + Record-refs. Heavy payload goes to the `RecordStore`, never onto the BaseUnit.
- **`RelationExtractor`** — BaseUnits → Edge rows with Anchors. **May read several modalities' BaseUnits to emit a cross-modal Edge in ONE Frame** — this is the capability the fused `extract()` cannot express.
- **`ModelInstantiator`** — Base + Relations → a typed Model. The **rule-adapter and the LLM-adapter sit behind the *same* port** — swapping one for the other is a registration change, not a code change.

### Driven, invariant (3) — modality-blind, one implementation

- **`Compositor`** — unit-detection + ascent (the compositional-honesty discipline; see `03-gates.md`).
- **`GroundingGate`** — Gate 1, modality-blind by construction.
- **`JudgementGate`** — Gate 2, pluggable strategy + per-modality calibration.

### Cross-cutting (2 + 1)

- **`GroundingResolver`** — registered *by anchor-kind*; performs the per-tier expensive witness recompute. **This is the genuinely-new multimodal port** — the one seam the prior single-modality prototypes never needed.
- **`ConstructStore`** — typed-row persistence (SQLite ⇄ memory), modality-blind.
- **`RecordStore`** (≡ the old `ParseSidecar`, renamed) — content-addressed heavy sidecar, cost-tiered, **never in the grounding chain.**

### Registry-bound, NOT ports

- **`Narrator`** — renders the `abstract` sentence from a typed Model (per-kind).
- **unit-floor declaration** — moved out of the core reducer into the registry. This closes radix-vis's `_FRAME_KIND_BY_ARTIFACT` core-leak, where modality knowledge had leaked into the invariant core.

---

## The four seams — where modality enters

Everything else is modality-invariant. Modality varies at exactly these four points. [DECIDED]

| Seam | What varies | Port / locus |
|---|---|---|
| **Segmenter** (unit-floor) | pixels → regions / tokens → CST / turns / shots | `Segmenter` |
| **Anchor + resolver** | the grounding atom + its per-tier verifier | `Anchor.locator` + `GroundingResolver` |
| **ModelInstantiator** | the comprehension organ (rule ⇄ LLM) | `ModelInstantiator` |
| **Judgement calibration** | thresholds / redundancy-patterns (gate *structure* stays invariant) | config behind `JudgementGate` |

### Falsifiable boundary [DECIDED — mechanically checked, UNBUILT check]

Adding a 6th modality must touch **zero** files in `{ runner, ConstructStore, GroundingGate, Compositor, JudgementGate, domain/Frame }` — only new adapters and registrations.

A checked-in **invariant-path manifest** asserts `git diff ∩ manifest = ∅`, and first asserts that every manifest path resolves (so a rename can't make the guard vacuous). **Any F1–F3 falsification ⇒ do not commit, return to synthesis.**

---

## vaani — the text parse Component

[**D-B3 RESOLVED 2026-06-08, DECIDED**]

vaani enters radix as a **parse-stage Component on the board.** It deposits its parse to a `RecordStore` Record; the `ModelInstantiator` then reads the typed parse from the Construct.

- The **substrate-library alternative** (calling vaani *inside* the instantiator) was **REJECTED** — it takes the parse out of the provenance chain and couples the instantiator to vaani's API.
- Swap seam = an **`NlpProvider` port**, never an import of `ModelInstantiator`.
- vaani is **text-modality parse only** — UDPipe CoNLL-U (tokens / lemmas / POS / dependency trees) plus statistical extractors. Nothing for other modalities.
- Ships **2 of 10** REP-019 §4 deterministic no-LLM primitives; the other 8 are **[UNBUILT]-but-buildable** (implementation, not inquiry).

**Honest gaps [OPEN design residual]:**

- `ParseSidecar.put(hash, payload: Any)` is **untyped**, and `ModelInputs` has **no parse field** — the consumption-contract typing is a **DESIGN pass** unblocked by D-B3, not done.
- The joint `(R, M, S)` atom-merge — vaani's deterministic facets + LLM frame-evocation fused into one atom — is the **one genuinely novel-design residual** (keystone c9).
- **radix does NOT currently use vaani.** radix-text has zero vaani imports; its `RuleModelInstantiator` is a deterministic-regex placeholder.

**Classification:** ~70–80% engineering + a small design pass. A **BUILD track, not a research mission.** vaani is silent on the temporal axis.

---

## Four modalities — current instantiation

Honest per-modality state. The unified radix binds these under the ports above; today they exist as **separate prototypes**.

| Modality | Status | Base | Relations | Models | Anchor tier |
|---|---|---|---|---|---|
| **text** | radix-text **[BUILT]** (81 tests) | BaseUnits | Edges (intra) | Schema family | T1 `CharSpan` (built as `SpanRef`) |
| **code** | radix-code **[BUILT]** (391 frames) | CST + spans + metrics | cargo/rustdoc graphs (intra **+ cross**) | `programming_model` | T1/T2 `NodePath` target; built as `grounded_in` strings |
| **vis** | radix-vis tracer **[BUILT, partial]** | CV primitives | symmetry / peak / ΔE (intra); cross **[UNBUILT]** | `GroundedClaim` (anemic) | Surface-under-Base built; T2/T3 target |
| **img (still)** | subset of vis | primitives at coords | spatial / compositional | Scene / SituationModel | T2 `BBox` |

Notes:

- **Only radix-code built both Relations scopes** — 43 cross-artifact frames (grounded far ends) + `UnresolvedReference` (dangling far end). Text and vis have **intra-only** built; cross-artifact grounded-vs-dangling far-ends are **[UNBUILT]** for text and **designed-only** for vis.
- **radix-vis is the strongest one-ontology evidence** — identical `FrameMode{BASE, RELATIONS, MODELS}` (not Kintsch) survives a radically non-textual modality, and it names Surface-under-Base explicitly ("Substrate", the 4-layer chain `Models → Relations → Base → Substrate`), directly corroborating the corrected model.
- **radix-vis is explicitly NOT canonical radix.** Its corpus is circular by the decided grounding standard — see `OD-7` (regenerate vs retire).

---

## Provenance

- D-B3 (vaani as parse-stage Component): `/Users/yza.vyas/mox/research/meta/decisions.md`.
- vaani gaps / REP-019 §4 primitives: the REP-019 record in the research ledger.
- Built prototypes: radix-text `/Users/yza.vyas/mox/research/tools/radix-text/`; radix-code `~/radix-workspaces/rust-mastery/tools/src/radix/`; radix-vis `~/mox/platform/mox.studio/tools/radix-vis/`.
- Ports + seams design authority: `/Users/yza.vyas/mox/research/meta/designs/2026-06-02-radix-multimodal-common-model.md`, `2026-06-09-radix-multimodal-proposal.md`.
- radix-vis cites `.radix/framework-amendments.md §A1-A7`, which **does not exist as a file** — its content lives only in code + SCOPE, and must be **lifted, not cited.**

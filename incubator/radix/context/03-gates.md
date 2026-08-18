# 03 — The Gates

> Honesty markers: **[DECIDED]** ratified · **[LEANING]** current lean, not closed · **[OPEN]** unlegislated · **[BUILT]** shipped in a prototype · **[UNBUILT]** designed, no code.
> Source of truth: the consolidated radix spec §3, ratified `D-JUDGE`. Prototype anchors cited inline.

radix runs **three disciplines, not two, and none folded into another.** Two of them are gates in the strict sense (they can change a Frame's `Status`); the third is a rule inside the Compositor that governs whether a scale of comprehension is allowed to exist at all. The recurring error the finalized spec has to prevent is collapsing these into one another — `framework.md` names two gates but its second gate is *structural-coherence*, which is not the judgement gate the common model and the principal mean. [DECIDED — §3]

---

## The three disciplines

| # | Discipline | Kind | When | Sets Status? |
|---|---|---|---|---|
| 1 | **Grounding-chain** (Gate 1) | part-existence, cheap, modality-blind | write-time | **No** — write-path predicate; rejects the write |
| 2 | **Judgement** (Gate 2) | configuration-integrity, expensive | assay-time | **Yes** — `Asserted` / `Dissolved` |
| 3 | **Compositional-honesty / unit-detection** | ascent criterion | inside the Compositor | **No** — dissolves a *scale*, not a Frame |

Unit-detection is the **ascent** criterion; judgement is the **confabulation** criterion. Neither covers the other. This threeness is [DECIDED].

**No verdict boolean is ever persisted on a Frame.** A `grounding_passed` bool is a correctness lie — a cached verdict with no invalidation path over a mutable source. `Status` (§4.4) is the single persisted assertion-bit. [DECIDED]

---

## Gate 1 — Grounding-chain

**Question:** does the part exist and terminate where it claims to?

- Part-existence check. **Cheap. Write-time. Modality-blind by construction** — it inspects structure, never content semantics.
- The store write-path **rejects an ungrounded Model or Edge** (`UngroundedModelError`) using the cheap predicate: **in-bounds + terminus-consistency + arity.** The chain `Model → Relations/Edge → Base → Artifact` must hold.
- The **expensive witness** — recompute the decode/parse, recompute the hash from source — stays in the **assay**. It is **never persisted as a Frame boolean.**
- [BUILT] for text and code. `components.py:92-95` in radix-text.

Gate 1 sets nothing. It either lets the write through or refuses it. It is the reason the Construct never contains a Model whose grounding chain is structurally broken.

---

## Gate 2 — Judgement

**Question:** is the integration load-bearing, or a schema-shaped confabulation?

Expensive. Runs at assay-time. This is the gate that writes `Asserted` or `Dissolved`. It is **distinct** from `framework.md §8`'s structural-coherence check — do not conflate them. [DECIDED — §3]

**Two orthogonal strategies behind one `JudgementGate` port. Neither subsumes the other.** [DECIDED]

- **remove-constituent** (structural integration) — remove one constituent; does the structure materially change? If it reads the same, the integration was manufactured → dissolve. Used in radix-text. (This is mīmāṃsā *anvaya-vyatireka* in provenance; the operational name is remove-constituent.)
- **four-question / non-redundancy** (training-collision) — is the claim paraphrasable from generic priors? If a claim is producible from training distribution alone, it is not comprehension of *this* artifact. Used in radix-vis and `framework.md`; radix-code runs `assay_with_voice` on local MLX and **fails closed to `needs_human_triage`.**

Both strategies coexist behind the same port. A modality picks a strategy (or both); the port contract does not change.

### Calibration is the fourth seam

Gate **structure is invariant** across modalities. The **threshold is data.** Redundancy patterns are Rust-literal for code, visual-literal for image; the four-question rubric is calibrated per modality. This per-modality calibration is one of the four modality seams (see `06-modality-registry.md`). [DECIDED]

- radix-code: `_REDUNDANT_PATTERNS` → per-modality calibration config, not hard-coded into the gate.
- **Ecosystem-quadrant carve-out** [DECIDED]: claims whose entire value is canonical reference-fixing (fixing *which* known thing is referenced) must **not** be non-redundancy-rejected. Their redundancy against priors is the point.

---

## Discipline 3 — Compositional-honesty / unit-detection

**Question (the ascent criterion):** can a Frame at *this* scale produce Models grounded in the *lower-scale* Frames? If not, dissolve the scale and flatten it into the parent's relations.

This is **not a gate that sets Status.** It is the dissolve-on-ascent rule that lives inside the Compositor. It governs whether a level of composition earns its own Frame or should collapse into its parent. [DECIDED — §3]

`framework.md §8` calls its second check compositional-honesty / unit-detection. That is *this* discipline — the ascent criterion — **not** the judgement gate. The spec carries both because they answer different questions:

- unit-detection asks *"should this scale exist?"*
- judgement asks *"is this integration real or confabulated?"*

Dropping either leaves a hole the other cannot fill.

---

## What is invariant, what is data

- **Invariant:** gate *structure*, the three-discipline split, the write-time/assay-time boundary, the ban on persisted verdict booleans.
- **Data (per-modality):** judgement calibration thresholds and redundancy patterns.

Both gates are **[BUILT] for text and code.** For image/video the judgement doctrine is stated but the assay is **[UNBUILT]** as a wired path — radix-vis carries the four-question rubric in design, not in a gate-passing corpus (see `OD-7`, which asks whether the existing visual corpus is regenerated or retired, since every existing visual chain is circular by the decided grounding standard).

---

## Provenance

- Ratification: `D-JUDGE` (both gates in radix; radix comprehends and STOPS). See `/Users/yza.vyas/mox/research/meta/decisions.md`.
- Built gate code: radix-text `components.py:92-95`; radix-code `judgement.py`, `validators.py` at `~/radix-workspaces/rust-mastery/tools/src/radix/`.
- Design authority: `/Users/yza.vyas/mox/research/meta/designs/2026-06-02-radix-multimodal-common-model.md`, `2026-06-09-radix-multimodal-proposal.md`.
- `framework.md §7`/`§8` two-gate naming is the reconciliation target — see `OD-9` (encode Gate 2 in `framework.md` or amend the framework-wins rule).

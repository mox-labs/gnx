# radix — Charter

> Context substrate for the FINAL radix. Honesty markers: **[DECIDED]** ratified · **[LEANING]**
> principal's lean, not closed · **[OPEN]** unlegislated · **[BUILT]** shipped in a prototype ·
> **[UNBUILT]** designed, no code. Authority order on conflict: this-session principal input >
> `~/mox/research/meta/decisions.md` OD rulings > 2026-06-09 proposal > 2026-06-02 common model >
> prototypes.

---

## What the final radix is

radix is a framework and toolkit for **compositional comprehension across heterogeneous
artifacts**. It takes an Artifact (text, code, image/video-scene) and produces **grounded,
judged Frames on the Construct** — comprehension, each Frame traceable to source and judged as
real integration rather than confabulation — **and stops.** [DECIDED, D-JUDGE;
`meta/decisions.md`]

---

## The one-ontology thesis (central)

ONE comprehension ontology comprehends any artifact in any modality. The **same** breakdown —
Surface → Base / Relations / Models, one grounded settling — applies to text, code, and
image/video; only the *instantiation* differs. Modality varies at exactly **four seams** (the
Segmenter's unit-floor, the Anchor + resolver, the ModelInstantiator, and judgement
calibration); everything else is modality-invariant. [DECIDED as a common model; see
`context/01-ontology.md`]

This is not asserted — it is **convergent**. The identical Frame shape, the `arises_from`
provenance relation, grounding-chain integrity, and the two gates already survive three built
modalities (text, code, visual). That is the evidence the thesis stands on.

**Honest caveat.** The thesis survives three *separate* prototypes. The *consolidated* multimodal
radix — one codebase comprehending all modalities through shared ports — is **UNBUILT**. The
common model is corroborated; the unification is designed, not shipped. Do not present multimodal
unification as built.

Independent landscape support: the situation model is defined in the primary literature as
**a-linguistic / modality-independent** (Kintsch & Rawson p.211; Zwaan & Radvansky) — outside
evidence that a comprehension ontology need not be text-bound.

---

## The wager (why comprehension that survives sessions matters)

radix is programme apparatus for the collaboration wager: the convergent machine amplifies the
human divergent spark, it does not replace it. Comprehension that is grounded, judged, and
provenance-carrying is what lets machine work compound without model collapse — the machine's
integration is auditable back to source, never a self-confirming loop. radix's honest-failure
discipline ("the architecture refuses to hallucinate") is the wager expressed as engineering.

---

## Boundary in one line [DECIDED, load-bearing]

**radix comprehends and stops.** Relevance/ranking (ilm) and decay/eviction (REP-021) are
**downstream** — radix introduces no downstream vocabulary, but the Construct interface must not
**preclude** them.

Ratified as **D-JUDGE**. Naming downstream machinery inside radix has previously "spawned fake
forks", which is why the boundary is enforced, not merely noted.

### Three things downstream of radix

| Concern | Owner | radix must NOT |
|---|---|---|
| **Relevance / ranking** — 4D resonance score, `ResonanceRanker` | **ilm** (read-side, query-time) | rank; compute relevance; name `ResonanceRanker` inside radix (violates D-JUDGE) |
| **Decay / eviction** — hot→cold tier, scheduled sleep-cron + write-admission | **REP-021** (RQ4) | decay; evict; **delete anything, ever** — the ledger is immutable; eviction only moves tiers |
| **Recency-as-ranking** — `last-recall` clock, fold over `recall_event` | **ilm** | compute or store a recency scalar |

### Interface constraint — do not preclude, do not name

radix introduces no downstream vocabulary (no ilm / SMRT / integration-phase / reduction /
`Grounded`) — **but** the Construct interface must not preclude: (a) shared-Construct reading
(the `ConstructStore` interface); (b) a ranking Component downstream of asserted Frames; (c) a
`recall_events` ledger that both ilm-ranking and REP-021-eviction can read.

**BUILT-state gap radix does NOT preserve today.** Source valid-time survives only inside
`artifact.meta` JSON, not a typed column; `frames.created_at` = comprehension time, not source
valid-time; there is no `recall_events` table; `edges.kind` has no typed-signed
CITES/REFUTES/EXTENDS/SYNTHESIZES; no proposed/accepted/deprecated modal enum. "There is nothing
to rank by yet" — which is fine, because ranking is not radix's job. **D-ilm-1** (proposed schema
additions to enable ranking) is an ilm-side recommendation ABOUT radix — **OPEN, gated behind a
principal scope amendment (= OD-6)**. radix must not name or build it, but must not preclude it.

---

## The two gates (charter level) [DECIDED, D-JUDGE; BUILT for text/code]

Both gates live in radix. They are a **Russellian type distinction — neither covers the other.**

- **Gate 1 — grounding-chain** (part-existence; cheap; write-time; modality-blind by
  construction). The store write-path rejects an ungrounded Model/Edge using the cheap predicate:
  in-bounds + terminus-consistency + arity. The chain Model → Relations/Edge → Base → Artifact
  must hold. The expensive witness (recompute decode/parse, recompute hash from source) stays in
  the assay, never persisted as a Frame boolean.

- **Gate 2 — judgement** (configuration-integrity; expensive; assay-time). Audits whether
  integration is load-bearing or schema-shaped confabulation. Two orthogonal coexisting
  strategies behind one `JudgementGate` port, neither subsuming the other: **remove-constituent**
  (does removing one constituent materially change the structure?) and **four-question /
  non-redundancy** (is the claim paraphrasable from generic priors?). Calibration is
  per-modality — the fourth seam. Gate *structure* is invariant; threshold is data.

**Three disciplines, not two, not folded.** Beyond the two gates there is a third discipline that
is NOT a gate: **compositional-honesty / unit-detection** — the Compositor's *ascent* criterion
("can a Frame at this scale produce Models grounded in the lower-scale Frames? if not, dissolve
the scale and flatten into the parent's relations"). Unit-detection is the *ascent* criterion;
judgement is the *confabulation* criterion; grounding-chain is the *part-existence* criterion.
None covers the others. **No verdict boolean is ever persisted on a Frame** — a `grounding_passed`
bool would be a cached verdict with no invalidation over a mutable source, i.e. a correctness lie.
`Status` is the single persisted assertion-bit. (Full detail in `context/01-ontology.md`.)

---

## The motto test

radix is programme apparatus; it inherits the motto — *Amplify Radical Nonconformity.* The
honest-failure discipline is the concrete expression: where a generic system would emit a
confident guess, radix settles or dissolves and records why. The architecture refuses to
hallucinate; that refusal is the nonconformity, engineered.

---

## Provenance

Charter consolidated 2026-07-04 from the authoritative radix spec (integrator's consolidation),
this-session principal input, `~/mox/research/meta/decisions.md` (D-JUDGE, OD register), and the
design authorities at `~/mox/research/meta/designs/2026-06-02-radix-multimodal-common-model.md`
and `2026-06-09-radix-multimodal-proposal.md`.

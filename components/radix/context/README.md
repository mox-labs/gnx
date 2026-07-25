# radix — context substrate

This directory is the **authoritative context substrate** for the final, brand-new radix. It consolidates the finalized discussions and decisions so the real build can proceed from **one home** instead of re-reading scattered designs, prototype code, and the decision ledger each time.

**What radix is, in one line:** a framework and toolkit for **compositional comprehension across heterogeneous artifacts** — it takes an Artifact (text, code, image/video-scene) and produces **grounded, judged Frames on the Construct**, each traceable to source and judged as real integration rather than confabulation, **and stops**.

**What this substrate is NOT:** it is not the build, and it is not shipped. `maturity: design`. Three prototypes are built *elsewhere* as separate tracers (`context/09-prototype-inventory.md`); the consolidated `gnx/components/radix/` is a design/context home. Do not read any file here as "already coded."

---

## How to read the status markers

Every claim in this substrate carries an honest marker. They are load-bearing — respect them.

| Marker | Meaning |
|---|---|
| **[DECIDED]** | ratified/settled ground; may be stated flatly |
| **[LEANING]** | the principal's current lean, **NOT** formally closed; write as a lean, record the alternative |
| **[OPEN]** | unlegislated; the build must **not** silently resolve it |
| **[BUILT]** | shipped in a prototype (named which) |
| **[UNBUILT]** | designed, but no code |

Two things this substrate refuses to let you forget:

1. **OD-1 (the third-frame name) is NOT closed.** The principal **leans KEEP `Models`**; the comprehension-ontology dossier recommends `Construal`. Written as a lean everywhere. See `05-vocabulary-unification.md` / `10-open-questions.md`.
2. **The one-ontology-across-modalities thesis is corroborated, not built.** It survives three modalities as separate prototypes; the finalized multimodal radix — especially the orthogonal temporal axis and first-class cross-artifact grounded/dangling far-ends — is design-and-build work still ahead.

---

## The substrate map

Numbered files, read in order for the full picture; jump by concern otherwise.

| File | Concern | Headline status |
|---|---|---|
| `00-charter.md` | What radix is; the one-ontology thesis; the wager; the boundary in one line | [DECIDED] charter |
| `01-ontology.md` | The corrected **Surface / Base / Relations / Models** layers; the Kintsch retirement; the modality-instantiation table; Model-KINDS | Base/Relations/Models [DECIDED]; genus-name OD-1 [OPEN] |
| `02-temporal-axis.md` | **Snapshot vs History** as a first-class orthogonal temporal axis over B/R/M; per-modality realization; the net-new design crux | axis [DECIDED]; realization as-an-axis [UNBUILT] |
| `03-gates.md` | The **two gates** (grounding-chain + judgement) as a Russellian type distinction; the three-disciplines reconciliation | [DECIDED]; BUILT text/code |
| `04-construct.md` | The typed **Construct** blackboard; the 10 entities; tiered **Anchor**; the 3-state **Status** union; **HonestFailure** | [DECIDED]; text BUILT, tiered Anchor [UNBUILT] |
| `05-vocabulary-unification.md` | Canonical noun set; **OD-1** name ruling; per-prototype rename map; migration ordering | canonical set [DECIDED]; OD-1 [LEANING] |
| `06-modality-registry.md` | The 8 ports; the four seams where modality enters; **vaani's place** (D-B3); the four modalities' current state | [DECIDED]; ports designed, adapters partial |
| `07-boundaries.md` | Where radix **stops**; ilm/decay downstream; do-not-name/do-not-preclude | [DECIDED, D-JUDGE] |
| `08-decision-ledger.md` | Staging view of the OD/D ledger; gating order; RULED vs OPEN | mixed; ledger SSOT is `meta/decisions.md` |
| `09-prototype-inventory.md` | Honest state of radix-code / radix-text / radix-vis / vaani; carry-forward matrix | BUILT/UNBUILT per prototype |
| `10-open-questions.md` | The "**do not silently resolve**" list — every OPEN OD and design tension | [OPEN] |

**Adjacent, at the component root (not in `context/`):**
- `manifest.yaml` — the gnx component manifest. `kind:` is **PROVISIONAL** (L2 processor vs L3 component) — gated on **OD-10**, which is unruled. `maturity: design`.
- `SKILL.md` — the entry/overview pointer into this substrate (not a runnable-capability skill; radix is not built). The **canonical, deposit-by-version framework text** (the source of truth that code identifiers migrate against per OD-12/OD-1) does **not live here yet** — it lands at the text-conformance step. Until then, `01-ontology.md` + `05-vocabulary-unification.md` hold the working ontology and names.

---

## Authority order (on any conflict)

> **this-session principal input > `meta/decisions.md` OD rulings > 2026-06-09 proposal > 2026-06-02 common model > prototypes.**

This substrate is a *staging* consolidation. The authoritative ledger lives in the research repo:
- `/Users/yza.vyas/mox/research/meta/decisions.md`
- `/Users/yza.vyas/mox/research/meta/missions/radix-od-queue.md`
- design authority: `/Users/yza.vyas/mox/research/meta/designs/2026-06-02-radix-multimodal-common-model.md` and `.../2026-06-09-radix-multimodal-proposal.md`

On any disagreement between a file here and those sources, **those sources win** and this substrate is the thing to correct.

---

## The one discipline that governs everything here

radix's whole apparatus — the two gates, mandatory provenance, honest-failure, the tiered Anchor, the retained-never-deleted `Dissolved` status — exists so that **"we do not know yet" is representable and never silently converted into "we decided."** An OPEN item written as DECIDED is a false foundation, and false foundations compound. Keep the markers honest.

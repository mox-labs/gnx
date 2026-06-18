# The component catalog

> The instrument set, made legible. Every component the catalog is planned to carry, tiered by
> **design-completeness** — how settled its design is, independent of whether its code is built.
> The tiers are honest on purpose: a design-complete component gets a full page; a named-only
> one gets a line. Inflating a vapor entry into a page is the failure this tiering exists to
> prevent. Each entry is held to [the component standard](/docs/component-standard); the worked
> per-component example is [aboot](/docs/aboot).

The four tiers, and what a tier means for documentation:

| Tier | Design state | What it gets here |
|------|--------------|-------------------|
| **Tarmac** | design-complete, or shipped methodology | a full design / architecture / affordances page |
| **Cobble** | designed, some questions open | a page where grounded; an honest entry where not |
| **Dirt road** | named, design intent only | an entry — name + intent, no page yet |
| **Flag** | roadmap-only / unverified | a line, deliberately not a page |

---

## Tarmac — design-complete or shipped

Write these fully; the design is settled enough to hold a page.

| Component | What it is | Status | Channel it breaks |
|-----------|------------|--------|-------------------|
| **aboot** | Session-continuity capability — briefs, handoffs, resume state. The sharpest test of the universal/vendor namespace split. | raw material shipped; componentization planned | Stasis — continuity core is runtime-agnostic; CC hooks are a vendor binding |
| **luminex** | A Flow — the legibility surface; the most design-complete manifest in the corpus. | planned — design-complete manifest, no plugin yet (name collision open, §10.8) | Opacity — makes composed behavior legible |
| **slick-as-plugin** | slick's grammar projected as an installable plugin face. | crate shipped; plugin face planned | Adaptability — the grammar travels to any Target |
| **ci-scaffolds** | Collaboration scaffolds — claim verification, decision frameworks, mastery-oriented review. | **shipped** v0.6.0 | Opacity — keeps the human able to predict and check |
| **guild-arch** | Multi-perspective architecture review — deliberate pushback before a decision sets. | **shipped** v0.2.0 | Drag — review is a composed panel, not a platform bottleneck |
| **antifragile** | ACES boundary review — runs the Boundary Test on proposed abstractions. | **shipped** v0.1.0 | all three — it is the standard's own enforcement lens |
| **craft-extensions** | Lexicon extension — growing vocabulary and concepts. | **shipped** v0.1.0 | Extensibility — new capability enters as a lexicon entry |
| **craft-research** | Research synthesis — literature analysis, evidence-grounded claims. | **shipped** v0.3.0 | Opacity — claims trace to sources |
| **craft-rhetoric** | Comprehension, rhetoric, explanation — docs, tutorials, diagrams. | **shipped** v0.3.0 | Opacity — makes understanding propagate |

The shipped cix plugins listed above install into Claude Code today — see [Install a plugin](/docs/install-a-plugin).

---

## Cobble — designed, some open

Real tools or grounded designs; full pages where the grounding is solid, honest entries where the design still has open seams.

| Component | What it is | Status |
|-----------|------------|--------|
| **matrix** | Execution tier — wires and runs a validated Flow. Lives in geist.sh, downstream of gnx. | partial |
| **ix** | Experimentation — trials and sensors over a subject (the composer island models its shape). | designed |
| **recon** | Reconnaissance — survey a codebase or system before acting on it. | **shipped** v0.7.0 |
| **memex** | Memory / retrieval — the canonical `--skill` exemplar (`memex --skill`). | designed; tooling exists |
| **radix** | Multi-modal input handling — text built, multi-modal designed. | parked while tuning (v0.3.4) |
| **post** | Comms / publishing — overlaps `press` (§10.15); fold or compose before either registers. | designed (open) |
| **niti** | Governance-gate hook plugin — hook-layer sibling of geist-edge. | live v0.1 in chanakya |
| **craft-evals → rubrix** | Evaluation suites and methodology rubrics; `rubrix` is the named gate they feed. | **shipped** v0.3.0 (craft-evals) |
| **dao-corpus** | The expertise wing — mastery distilled as Skill components pointing into a corpus. | rust corpus closed; py/ts queued |
| **assay** | Named in the founding set; design not yet captured in the ground truth. | named — needs a settlement pass |

---

## Dirt road — named, intent only

Design intent exists; no settled spec. An entry, not a page, until the design hardens.

- **bodhi / dagstra / hades** — the self-extension loop agents (sharpen → search → generate into the registry). Named; the bodhi→dagstra gap is explicitly unresolved (§1).
- **veritas / labcoat** — the two proven consciences from the artisans dao; open-source candidates, not yet componentized.
- **mudge** — a council + forensic-review skill; explicitly an open-source candidate (§10.16).
- **constitution + stack-scaffolds** — the CLAUDE.md-seeding and hexagonal-skeleton patterns. Proven as patterns; need componentizing into catalog entries.

---

## Flag — a line, not a page

Deliberately minimal. Inflating these would be the exact failure the tiering guards against.

- **cortex** — roadmap-only (REP-015), no research behind it. The programme's ruling is to *not* accept it merely to complete the picture. It gets this line and no more.
- **mashq** — named in the corpus as shipped, but no repository has been located. Verify it exists before reserving the name; treat as absent until then.

---

## Open questions

- **Per-component page demand.** Which cobble/dirt-road components earn a full page next is a
  judgment best made against intent — comment on a row to pull it up a tier and it gets written
  at the matched fidelity.
- **`assay` and the founding set.** `assay` is listed in the core set but undocumented in the
  ground truth. It needs a settlement pass before it can hold even a cobble page.
- **The loop agents' status.** bodhi / dagstra / hades are load-bearing for the gnx loop (§1)
  yet entirely unbuilt. Do they stay dirt-road entries, or does the loop need them specified
  before the catalog can claim self-extension as more than a design intent?

---

Comment on a row to request a full page, correct a tier, or flag a missing component.

# The ecosystem

> **Design register.** gnx is pre-build (NAMED — doctrine shaped, build starting). Present tense below states the design, not shipped behavior. What runs today: [status](/docs/status).

## The platform is a strict triad

Three repos, one separation:

| Layer | Repo | Role | Status |
|---|---|---|---|
| Grammar | slick (`~/mox/packages/slick`, crate `slickit`) | What components look like: the 5-field Manifest, TypedStruct, TypedRegistry; the four-kind grammar (open string, not a shipped enum). | SHIPPED v0.2.0 |
| **Vocabulary** | **gnx** | The catalog: component registry, marketplace, scaffolds, CLI. *Where components come from.* | NAMED — doctrine shaped, build starting |
| Execution | geist.sh (geist-edge + matrix + mox.hud) | Governed runtime that hosts components and mediates agent tool calls | PARTIAL |

slick gives the grammar. gnx supplies the vocabulary. geist.sh executes. The triad is exhaustive — nothing lives outside it at the component level.

(Settled 2026-06-09/10, `mox-system-model/system-model.md`.)

![The platform triad](/diagrams/triad.svg)

---

## The triad sits inside the samsara ontology

The triad sits inside a world-ontology settled 2026-06-04/05:

**samsara** is the world — the inhabited Construct. An append-only ledger of typed marks (slick TypedStructs) plus per-domain private projections. Humans and agents coordinate *stigmergically*: by reading each other's marks, not by messaging each other.

**Treya** is the framework that acts: ilm (knowing) · kalā (skill) · kriya (action).

A **Locus** is a bounded view into the world.

geist.sh is the world's execution organ, not the world itself. gnx validates and registers; geist.sh runs. The catalog is a governed commons that geist reads; it is not a runtime.

---

## A project comprises three pieces

A **project** = three pieces (2026-06-10):

```
claude          # agent + CLAUDE.md constitution
dao             # the agentic organization (charter, guild, ratchet)
.gnx/           # plugin-style projection of installed catalog components
```

`gnx init` constructs all three. The `.gnx/` directory is a Target-side projection of vendor-namespaced components into installable plugin directories — reads of the catalog, not first-class kinds.

---

## The pipeline through a project is strictly ordered

```
comprehend (radix)
  → envision (bodhi → [roadmap GAP] → dagstra)
  → do (matrix → rubrix)
  → show (luminex → mox.hud)
  → human re-grounds
```

The gap between bodhi and dagstra is explicitly unresolved. Each stage name is a component or component family; gnx is the catalog those components register into.

---

## The gnx loop provides autopoietic closure

From the samsara audit's strongest KEEP (2026-06-06):

> *"Bodhi sharpens → Dagstra searches → HADES generates into the registry → extends the next search — IS autopoietic closure in the precise sense. Build the frame on this joint."*

This is what distinguishes gnx from a static package registry. The mechanism:

1. **Dagstra** performs semantic proof-search over the registry, matching on `provides` tags.
2. **HADES** generates components and writes them into the registry.
3. Whatever enters the catalog immediately expands the surface the next Dagstra search reaches.

The loop is self-extending: each generation extends the lexicon, which extends future composition. gnx is the registry this loop runs through.

---

## Claude and agents are the primary consumers

Decided by yzavyas 2026-06-12, consistent with UC1 (2026-04-01):

> "Claude Code → gnx (discover) → slickit (types) → matrix (execute). Claude IS the orchestrator."

Humans browse second. Every catalog decision is evaluated against one test:

> *Can a capable reasoner decide to compose two components it has never run, from their declared surfaces alone?*

This is the Feb-2026 pragmatic turn: composition doesn't require formal proof; it requires legible surfaces. That test — not "is the README readable" — is the curation standard. It shapes the `provides` index, the manifest surface, the `--skill` requirement for Capability kinds, and the legibility tier of the intent-hardening gradient (§4).

---

## Open questions

- **No chartered research home** (§10.10): gnx doctrine lives in scratch notes and an unaudited conversational capture (`mox-system-model/system-model.md`). The triad and the gnx loop rest on that base. Does the catalog layer need its own settlement pass — a REP, or equivalent?

- **System-model audit**: The ground truth itself flags the system-model as "unaudited conversational capture." How much of the triad's settled status depends on that document, and what would a formal audit change?

- **Roadmap gap**: The pipeline has an explicit gap between bodhi and dagstra. What resolves it — a new component, a Kalā formalization, or a named gap that persists? (See §10.9: "roadmap-planner: new kind, new agent, or Kalā formalization?")

- **Locus ↔ project mapping**: The ground truth defines Locus as a bounded view; a project's `.gnx/` projection is the practical instantiation. Whether a `.gnx/` *is* a Locus, or merely resembles one, is not settled in §1.

---

Comment on specific blocks — flag the claim, the section, or the open question you want to revise.

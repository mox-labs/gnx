# The intake bench

How a cix component becomes a gnx component. Ruled by D21 (aspect-named, decomposed by use),
GEP-0001 (adoption re-mints; provenance in `source`/`relations`, never the identity), GEP-0002
(ports vs tags). Every import walks all five steps — a component that skips one isn't ported,
it's smuggled.

## The bench, per import

1. **Draft** — manifest in `harness/drafts/`, aspect-named per D21.
2. **Check** — `python3 harness/check.py` green (identity grammar, shape
   rule, flow compile where applicable).
3. **Pristine pass** — payload ported self-contained: plugin-relative references resolved or
   bundled; no dangling pointers into cix; no invented relations (an edge you can't evidence is
   an edge you don't write). ACE check: config over hardcoding, ports over coupling, readable
   without tribal knowledge.
4. **Mint** — `components/<aspect-name>/` with `manifest.yaml` + payload. The type_url is
   permanent from this moment (GEP-0001).
5. **Ledger** — status recorded in the queue below. (Graduates to the GEP-0008 accreditation
   ledger when that GEP is accepted; until then this table is the honest interim.)

## Ported

| type_url | kind | from | status |
|---|---|---|---|
| `gnx.dev.v1.intent-hardening` | Skill | native | shipped (pre-bench) |
| `gnx.dev.v1.rational-inquiry` | Skill | native | shipped (pre-bench) |
| `gnx.dev.v1.radix` | Capability | native | design; reclassified 2026-08-02 (processor is a Capability shape, not a kind) — closes harness R4 |
| `gnx.dev.v1.claim-extraction` | Agent | cix/craft-research `extract` | **ported 2026-07-23 — the bench exemplar**; ports at `designed` pending GEP-0005 |

## Queue — short names per D23 (names PROPOSED; a name mints only at step 4)

D23 amended D21: identities are short brands; semantics live in `description` + tags + ports.
Bench step 3 now includes a **karman naming/tag review** (the ontology steward — seated
2026-07-24).

| proposed type_url | kind | from |
|---|---|---|
| `gnx.dev.v1.bodhi` | Agent | the intent-elicitation agent (dao + slick shared atom) |
| `gnx.dev.v1.karman` | Agent | guild-arch `karman` — the ontology steward, early intake |
| `gnx.dev.v1.elicit` | Agent | craft-research `elicit` + `eliciting` skill |
| `gnx.dev.v1.recon` | Capability | `collecting` skill + the recon tool |
| `gnx.dev.v1.verify` | Agent | `scrutiny` + `verifying` skill (CoVE — domain-general by method; brand ruled 2026-07-24: inquiry rejected as too broad, assay taken, verify stands) |
| `gnx.dev.v1.synthesis` | Agent | `synthesis` + `synthesizing` skill |
| `gnx.dev.v1.audit` | Agent | `audit` + `auditing` skill |
| `gnx.dev.v1.craft-research-pipeline` | Flow | the `research` skill (a Flow wearing skill clothing — harness R6) |
| `slick.dev.v1.slick` | Capability | the crate — "slick is slick in here" (namespace call pending) |
| `slick.dev.v1.hades` | Agent | the building half (bodhi writes, hades reads) |

**Tool binding:** a Capability's `source` points at the tool that backs it (recon ←
`cix/tools/recon`; ix, radix, matrix follow the same pattern when they intake), and the tool
exposes `--skill` — the runnable and its semantic surface bind through the manifest, no extra
field needed.

**Figure projection (karman pattern):** roster components project from
`dao-corpus/corpora/agent-craft/` — one domain per agent/skill (76 exist: dijkstra, knuth,
feynman, ebert…), **one unit distilled per domain**, aspects assigned at project level (the
corpus's own architecture — which independently corroborates D23: the durable layer doesn't
carry aspects either). Intake for a roster component: domain exists? → distill → project.
Domain missing (karman today) → step 0 is raw gathering into a new domain; cix agent prose is
one input, not the durable home.

Then: craft-rhetoric (orwell, feynman, sagan, tufte, vyasa… — brands survive as-is under D23),
guild-arch (bundle + remaining council Agents), ci-scaffolds (blocked on the external-dependency
gap — pythea MCP has no field to live in, GEP-0004), antifragile, craft-evals, craft-extensions.

## Bench findings (feed the open GEPs)

- **Payload-dependency pattern:** cix agents instruct themselves to read their plugin's skills.
  The exemplar resolved it by bundling the method references into the component (self-contained)
  and rewriting the one plugin-relative pointer. Alternative — a `relations.skills` edge to a
  separately-minted method Skill — deferred until GEP-0004/0005 rule relation semantics and the
  Agent surface. Revisit if bundling starts duplicating method docs across components.
- **Port surfaces on Agents are invented until GEP-0005 rules them** — every Agent port mints
  at `maturity: designed`.
- **External install deps are inexpressible** (pythea MCP, hook files) — GEP-0004's two live
  test cases (harness R1).

# Accreditation and produce-authority

**Source**: §9 of ground-truth.md. Established doctrine (2026-05-25 origin, reaffirmed
2026-06-12); most consequential open design in the corpus.

---

## gnx ships correctness; x.uma ships trust

gnx ships *correctness*: structure validates, port contracts hold, namespaces are
well-formed. x.uma + geist-edge ship *trust*: FIS enforcement, autonomy ceilings,
mediation.

The load-bearing consequence: gnx accreditation is *data that x.uma's FIS reads*, not a
trust judgment gnx issues. Accredited compositions get higher autonomy ceilings upstream.
gnx's job is to make the composition visible and attributable; policy enforcement lives
above it.

The clearest statement of what gnx cannot do: a manifest declaring
`requires:[PII], provides:[Logs]` composes cleanly at gnx's level. It also leaks cleanly,
unless something above gnx enforces info-flow policy. gnx is not the leak-stopper. gnx is
the record that makes the leak attributable.

Collapsing correctness and trust into one authority would mean gnx must understand
semantics it cannot validate. That is the same category error as making `provides` tags
proofs rather than discovery surfaces (§4, intent-hardening gradient).

---

## LLM validators dissolve the anchor

LLMs both compose and validate components. If the validator is itself LLM-minted, the
anchor dissolves: you have an agent checking an agent's work, with no ground outside the
loop to stand on. This is fuzzy-governance REC1–3.

The escape requires an *exogenous* anchor — a source of authority the LLM can read but
cannot mint. gnx accreditation is the nominated anchor: human-ratified status that
persists in the catalog and is available to any composer that reads the registry.

Registry and ledger operations stay deterministic and auditable (§3). SDK sessions
(agentic) *propose*; deterministic code validates and writes. If gnx's own verdicts were
LLM-minted, the anchor would dissolve into the loop it exists to break. This is why
"dumb boundaries, smart interiors" is not an architecture preference — it is what
preserves gnx's exogenous-anchor property.

Position as of 2026-06-12: formally ratifying gnx in this role is open (§10.2). Everything
in the June corpus leans yes.

---

## Produce-authority is the make-or-break

The problem: TypedRegistry has no owner concept. geist keys on self-declared `agent_id`.
Until minting a warrant is structurally restricted to an authority *disjoint from the
mark's author*, every warrant is forgeable.

The doctrine (2026-06-05): "Build that wall first."

gnx owns the registry, which means authorship identity on every registration is one of
gnx's earliest schema decisions — not something to layer on later. This is the
produce-authority wall. It is harder to retrofit than any other catalog invariant because
provenance metadata added after the fact is not retrospectively trustworthy: a component
registered before the wall existed carries an unsigned warrant.

Concretely: when `gnx validate` accepts a registration, it must record *who* is asserting
this manifest. That identity is not a field the component author writes into the manifest
— it is bound by the registry at write time, from a credential the author cannot
self-issue. The exact form of that credential is [open], but the constraint
on its structure is settled: authority disjoint from author.

---

## Verdicts are grounding ⊥ warrant, never a single boolean

The prosecuted counterexample: `Frame.asserted:bool`. Treating a verdict as a boolean
collapses two independent axes into one bit. A frame can be:

- high grounding, legitimate warrant (validated, credentialed author)
- high grounding, illegitimate warrant (correct structure, forged authorship)
- low grounding, legitimate warrant (credentialed author, schema-shaped noise)
- low grounding, illegitimate warrant (both wrong)

Only the first case is a clean PASS. The boolean flattens all four into two. This was
prosecuted as a constitutional violation and fixed in PR #74 (2026-06-07). Any schema
design that reintroduces a single `valid:bool` or `accredited:bool` field is re-opening
that violation.

The implication for catalog surfaces: a component card must be able to show grounding and
warrant independently. Displaying a single badge ("verified") without surfacing which axis
it represents is misleading at the same level as presenting a `provides` tag as a
behavioral proof.

---

## The catalog's existential failure mode is pollution, not injection

Confident, schema-shaped, low-grounding agent traces that pass structural validation
look like components — and pollute the catalog without triggering any gate.

The catalog is only as useful as its signal-to-noise ratio. A registry full of
well-formed manifests pointing at generated artifacts with no grounding is worse than an
empty registry. It is an empty registry that costs discovery effort to search through.

Four cures are in doctrine (2026-05-29):

**1. Chartered membership for writers.** Not open submission. Who can register is a
governance question answered before the catalog opens, not after it fills with noise.

**2. Validation-at-registration.** `gnx validate` is the structural gate. Passes
structural validation ≠ accredited. But structural failure is an immediate hard stop.

**3. Decay and relevance dynamics.** REP-022's staged ranking is the only settled ranking
discipline in the programme: cheap recall first, then resonance rerank, with recency
tracked separately from eviction. This is chartered for the Construct and transferable to
catalog discovery by explicit decision. That decision has not been made yet.

**4. Curation budget from day one.** Not a remediation applied after the catalog grows.
The budget is part of the catalog's operational design.

---

## Ranking by adoption creates an echo-trap

Ranking by raw adoption counts is wrong-signed (2026-05-29). It rewards echo: a component
that appears first ranks higher, which keeps it first — presence, not quality.

The REP-022 resonance rerank breaks this by separating recall position from quality
signal. Cheap recall surfaces candidates; resonance scoring re-orders them on evidence
quality, not adoption count. The catalog's ranking discipline should follow this model —
but "should" is the current state. The explicit decision to transfer REP-022's ranking
to catalog discovery is open.

The echo-trap is a degeneration-watch item for gnx, parallel to the three watches in §8.
A catalog whose discovery layer runs on adoption counts will drift toward stale,
frequently-cited components regardless of their current grounding.

---

## Open questions

**§10.1 — Accreditation record shape**: three candidate forms, none chosen:
- Relation on the Flow's Manifest (stays within the existing schema shape)
- `kind: AccreditationRecord` (new kind with its own field contract — but adds kind
  proliferation; who owns the amendment process?)
- Side-table (out-of-band ledger, cleaner separation from manifest, harder to co-locate
  in searches)

Partly slick's call — the choice has consequences for how slickit's TypedRegistry is
extended. Needs resolution before `gnx validate` writes its first accreditation entry.

**§10.2 — Formally ratifying the exogenous-anchor role**: the June corpus leans yes;
the ratification itself is unfinished. The open question is not "should gnx be the
anchor?" but "what does formally ratifying that mean?" — does it require a REP, a charter
amendment, a ground-truth entry with a specific date and source?

**Ranking transfer decision**: transferring REP-022's staged ranking to catalog discovery
requires an explicit decision. The REP charters the discipline for the Construct; gnx
would be adopting it, not inheriting it. Who makes that call, and what does the decision
artifact look like?

**Produce-authority credential form**: the wall is settled in principle; the credential
structure is not. What does the registry bind at write time — a signing key, a mox
identity, a human-confirmed session token? This is the earliest schema decision in gnx's
implementation sequence.

---

Mark specific blocks where the framing diverges from your intent — especially the
correctness/trust split, the two-axis verdict structure, and the produce-authority
credential form.


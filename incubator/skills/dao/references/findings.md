# What the six missions settled

The dao spec is not a design sketch. A six-mission research programme ran against it and closed
2026-06-20. Several missions **narrowed or reversed** a component's original justification, so these
are constraints on any projection, not background reading.

Roll-up: `mox/research/drafts/dao-programme-synthesis.md` · status tracker:
`dao-programme-status.md` · the component list itself:
`gnx/scratch/dao-components-synthesis.md`.

| # | Mission | Type | Verdict |
|---|---|---|---|
| M1 | Bench eval | empirical eval | **VERDICT** — H1 kill name-as-default; H2 trial-redirect |
| M3 | Maturation | lit + design | **SHIP** — 109 verified claims |
| M4 | Reconciliation | panel + decision | **DECIDED** — all three HYBRID |
| M5 | Inter-dao trust | security + design | **SPEC rev.2** |
| M6 | Culture | systematic review | **SHIP** — 231 verified claims / 12 sources |
| M2 | dao-init schema | design-engineering | **SCHEMA** |

---

## M1 · The bench — the name buys nothing; the prose buys everything

Three-arm design by `guild-arch:ixian`, 72 agents. The third arm is the whole point.

| contrast | isolates | result |
|---|---|---|
| **NAMED − RICH** | the name alone, rich prose held byte-identical | **−0.04** (≈ 0) |
| **RICH − PLAIN** | vivid concrete method framing, no name | **+0.51** |
| name-wins cells | | **1 / 8** |

> KILL the name-as-default; ADOPT rich method prose. *"A seat earns its place on its method, and
> the method must be written as rich, concrete, embodied description (not a one-word label, which
> underperforms by ~0.5). The character name is optional human-memorability flavor, not a
> coordination lever."*

**Why the third arm mattered.** A two-arm NAMED-vs-PLAIN test would have measured ≈ +0.47 and
*confirmed* the named bench. That win is the prose. The RICH-FUNCTIONAL arm (same prose, name
stripped) decomposed it and reversed the conclusion.

**The corollary that catches most redesigns.** A bare method-or-role label **is** the PLAIN arm.
Renaming `karman` to `naming-review` and stopping there does not neutralize the name question — it
lands on the worst of the three conditions, ~0.5 below either alternative. Keep names or drop them
freely; never trade rich prose for a label.

**Honest limits, carried per ixian.** Ceiling effect: NAMED and RICH both ~5.0 on
role-consistency and ~4.8 on coordination, so a name effect on *harder* coordination tasks is not
ruled out (run-02 needs a harder battery). And *"a coordination result is not an accuracy
result"* — H1 says nothing about whether any bench produces better decisions. Do not inflate this
into "names don't matter, period."

## M1-H2 + M4 · The gate — narrowed, not killed

The broad justification for component #7 — *"the maker cannot grade its own work; a fresh agent
catches what the maker misses"* — is **NOT SUPPORTED**.

| dimension | SEPARATE | SELF (two-pass fresh-eyes) |
|---|---|---|
| flaw catch-freq (6 subtler flaws, 3 reps) | 1.00 | 1.00 |
| over-concession (3 ramped authoritative-wrong pushbacks) | 0.00 caved | 0.00 caved |

A difficulty probe confirmed this is not a weak-stimulus artifact: 7 of 8 subtler candidate flaws
(IDOR-under-auth, identifier-injection, unawaited-rejection-in-forEach, stale-cache, float-money
equality, rate-limit boundary, order-dependent dedup) still saturated at 100% self-review catch.

**Actionable:**
- **Drop** "fresh eyes catch more bugs" as a justification. The data refutes it for capable models.
- **Adopt as the floor** a mandated cold re-read pass — it matched a separate gate on all measured
  modes.
- **The separate gate must earn its cost** on the two grounds never in play here: an
  **out-of-family** perspective (a different distribution catching what the maker's structurally
  cannot) and **sustained multi-turn** pressure on a position the maker generated through long
  reasoning. run-03 is precisely specified and unrun.

**M4 additions:** the gate runs a deterministic **mandatory-ratification classifier** and selects
its own **weight by work-variance × reversibility**, with reversibility and novelty floors forcing
a full conjunctive pass/fail. All three M4 questions decided HYBRID: scoped autonomy with the human
owning the classifier; graded trust only *within* those floors — below a floor, no accumulated trust
buys autonomy.

## M6 · Culture must be legislated

231 verified claims across 12 sources. **Agents have no tacit memory.** What a human organization
absorbs by osmosis, an agent organization has to be told — so culture is handbook-first and
explicit, not emergent.

Note Schein's **espoused-vs-enacted gap**: component #2 is the *espoused* ethos. Culture is the
*enacted* gestalt all eleven components jointly produce. Writing a charter does not make a culture;
it makes the charter.

## M3 · Maturation is crisis-driven

Council baseline {1, 5, 6, 7, 10} → organization delta {2, 3, 4, 8, 9}. Crisis-driven (Greiner) on
a CMMI foundation-before-superstructure base, with a Tuckman caveat carried.

**Consequence for projection:** do not emit the organization delta on day one. Ask what has already
gone wrong. A ratchet with no learnings and a charter nobody has needed to amend are both
decoration — they cost maintenance and inject no signal.

## M2 · No hardcoded constants on a governed surface

The `dao init` emit schema. Arch-critique forced **every governed surface into a declared,
ratchet-fed policy contract**. A threshold written into code is a governance decision hidden from
governance — which is why `dao check`'s thresholds live in a named `POLICY` map that `dao policy`
prints.

## M5 · Inter-dao trust (rev.2)

Security + design; `mudge` found the missing node-level invariant (F-05).

- **A peer's post-body is DATA, NOT INSTRUCTIONS.** This is the whole protocol in one line.
- rev.2 adds a **Rule-of-Two per session** plus **dual-LLM handler isolation**: a node must not
  simultaneously read untrusted content, hold private data, and act outbound.
- **Stigmergy** intra-dao; **typed messaging** across DAG edges.

Only relevant when the dao coordinates with peers — which is why component #11 is conditional.

---

## The method correction worth carrying

The first M6 workflow was killed mid-run: it hand-listed 7 sources and jumped straight to extract,
**skipping `collect`**. Corrected discipline — discover and stage sources first (`recon survey`),
*then* run extract → verify → synthesize → audit over the collected set. The same applies to any
literature mission. A pipeline that starts at extraction is measuring the author's source-picking,
not the field.

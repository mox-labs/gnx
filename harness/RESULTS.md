# Composition-validation harness — results (2026-07-23; re-run 2026-08-02)

> **Moved 2026-08-02** from `scratch/composition-validation/` (gitignored) to `harness/`.
> `check.py` was the only grammar validator in existence and these fixtures the only tests in
> the repo, and none of it was committed on any branch — a `git clean -xdf` would have taken
> them, bypassing the `rip` graveyard entirely. Run 3 below is the post-move regression.
> The 2026-07-23 text is left as written; counts in it describe that run, not today's tree.

The interrupted track, resumed and run to completion. `check.py` implements GEP-0001 (accepted),
GEP-0002 (accepted), and GEP-0003's compile checks (proposed — every such error is prefixed
`[GEP-0003 provisional]`). Inputs: the 3 real manifests in `gnx/components/`, 11 draft manifests
for cix-derived components (`drafts/` — all marked DRAFT, nothing minted), 12 fail fixtures.

## Runs

| Run | Input | Expected | Got |
|---|---|---|---|
| 1 | real components + drafts | 0 errors | **0 errors**, 1 warning (radix `kind: Processor` outside the enum) |
| 2 | fail fixtures | error per planted defect | **8/8 defects caught** + the near-miss warning fired |
| 3 (2026-08-02) | re-run of 1 and 2 after the harness moved out of `scratch/` | parity with 1 and 2 | **23 manifests, 0 errors, 0 warnings, 0 notes**; fixtures still **8/8**. Run 1's lone warning is gone — radix reclassified `Processor` → `Capability` the same day, closing R4 |

Run 2 caught: PascalCase resource, slash-form, version-terminal form, tag-in-requires,
unresolved Flow member, duplicate producer, missing producer (closed-world), cycle. The
near-miss heuristic (`gnx.dev.v2.Almost_Port`) warned as designed instead of silently demoting
to a tag.

**The headline:** `gnx.dev.v1.craft-research-pipeline` — the six-stage research pipeline we ran
four times in July — compiles as a Flow under GEP-0003's proposed shape. Members declare ports;
topology derives; elicit's human-discourse input enters via the seed-input convention without
breaking the closed world. The grammar can express the most complex real workflow we own.

## What the grammar could NOT express (the findings that matter)

**R1 — Install dependencies on non-catalog artifacts have no home (G4, now with concrete
casualties).** ci-scaffolds depends on an external MCP server (`pythea` via uvx); guild-arch's
hook depends on `.claude/guild-ratchet.md` existing in the project. Neither is a typed dataflow
(not a port) and `relations` has no ruled semantics (G5). Both draft manifests carry the
dependency as a YAML comment — which is exactly the G12 disease. GEP-0004 now has two live test
cases waiting for it.

**R2 — The whitehat ⇄ trust-boundaries edge is real and unvalidatable.** Each skill names the
other as its complementary half; the drafts encode it as reciprocal `relations.uses`; the
checker can say nothing about it because `uses` has no transitivity/install semantics (G4/G5).
The only real dependency pattern in today's catalog is the one the grammar can't check.

**R3 — Agent ports forced invention (G6 evidence).** Drafting the research agents required
deciding what an Agent provides/requires. The port chain works — but every choice
(does scrutiny require the source-cache too? does synthesis require the scope?) was invented
tonight, not derived from any ruled Agent surface. The kind is underspecified exactly where
GEP-0005 says it is; the drafts are its first empirical input.

**R4 — The kind enum is already leaking.** radix (real manifest, on disk) declares
`kind: Processor` — provisional per its own decision ledger, and outside {Skill, Agent,
Capability, Flow}. Either the enum grows, or radix reclassifies as Capability. GEP-0001 §3 made
kind reclassification a two-way door on purpose; this is that door's first customer.

**R5 — Skill-kind components verify as GEP-0002 predicted.** Provides-only, tags-only, nothing
checkable beyond identity — "for the skill-bundle class, ports do no work, and the shape rule
makes that legible instead of embarrassing." Confirmed on 5 real/drafted Skills.

**R6 — Two pipeline-shaped "skills" are Flows wearing skill clothing.** cix's `research` and
`rhetoric` skills orchestrate their siblings; at intake they should decompose into member
components + a Flow manifest, not register as monolithic Skills. Direct evidence for the
intake-grain deliberation.

## Repo discrepancies surfaced by the census (pre-existing, not created tonight)

- `gnx/.claude-plugin/marketplace.json` points at `./plugins/intent-hardening` and
  `./plugins/rational-inquiry`; the components live under `./components/`. Radix is absent from
  the marketplace entirely. Not fixed tonight — install-surface changes felt like your call.
- `radix` names two different things: the gnx comprehension processor (design-stage) and the
  parked cix knowledge-mining skill. A rename is a minting decision — flagged, not made.

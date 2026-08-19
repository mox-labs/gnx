---
title: "Supersessions, errata, and the name registry"
section: ecosystem
status: shipped
mode: reference
---

# Supersessions, errata, and the name registry

*The corrections layer: rulings deliberately overridden, claims that failed verification, and names that collide. A future session that trusts a superseded claim re-derives a dead end — this page exists so that never happens silently. Verified 2026-07-06 by five independent deep-reads against the shipped artifacts.*

## Deliberate supersessions (rulings overridden on purpose)

| Superseded | By | Why |
|---|---|---|
| "matrix IS the runtime" (ruled 2026-04-01, reaffirmed 06-09/10, "no asserting override anywhere") | **Two runtimes, two trust postures** (yzavyas, 2026-07-06) | matrix continues as the in-process correctness-domain runtime, rebuilt on slickit; geistr is the trust-domain agent runtime. The old ruling's exclusivity is retired, not its content. |
| "Don't build a separate slickr/geistr until matrix proves insufficient" (2026-04-01 deliberation) | Consciously retired (2026-07-06) | Its premise was matrix-exclusivity; the trust-posture split is a principled reason for a second runtime, not scope creep. |
| "Normative Construct spec lives with the runtime; extract from matrix" (deferred-design table) | **The board contract hoists into slick** (direction, 2026-07-06) | Premised on one runtime. With two, runtime-owned board types are runtime-to-runtime coupling; the contract belongs in the shared layer (dependency inversion; the xDS types-repo pattern; the deliberation's own validation-invariants split already pointed here). Wire format = mint-once, GEP-grade, still open. |
| "gnx parses manifest JSON directly" (standing ruling) | **gnx consumes slick validate** (direction, 2026-07-06) | The old ruling was partly a workaround for the broken crust. Under the kit model, gnx adopting the kit is the first dogfood. |
| matrix's field names as manifest vocabulary | The composer owns the mapping | matrix speaks consumes/produces at runtime; manifests speak requires/provides; the crossing is historical fact (matrix renamed one way in Feb, slick the other way in Mar — runtime speaks actions, manifest speaks contracts). |

## Errata — handoff/ground-plan claims that failed verification

- **"geist.sh ships both halves natively (mox.hud panel + matrix runtime + geist-edge transport) — its ledgered charter."** No such charter exists in any geist tree. geist.sh ships an edge skeleton + a chat CLI; no hud panel, no matrix. geist-edge is the designed *runtime/mediation plane*, not "transport."
- **"The edge Component/Construct model."** Shipped geist-edge has no Component/Construct model — its vocabulary is Processor/PhaseResult over HTTP parts. Component/Construct are L3 design terms (SLICK protocol layer).
- **"matrix's README example raises TypeError."** It fails *earlier*: `AttributeError` at `Orchestrator([...])` construction (docs teach `provides`, code reads `produces`). All four matrix prose docs teach the dead 02-25 contract; source + tests are consistent with each other. Never author from matrix's docs.
- **"6 doctest occurrences of cix.commands."** Actually 9 crate-wide (2 doctest + 6 unit-test in manifest.rs + 1 wasm doc-comment).
- **"8 plugins ≈ 68 units."** 7 real plugins carry all 68 units; the 8th registry entry (recon) is a Python CLI with zero *plugin* components, registered as if a plugin. (Nuance: recon does bundle a skill internally at `src/recon/assets/skills/recon/` — a CLI-installed runtime asset, not a marketplace-projected component; it and its `--skill` support are why recon models the self-describing-tool convention.) cix has **no commands at all**; orchestration lives in umbrella skills.
- **"The cix.commands → cix.capability inheritance question."** Dissolves completely: neither string ever existed in cix — they were only ever slick-crate doctest examples. cix's shipped unit taxonomy is CC-native `skill/agent/hook/mcp`; the four-kind vocabulary (Capability/Agent/Skill/Flow) is a **new mint, not an inheritance**.
- **"The samsara 8 was radix ports, so GR2's evidence base shrank."** Incomplete: two real 8-kind lists exist in the corpus (comprehensive-understanding's and dagstra.md's — different eights). The later, better position is **4 blessed literals + an open-world string** (which is what GEP-0009 ships, and radix's `kind: Processor` exercises).
- **New, unlisted anywhere:** geist-edge is **compile-broken against current slick** — `registry.rs` imports `TypedConfig`, renamed to `TypedStruct` in v0.2.0, over a path dependency. The kit's one breaking release broke its one consumer, silently. This is the standing argument for dependents-CI.
- **provides-produces.md (2026-04-21) is stale against the crate it cites** — written a month *after* the v0.2.0 rename it doesn't know about; its "consumes/produces is the canonical schema pair" is backwards. Its *semantic* content (two composition phases: topology vs discovery) survives as GEP-0002's shape rule.
- **prog-grammar.md is conceptually right, field-level wrong** — its manifest field list matches no shipped schema; `augments` exists nowhere; its "four kinds settled (REP-004, v0.2.0)" cites two authorities that don't carry the claim (REP-004 never enumerates kinds; v0.2.0 shipped no kind field). Keep the execution-model discriminator; drop the "settled."
- **The luminex Flow sketch disagrees with GEP-0003** (the GEP's own cross-check TODO, now run): no members list, relations-carried composition (the rejected alternative), K8s-style envelope dialect — and it models the human surface as **ports** (`requires: canvas-renderer / provides: showcase`), a live alternative GEP-0006 must rebut or adopt. Also inherited from it: fidelity verdicts are two-axis, never a bool; and "Flow is spatial, not temporal" — don't smuggle an epoch axis into the kind unexamined.

## The name registry (collisions — gloss, never merge)

- **slick ×3** — the crate (`~/mox/packages/slick`, lib name `slick`, package `slickit`); the products/slick design memos (matrix/ix/assay layering — "naming overlap is incidental," its own README); SLICK-the-protocol vs `--skill`-the-CLI-convention.
- **geist ×5 → retired 2026-07-06** by the decomposition: **geist.sh** = the distribution · **geistr** = the agent runtime · **geist-edge** = the capability boundary · **mox.hud** = the view layer · "geist" unqualified = the project. Use the qualified names.
- **Construct ×2** — matrix's runtime board (the one this ecosystem means) vs radix's gated-write comprehension board. Binds harder the moment slick mints *the* Construct type: radix's sense must conform or rename.
- **HUD ×2** — mox.hud (the spatial workspace product) vs REP-009's research-programme HUD (a different, shipped app; sibling lineage).
- **TypedStruct ×2** — slick factory input vs matrix component output; same xDS root, opposite lifecycle ends.
- **ACES ×2** — the antifragile doctrine (REP-001) vs the multi-agent case-study system (the blueprints, source of the +21pp result).
- **Frame ×3, luminex ×3, MSG's "M" ×2½** — unchanged from the ground plan's registry; unresolved, mint-once.

## Ancestor patterns worth inheriting (verified shipped)

- **ix**: the experiment-as-directory (thin config entry point + source-referenced payloads + results ledger) — the proto-project for `gnx init`; per-type_url Pydantic config validation at the factory boundary — the shipped "defined" half of defined-or-opaque.
- **recon**: template configs in the package, mission instances in the project — a preset is itself a catalog citizen; explicit `source:` name-binding — the shipped answer to selection-among-N-providers (selection is config; matching is types).
- **matrix**: compile-time rejection (missing producer, duplicate output, all cycles), single mediated writer, double-entry produce-check, seed-input via factory-closure config with roots declaring `consumes = ∅` — inherited as semantics.
- **cix, negatively**: three-way version drift under attentive curation (re-stamp, never copy); census only by directory sweep (count hook *definitions*, not `.sh` files — cix's own site under-counts); do-not-revive list (parked radix's external fork is the live one; the old collab-scaffolds hook system; openclaw; radix's two rejected designs — "writing config before the consumer is theater").

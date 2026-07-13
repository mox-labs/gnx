---
title: "The ecosystem ground plan"
section: build
mode: reference
status: proposed
register: internal
fidelity: dirt-road
---

# The ecosystem ground plan

> **Supersession notice (2026-07-06).** This plan is the durable 07-04 snapshot. Rulings made
> 2026-07-06 override it where they touch: "matrix IS the runtime" → two runtimes, two trust
> postures; the deferred Construct-spec lean ("lives with runtime") → the board contract hoists
> into slick; "gnx parses manifest JSON directly" → gnx consumes slick validation (the kit model);
> and the geistr guard is consciously retired. The live alignment layer is the
> [ecosystem wing](/dossier/ecosystem/semantic-architecture) — read its
> [ledger](/dossier/ecosystem/ledger) for the full supersession table. This page stays as the
> annotatable record of the 07-04 grounding; where the two disagree, the ecosystem wing wins.

The pragmatic bottom. Every noun in the ecosystem resolves to exactly **one** of three states — **SHIPPED CODE**, **COMPONENT TO AUTHOR**, or **DEFERRED DESIGN** — and anything without a row here isn't real yet, by construction. Drafted 2026-07-04 from two adversarially-verified corpus dives (the slick-manifest deep dive, 16 agents; the component↔Construct contract validation, 11 agents) plus principal discourse; ported to the dossier 2026-07-05. The working copy lives in `scratch/`; this is the durable, annotatable one.

The rulings this ledger gates on are now drafted: **GEP-0001** (identity), **GEP-0002** (provides), **GEP-0003** (Flow), **GEP-0009** (overlay) — drafts, ratification owed.

## 0. The stack in one paragraph

slick declares — the typed skeleton (5 fields); the *semantic* half rides in source-referenced payloads (SKILL.md / `--skill`), not the struct; legibility is an empirical open question (MF4, the calibration cliff), not a premise. gnx catalogs — overlay + intake + projection; the control plane. **matrix IS the runtime** (ruled 2026-04-01, reaffirmed 06-09/10, no asserting override anywhere); geist-edge is a sibling (adapters/dispatch), not the executor. Claude Code is the near-term data plane via committed projection. The whole shape is xDS: catalog = management server, runtimes = data planes, type_url = the join key, config is data never code, Flow members = TypedExtensionConfigs.

The organizing invariant is **MSG**, carried in manifest.rs's own header: **M** — the Manifest, pure structure (identity, source, ports, relations); **S** — Skills, natural language, referenced via `relations["skills"]` (S rides in payloads by design — the open S problems are legibility MF4 + the calibration cliff, not a missing field); **G** — Governance, **external, never on the Manifest** (the overlay, the maturity gate, and the wall are gnx-side *by construction*, not by our preference). Every ruling below keeps to its plane: pressure to grow the slick core is answered by the overlay (G), the payload (S), or the runtime (M) — never by new core fields.

## 1. SHIPPED CODE

| What | Where | State + known defects |
|---|---|---|
| slickit v0.2.0 | `~/mox/packages/slick` | TypedStruct + TypedRegistry (always-on; registry Rust-only) + 5-field Manifest (feature-gated OFF). Serde-structural validation only, no `deny_unknown_fields` (overlay round-trips silently — the linter carries all validation). Bugs: wasm `relations` HashMap→ES-Map; half-landed `cix.commands→cix.capability` (6 doctests); unpinned crust ABI; **Python-crust ImportError (ghost Kind/TypedConfig imports — 4th bug, unlisted)**. Never dogfooded on itself. matrix imports zero slick symbols; gnx parses manifest JSON directly (standing ruling). |
| matrix | `~/mox/products/cix/tools/matrix` | THE runtime. Component protocol `{name, consumes: frozenset[str], produces: str, async run(construct) → TypedStruct}` (types.py:131–147). Single mediated writer: only the Orchestrator appends; runtime type_url checked against compile-time `produces` (double-entry, ContractError). DagCompiler rejects missing-producer / duplicate-kind / duplicate-name / ALL cycles at init. Construct = append-only board (ledger + by-type index + snapshot — all three layers in one object). Refuses persistence/retries/parallelism by design. **HAZARD: all four prose docs teach the dead 02-25-morning contract (`requires/provides`, `subject`, frozen Construct); the README flagship example raises TypeError. Regenerate before anyone authors against it.** |
| gnx prototype | `~/mox/products/gnx` | 3 components (overlay fields in YAML comments only), projector `cli.py` — **cannot execute the population**: git+https source crashes the build and rmtree destroys prior output; `lstrip('./')` mangles paths; only single-file `kind: Skill` projects; hardcoded version `0.1.0` (breaks updates — CC version = cache key); hardcoded MIT/"Mox Labs" (would mislabel Apache-2.0 slick); drops type_url/relations/maturity (RT4/G11). `--check` is a no-op stub. Entire pipeline + GEP docs uncommitted. |
| cix (frozen 2026-05-20) | `~/mox/products/cix` | Intake payload: 8 plugins ≈ 68 units (34 agents, 32 skills, 1 hook, 1 MCP, **0 commands**), plus tools. Component lists exist NOWHERE as data — census must be a directory sweep (the marketplaces miss assay + parked radix; recon is dual-registered). Version drift live in the frozen state — **re-stamp metadata at intake, never copy**. |
| ACES empirical layer | `~/yzavyas/public/blueprints` | The validated ancestor (n=29 pre-registered, +21pp): evidences exactly one variable — agents querying a pre-computed deterministic Construct beat raw-file exploration. Everything else is design-that-survived. **H6 judge paradox** (same-distribution judge anti-correlates with accuracy) = recorded design constraint on any future automated gate. |

## 2. DECISION QUEUE — the gate to population

One-way doors first; everything else is a two-way convention that needs to be *named*. Drafted where marked.

1. **Identity ruling — ONE-WAY → GEP-0001 (drafted).** gnx-owned namespace minting (dissolves the cix.commands/capability inheritance); version-medial confirmed; one casing rule (the pick owed); **no kind-derived segments**; namespace extraction; dot-vs-slash settled (matrix's slash-form is an unenforced docstring).
2. **Authorship-identity credential — the other ONE-WAY (wall-first, ruled 2026-07-02).** A non-self-issued credential + asserting-identity field in the registration schema BEFORE the first registration — pre-wall registrations carry permanently unsigned provenance. Scope guard: NOT SPIFFE/mTLS infrastructure; advisory-first stands. (H2 rides this.)
3. **On-disk format → GEP-0009 (drafted) + GEP-0002 (drafted).** Overlay = slick-5 + `kind` (provisional) + `maturity` (projection gate) + `version` (SSOT; the xDS split — type_url `<ver>` grammar-major vs overlay version as CC cache key). Provides shape rule: parses-as-type_url = port, dotted-lowercase = non-normative tag. GR4 register text corrected: *parses but silently loses topology fields*.
4. **Grain + census.** Plugin-grain intake (adopt/copy-tree — containment travels in the tree); census by directory sweep; registrations = aliases, never identities; per-unit decomposition later-additive. **The round-two four-kind re-decomposition grain is population's real long pole — principal's call.**
5. **Projector contract v0** (spec territory, not GEP). Copy-tree for adopted trees; lockfile-grade identity file (type_url + version + source ref) per plugin dir; license/author passthrough; maturity gate; source-resolution fixes; **Flow → N:1 plugin projection** (GEP-0003). Vendor policy recorded: the catalog vendors payloads at intake; `source` records provenance, not a fetch target.
6. **Ratify-or-exclude the 2026-05-02 ontology vocabulary** (Frame = table vs row; the memex renames) — one sign-off either way; catalog naming currently inherits unratified vocabulary.

## 3. COMPONENTS TO AUTHOR — the population queue

- **slick itself** — the first dogfood. Two-level: crate Capability (identity/version anchor, foreign source, Apache-2.0/Yashodeep Vyas — projector passthrough required) + its skills via `relations["skills"]`; a curated rewrite of maintainer-audience frontmatter, not a copy. Acceptance test (kavi lineage): an agent projects/composes from the manifest alone, without reading crate source.
- **8 cix plugins** at plugin grain + the tools (recon = ONE component, registrations as aliases; assay and parked radix arrive via the sweep). Round two re-cuts into four-kind units later — plugin-grain type_urls stay valid and gain `superseded/related` edges when sub-components mint.
- **The generative-loop bench, dirt-road grade** (PR6 answered in discourse 2026-07-04 — pending ratification): **Bodhi** = exists as an agent, manifest work only. **Dagstra** = an Agent + a composition-methodology Skill driving the matrix Capability (no formalism at this grade). **HADES** = an Agent emitting component *candidates*, admission-gated (GV11), never registering its own output.
- **The slick plugin** (a Flow): the authoring kit (manifest-model, registry-patterns) + the operators above. The loop as catalog citizen — the RQ1 self-referential-use test made artifact. **Invariant: the wall stays outside the plugin. Operators emit marks; none mint warrants (GV1/GV3).**
- **First-composition members:** 2–3 components authored against the SHIPPED matrix protocol.
- **The composer:** Claude + the slick plugin — a prompt/skill mapping catalog manifests → matrix Container registrations `[(type_url, config), …]`. No Dagstra-formal, no slickr, no matrix changes.

## 4. FIRST COMPOSITION — the milestone after the §2 rulings

Verified from three directions: **NO slickit changes needed** (slick has no orchestration surface; matrix never imports it; gnx bypasses the broken crust by ruling).

1. RULE: the minimal Flow form — GEP-0003's members list; matrix derives all topology. Ports-derivation, nesting, leashes deferred until one Flow has run.
2. RULE (a paragraph, not an API): the seed-input convention — external input enters via factory-closure constructor config; roots declare `consumes = ∅`.
3. BUILD (half a day): regenerate matrix's four stale docs, or mandate authoring-from-code.
4. BUILD: the member components; the composer skill; a ~20-line caller-side persistence shim (dump `construct.ledger` post-run — matrix refuses persistence by ruling; do not design a layer).
5. ACCEPTANCE: the run closes the never-executed 2026-04-01 step-1 bridge proof — gnx manifest → composer → matrix components → Construct written and persisted. No such path has ever existed end-to-end.

## 5. DEFERRED DESIGN — parked with owner and trigger, not floating

| Item | Why parked | Trigger / seed |
|---|---|---|
| RT5 merge-conflict semantics | Cannot fire on shipped matrix (duplicate producers statically rejected; batches sequential) | The Dagstra/streaming layer; seed = merge-strategy-on-schema-registration (the repudiated MergeStrategy stays dead) |
| RT10 table-vs-stream | An unratified CONVERGENCE (both sources lean typed-table-primary), not a contradiction | Public API freeze |
| SCC condensation + leashes | **Primary source unlocatable** (0 grep hits in the cited 06-02 doc) | Recover from the memex exports or downgrade the override to "proposed" BEFORE any leash work; first Flows acyclic by choice; fix the bad MOC/GR5 pointer |
| Multi-`produces` growth | No component needs two outputs yet | The first real 2-output component |
| Normative Construct spec + owner | No owner ruled anywhere; matrix's shipped Construct = the de-facto spec (and the validated ancestor) | External authoring opens; extract FROM matrix, lives with the runtime; gnx documents, doesn't own |
| Trust GATING + attested identity (SPIFFE/mTLS) | Advisory-first is the standing ruling | After the wall is sound; population needs only the credential + schema field (§2.2) |
| Protocol-location (the M plane's open question, MF7) | On-Manifest (`repeated Protocol protocols`, the designed successor to the cut `invoke`) vs inside `Capability.spec.implementation` (May settlement leans this). In neither dive's scope | Binds when a component with a real non-CC transport enters the catalog (geist-edge adapters); nothing at population — Skills have no protocol (Skill+protocol = representable type error), cix plugins are CC-shaped, first-Flow members are in-process |
| Dagstra-formal / slickr / streaming-Flux / capability negotiation / typed-config validation / matrix persistence | Out of scope (2026-07-02), refused-by-ruling, or no consumer yet | Evidence-driven (RT2); the one-shot Mono is the blessed degenerate case |
| Automated intake gates + out-of-family judge | Bind only when intake automates | Record the H6 paradox as a constraint now; build later. Kavi's calibration cliff = the manifest-vs-artifact verification step the automated pipeline must include; monoculture risk → out-of-family verify (the Gemini ship-gate pattern) |
| ECDS-style dynamic config delivery | Delivery mechanics | Runtime milestone; catalog-as-control-plane already accommodates it |
| The "gnx'd marketplace" framework generalization | Front-loads GV16 federation + the Sybil hinge | After the wall + cix intake proves the intake boundary on the home catalog |
| slickit hygiene track | Off both critical paths | Fix the Python-crust ImportError (and list it), finish the cix.capability rename, pin wasm-bindgen, commit the 06-01 skill rebuild with the geist-edge pointer fixed |

## 6. Name registry — collisions; gloss, never merge

- **Frame ×3:** (1) Construct-substrate units — typed/keyed, per-table append-only; whether "Frame" names the table or the row awaits the 05-02 ratification (§2.6). *This is the going-forward sense of "typed data frames."* (2) The radix comprehension Frame — monolithic per v2 (05-30, reaffirmed 06-02); the subtypes survive only as the storage/decay framework. (3) The CIP wire frame — 5-field, deferred out of Construct scope.
- **Construct ×2:** the matrix runtime board (the one this ledger means) vs radix's gated-write comprehension board.
- **TypedStruct ×2:** slick's factory INPUT vs matrix's component OUTPUT — same xDS root, opposite lifecycle ends.
- **ACES ×2:** the REP-001 antifragile doctrine vs the multi-agent case-study system. Noticed 2026-07-04.
- **MSG's "M" ×2½:** the shipped header glosses **M = Manifest** in the bullet but says "pure *Mechanics*" in the same sentence; the June transport-axis docs use M = "how to reach a component" (protocol); samsara's grid applies mechanics/semantics/governance as three planes *of each kind*. S and G are stable; pin M's expansion when the header next gets touched.
- **luminex ×3** (CT5) and the **GNX expansion itself** (GR15/H6) — both unresolved, both mint-once.

## 7. Ratification & bookkeeping debt — cheap, honest, lands with the first population commit

The 05-02 ontology sign-off (§2.6) · the bullock-cart fork: record a one-line adoption of the CIP layering (bookkeeping, not design) · execute the already-asserted Verified-grade amendment + ratify the Avowed/Warranted names (H6) · the GR4 register correction (done 2026-07-04) · the MOC/GR5 pointer fix · the MOC correction that the samsara "8" is radix *ports*, not slick kinds (GR2's evidence base shrank) · the slick CLAUDE.md geist-edge pointer (stale doc, not a placement fork) · **commit the working tree** — the entire authoritative layer (the gnx pipeline, the GEPs, the June re-groundings, slick's skills) is untracked.

## 8. Session-settled design (2026-07-04 discourse) — recorded; GEP'd where marked

- **Flow = the serialized composition root** → **GEP-0003 (drafted)**: members as TypedStructs, topology derived, never declared; triple derivation (ACES · matrix `container.py` · xDS TypedExtensionConfig); N:1 project-plugin = a Flow projected.
- **Repo layout = ground-truth §8a adopted:** `api/` + `components/{capabilities,agents,skills,flows}/` + `extensions/<target>/` + generated `plugins/`. Kind-in-path is safe (two-way) only because kind never enters type_urls; the linter enforces dir↔manifest agreement.
- **The defined-vs-opaque config fork** resolved by the xDS precedent: both, with an upgrade path — validate against the `api/` schema when present, ride opaque TypedStruct until then.
- **PR6 direction (pending ratification):** Bodhi/Dagstra/HADES = catalog components in the slick plugin, dirt-road grade; the wall stays outside.
- **gnx framing:** mech suit (the Norman-artifact reading, RQ8-aligned) for the agent-facing story; parts-certification (the wall) for the governance story. The framework/"gnx'd marketplaces" move = the intake boundary generalized — sequenced AFTER the wall proves out at home (§5).

## 9. Still owed to yzavyas — unchanged by all of the above

H1 autopoiesis wording (the reframe language is ready) · H2 credential form (§2.2 narrows it) · H3 CLI language · H4 the radix-import fork · H5 the dao sense · H6 name mints (now + the GNX expansion + Frame table/row + the maturity value-space + the ACES gloss) · H7 the ilm amendments · H8 mission framing · H9 the Avowed laundering guard · H10 format/layout (§2 leans set) · the round-two grain (§2.4's long pole) · PR6 ratification (§8) · the GEP-0001 casing pick.

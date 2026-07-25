# Decision ledger

The async touch-base surface for the docsite build. One-way doors (frame-shaping, expensive
to reverse) are closed in collaboration; two-way doors (reversible) are taken autonomously
with a default + the condition that would flip them. A fast-growing queue of open two-way
doors is backpressure — it means a one-way door was left open and is spawning ambiguity.

Shape per entry: **DECISION** · type · **why** · *what would change it*.

---

## Closed — entering production (2026-06-15)

**D1 — Section taxonomy: two-register, need-cut.**  one-way.
Public register cut by need (Diátaxis): **Start** (orientation + spine, shipped-only) ·
**Guides** (how-to, by task) · **Reference** (lookup, per-claim status) · **Explanation**
(the why, public-safe). Internal register, blended on purpose: **Design** (the 01–13
dossier) · **The Build** (rubric, eval, method, provenance). Cross-cutting **agent-IA
layer** (llms.txt, markdown twins, status frontmatter).
*Why:* audience-cut (User Guide / Dev Guide) is the documented NN/g + Diátaxis anti-pattern —
registry users compose AND author, so they aren't mutually exclusive; `arranging` already
defaults to need-cut; it collapses onto the public/internal boundary ground-truth §11 drew.
*What would change it:* if the public audiences were genuinely non-overlapping. They're not.

**D2 — Maturity markers are a machine-parseable safety gate.**  one-way.
Every capability claim carries `shipped | planned | proposed` in frontmatter, rendered as a
badge. shipped → may be imperative how-to/tutorial; planned/proposed → explanation/reference
register only, never command an unbuilt action.
*Why:* an agent ingests docs as context and acts — an unmarked "planned" claim becomes a
failed tool call. This is the project's honesty invariant, not editorial taste.
*What would change it:* nothing — it's load-bearing.

**D3 — Produce now with installed craft-rhetoric; crystallize the doc skill later.**  two-way.
Use the installed `craft-rhetoric` (vyasa/feynman/sagan/orwell/tufte/ebert) to produce the
docsite this session. The two genuinely-new disciplines surfaced by research (maturity-as-
safety-gate, the agent-IA layer) are applied as production constraints now and crystallized
into a `documenting` skill in craft-rhetoric afterward.
*Why:* don't build a tool before running the process once; craft-rhetoric's `arranging`/
`rhetoric` already cover most of the field. *What would change it:* if we were about to run
this protocol across many projects (amortization favors the tool).

**D4 — This build is the convergence-to-buildable milestone.**  one-way (leaning).
The doc set reaching ship-gate green = every ground-truth §10 open question closed or
explicitly marked deferred-to-build; then gnx implementation starts. This is the last
pure-design artifact, not one more pass.
*Why:* writing all registers until they reconcile is the coherence-forcing function.
*What would change it:* a hard GTM date needing a shippable public subset faster than full
M1–M7 — then front-load Start + Guides, defer the rest.

**D5 — Production method: the craft-rhetoric pipeline, per milestone.**  process.
vyasa architects → feynman/sagan draft from ground-truth → orwell voices → ebert + gemini
gate. Checkpoint with the principal at each milestone (M1–M7).

## Closed — 2026-07-02

**D6 — Final-version prose; maturity is metadata.**  one-way (yzavyas).
The docs read as the definitive reference — things are described fully whether built or
not. No "not built yet / nothing here runs today" prose banners, no design-intent tense
hedging, no inline "(planned)" markers. Maturity lives in the machine/metadata layer:
frontmatter `status:` (badge + llms.txt markers), the status page (the one honesty
surface), and grammar-reference's per-claim maturity marks (spec practice, kept).
*Why:* the disclaimers were throat-clearing repeated on every page; the metadata layer
already carries D2's honesty invariant for agents.
*Amends D2's prose application only* — D2's machine layer (frontmatter status,
never masking a planned claim as a runnable how-to imperative) stays load-bearing.

**D7 — Private wing is one config-driven path family.**  two-way (yzavyas: "public paths
and private paths", "clean, ACE, configuration driven").
`/docs/*` = the public family; `/dossier/[group]/[slug]` = the private family, one
dynamic route validated against `$lib/registers.ts` (spec / gep / appendix). Adding an
internal register = one config entry + a content/<section>/ dir — no new routes.
*Flip:* if a family ever needs a genuinely different page shell.

**D8 — GEPs are the chartered design-decision surface.**  one-way (leaning).
Rust-RFC-style proposals at /dossier/gep/*; GEP-0000 defines the process + the gap
register. Answers ground-truth §10.10 (no chartered research home). Grammar decisions
route through GEPs before landing in ground truth/specs.

## Closed — 2026-07-04

**D9 — Session grounding lands as dossier drafts, not doctrine edits.**  two-way (files), per D8.
GEP-0001 (identity grammar) · GEP-0002 (provides value-space) · GEP-0003 (Flow = the
composition root) · GEP-0009 (overlay contract, register addendum G12) drafted to
`content/gep/`; the ecosystem ground plan ported to `content/build/1-ground-plan.md`
(→ /dossier/appendix/ground-plan); surface.json tensions t.fieldnames / t.flow-cycles /
t.produce-authority updated with dated dive results. Ground-truth untouched — every
ruling stays owed (the H-register).
*Why:* D8 routes grammar/catalog decisions through GEPs; the convergence surface carries
drafts-awaiting-ruling rather than silent doctrine edits.
*What would change it:* ratification — an accepted GEP amends ground truth and retires
its tension.

## Closed — 2026-07-05

**D10 — Public register presents gnx standalone.**  one-way (yzavyas).
The public wing tells the gnx story on its own feet: **a mech suit for agents** — an agent
puts on the catalog, composes from it, and generates extensions for itself, with a human
governing what the catalog trusts. slick appears as a dependency citation (the manifest
grammar), never as ecosystem positioning. The ecosystem layer — triad, grammar lineage,
runtime, the wall's internals — lives dossier-side only.
*Why:* gnx IS a standalone project; the public reader needs the product, not the cosmology.
Sharpens (does not amend) the standing register discipline.
*What would change it:* nothing foreseeable — register discipline is load-bearing.

**D11 — Clean-room docsite regeneration.**  two-way (yzavyas: "clear out the app, preserve
the content, re-generate the content").
The app's presentation layer is rebuilt clean (landing genesis animation: heptagon = the
machine, spark = the human, triskelion with straight arms = emergence through extensions);
the ratified mechanics are preserved, not regressed: content-driven loader, the two
config-driven wings (D7), Commentable + file-backed feedback, /llms.txt + /raw twins,
/catalog live manifest reads, register 307 enforcement. Content is regenerated from the
2026-07-04/05 grounding under D10's register. Pre-clear backup: scratch/app-backup-2026-07-05/.
*Flip:* if the rebuilt shell regresses a ratified mechanic, restore from backup and re-cut.

**D12 — cix is dissolved into gnx; the public register carries no cix brand.**  one-way (yzavyas).
The plugins are **gnx components** — named on their own feet (craft-rhetoric, guild-arch,
ci-scaffolds…), being ported into the catalog as components/capabilities (plugin-grain
adoption now; round-two four-kind decomposition later). Public sweep: no "cix plugin
family"; marketplace config examples use the user-chosen `gnx` key (the key IS user-chosen —
`craft-rhetoric@gnx`); type_url examples use gnx namespaces; tool mentions drop the cix
umbrella. cix survives dossier-side only, as lineage and intake provenance — the same move
GEP-0001 makes at the identity layer, applied at the brand layer.
*Why:* succession is adoption, not co-branding; the succession decree archived cix.
*What would change it:* nothing — naming authority exercised.

**D13 — Identity casing: kebab-case resources.**  one-way-adjacent (yzavyas, 2026-07-06 — GEP-0001's
owed pick). `gnx.dev.v1.intent-hardening`, not `…IntentHardening`. Matches all real components on
disk, directory names, CC plugin names — one case class everywhere; the migration cost falls on
docs and doctests, not artifacts. Propagate to the losing side (grammar-reference examples, any
PascalCase residue) in the same pass.
*Why:* resources double as directory and plugin names, which are kebab-native.
*What would change it:* nothing after first mint — serialized type_urls are permanent.

**D14 — The provides shape rule is ratified.**  two-way convention (yzavyas, 2026-07-06 — GEP-0002).
An entry that parses under the identity grammar is a **port** (joins topology); dotted-lowercase
is a **discovery tag** (search only, never joins). Skills legitimately carry tags alone. The three
real components' manifests stay valid as-is; controlled tag vocabulary stays deferred.
*Flip:* the first component the parse rule cannot serve re-opens split-fields.

**D15 — The expansion is ratified: gnx = generative noetic extensions.**  mint-once (yzavyas,
2026-07-06). Already in the wild via the cix archive notice ("generative noetic extensions for
agents"); the public wing states it once, plainly, on the landing/what-is surface.
*Why:* naming authority exercised; the de-facto public statement becomes the de-jure one.

**D16 — Public naming boundary: gnx + slick + Claude Code.**  two-way (yzavyas, 2026-07-06).
slick is named publicly as the manifest format/kit (a real Apache-2.0 dependency); Claude Code as
the first target. matrix / geist.sh / geistr / mox.hud stay internal-register only; public prose
says "a runtime" abstractly where a runtime must be mentioned.
*Flip:* when a second runtime target ships publicly, revisit what the public wing may name.

**D17 — Register restructure + clean-room regeneration.**  two-way (yzavyas, 2026-07-06).
The GEP register moves to the **public** wing (open design in the open; GIPs join it when minted).
A new **ecosystem** section carries the internal wing: semantic architecture, ecosystem map,
reliability/honesty ledger, bootstrap plan, supersessions — the alignment layer that must not
inundate gnx-the-product's users. All content regenerated fresh (clean room on the concepts), with
a two-gate verification discipline: in-family grounding checks against shipped artifacts, then an
out-of-family review gate (agy/Gemini — the ship-gate pattern; same-family judges anti-correlate).
*Flip:* if the public GEP register confuses more than it invites, it can retreat to internal — the
section register is one line in content.ts.

**D18 — The docsite out-of-family gate is waived for revision passes.**  two-way (yzavyas, 2026-07-13,
in-session). The agy/Gemini diagonal ran for the passes that built the site (D17's two-gate
discipline); for revision passes over pages that already cleared it, same-family review (propagation
gate + adversarial invariants check) carries the gate. The dao doctrine's diagonal axis
(ground-truth §6a) stands as doctrine; this ruling scopes only the docsite's own revision workflow.
*Why:* the out-of-family judge's precision on revision-scale diffs was poor (10 of 14 findings
misfires on 2026-07-13); the owner ruled the residual value not worth the loop.
*Flip:* any word from yzavyas, or a revision defect that same-family review demonstrably missed.

**D19 — "Mark" renamed to "attestation" in the ledger vocabulary.**  two-way until serialization
(yzavyas, 2026-07-19, in-session). The typed defeasible claim an operator writes to the Construct
ledger is an **attestation**, not a "mark": agents emit attestations; warrants accredit them; the
human mints warrants (GV1/GV4 unchanged — only the noun renames). *Why:* "mark" collided with the
docsite's annotation marks and the dossier grammar's epistemic/reversibility marks, and read as
opaque on first contact; "attestation" is the standard supply-chain-security term (in-toto, SLSA)
for an authored, signed assertion about an artifact — which is exactly the semantics the wall
checks (author disjointness). The mark/warrant *distinction* is untouched; components (the goods)
remain a separate object from attestations (the paperwork about the goods). Scope: gnx doctrine
surfaces now; slick thesis docs (`~/mox/research/drafts/slick/`, REP-004) carry the old term and
owe a rename pass at next touch; scratch session records stay as written (history, not doctrine).
*Flip:* free until an `api/` schema serializes the name; D13 locks it then.

**D20 — The review surface: inquiry gets its own genre.**  two-way (yzavyas, 2026-07-20,
in-session). A **review** is the inquiry twin of the dossier, split by addressee+act: the dossier
equips a *ruling* (commitment apparatus — response menus, one-way friction); a review equips
*inquiry* — a research artifact (synthesis, extraction set) rendered annotatable, with an
open-questions register. The river flows until every question is **answered** by more research,
**spawned** as its own mission, or dissolved; then the review is marked settled. Feedback grammar
gains kinds `question` and `spawn` (append-only, same JSONL store); reviews are registered in
`docs/reviews.json` pointing at research artifacts in place (no copies — single source). Internal
register; excluded from public builds. First subject: the self-evolving-agents synthesis.
*Flip:* if reviews and dossier pages converge in practice, fold the questions rail into the
dossier surface and retire the route.

**D21 — Intake naming and grain ruled (closes D-CR3/D-CR4): aspect-named, decomposed by use.**
one-way at mint (yzavyas, 2026-07-23, in-session, per the 2026-07-23 guild recommendation).
A ported unit's `type_url` names the **aspect** — the capability the figure carries
(`gnx.dev.v1.claim-extraction`, `gnx.dev.v1.voice-preservation`) — never the persona (`orwell`)
and never a plugin prefix (`craft-rhetoric-orwell`). Persona and bundle-of-origin ride as tags
and `relations`; the persona binds at seat level (projection config). Grain follows real use:
pipeline plugins (craft-research, craft-rhetoric) intake as member components + a Flow;
council/bundle plugins (guild-arch) as a Capability bundle + member Agents; standalone skills as
Skills. Agent port surfaces mint at `maturity: designed` until GEP-0005 rules the Agent surface.
*Why:* three independent sources converge against persona-in-identity (dao-corpus agent-craft,
M1 "the name is decoration", envoy name-vs-type); prefixing smuggles bundle coupling into a
permanent string; capability names make provides-collisions surface real overlaps for the C2
dup-check instead of hiding them. *Flip:* free until a given `type_url` serializes into
`components/`; mint-once locks each name at its mint (GEP-0001).

**D22 — Docs separation: one tree, two builds.**  two-way (yzavyas, 2026-07-23, in-session).
The register field stays the single source of truth. gh-pages serves a **static build of the
public register only** (`GNX_PUBLIC_BUILD=1`, adapter-static, vyasa-arranged reading path);
the internal register never builds for Pages — it stays markdown + the local dev server, where
margins, reviews, and decision surfaces live (file-backed server routes Pages can't run anyway).
*Why:* physically forking the trees makes the same concept live twice and drift; the build gate
already enforces the boundary at one point. *Flip:* if the public site outgrows static (search,
live catalog), it moves to a server host — the register split survives that unchanged.

**D23 — Identities go semantically light (amends D21): short names; semantics live in the manifest.**
one-way at mint (yzavyas, 2026-07-24, in-session). A `type_url`'s resource is a short, memorable
name (`gnx.dev.v1.bodhi`, `gnx.dev.v1.scrutiny`, `slick.dev.v1.slickit`) — a brand, not a
description. The ontology splits into three layers: **identity** (permanent join key, carries no
semantic claims), **ports** (the hard ontology — what a component consumes/emits IS its scope),
**discovery** (the soft ontology — tags, revisable by rule, plus a one-line `description`
overlay field, added via GEP-0009, feeding catalog cards and semantic search; the deep surface
stays `--skill`/SKILL.md). *Why:* mint-once makes a meaningful name a frozen claim —
`research-scrutiny` froze an ontology cut (scrutiny-is-research-scoped) the method doesn't
support; domain scope belongs in ports and tags, which can grow. The dup-check never trusted
names anyway (C2 fingerprints behavior). Precedent: every registry at scale (serde, tokio) —
brands + description-search. Survives from D21: the anti-prefix rule and no-kind-in-identity.
Dissolved: the persona worry — `bodhi`/`orwell` are fine as brands *because* the name carries no
ontology weight; seat/persona binding stays at projection. `claim-extraction` (already minted)
keeps its name — alias-forever, verbose ≠ wrong. *Flip:* per-name at mint; the layer rule flips
only if description/tag search measurably fails to carry discovery.

**D24 — Catalog surfaces: excalidraw map + Svelte Flow composer, on the docsite.**  two-way
(yzavyas, 2026-07-24, in-session). Two views: the **catalog map** — the component landscape as a
real `.excalidraw` scene (editable anywhere, rendered on the site); the **composer** — a Svelte
Flow node editor where ports wire and the grammar validates live (GEP-0001/0002/0003 rules
ported to TS — check.py's rules in the page). *Why:* a whiteboard is for the landscape, a
node-graph editor is for typed composition; each tool does what it's for; both native to the
site rather than an external artifact. *Flip:* if the map and composer converge in use, fold
the map into the composer's read-only mode.

**D25 — RULED (yzavyas, 2026-07-25) — Kind is an open, namespaced vocabulary, not an enum.**
Accepted as redrafted below (two-slot structure). Same ruling settles the companion namespace
question: **slick takes `slick.dev` — a vendor namespace, not gnx.dev adoption.** slickit is a
separate project with its own authority (`~/mox/packages/slick`: the crate + CLI — the real
slick; `~/mox/products/slick` is design memos, a shell). gnx adopting it under `gnx.dev` would
assert authorship gnx does not have; the vendor-namespace mechanism this GEP-lineage was built
to demonstrate is exactly the right instrument. **Unblocks the slick and hades mints.** A closed kind enum is a stasis force: it forecloses vendor extension
and has already been hit three times in bootstrap (radix `Processor`, the Flow-wearing-skill-
clothing, View/Shell). The established design (Oct-2025 slick memex export,
`drafts/slick/sources/conv-2025-10-28`) already ruled this: a minimal core kind set + vendor
kinds via API-group namespaces (the k8s CRD pattern — Skill was explicitly positioned as
Anthropic's vendor extension `skills.claude.anthropic.com/v1`, the extensibility proof point;
Tool and Resource were *merged into* Capability with labels, not taxonomy).

**REDRAFTED 2026-07-25 around the two-slot structure (resolves the "why not kind in the
namespace" objection yzavyas raised twice).** The objection was right and the earlier framing
was wrong: xDS *does* encode structure in a namespace — verified in `~/oss/reference` (Envoy
config, e.g. `kgateway/test/deployer/testdata/envoy-log-json-out.yaml:49-51`) — but it uses
**two slots**, and gnx had one slot doing both jobs:

```
- name: envoy.filters.http.router                                   ← instance name
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router   ← config message type
```

Adopt both slots: **identity** (Envoy's `name`) = `gnx.dev.v1.bodhi` — mint-once,
alias-forever, semantically light, no kind; **config type** (Envoy's `@type`) =
`gnx.dev.kinds.agent.v1.AgentSpec` — kind-structured *and versioned*. Kind-in-namespace is
safe here precisely because the config-type slot is versioned: Envoy restructures v2→v3 while
names stay stable, so a mis-typed component repoints its config type without breaking its
identity or anyone's join key. This dissolves the mint-once objection that made D21/D23 keep
kind out of the identity — that rule stands unchanged; the kind simply lives in the other slot.
It also collapses the proto-seam decision into this one: **the config-type slot IS the proto
message**, so kind declaration and port contract are one mechanism, not two.

Mechanics: (1) unqualified `kind:` = core, under gnx.dev authority; qualified = vendor
namespace. (2) Draft-time unknown kind = warning (check.py's current behavior, now
principled); **mint-time undeclared kind = error** — a kind must be declared before anything
mints against it; declaring is cheap, namespaced, and ledgered. (3) A kind declaration carries
its grammar: composition rules, payload expectations, portability default; undeclared/foreign
kinds compose conservatively (opaque). (4) Adapters and runtimes declare which kinds they
support (Oct-2025 design). (5) The portability taxonomy
(Universal/Specialized/Vendor-Specific/Multi-Vendor) rides as tags — soft layer per D23, never
identity. (6) The invariant that governs the mint-gate, from the 2026-06-03 export: **"a fixed
set of composition rules while the component catalog grows unbounded… like adding vocabulary
without rewriting grammar."** A new *component* is vocabulary (free); a new *kind* is grammar
(gated).

**Core set grows by two, and two candidates are rejected as rung errors** (Fable calls,
2026-07-25): **View** — a component whose payload renders; adopted. **Processor** — pure
inputs→outputs, no side effects; adopted, and no longer an anomaly now that D27 adopts matrix,
whose runtime has processors as a first-class concept (radix's standing `Processor` warning
resolves here). **Shell — rejected**: a shell is a *View that hosts Views* (a View with slot
ports), i.e. a composition pattern; minting it would freeze a pattern into grammar.
**Observable — rejected**: same rung error as antifragility per D28 — a claim about behavior,
not about what a thing *is*. Telemetry needs no kind; per D26 it is a typed event stream, i.e.
a port on an existing component. **Sub-question resolved in-session (yzavyas,
2026-07-25):** Skill was Anthropic-specific *before it was adopted as an open standard* — the
Oct-2025 export predates the adoption. Skill-as-core is therefore the post-adoption reading,
not a break with the lineage; the vendor-kind mechanism it demonstrated survives as the
pattern for future vendor extensions. Note: this also dates the corpus — the Oct-2025 slick
understanding is NOT current; two newer memex exports (Google Drive, two accounts) are staged
for intake before the slick namespace ruling. *Flip:* two-way until the
kind registry serializes; each kind declaration is then mint-once like any identity.

**D26 (PROPOSED — Fable, 2026-07-25; awaiting yzavyas ruling) — Capability contracts are
protocol-independent; transports are adapters.** A Capability's contract is its typed seam
(the `gnx.dev.v1` proto messages, per the proto-seam decision) — never a transport. Adapters
bind the contract onto existing access protocols (MCP server, CLI, library, HTTP/ext_proc),
are declared in the manifest as bindings, and are largely generatable from the contract.
Established: the recon collector (transport-agnostic collector + normalizer to canonical
shape, geist-architecture exports 2026-03-28) and the AGS data-plane principle ("business
logic portable, reusable, protocol-agnostic" behind the slick contract layer, 2025-10-05
export). *Why:* binding a capability to MCP-or-CLI in its contract couples the semantic unit
to a transport's lifespan; the three-axes rule (namespace/transport/adapter) keeps them
orthogonal. *Flip:* two-way until the first adapter field serializes into a minted manifest.

**D27 — Adopt matrix as gnx's mechanical runtime (closes the runtime fork; geistr not built).**
two-way at this stage (yzavyas, 2026-07-25, in-session: "yeah sure we can adopt matrix").
Flows that need mechanical execution run on matrix — the Component runtime substrate
(Components, **Construct = append-only ledger**, DAG topology, async execution, agent-agnostic;
`~/mox/products/slick/architecture.md`). The CC-projection remains the default path for
agent-carrying Flows; matrix is the mechanical option, no longer hypothetical. *Why:* the
2026-07-23 reconciliation found matrix GREEN (119 tests; the 07-06 "broken" was a stale README
on the pre-TypedStruct API) with live use via ix per trial; yzavyas's "agents need an agent
runtime" met the briefing's own flip condition; and Construct-as-append-only-ledger is the same
primitive as GEP-0008's accreditation ledger and slick's manifest-as-ledger — three independent
arrivals. *Flip:* two-way until a gnx component depends on a matrix API; revisit geistr only if
matrix's shape fights the Flow grammar in practice.

**D29 — RULED (yzavyas, 2026-07-25) — `fidelity` is deleted as a frontmatter field.** It was
parsed into `DocMeta` and rendered by nothing — authored in 32 docs, consumed by zero consumers.
Removed from all content files, from `DocMeta`, and from the parser. The dirt-road → cobblestone
→ tarmac *standard* survives as a quality bar (the public docsite ships at tarmac, revised to it
before this deletion); what dies is the unread field claiming it. Caught by the new frontmatter
guard's ORPHAN sweep (`bun run check:docs`) — the general rule being: **a field no consumer reads
is fluff by definition, and that is mechanically detectable.** *Flip:* if fidelity ever earns a
rendered badge, it returns as a rendered field — never as an unread one.

**D28 (PROPOSED — Fable, 2026-07-25) — Antifragility is a demonstrated property of gnx, not an
ontological claim.** yzavyas asked to include antifragility as a property of gnx; the corpus
supplies the discipline for how (memex intake 2026-07-25, `scratch/memex-intake-2026-07-25.md`
F3/F4). The rung rule, verbatim from the 2026-06-08 export: antifragility is "a claim about
behavior under stress, not part of the essence… a property you'd have to **demonstrate**… not
assert in a definition." ACES = the structural properties (adaptable/composable/extensible);
antifragile = the time-and-stress behavior they buy. Proposed: (1) antifragility never appears
as an adjective in the gnx ontology, README, or positioning; (2) it enters as a **claim with a
demonstration obligation**, recorded in the GEP-0008 accreditation ledger like any other
measured property; (3) the four composite conditions (antifragile components, bulkheading with
feedback, composition-layer diversity, capability mediation as glue) are the design checklist,
**with the transfer caution logged**: capability mediation there is a *runtime* substrate
mechanism (gestalt/mox.nexus, seL4/POLA), while gnx's `Capability` is a design-time catalog
kind — gnx does not enforce mediation at runtime today, and must not claim to. D27's matrix
adoption is what would eventually give that claim a home. *Open:* own GEP vs riding GEP-0008's
ledger. *Flip:* free — nothing serializes.

**D26 amendment (same date, from the intake):** adapters are not only transport bindings — the
established design calls them "the plug-and-play layer, **transforming components into
components**" (2026-06-03 export). The ruling should cover both halves: transport adapters
(contract → protocol) and component adapters (component → component). Also carried into the
proto-seam rationale: slick's grammar is "the **decidable skeleton**" and the agent does "the
undecidable semantic part that no type system can handle" — proto types the seam, it does not
close the semantic gap, and gnx must not claim otherwise.

## Open two-way doors (autonomous defaults; flag if the list grows)

- Public-landing voice register — cix·operator (IBM Plex Mono, tool-ness) vs blueprints·
  billboard (DM Mono, loud). Default: operator for docs. *Flip:* if the public marketplace
  landing wants the louder voice.
- On-disk layout for new public docs — `content/<section>/<slug>.md` subdirs (chosen) vs
  flat numbered. Reversible.
- Ground-truth placement — currently anchors Design (internal). May split a public
  Reference from it once that section is written.

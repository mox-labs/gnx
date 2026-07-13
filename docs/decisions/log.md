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

## Open two-way doors (autonomous defaults; flag if the list grows)

- Public-landing voice register — cix·operator (IBM Plex Mono, tool-ness) vs blueprints·
  billboard (DM Mono, loud). Default: operator for docs. *Flip:* if the public marketplace
  landing wants the louder voice.
- On-disk layout for new public docs — `content/<section>/<slug>.md` subdirs (chosen) vs
  flat numbered. Reversible.
- Ground-truth placement — currently anchors Design (internal). May split a public
  Reference from it once that section is written.

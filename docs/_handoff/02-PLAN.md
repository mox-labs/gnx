# Execution plan — the polished gnx doc site, by iteration and milestone

Doc-driven design: the doc set's maturity **is** the spec's maturity. The docsite is the
convergence surface for the shared understanding between yzavyas and Claude (the comment
loop is interpretant-exposure → convergence), a coherence-forcing function (writing the
user + dev + design views forces the design to reconcile — discrepancies are design
gaps), and the session-bootstrap substrate (any session reads it and gets the point).

Built on **ci-scaffolds discipline** (internalized 2026-06-14):
- **Fidelity ladder** — dirt road (happy path) → cobble (edges + tests) → tarmac
  (hardened). State target fidelity before building each thing. The trap is tarmac-ing
  everything (waste) or dirt-roading everything (debt). Milestones are fidelity gates.
- **Scaffolding order** per iteration — foundation → implementation → integration →
  cleanup. Cleanup is not optional.
- **Task stewardship** — Claude generates, yzavyas comprehends/reviews/refines/authorizes
  (the 86% pattern). Checkpoints at iteration boundaries: "does this match your intent?"
- **Falsification before advocacy** — every recommendation carries its strongest
  counter-argument. No binary framing of orthogonal dimensions.
- **Crystallization** — each iteration leaves memory + the docsite smarter.

---

## Phase 0 — Internalize, then orient  ·  fidelity: n/a  ·  gate: working method stated

1. Read, in order: `~/.claude/CLAUDE.md` (the constitution), the **ci-scaffolds** skills
   (`crafting`, `collaborating`, `problem-solving` at
   `~/.claude/plugins/cache/cix/ci-scaffolds/0.6.0/skills/`), the memory files, then
   `ground-truth.md` and the existing `content/01-13`.
2. Crystallize the working method for this build in one short note: fidelity targets,
   checkpoint cadence, who authorizes what. This is the steward contract.

Gate: a one-paragraph method statement the principal can confirm before any building.

## Phase 1 — Architecture + the orientation spine (vyasa)  ·  fidelity: cobble  ·  gate: content map + spine

1. **craft-rhetoric:vyasa** designs: the section taxonomy (Start / User Guide / Dev Guide
   / Design / The Build / Reference), per-section doc inventory, nav order, reading paths,
   and which existing docs move where.
2. **The orientation spine** is a first-class deliverable, not a byproduct: the tight
   "get the point in ~5 minutes / a fresh session is oriented" path that sits *on top of*
   the full depth. Name its 4–6 stops explicitly. (Bootstrap goal: a new session reads
   the spine and can act.) Consider wiring it to the project's SessionStart/CLAUDE.md so
   context acquisition is automatic.
3. Merge `01-RUBRIC-EXTENSION.md` into `docs/RUBRIC.md` (criteria H/I/J, the
   spec-maturity reframe of H, the register-profile table, and the component ACE/
   antifragile rubric). Add per-doc `register:` + `fidelity:` + `maturity:` frontmatter.
4. Write the result to `docs/_handoff/CONTENT-MAP.md`.

Gate: a content map with the spine named, every doc tagged (section/register/fidelity/
maturity), no orphans, no audience served twice.

## Phase 2 — Infrastructure + brand adoption  ·  fidelity: cobble  ·  gate: multi-section nav, on-brand, islands intact

Two parts. (a) **Structure**: extend `content.ts` section union + read frontmatter
(register/fidelity/maturity); extend `+layout.svelte` nav into the section groups + render
the orientation spine prominently; redesign the cover as a multi-audience entry; add a
per-doc maturity/fidelity badge.

(b) **Lift cix's brand layer** (see `03-DESIGN.md` — the current look is improvised; gnx
inherits the cix brand directly, sigil included). Copy `tokens.css` + `typography.css`,
the sigil SVGs, and the `Sigil`/`CanonicalSigil`/`BrandHero` components from
`~/mox/products/cix/docs/experience/`; replace the improvised `app.css` palette/type with
them. Recolor the docsite chrome by role (Spark `--ci-blue` = links/emphasis, Constraint
`--ci-red` = RETURN/boundaries, Emergence `--ci-green` = SHIP/code); retheme the diagrams
to trichrome-by-role. Wire the maturity badges + established/open markers to the
**evidence-brightness** scale. Place the cix sigil as cover mark + favicon (it's gnx's
mark). cix's `typography.css` gives the cix·operator (IBM Plex Mono, tool-ness) register
for docs by default; the public landing may later want the louder blueprints·billboard
voice.

Gate: `bun run check` clean; existing docs render *on-brand*; composer + both decision
explorers + select-to-comment verified in-browser; sanity-checked against
`brand/examples/gnx-dark.png`.

## Phase 3 — Content, as iterations  ·  the body of the work

Each iteration is a milestone at a stated fidelity. Per iteration: foundation
(structure/frontmatter) → draft (feynman/sagan, register-aware) → voice (orwell) →
diagram (tufte where it earns it) → checkpoint with yzavyas → cleanup. Then the
ship-gate (Phase 4) runs per iteration, not just at the end.

**Iteration A — the spine + Start (M1).** Fidelity: tarmac (it's the front door and the
bootstrap path). The orientation spine pages + the multi-audience landing. This is what a
session reads to get the point; it must be right first.

**Iteration B — Design polish (M2).** Fidelity: tarmac. Move `content/01-13` into the
Design section; fix the two **owed re-gates** (10 projection, 13 composing — layout +
protocol sections went in directly). Promote the cross-cutting **ACE / antifragile
component rubric** out of docs 06/08 into an explicit, reusable standard page (the
Boundary Test, the three ACES properties, the degeneration watches — the bar every
component is held to).

**Iteration C — Dev Guide (M3).** Fidelity: cobble→tarmac. Verify every grammar claim
against slick source (criterion J). Pages: anatomy of a component (5-field manifest,
real) · `--skill` · ports (provides/requires, type_url) · config & the api/ schema
(mechanics; defined-vs-opaque; generation produces the API) · protocols (the transport
axes; the open fork via the decision explorer) · `gnx component init` (planned) ·
projecting to a plugin (the repo layout). Reuse composer + decision explorers.

**Iteration D — User Guide (M4).** Fidelity: cobble. Criterion G + H + I hard. Task pages,
all maturity-marked, bounded to registry/marketplace pragmatics: add the marketplace ·
install a plugin · `gnx init` (planned) · compose (composer island) · pick the core set ·
a "what's real vs planned" status page. No cosmology.

**Iteration E — The component set (M5).** Fidelity: matched per component (below). This is
"the plan for all the instruments/tools/components." Each gets a page: **design** (why
shaped this way) / **architecture** (how structured) / **affordances** (what it offers,
how you compose it), held to the ACE/antifragile rubric, maturity-marked. Writing each
one is the coherence-forcing function per component. Build in fidelity order — write the
design-complete ones fully, stub the vapor ones honestly:

| Fidelity now | Components | Note |
|---|---|---|
| **Tarmac** (design-complete; write fully) | aboot, luminex, ci-scaffolds, guild-arch, antifragile, craft-extensions, craft-research, craft-rhetoric, slick-as-plugin | shipped methodology or design-complete manifest |
| **Cobble** (designed, some open) | matrix, ix, assay, recon, memex, radix (text built / multimodal designed), post, niti, craft-evals→rubrix, dao-corpus | real tools or grounded designs |
| **Dirt road / honest stub** | bodhi, dagstra, hades (named loop agents, not built), veritas, labcoat (consciences), mudge (plan-only), constitution+stack-scaffolds (patterns, need componentizing) | name + design intent only |
| **Roadmap-only / flag** | cortex (REP-015 vapor — do NOT inflate), mashq (memory says shipped, **no repo found** — verify or mark missing) | a line, not a page |

**Iteration F — The Build / meta (M6).** Fidelity: cobble. The honest making-of: how this
site works · the ship-gate (rubric → gemini out-of-family eval → optimize, with the live
scorecard) · the feedback loop as the coordination pattern · the AI-collaboration method
(ci-scaffolds, the memory, this kit). Positioning call for yzavyas: public or internal.

**Iteration G — Reference (M7).** Fidelity: cobble. Ground truth (appendix), a
slick-verified grammar reference, the api/ schema index (schema *shape* as planned until
schemas exist).

## Phase 4 — Ship-gate (per iteration + final)  ·  gate: scorecard honest

Gemini out-of-family eval against the register-aware rubric (each eval told its doc's
register so it loads the right profile) → claude optimize → gemini re-eval. Re-gate the
owed 10/13 in Iteration B. Regenerate `docs/evaluation.json` with the H/I/J columns so the
Evaluation scorecard reflects the full site.

## Phase 5 — Polish + verify  ·  gate: in-browser pass

`bun run check` clean; all routes 200; comment on a *new* User-Guide block (anchored
highlight persists); composer compiles + rejects; a decision scenario flips the winner;
badges render. Clean up screenshot/`.playwright-mcp` artifacts with `rip`.

## Phase 6 — Close the original loop  ·  gate: committed

The git workflow yzavyas asked for **first** (still owed): lift cix's `.githooks/` + wire
`core.hooksPath` (cix never wired it — fix that); a `justfile` (gate SSOT, vaani pattern);
`CONTRIBUTING.md` (GitHub Flow, conventional commits); `.github/workflows/ci.yml` (lint +
typecheck + commit-lint); optional gitleaks from `~/mox/research`. Then commit on
`feat/docsite`, conventional commits, one logical change each, Co-Authored-By trailer.
Do not push or open a PR unless asked.

---

## Milestones (fidelity gates, in order)

- **M1** — orientation spine + Start, tarmac. *A session can get the point.*
- **M2** — Design section polished + the ACE/antifragile component rubric, tarmac. *The why is settled.*
- **M3** — Dev Guide, grammar-verified. *A dev could author a component (on paper).*
- **M4** — User Guide, maturity-honest. *A user could use gnx (where it exists).*
- **M5** — every component documented at matched fidelity. *The instrument set is legible.*
- **M6** — The Build documented. *The method is legible.*
- **M7** — Reference complete; full ship-gate green; committed. *The spec is mature; it's in git.*

Each milestone is a checkpoint: Claude presents, yzavyas comprehends/reviews/authorizes,
the comment loop closes the interpretant gap, then proceed.

## Risks & notes (scars from this build)

- **Spend limit** bit mid-workflow twice — batch agent work; bounded workflows; if a gate
  dies, drafts survive, resume from them.
- **`craft-rhetoric:ebert` is read-only** — capture its output, write the file yourself.
  Writers: feynman, sagan, orwell, vyasa.
- **gemini** headless needs `--approval-mode plan --skip-trust`; strip the "Ripgrep"
  line; it's the out-of-family evaluator (claude excluded as maker family).
- **Verify grammar against slick source, not memory** — biggest Dev-Guide risk. Shipped
  manifest = 5 fields; kind/apiVersion/protocol = doctrine.
- **Don't renumber `content/01-13` casually** — breaks `evaluation.json`'s map and
  anchored comments. If sections force renames, remap evaluation.json too.
- **Match fidelity to design-completeness** — the cardinal ci-scaffolds move here. A
  tarmac page for cortex (vapor) or a stub for aboot (design-complete) both fail.
- **The maturity-honest, spec-maturity axis is the point** — a polished guide to an
  unbuilt tool written as if it works is the one failure that discredits the site.

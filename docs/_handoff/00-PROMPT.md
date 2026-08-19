# Next-session prompt — the polished gnx doc site

Paste this as the opening instruction next session. It is self-contained; everything it
references is on disk.

---

## What gnx is — the product, and the positioning line (load-bearing)

**As a product/package, gnx is just a marketplace + registry** — of **cognitive
extensions**: reusable, composable components any agent can use, plus the **slick plugin**
that agents use to compose them. That is the entire public surface. A developer or an
agent meets gnx as "a registry of cognitive extensions for agents, with a way to compose
them." Nothing more.

**The ecosystem connection is internal detail.** That gnx is the registry the autopoietic
loop runs through, that it feeds geist.sh / samsara, the Bodhi-Dagstra-HADES loop, the
whole cosmology — that is the *why*, not the product. It lives **only** in the Design
section, never in the public registers.

**Honesty marker on the mission:** "for any agent" is the *structural* promise (vendor
neutrality by apiVersion namespace + adapter capability negotiation + the projection
model) — real by construction. **Claude Code is the only Target that ships today.** Public
framing is "designed for any agent; Claude Code today," not "works with any agent now."

The hard positioning rule for every register: **User Guide, Start, and Reference describe
the standalone product** (registry, marketplace, cognitive extensions, compose). **The
ecosystem / cosmology appears only in Design.** A public page that explains the gnx loop
or samsara has leaked internal detail into the product — a register violation (criterion
C and the public boundary, doc 12).

## Mission

Turn the gnx **genesis dossier** (a single internal design surface) into a **polished,
multi-audience documentation site** — User Guide, Dev Guide, Design (the current
dossier), and "The Build" (how this site and its tooling were made) — every page
commentable and annotatable, all on the existing SvelteKit substrate. Execute the plan in
`docs/_handoff/02-PLAN.md` against the rubric in `docs/RUBRIC.md` (extended per
`docs/_handoff/01-RUBRIC-EXTENSION.md`), with the design direction in
`docs/_handoff/03-DESIGN.md` (the current look is improvised; adopt the canonical mox
brand at `~/mox/brand/`). The doc set's maturity **is** the spec's
maturity; the site is the convergence surface for the shared understanding between
yzavyas and Claude (the comment loop is interpretant-exposure → convergence), a
coherence-forcing function across the registers, and the session-bootstrap substrate.

## Where everything is

- **App**: `~/mox/products/gnx/docs/experience/` (SvelteKit + bun + TS). Run:
  `cd docs/experience && bun dev --port 5183`. Typecheck: `bun run check`.
- **Content**: `docs/content/NN-name.md` (13 design docs + `_landing.md` / `_brief.md`
  cover), `docs/ground-truth.md` (factual authority), `docs/RUBRIC.md` (ship-gate
  instrument), `docs/evaluation.json` (scorecard data).
- **Infrastructure already built** (reuse, do not reinvent):
  - `src/lib/server/content.ts` — the loader. Sections are `process | concepts |
    appendix`; blocks parse from markdown; `` ```composer `` and `` ```decision `` fences
    become interactive islands. Block ids are content-hash anchors.
  - `src/lib/server/feedback.ts` + `routes/api/feedback/+server.ts` — append-only
    JSONL feedback ledger (`docs/feedback/<doc>.jsonl`), threads derived.
  - `src/lib/components/` — `Commentable.svelte` (select-to-comment + highlight paint),
    `Block.svelte`, `Composer.svelte` (live DAG compiler), `Decision.svelte` (explorable
    fork: alternatives × criteria × scenarios), `client/anchor.ts` (text-quote anchoring).
  - `routes/evaluation/+page.svelte` — the scorecard renderer.
- **Method memory** (read first): `~/.claude/projects/-Users-yza-vyas-mox-products-gnx/memory/`
  — especially `gnx-docsite.md`, `gemini-shipgate-pattern.md`,
  `protocol-diversity-and-layout.md`, `corpus-recency-weighting.md`,
  `discourse-before-decisions.md`.

## The target structure (Phase 0 / vyasa refines this; it is a strong default, not law)

| Nav section | Audience | Register | Source |
|---|---|---|---|
| **Start here** | everyone | orientation | new landing — multi-audience entry |
| **User Guide** | users + agents using gnx | pragmatic, task-oriented | new (spec-honest — gnx isn't built) |
| **Dev Guide** | component authors | how-to + reference | new (must match slick source exactly) |
| **Design** | principal, maintainers | internal design (the dossier) | existing `content/01-13` |
| **The Build** | curious / the method | meta / honest making-of | existing Process (rubric+eval) + new |
| **Reference** | all | reference | ground truth, grammar, api schemas |

The current `concepts` group = **Design**. The current `process` group (rubric +
evaluation) folds into **The Build**. Ground truth → **Reference**.

## The non-negotiable: honesty about maturity

gnx is **not built** — it is designed. The User Guide and Dev Guide are therefore
partly *specification*, not documentation of a working tool. **Every capability claim
must carry a maturity marker — shipped / planned / proposed — and nothing aspirational
may be written as if it works today.** A user guide that pretends `gnx init` runs is a
lie. The dossier's established/decided/open discipline extends to the guides as
shipped/planned/proposed. This is criterion H in the rubric extension and a hard gate.

## Voice & discipline

- Voice anchors: `~/mox/research/drafts/aces/voice.md` + `~/mox/packages/vaani/.rhet/voice.md`.
  Transferable yzavyas register: direct technical precision, short declaratives,
  architectural "X does this; you do that," no marketing words. **Em-dashes are KEPT**
  (the aces book-register uses them). Do NOT import vaani-product-specifics.
- **Criterion G (legibility) bites hardest in the User Guide**: no internal type/tool
  names (Frame, Construct, TypedStruct, samsara, Dagstra) in user-facing prose — name
  the value, not the mechanism. The Dev Guide may use them (the mechanism IS the subject).
- Recency-weight the corpus (June > May > April). `ground-truth.md` is the factual
  authority; do not introduce claims beyond it without grounding.
- Public/internal boundary (doc 12 / §11): the User Guide stays bounded to registry +
  marketplace pragmatics — no samsara/Treya/cosmology. Cosmology lives in Design, behind
  links.

## Pipeline (per the ship-gate method)

vyasa (collection architecture) → feynman/sagan (draft, register-aware) → orwell (voice)
→ tufte (diagrams where they carry more than prose) → **gemini out-of-family eval**
against the register-aware rubric → claude optimize → gemini re-eval. ebert is read-only
(capture its output and write it yourself). gemini headless:
`gemini -p "…" --approval-mode plan --skip-trust` (strip the "Ripgrep" warning line).

## Hard don'ts

- Don't claim built what is designed. Mark maturity on every claim.
- Don't break the public/internal boundary (no cosmology in the User Guide).
- Don't invent product features that aren't in `ground-truth.md`.
- Don't let the interactive islands or the feedback loop regress — verify them in-browser.
- Don't commit until the git workflow is set up (Phase 5) — and that was the principal's
  original first request, still owed.
- Owed from this session: docs `10` (projection) and `13` (composing) had layout +
  protocol sections added directly, not gate-verified — re-gate them in Phase 3.

## Definition of done

Every nav section exists, is written register-correctly, carries honest maturity markers,
passes its rubric profile via the gemini gate, renders with working comments/annotation,
and the evaluation scorecard reflects the new state. Then (Phase 5) the git workflow is
wired and the whole thing is committed on `feat/docsite`.

# Bootstrap prompt — paste this to start the session

---

You are picking up the gnx project (`~/mox/products/gnx`). The work is doc-driven design:
we are building a polished, multi-audience documentation site whose maturity **is** the
spec's maturity. Do not start building until you've oriented.

**gnx, as a product, is just a marketplace + registry of cognitive extensions** any agent
can use, plus the slick plugin to compose them. The ecosystem connection (the loop,
geist.sh, samsara) is *internal detail* — design rationale, never the public product.
"Any agent" is the structural promise; Claude Code is the only Target shipping today.

**Acquire context in this order, then stop and tell me your plan before acting:**
1. `~/.claude/CLAUDE.md` — the constitution.
2. The **ci-scaffolds** skills — `~/.claude/plugins/cache/cix/ci-scaffolds/0.6.0/skills/`
   (`crafting`, `collaborating`, `problem-solving`). Internalize them; the fidelity ladder
   (dirt→cobble→tarmac) is the milestone framework, the collaboration model is how we work.
3. Your project memory (`MEMORY.md` and the files it indexes).
4. The handoff kit, in order: `docs/_handoff/00-PROMPT.md` (full spec) →
   `01-RUBRIC-EXTENSION.md` → `02-PLAN.md` (the 6 phases / 7 milestones) →
   `03-DESIGN.md`.
5. `docs/ground-truth.md` (factual authority) and skim `docs/content/01-13`.
6. Run the dev server: `cd docs/experience && bun dev --port 5183`; `bun run check`.

**Then execute `02-PLAN.md` starting at Phase 0.** Checkpoint with me at every milestone
(M1–M7): present, let me comprehend and authorize, let the comment loop close the gap,
then proceed. AI generates → human comprehends — don't run ahead of my understanding.

**Non-negotiables (do not lose these):**
- **Honesty = maturity.** The doc set tracks the spec: mark every claim
  established/decided/open and shipped/planned/proposed. A polished guide to a tool that
  doesn't exist, written as if it does, is the one failure that discredits the site.
- **Positioning boundary.** Public registers (User/Start/Reference) = the standalone
  product. Cosmology lives only in Design. A public page explaining the gnx loop has
  leaked internal detail.
- **Design = inherit cix.** Lift cix's brand layer + sigil from
  `~/mox/products/cix/docs/experience/` (`src/lib/brand/{tokens.css,typography.css}`,
  `static/sigil*.svg`, the Sigil components). Do not improvise a palette. The current
  `app.css` (serif + terracotta) is off-brand and gets replaced.
- **Owed from last session:** re-gate docs `10` (projection) and `13` (composing) — their
  layout + protocol sections went in directly, ungated.
- **Don't commit until Phase 6** (the git workflow yzavyas asked for first is still owed),
  and don't push or open a PR unless asked.

Falsify before you advocate; show tradeoffs, not mandates; the call is mine.

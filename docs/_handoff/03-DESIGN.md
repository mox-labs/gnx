# Design & aesthetic — the honest state, and the canonical direction

## The honest state: the current docsite is improvised and off-brand

The look I built this session — **Charter serif body, a single terracotta accent
(#e05d44), ad-hoc grays** — was improvised without consulting the mox brand system. It
reads fine, but it contradicts the canonical system on **every axis**. It is a working
internal skin, not the design direction. Do not treat it as decided.

## gnx inherits cix's brand layer and sigil — lift it, don't derive

gnx is the cix successor (same body of work, sharpened). **gnx uses the cix sigil and
cix's docsite brand layer directly** — no new sigil to derive, no theming from scratch.
The canonical *applied* source is `~/mox/products/cix/docs/experience/`:

- `src/lib/brand/tokens.css` — the design tokens, already the trichrome in OKLCH
  (`--ci-blue` Spark, `--ci-red` Constraint, `--ci-green` Emergence) + dao zero-chroma
  neutrals + spark/emergence layers. **The mox brand tokens were derived from this file.**
- `src/lib/brand/typography.css` — the type system.
- `static/sigil-canonical.svg`, `static/sigil.svg` — the **shared sigil** (gnx = cix sigil).
- `src/lib/components/landing/Sigil.svelte`, `glyph/CanonicalSigil.svelte`,
  `landing/BrandHero.svelte` — the sigil/brand components to bring over.

For the *why* behind the tokens, the system docs at `~/mox/brand/` are the reference
(`system.md`, `sigil.md`, `decisions.md`, `style-guide.html`) and `~/mox/studio.md` is
the aesthetic-stance companion — but the **action is to lift cix's brand layer**, which is
the canonical system already applied. The current `app.css` (serif + terracotta) is the
thing being replaced.

### What's canonical (adopt, don't reinvent)

- **Trichrome, used by ROLE not by color** (OKLCH): **Spark** (blue, `~#4da6ff`) =
  thesis / human signal — interaction, links, emphasis. **Constraint** (red, `~#d45555`)
  = antithesis / machine signal — boundaries, warnings, errors, RETURN. **Emergence**
  (green, `~#00FF41`) = synthesis — code, success, SHIP, evidence-strong. My single
  terracotta accent is wrong: links should be Spark, not one universal accent.
- **dao neutrals** = zero-chroma, "so the trichrome can sing." **depth** = never pure
  black; always a 240° trace. (My grays carry an arbitrary warm tint — replace.)
- **The 9-grid** (9, 18, 27, 36, 54, 72, 90, 108) for spacing. Don't reach past it.
- **Mono-forward type.** Every mox surface is monospace-led; "the shared mono is the
  bridge." gnx's assigned register is **#4 blueprints · billboard ("gnx default")** —
  **DM Mono** body, **JetBrains Mono** code, voice "industrial, loud, earned." *Decision
  to make:* a docs surface may instead want **#1 cix · operator** — **IBM Plex Mono**
  body, "this is a tool, not a magazine; monospace body signals tool-ness." Either way,
  the **serif body I used is off the system** — gnx is mono-led.
- **Drop-in tokens**: import `~/mox/brand/tokens/mox.tokens.css` and theme against the
  variables instead of hand-rolling `app.css` colors.

### The standout fit — evidence-brightness ↔ the dossier's markers

The brand has an **evidence** color scale where *brightness encodes confidence*
(strong / moderate / weak / speculative), explicitly "for research and documentation
surfaces." This maps almost perfectly onto what this dossier needs: the
established / decided / open registers and the shipped / planned / proposed maturity
markers. **Use evidence-brightness for the maturity badges and the established/open
distinction** — it's the brand system already solving our exact problem. (Established ≈
strong, decided ≈ moderate, proposed/open ≈ speculative.)

### Open design tasks (genuinely undecided)

1. **Sigil is settled — gnx uses the cix sigil** (lift `static/sigil*.svg` +
   `Sigil.svelte` / `CanonicalSigil.svelte`). No derivation. (Earlier draft of this doc
   wrongly said gnx needed its own — corrected: gnx = cix sigil, the lineage.)
2. **gnx's docs register**: cix's docsite uses the cix·operator voice (IBM Plex
   Mono, tool-ness) — fits the documentation surface, so lifting cix's `typography.css`
   gives the right register by default. Open only at the edge: the *public marketplace
   landing* might want the louder blueprints·billboard voice (DM Mono). Possibly: docs in
   operator, public landing in billboard.
3. **Reconcile with `brand/decisions.md`** — eight open brand questions the polish pass
   may want to resolve; read them, but cix's applied layer already encodes the answers in
   practice.

## What to keep from the current build

The *structure* is sound — keep it, reskin it: the reading-surface idea (generous
measure, clear hierarchy), the section nav, the cover composition, the interactive
primitives (composer, decision explorer, scorecard), the tufte diagram approach
(mid-gray + one accent → becomes trichrome-by-role), the annotation/highlight UX. Only the
*tokens* (type, color, spacing) are off — swap them for the canonical system.

## Action (folds into the plan, Phase 2)

1. **Lift cix's brand layer** into the gnx docsite: copy `tokens.css` + `typography.css`
   from `~/mox/products/cix/docs/experience/src/lib/brand/`, the sigil SVGs from its
   `static/`, and the `Sigil` / `CanonicalSigil` / `BrandHero` components. Replace the
   improvised `app.css` palette/type with these tokens.
2. Recolor the docsite's own chrome **by role**: links/emphasis → Spark (`--ci-blue`);
   RETURN/boundaries/errors → Constraint (`--ci-red`); SHIP/code/evidence-strong →
   Emergence (`--ci-green`). Retheme the tufte diagrams to trichrome-by-role.
3. Wire the maturity badges + established/open markers to the **evidence-brightness**
   scale (if cix's tokens don't already carry the evidence colors, bring them from
   `~/mox/brand/tokens/`).
4. Place the cix sigil as the cover mark + favicon (it's gnx's mark now).
5. Sanity-check against the cix docsite rendered, and `~/mox/brand/examples/gnx-dark.png`.

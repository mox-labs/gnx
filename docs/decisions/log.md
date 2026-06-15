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

## Open two-way doors (autonomous defaults; flag if the list grows)

- Public-landing voice register — cix·operator (IBM Plex Mono, tool-ness) vs blueprints·
  billboard (DM Mono, loud). Default: operator for docs. *Flip:* if the public marketplace
  landing wants the louder voice.
- On-disk layout for new public docs — `content/<section>/<slug>.md` subdirs (chosen) vs
  flat numbered. Reversible.
- Ground-truth placement — currently anchors Design (internal). May split a public
  Reference from it once that section is written.

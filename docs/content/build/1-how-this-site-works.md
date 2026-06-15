# How this site works

> The docsite is the design surface for gnx, built *before* the tool. Doc-driven design: the
> doc set's maturity is the spec's maturity. Writing the user, dev, and design views forces the
> design to reconcile — a discrepancy between two pages is a design gap, not a wording problem.

---

## Markdown in, anchored blocks out

Content is plain markdown in `docs/content/`. A SvelteKit + bun app (`docs/experience/`) reads it: `content.ts` parses each file's frontmatter and splits the body into blocks. Two register groups fall out of the section key — public (Start / Guides / Reference / Explanation) leads; internal (Design / The Build) trails.

Each block gets a **content-hash anchor** — an id derived from the block's text, stable across reordering and changing only when the block's content changes. That property is load-bearing for the feedback loop: a comment points at a hash, so a comment that survives an edit means the block didn't change, and a comment that detaches means it did. The detachment is signal, not breakage.

---

## Frontmatter carries the register and the maturity

Each page declares `section`, `register`, `mode`, `status`, and `fidelity`. `status` (shipped / planned / proposed) renders as the badge in the nav — the honesty gate made visible. `register` decides which side of the public/internal line a page sits on. `mode` is its Diátaxis need-type. The frontmatter is how the gate's profile is selected: a page tells the evaluator which bar to apply.

---

## Two islands carry what prose can't

Most blocks are rendered markdown. Two fenced blocks become interactive components instead:

- ` ```composer ` mounts the composition model — components with declared ports, a DAG compiled from the declarations alone, parallel batches and compile-time rejection. "Compose without running" made tactile. It's used in [Compose components](/docs/compose-components).
- ` ```decision ` mounts a decision explorer from a JSON payload — alternatives scored across criteria, used in the Design docs to make an open fork manipulable rather than described.

The islands are the show-don't-tell surface: a diagram a reader can drive beats a paragraph about composition.

---

## The scorecard is generated, not asserted

`docs/evaluation.json` holds the ship-gate's verdicts and per-criterion scores; the `/evaluation` route renders it. The scores are the out-of-family evaluator's, not the author's — see [The ship-gate](/docs/the-ship-gate). When the rubric grows a criterion, a new column appears and older rows show `·` for the criteria that postdate them.

---

## See also

- **[The ship-gate](/docs/the-ship-gate)** — how each page earns its verdict.
- **[The feedback loop](/docs/the-feedback-loop)** — how a comment becomes a revision.

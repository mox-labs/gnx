# The ship-gate

> Every page on this site was graded by a model from a different family than the one that wrote
> it. That is the whole mechanism: the maker does not grade its own work. What follows is the
> instrument, the diagonal, and the loop — described as it actually runs, including where it
> misfires.

---

## The instrument: a rubric with hard gates

`docs/RUBRIC.md` is the standard, and it is reviewable like any other page. It defines criteria — voice, no-LLM-tells, no-register-announcing, factual fidelity, propagation, tightness, legibility, and the register extension's maturity / task-orientation / grammar-accuracy — with some marked **hard gates**. A register profile says which criteria gate which kind of page: a public guide is gated on task orientation, an internal design doc on factual fidelity and depth.

The verdict grammar is three-valued. **SHIP** — all applicable hard gates clear, nothing below a 3. **TIGHTEN** — gates clear, one non-gate sits at 3; fix the named passage and it ships. **RETURN** — a hard gate fails, or any criterion hits 1 or 2; the page goes back with the failing passage named.

---

## The diagonal: out-of-family by construction

Claude writes the content, so Claude cannot be the judge — it would be grading its own family. The evaluator is **gemini**, a different model family. This is one of three accountability axes the dao doctrine names: vertical (the human), horizontal (same-family review), and **diagonal** (an out-of-family evaluator). The diagonal is the non-negotiable one — it catches the failure modes a same-family reviewer shares the blind spot for.

The evaluator is told the page's register so it loads the right profile, and is run read-only:

```
gemini -p "<rubric + register profile + the page>" --approval-mode plan --skip-trust
```

---

## The loop: evaluate, optimize, re-gate

The maker optimizes; the out-of-family model judges; repeat until clean. The optimizer owns the final text — it applies the genuine fixes the gate surfaces and rejects edits that would drift a fact or flatten the voice. The scorecard at [/evaluation](/evaluation) shows the result, in the evaluator's scores, not the author's.

A same-family panel runs first, before the diagonal: several reviewers, each on a different lens — factual trace, maturity honesty, register, LLM-speak — surface the cheap-to-catch issues in parallel so the slower out-of-family pass spends its budget on what's left. Horizontal first, diagonal last.

---

## Where it misfires, honestly

The out-of-family gate is not deterministic. The same unchanged page has flipped between SHIP and RETURN across runs, and re-flagged a heading it scored clean a round earlier. It also over-applies a gate now and then — reading a public-product roadmap reference as a register violation, or a real package name as a typo.

The discipline that keeps this from becoming thrash: take the genuine catches (the gate has found real ones — a shipped-vs-planned inversion, a malformed example, a contamination leak), tighten the evaluator's prompt to remove the misfire, and once the hard gates have cleared on a defensible pass, ship — rather than chase a nondeterministic judge in circles. A verdict you can defend against the evidence beats a green cell bought by capitulation. The scorecard says as much: a verdict you disagree with means the rubric needs adjusting, not the doc.

---

## See also

- **[Read the rubric](/docs/rubric)** — the instrument itself.
- **[The collaboration method](/docs/the-collaboration-method)** — the discipline the gate sits inside.

---
name: dao
description: >-
  This skill should be used when the user asks to "set up a dao", "stand up an agent organization",
  "scaffold this project", "set up the harness for this project", "give this project a bench",
  "audit our dao", "check our agent setup", "what's missing from our organization", or brings a
  project that needs an agentic organization built for it. Conducts the four-phase setup — Discourse,
  Settlement, Projection, Audit — that turns a project the user describes into a working
  organization under `.claude/`: a bench, a practice library, a routing surface, a verification
  gate, and a declared human-accountability surface. Holds the eleven-component spec, which seats
  to convene, and what each component must contain to count as present rather than merely emitted.
  NOT for running work inside an already-standing dao — only for standing one up or auditing one.
---

# The dao — standing up a Directed Agentic Organization

A **dao** is a Directed Agentic Organization that maintains a project: a collective that does the
work, a charter it declares, habits it keeps, a pragmatics for getting work done, governance that
keeps its cognition effective, human direction, and a boundary. The human is the non-delegable direction inlet and irreducible arbiter (component #10) —
autonomy over method, never over accountability.

You are the conductor. The user brings a project; you discuss it, settle what it needs, project the
organization, and audit what you emitted. **The eleven components are the template; the domain is
the fill.**

> Settled by a six-mission research programme (2026-06-20). Several missions **narrowed or
> reversed** a component's original justification — the constraints in
> `references/findings.md` are not commentary, and a projection that ignores them rebuilds
> what the research already refuted. Read that file before authoring a bench or a gate.

## The floor, and the delta

Not all eleven are equally required:

- **A council** — deliberates. Needs **#1** bench · **#5** practice library · **#6** routing ·
  **#7** verification gate · **#10** human-accountability.
- **An organization** — runs a project. Adds **#2** charter · **#3** ceremonies ·
  **#4** process-encoding · **#8** ratchet · **#9** membership.
- **#11** the emitted record, plus an inter-dao protocol *if* it coordinates with peers —
  conditional; the domain fills it.

**Do not front-load the delta.** M3 found the council→organization transition is *crisis-driven*
(Greiner) on a foundation-before-superstructure base (CMMI). An organization component is what a
specific pain buys, not a day-one deliverable. An incomplete organization delta is a legitimate
state; an incomplete council floor is not.

## The four phases

| # | Phase | Convene | Emits | Skip when |
|---|---|---|---|---|
| 1 | **Discourse** — find the real project | `bodhi` | a hardened project-spec with named uncertainties | never — an unhardened intent produces a bench that fits nothing |
| 2 | **Settlement** — decide what this dao needs | conductor + the human | the seat list, the floor, the gate posture | never — this is where the human's direction enters |
| 3 | **Projection** — build the organization | `hades` | `.claude/` + `CLAUDE.md` | never |
| 4 | **Audit** — check what you emitted | `dao check` | a PRESENT/PARTIAL/ABSENT report | never — you cannot grade your own emission by looking at it |

### Phase 1 — Discourse

Convene **`bodhi`**. The user brings intent, not a spec. Bodhi runs the three-tier hardening
gradient (structural → behavioral → semantic) against **external anchors** and returns a calibrated
proxy that names what it leaves open. No tier is hardened by introspection alone — Huang et al. 2024
(ICLR) established that self-correction without external feedback does not improve reasoning.

What you need out of this phase: what the project *is*, what work it repeats, where it has been
hurt before, and who is accountable. The last one is not optional — a dao with no named human is
not directed.

### Phase 2 — Settlement

This is a conversation with the human, not a computation. Settle four things:

1. **The seats.** What methods does this project's work actually require? One seat per method. Run
   `dao figures <project>` to see what the corpus already grounds — the `informs` edges in
   `dao-corpus/corpora/agent-craft` record which domains feed which project's units. Prefer a
   grounded seat over an invented one, and **say which are ungrounded** rather than implying the
   corpus validated them.
2. **The floor.** Council-only, or organization too? Ask what has already gone wrong. If nothing
   has, the delta is premature.
3. **The gate posture.** Out-of-family, or the honest cold-reread floor? See below — this is the
   decision most often gotten wrong.
4. **What this dao does not own.** The perimeter is easier to write now than after drift.

### Phase 3 — Projection

Convene **`hades`** — it builds behind declared boundaries, to the ACE triad, and it will not
resolve an ambiguity silently in code. Emit:

```
CLAUDE.md              # #2 charter · #3 ceremonies · #4 flow · #6 routing · #9 perimeter · #10 direction
.claude/agents/        # #1 the bench — one file per seat
.claude/skills/        # #5 the practice library
.claude/ratchet.md     # #8 the learning loop
```

**Write the method, not the label.** This is the single most consequential instruction in the
skill, and it is measured: a seat's coordination value comes from **rich, concrete, embodied method
prose**. A bare one-word label is *worse* than either a well-described persona or a well-described
role — see `references/findings.md`, M1.

Each seat states the method it owns **and what it refuses**. Two seats must not own the same method.

### Phase 4 — Audit

Run `dao check <project>`. It reports PRESENT / PARTIAL / ABSENT per component — never a score,
because a single number lets a project average away a missing gate. **PARTIAL is the useful
verdict:** it means the artifact exists and the substantive test does not pass, which is the failure
this whole skill exists to prevent. `.claude/agents/` with one thin prompt in it is a directory, not
a bench.

Then fix and re-run. `dao check` exits non-zero while the council floor is incomplete.

## The gate — get this one right

Component **#7** is the component whose justification the research narrowed most. The broad claim —
*"the maker cannot grade its own work; fresh eyes catch what the maker misses"* — is **NOT
SUPPORTED**. Measured: a two-pass fresh-eyes self-review matched a structurally separate agent on
every mode tested (1.00 flaw-catch both, 0.00 over-concession both, six subtler planted flaws).

So:

- **The cheap floor** is a *mandated cold re-read pass*. It is empirically sufficient for everything
  measured. Adopt it always.
- **A separate gate earns its extra cost on two grounds only:** an **out-of-family** anchor (a
  different model distribution catching what the maker's structurally cannot) and **sustained
  multi-turn pressure** on a position the maker generated through long reasoning. Both untested;
  run-03 is specified but unrun.
- **A same-family "independent reviewer" is the refuted claim wearing a new hat.** If the gate is
  same-family, label it a cold-reread floor. `dao check` flags the dishonest version.

Out-of-family today means a non-Claude family — `mlx-lm` locally is the live option. The free-tier
`gemini` CLI is deprecated as of 2026-06; do not rely on it.

## The one law for ceremonies

**A ritual helps only if it injects signal the agent could not fabricate.** A ceremony that asks
the model to introspect on its own output adds cost and no signal. Every ceremony you declare must
name the signal it injects and where that signal comes from *outside* the agent performing it.

## Stay thin

Convene by method, not by default. One seat by default; a council only for genuinely multi-track
work, **never a standing debate** — that is the procrastination-engine failure. A dao that convenes
everyone for everything is slower than no dao at all.

## References

- **`references/eleven-components.md`** — each component: what it is, where it lands, and the
  substantive test that distinguishes present from emitted.
- **`references/findings.md`** — the six missions and what each one settled, narrowed, or
  reversed. Read before authoring a bench (M1) or a gate (M1-H2, M4).

## The backing capability

The deterministic half is the `dao` CLI:

| Command | Does |
|---|---|
| `dao spec --findings` | the eleven components with their constraining findings |
| `dao figures <project>` | grounded domains available for a project, with honesty about which are RAW_GATHERED |
| `dao check <project>` | audit; exits non-zero while the council floor is incomplete |
| `dao policy` | the audit's thresholds as declared policy |

There is deliberately **no `dao init`** command. Scaffolding eleven plausible files is exactly the
artifact-shaped hole the audit exists to catch, and the method prose is the load-bearing part.
The judgment is this skill's; the checking is the code's.

---

*Discourse the intent · settle what it needs with the human · project behind boundaries · audit
what you emitted. The components are the template; the domain is the fill; the human holds the
direction.*

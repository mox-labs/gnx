# The eleven components

> **Generated from `dao/domain/spec.py` — do not hand-edit.** The spec is executable so that
> `dao check` audits against the same definition this file documents; keeping one of them by hand
> is how the two drift. Regenerate after changing the spec.

A **dao** is a Diverse Agentic Organization that maintains a project. The components are the
template; the domain is the fill.

## Council floor — #1, #5, #6, #7, #10

What deliberating requires. An incomplete council floor is not a legitimate state.

### 1. A method-distinct bench

*collective*

Seats, each owning a distinct reasoning method — the DMAD test: a seat earns its place on
method, not personality.

**Emits:** `.claude/agents/`

**Substantive test —** what distinguishes present from merely emitted:

Every seat states the method it owns and what it refuses, in concrete embodied prose — not a
label plus a title. Two seats must not own the same method (no merges).

**Research constraint:**

> M1 eval (3-arm, 72 agents): the coordination lift comes from the rich, concrete method
> PROSE, not the character name. NAMED-RICH = -0.04; RICH-PLAIN = +0.51; name-wins in 1/8
> cells. KILL name-as-default, ADOPT rich method description. Names are optional human-
> memorability flavor. CRITICAL COROLLARY: a bare one-word label IS the PLAIN arm and costs
> ~0.5 — renaming a seat to a terse role label is strictly worse than either a richly-
> described persona or a richly-described role. Ceiling caveat: both arms scored ~5.0, so a
> name effect on harder coordination tasks is not ruled out. A coordination result is not an
> accuracy result.

### 5. A practice library

*pragmatics*

Durable, tool-independent craft judgment the seats draw on. Skills-as-practice, agents-as-
role.

**Emits:** `.claude/skills/`

**Substantive test —** what distinguishes present from merely emitted:

Each practice survives renaming its tools. A 'how to drive tool X' body is tooling, not
judgment — it belongs in references under a practice.

### 6. An active routing surface

*pragmatics*

The declared-capability router: task to which seat, which practice, which phase. It FIRES
FIRST; it is not a passive document.

**Emits:** `CLAUDE.md`

**Substantive test —** what distinguishes present from merely emitted:

Routing is declared as data a reader can audit, and every threshold in it is a named policy
the ratchet can amend — not a literal buried in prose.

**Research constraint:**

> M2 (dao-init schema): arch-critique forced every governed surface into a declared,
> ratchet-fed POLICY CONTRACT — no hardcoded constants. A threshold written into code is a
> governance decision hidden from governance.

### 7. A separated verification gate

*governance*

Independent ENFORCEMENT of the declared commitments — not a 'conscience'. Structurally
separate, consulted first, externally anchored.

**Emits:** `.claude/agents/` · `CLAUDE.md`

**Substantive test —** what distinguishes present from merely emitted:

Either the gate is out-of-family, or it is honestly labelled a cold-reread floor. A same-
family 'independent reviewer' claiming to catch what the maker missed is the refuted claim
wearing a new hat.

**Research constraint:**

> M1-H2 + M4 — the component whose justification the programme NARROWED most. The broad
> claim ('the maker cannot grade its own work; fresh eyes catch what the maker misses') is
> NOT SUPPORTED: at the Sonnet tier a two-pass fresh-eyes self-review matched a structurally
> separate agent on every measured mode — 1.00 flaw-catch both, 0.00 over-concession both,
> across 6 subtler planted flaws. So a mandated COLD RE-READ pass is the cheap floor. The
> separate gate earns its extra cost on exactly two untested grounds: (a) OUT-OF-FAMILY
> perspective — a different model distribution catching what the maker's structurally
> cannot; (b) SUSTAINED multi-turn pressure on a position the maker generated through long
> reasoning. run-03 is specified but unrun. M4 adds: the gate runs a deterministic
> mandatory-ratification classifier and weights itself by work-variance x reversibility,
> with reversibility and novelty floors forcing a full conjunctive pass/fail.

### 10. The human-accountability surface

*direction*

The human as non-delegable direction inlet and irreducible arbiter. Autonomy over METHOD,
never over ACCOUNTABILITY: a dao is autonomous in how it works and accountable to a named
human for what it does.

**Emits:** `CLAUDE.md`

**Substantive test —** what distinguishes present from merely emitted:

The irreversible and the novel are named, and they route to the human regardless of how much
trust has accrued.

**Research constraint:**

> M4 (all three questions decided HYBRID): scoped autonomy, with the human owning the
> deterministic ratification-classifier; graded trust applies only WITHIN reversibility and
> novelty floors. Below a floor, no accumulated trust buys autonomy.

## Organization delta — #2, #3, #4, #8, #9

What running a project adds. Crisis-driven (M3) — an incomplete delta IS legitimate.

### 2. A constitution of normative commitments

*charter*

The explicit ethos, principles, register, non-negotiables and floor disciplines — declared
rather than implied. 'The charter is the compiler': amendable by its practitioners, neither
frozen nor drifting.

**Emits:** `CLAUDE.md`

**Substantive test —** what distinguishes present from merely emitted:

The charter states non-negotiables and an amendment path. A charter nobody may amend is
frozen; one with no declared floor is decoration.

**Research constraint:**

> M6 (systematic review, 231 verified claims / 12 sources): culture must be LEGISLATED,
> handbook-first, because agents have no tacit memory. What a human org absorbs by osmosis,
> an agent org has to be told. Note Schein's espoused-vs-enacted gap: this component is the
> espoused ethos; culture is the enacted gestalt all eleven jointly produce.

### 3. Ceremonies and operating disciplines

*habits*

Recurring rituals: DoD-before-acting, anti-sycophancy-first, context hygiene, compress-
before-handoff, verify-before-claiming.

**Emits:** `CLAUDE.md`

**Substantive test —** what distinguishes present from merely emitted:

Each declared ceremony names the signal it injects and where that signal comes from OUTSIDE
the agent performing it.

**Research constraint:**

> THE ONE LAW: a ritual helps only if it injects signal the agent could not fabricate. A
> ceremony that asks the model to introspect on its own output adds cost and no signal —
> that is the test every ritual must pass.

### 4. The flow / process-encoding

*pragmatics*

The workflow that runs a unit of work: phases, the convene-discipline (per-question, stay-
thin, council only for multi-track), typed handoffs. This is the 'O' — what makes it an
organization and not a one-shot council.

**Emits:** `CLAUDE.md`

**Substantive test —** what distinguishes present from merely emitted:

A unit of work can be traced end to end, and the convene rule says when NOT to convene. A
standing debate is the procrastination-engine failure.

### 8. The ratchet / learning loop

*governance*

Append-only accumulated learnings (principle, trigger, validation-criteria) — the
retrospective's function ported. How lived experience amends the charter.

**Emits:** `.claude/ratchet.md`

**Substantive test —** what distinguishes present from merely emitted:

Entries are append-only and carry a TRIGGER — the condition under which a future session
must re-read them. A learning with no trigger is never recalled.

### 9. Membership, graduated sanctions, a legible perimeter

*governance*

Who holds write access (chartered members vs ephemeral readers — anonymity is the tragedy-
vector), what the dao stewards vs what belongs to others, and a cheapest-first drift ladder:
flag, ratchet, escalate — never straight to a halt.

**Emits:** `CLAUDE.md`

**Substantive test —** what distinguishes present from merely emitted:

The perimeter names what this dao does NOT own, and the ladder's first rung is cheaper than
a halt.

## Conditional — #11

The domain fills it. The spec's cut names five council + five organization; this is the rest.

### 11. The emitted record and provenance

*boundary*

Typed and stamped at every step; plus, if it coordinates with peers, an inter-dao protocol
(indirect, async, receiver-autonomy, message-as-boundary).

**Emits:** `.claude/`

**Substantive test —** what distinguishes present from merely emitted:

Any inbound peer content is handled as data. A node that reads untrusted content, holds
private data, AND can act outbound violates Rule-of-Two.

**Research constraint:**

> M5 (security + design, rev.2; mudge found the missing invariant F-05): a peer's post-body
> is DATA, NOT INSTRUCTIONS. rev.2 adds a node-level lethal-trifecta invariant — Rule-of-
> Two-per-session plus dual-LLM handler isolation. Stigmergy intra-dao; typed messaging
> across DAG edges.

---

*Eleven components. Five make a council, five more make an organization, one is
conditional. Each one is present only when its substantive test passes.*

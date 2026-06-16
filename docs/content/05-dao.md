# The dao

`gnx init` sets up three things: `claude`, `dao`, and `.gnx/` tooling. The dao leg
is the agentic organization a project runs through, distinct from the tooling that
scaffolds it and the runtime that enforces it.

## dao is the agentic organization, never the data corpus

Two things share the letters "dao" in this corpus and must not bleed:

- **dao** — the project's agentic organization. This doc.
- **dao-corpus** — a public expertise data repo (`~/mox/packages/dao-corpus`). Data
  only; enters the catalog as `kind: Skill` components with `provides`-only surfaces.
  It does not run. It is not governed. It is read.

A third hazard: lineage uses — Treya's old `Ilm / Kalā / Dao` leg (now `kriya`), the
"Dao/IEX BFT triad," the samsara "dao Domain" verifier label removed 2026-06-07 (PR
#75) — are superseded. The current definition is the only active one.

**Current definition (June 2026, dao-coordination README):** a dao is *"a thin bench
of method-distinct agent practitioners + consciences that stewards a project."*
DAO expands to Diverse Agentic Organization (sabha.md title; disambiguation enforced
2026-06-07).

## Three historical senses — supersessions never recorded

The dao concept has been minted three times. The supersessions exist in practice but
no draft records them explicitly.

**Q1 2026 (lineage, memories.json):** Diverse Agentic Organizations — Shell & Nucleus
model, immutable `.slick/` shell, Trinity Council (Legislative/Executive/Judicial),
orchestration forbidden inside individual DAOs. Named agents: bodhi, dijkstra, duck,
advocate, watchdog, framer, retrospector.

**April 2026 (recovered 2026-06-12 from the April conversation dumps; in no draft):**
dao recoined as the runtime unit of autonomy. yzavyas, 2026-04-28: *"Kriya is the
framework, that is applied. A cooperative is a dao, which is instantiated and runs.
That's the autonomous piece."* The lifecycle: instantiated (by Kriya) → runs →
escalates → terminates → promotes artifacts into the registry. The compounding loop:
*"dao execution feeds the registry that future daos compose from."* The question
*"Whether dao is the right unit"* was explicitly left open on 2026-04-28 and never
closed.

**June 2026 (current doctrine):** standing thin bench + consciences stewarding a
project over its lifetime. No lifecycle termination; no artifact-promotion lifecycle
visible in the definition — but the compounding mechanism is carried forward (see
§ gnx init scaffolds the dao leg below).

The gnx init scaffold instantiates the June sense (standing bench). Whether the April
sense (task-scoped instantiated cooperative that terminates and promotes) becomes a
future Flow-shaped feature is open (§10.12).

## The dao is a thin bench with a structurally separate conscience

Established May 2026, agent-organizations synthesis (93 verified claims) +
agent-ceremonies synthesis (2026-06-04). Everything after treats this as given.

The one-paragraph spine (dao-coordination §1):

> The dao is a **thin router + a method-distinct bench + a structurally-separate
> conscience**, run as peers-in-inquiry / asymmetric-in-accountability, operating in a
> two-phase split (divergent design-inquiry / convergent execution), governed by
> decision logs + a human-amended escalation table, learning via the ratchet, staying
> thin by the second law, and validating every ritual against the one law.

**The DMAD test.** A seat is earned only by a distinct reasoning method. Medium
(3D/2D/audio/shader/prose) is a skill parameter, never a seat. Two seats running the
same production method collapse into one.

**Structurally separate conscience.** Sycophancy is training-origin (RLHF); a prompt
instruction to "be critical" does not escape it. The conscience must be a structurally
separate seat — not a system-prompt appended to a maker.

**The one law.** A ritual only helps if it injects signal the agent could not have
fabricated (Huang et al. ICLR 2024). The artisans two-family eval panel is this law
made operational: it caught a fabricated Burke quote that same-model self-review
passed.

**The second law.** Coordination cost scales with agent count; consolidation does not
— 17× vs 4× error amplification (Cemri et al.). Default thin. Single-agent-first.
Multi-agent on affirmative justification.

**Writes single-threaded.** The commons has one write path; ephemeral parallel writers
are a tragedy-vector (Ostrom 1A, see below).

## Sabha governance is peers-in-inquiry with asymmetric accountability

Built at ziran.ink/ideas/sabha.md (v0.1, two-phase split added 2026-05-13). Every dao
inherits this grammar.

Posture: **peers-in-inquiry / asymmetric-in-accountability.** Accountability is
non-delegable.

**Decision log schema:** DECISION / ACTOR / SCOPE / WHY / WHAT-WOULD-CHANGE-IT /
HUMAN-NOTIFIED.

**Escalation table:** human-designed, human-amended. Agents propose changes; cannot
enact them.

**Anti-sycophancy first-consulted.** The conscience is the first voice, not the last
check.

**Default-to-hold.** Uncertainty resolves toward hold, not ship.

### The Sabha ran a three-tier graded verdict in practice — field-proven, in no draft

*Recovered 2026-06-12 from the April conversation dumps. Exists in no draft. The live
Sabha run appears in the 2026-04-22/23 mox-rnd attachment.*

- **SHIP** — clears all gates; evaluator consensus on warrant and grounding
- **SHIP-WITH-TIGHTENING** — shippable; named required-vs-optional tightening items
  attached; required items logged and tracked
- **REVISE-AND-SHIP** — does not ship; specific misgroundings named; returns to author

**G10 hold-by-default:** on a split verdict (e.g. 3-3 between REVISE-AND-SHIP and
SHIP-WITH-TIGHTENING), REVISE-AND-SHIP governs. The conservative verdict holds until
convergence.

**Round-0 self-review treated as suspicious (rubric F7).** The author's own
assessment is submitted but flagged — it does not count toward convergence. This is
the one-law consequence: an author cannot inject unfabricatable signal about their own
work.

**Per-cell misgrounding scan.** Citation-content match applied to each cell of the
evaluated artifact (citing Stanford RegLab/JELS 2025 methodology).

**CANON status versioning.** Artifacts that achieve SHIP carry a CANON status tag with
evaluator history — the accreditation record is append-only.

## Ostrom commons principles apply as structured analogies

Source: dao-coordination/coordination-governance.md (deposited 2026-06-09). Every
application of Ostrom's commons principles to the dao is explicitly flagged
**[synthesis]** and **untested** — *"No source studies Ostrom-for-agent-daos
directly"* (F2 G3). These are structured analogies, not cited transfers.

**What is established:**

- **Chartered named membership (Ostrom 1A).** Only named, constitution-bound
  practitioners may WRITE the commons. Ephemeral spawned writers are a tragedy-vector.
  Ephemeral read-only subagents are fine.
- **Amendable constitution with protected dissent.** Agents propose amendments; the
  human enacts. Dissent is standing — a conscience doesn't wait to be asked.
- **Insider monitoring — two axes.** 4A: members monitor the ship gate (the gate IS
  the monitoring). 4B: commons-health audit (trigger: event vs. cadence — undecided).
- **Graduated sanctions — the 3-rung drift ladder [synthesis].** Drift is handled at
  the lowest rung that contains it: (1) gate flag or return; (2) repeated miss →
  RATCHET.md entry; (3) escalate to human. Never skip to rung 3.
- **Conjunctive ship-gate [synthesis].** Any single conscience's refusal-to-ship
  holds. The human is the defined escape valve.
- **Inspector-general invariant [synthesis].** A maker may not mark its own work
  shipped. Override requires the human, logged.
- **Three accountability axes:** vertical (human), horizontal (same-family
  consciences), diagonal (out-of-family evaluator). The diagonal axis is
  non-negotiable: *"no gemma beside gemini."*

**The dirt-road items that never landed.** dao-coordination §7.5 prescribed "three
lines in dao.md" for the drift ladder, one sentence each for the conjunctive-gate rule
and the IG invariant, plus an appetite/circuit-breaker step. The artisans dao.md
(mtime 2026-06-08, predating the spec deposit) contains none of them. They remain
spec-only as of 2026-06-12 (§10.13).

## The artisans dao — the one proven instance

`~/mox/assets/artisans/` is its own git repo and already a Claude Code plugin
(`artisans` v0.1.0). It is the only dao that has shipped real deliveries under this
doctrine. The ziran Sabha is the second live instance (deliberation pattern; not a
service wing).

### Three phases, no triage seat — artisans' on-disk anatomy

```
artisans/
├── dao.md            registry — SSoT (practitioners table, skill shelf, ship-gate protocol)
├── dao.html          human-legible companion; carries a structured REGISTRY object
├── CLAUDE.md         wing constitution; defers upward to ~/mox/assets/CLAUDE.md
├── RATCHET.md        append-only learning loop (18 entries through 2026-06-10)
├── agents/           8 practitioners in Claude Code agent format
│   ├── davinci.md    concept / invention (design phase)
│   ├── director.md   art direction (design phase)
│   ├── roark.md      architecture / contract design (design phase)
│   ├── generator.md  procedural / generative craft
│   ├── projector.md  3D → 2D illumination
│   ├── craftsperson.md  production-readiness
│   ├── veritas.md    generation ethics + ownability (conscience)
│   └── labcoat.md    HCI / perception (conscience)
└── skills/
    ├── _RUBRIC.md    ship gate: A1–A4 mechanics, B1/B2 hard gates ≥4, B3 altitude, C1/C2 fit
    └── <28 skill dirs>
```

Three phases: design trio (davinci / director / roark) → makers (generator / projector
/ craftsperson) → consciences (veritas / labcoat). Triage is a routing workflow step,
not a practitioner.

### The two-family eval panel is the third ship-gate anchor

Evaluators from a different model family, read-only, scoring against the ticket's
acceptance criteria.

Current panel: gemini (Google) + mlx-lm running Qwen3-8B. Claude is **excluded** —
same family as the makers. Gemma is avoided (collapses into Gemini's family). The
principle: *"divergence between the families is itself signal."*

What it caught that same-model self-review passed (RATCHET 2026-06-08): a fabricated
Burke quote, a Fraser quote twisted to its opposite, altitude violations where
project-specific intel had leaked into what was meant to be a broad reusable skill.

### Operational hazards (RATCHET-recorded)

**Registry-is-not-the-loader (2026-06-08).** A skill change is not done until four
places agree: (1) the skill directory, (2) `dao.md` table and list, (3) `dao.html`
REGISTRY object, (4) every agent's `agents/<name>.md` frontmatter `skills:` line —
the actual loader. Updating the registry without updating agent frontmatter leaves
agents loading deleted skills silently. Caught live when director and generator still
listed `marwar-heritage` after its demotion.

**The altitude rule (2026-06-08).** A skill is broad reusable grammar that recurs
across projects. Project-specific intel travels with the ticket as a register-grounding
output, never as a standing skill. The test: does this knowledge recur across ≥3
unrelated tickets? If not, it is intel, not grammar.

**The factory/library split (decided 2026-06-09/10, not yet executed).** The artisans
wing is a factory housed inside a personal library (`~/mox/assets/`). Two reasons to
separate: conceptual (warehouse ≠ factory) and entity (personal library vs. Mox Labs
LLC — commingling). The seam is clean; artisans is already its own repo. The split
has not been executed — it is a breaking change and requires deliberate staging. The
graduation target is the gnx/geist stack, making the artisans factory the natural
first consumer of `gnx init`.

## gnx init scaffolds the dao leg

**No genesis spec exists.** The project triad is `claude` (agent + CLAUDE.md
constitution) + `dao` (the agentic organization) + `.gnx/` tooling. What `gnx init`
places in the `dao/` directory is undocumented (§10.13). The artisans file anatomy is
the only proven template.

**Hard ACE locus 3.** The dao is the process locus of Hard ACE (§7 of the ground
truth). Its equipment IS catalog components wired at defined trigger points:
guild-arch review on design commits; antifragile ACES boundary test at Capability
registration; craft-evals gating Flow accreditation (which geist-edge reads for
autonomy ceilings); ratchet loader on SessionStart via aboot. The cix lesson applies
directly here: *shipped hooks that were never wired* — the dao components must be
structurally wired by init, not dropped as files for the project to connect manually.

**One design, two loci.** Dao membership doctrine and catalog governance are the same
invariant at different scales. Ostrom 1A chartered membership (only named,
constitution-bound practitioners write the commons) = gnx's produce-authority wall
(warrant minting restricted to an authority disjoint from the mark's author) =
ground-truth §9's "chartered membership for writers." The registry's authorship-identity
schema is dao governance applied at catalog scale.

**geist-edge enforces what the dao constitutes.** What gnx writes into the charter at
init is the design-time artifact the runtime later enforces: autonomy ceilings,
escalation table. The escalation-table format (sabha.md §6) and decision-log schema
(§5) are the concrete, machine-readable artifacts the charter component must carry for
the enforcement seam to have something to read.

**The gnx loop is the April compounding loop carried forward.** The April 2026 dao
founding conversation (recovered 2026-06-12) defined the mechanism: *"dao execution
feeds the registry that future daos compose from."* June doctrine carries this
forward as the gnx loop (Bodhi → Dagstra → HADES → registry), with the step the June
framing makes implicit kept explicit here: **artifacts promote into the registry only
after surviving the governance stack.** That is the lineage argument for
validation-at-registration and accreditation-as-exogenous-anchor (ground-truth §9's
pollution doctrine). The dao is the stack those artifacts must survive.

## The dao charter must be pre-generative, not post-hoc

The "why" under the Constitution component in the core set (§5) has a dated origin:
2026-04-19 conversation "Claude's admission about following rules over intent"
(recovered 2026-06-12 from the April dumps; in no draft).

The trigger: an Opus rule-inversion incident. The insight: *"A rubric says 'grade the
output against these criteria.' A constitution says 'generate toward these properties.'
The rubric is post-hoc. The constitution is pre-generative."* Linked to REP-002
(motivations/why outperforms mandates/how on security outcomes).

The practical consequence: the dao charter component should encode **properties and
orientations** (what the project is and why), not procedural rules (what agents must
do in sequence). The ACES properties are constitutionally encoded, not checked after
the fact. This is also why the artisans CLAUDE.md constitution works — it relocates
truth: onboarding is reading, disagreement is an edit, drift is visible. *"Charter =
the compiled, stable culture; ratchet = the learning loop by which lived experience
edits the charter."*

## Four questions remain open in the corpus

**§10.12 — Which dao sense does gnx init scaffold?** The corpus carries three senses
with supersessions never recorded. Init scaffolds the June standing-bench sense — the
`dao` leg of project anatomy (01-ecosystem.md § Project anatomy); the
April task-scoped instantiated cooperative (lifecycle: instantiated → runs → escalates
→ terminates → promotes artifacts) was explicitly left open 2026-04-28 and no document
ever closed whether it becomes a future Flow-shaped feature or is formally retired.

**§10.13 — No genesis spec; where do the dirt-road items land?** The scaffolded
`dao/` directory's contents are undocumented. The artisans anatomy is the only proven
template. The June 9 governance dirt-road items (3-rung drift ladder, conjunctive
gate, IG invariant, appetite/circuit-breaker step) were prescribed as "three lines in
dao.md" on 2026-06-09 and still aren't there. Do they land in the artisans dao.md,
in the componentized dao-charter template, or both — and what is the right flag
(established / decided / [synthesis]) for items that are prescribed-but-untested?

**Are dao practitioners registered `kind: Agent` catalog components or project-local
files?** The init interview installs "curated agents and skills" from the catalog but
the registration story for dao roles is never stated. The `--skill` agent-operability
hard rule (every `kind: Capability` must expose `--skill`) is flagged open for whether
it extends to `kind: Agent` (09-cli.md).

**Does the Sabha verdict grammar belong in the charter component or in dao-coordination
(Track A)?** The graded verdict mechanics (SHIP / SHIP-WITH-TIGHTENING /
REVISE-AND-SHIP, G10 hold-by-default, Round-0 self-review suspicion, per-cell
misgrounding scan) are field-proven ceremony mechanics from April that
dao-coordination Track A never absorbed. The charter component must carry something
machine-readable for geist-edge to enforce; whether that is the verdict grammar itself
or a reference to it is undecided.

---

*Comment on any block — the feedback loop is how this doc gets corrected.*

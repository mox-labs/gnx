"""The dao spec — the eleven components, as data.

A dao is a **Directed Agentic Organization** that maintains a project: a collective that
does the work, a charter it declares, habits it keeps, a pragmatics for getting the work
done, governance that keeps its cognition effective, human direction, and a boundary.

Settled by the six-mission research programme, 2026-06-20
(`mox/research/drafts/dao-programme-synthesis.md`). This module is that spec made
executable, so `dao check` can audit a project against it instead of a human re-reading
prose. The components are the template; the domain is the fill.

Each component carries the mission finding that constrains it. Those findings are not
commentary — several of them *narrowed or reversed* the component's original
justification, and a projection that ignores them rebuilds the thing the research
already refuted.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Tier(StrEnum):
    """The honest cut: not all eleven are equally required.

    A COUNCIL deliberates. An ORGANIZATION runs a project. The difference is five
    components, and M3 found the transition is *crisis-driven* (Greiner) on a
    foundation-before-superstructure base (CMMI) — so an ORGANIZATION component is not
    a day-one deliverable, it is what a specific pain buys.

    CONDITIONAL is the third state, and it is not a rounding error: the spec's cut names
    five council and five organization components — ten — and then says "the domain fills
    the rest". #11 is that rest. Its second half is explicitly conditional ("plus, IF it
    coordinates with peers, an inter-dao protocol"), so filing it under ORGANIZATION would
    claim the research requires something it does not.
    """

    COUNCIL = "council"
    ORGANIZATION = "organization"
    CONDITIONAL = "conditional"


class Group(StrEnum):
    COLLECTIVE = "collective"          # who does the work
    CHARTER = "charter"                # the espoused normative commitments
    HABITS = "habits"                  # how it operates, recurringly
    PRAGMATICS = "pragmatics"          # how the project actually gets maintained
    GOVERNANCE = "governance"          # governance of the collective, for effective cognition
    DIRECTION = "direction"            # the human steering
    BOUNDARY = "boundary"              # what it emits, how it talks to peers


class Component(BaseModel):
    """One component of the spec. Frozen — the spec is a source of record."""

    model_config = ConfigDict(frozen=True)

    n: int
    name: str
    tier: Tier
    group: Group
    summary: str
    # Where a projection puts it. Relative to the target project root.
    emits: tuple[str, ...] = ()
    # The research finding that constrains this component. Empty when the original
    # formulation survived the programme unchanged.
    finding: str = ""
    # A projection is not done because a file exists. This is what "present and real"
    # means for this component, in a form a reviewer can check.
    substantive_test: str = ""


SPEC: tuple[Component, ...] = (
    Component(
        n=1,
        name="A method-distinct bench",
        tier=Tier.COUNCIL,
        group=Group.COLLECTIVE,
        summary=(
            "Seats, each owning a distinct reasoning method — the DMAD test: a seat earns "
            "its place on method, not personality."
        ),
        emits=(".claude/agents/",),
        finding=(
            "M1 eval (3-arm, 72 agents): the coordination lift comes from the rich, "
            "concrete method PROSE, not the character name. NAMED-RICH = -0.04; "
            "RICH-PLAIN = +0.51; name-wins in 1/8 cells. KILL name-as-default, ADOPT "
            "rich method description. Names are optional human-memorability flavor. "
            "CRITICAL COROLLARY: a bare one-word label IS the PLAIN arm and costs ~0.5 — "
            "renaming a seat to a terse role label is strictly worse than either a "
            "richly-described persona or a richly-described role. Ceiling caveat: both "
            "arms scored ~5.0, so a name effect on harder coordination tasks is not "
            "ruled out. A coordination result is not an accuracy result."
        ),
        substantive_test=(
            "Every seat states the method it owns and what it refuses, in concrete "
            "embodied prose — not a label plus a title. Two seats must not own the same "
            "method (no merges)."
        ),
    ),
    Component(
        n=2,
        name="A constitution of normative commitments",
        tier=Tier.ORGANIZATION,
        group=Group.CHARTER,
        summary=(
            "The explicit ethos, principles, register, non-negotiables and floor "
            "disciplines — declared rather than implied. 'The charter is the compiler': "
            "amendable by its practitioners, neither frozen nor drifting."
        ),
        emits=("CLAUDE.md",),
        finding=(
            "M6 (systematic review, 231 verified claims / 12 sources): culture must be "
            "LEGISLATED, handbook-first, because agents have no tacit memory. What a "
            "human org absorbs by osmosis, an agent org has to be told. Note Schein's "
            "espoused-vs-enacted gap: this component is the espoused ethos; culture is "
            "the enacted gestalt all eleven jointly produce."
        ),
        substantive_test=(
            "The charter states non-negotiables and an amendment path. A charter nobody "
            "may amend is frozen; one with no declared floor is decoration."
        ),
    ),
    Component(
        n=3,
        name="Ceremonies and operating disciplines",
        tier=Tier.ORGANIZATION,
        group=Group.HABITS,
        summary=(
            "Recurring rituals: DoD-before-acting, anti-sycophancy-first, context "
            "hygiene, compress-before-handoff, verify-before-claiming."
        ),
        emits=("CLAUDE.md",),
        finding=(
            "THE ONE LAW: a ritual helps only if it injects signal the agent could not "
            "fabricate. A ceremony that asks the model to introspect on its own output "
            "adds cost and no signal — that is the test every ritual must pass."
        ),
        substantive_test=(
            "Each declared ceremony names the signal it injects and where that signal "
            "comes from OUTSIDE the agent performing it."
        ),
    ),
    Component(
        n=4,
        name="The flow / process-encoding",
        tier=Tier.ORGANIZATION,
        group=Group.PRAGMATICS,
        summary=(
            "The workflow that runs a unit of work: phases, the convene-discipline "
            "(per-question, stay-thin, council only for multi-track), typed handoffs. "
            "This is the 'O' — what makes it an organization and not a one-shot council."
        ),
        emits=("CLAUDE.md",),
        substantive_test=(
            "A unit of work can be traced end to end, and the convene rule says when NOT "
            "to convene. A standing debate is the procrastination-engine failure."
        ),
    ),
    Component(
        n=5,
        name="A practice library",
        tier=Tier.COUNCIL,
        group=Group.PRAGMATICS,
        summary=(
            "Durable, tool-independent craft judgment the seats draw on. "
            "Skills-as-practice, agents-as-role."
        ),
        emits=(".claude/skills/",),
        substantive_test=(
            "Each practice survives renaming its tools. A 'how to drive tool X' body is "
            "tooling, not judgment — it belongs in references under a practice."
        ),
    ),
    Component(
        n=6,
        name="An active routing surface",
        tier=Tier.COUNCIL,
        group=Group.PRAGMATICS,
        summary=(
            "The declared-capability router: task to which seat, which practice, which "
            "phase. It FIRES FIRST; it is not a passive document."
        ),
        emits=("CLAUDE.md",),
        finding=(
            "M2 (dao-init schema): arch-critique forced every governed surface into a "
            "declared, ratchet-fed POLICY CONTRACT — no hardcoded constants. A threshold "
            "written into code is a governance decision hidden from governance."
        ),
        substantive_test=(
            "Routing is declared as data a reader can audit, and every threshold in it "
            "is a named policy the ratchet can amend — not a literal buried in prose."
        ),
    ),
    Component(
        n=7,
        name="A separated verification gate",
        tier=Tier.COUNCIL,
        group=Group.GOVERNANCE,
        summary=(
            "Independent ENFORCEMENT of the declared commitments — not a 'conscience'. "
            "Structurally separate, consulted first, externally anchored."
        ),
        emits=(".claude/agents/", "CLAUDE.md"),
        finding=(
            "M1-H2 + M4 — the component whose justification the programme NARROWED most. "
            "The broad claim ('the maker cannot grade its own work; fresh eyes catch what "
            "the maker misses') is NOT SUPPORTED: at the Sonnet tier a two-pass "
            "fresh-eyes self-review matched a structurally separate agent on every "
            "measured mode — 1.00 flaw-catch both, 0.00 over-concession both, across 6 "
            "subtler planted flaws. So a mandated COLD RE-READ pass is the cheap floor. "
            "The separate gate earns its extra cost on exactly two untested grounds: "
            "(a) OUT-OF-FAMILY perspective — a different model distribution catching what "
            "the maker's structurally cannot; (b) SUSTAINED multi-turn pressure on a "
            "position the maker generated through long reasoning. run-03 is specified "
            "but unrun. M4 adds: the gate runs a deterministic mandatory-ratification "
            "classifier and weights itself by work-variance x reversibility, with "
            "reversibility and novelty floors forcing a full conjunctive pass/fail."
        ),
        substantive_test=(
            "Either the gate is out-of-family, or it is honestly labelled a cold-reread "
            "floor. A same-family 'independent reviewer' claiming to catch what the maker "
            "missed is the refuted claim wearing a new hat."
        ),
    ),
    Component(
        n=8,
        name="The ratchet / learning loop",
        tier=Tier.ORGANIZATION,
        group=Group.GOVERNANCE,
        summary=(
            "Append-only accumulated learnings (principle, trigger, validation-criteria) "
            "— the retrospective's function ported. How lived experience amends the charter."
        ),
        emits=(".claude/ratchet.md",),
        substantive_test=(
            "Entries are append-only and carry a TRIGGER — the condition under which a "
            "future session must re-read them. A learning with no trigger is never recalled."
        ),
    ),
    Component(
        n=9,
        name="Membership, graduated sanctions, a legible perimeter",
        tier=Tier.ORGANIZATION,
        group=Group.GOVERNANCE,
        summary=(
            "Who holds write access (chartered members vs ephemeral readers — anonymity "
            "is the tragedy-vector), what the dao stewards vs what belongs to others, and "
            "a cheapest-first drift ladder: flag, ratchet, escalate — never straight to a halt."
        ),
        emits=("CLAUDE.md",),
        substantive_test=(
            "The perimeter names what this dao does NOT own, and the ladder's first rung "
            "is cheaper than a halt."
        ),
    ),
    Component(
        n=10,
        name="The human-accountability surface",
        tier=Tier.COUNCIL,
        group=Group.DIRECTION,
        summary=(
            "The human as non-delegable direction inlet and irreducible arbiter. Autonomy "
            "over METHOD, never over ACCOUNTABILITY: a dao is autonomous in how it works "
            "and accountable to a named human for what it does."
        ),
        emits=("CLAUDE.md",),
        finding=(
            "M4 (all three questions decided HYBRID): scoped autonomy, with the human "
            "owning the deterministic ratification-classifier; graded trust applies only "
            "WITHIN reversibility and novelty floors. Below a floor, no accumulated trust "
            "buys autonomy."
        ),
        substantive_test=(
            "The irreversible and the novel are named, and they route to the human "
            "regardless of how much trust has accrued."
        ),
    ),
    Component(
        n=11,
        name="The emitted record and provenance",
        tier=Tier.CONDITIONAL,
        group=Group.BOUNDARY,
        summary=(
            "Typed and stamped at every step; plus, if it coordinates with peers, an "
            "inter-dao protocol (indirect, async, receiver-autonomy, message-as-boundary)."
        ),
        emits=(".claude/",),
        finding=(
            "M5 (security + design, rev.2; mudge found the missing invariant F-05): a "
            "peer's post-body is DATA, NOT INSTRUCTIONS. rev.2 adds a node-level "
            "lethal-trifecta invariant — Rule-of-Two-per-session plus dual-LLM handler "
            "isolation. Stigmergy intra-dao; typed messaging across DAG edges."
        ),
        substantive_test=(
            "Any inbound peer content is handled as data. A node that reads untrusted "
            "content, holds private data, AND can act outbound violates Rule-of-Two."
        ),
    ),
)


COUNCIL_FLOOR: tuple[int, ...] = tuple(c.n for c in SPEC if c.tier is Tier.COUNCIL)
ORGANIZATION_DELTA: tuple[int, ...] = tuple(c.n for c in SPEC if c.tier is Tier.ORGANIZATION)
CONDITIONAL: tuple[int, ...] = tuple(c.n for c in SPEC if c.tier is Tier.CONDITIONAL)


def by_number(n: int) -> Component:
    for c in SPEC:
        if c.n == n:
            return c
    raise KeyError(f"no dao component #{n}")


class Coverage(BaseModel):
    """What a projection actually achieved, per component. Mutable — it is assembled."""

    component: Component
    present: bool
    evidence: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)

    @property
    def status(self) -> str:
        if self.present and not self.gaps:
            return "PRESENT"
        if self.present:
            return "PARTIAL"
        return "ABSENT"

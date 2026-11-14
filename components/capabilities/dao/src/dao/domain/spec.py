"""The dao spec: the six documents a directed agent organization keeps, as data.

A dao is a **directed agent organization** that maintains a project. It is described the way
Ostrom describes any institution: **positions** (the roles), **participants** (who holds them),
and three levels of rule: a **constitution** (what the organization is for, and who may change
its rules), **governing policies** (who decides what, and how a rule is changed) and
**operating principles** (how day-to-day work is done). The sixth document is the **front
door**: the page that explains the organization to a reader, and the pointer that makes an
agent read it.

This module is that spec made executable, so `dao check` audits a project against it. Each
element carries what it must contain and, where one exists, the research finding that shaped
the requirement (the six-mission programme that closed 2026-06-20).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Key(StrEnum):
    """The six elements, by the name the CLI and the reports use."""

    CONSTITUTION = "constitution"
    POLICIES = "policies"
    PRINCIPLES = "principles"
    POSITIONS = "positions"
    PARTICIPANTS = "participants"
    FRONT_DOOR = "front-door"


class Status(StrEnum):
    """PARTIAL is the useful one: the file is there and its test does not pass."""

    PRESENT = "PRESENT"
    PARTIAL = "PARTIAL"
    ABSENT = "ABSENT"


class Element(BaseModel):
    """One element of the spec. Frozen: the spec is a source of record."""

    model_config = ConfigDict(frozen=True)

    key: Key
    name: str
    # The file that holds it, relative to the organization's directory.
    file: str
    summary: str
    # What "present and real" means, in a form a reviewer can check.
    must_contain: tuple[str, ...]
    # The research finding behind a requirement. Empty when there is none.
    finding: str = ""


SPEC: tuple[Element, ...] = (
    Element(
        key=Key.CONSTITUTION,
        name="Constitution",
        file="constitution.md",
        summary=(
            "What the organization is for, what it commits to, and who may change its rules. "
            "Every other document is written under it."
        ),
        must_contain=(
            "a Purpose section",
            "a Commitments section: what always holds and what never happens",
            "an Amendment section that names the position allowed to change this file",
        ),
        finding=(
            "M6 (231 verified claims, 12 sources): agents have no tacit memory, so what a "
            "human organization absorbs by osmosis has to be written down. A constitution "
            "nobody may amend is frozen; one with no commitments is decoration."
        ),
    ),
    Element(
        key=Key.POLICIES,
        name="Governing policies",
        file="policies.md",
        summary=(
            "Who decides what: which decisions a participant makes alone, which need a "
            "named position, how work is checked, and how a policy or a principle is changed."
        ),
        must_contain=(
            "a Decisions section that sends the irreversible and the novel to a human",
            "a Verification section that says what kind of check it is: a different model "
            "family, the same family reading cold, or a person",
            "a Changing the rules section",
        ),
        finding=(
            "M4: scoped autonomy. Below a reversibility or novelty floor no accumulated "
            "trust buys autonomy, so those decisions go to a human. M1-H2: a same-family "
            "reviewer matched a two-pass cold re-read on every measured mode, so a check "
            "must say which kind it is; neither label is ranked above the other, because "
            "that comparison has not been run."
        ),
    ),
    Element(
        key=Key.PRINCIPLES,
        name="Operating principles",
        file="principles.md",
        summary="How day-to-day work is done here: the habits every participant keeps.",
        must_contain=("at least three principles, each a list item or a section",),
        finding=(
            "The one law for a habit: it helps only if it injects a signal the agent could "
            "not fabricate. A principle that asks a model to introspect on its own output "
            "adds cost and no signal. The audit cannot test this; the review in the skill does."
        ),
    ),
    Element(
        key=Key.POSITIONS,
        name="Positions",
        file="positions.md",
        summary=(
            "The roles. Each says what it does, what it must not do, and which other "
            "position checks its work."
        ),
        must_contain=(
            "at least two positions, each a section",
            "for each: Does, Must not, and Checked by",
            "a remit written as prose, not a label",
            "Checked by names a different position, unless only humans hold the position",
        ),
        finding=(
            "M1 (three arms, 72 agents): the coordination lift comes from rich, concrete "
            "prose about the method, not from the name (rich minus plain = +0.51; named "
            "minus rich = -0.04). A bare label is the worst of the three conditions. A "
            "coordination result is not an accuracy result."
        ),
    ),
    Element(
        key=Key.PARTICIPANTS,
        name="Participants",
        file="participants.md",
        summary="Who holds each position: a person, or an agent and the file that defines it.",
        must_contain=(
            "a table with Participant, Kind (human or agent) and Position columns",
            "at least one human",
            "every position held by someone, and no participant in a position that does not exist",
            "every file named under Defined in exists",
        ),
        finding=(
            "M4: a dao is autonomous in how it works and accountable to a named human for "
            "what it does. An organization with no human participant is not directed."
        ),
    ),
    Element(
        key=Key.FRONT_DOOR,
        name="Front door",
        file="README.md",
        summary=(
            "The page that explains the organization to someone who has never seen it, and "
            "the line in the project's agent instructions that makes an agent read it."
        ),
        must_contain=(
            "a README in the organization's directory that links the other five documents",
            "the project's agent instructions (CLAUDE.md or AGENTS.md) name that directory",
        ),
        finding=(
            "An organization nobody is pointed at exists on paper only: an agent that "
            "starts a session without the pointer never reads a word of it."
        ),
    ),
)


def by_key(key: Key | str) -> Element:
    for e in SPEC:
        if e.key == key:
            return e
    raise KeyError(f"no dao element {key!r}")


class Coverage(BaseModel):
    """What a project actually has for one element. Mutable: it is assembled."""

    element: Element
    present: bool
    evidence: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)

    @property
    def status(self) -> Status:
        if self.present and not self.gaps:
            return Status.PRESENT
        if self.present:
            return Status.PARTIAL
        return Status.ABSENT

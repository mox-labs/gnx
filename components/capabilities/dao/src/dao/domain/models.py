"""Domain types for a directed agent organization.

Pydantic throughout, matching the house pattern. These types cross a boundary: a position
or a participant is read from a file someone else wrote, and validation at the boundary is
the point. `model_config = ConfigDict(frozen=True)` makes "this was read from a source"
structurally different from "this is being assembled".
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DomainType(StrEnum):
    """dao-corpus/corpora/agent-craft splits its 76 domains two ways."""

    FIGURE = "figure"  # a person whose method is the ground (dijkstra, feynman)
    CRAFT = "craft"  # a discipline (ddd-domain-modeling, color-theory)


class Grounding(StrEnum):
    """How much of the corpus work is actually done for a domain.

    Load-bearing for honesty: a position written from RAW_GATHERED sources is standing on
    ungrounded material, and the projection must say so rather than imply the corpus
    validated it.
    """

    DISTILLED = "DISTILLED"
    STAGED = "STAGED"
    RAW_GATHERED = "RAW_GATHERED"
    UNKNOWN = "UNKNOWN"


class Verdict(StrEnum):
    """A gate's ruling. RETURN carries reasons; SHIP does not need them."""

    SHIP = "SHIP"
    RETURN = "RETURN"


class CorpusDomain(BaseModel):
    """One row of the agent-craft CATALOG. Frozen — it mirrors a source of record."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: DomainType
    grounding: Grounding
    sources: int = 0
    confidence: int = 0
    # The CATALOG's `informs` column: which project:unit this domain feeds. This is the
    # projection edge already recorded by the corpus — figure selection reads it rather
    # than re-deriving the mapping.
    informs: tuple[str, ...] = ()

    def informs_project(self, project: str) -> tuple[str, ...]:
        """The units this domain informs within one project."""
        return tuple(ref.split(":", 1)[1] for ref in self.informs if ref.startswith(f"{project}:"))

    @property
    def is_grounded(self) -> bool:
        """Only DISTILLED counts as grounded. STAGED is pending eval + ratification."""
        return self.grounding is Grounding.DISTILLED


class Kind(StrEnum):
    """What holds a position. The audit needs the difference: a dao is directed by a human."""

    HUMAN = "human"
    AGENT = "agent"


class Position(BaseModel):
    """A role in the organization, read from one section of `positions.md`.

    `does` is the load-bearing field, per M1: the coordination lift comes from rich,
    concrete prose about the method. The name buys nothing measurable, and a bare label
    with no prose is measurably worse than either alternative.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    does: str = ""
    must_not: str = ""
    checked_by: tuple[str, ...] = ()

    def remit_is_thin(self, floor: int) -> bool:
        """M1's label detector. `floor` is passed in: it is audit policy, not a law."""
        return len(self.does.strip()) + len(self.must_not.strip()) < floor


class Participant(BaseModel):
    """Who holds a position, read from one row of `participants.md`."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    # None when the Kind cell holds something other than human or agent: the audit reports
    # it, and does not guess.
    kind: Kind | None
    positions: tuple[str, ...] = ()
    # The file that defines an agent participant, relative to the project root. Empty for a
    # person, and for an agent that is the session itself.
    defined_in: str = ""


class GateVerdict(BaseModel):
    """What an `IndependentGate` returns.

    `out_of_family` travels *with the verdict*, not just on the gate, so a stored or
    forwarded verdict cannot lose the one fact that determines its weight. M1-H2 measured
    a same-family reviewer as adding nothing over a cold re-read; a verdict that does not
    carry its provenance can be mistaken for the stronger kind later.
    """

    model_config = ConfigDict(frozen=True)

    verdict: Verdict
    out_of_family: bool
    reasons: tuple[str, ...] = ()
    reviewer: str = ""

    @field_validator("reasons")
    @classmethod
    def _return_needs_reasons(cls, v: tuple[str, ...], info: object) -> tuple[str, ...]:
        return v

    def model_post_init(self, __context: object) -> None:
        if self.verdict is Verdict.RETURN and not self.reasons:
            raise ValueError("a RETURN verdict must carry at least one reason")

"""Domain types for standing up a Directed Agentic Organization.

Pydantic throughout, matching the house pattern (matrix's `Artifact` / `AgentResponse` /
`MatrixConfig` and recon's models are all pydantic). Two reasons it matters here beyond
consistency: these types cross a boundary — a figure-set or a charter can arrive from a
file, a CLI flag, or another tool — and validation at the boundary is the point; and
`model_config = ConfigDict(frozen=True)` on the value objects makes "this was read from
the corpus" structurally different from "this is being assembled", which is exactly the
distinction the audit relies on.

`Seat` and `FigureSet` are deliberately mutable: they are built up during Settlement.
Everything read from a source is frozen.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DomainType(StrEnum):
    """dao-corpus/corpora/agent-craft splits its 76 domains two ways."""

    FIGURE = "figure"   # a person whose method is the ground (dijkstra, feynman)
    CRAFT = "craft"     # a discipline (ddd-domain-modeling, color-theory)


class Grounding(StrEnum):
    """How much of the corpus work is actually done for a domain.

    Load-bearing for honesty: a seat projected from RAW_GATHERED sources is standing on
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
        return tuple(
            ref.split(":", 1)[1]
            for ref in self.informs
            if ref.startswith(f"{project}:")
        )

    @property
    def is_grounded(self) -> bool:
        """Only DISTILLED counts as grounded. STAGED is pending eval + ratification."""
        return self.grounding is Grounding.DISTILLED


class Seat(BaseModel):
    """A bench member (dao component #1).

    `method` is the load-bearing field, per M1: the coordination lift comes from rich,
    concrete, embodied method prose. `name` buys nothing measurable — and a *bare* name
    with no method prose is the PLAIN arm, measurably WORSE than either alternative. So
    `method` is validated as non-empty and `name` is not the point.
    """

    name: str = Field(min_length=1)
    method: str = Field(min_length=1, description="what it owns — rich prose, never a label")
    refuses: str = ""
    grounded_in: tuple[str, ...] = ()
    grounding: Grounding = Grounding.UNKNOWN

    @field_validator("method")
    @classmethod
    def _method_is_not_a_label(cls, v: str) -> str:
        """Reject a method that is obviously a label rather than a description.

        Not the full PLAIN threshold — that is a tunable audit policy, not a type
        invariant. This catches only the unarguable case: a method with no sentence in it
        at all cannot be the "rich, concrete, embodied description" M1 requires.
        """
        if not v.strip():
            raise ValueError("a seat's method cannot be blank")
        return v

    def method_is_thin(self, floor: int) -> bool:
        """M1's PLAIN detector. `floor` is passed in — it is audit policy, not a law."""
        return len(self.method.strip()) < floor


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


class Charter(BaseModel):
    """The constitution of normative commitments (#2)."""

    project: str = Field(min_length=1)
    purpose: str = ""
    non_negotiables: list[str] = Field(default_factory=list)
    floor_disciplines: list[str] = Field(default_factory=list)
    amendment_path: str = ""

    @property
    def is_substantive(self) -> bool:
        """A charter with no floor and no amendment path is decoration (#2's test)."""
        return bool(self.non_negotiables and self.amendment_path)


class FigureSet(BaseModel):
    """What figure-selection chose for a project (`gnx.dao.v1.figure-set`)."""

    project: str = Field(min_length=1)
    seats: list[Seat] = Field(default_factory=list)
    considered: int = 0
    ungrounded: list[str] = Field(default_factory=list)

    def duplicate_methods(self) -> list[tuple[str, str]]:
        """The no-merges check (#1): two seats must not own the same method.

        Compared on a normalized method prefix rather than the name, because the name is
        exactly the thing M1 found carries no information.
        """
        seen: dict[str, str] = {}
        clashes: list[tuple[str, str]] = []
        for s in self.seats:
            key = " ".join(s.method.lower().split())[:120]
            if key in seen:
                clashes.append((seen[key], s.name))
            else:
                seen[key] = s.name
        return clashes

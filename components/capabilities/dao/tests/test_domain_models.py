"""Domain model invariants — the ones that must hold structurally, not by convention."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dao.domain.models import (
    CorpusDomain,
    DomainType,
    GateVerdict,
    Grounding,
    Kind,
    Participant,
    Position,
    Verdict,
)

RICH = "This position owns the following method, described concretely enough to guide behavior."


class TestGateVerdict:
    def test_return_must_carry_reasons(self) -> None:
        """A RETURN with no reasons is unactionable — the author cannot fix anything."""
        with pytest.raises(ValidationError):
            GateVerdict(verdict=Verdict.RETURN, out_of_family=True)

    def test_ship_needs_no_reasons(self) -> None:
        v = GateVerdict(verdict=Verdict.SHIP, out_of_family=False)
        assert v.reasons == ()

    def test_out_of_family_travels_with_the_verdict(self) -> None:
        """M1-H2: a same-family verdict must not be mistakable for the stronger kind.

        Carrying the flag on the verdict itself — not only on the gate that produced it —
        is what keeps a forwarded or stored verdict honest about its own weight.
        """
        v = GateVerdict(verdict=Verdict.RETURN, out_of_family=False, reasons=("thin",))
        assert v.out_of_family is False

    def test_verdict_is_frozen(self) -> None:
        v = GateVerdict(verdict=Verdict.SHIP, out_of_family=True)
        with pytest.raises(ValidationError):
            v.out_of_family = False


class TestPosition:
    def test_remit_is_thin_takes_policy_not_a_constant(self) -> None:
        p = Position(name="x", does="short", must_not="no")
        assert p.remit_is_thin(240) is True
        assert p.remit_is_thin(2) is False

    def test_a_position_read_from_a_file_is_frozen(self) -> None:
        p = Position(name="x", does=RICH)
        with pytest.raises(ValidationError):
            p.does = "other"


class TestParticipant:
    def test_an_unreadable_kind_is_none_not_a_guess(self) -> None:
        assert Participant(name="x", kind=None).kind is None
        assert Participant(name="x", kind=Kind.AGENT).kind is Kind.AGENT


class TestCorpusDomain:
    def test_only_distilled_counts_as_grounded(self) -> None:
        """STAGED is pending eval + ratification — not yet ground to stand a position on."""
        for g, expected in (
            (Grounding.DISTILLED, True),
            (Grounding.STAGED, False),
            (Grounding.RAW_GATHERED, False),
            (Grounding.UNKNOWN, False),
        ):
            d = CorpusDomain(name="d", type=DomainType.FIGURE, grounding=g)
            assert d.is_grounded is expected

    def test_frozen(self) -> None:
        d = CorpusDomain(name="d", type=DomainType.CRAFT, grounding=Grounding.DISTILLED)
        with pytest.raises(ValidationError):
            d.name = "other"

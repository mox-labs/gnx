"""Domain model invariants — the ones that must hold structurally, not by convention."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dao.domain.models import (
    CorpusDomain,
    DomainType,
    FigureSet,
    GateVerdict,
    Grounding,
    Seat,
    Verdict,
)

RICH = "This seat owns the following method, described concretely enough to guide behavior."


class TestGateVerdict:
    def test_return_must_carry_reasons(self):
        """A RETURN with no reasons is unactionable — the author cannot fix anything."""
        with pytest.raises(ValidationError):
            GateVerdict(verdict=Verdict.RETURN, out_of_family=True)

    def test_ship_needs_no_reasons(self):
        v = GateVerdict(verdict=Verdict.SHIP, out_of_family=False)
        assert v.reasons == ()

    def test_out_of_family_travels_with_the_verdict(self):
        """M1-H2: a same-family verdict must not be mistakable for the stronger kind.

        Carrying the flag on the verdict itself — not only on the gate that produced it —
        is what keeps a forwarded or stored verdict honest about its own weight.
        """
        v = GateVerdict(verdict=Verdict.RETURN, out_of_family=False, reasons=("thin",))
        assert v.out_of_family is False

    def test_verdict_is_frozen(self):
        v = GateVerdict(verdict=Verdict.SHIP, out_of_family=True)
        with pytest.raises(ValidationError):
            v.out_of_family = False


class TestSeat:
    def test_blank_method_rejected(self):
        with pytest.raises(ValidationError):
            Seat(name="x", method="   ")

    def test_method_is_thin_takes_policy_not_a_constant(self):
        s = Seat(name="x", method="short")
        assert s.method_is_thin(240) is True
        assert s.method_is_thin(2) is False


class TestFigureSet:
    def test_duplicate_methods_compares_method_not_name(self):
        """Two differently-named seats owning one method is the no-merges violation."""
        fs = FigureSet(
            project="p",
            seats=[Seat(name="alpha", method=RICH), Seat(name="beta", method=RICH)],
        )
        assert fs.duplicate_methods() == [("alpha", "beta")]

    def test_distinct_methods_do_not_clash(self):
        fs = FigureSet(
            project="p",
            seats=[
                Seat(name="alpha", method=RICH + " It reviews boundaries."),
                Seat(name="beta", method="A wholly different method, described at length here."),
            ],
        )
        assert fs.duplicate_methods() == []


class TestCorpusDomain:
    def test_only_distilled_counts_as_grounded(self):
        """STAGED is pending eval + ratification — not yet ground to stand a seat on."""
        for g, expected in (
            (Grounding.DISTILLED, True),
            (Grounding.STAGED, False),
            (Grounding.RAW_GATHERED, False),
            (Grounding.UNKNOWN, False),
        ):
            d = CorpusDomain(name="d", type=DomainType.FIGURE, grounding=g)
            assert d.is_grounded is expected

    def test_frozen(self):
        d = CorpusDomain(name="d", type=DomainType.CRAFT, grounding=Grounding.DISTILLED)
        with pytest.raises(ValidationError):
            d.name = "other"

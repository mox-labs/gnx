"""The audit must distinguish PRESENT from PARTIAL — an artifact is not a component.

These tests exist because the whole failure mode the spec guards against is a projection
that creates eleven files and claims eleven components. A directory named `agents/` with
one thin prompt in it must not read as a bench.
"""

from __future__ import annotations

from dao.application.audit import POLICY, audit, read_bench, summarise
from dao.domain.spec import (
    CONDITIONAL,
    COUNCIL_FLOOR,
    ORGANIZATION_DELTA,
    SPEC,
    Tier,
)

RICH = "x" * (POLICY["seat_method_min_chars"] + 50)


def _seat(tmp_path, name: str, body: str, refuses: str = "Cannot discuss: everything else."):
    d = tmp_path / ".claude" / "agents"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(f"---\nname: {name}\n---\n\n{body}\n\n{refuses}\n")


def _status(coverage, n: int) -> str:
    return next(c for c in coverage if c.component.n == n).status


def test_empty_project_has_no_council_floor(tmp_path):
    cov = audit(tmp_path)
    s = summarise(cov)
    assert not s["council_complete"]
    assert set(s["council_missing"]) == set(COUNCIL_FLOOR)


def test_spec_tiers_partition_all_eleven():
    assert len(SPEC) == 11
    tiers = [set(COUNCIL_FLOOR), set(ORGANIZATION_DELTA), set(CONDITIONAL)]
    assert set().union(*tiers) == {c.n for c in SPEC}
    for a, b in ((0, 1), (0, 2), (1, 2)):
        assert not tiers[a] & tiers[b], "tiers must be disjoint"


def test_council_floor_is_the_five_the_research_named():
    assert set(COUNCIL_FLOOR) == {1, 5, 6, 7, 10}


def test_organization_delta_is_the_five_the_research_named():
    assert set(ORGANIZATION_DELTA) == {2, 3, 4, 8, 9}


def test_component_11_is_conditional_not_organization():
    """The cut names 5 council + 5 organization = ten, then "the domain fills the rest".

    #11 is that rest, and its inter-dao half is explicitly conditional. Filing it under
    ORGANIZATION would claim the research requires something it does not.
    """
    assert set(CONDITIONAL) == {11}


def test_thin_seat_makes_the_bench_partial_not_present(tmp_path):
    """M1: a one-word label is the PLAIN arm. A thin seat is a gap, not a bench."""
    _seat(tmp_path, "terse", "Reviews things.")
    _seat(tmp_path, "rich", RICH)
    cov = audit(tmp_path)
    assert _status(cov, 1) == "PARTIAL"
    gaps = next(c for c in cov if c.component.n == 1).gaps
    assert any("PLAIN risk" in g and "terse" in g for g in gaps)
    assert not any("rich" in g for g in gaps if "PLAIN risk" in g)


def test_single_rich_seat_is_still_not_a_bench(tmp_path):
    _seat(tmp_path, "solo", RICH)
    cov = audit(tmp_path)
    assert _status(cov, 1) == "PARTIAL"
    assert any("disagree" in g for g in next(c for c in cov if c.component.n == 1).gaps)


def test_seat_without_a_stated_refusal_is_flagged(tmp_path):
    _seat(tmp_path, "a", RICH, refuses="")
    _seat(tmp_path, "b", RICH, refuses="")
    gaps = next(c for c in audit(tmp_path) if c.component.n == 1).gaps
    assert any("refusal" in g or "boundary" in g for g in gaps)


def test_same_family_gate_claiming_independence_is_flagged(tmp_path):
    """M1-H2's sharpest test: the refuted claim wearing a new hat."""
    _seat(tmp_path, "maker", RICH)
    _seat(tmp_path, "reviewer", RICH + " This seat verifies and reviews the work.")
    (tmp_path / "CLAUDE.md").write_text(
        "# Project\n\nA verification gate reviews every change independently.\n"
    )
    cov = audit(tmp_path)
    gaps = next(c for c in cov if c.component.n == 7).gaps
    assert any("out-of-family" in g for g in gaps)


def test_out_of_family_gate_satisfies_the_test(tmp_path):
    _seat(tmp_path, "maker", RICH)
    _seat(tmp_path, "gatekeeper", RICH + " This seat verifies the work and may refuse it.")
    (tmp_path / "CLAUDE.md").write_text(
        "# Project\n\nThe verification gate runs out-of-family via mlx-lm — a different\n"
        "model distribution, per M1-H2.\n"
    )
    cov = audit(tmp_path)
    assert not any("out-of-family" in g
                   for g in next(c for c in cov if c.component.n == 7).gaps)


def test_honest_cold_reread_label_also_satisfies_it(tmp_path):
    """The cheap floor is acceptable — as long as it is labelled, not oversold."""
    _seat(tmp_path, "maker", RICH)
    _seat(tmp_path, "checker", RICH + " This seat reviews and may refuse.")
    (tmp_path / "CLAUDE.md").write_text(
        "# Project\n\nGate: a mandated cold re-read pass. This is the cheap floor, not an\n"
        "out-of-family anchor, and is labelled as such.\n"
    )
    cov = audit(tmp_path)
    assert not any("out-of-family" in g
                   for g in next(c for c in cov if c.component.n == 7).gaps)


def test_ratchet_without_trigger_is_partial(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "ratchet.md").write_text("# Ratchet\n\nWe learned a thing.\n")
    cov = audit(tmp_path)
    assert _status(cov, 8) == "PARTIAL"
    assert any("TRIGGER" in g for g in next(c for c in cov if c.component.n == 8).gaps)


def test_ratchet_with_trigger_is_present(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "ratchet.md").write_text(
        "# Ratchet\n\n## Learning\n\n### Future Triggers\n- On any intake, re-read this.\n"
    )
    assert _status(audit(tmp_path), 8) == "PRESENT"


def test_charter_without_amendment_path_is_partial(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# Project\n\nNon-negotiable: never ship unverified.\n")
    cov = audit(tmp_path)
    assert _status(cov, 2) == "PARTIAL"
    assert any("amendment" in g for g in next(c for c in cov if c.component.n == 2).gaps)


def test_direction_without_reversibility_floors_is_partial(tmp_path):
    (tmp_path / "CLAUDE.md").write_text(
        "# Project\n\nThe human is the non-delegable direction inlet and accountable arbiter.\n"
    )
    cov = audit(tmp_path)
    assert _status(cov, 10) == "PARTIAL"
    assert any("irreversible" in g.lower() for g in
               next(c for c in cov if c.component.n == 10).gaps)


def test_read_bench_prefers_frontmatter_name(tmp_path):
    d = tmp_path / ".claude" / "agents"
    d.mkdir(parents=True)
    (d / "file-name.md").write_text("---\nname: real-name\n---\n\nbody\n")
    assert read_bench(tmp_path)[0].name == "real-name"


def test_every_component_declares_a_substantive_test():
    """A component with no substantive test cannot be audited beyond file-existence."""
    missing = [c.n for c in SPEC if not c.substantive_test]
    assert not missing, f"components lacking a substantive test: {missing}"


def test_council_components_all_carry_a_check():
    from dao.application.audit import _CHECKS
    assert set(_CHECKS) == {c.n for c in SPEC}


def test_tier_membership_matches_spec_objects():
    for c in SPEC:
        assert (c.n in COUNCIL_FLOOR) == (c.tier is Tier.COUNCIL)
        assert (c.n in CONDITIONAL) == (c.tier is Tier.CONDITIONAL)

"""Audit a project against the dao spec.

`dao check` answers one question honestly: which of the eleven components does this
project actually have, and where is a component present in name but not in substance?

The distinction matters more than the count. Every component has a `substantive_test` in
`domain.spec`, because a projection that creates `.claude/agents/` and stops has
component #1 as a directory, not as a bench. This module reports PRESENT / PARTIAL /
ABSENT, never a score — a single number would let a project average away a missing gate.
"""

from __future__ import annotations

import re
from pathlib import Path

from dao.domain.models import Grounding, Seat
from dao.domain.spec import SPEC, Component, Coverage, Tier

# --- policy values -----------------------------------------------------------------
# M2's ruling: every governed surface is a declared, ratchet-fed policy contract — no
# hardcoded constants. These are the audit's thresholds, named so they can be cited and
# amended rather than discovered by reading code.
POLICY = {
    # M1: a seat's method must be rich embodied prose. Below this it is closer to the
    # PLAIN arm (a label), which measured ~0.5 WORSE than either richer condition.
    "seat_method_min_chars": 240,
    # A bench of one is not a bench; a council needs enough seats to disagree.
    "bench_min_seats": 2,
    # #8: a ratchet entry without a trigger is never recalled, so it teaches nothing.
    "ratchet_requires_trigger": True,
}


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _frontmatter_and_body(text: str) -> tuple[str, str]:
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    return (m.group(1), m.group(2)) if m else ("", text)


def read_bench(root: Path) -> list[Seat]:
    """Read the bench from `.claude/agents/*.md`.

    The method is taken to be the agent's body — the system prompt IS the method
    description. The name is read but never used to judge the seat, per M1.
    """
    seats: list[Seat] = []
    agents = root / ".claude" / "agents"
    if not agents.is_dir():
        return seats
    for f in sorted(agents.glob("*.md")):
        fm, body = _frontmatter_and_body(_read(f))
        name = f.stem
        m = re.search(r"^name:\s*(\S+)", fm, re.M)
        if m:
            name = m.group(1).strip().strip("\"'")
        refuses = ""
        r = re.search(
            r"(?:Cannot discuss|You refuse|Refuses|Orthogonality Lock)[:\s]*(.{0,300})",
            body, re.S | re.I,
        )
        if r:
            refuses = " ".join(r.group(1).split())
        seats.append(Seat(name=name, method=body, refuses=refuses,
                          grounding=Grounding.UNKNOWN))
    return seats


# --- per-component checks ----------------------------------------------------------
# Each returns (present, evidence, gaps). A gap means "the artifact is there and the
# substantive test does not pass" — which is the finding worth surfacing.

def _check_bench(root: Path) -> tuple[bool, list[str], list[str]]:
    seats = read_bench(root)
    if not seats:
        return False, [], ["no .claude/agents/*.md"]
    evidence = [f"{len(seats)} seat(s): {', '.join(s.name for s in seats[:8])}"
                + (" …" if len(seats) > 8 else "")]
    gaps: list[str] = []
    if len(seats) < POLICY["bench_min_seats"]:
        gaps.append(f"only {len(seats)} seat(s); a bench needs "
                    f">= {POLICY['bench_min_seats']} to disagree")
    thin = [s.name for s in seats if s.method_is_thin(POLICY["seat_method_min_chars"])]
    if thin:
        gaps.append(
            f"thin method prose (M1 PLAIN risk, < {POLICY['seat_method_min_chars']} chars): "
            + ", ".join(thin)
        )
    unbounded = [s.name for s in seats if not s.refuses]
    if unbounded:
        gaps.append("no stated refusal/boundary — method-ownership unverifiable: "
                    + ", ".join(unbounded[:6]) + (" …" if len(unbounded) > 6 else ""))
    return True, evidence, gaps


def _check_charter(root: Path) -> tuple[bool, list[str], list[str]]:
    md = root / "CLAUDE.md"
    if not md.is_file():
        return False, [], ["no CLAUDE.md at the project root"]
    text = _read(md)
    evidence = [f"CLAUDE.md ({len(text.split())} words)"]
    gaps: list[str] = []
    if not re.search(r"non-negotiab|never |must not|floor", text, re.I):
        gaps.append("no non-negotiables or floor disciplines declared")
    if not re.search(r"amend|revise|ratchet", text, re.I):
        gaps.append("no amendment path — a charter nobody may amend is frozen")
    return True, evidence, gaps


def _check_ceremonies(root: Path) -> tuple[bool, list[str], list[str]]:
    text = _read(root / "CLAUDE.md")
    if not text:
        return False, [], ["no CLAUDE.md to declare ceremonies in"]
    named = [k for k in ("definition of done", "verify", "compact", "handoff",
                         "sycophan", "context hygiene", "cold re-read", "reset")
             if k in text.lower()]
    if not named:
        return False, [], ["no recurring disciplines named"]
    gaps = []
    if not re.search(r"could not fabricate|external|out-of-family|outside", text, re.I):
        gaps.append("THE ONE LAW unverified: no ceremony names the signal it injects "
                    "from outside the agent performing it")
    return True, [f"disciplines named: {', '.join(named)}"], gaps


def _check_flow(root: Path) -> tuple[bool, list[str], list[str]]:
    text = _read(root / "CLAUDE.md")
    if not re.search(r"phase|workflow|pipeline|flow|process", text, re.I):
        return False, [], ["no process-encoding — this is a council, not an organization"]
    gaps = []
    if not re.search(r"convene|stay thin|do not convene|only for", text, re.I):
        gaps.append("no convene-discipline: nothing says when NOT to convene "
                    "(the procrastination-engine failure)")
    return True, ["process described in CLAUDE.md"], gaps


def _check_practices(root: Path) -> tuple[bool, list[str], list[str]]:
    skills = root / ".claude" / "skills"
    found = sorted(p.parent.name for p in skills.glob("*/SKILL.md")) if skills.is_dir() else []
    if not found:
        return False, [], ["no .claude/skills/*/SKILL.md"]
    return True, [f"{len(found)} practice(s): {', '.join(found[:8])}"
                  + (" …" if len(found) > 8 else "")], []


def _check_routing(root: Path) -> tuple[bool, list[str], list[str]]:
    text = _read(root / "CLAUDE.md")
    if not re.search(r"rout|which (seat|agent)|dispatch|when to use", text, re.I):
        return False, [], ["no routing surface — nothing maps a task to a seat"]
    gaps = []
    if not re.search(r"\|.*\|", text):
        gaps.append("routing is prose, not declared data a reader can audit")
    return True, ["routing declared in CLAUDE.md"], gaps


def _check_gate(root: Path) -> tuple[bool, list[str], list[str]]:
    text = _read(root / "CLAUDE.md")
    seats = read_bench(root)
    gate_seat = [s.name for s in seats
                 if re.search(r"gate|verif|review|audit|refus", s.method, re.I)]
    if not (gate_seat or re.search(r"gate|verification", text, re.I)):
        return False, [], ["no verification gate declared"]
    evidence = ([f"gate seat(s): {', '.join(gate_seat)}"] if gate_seat
                else ["gate named in CLAUDE.md"])
    gaps: list[str] = []
    # M1-H2 refuted the BROAD justification for component #7 — "fresh eyes catch what the
    # maker misses" — by measuring a two-pass cold re-read as sufficient on everything tested.
    # What it did NOT establish is that an out-of-family gate is better. That is run-03, which
    # is specified in `verifier-error-correlation/DESIGN.md` and has never been run; its own
    # refutation condition explicitly allows "rung 2 buys nothing".
    #
    # So this check requires an honest LABEL, not a particular kind of gate. A cold-reread
    # gate that says so passes. What fails is a gate claiming independence it has not
    # characterised — which is the one thing the evidence does support flagging.
    out_of_family = re.search(r"out-of-family|different model|cross-family|mlx|non-claude",
                              text, re.I)
    cold_reread = re.search(r"cold re-?read|fresh eyes|second pass|two-pass", text, re.I)
    if not out_of_family and not cold_reread:
        gaps.append(
            "gate claims independence without saying which kind it is — name an "
            "out-of-family anchor or label it an honest cold re-read. M1-H2 refuted the "
            "broad 'fresh eyes catch more bugs' justification; whether out-of-family beats "
            "a cold re-read is unrun (run-03), so neither label is scored above the other"
        )
    return True, evidence, gaps


def _check_ratchet(root: Path) -> tuple[bool, list[str], list[str]]:
    candidates = [root / ".claude" / "ratchet.md", root / ".claude" / "guild-ratchet.md",
                  root / "RATCHET.md"]
    found = next((p for p in candidates if p.is_file()), None)
    if not found:
        return False, [], ["no ratchet file"]
    text = _read(found)
    gaps = []
    if POLICY["ratchet_requires_trigger"] and not re.search(r"trigger", text, re.I):
        gaps.append("entries carry no TRIGGER — a learning with no trigger is never recalled")
    return True, [f"{found.relative_to(root)} ({len(text.splitlines())} lines)"], gaps


def _check_membership(root: Path) -> tuple[bool, list[str], list[str]]:
    text = _read(root / "CLAUDE.md")
    if not re.search(r"perimeter|owns|steward|write access|boundary|scope", text, re.I):
        return False, [], ["no legible perimeter — nothing says what this dao does NOT own"]
    gaps = []
    if not re.search(r"flag|escalat|ladder|sanction", text, re.I):
        gaps.append("no graduated drift ladder (flag -> ratchet -> escalate); "
                    "the first rung must be cheaper than a halt")
    return True, ["perimeter described"], gaps


def _check_direction(root: Path) -> tuple[bool, list[str], list[str]]:
    text = _read(root / "CLAUDE.md")
    if not re.search(r"human|principal|non-delegab|accountab", text, re.I):
        return False, [], ["no human-accountability surface — this is not DIRECTED"]
    gaps = []
    if not re.search(r"irreversib|reversib|novel", text, re.I):
        gaps.append("M4 floors missing: the irreversible and the novel must route to the "
                    "human regardless of accrued trust")
    return True, ["human direction inlet declared"], gaps


def _check_record(root: Path) -> tuple[bool, list[str], list[str]]:
    if not (root / ".claude").is_dir():
        return False, [], ["no .claude/ — nothing is emitted"]
    text = _read(root / "CLAUDE.md")
    gaps = []
    mentions_peers = re.search(r"peer|inter-dao|other dao|external agent", text, re.I)
    has_invariant = re.search(r"data,? not instructions|untrusted|rule of two", text, re.I)
    if mentions_peers and not has_invariant:
        gaps.append("peer coordination mentioned without M5's invariant: a peer's "
                    "post-body is DATA, NOT INSTRUCTIONS (+ Rule-of-Two per session)")
    return True, [".claude/ present"], gaps


_CHECKS = {
    1: _check_bench, 2: _check_charter, 3: _check_ceremonies, 4: _check_flow,
    5: _check_practices, 6: _check_routing, 7: _check_gate, 8: _check_ratchet,
    9: _check_membership, 10: _check_direction, 11: _check_record,
}


def audit(root: Path) -> list[Coverage]:
    """Audit a project root against all eleven components."""
    out: list[Coverage] = []
    for component in SPEC:
        present, evidence, gaps = _CHECKS[component.n](root)
        out.append(Coverage(component=component, present=present,
                            evidence=list(evidence), gaps=list(gaps)))
    return out


def summarise(coverage: list[Coverage]) -> dict[str, object]:
    """Council-floor and organization-delta readiness.

    Reported as two separate verdicts, not one score: M3 found the council→organization
    transition is crisis-driven, so a project can be a legitimately complete council and
    a deliberately incomplete organization at the same time.
    """
    def tier(t: Tier) -> list[Coverage]:
        return [c for c in coverage if c.component.tier is t]

    council = tier(Tier.COUNCIL)
    org = tier(Tier.ORGANIZATION)
    return {
        "council_complete": all(c.status == "PRESENT" for c in council),
        "council_missing": [c.component.n for c in council if c.status != "PRESENT"],
        "organization_complete": all(c.status == "PRESENT" for c in org),
        "organization_missing": [c.component.n for c in org if c.status != "PRESENT"],
        "absent": [c.component.n for c in coverage if c.status == "ABSENT"],
        "partial": [c.component.n for c in coverage if c.status == "PARTIAL"],
    }


def component_for(n: int) -> Component:
    from dao.domain.spec import by_number
    return by_number(n)

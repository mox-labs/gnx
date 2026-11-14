"""Audit a project against the dao spec.

`dao check` answers one question: which of the six documents does this project have, and
where is one present in name but not in substance?

It is a check of form. It reads structure (sections, fields, table rows), whether the
documents agree with each other (a participant's position exists, a checker is a different
position, a named file is on disk), three things the research says a document must
address. It does not judge
meaning: a well-formed constitution that says nothing useful passes. That judgment is a
reader's, and the `dao` skill's review asks for it.

Reports PRESENT / PARTIAL / ABSENT per element, never a score: one number would let a
strong constitution average away a missing check on the work.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from dao.application.organization import Organization, file_of
from dao.application.policy import POLICY, Policy
from dao.application.reading import find_section, list_items, norm, sections
from dao.domain.models import Kind
from dao.domain.spec import SPEC, Coverage, Key, Status

__all__ = ["POLICY", "Policy", "audit", "complete"]


Result = tuple[bool, list[str], list[str]]


def _absent(org: Organization, key: Key) -> Result:
    where = f"{org.directory}/{file_of(key)}"
    refused = org.refused(key)
    return False, [], [f"{where} {refused}, so it is not read" if refused else f"no {where}"]


def _need_section(parts: dict[str, str], pattern: str, called: str, gaps: list[str]) -> str | None:
    """The body of a required section, or None with the gap recorded."""
    found = find_section(parts, pattern)
    if found is None:
        gaps.append(f"no {called} section")
        return None
    heading, body = found
    if len(body) < POLICY.section_min_chars:
        gaps.append(f"the {heading!r} section is empty or a single line")
        return None
    return body


# --- per-element checks ------------------------------------------------------------
# Each returns (present, evidence, gaps). A gap means "the file is there and its test does
# not pass", which is the finding worth surfacing.


def _check_constitution(org: Organization) -> Result:
    if not org.has(Key.CONSTITUTION):
        return _absent(org, Key.CONSTITUTION)
    text = org.text(Key.CONSTITUTION)
    parts = sections(text)
    gaps: list[str] = []
    _need_section(parts, r"purpose", "Purpose", gaps)
    _need_section(parts, r"commitment|non-negotiable", "Commitments", gaps)
    amendment = _need_section(parts, r"amend|chang", "Amendment", gaps)
    if amendment is not None and org.positions:
        named = [p.name for p in org.positions if p.name in norm(amendment)]
        if not named:
            gaps.append(
                "the Amendment section names no position: nothing says who may change this file"
            )
        elif (
            not any(h.kind is Kind.HUMAN for name in named for h in org.holders(name))
            and org.participants is not None
        ):
            gaps.append(
                "no human holds a position that may amend the constitution: " + ", ".join(named)
            )
    return True, [f"{len(text.split())} words, {len(parts)} section(s)"], gaps


def _check_policies(org: Organization) -> Result:
    if not org.has(Key.POLICIES):
        return _absent(org, Key.POLICIES)
    text = org.text(Key.POLICIES)
    parts = sections(text)
    gaps: list[str] = []
    decisions = _need_section(parts, r"decision|decid", "Decisions", gaps)
    if decisions is not None and not re.search(
        r"irreversib|cannot be undone|can't be undone|novel|first time", decisions, re.I
    ):
        gaps.append(
            "the Decisions section does not say where an irreversible or a novel decision "
            "goes (M4: those go to a human, whatever trust has accrued)"
        )
    verification = _need_section(parts, r"verif|review|check", "Verification", gaps)
    if verification is not None and not re.search(
        r"out-of-family|different (model )?famil|another (model )?famil|cross-family|"
        r"same (model )?famil|cold re-?read|second pass|two-pass|a person|a human|by hand",
        verification,
        re.I,
    ):
        gaps.append(
            "the Verification section does not say what kind of check it is: a different "
            "model family, the same family reading cold, or a person (M1-H2)"
        )
    _need_section(parts, r"chang|amend", "Changing the rules", gaps)
    return True, [f"{len(text.split())} words, {len(parts)} section(s)"], gaps


def _check_principles(org: Organization) -> Result:
    if not org.has(Key.PRINCIPLES):
        return _absent(org, Key.PRINCIPLES)
    text = org.text(Key.PRINCIPLES)
    count = max(len(sections(text)), list_items(text))
    gaps: list[str] = []
    floor = POLICY.principles_min
    if count < floor:
        gaps.append(f"{count} principle(s) found as list items or sections; expected >= {floor}")
    return True, [f"{count} principle(s)"], gaps


def _check_positions(org: Organization) -> Result:
    if not org.has(Key.POSITIONS):
        return _absent(org, Key.POSITIONS)
    positions = org.positions
    if not positions:
        return True, [], ["no positions: each position is a '## name' section"]
    names = [p.name for p in positions]
    evidence = [f"{len(positions)} position(s): {', '.join(names)}"]
    gaps: list[str] = []

    floor = POLICY.positions_min
    if len(positions) < floor:
        gaps.append(
            f"only {len(positions)} position(s); it takes >= {floor} for one to check another"
        )
    repeated = sorted(n for n, k in Counter(names).items() if k > 1)
    if repeated:
        gaps.append("defined more than once: " + ", ".join(repeated))

    def lacking(field: str) -> list[str]:
        return [p.name for p in positions if not getattr(p, field)]

    if lacking("does"):
        gaps.append("no 'Does:' field: " + ", ".join(lacking("does")))
    if lacking("must_not"):
        gaps.append("no 'Must not:' field: " + ", ".join(lacking("must_not")))

    remit_floor = POLICY.position_remit_min_chars
    thin = [p.name for p in positions if p.does and p.remit_is_thin(remit_floor)]
    if thin:
        gaps.append(
            f"remit under {remit_floor} characters, closer to a label than a method (M1): "
            + ", ".join(thin)
        )

    for p in positions:
        unknown = [c for c in p.checked_by if c not in names]
        if unknown:
            gaps.append(f"{p.name} is checked by a position that does not exist: {unknown[0]}")
        others = [c for c in p.checked_by if c in names and c != p.name]
        if others:
            continue
        holders = org.holders(p.name)
        only_humans = bool(holders) and all(h.kind is Kind.HUMAN for h in holders)
        if only_humans:
            continue  # a person's own accountability is where the chain ends
        if p.name in p.checked_by:
            gaps.append(f"{p.name} checks its own work")
        elif not unknown:
            gaps.append(f"{p.name} has no 'Checked by:' naming another position")
    return True, evidence, gaps


def _check_participants(org: Organization) -> Result:
    if not org.has(Key.PARTICIPANTS):
        return _absent(org, Key.PARTICIPANTS)
    participants = org.participants
    if participants is None:
        return True, [], ["no table with a Participant column and a Position column"]
    if not participants:
        return True, [], ["the participants table has no rows"]

    humans = [p.name for p in participants if p.kind is Kind.HUMAN]
    agents = [p.name for p in participants if p.kind is Kind.AGENT]
    evidence = [f"{len(humans)} human, {len(agents)} agent"]
    gaps: list[str] = []

    repeated = sorted(n for n, k in Counter(p.name for p in participants).items() if k > 1)
    if repeated:
        gaps.append("listed more than once: " + ", ".join(repeated))
    unkinded = [p.name for p in participants if p.kind is None]
    if unkinded:
        gaps.append("Kind is not 'human' or 'agent': " + ", ".join(unkinded))
    if not humans:
        gaps.append("no human participant: nobody is accountable, so this is not directed (M4)")

    positions = {p.name for p in org.positions}
    for p in participants:
        if not p.positions:
            gaps.append(f"{p.name} holds no position")
        for held in p.positions:
            if positions and held not in positions:
                gaps.append(f"{p.name} holds a position that does not exist: {held}")
        if p.defined_in:
            try:
                target = (org.root / p.defined_in).resolve()
                outside = not target.is_relative_to(org.root.resolve())
                missing = not outside and not target.is_file()
            except (OSError, ValueError):
                gaps.append(f"{p.name} is defined in a path that cannot be read")
                continue
            if outside:
                gaps.append(f"{p.name} is defined in a path outside the project")
            elif missing:
                gaps.append(f"{p.name} is defined in a file that does not exist: {p.defined_in}")
    vacant = sorted(n for n in positions if not org.holders(n))
    if vacant:
        gaps.append("position(s) nobody holds: " + ", ".join(vacant))
    return True, evidence, gaps


def _check_front_door(org: Organization) -> Result:
    if not org.has(Key.FRONT_DOOR):
        return _absent(org, Key.FRONT_DOOR)
    text = org.text(Key.FRONT_DOOR)
    gaps: list[str] = []
    unlinked = [e.file for e in SPEC if e.key is not Key.FRONT_DOOR and e.file not in text]
    if unlinked:
        gaps.append("the README does not link: " + ", ".join(unlinked))

    names = POLICY.agent_instructions
    texts = {n: org.instructions(n) for n in names}
    found = [n for n in names if texts[n] is not None]
    pointer = re.compile(rf"(?<![\w-]){re.escape(org.directory)}/")
    pointing = [n for n in found if pointer.search(texts[n] or "")]
    if not found:
        gaps.append(f"no {' or '.join(names)} at the project root: no agent is pointed here")
    elif not pointing:
        gaps.append(
            f"{', '.join(found)} does not mention {org.directory}/: an agent starting a "
            "session never reads the organization"
        )
    evidence = [f"{org.directory}/README.md"] + [f"pointed at from {n}" for n in pointing]
    return True, evidence, gaps


_CHECKS: dict[Key, Callable[[Organization], Result]] = {
    Key.CONSTITUTION: _check_constitution,
    Key.POLICIES: _check_policies,
    Key.PRINCIPLES: _check_principles,
    Key.POSITIONS: _check_positions,
    Key.PARTICIPANTS: _check_participants,
    Key.FRONT_DOOR: _check_front_door,
}


def audit(root: Path, directory: str | None = None) -> list[Coverage]:
    """Audit a project root against the six elements.

    `directory` is where the organization's documents live, relative to the root; the
    declared policy value when not given. The book is not part of this: `application.book`
    adds it, because judging a page needs the page's renderer.
    """
    org = Organization(root, directory)
    out: list[Coverage] = []
    for element in SPEC:
        present, evidence, gaps = _CHECKS[element.key](org)
        out.append(Coverage(element=element, present=present, evidence=evidence, gaps=gaps))
    return out


def complete(coverage: list[Coverage]) -> bool:
    """Whether every element is PRESENT. The exit code of `dao check` is this, and nothing else."""
    return all(c.status is Status.PRESENT for c in coverage)

"""The audit must tell PRESENT from PARTIAL: a file is not an organization.

The fixture is the worked organization in the skill's `references/documents.md`, read from
that file, so the documented formats and the audit cannot drift apart. Each test then breaks
one thing and checks that the audit names it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from dao.application.audit import POLICY, audit, complete
from dao.domain.spec import SPEC, Coverage, Key, Status
from dao.skill_files import skill

if TYPE_CHECKING:
    from pathlib import Path

_EXAMPLE = re.compile(r"^## (\S+\.md)\n.*?^```markdown\n(.*?)^```", re.S | re.M)
POINTER = "This project is run as a dao. Read `dao/README.md` at the start of a session.\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """The documented example, written out as a project."""
    examples = dict(_EXAMPLE.findall(skill("documents")))
    assert set(examples) == {e.file for e in SPEC}, "documents.md must show all six"
    (tmp_path / "dao").mkdir()
    for name, body in examples.items():
        (tmp_path / "dao" / name).write_text(body, encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text(POINTER, encoding="utf-8")
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "cold-reader.md").write_text("---\nname: cold-reader\n---\nRead the diff.\n")
    return tmp_path


def _of(coverage: list[Coverage], key: Key) -> Coverage:
    return next(c for c in coverage if c.element.key is key)


def _edit(project: Path, name: str, old: str, new: str) -> None:
    path = project / "dao" / name
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} is not in {name}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def _gaps(project: Path, key: Key) -> str:
    c = _of(audit(project), key)
    assert c.status is Status.PARTIAL, c.gaps
    return " | ".join(c.gaps)


def test_the_documented_example_is_complete(project: Path) -> None:
    coverage = audit(project)
    assert {c.element.name: c.gaps for c in coverage if c.gaps} == {}
    assert complete(coverage)


def test_an_empty_project_has_nothing(tmp_path: Path) -> None:
    coverage = audit(tmp_path)
    assert [c.status for c in coverage] == [Status.ABSENT] * len(SPEC)
    assert not complete(coverage)


def test_one_partial_element_makes_the_whole_incomplete(project: Path) -> None:
    _edit(project, "principles.md", "- Stop and ask", "Stop and ask")
    _edit(project, "principles.md", "- One change", "One change")
    coverage = audit(project)
    assert _of(coverage, Key.PRINCIPLES).status is Status.PARTIAL
    assert not complete(coverage)


def test_documents_can_live_in_another_directory(project: Path) -> None:
    (project / "dao").rename(project / "governance")
    (project / "CLAUDE.md").write_text("Read `governance/README.md` first.\n")
    assert not complete(audit(project))
    assert complete(audit(project, "governance"))


# --- constitution ------------------------------------------------------------------


def test_constitution_without_an_amendment_section_is_partial(project: Path) -> None:
    _edit(project, "constitution.md", "## Amendment", "### Amendment")
    assert "no Amendment section" in _gaps(project, Key.CONSTITUTION)


def test_amendment_must_name_a_position(project: Path) -> None:
    _edit(project, "constitution.md", "Only the owner changes", "Only the right person changes")
    assert "names no position" in _gaps(project, Key.CONSTITUTION)


def test_amendment_must_rest_with_a_human(project: Path) -> None:
    _edit(project, "participants.md", "| ada | human | owner | |", "| ada | agent | owner | |")
    assert "no human holds a position that may amend" in _gaps(project, Key.CONSTITUTION)


def test_a_heading_with_nothing_under_it_is_not_a_section(project: Path) -> None:
    path = project / "dao" / "constitution.md"
    text = path.read_text()
    start = text.index("## Purpose")
    end = text.index("## Commitments")
    path.write_text(text[:start] + "## Purpose\n\nTides.\n\n" + text[end:])
    assert "'Purpose' section is empty" in _gaps(project, Key.CONSTITUTION)


# --- governing policies ------------------------------------------------------------


def test_decisions_must_route_the_irreversible_and_the_novel(project: Path) -> None:
    _edit(project, "policies.md", "Anything irreversible (a release", "A big thing (a release")
    _edit(project, "policies.md", "for the first time", "at night")
    assert "irreversible or a novel decision" in _gaps(project, Key.POLICIES)


def test_verification_must_say_what_kind_of_check_it_is(project: Path) -> None:
    path = project / "dao" / "policies.md"
    text = path.read_text()
    start = text.index("## Verification")
    end = text.index("## Changing the rules")
    vague = "## Verification\n\nEvery change is independently reviewed before it merges.\n\n"
    path.write_text(text[:start] + vague + text[end:])
    assert "what kind of check" in _gaps(project, Key.POLICIES)


def test_policies_need_a_way_to_change_the_rules(project: Path) -> None:
    _edit(project, "policies.md", "## Changing the rules", "## Later")
    assert "no Changing the rules section" in _gaps(project, Key.POLICIES)


# --- positions ---------------------------------------------------------------------


def test_a_label_is_not_a_remit(project: Path) -> None:
    path = project / "dao" / "positions.md"
    text = path.read_text()
    start = text.index("## reviewer")
    thin = "## reviewer\n\nDoes: Reviews code.\n\nMust not: Merge.\n\nChecked by: owner\n"
    path.write_text(text[:start] + thin)
    gaps = _gaps(project, Key.POSITIONS)
    assert f"remit under {POLICY.position_remit_min_chars} characters" in gaps
    assert "reviewer" in gaps


def test_a_position_cannot_check_its_own_work(project: Path) -> None:
    _edit(project, "positions.md", "Checked by: reviewer", "Checked by: builder")
    assert "builder checks its own work" in _gaps(project, Key.POSITIONS)


def test_a_checker_must_be_a_position_that_exists(project: Path) -> None:
    _edit(project, "positions.md", "Checked by: reviewer", "Checked by: auditor")
    assert "does not exist: auditor" in _gaps(project, Key.POSITIONS)


def test_an_agent_held_position_needs_a_checker(project: Path) -> None:
    _edit(project, "positions.md", "Checked by: reviewer", "")
    assert "builder has no 'Checked by:'" in _gaps(project, Key.POSITIONS)


def test_a_position_only_people_hold_may_end_the_chain(project: Path) -> None:
    """The example's owner has no checker, and a person holds it: that is not a gap."""
    assert _of(audit(project), Key.POSITIONS).status is Status.PRESENT
    _edit(project, "participants.md", "| ada | human | owner | |", "| ada | agent | owner | |")
    assert "owner has no 'Checked by:'" in _gaps(project, Key.POSITIONS)


def test_a_position_needs_does_and_must_not(project: Path) -> None:
    _edit(project, "positions.md", "Must not: Merge anything.", "It will not merge anything.")
    assert "no 'Must not:' field: builder" in _gaps(project, Key.POSITIONS)


def test_one_position_is_not_an_organization(project: Path) -> None:
    path = project / "dao" / "positions.md"
    text = path.read_text()
    path.write_text(text[: text.index("## builder")])
    assert f">= {POLICY.positions_min}" in _gaps(project, Key.POSITIONS)


def test_fields_may_be_bold_list_items(project: Path) -> None:
    _edit(project, "positions.md", "Checked by: reviewer", "- **Checked by:** `reviewer`")
    assert _of(audit(project), Key.POSITIONS).status is Status.PRESENT


# --- participants ------------------------------------------------------------------


def test_an_organization_with_no_human_is_not_directed(project: Path) -> None:
    _edit(project, "participants.md", "| ada | human |", "| ada | agent |")
    assert "no human participant" in _gaps(project, Key.PARTICIPANTS)


def test_a_participant_in_a_position_that_does_not_exist(project: Path) -> None:
    _edit(project, "participants.md", "| agent | builder |", "| agent | wizard |")
    gaps = _gaps(project, Key.PARTICIPANTS)
    assert "does not exist: wizard" in gaps
    assert "nobody holds: builder" in gaps


def test_a_defined_in_file_must_exist(project: Path) -> None:
    (project / ".claude" / "agents" / "cold-reader.md").unlink()
    assert "file that does not exist" in _gaps(project, Key.PARTICIPANTS)


def test_a_defined_in_path_may_not_leave_the_project(project: Path) -> None:
    _edit(project, "participants.md", ".claude/agents/cold-reader.md", "../../etc/hosts")
    assert "outside the project" in _gaps(project, Key.PARTICIPANTS)


def test_a_kind_that_is_neither_is_reported_not_guessed(project: Path) -> None:
    _edit(project, "participants.md", "| cold-reader | agent |", "| cold-reader | robot |")
    assert "Kind is not 'human' or 'agent': cold-reader" in _gaps(project, Key.PARTICIPANTS)


def test_participants_without_a_table_is_partial(project: Path) -> None:
    (project / "dao" / "participants.md").write_text("# Participants\n\nAda and two agents.\n")
    assert "no table" in _gaps(project, Key.PARTICIPANTS)


# --- front door --------------------------------------------------------------------


def test_the_readme_must_link_every_document(project: Path) -> None:
    _edit(project, "README.md", "- [principles.md](principles.md)", "- the principles")
    assert "does not link: principles.md" in _gaps(project, Key.FRONT_DOOR)


def test_agents_must_be_pointed_at_the_organization(project: Path) -> None:
    (project / "CLAUDE.md").write_text("Be careful.\n")
    assert "never reads the organization" in _gaps(project, Key.FRONT_DOOR)
    (project / "CLAUDE.md").unlink()
    assert "no agent is pointed here" in _gaps(project, Key.FRONT_DOOR)


def test_agents_md_is_an_agent_instruction_file_too(project: Path) -> None:
    (project / "CLAUDE.md").rename(project / "AGENTS.md")
    assert _of(audit(project), Key.FRONT_DOOR).status is Status.PRESENT


# --- the spec ----------------------------------------------------------------------


def test_every_element_says_what_it_must_contain_and_has_a_check() -> None:
    from dao.application.audit import _CHECKS

    assert {e.key for e in SPEC} == set(_CHECKS) == set(Key)
    assert all(e.must_contain for e in SPEC)
    assert len({e.file for e in SPEC}) == len(SPEC)

"""The reading grammar: what counts as a section, a field and a participants row."""

from __future__ import annotations

from dao.application.reading import (
    list_items,
    read_participants,
    read_positions,
    sections,
)
from dao.domain.models import Kind


def test_a_heading_inside_a_code_fence_is_not_a_section() -> None:
    text = "## One\n\nbody\n\n```\n## Not this\n```\n\n## Two\n\nmore\n"
    assert list(sections(text)) == ["One", "Two"]
    assert "## Not this" in sections(text)["One"]


def test_a_field_runs_until_the_next_field() -> None:
    text = "## Builder\n\nDoes: writes\nchanges.\n\nMust not: merge.\nChecked by: A, `B` and c\n"
    (p,) = read_positions(text)
    assert p.name == "builder"
    assert p.does == "writes changes."
    assert p.must_not == "merge."
    assert p.checked_by == ("a", "b", "c")


def test_checked_by_nobody_is_empty() -> None:
    (p,) = read_positions("## owner\n\nDoes: x\nMust not: y\nChecked by: none\n")
    assert p.checked_by == ()


def test_participants_reads_the_table_by_its_header() -> None:
    text = (
        "Intro.\n\n"
        "| Position | Participant | Kind | Defined in |\n"
        "|---|---|---|---|\n"
        "| owner, reviewer | `Ada` | Human | |\n"
        "| builder | bot | agent | `.claude/agents/bot.md` |\n"
        "\nAfter.\n\n| Participant | Position |\n|---|---|\n| late | owner |\n"
    )
    people = read_participants(text)
    assert people is not None
    assert [(p.name, p.kind, p.positions, p.defined_in) for p in people] == [
        ("Ada", Kind.HUMAN, ("owner", "reviewer"), ""),
        ("bot", Kind.AGENT, ("builder",), ".claude/agents/bot.md"),
    ]


def test_no_participants_table_is_none_and_an_empty_one_is_empty() -> None:
    assert read_participants("Ada and a bot.\n") is None
    assert read_participants("| Participant | Position |\n|---|---|\n") == []


def test_list_items_ignores_code() -> None:
    assert list_items("- a\n* b\n1. c\n```\n- not\n```\nplain\n") == 3

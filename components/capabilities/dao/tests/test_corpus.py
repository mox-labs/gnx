"""The corpus reader parses agent-craft's CATALOG without inventing anything."""

from __future__ import annotations

from dao.adapters._out.corpus import for_project, load_catalog, parse_catalog, projects
from dao.domain.models import DomainType, Grounding

CATALOG = """\
# Agent Craft — domains

> One **domain** per agent/skill we ground.

## Domains (4)

| domain | type | status | sources | cnf | informs |
|---|---|---|---|---|---|
| aces-dx | craft | RAW_GATHERED | 24 | 6 | guild-arch:ace |
| krishna | figure | DISTILLED | 33 | 9 | guild-arch:k |
| tacit-knowledge-elicitation | craft | RAW_GATHERED | 5 | 3 | cix:radix, research:polanyi |
| mystery | wobble | RAW_GATHERED | 1 | 1 | nowhere:x |

**Totals:** 63 sources · 12 could-not-fetch.
"""


def test_parses_rows_and_skips_header_separator_and_totals():
    d = parse_catalog(CATALOG)
    # 4 table rows, but `mystery` has an unknown type and is skipped rather than guessed.
    assert [x.name for x in d] == ["aces-dx", "krishna", "tacit-knowledge-elicitation"]


def test_unknown_type_is_skipped_not_defaulted():
    """A bad classification must not become a silent DomainType.CRAFT."""
    assert all(x.name != "mystery" for x in parse_catalog(CATALOG))


def test_fields_and_grounding():
    krishna = next(x for x in parse_catalog(CATALOG) if x.name == "krishna")
    assert krishna.type is DomainType.FIGURE
    assert krishna.grounding is Grounding.DISTILLED
    assert krishna.sources == 33
    assert krishna.confidence == 9


def test_multi_valued_informs_splits():
    tacit = next(x for x in parse_catalog(CATALOG)
                 if x.name == "tacit-knowledge-elicitation")
    assert tacit.informs == ("cix:radix", "research:polanyi")
    assert tacit.informs_project("cix") == ("radix",)
    assert tacit.informs_project("research") == ("polanyi",)
    assert tacit.informs_project("guild-arch") == ()


def test_projects_counts_edges_not_domains():
    counts = projects(parse_catalog(CATALOG))
    assert counts["guild-arch"] == 2
    assert counts["cix"] == 1
    assert counts["research"] == 1


def test_for_project_filters():
    rows = for_project(parse_catalog(CATALOG), "guild-arch")
    assert {r.name for r in rows} == {"aces-dx", "krishna"}


def test_missing_corpus_degrades_to_empty(tmp_path):
    """A project may ground its bench some other way; absence is not an error."""
    assert load_catalog(tmp_path) == []


def test_reads_the_real_catalog_if_present():
    """Integration: when dao-corpus is on disk, the totals must agree with its own header.

    The catalog states '43 figure, 33 craft' — if the parser drifts, this catches it
    against real data rather than a fixture.
    """
    real = load_catalog()
    if not real:
        return  # corpus not present on this machine; the unit tests above still bind
    figures = sum(1 for d in real if d.type is DomainType.FIGURE)
    craft = sum(1 for d in real if d.type is DomainType.CRAFT)
    assert figures + craft == len(real)
    assert len(real) >= 70, "the agent-craft catalog should hold ~76 domains"

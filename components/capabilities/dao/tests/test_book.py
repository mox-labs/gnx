"""The book: a copy of the documents that says only what they say, and cannot act.

Three properties are held here. The book derives the cross-references a reader needs. The page
is inert: nothing a document contains runs, and the page loads nothing. And `dao check` knows
when the page has fallen behind the documents.
"""

from __future__ import annotations

import json
from importlib import metadata
from typing import TYPE_CHECKING

from click.testing import CliRunner

from dao.adapters._in.cli import main
from dao.adapters._out.html import render, slug, write
from dao.application.audit import POLICY, audit, complete
from dao.application.book import UNTITLED, build, chains, check
from dao.application.organization import Organization, rendered_from
from dao.application.reading import read_participants, read_positions, sections
from dao.domain.models import Position
from dao.domain.spec import SPEC, Coverage, Key, Status

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _page(project: Path) -> str:
    return render(build(project), version="test")


def _append(project: Path, name: str, text: str) -> None:
    path = project / "dao" / name
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def _checked(project: Path) -> list[Coverage]:
    """What `dao check` computes: the audit, with the book held to the page dao writes."""
    return check(project, render=lambda book: render(book, version=metadata.version("dao")))


def _front_door_gaps(project: Path) -> list[str]:
    return next(c for c in _checked(project) if c.element.key is Key.FRONT_DOOR).gaps


def _gaps_of(project: Path, key: Key) -> str:
    return " | ".join(next(c for c in audit(project) if c.element.key is key).gaps)


# --- what the book works out -------------------------------------------------------


def test_the_book_cross_references_positions_and_participants(project: Path) -> None:
    book = build(project)
    assert book.title == "How tidepool is run"
    assert book.complete
    assert [d.key for d in book.documents] == [e.key for e in SPEC]

    by_name = {p.name: p for p in book.positions}
    assert by_name["builder"].held_by == ("the session",)
    assert by_name["builder"].checked_by == ("reviewer",)
    assert by_name["reviewer"].checks == ("builder",)
    assert by_name["owner"].held_only_by_people
    assert not by_name["reviewer"].held_only_by_people
    assert book.chains == (("builder", "reviewer", "owner"),)


def test_a_document_keeps_its_text_and_loses_only_its_title_line(project: Path) -> None:
    constitution = build(project).document(Key.CONSTITUTION)
    assert constitution.markdown.startswith("## Purpose")
    assert "A released version never changes." in constitution.markdown


def test_a_readme_without_a_title_gets_a_plain_one(project: Path) -> None:
    readme = project / "dao" / "README.md"
    readme.write_text(readme.read_text().replace("# How tidepool is run\n", ""))
    assert build(project).title == UNTITLED


def test_chains_drop_tails_and_do_not_walk_a_loop_twice() -> None:
    def position(name: str, *checked_by: str) -> Position:
        return Position(name=name, checked_by=checked_by)

    line = [position("a", "b"), position("b", "c"), position("c")]
    assert chains(line) == (("a", "b", "c"),)

    two_into_one = [position("a", "c"), position("b", "c"), position("c")]
    assert chains(two_into_one) == (("a", "c"), ("b", "c"))

    loop = [position("a", "b"), position("b", "a")]
    assert chains(loop) == (("a", "b"),)

    alone = [position("a"), position("b", "nobody-here")]
    assert chains(alone) == (("a",), ("b",))


def test_an_incomplete_organization_still_makes_a_book_that_says_so(project: Path) -> None:
    (project / "dao" / "principles.md").unlink()
    book = build(project)
    assert not book.complete
    assert book.document(Key.PRINCIPLES).status is Status.ABSENT
    page = render(book, version="test")
    assert "This organization is not complete" in page
    assert "no dao/principles.md" in page


# --- the page is inert --------------------------------------------------------------


def test_the_page_runs_nothing_and_loads_nothing(project: Path) -> None:
    page = _page(project)
    assert page.startswith("<!doctype html>")
    assert "default-src 'none'" in page
    assert "<script" not in page.lower()
    assert "http://" not in page and "https://" not in page
    assert "@import" not in page and "url(" not in page


def test_html_in_a_document_is_shown_not_run(project: Path) -> None:
    _append(project, "constitution.md", '\n<script>alert("x")</script>\n\n<img src=x onerror=y>\n')
    _append(project, "principles.md", "- [click](javascript:alert(1))\n")
    positions = project / "dao" / "positions.md"
    positions.write_text(positions.read_text().replace("Merge anything.", "<b onclick=z>Merge</b>"))
    page = _page(project)
    assert "<script" not in page.lower()
    assert "&lt;script&gt;" in page
    assert "<img" not in page
    assert "<b onclick" not in page
    assert 'href="javascript:' not in page


def test_a_hostile_name_cannot_leave_its_place_in_the_page(project: Path) -> None:
    participants = project / "dao" / "participants.md"
    participants.write_text(
        participants.read_text().replace("| ada |", '| ada"><script>x</script> |')
    )
    page = _page(project)
    assert "<script" not in page.lower()


def test_links_between_documents_stay_on_the_page(project: Path) -> None:
    _append(project, "principles.md", "- See [the site](https://example.org/a).\n")
    page = _page(project)
    assert '<a href="#constitution">constitution.md</a>' in page
    assert '<a href="https://example.org/a" rel="noopener noreferrer">' in page


def test_section_headings_sit_under_the_page_heading(project: Path) -> None:
    page = _page(project)
    assert "<h3>Purpose</h3>" in page
    assert "<h2>Purpose</h2>" not in page


def test_the_same_documents_make_the_same_page(project: Path) -> None:
    assert _page(project) == _page(project)


def test_slug() -> None:
    assert slug("The Session") == "the-session"
    assert slug("***") == "unnamed"


def test_only_some_addresses_become_links_or_images(project: Path) -> None:
    _append(
        project,
        "principles.md",
        "- [share](//evil.example/share/x) [root](/etc/passwd) [smb](smb://evil.example/x)\n"
        "- [beside](../CONTRIBUTING.md) [mail](mailto:ada@example.org)\n"
        "- ![remote](//evil.example/p.png) ![rooted](/etc/hosts) ![logo](logo.png)\n",
    )
    page = _page(project)
    assert "evil.example" not in page
    assert "/etc/passwd" not in page and "/etc/hosts" not in page
    assert "share" in page and "smb" in page and "remote" in page
    assert '<a href="../CONTRIBUTING.md">beside</a>' in page
    assert '<a href="mailto:ada@example.org" rel="noopener noreferrer">mail</a>' in page
    assert '<img src="logo.png" alt="logo"' in page


# --- what dao reads from a project ---------------------------------------------------


def test_a_document_that_is_a_link_is_not_read(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A repository can carry a link to any file its reader can open. dao does not follow it."""
    outside = tmp_path_factory.mktemp("outside") / "credentials"
    outside.write_text("- aws_secret_access_key = FAKE\n- two\n- three\n")
    principles = project / "dao" / "principles.md"
    principles.unlink()
    principles.symlink_to(outside)

    assert "principles.md is a symbolic link, so it is not read" in _gaps_of(
        project, Key.PRINCIPLES
    )
    assert "FAKE" not in _page(project)
    result = CliRunner().invoke(main, ["book", str(project), "--json"])
    assert "FAKE" not in result.output


def test_a_device_or_an_oversized_file_is_not_read(project: Path) -> None:
    positions = project / "dao" / "positions.md"
    positions.unlink()
    positions.symlink_to("/dev/zero")
    assert "is a symbolic link" in _gaps_of(project, Key.POSITIONS)
    build(project)

    positions.unlink()
    positions.write_bytes(b"x" * (POLICY.document_max_bytes + 1))
    assert f"is larger than {POLICY.document_max_bytes} bytes" in _gaps_of(project, Key.POSITIONS)


def test_documents_in_a_directory_linked_from_outside_are_not_read(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside")
    (project / "dao").rename(outside / "dao")
    (project / "dao").symlink_to(outside / "dao")
    coverage = audit(project)
    assert [c.status for c in coverage] == [Status.ABSENT] * len(SPEC)
    assert "is outside the project" in coverage[0].gaps[0]
    result = CliRunner().invoke(main, ["book", str(project), "--json"])
    assert "tidepool" not in result.output


def test_a_defined_in_path_the_system_rejects_is_a_gap_not_a_crash(project: Path) -> None:
    participants = project / "dao" / "participants.md"
    participants.write_text(
        participants.read_text().replace(".claude/agents/cold-reader.md", "x" * 5000)
    )
    assert "cannot be read" in _gaps_of(project, Key.PARTICIPANTS)


def test_no_line_can_make_reading_slow() -> None:
    """Each of these took seconds to minutes before the patterns were made linear.

    The suite's per-test timeout is the assertion: all of this runs in well under a second.
    """
    gap = " " * 200_000
    sections(f"## a{gap}b\n\ntext\n")
    read_positions(f"## p\n\n{gap}\n- {gap}x\nDoes:{gap}x\n")
    read_participants(f"| Participant | Position |\n{gap}|---|---|\n|{gap}-{gap}|\n")
    line = [Position(name=str(i), checked_by=(str(i + 1),)) for i in range(20_000)]
    assert len(chains(line)) == 1


def test_a_heading_keeps_its_text_and_loses_closing_hashes() -> None:
    assert list(sections("## Purpose ##\n\nx\n## C# and F#\n\ny\n")) == ["Purpose", "C# and F#"]


# --- the page and the documents stay together --------------------------------------


def test_a_fresh_book_is_current_and_an_edit_makes_it_stale(project: Path) -> None:
    assert CliRunner().invoke(main, ["book", str(project)]).exit_code == 0
    org = Organization(project)
    assert org.book == project / "dao" / POLICY.book
    assert rendered_from(org.book.read_text()) == org.sources()
    assert complete(_checked(project))
    assert CliRunner().invoke(main, ["check", str(project)]).exit_code == 0

    _append(project, "principles.md", "- Write the date in ISO 8601.\n")
    assert "rendered from older documents" in " ".join(_front_door_gaps(project))

    assert CliRunner().invoke(main, ["book", str(project)]).exit_code == 0
    assert complete(_checked(project))
    assert "This organization is not complete" not in org.book.read_text()


def test_a_book_dao_did_not_write_is_reported(project: Path) -> None:
    (project / "dao" / POLICY.book).write_text("<html>by hand</html>")
    assert "is not the page `dao book` writes" in " ".join(_front_door_gaps(project))


def test_a_forged_book_with_the_right_digest_is_not_vouched_for(project: Path) -> None:
    """The digest can be copied onto any HTML, so the check renders again and compares."""
    digest = Organization(project).sources()
    forged = f'<!doctype html><meta name="dao-sources" content="{digest}"><script>x()</script>'
    (project / "dao" / POLICY.book).write_text(forged)
    assert "is not the page `dao book` writes" in " ".join(_front_door_gaps(project))
    assert CliRunner().invoke(main, ["check", str(project)]).exit_code == 1


def test_a_book_that_is_a_link_is_not_read(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "secret"
    outside.write_text("the secret")
    (project / "dao" / POLICY.book).symlink_to(outside)
    assert "is a symbolic link" in " ".join(_front_door_gaps(project))


def test_no_book_is_not_a_gap(project: Path) -> None:
    assert _front_door_gaps(project) == []


def test_a_missing_document_changes_the_digest(project: Path) -> None:
    before = Organization(project).sources()
    (project / "dao" / "principles.md").write_text("")
    emptied = Organization(project).sources()
    (project / "dao" / "principles.md").unlink()
    assert len({before, emptied, Organization(project).sources()}) == 3


# --- the command ---------------------------------------------------------------------


def test_book_json_prints_the_organization_and_writes_nothing(project: Path) -> None:
    result = CliRunner().invoke(main, ["book", str(project), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["title"] == "How tidepool is run"
    assert data["chains"] == [["builder", "reviewer", "owner"]]
    assert not (project / "dao" / POLICY.book).exists()


def test_book_needs_a_directory_of_documents(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["book", str(tmp_path)])
    assert result.exit_code == 1
    assert "no dao/" in result.output


def test_book_does_not_write_through_a_symlink(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "target.html"
    outside.write_text("untouched")
    (project / "dao" / POLICY.book).symlink_to(outside)
    result = CliRunner().invoke(main, ["book", str(project)])
    assert result.exit_code == 1
    assert outside.read_text() == "untouched"


def test_the_write_replaces_a_link_and_does_not_follow_it(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Whatever is planted at the target after the command's checks, the page stays put."""
    outside = tmp_path_factory.mktemp("outside") / "victim.txt"
    outside.write_text("untouched")
    target = project / "dao" / POLICY.book
    target.symlink_to(outside)
    write("<!doctype html>", target)
    assert outside.read_text() == "untouched"
    assert not target.is_symlink()
    assert target.read_text() == "<!doctype html>"
    assert [p.name for p in (project / "dao").iterdir() if p.name.endswith(".tmp")] == []


def test_book_does_not_write_outside_the_project(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    result = CliRunner().invoke(main, ["book", str(project), "--dir", f"../{elsewhere.name}"])
    assert result.exit_code == 1
    assert list(elsewhere.iterdir()) == []


def test_check_json_carries_every_verdict(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    result = CliRunner().invoke(main, ["check", str(project), "--json"])
    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report["complete"] is True
    assert [e["key"] for e in report["elements"]] == [e.key.value for e in SPEC]

    empty = tmp_path_factory.mktemp("empty")
    result = CliRunner().invoke(main, ["check", str(empty), "--json"])
    assert result.exit_code == 1
    report = json.loads(result.output)
    assert report["complete"] is False
    assert {e["status"] for e in report["elements"]} == {"ABSENT"}

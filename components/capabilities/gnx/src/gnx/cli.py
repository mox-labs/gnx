"""gnx CLI — load components, project them as Claude Code plugins.

`gnx list`  — show the registered components.
`gnx build` — project components/ → .claude-plugin/marketplace.json + plugins/.

Operates on the current working directory (the gnx repo): reads ./components, writes
./plugins + ./.claude-plugin. slick manifests are parsed as YAML directly (slickit's
Python binding is broken upstream; ground-truth §3).

Projection grain: components are per-unit (D21/D23), plugins are coarser. The regrouping
is declared in components/bundles.yaml, never inferred — see that file's header. plugins/
is generated output: committed, never hand-authored.
"""

from __future__ import annotations

import filecmp
import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click
import yaml
from rich.console import Console
from rich.table import Table

console = Console()


@dataclass
class Component:
    type_url: str
    kind: str
    source: str
    provides: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    relations: dict[str, list[str]] = field(default_factory=dict)
    dir: Path = field(default=Path("."))

    @property
    def slug(self) -> str:
        return self.dir.name


def _frontmatter(md: str) -> dict[str, object]:
    """Parse the YAML frontmatter of a payload (between the first pair of --- fences)."""
    m = re.match(r"^---\r?\n(.*?)\r?\n---", md, re.S)
    return (yaml.safe_load(m.group(1)) or {}) if m else {}


def _one_line(text: str) -> str:
    """Collapse a (possibly multi-line) description to one tight line."""
    return " ".join((text or "").split())


def _strip_dot_slash(p: str) -> str:
    """Remove a leading './' — once, as a prefix.

    Not `lstrip('./')`: lstrip strips a *character set*, so './.claude/x' loses the
    dot of the hidden directory and becomes 'claude/x'.
    """
    return p[2:] if p.startswith("./") else p.lstrip("/")


def kind_dir(kind: str) -> str:
    """The directory a component of this `kind` belongs in: components/<kind-plural>/<slug>/.

    GEP-0009 §1. The vocabulary is open (D25) — anything pluralises by the same rule, so a
    new kind needs no code change here. Only the -y → -ies irregular is special-cased.
    """
    k = kind.lower()
    return f"{k[:-1]}ies" if k.endswith("y") else f"{k}s"


def load_components(root: Path) -> list[Component]:
    """Load every components/<kind-plural>/<slug>/manifest.yaml."""
    comps: list[Component] = []
    cdir = root / "components"
    if not cdir.exists():
        return comps
    for kd in sorted(p for p in cdir.iterdir() if p.is_dir()):
        for d in sorted(p for p in kd.iterdir() if p.is_dir()):
            mf = d / "manifest.yaml"
            if not mf.exists():
                continue
            m = yaml.safe_load(mf.read_text()) or {}
            comps.append(
                Component(
                    type_url=m.get("type_url", d.name),
                    kind=m.get("kind", "Component"),
                    source=m.get("source", "./SKILL.md"),
                    provides=m.get("provides") or [],
                    requires=m.get("requires") or [],
                    relations=m.get("relations") or {},
                    dir=d,
                )
            )
    return comps


def check_layout(comps: list[Component]) -> list[str]:
    """Every component's declared `kind` must agree with the directory holding it.

    Without this the layout is convention, and convention drifts silently.
    """
    return [
        f"{c.dir.parent.name}/{c.slug}: kind '{c.kind}' belongs in components/{kind_dir(c.kind)}/"
        for c in comps
        if c.dir.parent.name != kind_dir(c.kind)
    ]


# --------------------------------------------------------------------------- projection


def load_bundles(root: Path) -> dict[str, Any]:
    path = root / "components" / "bundles.yaml"
    if not path.exists():
        raise click.ClickException(
            "components/bundles.yaml not found — it declares the plugin projections"
        )
    doc = yaml.safe_load(path.read_text()) or {}
    if not doc.get("plugins"):
        raise click.ClickException("components/bundles.yaml declares no plugins")
    return doc


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy a payload directory whole.

    Skills carry references/, examples/, scripts/ and assets/ — those files ARE the
    payload under progressive disclosure. Copying only SKILL.md ships a skill whose
    own text points at files that are not there.
    """
    shutil.copytree(src, dst, dirs_exist_ok=True)


def build_plugin(
    root: Path, spec: dict[str, Any], defaults: dict[str, Any], out: Path
) -> tuple[dict[str, Any], list[str]]:
    """Write one plugin projection into `out`. Returns (marketplace entry, problems)."""
    problems: list[str] = []
    name = spec.get("name")
    if not name:
        return {}, ["a plugin entry has no name"]

    pdir = out / name
    (pdir / ".claude-plugin").mkdir(parents=True, exist_ok=True)

    # --- agents: components/agents/<slug>/agent.md -> agents/<agent-name>.md
    for slug in spec.get("agents") or []:
        src = root / "components" / "agents" / slug / "agent.md"
        if not src.exists():
            problems.append(f"{name}: agent component {slug!r} has no agent.md")
            continue
        fm = _frontmatter(src.read_text())
        # The CC-facing filename follows the agent's own `name`, which is allowed to
        # differ from the component directory (claim-extraction ships `extract`).
        agent_name = fm.get("name") or slug
        (pdir / "agents").mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, pdir / "agents" / f"{agent_name}.md")

    # --- skills: components/skills/<slug>/ -> skills/<slug>/ (whole tree)
    for slug in spec.get("skills") or []:
        src = root / "components" / "skills" / slug
        if not (src / "SKILL.md").exists():
            problems.append(f"{name}: skill component {slug!r} has no SKILL.md")
            continue
        _copy_tree(src, pdir / "skills" / slug)
        # A manifest is catalog-side metadata, not part of the installed plugin.
        stale = pdir / "skills" / slug / "manifest.yaml"
        if stale.exists():
            stale.unlink()

    # --- external skills: a Capability's in-package semantic surface
    for ext in spec.get("external_skills") or []:
        src = root / ext["path"]
        if not (src / "SKILL.md").exists():
            problems.append(f"{name}: external skill path {ext['path']!r} has no SKILL.md")
            continue
        _copy_tree(src, pdir / "skills" / ext["name"])

    manifest = {
        "name": name,
        # Version comes from the declaration. Claude Code uses it as a cache key, so a
        # hardcoded constant means updates are never picked up.
        "version": spec.get("version", "0.0.0"),
        "description": _one_line(spec.get("description", "")),
        "author": spec.get("author", defaults.get("author")),
        "license": spec.get("license", defaults.get("license")),
        "keywords": spec.get("keywords") or [],
    }
    if spec.get("homepage"):
        manifest["homepage"] = spec["homepage"]
    if spec.get("repository"):
        manifest["repository"] = spec["repository"]
    manifest = {k: v for k, v in manifest.items() if v not in (None, [], "")}

    (pdir / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )

    entry = {
        "name": name,
        "source": f"./plugins/{name}",
        "description": manifest.get("description", ""),
        "version": manifest["version"],
    }
    if spec.get("category"):
        entry["category"] = spec["category"]
    if manifest.get("keywords"):
        entry["keywords"] = manifest["keywords"]
    if manifest.get("author"):
        entry["author"] = manifest["author"]
    if manifest.get("license"):
        entry["license"] = manifest["license"]
    return entry, problems


def _strip_code(md: str) -> str:
    """Blank out fenced blocks and inline code spans, preserving line count.

    Markdown inside code is a specimen, not a link: these payloads show
    `![Architecture](./diagrams/architecture.svg)` to teach the syntax, and quote
    `[Benchmark](url)` as an example of a *bad* citation. Reporting those as broken links
    is how a gate teaches people to ignore it.
    """
    out: list[str] = []
    fenced = False
    for line in md.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            fenced = not fenced
            out.append("")
            continue
        out.append("" if fenced else re.sub(r"`[^`]*`", lambda m: " " * len(m.group(0)), line))
    return "\n".join(out)


def check_links(plugin_dir: Path) -> list[str]:
    """Relative markdown links inside a built plugin must resolve inside that plugin.

    A bundle entry-point skill links to its method skills as ../<sibling>/SKILL.md. That
    is broken relative to components/skills/ but correct once both are projected into the
    same plugin — so this is the only place the question can be answered. It also catches
    the opposite failure: a link to a component that was left out of the bundle.

    Install copies only the plugin subdirectory, so a link resolving outside plugin_dir is
    dead on an installed machine even though it resolves in this repo.
    """
    problems: list[str] = []
    root = plugin_dir.resolve()
    link = re.compile(r"\[[^\]]*\]\(([^)\s#]+)(?:#[^)]*)?\)")
    for md in sorted(plugin_dir.rglob("*.md")):
        for m in link.finditer(_strip_code(md.read_text())):
            target = m.group(1)
            if re.match(r"^(?:[a-z][a-z0-9+.-]*:|//|#|\$\{|\$[A-Z])", target):
                continue  # absolute URL, anchor, or a variable-rooted path
            resolved = (md.parent / target).resolve()
            rel = md.relative_to(plugin_dir)
            if not resolved.exists():
                problems.append(f"{plugin_dir.name}: {rel} -> {target} does not resolve")
            elif root not in resolved.parents and resolved != root:
                problems.append(
                    f"{plugin_dir.name}: {rel} -> {target} escapes the plugin "
                    "(install copies only this plugin's subdirectory)"
                )
    return problems


def _tree_differs(a: Path, b: Path) -> list[str]:
    """Paths differing between two trees. Used by --check to detect real drift."""
    diffs: list[str] = []

    def walk(d: Path) -> set[str]:
        if not d.exists():
            return set()
        return {
            str(p.relative_to(d))
            for p in d.rglob("*")
            if p.is_file() and ".DS_Store" not in p.name
        }

    fa, fb = walk(a), walk(b)
    diffs += [f"only in existing: {p}" for p in sorted(fa - fb)]
    diffs += [f"only in rebuild:  {p}" for p in sorted(fb - fa)]
    for p in sorted(fa & fb):
        if not filecmp.cmp(a / p, b / p, shallow=False):
            diffs.append(f"changed: {p}")
    return diffs


@click.group()
@click.version_option(package_name="gnx")
def main() -> None:
    """gnx — the catalog: component registry + Claude Code marketplace + agentic CLI."""


@main.command("list")
def list_cmd() -> None:
    """List the registered components."""
    comps = load_components(Path.cwd())
    if not comps:
        console.print("[yellow]no components found under ./components[/]")
        return
    t = Table(title="gnx components", title_style="bold")
    t.add_column("kind", style="cyan")
    t.add_column("type_url")
    t.add_column("provides", style="dim")
    for c in comps:
        t.add_row(c.kind, c.type_url, ", ".join(c.provides))
    console.print(t)
    for p in check_layout(comps):
        console.print(f"[red]layout:[/] {p}")


@main.command("build")
@click.option(
    "--check",
    is_flag=True,
    help="Build to a temp dir and diff against the committed projection; exit 1 on drift.",
)
def build_cmd(check: bool) -> None:
    """Project components/ → Claude Code plugins (marketplace.json + plugins/)."""
    root = Path.cwd()
    doc = load_bundles(root)
    defaults = doc.get("defaults") or {}
    mp_meta = doc.get("marketplace") or {}

    if problems := check_layout(load_components(root)):
        for p in problems:
            console.print(f"[red]layout:[/] {p}")
        raise SystemExit(1)

    # Build into a staging directory, then swap. The previous implementation rmtree'd
    # plugins/ up front, so any failure mid-build left the repo with no projection at all.
    staging = Path(tempfile.mkdtemp(prefix="gnx-build-", dir=root / ".git"))
    try:
        stage_plugins = staging / "plugins"
        stage_plugins.mkdir(parents=True)

        entries: list[dict[str, Any]] = []
        all_problems: list[str] = []
        for spec in doc["plugins"]:
            entry, problems = build_plugin(root, spec, defaults, stage_plugins)
            all_problems += problems
            if entry:
                entries.append(entry)

        # Links can only be checked once every member of a bundle is in place.
        link_problems: list[str] = []
        for spec in doc["plugins"]:
            pdir = stage_plugins / spec["name"]
            if pdir.exists():
                link_problems += check_links(pdir)

        if all_problems or link_problems:
            for p in all_problems:
                console.print(f"[red]projection:[/] {p}")
            for p in link_problems:
                console.print(f"[yellow]link:[/] {p}")
            if all_problems:
                raise SystemExit(1)

        marketplace: dict[str, Any] = {"name": mp_meta.get("name", "gnx")}
        if mp_meta.get("owner"):
            marketplace["owner"] = mp_meta["owner"]
        if mp_meta.get("metadata"):
            md = dict(mp_meta["metadata"])
            if md.get("description"):
                md["description"] = _one_line(md["description"])
            marketplace["metadata"] = md
        marketplace["plugins"] = entries
        mp_text = json.dumps(marketplace, indent=2) + "\n"
        (staging / "marketplace.json").write_text(mp_text)

        live_plugins = root / "plugins"
        live_mp = root / ".claude-plugin" / "marketplace.json"

        if check:
            drift = _tree_differs(live_plugins, stage_plugins)
            existing_mp = live_mp.read_text() if live_mp.exists() else None
            if existing_mp != mp_text:
                drift.append("changed: .claude-plugin/marketplace.json")
            if drift:
                console.print(
                    f"[red]drift[/] — {len(drift)} difference(s) vs the committed projection:"
                )
                for d in drift[:40]:
                    console.print(f"  {d}")
                if len(drift) > 40:
                    console.print(f"  … and {len(drift) - 40} more")
                raise SystemExit(1)
            console.print(
                f"[green]clean[/] — {len(entries)} plugin(s); "
                "committed projection matches components/"
            )
            return

        if live_plugins.exists():
            shutil.rmtree(live_plugins)
        shutil.move(str(stage_plugins), str(live_plugins))
        live_mp.parent.mkdir(parents=True, exist_ok=True)
        live_mp.write_text(mp_text)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    console.print(
        f"[green]projected[/] {len(entries)} plugin(s) → "
        f"[bold].claude-plugin/marketplace.json[/] + [bold]plugins/[/]"
    )
    for e in entries:
        counts = []
        pdir = root / "plugins" / e["name"]
        for sub, label in (("agents", "agent"), ("skills", "skill")):
            n = len(list((pdir / sub).glob("*"))) if (pdir / sub).exists() else 0
            if n:
                counts.append(f"{n} {label}{'s' if n != 1 else ''}")
        console.print(f"  • {e['name']:<20} [dim]{', '.join(counts) or 'no components'}[/]")


if __name__ == "__main__":
    main()

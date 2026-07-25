"""gnx CLI — load components, project them as Claude Code plugins.

`gnx list`  — show the registered components.
`gnx build` — project components/ → .claude-plugin/marketplace.json + plugins/ (the Claude Code marketplace).

Operates on the current working directory (the gnx repo): reads ./components, writes ./plugins + ./.claude-plugin.
slick manifests are parsed as YAML directly (slickit's Python binding is broken upstream; ground-truth §3).
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table

console = Console()
OWNER = {"name": "Mox Labs", "email": "mox.rnd@gmail.com"}


@dataclass
class Component:
    type_url: str
    kind: str
    source: str
    provides: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    relations: dict = field(default_factory=dict)
    dir: Path = field(default=Path("."))

    @property
    def slug(self) -> str:
        return self.dir.name


def _frontmatter(md: str) -> dict:
    """Parse the YAML frontmatter of a SKILL.md (between the first pair of --- fences)."""
    m = re.match(r"^---\r?\n(.*?)\r?\n---", md, re.S)
    return (yaml.safe_load(m.group(1)) or {}) if m else {}


def _one_line(text: str) -> str:
    """Collapse a (possibly multi-line) description to a single tight line for the marketplace surface."""
    return " ".join((text or "").split())


def load_components(root: Path) -> list[Component]:
    comps: list[Component] = []
    cdir = root / "components"
    if not cdir.exists():
        return comps
    for d in sorted(p for p in cdir.iterdir() if p.is_dir()):
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


@main.command("build")
@click.option("--check", is_flag=True, help="Verify the projection without writing (exit 1 on drift). [stub]")
def build_cmd(check: bool) -> None:
    """Project components/ → Claude Code plugins (marketplace.json + plugins/)."""
    root = Path.cwd()
    comps = load_components(root)
    if not comps:
        console.print("[red]no components to build[/]")
        raise SystemExit(1)

    plugins_dir = root / "plugins"
    if not check and plugins_dir.exists():
        shutil.rmtree(plugins_dir)  # generated; regenerate clean

    marketplace_plugins: list[dict] = []
    for c in comps:
        skill_path = (c.dir / c.source.lstrip("./")).resolve()
        fm = _frontmatter(skill_path.read_text()) if skill_path.exists() else {}
        name = fm.get("name") or c.slug
        desc = _one_line(fm.get("description", ""))

        if not check:
            # one component (a Skill) → one plugin holding that skill
            pdir = plugins_dir / name
            (pdir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
            (pdir / ".claude-plugin" / "plugin.json").write_text(
                json.dumps(
                    {
                        "name": name,
                        "version": "0.1.0",
                        "description": desc,
                        "author": OWNER,
                        "license": "MIT",
                        "keywords": c.provides,
                    },
                    indent=2,
                )
                + "\n"
            )
            if c.kind == "Skill":
                sdir = pdir / "skills" / name
                sdir.mkdir(parents=True, exist_ok=True)
                shutil.copy(skill_path, sdir / "SKILL.md")

        marketplace_plugins.append(
            {"name": name, "source": f"./plugins/{name}", "description": desc}
        )

    marketplace = {"name": "gnx", "owner": OWNER, "plugins": marketplace_plugins}
    mp_path = root / ".claude-plugin" / "marketplace.json"
    if not check:
        mp_path.parent.mkdir(parents=True, exist_ok=True)
        mp_path.write_text(json.dumps(marketplace, indent=2) + "\n")

    verb = "would project" if check else "projected"
    console.print(
        f"[green]{verb}[/] {len(comps)} component(s) → {len(marketplace_plugins)} plugin(s) "
        f"→ [bold].claude-plugin/marketplace.json[/]"
    )
    for p in marketplace_plugins:
        console.print(f"  • {p['name']}  [dim]{p['source']}[/]")


if __name__ == "__main__":
    main()

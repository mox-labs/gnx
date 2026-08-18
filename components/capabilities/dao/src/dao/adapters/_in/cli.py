"""dao CLI — `dao check`, `dao spec`, `dao figures`.

`dao init` is deliberately NOT a code command. Scaffolding eleven components is a
judgment task (which seats, what method prose, which non-negotiables) and the research is
explicit that the method prose is the load-bearing part. A generator that emits eleven
plausible files would produce exactly the artifact-shaped-hole this audit exists to
catch. The method lives in the `dao` skill; the code checks the result.
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from dao.adapters._out.corpus import (
    DEFAULT_CORPUS,
    for_project,
    load_catalog,
    projects,
)
from dao.application.audit import POLICY, audit, summarise
from dao.domain.spec import SPEC, Tier

console = Console()

_STATUS_STYLE = {"PRESENT": "green", "PARTIAL": "yellow", "ABSENT": "red"}


@click.group()
@click.version_option(package_name="dao")
def main() -> None:
    """dao — stand up and audit a Directed Agentic Organization for a project."""


@main.command("spec")
@click.option("--findings/--no-findings", default=False,
              help="Show the research finding that constrains each component.")
def spec_cmd(findings: bool) -> None:
    """The eleven components of a dao."""
    for tier in (Tier.COUNCIL, Tier.ORGANIZATION):
        label = ("COUNCIL FLOOR — what deliberating requires"
                 if tier is Tier.COUNCIL
                 else "ORGANIZATION DELTA — what running a project adds (crisis-driven, M3)")
        console.print(f"\n[bold]{label}[/]")
        for c in (x for x in SPEC if x.tier is tier):
            console.print(f"  [cyan]#{c.n:<2}[/] [bold]{c.name}[/]  [dim]{c.group.value}[/]")
            console.print(f"      {c.summary}")
            if c.emits:
                console.print(f"      [dim]emits: {', '.join(c.emits)}[/]")
            if findings and c.finding:
                console.print(f"      [magenta]finding:[/] {c.finding}")
            if findings and c.substantive_test:
                console.print(f"      [yellow]test:[/] {c.substantive_test}")


@main.command("check")
@click.argument("project", type=click.Path(exists=True, file_okay=False, path_type=Path),
                default=".")
@click.option("--verbose", "-v", is_flag=True, help="Show the substantive test for each gap.")
def check_cmd(project: Path, verbose: bool) -> None:
    """Audit PROJECT against the eleven components."""
    coverage = audit(project)
    s = summarise(coverage)

    t = Table(title=f"dao check — {project.resolve().name}", title_style="bold")
    t.add_column("#", justify="right", style="cyan")
    t.add_column("component")
    t.add_column("tier", style="dim")
    t.add_column("status")
    t.add_column("evidence / gaps")
    for c in coverage:
        detail = "; ".join(c.evidence)
        if c.gaps:
            flagged = "\n".join(f"[yellow]! {g}[/]" for g in c.gaps)
            detail = (detail + "\n" + flagged) if detail else flagged
        t.add_row(str(c.component.n), c.component.name, c.component.tier.value,
                  f"[{_STATUS_STYLE[c.status]}]{c.status}[/]", detail or "—")
    console.print(t)

    council = "[green]COMPLETE[/]" if s["council_complete"] else \
        f"[red]INCOMPLETE[/] — missing {s['council_missing']}"
    org = "[green]COMPLETE[/]" if s["organization_complete"] else \
        f"[yellow]INCOMPLETE[/] — missing {s['organization_missing']}"
    console.print(f"\n  council floor      {council}")
    console.print(f"  organization delta {org}")
    console.print("  [dim]M3: the council→organization transition is crisis-driven. An "
                  "incomplete organization\n  delta is a legitimate state; an incomplete "
                  "council floor is not.[/]")

    if verbose:
        for c in coverage:
            if c.gaps and c.component.substantive_test:
                console.print(f"\n[cyan]#{c.component.n}[/] {c.component.name}")
                console.print(f"  [yellow]test:[/] {c.component.substantive_test}")
                if c.component.finding:
                    console.print(f"  [magenta]why:[/] {c.component.finding}")

    raise SystemExit(0 if s["council_complete"] else 1)


@main.command("figures")
@click.argument("project", default="")
@click.option("--corpus", type=click.Path(path_type=Path), default=None,
              help=f"dao-corpus root (default {DEFAULT_CORPUS}).")
def figures_cmd(project: str, corpus: Path | None) -> None:
    """Grounded domains available for PROJECT; with no PROJECT, list every project."""
    catalog = load_catalog(corpus)
    if not catalog:
        console.print(f"[yellow]no catalog found[/] under {corpus or DEFAULT_CORPUS}")
        raise SystemExit(1)

    if not project:
        t = Table(title=f"dao-corpus — {len(catalog)} domains", title_style="bold")
        t.add_column("project", style="cyan")
        t.add_column("domains", justify="right")
        for p, n in projects(catalog).items():
            t.add_row(p, str(n))
        console.print(t)
        console.print("  [dim]`dao figures <project>` for one project's grounded bench[/]")
        return

    rows = for_project(catalog, project)
    if not rows:
        console.print(f"[yellow]no domains inform[/] {project!r}")
        raise SystemExit(1)
    t = Table(title=f"grounded domains — {project}", title_style="bold")
    t.add_column("domain", style="cyan")
    t.add_column("informs")
    t.add_column("type", style="dim")
    t.add_column("src", justify="right")
    t.add_column("grounding")
    for d in rows:
        style = {"DISTILLED": "green", "STAGED": "yellow"}.get(d.grounding.value, "red")
        t.add_row(d.name, ", ".join(d.informs_project(project)), d.type.value,
                  str(d.sources), f"[{style}]{d.grounding.value}[/]")
    console.print(t)
    raw = [d.name for d in rows if d.grounding.value == "RAW_GATHERED"]
    if raw:
        console.print(
            f"\n  [yellow]{len(raw)} of {len(rows)} are RAW_GATHERED[/] — sources collected, "
            "not distilled. A seat projected from\n  these is standing on ungrounded "
            "material; say so rather than implying the corpus validated it."
        )


@main.command("policy")
def policy_cmd() -> None:
    """The audit's thresholds, as declared policy (M2: no hardcoded constants)."""
    t = Table(title="dao check — policy contract", title_style="bold")
    t.add_column("key", style="cyan")
    t.add_column("value")
    for k, v in POLICY.items():
        t.add_row(k, str(v))
    console.print(t)
    console.print("  [dim]M2 (dao-init schema): every governed surface is a declared, "
                  "ratchet-fed policy\n  contract. A threshold buried in code is a "
                  "governance decision hidden from governance.[/]")


if __name__ == "__main__":
    main()

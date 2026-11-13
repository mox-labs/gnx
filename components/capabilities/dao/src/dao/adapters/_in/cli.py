"""dao CLI: `dao check`, `dao book`, `dao spec`, `dao policy`, `dao figures`, `dao --skill`.

`dao init` is deliberately NOT a command. Writing an organization is a judgment task
(which positions, what each one's remit is, what the constitution commits to), and the
research is explicit that the prose is the load-bearing part. A generator that emits six
plausible files would produce exactly the artifact-shaped hole this audit exists to catch.
The writing lives in the `dao` skill; the code checks the result.

`dao book` is the one command that writes, and it writes one file: a page that shows what
the six documents already say. It decides nothing, so it is not the generator refused above.
"""

from __future__ import annotations

import json
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.table import Table

from dao.adapters._out.corpus import (
    DEFAULT_CORPUS,
    for_project,
    load_catalog,
    projects,
)
from dao.adapters._out.html import render, write
from dao.application.audit import POLICY, complete
from dao.application.book import build, check
from dao.application.organization import Organization
from dao.domain.spec import SPEC, Status

if TYPE_CHECKING:
    from dao.domain.book import Book
from dao.skill_files import skill as skill_text
from dao.skill_files import skill_references

console = Console()


def _page(book: Book) -> str:
    return render(book, version=metadata.version("dao"))


_STATUS_STYLE = {Status.PRESENT: "green", Status.PARTIAL: "yellow", Status.ABSENT: "red"}


@click.group(invoke_without_command=True)
@click.version_option(package_name="dao")
@click.option(
    "--skill", is_flag=True, help="Print how to set a project up (for an agent), then exit."
)
@click.option(
    "-r",
    "--reference",
    type=click.Choice(skill_references()),
    help="With --skill: print one reference instead.",
)
@click.pass_context
def main(ctx: click.Context, skill: bool, reference: str | None) -> None:
    """dao: check a project's directed agent organization, and show it on one page."""
    if reference is not None and not skill:
        raise click.UsageError("-r/--reference is used with --skill")
    if skill:
        click.echo(skill_text(reference), nl=False)
        ctx.exit(0)
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.command("spec")
@click.option(
    "--findings/--no-findings",
    default=False,
    help="Show the research finding behind each element.",
)
def spec_cmd(findings: bool) -> None:
    """The six documents of a dao, and what each must contain."""
    for e in SPEC:
        console.print(f"\n[bold]{e.name}[/]  [dim]{POLICY.directory}/{e.file}[/]")
        console.print(f"  {e.summary}")
        for line in e.must_contain:
            console.print(f"  [yellow]must contain:[/] {line}")
        if findings and e.finding:
            console.print(f"  [magenta]finding:[/] {e.finding}")


@main.command("check")
@click.argument(
    "project", type=click.Path(exists=True, file_okay=False, path_type=Path), default="."
)
@click.option(
    "--dir",
    "directory",
    default=None,
    help=f"Where the documents live, relative to PROJECT (default {POLICY.directory}).",
)
@click.option(
    "--verbose", "-v", is_flag=True, help="Show what each element with a gap must contain."
)
@click.option("--json", "as_json", is_flag=True, help="Print the verdicts as JSON.")
def check_cmd(project: Path, directory: str | None, verbose: bool, as_json: bool) -> None:
    """Audit PROJECT against the six documents. Exits 1 unless every one is PRESENT."""
    coverage = check(project, directory, render=_page)
    if as_json:
        report = {
            "directory": directory or POLICY.directory,
            "complete": complete(coverage),
            "elements": [
                {
                    "key": c.element.key.value,
                    "name": c.element.name,
                    "file": c.element.file,
                    "status": c.status.value,
                    "evidence": c.evidence,
                    "gaps": c.gaps,
                }
                for c in coverage
            ],
        }
        click.echo(json.dumps(report, indent=2))
        raise SystemExit(0 if complete(coverage) else 1)

    t = Table(title=f"dao check: {project.resolve().name}", title_style="bold")
    t.add_column("element")
    t.add_column("file", style="dim")
    t.add_column("status")
    t.add_column("evidence / gaps")
    for c in coverage:
        detail = "; ".join(c.evidence)
        if c.gaps:
            flagged = "\n".join(f"[yellow]! {g}[/]" for g in c.gaps)
            detail = (detail + "\n" + flagged) if detail else flagged
        t.add_row(
            c.element.name,
            c.element.file,
            f"[{_STATUS_STYLE[c.status]}]{c.status.value}[/]",
            detail or "-",
        )
    console.print(t)

    ok = complete(coverage)
    not_present = [c.element.name for c in coverage if c.status is not Status.PRESENT]
    console.print(
        "\n  [green]COMPLETE[/]: all six are present."
        if ok
        else f"\n  [red]INCOMPLETE[/]: {', '.join(not_present)}"
    )
    console.print(
        "  [dim]This is a check of form. It does not judge whether a document says "
        "anything\n  useful; PRESENT is not a review.[/]"
    )

    if verbose:
        for c in coverage:
            if c.status is Status.PRESENT:
                continue
            console.print(f"\n[bold]{c.element.name}[/]")
            for line in c.element.must_contain:
                console.print(f"  [yellow]must contain:[/] {line}")
            if c.element.finding:
                console.print(f"  [magenta]why:[/] {c.element.finding}")

    raise SystemExit(0 if ok else 1)


@main.command("book")
@click.argument(
    "project", type=click.Path(exists=True, file_okay=False, path_type=Path), default="."
)
@click.option(
    "--dir",
    "directory",
    default=None,
    help=f"Where the documents live, relative to PROJECT (default {POLICY.directory}).",
)
@click.option(
    "--json", "as_json", is_flag=True, help="Print the organization as JSON; write nothing."
)
def book_cmd(project: Path, directory: str | None, as_json: bool) -> None:
    """Write PROJECT's organization as one page a person can read.

    The page is book.html in the documents' directory. It is a copy of what the six documents
    say; `dao check` reports it when the documents have changed since.
    """
    org = Organization(project, directory)
    if not org.dir.is_dir():
        raise click.ClickException(
            f"no {org.directory}/ in {project}: there are no documents to make a book from"
        )
    book = build(project, directory)
    if as_json:
        click.echo(book.model_dump_json(indent=2))
        return

    where = f"{org.directory}/{POLICY.book}"
    page = _page(book)
    if org.book.is_symlink() or not org.inside:
        raise click.ClickException(f"{where} would be written outside {project}; nothing written")
    write(page, org.book)

    console.print(f"  wrote [bold]{where}[/]: open it in a browser")
    if not book.complete:
        console.print(
            "  [yellow]dao check finds gaps in the documents.[/] The page lists them at the "
            "top; `dao check` says more."
        )


@main.command("figures")
@click.argument("project", default="")
@click.option(
    "--corpus",
    type=click.Path(path_type=Path),
    default=None,
    help=f"dao-corpus root (default {DEFAULT_CORPUS}).",
)
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
        t.add_row(
            d.name,
            ", ".join(d.informs_project(project)),
            d.type.value,
            str(d.sources),
            f"[{style}]{d.grounding.value}[/]",
        )
    console.print(t)
    raw = [d.name for d in rows if d.grounding.value == "RAW_GATHERED"]
    if raw:
        console.print(
            f"\n  [yellow]{len(raw)} of {len(rows)} are RAW_GATHERED[/] — sources collected, "
            "not distilled. A position written from\n  these is standing on ungrounded "
            "material; say so rather than implying the corpus validated it."
        )


@main.command("policy")
def policy_cmd() -> None:
    """The audit's thresholds, as declared policy (M2: no hardcoded constants)."""
    t = Table(title="dao check: policy", title_style="bold")
    t.add_column("key", style="cyan")
    t.add_column("value")
    for k, v in POLICY.model_dump().items():
        t.add_row(k, ", ".join(v) if isinstance(v, tuple) else str(v))
    console.print(t)
    console.print(
        "  [dim]M2: every governed surface is a declared policy. A threshold buried in "
        "code\n  is a governance decision hidden from governance.[/]"
    )


if __name__ == "__main__":
    main()

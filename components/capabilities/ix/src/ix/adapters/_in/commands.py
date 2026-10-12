"""The experiment model's commands: ``validate``, ``plan``, ``register``, ``genres``.

An inbound adapter over the library API and nothing else: every call goes through ``ix``'s
declared names, so a program importing ``ix`` gets exactly what these commands print.

Data goes to stdout and commentary to stderr. ``--json`` prints one versioned document; an
error is then one JSON line on stderr, ``{"error": {"kind", "message", "fix"}}``. Nothing
prompts.
"""

from __future__ import annotations

import json
import sys
from typing import Any, NoReturn

import rich_click as click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

import ix

out = Console()
err = Console(stderr=True)

EXIT_FAILURE = 1
EXIT_CONFIG = 3
EXIT_NOT_FOUND = 4

#: Exit code by error kind: what the caller should do next, not where the error arose.
_EXIT_BY_KIND = {"config": EXIT_CONFIG, "conflict": EXIT_CONFIG, "not_found": EXIT_NOT_FOUND}

EXIT_CODES = """\b
Exit codes:
  0  done; for validate, the experiment is valid
  2  usage: a bad flag or argument
  3  config: the experiment is invalid, or register met a changed experiment
  4  not found: no experiment file, or no registration to check
"""

_json_option = click.option(
    "--json", "as_json", is_flag=True, help="One JSON document on stdout; errors as JSON on stderr"
)
_path_argument = click.argument("path", type=click.Path())


def _emit(document: dict[str, Any]) -> None:
    click.echo(json.dumps(document, indent=2, default=str))


def _fail(error: ix.IxError, *, as_json: bool) -> NoReturn:
    """Report an error and exit with the code its kind maps to."""
    body: dict[str, Any] = {"kind": error.kind, "message": str(error), "fix": error.fix}
    problems = [p.document() for p in getattr(error, "problems", ())]
    deviations = [d.document() for d in getattr(error, "deviations", ())]
    if problems:
        body["problems"] = problems
    if deviations:
        body["deviations"] = deviations
    if as_json:
        click.echo(json.dumps({"error": body}), err=True)
    else:
        err.print(f"[red]Error:[/red] {escape(str(error))}")
        _print_problems(problems, err)
        for deviation in deviations:
            err.print(f"  [red]-[/red] {escape(_deviation_line(deviation))}")
        if error.fix:
            err.print(f"  [dim]Fix: {escape(error.fix)}[/dim]")
    sys.exit(_EXIT_BY_KIND.get(error.kind, EXIT_FAILURE))


def _print_problems(problems: list[dict[str, Any]], console: Console, mark: str = "red") -> None:
    for problem in problems:
        where = problem["path"] or "(file)"
        if problem.get("source"):
            where = f"{problem['source']}: {where}"
        console.print(
            f"  [{mark}]-[/{mark}] [bold]{escape(where)}[/bold] "
            f"[dim]{escape(problem['kind'])}[/dim] {escape(problem['message'])}"
        )
        console.print(f"    [dim]Fix: {escape(problem['fix'])}[/dim]")


def _deviation_line(deviation: dict[str, Any]) -> str:
    return (
        f"{deviation['field']}: {deviation['message']} "
        f"(registered {deviation['registered']!r}, now {deviation['current']!r})"
    )


@click.command(epilog=EXIT_CODES)
@_path_argument
@_json_option
def validate(path: str, as_json: bool) -> None:
    """Check an experiment file: every problem at once, nothing run.

    PATH is the experiment file, or a directory that holds experiment.yaml. Exits 3 when the
    experiment is invalid; the document on stdout lists each problem with its path, kind,
    message and fix.
    """
    try:
        result = ix.validate(path)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    document = result.document()
    if as_json:
        _emit(document)
    else:
        label = escape(result.experiment or result.source)
        if result.valid:
            out.print(f"[green]valid[/green] {label} [dim]({escape(result.genre)})[/dim]")
        else:
            out.print(f"[red]invalid[/red] {label}: {len(result.problems)} problem(s)")
            _print_problems(document["problems"], out)
        if result.warnings:
            out.print(f"{len(result.warnings)} warning(s)")
            _print_problems(document["warnings"], out, mark="yellow")
    if not result.valid:
        sys.exit(EXIT_CONFIG)


def _print_plan(plan: ix.Plan) -> None:
    out.print(
        f"[bold]{escape(plan.experiment)}[/bold] [dim]{escape(plan.genre)}[/dim]  "
        f"plan [cyan]{plan.plan[:12]}[/cyan]"
    )
    subjects = Table(title="Subjects", title_justify="left")
    for column in ("subject", "role", "kind"):
        subjects.add_column(column)
    for subject in plan.subjects:
        subjects.add_row(escape(subject.name), subject.role, escape(subject.kind))
    out.print(subjects)
    if plan.control is not None:
        out.print(f"Control: [bold]{escape(plan.control)}[/bold]")

    factors = " x ".join(f"{name}({len(levels)})" for name, levels in plan.factors.items())
    out.print(f"Cells: {len(plan.cells)}" + (f"  [dim]{escape(factors)}[/dim]" if factors else ""))
    out.print(f"Probes: {plan.probes.count}  [dim]digest {plan.probes.digest[:12]}[/dim]")
    out.print(f"Repeats: {plan.repeats}")
    if plan.calibration.trials:
        out.print(
            f"Calibration: A/A on {escape(str(plan.calibration.subject))}, "
            f"{plan.calibration.trials} trials"
        )
    out.print(f"Trials: [bold]{plan.trials}[/bold]")
    out.print(f"Order: {plan.order.rule}, seed {plan.order.seed}")
    out.print(f"  [dim]{escape(plan.order.description)}[/dim]")

    if plan.effects:
        out.print("Effects:")
        for effect in plan.effects:
            out.print(f"  - {escape(effect.effect)} [dim]({escape(effect.declared_by)})[/dim]")
    else:
        out.print("Effects: none declared")
    spend = plan.spend
    if spend.estimated is None:
        out.print(f"Spend: not estimated [dim]({escape(spend.basis)})[/dim]")
    else:
        ceiling = "" if spend.ceiling is None else f", ceiling {spend.ceiling:g}"
        out.print(
            f"Spend: about {spend.estimated:g} {escape(spend.currency)}{ceiling} "
            f"[dim]({escape(spend.basis)})[/dim]"
        )

    settings = Table(title="Design", title_justify="left")
    for column in ("setting", "value", "from", "note"):
        settings.add_column(column, no_wrap=column in ("setting", "from"))
    for setting in plan.settings:
        settings.add_row(
            escape(setting.field),
            escape(str(setting.value)),
            escape(setting.source),
            escape(setting.note),
        )
    out.print(settings)
    for note in plan.notes:
        out.print(f"[dim]Note: {escape(note)}[/dim]")


@click.command(epilog=EXIT_CODES)
@_path_argument
@_json_option
def plan(path: str, as_json: bool) -> None:
    """Show what a run would do. Runs nothing and spends nothing.

    Lists the subjects with their roles (and which is the control), the cells, the probes,
    the repeats, the order rule, the declared effects and the estimated spend. The plan's
    digest changes when any of that does.
    """
    try:
        result = ix.plan(path)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    if as_json:
        _emit(result.document())
    else:
        _print_plan(result)


@click.command(epilog=EXIT_CODES)
@_path_argument
@click.option(
    "--check",
    is_flag=True,
    help="Write nothing: list what changed since the experiment was registered",
)
@_json_option
def register(path: str, check: bool, as_json: bool) -> None:
    """Freeze an experiment's decision parameters before it runs.

    Writes <file-stem>.registration.json beside the experiment: the digests of the file and
    of the probe set, the genre, the primary metric, the margin, the repeats, the seed and
    the spend ceiling. Registering again with nothing changed does nothing. A changed
    experiment is refused (exit 3): the change is a deviation, and the record is kept.
    """
    try:
        if check:
            checked = ix.deviations(path)
            if as_json:
                _emit(checked.document())
                return
            if not checked.deviations:
                out.print(f"[green]no deviations[/green] from {escape(checked.location)}")
            else:
                out.print(f"{len(checked.deviations)} deviation(s) from {escape(checked.location)}")
                for deviation in checked.document()["deviations"]:
                    out.print(f"  - {escape(_deviation_line(deviation))}")
            return
        registered = ix.register(path)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    if as_json:
        _emit(registered.document())
        return
    verb = "registered" if registered.created else "already registered"
    record = registered.registration
    out.print(f"[green]{verb}[/green] {escape(record.experiment)} at {escape(registered.location)}")
    out.print(
        f"  [dim]genre {escape(record.genre)}, repeats {record.repeats}, seed {record.seed}, "
        f"file {record.file_digest[:12]}, probes {record.probes_digest[:12]}[/dim]"
    )


@click.command(epilog=EXIT_CODES)
@click.option(
    "--beside",
    type=click.Path(),
    help="An experiment file: also list the genres in genres/ beside it",
)
@_json_option
def genres(beside: str | None, as_json: bool) -> None:
    """List the genres an experiment may name, and any genre file ix refused."""
    try:
        catalogue = ix.catalogue(beside)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    document = catalogue.document()
    if as_json:
        _emit(document)
        return
    table = Table(title="Genres", title_justify="left")
    for column in ("genre", "extends", "question", "requires", "verdict"):
        table.add_column(column, no_wrap=column in ("genre", "extends"))
    for genre in catalogue.genres.values():
        table.add_row(
            escape(genre.name),
            escape(genre.extends or ""),
            escape(genre.question),
            escape(", ".join(genre.requires)),
            genre.verdict,
        )
    out.print(table)
    for entry in document["rejected"]:
        out.print(f"[red]refused[/red] {escape(entry['name'])}")
        _print_problems(entry["problems"], out)


#: The commands the root group adds, by the name each is invoked with.
COMMANDS: dict[str, click.Command] = {
    "validate": validate,
    "plan": plan,
    "register": register,
    "genres": genres,
}

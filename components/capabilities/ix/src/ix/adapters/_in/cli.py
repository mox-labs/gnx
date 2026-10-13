"""The ``ix`` command line: validate, plan, register, deviations, genres, kinds.

An inbound adapter over the library API and nothing else: every call goes through ``ix``'s
declared names, so a program importing ``ix`` gets exactly what these commands print.

Data goes to stdout and commentary to stderr. ``--json`` prints one versioned document. A
command that fails prints the ``ix.v1.error`` envelope on stderr (one JSON line under
``--json``) and exits with the code its kind maps to. Nothing prompts.
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

EXIT_CODES = "\b\nExit codes:\n" + "\n".join(
    f"  {code}  {meaning}" for code, meaning in ix.EXIT_CODES.items()
)

_json_option = click.option(
    "--json", "as_json", is_flag=True, help="One JSON document on stdout; errors as JSON on stderr"
)
_path_argument = click.argument("path", type=click.Path())


def _emit(document: dict[str, Any]) -> None:
    click.echo(json.dumps(document, indent=2, default=str))


def _text(value: Any) -> str:
    """A value as an author would write it in YAML, never as Python prints it."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


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
        f"{deviation['field']}: registered {_text(deviation['registered'])}, "
        f"now {_text(deviation['current'])}"
    )


def _fail(error: ix.IxError, *, as_json: bool) -> NoReturn:
    """Print the error envelope and exit with the code the error's kind maps to."""
    document = ix.error_document(error)
    if as_json:
        click.echo(json.dumps(document), err=True)
    else:
        body = document["error"]
        err.print(f"[red]Error:[/red] {escape(body['message'])}")
        _print_problems(body["problems"], err)
        for deviation in body["deviations"]:
            err.print(f"  [red]-[/red] {escape(_deviation_line(deviation))}")
        if body["fix"]:
            err.print(f"  [dim]Fix: {escape(body['fix'])}[/dim]")
    sys.exit(error.exit_code)


class _Group(click.RichGroup):
    """The root group. A usage error is reported in the same envelope as every other
    failure, so a caller parses one shape."""

    def main(self, *args: Any, **kwargs: Any) -> Any:
        argv = list(
            kwargs.get("args") or (args[0] if args and args[0] is not None else sys.argv[1:])
        )
        kwargs["standalone_mode"] = False
        try:
            code = super().main(*args, **kwargs)
        except click.UsageError as error:
            usage = ix.IxError(
                error.format_message(),
                fix="`ix --help` lists the commands; `ix <command> --help` its arguments",
            )
            usage.kind = "usage"
            _fail(usage, as_json="--json" in argv)
        except click.Abort:
            sys.exit(1)
        sys.exit(code if isinstance(code, int) else 0)


def _print_skill(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    click.echo(ix.skill())
    ctx.exit()


@click.group(cls=_Group, invoke_without_command=True, epilog=EXIT_CODES)
@click.version_option(ix.__version__, prog_name="ix")
@click.option(
    "--skill",
    is_flag=True,
    expose_value=False,
    is_eager=True,
    callback=_print_skill,
    help="Print how to write an experiment for each genre and kind, then exit",
)
@click.pass_context
def main(ctx: click.Context) -> None:
    """ix: one file declares a question about a system and how it will be answered.

    ix checks the file, shows the decision a run would inform and what it would cost, and
    freezes the design before anything runs. There is no `ix run` yet: ix checks, plans and
    registers an experiment; it does not execute one.

    \b
    ix --skill                  how to write an experiment, generated from what is installed
    ix validate lab/proxy       every problem at once, nothing run
    ix plan lab/proxy           the decision, the trials, the effects, the spend
    ix register lab/proxy       freeze the design before a run
    ix deviations lab/proxy     what changed since registration
    """
    if ctx.invoked_subcommand is None:
        out.print(ctx.get_help())


# --- validate ----------------------------------------------------------------------------


@main.command(epilog=EXIT_CODES)
@_path_argument
@_json_option
def validate(path: str, as_json: bool) -> None:
    """Check an experiment file: every problem at once, nothing run.

    PATH is the experiment file, or a directory that holds experiment.yaml. Exits 3 when the
    experiment is invalid; the document on stdout lists each problem with its path, kind,
    message, fix, the allowed values and what was written.
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
            out.print(f"[dim]Next: ix plan {escape(path)}[/dim]", soft_wrap=True)
        else:
            out.print(f"[red]invalid[/red] {label}: {len(result.problems)} problem(s)")
            _print_problems(document["problems"], out)
            if not result.complete:
                out.print(
                    "[yellow]Incomplete:[/yellow] the file did not load as an experiment, so "
                    "the genre, kind and probe checks did not run. Fix these and validate again."
                )
        if result.warnings:
            out.print(f"{len(result.warnings)} warning(s)")
            _print_problems(document["warnings"], out, mark="yellow")
    sys.exit(result.exit_code)


# --- plan --------------------------------------------------------------------------------


def _field(label: str, text: str, *more: str) -> None:
    """One ``Label:  text`` line; continuation lines align under the text."""
    out.print(f"[bold]{label + ':':<14}[/bold]{escape(text)}")
    for line in more:
        out.print(f"{'':<14}{escape(line)}")


def _print_plan(plan: ix.Plan, path: str) -> None:
    out.print(
        f"[bold]{escape(plan.experiment)}[/bold]  {escape(plan.genre)} "
        f"[dim]({escape(plan.genre_url)})[/dim]  plan [cyan]{plan.digest[:12]}[/cyan]"
    )
    _field("Question", plan.question)
    _field("Decides", *(plan.decides or ("(the genre renders no hypothesis)",)))
    if plan.comparison:
        _field("Comparison", plan.comparison)
    if plan.guardrails:
        _field("Guardrails", *plan.guardrails)
    _field("Verdict", plan.verdict)
    out.print()

    one_role = len({subject.role for subject in plan.subjects}) == 1
    subjects = Table(title="Subjects", title_justify="left")
    for column in ("subject", "kind") if one_role else ("subject", "role", "kind"):
        subjects.add_column(column)
    for subject in plan.subjects:
        row = (
            (subject.subject, subject.kind)
            if one_role
            else (subject.subject, subject.role, subject.kind)
        )
        subjects.add_row(*(escape(cell) for cell in row))
    out.print(subjects)
    if plan.control is not None:
        _field("Control", plan.control)
    if plan.factors:
        _field(
            "Cells",
            f"{plan.trials.cells}: "
            + " x ".join(f"{name} ({len(levels)})" for name, levels in plan.factors.items()),
        )
    _field("Probes", f"{plan.probes.count}  (digest {plan.probes.digest[:12]})")
    _field("Trials", plan.trials.text())
    _field("Duration", plan.duration.text())
    _field("Order", f"{plan.order.scheme}, seed {plan.order.seed}", plan.order.description)
    if plan.sensors:
        _field(
            "Sensors",
            *(
                f"{sensor.label}: {', '.join(sensor.metrics) or '(no metric named)'}"
                for sensor in plan.sensors
            ),
        )

    by_effect: dict[tuple[str, str], list[str]] = {}
    for effect in plan.effects:
        by_effect.setdefault((effect.effect, effect.detail), []).append(effect.source)
    lines = [
        f"{name}{': ' + detail if detail else ''}  ({', '.join(sources)})"
        for (name, detail), sources in by_effect.items()
    ]
    _field("Effects", *(lines or ["none"]))

    spend = plan.spend
    if spend.estimated is None:
        ceiling = "" if spend.ceiling is None else f"; ceiling {spend.ceiling:g} {spend.currency}"
        _field("Spend", f"not estimated ({spend.basis}){ceiling}")
    else:
        ceiling = "" if spend.ceiling is None else f", ceiling {spend.ceiling:g}"
        _field("Spend", f"about {spend.estimated:g} {spend.currency}{ceiling}  ({spend.basis})")
    _field("Registration", plan.registration.text())
    out.print()

    design = Table(title="Design", title_justify="left")
    for column in ("setting", "value", "from"):
        design.add_column(column)
    footnotes = []
    for value in plan.provenance:
        design.add_row(escape(value.path), escape(_text(value.value)), value.source)
        if value.note:
            footnotes.append(f"{value.path}: {value.note}")
    out.print(design)
    for note in footnotes:
        out.print(f"[dim]{escape(note)}[/dim]")
    for note in plan.notes:
        out.print(f"[dim]The genre's design is {escape(note)}.[/dim]")
    next_step = {
        "none": f"ix register {path}",
        "matches": "registered and unchanged",
        "deviations": f"ix deviations {path}",
    }[plan.registration.status]
    out.print(f"[dim]Next: {escape(next_step)}[/dim]", soft_wrap=True)


@main.command(epilog=EXIT_CODES)
@_path_argument
@_json_option
def plan(path: str, as_json: bool) -> None:
    """Show the decision a run would inform and what it would cost. Runs nothing.

    States the question, the hypothesis rendered from the genre, the guardrails and the form
    of the verdict; then the subjects with their roles, the trials with their arithmetic, the
    estimated duration, the effects, the spend and where the experiment stands against its
    registration. The plan's digest changes when a value a run would use changes.
    """
    try:
        result = ix.plan(path)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    if as_json:
        _emit(result.document())
    else:
        _print_plan(result, path)


# --- register and deviations -------------------------------------------------------------


@main.command(epilog=EXIT_CODES)
@_path_argument
@click.option(
    "--amend", is_flag=True, help="Replace a registration the experiment has moved away from"
)
@click.option("--reason", help="Why the design changed; required with --amend")
@_json_option
def register(path: str, amend: bool, reason: str | None, as_json: bool) -> None:
    """Freeze an experiment's design before it runs.

    Writes <file-stem>.registration.json beside the experiment: the design as resolved (what
    the file sets and what the genre supplies) and the digests of the probe set, the
    experiment and the plan. Registering again with nothing changed does nothing. A changed
    experiment is refused (exit 3) and the record is kept; `--amend --reason "<why>"`
    replaces it and keeps the superseded record, with the reason, inside the new one.
    """
    if amend != (reason is not None):
        raise click.UsageError('--amend and --reason go together: --amend --reason "<why>"')
    try:
        registered = ix.register(path, amend=reason if amend else None)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    if as_json:
        _emit(registered.document())
        return
    verb = {
        "created": "registered",
        "unchanged": "already registered, unchanged:",
        "amended": "amended the registration of",
    }[registered.outcome]
    record = registered.registration
    out.print(
        f"[green]{verb}[/green] {escape(record.experiment)} at {escape(registered.location)}",
        soft_wrap=True,
    )
    out.print(f"  [dim]plan {record.plan_digest[:12]}, probes {record.probes_digest[:12]}[/dim]")
    if registered.outcome == "amended":
        out.print(f"  [dim]reason: {escape(record.amendments[-1].reason)}[/dim]")


@main.command(epilog=EXIT_CODES)
@_path_argument
@_json_option
def deviations(path: str, as_json: bool) -> None:
    """List what changed since the experiment was registered. Writes nothing.

    Exits 0 when nothing did and 9 when anything did; the document on stdout names each
    frozen value with what was registered and what it is now. A comment, key order or a
    default written out is not a deviation.
    """
    try:
        result = ix.deviations(path)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    document = result.document()
    if as_json:
        _emit(document)
    elif not result.deviations:
        out.print(f"[green]no deviations[/green] from {escape(result.location)}", soft_wrap=True)
    else:
        out.print(
            f"{len(result.deviations)} deviation(s) from {escape(result.location)}", soft_wrap=True
        )
        for deviation in document["deviations"]:
            out.print(f"  - {escape(_deviation_line(deviation))}", soft_wrap=True)
        amend = f'ix register {path} --amend --reason "<why>"'
        out.print(f"[dim]Next: leave them recorded, or {escape(amend)}[/dim]", soft_wrap=True)
    sys.exit(result.exit_code)


# --- genres and kinds --------------------------------------------------------------------

_lab_option = click.option(
    "--lab",
    type=click.Path(),
    help="A directory: also list what its genres/ and kinds/ directories hold",
)


def _print_rejected(rejected: dict[str, Any]) -> None:
    for name, problems in rejected.items():
        out.print(f"[red]refused[/red] {escape(name)}")
        _print_problems(problems, out)


@main.command(epilog=EXIT_CODES)
@_lab_option
@_json_option
def genres(lab: str | None, as_json: bool) -> None:
    """List the genres an experiment may name, and any genre file ix refused."""
    try:
        found = ix.genres(lab)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    document = found.document()
    if as_json:
        _emit(document)
        return
    table = Table(title="Genres", title_justify="left")
    table.add_column("genre", no_wrap=True)
    table.add_column("question")
    table.add_column("you must set")
    table.add_column("verdict", no_wrap=True)
    for genre in found.genres.values():
        table.add_row(
            escape(genre.label),
            escape(genre.question),
            escape(", ".join(genre.author_must_set) or "-"),
            genre.verdict_form,
        )
    out.print(table)
    out.print(
        "[dim]Each genre's type URL is ix.v1.genre.<name>; `ix --skill` has the detail.[/dim]"
    )
    _print_rejected(document["rejected"])


@main.command(epilog=EXIT_CODES)
@_lab_option
@_json_option
def kinds(lab: str | None, as_json: bool) -> None:
    """List the subject kinds, probe kinds, sensors and environment kinds.

    Each kind declares its options (required ones marked *), the facets it needs and offers,
    its effects and, for a sensor, the metrics it provides.
    """
    try:
        found = ix.kinds(lab)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    document = found.document()
    if as_json:
        _emit(document)
        return
    table = Table(title="Kinds", title_justify="left")
    table.add_column("family", no_wrap=True)
    table.add_column("kind", no_wrap=True)
    table.add_column("options (* required)", overflow="fold")
    table.add_column("needs > offers")
    table.add_column("effects")
    metrics = Table(title="Metrics", title_justify="left")
    for column in ("metric", "sensor", "unit", "shape", "better", "judged"):
        metrics.add_column(column, no_wrap=column != "unit")
    for kind in found.kinds.values():
        label = kind.label
        options = ", ".join(
            f"{name}{'*' if option.required else ''}" for name, option in kind.options.items()
        )
        facets = f"{', '.join(kind.needs) or '-'} > {', '.join(kind.offers) or '-'}"
        table.add_row(
            kind.family,
            escape(label),
            escape(options or "-"),
            escape(facets),
            escape(", ".join(kind.effects) or "-"),
        )
        for metric in kind.metrics:
            metrics.add_row(
                escape(metric.name),
                escape(label),
                escape(metric.unit or "-"),
                metric.shape,
                metric.direction,
                "yes" if metric.judged else "no",
            )
    out.print(table)
    out.print(metrics)
    _print_rejected(document["rejected"])


if __name__ == "__main__":
    main()

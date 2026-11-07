"""The ``ix`` command line: validate, plan, register, deviations, run, report, runs, genres,
kinds, and ``mutants sample|read``.

An inbound adapter over the library API and nothing else: every call goes through ``ix``'s
declared names, so a program importing ``ix`` gets exactly what these commands print.

Data goes to stdout and commentary to stderr. ``--json`` prints one versioned document. A
command that fails, a usage error and a defect in ix included, prints the ``ix.v1.error``
envelope on stderr (one JSON line under ``--json``) and exits with the code its kind maps
to. Nothing prompts.

``ix run`` shows progress on stderr, and none under ``--json``, where stderr carries only
an error envelope. A run that was stopped or that failed is not an error: its report is
printed and the exit code says how it ended; without ``--json`` the code, what it means and
the next step follow it on stderr (``exit`` in the document). What a run warned of (a
runtime that lets the agent use tools without asking) is said once: in the report's
``warnings``, and once on stderr without ``--json``.

Telemetry is configured once per command from the standard ``OTEL_*`` variables; what it
warns of (export asked for in an install that cannot do it) is in every ``--json``
document's ``warnings``, and on stderr otherwise.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import TYPE_CHECKING, Any, NoReturn, Protocol

import rich_click as click
from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

import ix

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

out = Console()
err = Console(stderr=True)
# Off a terminal nothing is wrapped: a path or a command split at column 80 cannot be
# copied, and a reader of a pipe or a log has no width to wrap to.
for _console in (out, err):
    _console.soft_wrap = not _console.is_terminal

# rich-click rewraps an epilog's lines; a blank line between them keeps each on its own.
EXIT_CODES = "Exit codes:\n\n" + "\n\n".join(
    f"{code}  {kind}: {ix.EXIT_MEANINGS[kind]}. Next: {ix.EXIT_NEXT[kind]}"
    for kind, code in ix.EXIT_CODES.items()
)

_json_option = click.option(
    "--json", "as_json", is_flag=True, help="One JSON document on stdout; errors as JSON on stderr"
)
_path_argument = click.argument("path", type=click.Path())


#: What telemetry warned of when the command started: every document carries it.
_TELEMETRY: list[ix.Problem] = []


def _emit(document: dict[str, Any]) -> None:
    if _TELEMETRY:
        document["warnings"] = [
            *document.get("warnings", []),
            *(warning.document() for warning in _TELEMETRY),
        ]
    click.echo(json.dumps(document, indent=2, default=str))


def _text(value: Any) -> str:
    """A value as an author would write it in YAML, never as Python prints it."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _print_problems(problems: Iterable[ix.Problem], console: Console, mark: str = "red") -> None:
    for problem in problems:
        where = problem.path or "(file)"
        if problem.source:
            where = f"{problem.source}: {where}"
        console.print(
            f"  [{mark}]-[/{mark}] [bold]{escape(where)}[/bold] "
            f"[dim]{escape(problem.kind)}[/dim] {escape(problem.message)}"
        )
        console.print(f"    [dim]fix: {escape(problem.fix)}[/dim]")


def _fail(error: ix.IxError, *, as_json: bool) -> NoReturn:
    """Print the error envelope and exit with the code the error's kind maps to."""
    if as_json:
        click.echo(json.dumps(ix.error_document(error)), err=True)
    else:
        err.print(f"[red]error ({error.kind}):[/red] {escape(str(error))}")
        _print_problems(error.problems, err)
        for deviation in error.deviations:
            err.print(f"  [red]-[/red] {escape(deviation.text())}")
        if error.fix:
            err.print(f"[dim]fix: {escape(error.fix)}[/dim]")
    sys.exit(error.exit_code)


class _Interrupted(ix.IxError):
    kind = "interrupted"


class _Group(click.RichGroup):
    """The root group. A usage error, Ctrl-C and a defect are reported in the same envelope
    as every other failure, so a caller parses one shape. Ctrl-C during ``ix run`` stops the
    run, which then reports (exit 12); anywhere else it is ``interrupted`` (exit 130)."""

    def main(self, *args: Any, **kwargs: Any) -> Any:
        argv = list(
            kwargs.get("args") or (args[0] if args and args[0] is not None else sys.argv[1:])
        )
        kwargs["standalone_mode"] = False
        try:
            telemetry = ix.configure_telemetry()
        except ix.IxError as error:
            _fail(error, as_json="--json" in argv)
        _TELEMETRY[:] = telemetry.warnings
        if telemetry.warnings and "--json" not in argv:
            _print_problems(telemetry.warnings, err, mark="yellow")
        try:
            return self._main(argv, *args, **kwargs)
        finally:
            telemetry.shutdown()

    def _main(self, argv: list[str], *args: Any, **kwargs: Any) -> Any:
        try:
            code = super().main(*args, **kwargs)
        except click.UsageError as error:
            usage = ix.UsageError(
                error.format_message(),
                fix="`ix --help` lists the commands; `ix <command> --help` its arguments",
            )
            _fail(usage, as_json="--json" in argv)
        except click.Abort:  # click's form of Ctrl-C (or of end of input, which nothing reads)
            interrupted = _Interrupted(
                "interrupted before the command finished", fix="run the command again"
            )
            _fail(interrupted, as_json="--json" in argv)
        except ix.IxError as error:
            _fail(error, as_json="--json" in argv)
        except Exception as error:  # a defect: still one envelope, so a caller parses one shape
            if "--json" not in argv:
                traceback.print_exc()
            defect = ix.IxError(
                f"{type(error).__name__}: {error}",
                fix="report this as a bug in ix, with the command that raised it",
            )
            _fail(defect, as_json="--json" in argv)
        sys.exit(code if isinstance(code, int) else 0)


@click.group(
    cls=_Group,
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog=EXIT_CODES,
)
@click.version_option(ix.__version__, prog_name="ix")
@click.option(
    "--skill",
    is_flag=True,
    help="Print the skill an agent reads: what ix is for, a working example, the commands "
    "and the exit codes; then exit",
)
@click.option(
    "-r",
    "--reference",
    metavar="NAME",
    help="With --skill: print the reference NAME instead; the skill lists the names",
)
@click.pass_context
def main(ctx: click.Context, skill: bool, reference: str | None) -> None:
    """ix: one file declares a question about a system and how it will be answered.

    ix checks the file, shows the decision a run would inform and how many trials it takes,
    freezes the design before anything runs, then runs it and reports what the readings say
    about that decision.

    \b
    ix --skill                  the skill an agent reads; ix --skill -r NAME a reference
    ix validate lab/proxy       every problem at once, nothing run
    ix plan lab/proxy           the decision, the trials, what it needs here
    ix register lab/proxy       freeze the design before a run
    ix deviations lab/proxy     what changed since registration
    ix run lab/proxy            carry it out; the exit code is the verdict
    ix run lab/proxy --detach   start it and return: a long run outlasts a tool call
    ix status lab/proxy         where its latest run stands; ix stop to stop it
    ix events lab/proxy -f      its events as they come, until it ends
    ix report lab/proxy         the report of its latest run, computed again
    ix runs lab/proxy           the runs kept beside it
    """
    if reference is not None and not skill:
        raise ix.UsageError(
            "-r/--reference is a reference of the skill and goes with --skill",
            fix=f"run `ix --skill -r {reference}`",
        )
    if skill:
        click.echo(ix.skill(reference), nl=False)
        ctx.exit()
    if ctx.invoked_subcommand is None:
        out.print(ctx.get_help())


# --- validate ----------------------------------------------------------------------------


@main.command(epilog=EXIT_CODES)
@_path_argument
@_json_option
def validate(path: str, as_json: bool) -> None:
    """Check an experiment file: every problem at once, nothing run.

    PATH is the experiment file, a directory that holds experiment.yaml, or - for standard
    input (its relative paths resolve against the current directory). Exits 3 when the
    experiment is invalid; the document on stdout lists each problem with its path, kind,
    message, fix, the allowed values and what was written.
    """
    try:
        result = ix.validate(path)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    if as_json:
        _emit(result.document())
    else:
        label = escape(result.experiment or result.source)
        if result.valid:
            out.print(f"[green]valid[/green] {label} [dim]({escape(result.genre)})[/dim]")
            err.print(f"[dim]Next: ix plan {escape(path)}[/dim]", soft_wrap=True)
        else:
            out.print(f"[red]invalid[/red] {label}: {len(result.problems)} problem(s)")
            _print_problems(result.problems, out)
            if not result.complete:
                err.print(
                    "[yellow]Incomplete:[/yellow] the file did not load as an experiment, so "
                    "the genre, kind and probe checks did not run. Fix these and validate again."
                )
        if result.warnings:
            err.print(f"{len(result.warnings)} warning(s)")
            _print_problems(result.warnings, err, mark="yellow")
    sys.exit(result.exit_code)


# --- plan --------------------------------------------------------------------------------


def _field(label: str, text: str, *more: str) -> None:
    """One ``Label:  text`` line; continuation lines align under the text."""
    out.print(f"[bold]{label + ':':<13}[/bold] {escape(text)}")
    for line in more:
        out.print(f"{'':<14}{escape(line)}")


#: Where a design value came from, as a plan's lines say it.
_SOURCED = {
    "experiment": "set in the experiment",
    "genre": "the genre's default",
    "ix": "ix's",
    "kind": "its kind's default",
    "runtime": "its runtime's own",
}


def _print_plan_decision(plan: ix.Plan) -> None:
    out.print(
        f"[bold]{escape(plan.experiment)}[/bold]  {escape(plan.genre)} "
        f"[dim]({escape(plan.genre_url)})[/dim]  plan [cyan]{plan.digest[:12]}[/cyan]"
    )
    _field("Question", plan.question)
    _field("Decides", *(plan.decides or ("(the genre renders no hypothesis)",)))
    if plan.comparison:
        _field("Comparison", plan.comparison)
    margin = next((value for value in plan.provenance if value.path == "margin"), None)
    if margin is not None:
        _field("Margin", f"{_text(margin.value)}, {_SOURCED[margin.origin]}: {margin.note}")
    if plan.guardrails:
        _field("Guardrails", *plan.guardrails)
    _field("Verdict", plan.verdict)
    out.print()


def _print_plan_work(plan: ix.Plan) -> None:
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
    if plan.overridden:
        _field("Changed", *plan.overridden)
    if plan.sensors:
        _field(
            "Sensors",
            *(
                f"{sensor.label}: {', '.join(sensor.metrics) or '(no metric named)'}"
                for sensor in plan.sensors
            ),
        )


def _print_plan_cost(plan: ix.Plan) -> None:
    lines = [
        f"{'yes' if required.holds else 'NO '}  {required.need}  ({', '.join(required.needed_by)})"
        for required in plan.requirements
    ]
    _field("Requires", *(lines or ["nothing of this host that an adapter checks"]))
    _field("Model calls", plan.work.text())
    _field("Registration", plan.registration.text())
    _field("Sensitivity", plan.sensitivity.text)
    blockers = [
        *(f"this ix has no adapter for {kind}" for kind in plan.unimplemented),
        *(f"not on this host: {required.text()}" for required in plan.unmet),
    ]
    if not plan.runnable:
        _field("Runnable", "no", *blockers)
    out.print()


#: The narrowest the Design table's value column may be before the table gives way to lines.
_VALUE_MIN = 16


def _print_plan_design(plan: ix.Plan, path: str) -> None:
    rows = []
    footnotes = []
    for value in plan.provenance:
        if value.origin == "runtime":
            shown = "(unknown to ix)"
        elif value.origin == "kind" and value.value is None:  # a meaning, not a value
            shown = value.note
        else:
            shown = _text(value.value)
        rows.append((value.path, shown, value.origin))
        # The margin's note is on its own line above; a kind's default names its kind, which
        # the entry's own line already implies.
        if value.note and value.path != "margin" and value.origin != "kind":
            footnotes.append(f"{value.path}: {value.note}")
    # A setting's path is never broken: it is what a reader searches for and copies. The
    # value folds; where even a narrow value does not fit beside the longest path, each
    # setting is a line of its own.
    key = max((len(row[0]) for row in rows), default=len("setting"))
    origin = max((len(row[2]) for row in rows), default=len("from"))
    borders = 10  # three columns' padding and four rules
    if key + origin + _VALUE_MIN + borders <= out.width:
        design = Table(title="Design", title_justify="left")
        design.add_column("setting", no_wrap=True, min_width=key)
        design.add_column("value", overflow="fold")
        design.add_column("from", no_wrap=True, min_width=origin)
        for setting, shown, came in rows:
            design.add_row(escape(setting), escape(shown), came)
        out.print(design)
    else:
        out.print("[bold]Design[/bold] (setting: value (from))")
        for setting, shown, came in rows:
            out.print(f"  {escape(setting)}: {escape(shown)} ({came})", soft_wrap=True)
    for note in footnotes:
        out.print(f"[dim]{escape(note)}[/dim]")
    for note in plan.notes:
        out.print(f"[dim]The genre's design is {escape(note)}.[/dim]")
    next_step = {
        "none": f"ix register {path}",
        "matches": f"ix run {path}",
        "deviations": f"ix deviations {path}",
    }[plan.registration.status]
    if plan.work.stood_in and plan.registration.status != "deviations":
        # A stand-in answers: a registered run of it exits simulated, never with a verdict.
        next_step = (
            f"ix run {path} --exploratory (a stand-in answers "
            f"{', '.join(plan.work.stood_in)}: it checks the harness and gives no verdict)"
        )
    if plan.overridden:  # a width to try: a run that gives a verdict refuses it
        next_step = f"ix run {path} --exploratory --trials-in-flight {plan.order.trials_in_flight}"
    err.print(f"[dim]Next: {escape(next_step)}[/dim]", soft_wrap=True)


def _print_plan(plan: ix.Plan, path: str) -> None:
    _print_plan_decision(plan)
    _print_plan_work(plan)
    _print_plan_cost(plan)
    _print_plan_design(plan, path)
    if plan.warnings:
        err.print(f"{len(plan.warnings)} warning(s)")
        _print_problems(plan.warnings, err, mark="yellow")


@main.command(epilog=EXIT_CODES)
@_path_argument
@click.option(
    "--trials-in-flight",
    type=click.IntRange(min=1),
    metavar="N",
    help="Plan the run `ix run --exploratory --trials-in-flight N` would carry out: N trials "
    "at once in place of the file's trials_in_flight (1 when it writes none), refused "
    "where a kind in use or the genre forbids overlap, or a deciding metric is contended",
)
@_json_option
def plan(path: str, trials_in_flight: int | None, as_json: bool) -> None:
    """Show the decision a run would inform and how many trials it takes. Runs nothing.

    States the question, the hypothesis rendered from the genre, the guardrails and the form
    of the verdict; then the subjects with their roles, the trials with their arithmetic, the
    estimated duration, what the run needs of this host with whether each holds here, and
    where the experiment stands against its registration. The plan's digest changes when a
    value a run would use changes.

    `--trials-in-flight N` previews a width without editing the file: the plan at that width,
    the change listed under `Changed` (`overridden`), and the refusal or warnings the run
    would meet. A run that gives a verdict refuses the flag, so it is tried with
    `ix run --exploratory` or `--dry-run`; to decide on a width, write `trials_in_flight:`
    in the file and register it.

    PATH is the experiment file, a directory that holds experiment.yaml, or - for standard
    input (which has no registration).
    """
    try:
        result = ix.plan(path, trials_in_flight=trials_in_flight)
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
    experiment is refused (exit 7, conflict) and the record is kept; `--amend --reason "<why>"`
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
    if registered.warnings:
        err.print(f"{len(registered.warnings)} warning(s)")
        _print_problems(registered.warnings, err, mark="yellow")


@main.command(epilog=EXIT_CODES)
@_path_argument
@_json_option
def deviations(path: str, as_json: bool) -> None:
    """List what changed since the experiment was registered. Writes nothing.

    Exits 0 when nothing did and 14 when anything did; the document on stdout names each
    frozen value with what was registered and what it is now. A comment, key order or a
    default written out is not a deviation.
    """
    try:
        result = ix.deviations(path)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    if as_json:
        _emit(result.document())
    elif not result.deviations:
        out.print(f"[green]no deviations[/green] from {escape(result.location)}", soft_wrap=True)
    else:
        out.print(
            f"{len(result.deviations)} deviation(s) from {escape(result.location)}", soft_wrap=True
        )
        for deviation in result.deviations:
            out.print(f"  - {escape(deviation.text())}", soft_wrap=True)
        amend = f'ix register {path} --amend --reason "<why>"'
        err.print(f"[dim]Next: leave them recorded, or {escape(amend)}[/dim]", soft_wrap=True)
    sys.exit(result.exit_code)


# --- run, report and runs ----------------------------------------------------------------

_MARK = {"holds": "green", "fails": "red", "inconclusive": "yellow"}


def _progress(
    as_json: bool,
) -> tuple[Callable[[str, Mapping[str, Any]], None] | None, Progress | None]:
    """What `ix run` does with a run's progress events, and what to close when it ends.

    On a terminal: one bar. Otherwise: one line per trial, for whoever tails stderr. Under
    ``--json``: nothing, so stderr carries only an error envelope.
    """
    if as_json:
        return None, None
    if not err.is_terminal:

        def line(name: str, fields: Mapping[str, Any]) -> None:
            if name != "ix.trial.end":
                return
            failure = fields.get("failure")
            state = "ok" if failure is None else f"{failure['owner']}: {failure['reason']}"
            err.print(
                f"[{fields['seq'] + 1}/{fields['total']}] {fields['subject']} "
                f"({fields['role']}) {state} {fields['duration_ms'] / 1000:.1f}s",
                highlight=False,
                markup=False,
            )

        return line, None

    bar = Progress(
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=err,
        transient=True,
    )
    task = bar.add_task("starting", total=None)
    bar.start()

    def update(name: str, fields: Mapping[str, Any]) -> None:
        if name == "ix.trial.start":
            bar.update(
                task,
                total=fields["total"],
                completed=fields["seq"],
                description=f"{fields['subject']} ({fields['role']})",
            )
        elif name == "ix.trial.end":
            bar.update(task, completed=fields["seq"] + 1)

    return update, bar


def _number(value: float | None, unit: str = "") -> str:
    if value is None:
        return "-"
    signed = unit in ("%", "pp")
    text = f"{value:+.2f}" if signed else f"{value:.4g}"
    return f"{text}{unit if signed else ''}"


class _Interval(Protocol):
    """The shape of ``ix.domain.Interval``. The command line reaches ix through ``ix``'s own
    names only, so it names the shape and not the part."""

    low: float
    high: float


def _interval(interval: _Interval | None, unit: str = "") -> str:
    if interval is None:
        return "-"
    return f"[{_number(interval.low, unit)}, {_number(interval.high, unit)}]"


def _cell(cell: Mapping[str, Any]) -> str:
    return ", ".join(f"{name}={_text(value)}" for name, value in cell.items()) or "-"


def _print_outcome(report: ix.Report) -> None:
    out.print(
        f"[bold]{escape(report.experiment)}[/bold]  {escape(report.genre)} "
        f"[dim]({escape(report.genre_url)})[/dim]  run [cyan]{escape(report.run)}[/cyan]"
    )
    if report.question:
        _field("Question", report.question)
    _field("Decides", *(report.decides or ("(the genre renders no hypothesis)",)))
    if report.verdict is not None:
        mark = _MARK[report.verdict]
        out.print(f"[bold]{'Verdict:':<14}[/bold][{mark}]{report.verdict}[/{mark}]")
    else:
        _field("Verdict", f"none: {report.no_verdict}")
        if report.indication is not None and report.verdict_form != "none":
            _field("Indication", f"{report.indication} (not a gate result)")
    if report.shows:
        _field("Shows", *report.shows)
    if report.analysis.reason:
        _field("Why", report.analysis.reason)
    if report.best_case:
        _field("Best case", report.best_case)
    width = report.trials_in_flight
    _field(
        "Run",
        f"{report.status}; {report.trials.text()}"
        + (f"; {width} trials in flight" if width > 1 else ""),
    )
    if report.overridden:
        _field("Changed", *report.overridden)
    if _stood_in(report) and not report.no_verdict.startswith("Dry run"):
        out.print(
            f"[bold]{'Dry run:':<14}[/bold][yellow]on mock replies: checks the harness, not "
            "the agent[/yellow]"
        )
    if report.stood_in:
        _field("Stood in", *report.stood_in)
    for error in report.errors:
        _field("Failed", error)
    if report.registered_at:
        held = (
            "held to it"
            if report.gated
            else "a stand-in answered, so not held to it"
            if _stood_in(report) and not report.exploratory
            else "exploratory: not held to it"
        )
        _field("Registration", f"registered {report.registered_at}; {held}")
    else:
        _field("Registration", "none (exploratory)")
    if report.deviations:
        _field(
            "Deviations",
            *(deviation.text() for deviation in report.deviations),
        )
    if report.contents:
        _field(
            "Contents",
            *(f"{c.label}  {c.digest[:12] or 'nothing there'}" for c in report.contents),
        )
    if report.provenance is not None:
        _field("Ran from", report.provenance.checkout())
        versions = report.provenance.versions
        _field("Versions", ", ".join(f"{name} {found}" for name, found in versions.items()))
    out.print()


def _stood_in(report: ix.Report) -> bool:
    """Whether a stand-in answered: the run checked the harness, never the subject."""
    return report.dry_run or report.simulated != "none"


def _print_findings(report: ix.Report) -> None:
    findings = report.analysis.findings
    if not findings:
        return
    canned = _stood_in(report)
    if canned:
        out.print(
            "[bold]Findings on mock replies[/bold] [yellow](the harness, not the agent: hold "
            "each estimate to the grade its canned reply should get)[/yellow]",
            soft_wrap=True,
        )
    else:
        out.print("[bold]Findings[/bold]")
    for finding in findings:
        # On canned replies an outcome grades the harness's reading of known answers, so it
        # is not marked as a verdict would be.
        mark = "dim" if canned else _MARK[finding.outcome]
        where = f" in {_cell(finding.cell)}" if finding.cell else ""
        out.print(
            f"  [bold]{escape(finding.subject)}[/bold]{escape(where)}: "
            f"[{mark}]{finding.outcome}[/{mark}]"
            + (f" [dim]({escape(finding.reason)})[/dim]" if finding.reason else ""),
            soft_wrap=True,
        )
        for decision in finding.decisions:
            measure = f"{decision.metric} {decision.statistic} ({decision.estimator})"
            out.print(
                f"    {decision.part}: {escape(measure)} {escape(decision.claim)}: "
                f"{decision.outcome}",
                soft_wrap=True,
                highlight=False,
            )
            facts = []
            if decision.difference is not None:
                label = "estimate" if decision.form == "threshold" else "difference"
                facts.append(
                    f"{label} {_number(decision.difference, decision.unit)} "
                    f"{_interval(decision.interval, decision.unit)}"
                    + (" (95%, unadjusted)" if decision.interval is not None else "")
                )
            if decision.better and decision.form != "threshold":
                facts.append(f"{decision.better} is better")
            if decision.method:
                facts.append(decision.method)
            facts.append(f"n {decision.n}")
            if decision.decided_at is not None and decision.allowed is not None:
                facts.append(
                    f"decided at {decision.decided_at:.3g} (allowed {decision.allowed:.3g})"
                )
            out.print(f"      [dim]{escape(', '.join(facts))}[/dim]", soft_wrap=True)
            if decision.reason:
                out.print(f"      [dim]{escape(decision.reason)}[/dim]", soft_wrap=True)
    out.print()


def _render_estimates(report: ix.Report, title: str, role: str | None = None) -> None:
    rows = [row for row in report.analysis.estimates if role is None or row.role == role]
    if not rows:
        return
    table = Table(title=title, title_justify="left")
    for column in ("subject", "role", "cell", "metric", "estimate", "interval", "n"):
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(
            escape(row.subject),
            row.role,
            escape(_cell(row.cell)),
            escape(f"{row.metric} {row.statistic} ({row.estimator})"),
            escape(f"{_number(row.value)} {row.unit}".strip()),
            escape(_interval(row.interval)),
            str(row.n),
        )
    out.print(table)


def _render_calibration(report: ix.Report) -> None:
    checks = report.analysis.calibration
    if not checks:
        return
    confidence = f"{checks[0].confidence * 100:.4g}%"
    table = Table(
        title=f"Calibration (the control against itself; {confidence} intervals)",
        title_justify="left",
    )
    for column in ("cell", "metric", "difference", "interval", "n", "stable"):
        table.add_column(column, overflow="fold")
    for check in checks:
        stable = {True: "yes", False: "no", None: "not checked"}[check.stable]
        table.add_row(
            escape(_cell(check.cell)),
            escape(f"{check.metric} {check.statistic} ({check.estimator})"),
            escape(_number(check.difference, check.unit)),
            escape(_interval(check.interval, check.unit)),
            str(check.n),
            stable if check.stable is not False else f"[red]{stable}[/red]",
        )
    out.print(table)


def _render_failures(report: ix.Report) -> None:
    if not report.analysis.failures:
        _field("Failures", "none")
        return
    table = Table(title="Failures", title_justify="left")
    for column in ("subject", "role", "whose", "reason", "count"):
        table.add_column(column)
    for failed in report.analysis.failures:
        table.add_row(
            escape(failed.subject), failed.role, failed.owner, escape(failed.reason),
            str(failed.count),
        )  # fmt: skip
    out.print(table)


def _render_warrant(report: ix.Report) -> None:
    analysis = report.analysis
    lines = list(analysis.method)
    if analysis.family:
        lines.append(
            f"{analysis.family} decision(s) corrected together by Holm's procedure on the "
            "fails side."
        )
    if report.readings == "derived":
        lines.append(
            "The run ended before its sensors read the interactions; the readings were "
            "derived from the record when this report was made."
        )
    elif report.readings == "none":
        lines.append(
            f"No readings: none were recorded, and none were derived here: {report.unread}."
            if report.unread
            else "No readings were recorded."
        )
    _field("Warrant", *lines, *analysis.notes)


def _render_record(report: ix.Report) -> None:
    files = sorted(file.rsplit("/", 1)[-1] for file in report.tables.values())
    _field("Record", report.location, *files)


#: How each report section a genre asks for is printed.
_SECTIONS: dict[str, Callable[[ix.Report], None]] = {
    "estimates": lambda report: _render_estimates(report, "Estimates"),
    "calibration": _render_calibration,
    "baseline": lambda report: _render_estimates(report, "Baseline (the control)", "control"),
    "failures": _render_failures,
    "warrant": _render_warrant,
    "raw": _render_record,
}

#: Sections the outcome lines at the top already state.
_STATED = frozenset({"question", "design", "gate"})


def _render_flags(report: ix.Report) -> None:
    for flag in report.flags:
        out.print(f"[yellow]Flag:[/yellow] {escape(flag)}", soft_wrap=True)


def _share(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _render_confusion(report: ix.Report) -> None:
    for table in report.confusion:
        expected = sorted(table.counts)
        observed = sorted({name for row in table.counts.values() for name in row})
        grid = Table(
            title=f"Expected against observed ({table.sensor}, {table.subject})",
            title_justify="left",
        )
        grid.add_column("expected, by observed", overflow="fold")
        for name in observed:
            grid.add_column(escape(name), overflow="fold")
        for name in expected:
            row = table.counts[name]
            grid.add_row(escape(name), *(str(row.get(seen, 0)) for seen in observed))
        out.print(grid)
        _field(
            "Precision",
            f"{_share(table.precision)} (tp {table.true_positive}, fp {table.false_positive})",
        )
        _field(
            "Recall",
            f"{_share(table.recall)} (fn {table.false_negative}, tn {table.true_negative})",
        )


def _render_screen(report: ix.Report) -> None:
    flagged = [probe for probe in report.screen if probe.screen != "ok"]
    if not report.screen:
        return
    if not flagged:
        _field("Screen", f"each of the {len(report.screen)} probes can discriminate")
        return
    table = Table(title="Probes that cannot discriminate", title_justify="left")
    for column in ("probe", "metric", "screen", "mean", "sd", "n"):
        table.add_column(column, overflow="fold")
    for probe in flagged:
        table.add_row(
            escape(probe.probe),
            escape(probe.metric),
            probe.screen,
            _number(probe.mean),
            _number(probe.sd),
            str(probe.n),
        )
    out.print(table)


def _print_report(report: ix.Report) -> None:
    _print_outcome(report)
    _render_flags(report)
    _print_findings(report)
    for section in report.sections:
        if section in _SECTIONS:
            _SECTIONS[section](report)
        elif section not in _STATED:
            _field(section, "(this ix has no renderer for this section)")
    _render_confusion(report)
    _render_screen(report)


def _finish(report: ix.Report, as_json: bool, path: str | None = None) -> NoReturn:
    """Print the report and exit as it says. Without ``--json``, what the run warned of goes
    to stderr before it, and the exit code, what it means and the next step after it;
    ``path`` (the experiment, for ``ix run``) fills the next step's ``<path>``."""
    if as_json:
        _emit(report.document())
        sys.exit(report.exit_code)
    if report.warnings:
        err.print(f"{len(report.warnings)} warning(s)")
        _print_problems(report.warnings, err, mark="yellow")
    _print_report(report)
    outcome = report.exit
    step = outcome.next.replace("<run directory>", report.location)
    if path is not None:
        step = step.replace("<path>", path)
    err.print(f"[dim]Exit {outcome.code} ({outcome.kind}): {escape(outcome.meaning)}[/dim]")
    err.print(f"[dim]Next: {escape(step)}[/dim]", soft_wrap=True)
    sys.exit(report.exit_code)


_CODE = ix.EXIT_CODES

_RUN_HELP = f"""Carry out an experiment and report what it found.

Starts the subjects, applies the probes and takes the measurements the experiment
declares, one trial at a time unless it writes `trials_in_flight: N`, and records each
trial as it finishes under <experiment directory>/.ix/runs/<run id>/. Then prints the report
(ix.v1.report).

The experiment must be registered first (`ix register`), or the run refused (exit
{_CODE["unregistered"]}); `--exploratory` runs it anyway and gives no verdict, and so does a
run of an experiment that changed since its registration, until the registration is amended
with a reason. `--dry-run` runs the experiment as written with every subject on a
stand-in (an agent session on the mock runtime, replying with each probe's mock_response),
setting aside the options only the real runtime takes: it checks the harness, calls no
model and gives no verdict. `--repeats N` pilots either with N repeats in place of the
file's (never below the genre's minimum); a run that gives a verdict refuses it (exit
{_CODE["usage"]}). `--trials-in-flight N` carries N trials out at once in place of the
file's width, with `--exploratory` or `--dry-run` only (a run that gives a verdict refuses
it, exit {_CODE["usage"]}), and only where every kind in use allows overlap, no deciding
metric is contended and the genre allows it (`trials_in_flight_refused` names what blocks it).
PATH may be - for an experiment on standard input, which
runs only with `--exploratory` or `--dry-run` and keeps its runs under the current
directory. The exit code
is the verdict: {_CODE["ok"]} holds (or no verdict to give), {_CODE["fails"]} fails,
{_CODE["inconclusive"]} inconclusive; a registered run a stand-in answered (`runtime: mock`
written in) gives none and exits {_CODE["simulated"]}, never {_CODE["ok"]}. Ctrl-C stops the
trial in flight, releases what was started and exits {_CODE["run_interrupted"]} with the
partial report; a run the harness could not finish exits {_CODE["run_failed"]}. Either way
the record is kept and `ix report` reads it. Without --json the exit code, what it means and
the next step follow the report on stderr.

`--detach` starts the run in a process of its own and returns as soon as it has opened its
record, printing its status (ix.v1.status: the run's id first, its directory, the process
writing it) and exiting as `ix status` would: {_CODE["running"]} while it runs. A long run
outlasts a tool call. It is refused
before anything starts exactly as a run in the foreground is, with the same exit codes. Then
`ix status <run directory>` says where it stands, `ix events <run directory> --follow`
prints its events until it ends, `ix stop <run directory>` stops it, and `ix report <run
directory>` gives its verdict once it has ended. `--run-id ID` names the run (letters,
digits, '.', '_', '-'); an id a run is kept under already is refused (exit {_CODE["usage"]})."""

_REPORT_HELP = f"""Print the report of a run, computed again from its record. Runs nothing.

RUN is a run's directory (as `ix run` and `ix runs` print it), or an experiment file or
directory, meaning its latest run. Works on a run in any state: finished, stopped, failed,
killed or still running. Exits as `ix run` would have: {_CODE["ok"]} holds (or no verdict to
give), {_CODE["fails"]} fails, {_CODE["inconclusive"]} inconclusive, {_CODE["run_interrupted"]}
for a run that was stopped or killed, {_CODE["run_failed"]} for one the harness could not
finish and {_CODE["simulated"]} for a registered run a stand-in answered; and
{_CODE["running"]} for a run that is still running."""


@main.command(epilog=EXIT_CODES, help=_RUN_HELP)
@_path_argument
@click.option(
    "--exploratory",
    is_flag=True,
    help="Run without holding the run to a registration; such a run gives no verdict",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Run the experiment as written on stand-ins (agent sessions on mock, each probe's "
    "mock_response the reply): checks the harness, calls no model, never a verdict",
)
@click.option(
    "--repeats",
    type=click.IntRange(min=1),
    metavar="N",
    help="A pilot: N repeats in place of the file's, with --exploratory or --dry-run only "
    "(a run that gives a verdict is held to its registered design)",
)
@click.option(
    "--trials-in-flight",
    type=click.IntRange(min=1),
    metavar="N",
    help="Carry N trials out at once in place of the file's trials_in_flight (1 when it "
    "writes none), with --exploratory or --dry-run only; refused where a kind in use or "
    "the genre forbids overlap, or a deciding metric is contended",
)
@click.option(
    "--detach",
    is_flag=True,
    help="Start the run in a process of its own and return once it has started, with its "
    "status; follow it with `ix status`, `ix events --follow`, stop it with `ix stop`",
)
@click.option(
    "--run-id",
    metavar="ID",
    help="The run's id (default: <experiment>-<UTC time>-<4 hex digits>); one a run is kept "
    "under already is refused",
)
@_json_option
def run(
    path: str,
    exploratory: bool,
    dry_run: bool,
    repeats: int | None,
    trials_in_flight: int | None,
    detach: bool,
    run_id: str | None,
    as_json: bool,
) -> None:
    if detach:
        try:
            started = ix.start(
                path,
                exploratory=exploratory,
                dry_run=dry_run,
                repeats=repeats,
                trials_in_flight=trials_in_flight,
                run_id=run_id,
            )
        except ix.IxError as error:
            _fail(error, as_json=as_json)
        _print_status(started, as_json)
        sys.exit(started.exit_code)
    on_event, bar = _progress(as_json)
    try:
        report = ix.run(
            path,
            exploratory=exploratory,
            dry_run=dry_run,
            repeats=repeats,
            trials_in_flight=trials_in_flight,
            run_id=run_id,
            on_event=on_event,
        )
    except ix.IxError as error:
        if bar is not None:
            bar.stop()
        _fail(error, as_json=as_json)
    finally:
        # However the run ended: a defect or a stop outside the run must not print over a
        # live display.
        if bar is not None:
            bar.stop()
    _finish(report, as_json, path)


@main.command(epilog=EXIT_CODES, help=_REPORT_HELP)
@click.argument("run", type=click.Path())
@_json_option
def report(run: str, as_json: bool) -> None:
    try:
        found = ix.report(run)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    _finish(found, as_json)


_RUN_ARGUMENT = click.argument("run", type=click.Path())

_STANDING_HELP = f"""RUN is a run's directory (as `ix run --detach`, `ix run` and `ix runs` print
it), or an experiment file or directory, meaning its latest run. Exits {_CODE["running"]} while
the run runs, {_CODE["ok"]} once it completed (which is no verdict: `ix report` gives that),
{_CODE["run_interrupted"]} for a run that was stopped or whose process died, and
{_CODE["run_failed"]} for one the harness could not finish."""


@main.command(
    epilog=EXIT_CODES,
    help=f"""Say where a run stands. Runs nothing.

Prints ix.v1.status: the run's id, its directory, whether it is running or how it ended,
how many of its planned trials were carried out, the process writing it while it runs
here, and its event log. Read from the record alone, with nothing computed from its
readings, so it is cheap to ask again. {_STANDING_HELP}""",
)
@_RUN_ARGUMENT
@_json_option
def status(run: str, as_json: bool) -> None:
    try:
        found = ix.status(run)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    _print_status(found, as_json)
    sys.exit(found.exit_code)


@main.command(
    epilog=EXIT_CODES,
    help=f"""Print a run's events, oldest first.

Each is a matrix.v1.event (its name, time, component and fields): a trial starting and
ending, a component's progress. They are telemetry and may be dropped; read results from
`ix report`. With --json, one event document a line (JSON lines). With --follow, each event
as it is written, until the run ends however it ends (its process killed included).
--follow on a run still written on another host is refused (exit {_CODE["usage"]}): whether
its process there is alive cannot be read from here, so it might never end; read it without
--follow, or ask `ix status` again later. Ctrl-C stops following and leaves the run
running. At the end it exits as `ix status` would then.
{_STANDING_HELP}""",
)
@_RUN_ARGUMENT
@click.option("--follow", "-f", is_flag=True, help="Go on printing events until the run ends")
@click.option(
    "--json", "as_json", is_flag=True, help="One event document a line; errors as JSON on stderr"
)
def events(run: str, follow: bool, as_json: bool) -> None:
    try:
        for event in ix.events(run, follow=follow):
            if as_json:
                click.echo(json.dumps(event, default=str))
            else:
                out.print(_event_line(event), soft_wrap=True, highlight=False)
        found = ix.status(run)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    if not as_json:
        err.print(f"[dim]run {escape(found.run)} {found.status}[/dim]")
    sys.exit(found.exit_code)


@main.command(
    epilog=EXIT_CODES,
    help=f"""Stop a running run, as Ctrl-C stops one in the foreground.

The trial in flight is stopped and kept as the harness's, everything the run started is
released, and the record says interrupted; `ix report` reads what was kept. Waits up to 30
seconds for the run to end, then prints its status (ix.v1.status); a run still releasing
what it started then says running (exit {_CODE["running"]}): ask `ix status` again. A run
that has ended is left as it is. A run written on another host is refused (exit
{_CODE["usage"]}). {_STANDING_HELP}""",
)
@_RUN_ARGUMENT
@_json_option
def stop(run: str, as_json: bool) -> None:
    try:
        found = ix.stop(run)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    _print_status(found, as_json)
    sys.exit(found.exit_code)


def _print_status(found: ix.Status, as_json: bool) -> None:
    """Print a run's status: the document with ``--json``; else its id first, where it
    stands, and on stderr what that means and what to do next."""
    if as_json:
        _emit(found.document())
        return
    out.print(f"run {escape(found.run)}", soft_wrap=True, highlight=False)
    _field("Directory", found.location)
    _field("Status", found.status + (f" (pid {found.pid} on {found.host})" if found.pid else ""))
    _field("Trials", f"{found.carried_out} of {found.planned} carried out")
    _field("Started", found.started_at or "-")
    if found.ended_at:
        _field("Ended", found.ended_at)
    _field("Events", found.events)
    if found.log:
        _field("Log", found.log)
    outcome = found.exit
    step = outcome.next.replace("<run directory>", found.location)
    err.print(f"[dim]Exit {outcome.code} ({outcome.kind}): {escape(outcome.meaning)}[/dim]")
    err.print(f"[dim]Next: {escape(step)}[/dim]", soft_wrap=True)


def _event_line(event: Mapping[str, Any]) -> str:
    """One event as a line: its time, name, component and fields."""
    fields = event.get("fields") or {}
    said = " ".join(f"{key}={_text(value)}" for key, value in fields.items())
    component = f" [{event['component']}]" if event.get("component") else ""
    return escape(f"{event.get('time', '')} {event.get('name', '')}{component} {said}".rstrip())


@main.command(epilog=EXIT_CODES)
@click.argument("path", type=click.Path(), required=False)
@_json_option
def runs(path: str | None, as_json: bool) -> None:
    """List the runs kept beside an experiment, oldest first.

    PATH is the experiment file or its directory; without it, every experiment's runs under
    the current directory (each .ix/runs of an experiment at most 6 directories below it),
    or under the settings' runs. Each line
    is a run: its id, when it started, whether it was held to a registration (gated),
    exploratory or a dry run, how it ended, its verdict (or the indication of a run that
    gives none), its trials and the commit it ran from. Each run is a directory: pass it to
    `ix report`.
    """
    try:
        found = ix.runs(path)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    if as_json:
        _emit(found.document())
        return
    if not found.runs:
        out.print(f"no runs in {escape(found.location)}", soft_wrap=True)
        return
    out.print(f"[bold]Runs in[/bold] {escape(found.location)}", soft_wrap=True)
    for item in found.runs:
        if item.verdict is not None:
            mark = _MARK[item.verdict]
            outcome = f"[{mark}]{item.verdict}[/{mark}]"
        elif item.indication is not None:
            outcome = f"indication {item.indication}"
        else:
            outcome = "the harness, not the agent" if item.mode == "dry" else "no verdict"
        checkout = (
            f"commit {item.commit[:12]}{' (dirty)' if item.dirty else ''}"
            if item.commit
            else "no commit"
        )
        out.print(
            f"  {escape(item.run)}  {escape(item.started_at)}  {checkout}  {item.mode}  "
            f"{item.status}  {outcome}  {item.carried_out}/{item.planned} trials",
            soft_wrap=True,
            highlight=False,
        )
    err.print(f"[dim]Next: ix report {escape(found.runs[-1].location)}[/dim]", soft_wrap=True)


# --- genres and kinds --------------------------------------------------------------------

_lab_option = click.option(
    "--lab",
    type=click.Path(),
    help="A directory: also list what its genres/ and kinds/ directories hold",
)


def _print_rejected(rejected: Mapping[str, Iterable[ix.Problem]]) -> None:
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
    if as_json:
        _emit(found.document())
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
    err.print(
        "[dim]Each genre's type URL is ix.v1.genre.<name>; `ix --skill -r genres` has the "
        "detail.[/dim]"
    )
    _print_rejected(found.rejected)


@main.command(epilog=EXIT_CODES)
@_lab_option
@_json_option
def kinds(lab: str | None, as_json: bool) -> None:
    """List the subject kinds, probe kinds, sensors and environment kinds.

    Each kind declares its options (required ones marked *), the facets it needs and offers,
    for an environment the keys each service it brings up is bound with (`service_keys`:
    `{<service>.url}`), and its `overlap`: whether two trials in flight may use it at once
    (`allowed` or `forbidden`). `runs` says whether this ix has an adapter for the kind: one
    without can be validated, planned and registered, not run. The metrics are listed under
    the sensor that provides each, with whether a model judges it (`judged`: it decides no
    verdict) and whether what runs beside a trial moves it (`contended`: it decides no run
    with trials_in_flight above 1).
    """
    try:
        found = ix.kinds(lab)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    if as_json:
        _emit(found.document())
        return
    tables: dict[str, Table] = {}
    for family in ("subject", "probe", "sensor", "environment"):
        # A table is as wide as its own longest option, so each still fits in 80 columns.
        widest = max(
            (len(name) + 1 for kind in found.of(family) for name in kind.options), default=1
        )
        table = Table(title=f"{family.capitalize()} kinds", title_justify="left")
        table.add_column("kind", no_wrap=True)
        table.add_column("options", overflow="fold", min_width=widest)
        table.add_column("needs > offers")
        if family == "environment":
            table.add_column("service keys", no_wrap=True)
        table.add_column("overlap", no_wrap=True)
        table.add_column("runs", no_wrap=True)
        tables[family] = table
    # Each sensor heads its metrics: a column of its own would not leave 80 for the rest.
    metrics = Table(title="Metrics", title_justify="left")
    for column in ("sensor / metric", "unit", "shape", "better", "judged", "contended"):
        metrics.add_column(column, no_wrap=column != "unit")
    environments = {kind.name: kind.service_keys for kind in found.of("environment")}
    for kind in found.kinds.values():
        label = kind.label
        # One option a line: a name folded across lines could not be read or searched for.
        # Its default is in `--json` and `--skill -r kinds`: beside the name it would not fit.
        options = "\n".join(
            f"{name}{'*' if option.required else ''}" for name, option in kind.options.items()
        )
        facets = f"{', '.join(kind.needs) or '-'} > {', '.join(kind.offers) or '-'}"
        keys = (
            [escape(", ".join(environments[kind.name]) or "-")] if kind.name in environments else []
        )
        tables[kind.family].add_row(
            escape(label),
            escape(options or "-"),
            escape(facets),
            *keys,
            kind.overlap,
            "yes" if kind.name in found.runnable else "no",
        )
    for sensor in found.of("sensor"):
        metrics.add_row(f"[bold]{escape(sensor.label)}[/bold]")
        for index, metric in enumerate(sensor.metrics, start=1):
            metrics.add_row(
                f"  {escape(metric.name)}",
                escape(metric.unit or "-"),
                metric.shape,
                metric.direction,
                "yes" if metric.judged else "no",
                "yes" if metric.contended else "no",
                end_section=index == len(sensor.metrics),
            )
    for table in tables.values():
        out.print(table)
    out.print(metrics)
    err.print(
        "[dim]options: * marks a required one. overlap: whether two trials in flight may use "
        "the kind at once. runs: whether this ix has an adapter for the kind that builds "
        "here. One without can be validated, planned and registered, and `ix run` refuses "
        "it. contended: the metric moves with what runs beside a trial, so it decides no run "
        "with trials_in_flight above 1.[/dim]"
    )
    _print_rejected(found.rejected)
    for name, why in found.unbuildable.items():
        err.print(f"[red]Adapter cannot be built:[/red] {escape(name)}: {escape(why)}")
    for line in found.quarantined:
        err.print(f"[red]Extension not loaded:[/red] {escape(line)} [dim](matrix catalog)[/dim]")


# --- mutation (provisional) --------------------------------------------------------------


@main.group(epilog=EXIT_CODES)
def mutants() -> None:
    """Mutation testing: draw the mutants of a scope, and read what a run found.

    An experiment of genre mutation-score (or mutation-regress) has subjects of kind
    test-suite and a probe per mutant. `ix mutants sample` draws the mutants, pins each as a
    probe file and makes each suite's selection map (`ix mutants map` makes the map alone, for
    probe files another tool wrote); validate, plan, register and run it as any experiment;
    `ix mutants read` lists the survivors and the tests the run found no use for.
    Provisional: the names may change in a minor release, said in the changelog.
    """


@mutants.command(name="sample", epilog=EXIT_CODES)
@_path_argument
@click.option("--n", "n", type=int, help="How many mutants to draw (default 100)")
@click.option("--seed", type=int, help="The seed the sample is drawn from (default 1)")
@click.option(
    "--exclude",
    type=click.Path(),
    help="An experiment an earlier sample was drawn into: draw none of its mutants",
)
@click.option("--source", help="The mutant source's type URL, when more than one is installed")
@click.option(
    "--timeout",
    "timeout_s",
    type=float,
    help="Seconds the baseline run under coverage may take (default 3600)",
)
@_json_option
def mutants_sample(
    path: str,
    n: int | None,
    seed: int | None,
    exclude: str | None,
    source: str | None,
    timeout_s: float | None,
    as_json: bool,
) -> None:
    """Draw the mutants of the test-suite subjects' scope and make their selection maps.

    Asks the mutant source for every mutant of each scope file, keeps those that parse and
    change the code, draws N from SEED, and runs each suite's baseline once in a fresh copy
    under coverage.py's per-test contexts (pytest-cov in the suite's own environment). Writes
    probes/m-<id>.yaml, each subject's map and mutation/population.json beside PATH, and
    nothing when it refuses: a baseline that does not pass, a probes/ folder that holds
    files, no mutant source installed (install ix-cosmic-ray beside ix). Prints
    ix.v1.mutation-sample: the population by operator, what was left out and why, the
    uncovered mutants drawn and the test time the run will take.
    """
    try:
        found = ix.mutants.sample(
            path, n=n, seed=seed, exclude=exclude, source=source, timeout_s=timeout_s
        )
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    if as_json:
        _emit(found)
        return
    settings = found["settings"]
    out.print(
        f"[bold]Sampled[/bold] {found['sampled']} of {found['population']} mutants "
        f"{'(every one: a census)' if found['census'] else ''}from "
        f"{escape(found['source']['type_url'])}",
        highlight=False,
    )
    _field(
        "Drawn with",
        ", ".join(f"{key} {value['value']} ({value['from']})" for key, value in settings.items()),
    )
    _field("By operator", _by_operator(found["by_operator"]))
    dropped = {reason: count for reason, count in found["dropped"].items() if count}
    _field(
        "Left out",
        ", ".join(f"{reason} {count}" for reason, count in dropped.items()) or "none",
    )
    if found["unmade"]:
        _field(
            "Not made",
            ", ".join(
                f"{op} {item['count']} ({item['reason']})" for op, item in found["unmade"].items()
            ),
        )
    for subject in found["subjects"]:
        baseline = subject["baseline"]
        _field(
            f"Subject {subject['subject']}",
            f"baseline passed: {baseline['tests']} tests in {baseline['duration_s']:g}s; "
            f"{subject['uncovered']} drawn mutant(s) no test runs (confirm: "
            f"{subject['confirm']}); about {subject['estimated_s']:g}s of test time to run"
            + (
                f", {subject['worst_s']:g}s if every covered mutant survives"
                if subject["worst_s"] != subject["estimated_s"]
                else ""
            ),
        )
    err.print(f"[dim]{escape(found['estimate_note'])}[/dim]")
    err.print(f"[dim]Next: ix validate {escape(path)}, then ix plan, ix register, ix run[/dim]")


#: How many operators a sample's ``By operator`` line names; ``--json`` has every one.
_OPERATORS_SHOWN = 5


def _by_operator(counts: Mapping[str, int]) -> str:
    """The operators that made the most of a population, most first, and how many more."""
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    shown = ", ".join(f"{op} {count}" for op, count in ranked[:_OPERATORS_SHOWN])
    rest = len(ranked) - _OPERATORS_SHOWN
    return shown + (f", and {rest} more (--json lists every one)" if rest > 0 else "")


@mutants.command(name="map", epilog=EXIT_CODES)
@_path_argument
@click.option(
    "--timeout",
    "timeout_s",
    type=float,
    help="Seconds the baseline run under coverage may take (default 3600)",
)
@_json_option
def mutants_map(path: str, timeout_s: float | None, as_json: bool) -> None:
    """Make each test-suite subject's selection map, and nothing else.

    For probe files of kind mutant another tool wrote: runs each suite's baseline once in a
    fresh copy under coverage.py's per-test contexts (pytest-cov in the suite's own
    environment) and writes the map where the subject's map option names. Needs no mutant
    source. Prints ix.v1.mutation-maps.
    """
    try:
        found = ix.mutants.map(path, timeout_s=timeout_s)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    if as_json:
        _emit(found)
        return
    for subject in found["subjects"]:
        baseline = subject["baseline"]
        _field(
            f"Subject {subject['subject']}",
            f"baseline passed: {baseline['tests']} tests in {baseline['duration_s']:g}s; "
            f"map {escape(subject['map'])}",
        )
    err.print(f"[dim]Next: ix validate {escape(path)}, then ix plan, ix register, ix run[/dim]")


@mutants.command(name="read", epilog=EXIT_CODES)
@_RUN_ARGUMENT
@click.option("--all", "every", is_flag=True, help="List every survivor and candidate, not 50")
@_json_option
def mutants_read(run: str, every: bool, as_json: bool) -> None:
    """Read a mutation run: its survivors, and the tests it found no use for.

    RUN is a run's directory, or an experiment for its latest run. Each survivor is an
    assertion gap (tests ran its statement and passed: they are named) or a coverage gap (no
    test ran it). Pruning candidates come from the kill matrix of the subject (or the control):
    tests outside a greedy keep set, removable together as far as these mutants show, with
    the deselect list a mutation-regress variant leaves out. A recommendation only: confirm it
    on a fresh, disjoint sample. Prints ix.v1.mutants-reading; 50 of each unless --all, and
    says when it cut.
    """
    try:
        found = ix.mutants.read(run, every=every)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    if as_json:
        _emit(found)
        return
    for subject in found["subjects"]:
        rate = subject["kill_rate"]
        unread = ", ".join(f"{reason} {count}" for reason, count in subject["unread"].items())
        out.print(
            f"[bold]{escape(subject['subject'])}[/bold] ({subject['role']}): killed "
            f"{subject['killed']} of {subject['read']} read"
            + ("" if rate is None else f" ({rate:.1%})")
            + f"; unread {unread or 'none'}",
            highlight=False,
        )
    if not found["survivors"]:
        out.print("No survivors: every mutant read was killed.")
    else:
        out.print(f"[bold]Survivors[/bold] ({found['survivors_total']})")
    for survivor in found["survivors"]:
        tests = survivor["tests"]
        more = survivor["tests_count"] - len(tests)
        named = ", ".join(tests) + (f" and {more} more" if more > 0 else "")
        out.print(
            f"  {escape(survivor['file'])}:{survivor['line']}  {escape(survivor['change'])}  "
            f"[dim]{escape(survivor['operator'])}[/dim]  {survivor['class']}"
            + (
                ": its line runs at import, so every test ran it"
                if survivor["at_import"]
                else f": strengthen {escape(named)}"
                if survivor["class"] == "assertion_gap"
                else ": no test runs its statement"
            )
            + f"  [dim]{survivor['probe']}[/dim]",
            highlight=False,
        )
    if found["truncated"]:
        err.print(
            f"[dim]{found['limit']} of {found['survivors_total']} shown: --all for every one[/dim]"
        )
    pruning = found["pruning"]
    if pruning is not None:
        out.print(
            f"[bold]Pruning candidates[/bold] of {escape(pruning['subject'])}: "
            f"{pruning['candidates_total']} of {pruning['judged']} judged tests "
            f"(keep {pruning['keep']}; {pruning['not_judged']} not judged)",
            highlight=False,
        )
        for candidate in pruning["candidates"]:
            what = (
                "kills nothing"
                if candidate["zero_kill"]
                else f"kills {len(candidate['kills'])}, each also killed by the keep set"
            )
            outside = candidate["also_runs"]
            out.print(
                f"  {escape(candidate['test'])}: {what}"
                + (f"; also runs {len(outside)} file(s) outside the scope" if outside else ""),
                highlight=False,
            )
        if pruning["truncated"]:
            err.print("[dim]candidates cut at 50: --all for every one[/dim]")
        err.print(f"[dim]{escape(pruning['note'])}[/dim]")
    for caveat in found["caveats"]:
        err.print(f"[dim]- {escape(caveat)}[/dim]")


if __name__ == "__main__":
    main()

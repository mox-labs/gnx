"""The ``ix`` command line: validate, plan, register, deviations, run, report, runs, genres,
kinds.

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
warns of (export asked for without matrix's ``otel`` extra) is in every ``--json``
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

    ix checks the file, shows the decision a run would inform and what it would cost,
    freezes the design before anything runs, then runs it and reports what the readings say
    about that decision.

    \b
    ix --skill                  the skill an agent reads; ix --skill -r NAME a reference
    ix validate lab/proxy       every problem at once, nothing run
    ix plan lab/proxy           the decision, the trials, the effects
    ix register lab/proxy       freeze the design before a run
    ix deviations lab/proxy     what changed since registration
    ix run lab/proxy            carry it out; the exit code is the verdict
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
    out.print(f"[bold]{label + ':':<14}[/bold]{escape(text)}")
    for line in more:
        out.print(f"{'':<14}{escape(line)}")


#: Where a design value came from, as a plan's lines say it.
_SOURCED = {"experiment": "set in the experiment", "genre": "the genre's default", "ix": "ix's"}


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
        _field("Margin", f"{_text(margin.value)}, {_SOURCED[margin.source]}: {margin.note}")
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
    if plan.sensors:
        _field(
            "Sensors",
            *(
                f"{sensor.label}: {', '.join(sensor.metrics) or '(no metric named)'}"
                for sensor in plan.sensors
            ),
        )


def _print_plan_cost(plan: ix.Plan) -> None:
    by_effect: dict[tuple[str, str], list[str]] = {}
    for effect in plan.effects:
        by_effect.setdefault((effect.effect, effect.detail), []).append(effect.source)
    lines = [
        f"{name}{': ' + detail if detail else ''}  ({', '.join(sources)})"
        for (name, detail), sources in by_effect.items()
    ]
    _field("Effects", *(lines or ["none"]))
    _field("Registration", plan.registration.text())
    _field("Sensitivity", plan.sensitivity.text)
    blockers = [
        *(f"this ix has no adapter for {kind}" for kind in plan.unimplemented),
        *(f"cannot start on this host: {reason}" for reason in plan.unstartable),
    ]
    if not plan.runnable:
        _field("Runnable", "no", *blockers)
    out.print()


def _print_plan_design(plan: ix.Plan, path: str) -> None:
    design = Table(title="Design", title_justify="left")
    for column in ("setting", "value", "from"):
        design.add_column(column)
    footnotes = []
    for value in plan.provenance:
        design.add_row(escape(value.path), escape(_text(value.value)), value.source)
        if value.note and value.path != "margin":  # the margin's is on its own line above
            footnotes.append(f"{value.path}: {value.note}")
    out.print(design)
    for note in footnotes:
        out.print(f"[dim]{escape(note)}[/dim]")
    for note in plan.notes:
        out.print(f"[dim]The genre's design is {escape(note)}.[/dim]")
    next_step = {
        "none": f"ix register {path}",
        "matches": f"ix run {path}",
        "deviations": f"ix deviations {path}",
    }[plan.registration.status]
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
@_json_option
def plan(path: str, as_json: bool) -> None:
    """Show the decision a run would inform and what it would cost. Runs nothing.

    States the question, the hypothesis rendered from the genre, the guardrails and the form
    of the verdict; then the subjects with their roles, the trials with their arithmetic, the
    estimated duration, the effects and where the experiment stands against its
    registration. The plan's digest changes when a value a run would use changes.

    PATH is the experiment file, a directory that holds experiment.yaml, or - for standard
    input (which has no registration).
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
    _field("Run", f"{report.status}; {report.trials.text()}")
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
    out.print()


def _stood_in(report: ix.Report) -> bool:
    """Whether a stand-in answered: the run checked the harness, never the subject."""
    return report.dry_run or report.simulated != "none"


def _print_findings(report: ix.Report) -> None:
    findings = report.analysis.findings
    if not findings:
        return
    if _stood_in(report):
        out.print(
            "[bold]Findings on mock replies[/bold] [yellow](the harness, not the agent)[/yellow]"
        )
    else:
        out.print("[bold]Findings[/bold]")
    for finding in findings:
        mark = _MARK[finding.outcome]
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
        lines.append("No readings: none were recorded, and none could be derived here.")
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
declares, one trial at a time, and records each trial as it finishes under
<experiment directory>/.ix/runs/<run id>/. Then prints the report (ix.v1.report).

The experiment must be registered first (`ix register`), or the run refused (exit
{_CODE["unregistered"]}); `--exploratory` runs it anyway and gives no verdict, and so does a
run of an experiment that changed since its registration, until the registration is amended
with a reason. `--dry-run` runs the experiment as written with every subject on a
stand-in (an agent session on the mock runtime, replying with each probe's mock_response),
setting aside the options only the real runtime takes: it checks the harness, calls no
model and gives no verdict. `--repeats N` pilots either with N repeats in place of the
file's (never below the genre's minimum); a run that gives a verdict refuses it (exit
{_CODE["usage"]}). PATH may be - for an experiment on standard input, which
runs only with `--exploratory` or `--dry-run` and keeps its runs under the current
directory. The exit code
is the verdict: {_CODE["ok"]} holds (or no verdict to give), {_CODE["fails"]} fails,
{_CODE["inconclusive"]} inconclusive; a registered run a stand-in answered (`runtime: mock`
written in) gives none and exits {_CODE["simulated"]}, never {_CODE["ok"]}. Ctrl-C stops the
trial in flight, releases what was started and exits {_CODE["run_interrupted"]} with the
partial report; a run the harness could not finish exits {_CODE["run_failed"]}. Either way
the record is kept and `ix report` reads it. Without --json the exit code, what it means and
the next step follow the report on stderr."""

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
@_json_option
def run(path: str, exploratory: bool, dry_run: bool, repeats: int | None, as_json: bool) -> None:
    on_event, bar = _progress(as_json)
    try:
        report = ix.run(
            path, exploratory=exploratory, dry_run=dry_run, repeats=repeats, on_event=on_event
        )
    except ix.IxError as error:
        if bar is not None:
            bar.stop()
        _fail(error, as_json=as_json)
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


@main.command(epilog=EXIT_CODES)
@click.argument("path", type=click.Path(), required=False)
@_json_option
def runs(path: str | None, as_json: bool) -> None:
    """List the runs kept beside an experiment, oldest first.

    PATH is the experiment file or its directory; without it, every experiment's runs under
    the current directory. Each line is a run: its id, when it started, whether it was held
    to a registration (gated), exploratory or a dry run, how it ended, its verdict (or the
    indication of a run that gives none), its trials and what it spent. Each run is a
    directory: pass it to `ix report`.
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
        out.print(
            f"  {escape(item.run)}  {escape(item.started_at)}  {item.mode}  {item.status}  "
            f"{outcome}  {item.carried_out}/{item.planned} trials",
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
    its effects and, for a sensor, the metrics it provides. `runs` says whether this ix has
    an adapter for the kind: one without can be validated, planned and registered, not run.
    """
    try:
        found = ix.kinds(lab)
    except ix.IxError as error:
        _fail(error, as_json=as_json)
    if as_json:
        _emit(found.document())
        return
    widest = max(
        (len(name) + 1 for kind in found.kinds.values() for name in kind.options), default=1
    )
    tables: dict[str, Table] = {}
    for family in ("subject", "probe", "sensor", "environment"):
        table = Table(title=f"{family.capitalize()} kinds", title_justify="left")
        table.add_column("kind", no_wrap=True)
        table.add_column("options (* required)", overflow="fold", min_width=widest)
        table.add_column("needs > offers")
        table.add_column("effects")
        table.add_column("runs", no_wrap=True)
        tables[family] = table
    metrics = Table(title="Metrics", title_justify="left")
    for column in ("metric", "sensor", "unit", "shape", "better", "judged"):
        metrics.add_column(column, no_wrap=column != "unit")
    for kind in found.kinds.values():
        label = kind.label
        # One option a line: a name folded across lines could not be read or searched for.
        options = "\n".join(
            f"{name}{'*' if option.required else ''}" for name, option in kind.options.items()
        )
        facets = f"{', '.join(kind.needs) or '-'} > {', '.join(kind.offers) or '-'}"
        tables[kind.family].add_row(
            escape(label),
            escape(options or "-"),
            escape(facets),
            escape("\n".join(kind.effects) or "-"),
            "yes" if kind.name in found.runnable else "no",
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
    for table in tables.values():
        out.print(table)
    out.print(metrics)
    err.print(
        "[dim]runs: whether this ix has an adapter for the kind. One without can be "
        "validated, planned and registered, and `ix run` refuses it.[/dim]"
    )
    _print_rejected(found.rejected)
    for line in found.quarantined:
        err.print(f"[red]Extension not loaded:[/red] {escape(line)} [dim](matrix catalog)[/dim]")


if __name__ == "__main__":
    main()

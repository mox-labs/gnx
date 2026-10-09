"""CLI adapter for ix — Intelligent Experimentation.

Thin driving adapter over the composition root. Output discipline (clig.dev): the primary
output — a results table, or JSON with ``--format json`` — goes to stdout; banners,
progress and errors go to stderr, so ``ix results x --format json | jq`` always parses.

Exit codes (one scheme, shared with hardline):

    0  success
    1  failure not otherwise classified (an engine error, a runtime error)
    2  usage: a bad flag or argument (click)
    3  config: an invalid experiment, a failed ``experiment validate``, ``experiment list``
       with an invalid experiment, or a run refused for starting live sessions implicitly
    4  not found: a lab, experiment, subject or saved results
    5  transient: a runtime failure whose cause is retryable (rate limit, timeout) — retry
    6  auth: a runtime failure the provider refused for credentials

With ``--format json`` an error is one line of JSON on stderr::

    {"error": {"kind": "config|not_found|engine|transient|auth|unknown",
               "message": "...", "fix": "<next command>" | null, "problems": [...]}}

``problems`` appears on ``experiment validate`` only. Usage errors (2) are click's prose.
Every JSON document on stdout names its shape in a ``schema`` field (``ix.v1/results``, …).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from typing import TYPE_CHECKING, Any, NoReturn

import rich_click as click
from matrix import AgentRuntimeError, MatrixError
from matrix import ConfigError as MatrixConfigError
from matrix import NotFoundError as MatrixNotFoundError
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from ix import __version__
from ix.domain.errors import (
    ConfigError,
    EngineError,
    IxError,
    LabNotFoundError,
    MissingExtraError,
    NotFoundError,
    ResultsNotFoundError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from ix.domain.types import Reading, Subject, Trial
    from ix.eval.models import Comparison, ExperimentConfig, ExperimentResults, ProbeResult

out = Console()
err = Console(stderr=True)

EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3
EXIT_NOT_FOUND = 4
EXIT_TRANSIENT = 5
EXIT_AUTH = 6

_EXIT_CODES = """\b
Exit codes: 0 ok · 1 failure · 2 usage · 3 config (incl. failed validate, refused live run)
· 4 not found · 5 transient, retry · 6 auth.
With --format json, an error is one JSON line on stderr: {"error": {"kind", "message", "fix"}}.
"""


# --- Output helpers ------------------------------------------------------------------


def _status_style(status: str) -> str:
    return {
        "excellent": "[bold green]EXCELLENT[/bold green]",
        "good": "[green]GOOD[/green]",
        "needs_work": "[yellow]NEEDS WORK[/yellow]",
        "poor": "[bold red]POOR[/bold red]",
    }.get(status, status.upper())


_BARS = "▁▂▃▄▅▆▇█"


def _strip(scores: tuple[float, ...]) -> str:
    """One character per trial, its height the trial's score — 15 trials fit in 15 columns."""
    return "".join(_BARS[round(max(0.0, min(1.0, s)) * (len(_BARS) - 1))] for s in scores)


def _short_path(path: str) -> str:
    """Relative to the working directory when under it — the full path wraps at 80 columns."""
    from pathlib import Path

    p = Path(path)
    try:
        return str(p.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return path


def _pm(value: float | None) -> str:
    return f" ± {value:.1%}" if value is not None else ""


def _print_metrics(results: ExperimentResults) -> None:
    out.print()
    out.rule(f"[bold]{results.experiment_name}[/bold] · subject [cyan]{results.subject}[/cyan]")
    out.print()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    table.add_row("Pass rate", f"{results.pass_rate:.1%}{_pm(results.pass_rate_stderr)}")
    table.add_row("Mean score", f"{results.mean_score:.1%}{_pm(results.mean_score_stderr)}")
    table.add_row("Min / max", f"{results.min_score:.1%} / {results.max_score:.1%}")
    table.add_row("Probes", f"{results.n_probes}  (± is 1 standard error over probes)")
    if results.repeats > 1:
        rates = ", ".join(f"{r:.1%}" for r in results.per_run_pass_rates)
        table.add_row("Per-repeat rates", rates)
        if results.noise_floor_sd is not None:
            table.add_row("Noise floor, pass rate (sd)", f"{results.noise_floor_sd:.1%}")
        if results.score_noise_floor_sd is not None:
            table.add_row("Noise floor, mean score (sd)", f"{results.score_noise_floor_sd:.1%}")
    table.add_row("Engine", results.engine)
    table.add_row("Answered by", ", ".join(results.families) or "-")
    if results.sensor_faults:
        table.add_row(
            "Sensor faults",
            f"[red]{results.sensor_faults}[/red]  (sensor crashed; scored as failures)",
        )
    if results.trials_log:
        table.add_row("Trials", _short_path(results.trials_log))
    out.print(table)

    if results.probe_results:
        out.print()
        out.print(_probe_table(results.probe_results, out.width))

    if results.confusion_matrix:
        out.print()
        out.rule("[bold]Confusion matrix[/bold]")
        cm_table = Table(show_header=True, header_style="bold")
        cm_table.add_column("Expected")
        cm_table.add_column("Activated")
        cm_table.add_column("Count")
        for expected, activations in sorted(results.confusion_matrix.items()):
            for activated, count in sorted(activations.items()):
                style = "green" if expected == activated else "red"
                cm_table.add_row(expected, f"[{style}]{activated}[/{style}]", str(count))
        out.print(cm_table)

    out.print()
    if results.measured_a_model:
        out.print(f"Status: {_status_style(results.status)}")
    else:
        out.print(f"Status: [yellow]harness only[/yellow] ({_status_style(results.grade)})")
    if not results.measured_a_model:
        out.print(
            "[yellow]No real model answered: this checks the harness, not the subject.[/yellow]"
        )
    out.print()


def _probe_table(probe_results: tuple[ProbeResult, ...], width: int) -> Table:
    """Probe, verdict, score and one bar per trial always fit; Details takes what is left.

    Sized here rather than left to rich: rich shrinks no-wrap columns before a long one,
    and at 80 columns that collapsed Score and Trials to an ellipsis.
    """
    probe_w = min(32, max(len(pr.probe_id) for pr in probe_results))
    trials_w = max(6, max(len(pr.trial_scores) for pr in probe_results))
    # Each column costs its width + 2 padding + 1 border; one more border closes the table.
    fixed = (probe_w + 3) + (7 + 3) + (6 + 3) + (trials_w + 3) + 1
    details_w = width - fixed - 3

    table = Table(show_header=True, header_style="bold")
    table.add_column("Probe", width=probe_w, overflow="ellipsis", no_wrap=True)
    table.add_column("Verdict", width=7, no_wrap=True)
    table.add_column("Score", width=6, justify="right", no_wrap=True)
    table.add_column("Trials", width=trials_w, no_wrap=True)
    if details_w >= 12:
        table.add_column("Details", width=min(details_w, 60), overflow="ellipsis", no_wrap=True)
    for pr in probe_results:
        row = [
            pr.probe_id,
            "[green]PASS[/green]" if pr.passed else "[yellow]FAIL[/yellow]",
            f"{pr.score:.0%}",
            _strip(pr.trial_scores),
        ]
        if details_w >= 12:
            row.append(pr.details[0] if pr.details else "")
        table.add_row(*row)
    return table


def _print_comparison(c: Comparison) -> None:
    out.print()
    out.rule(f"[bold]{c.experiment}[/bold] · [cyan]{c.a}[/cyan] → [cyan]{c.b}[/cyan]")
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    table.add_row("Shared probes", str(c.n))
    table.add_row("Pass rate", f"{c.a} {c.pass_rate_a:.1%} → {c.b} {c.pass_rate_b:.1%}")
    ci = f"  95% CI [{c.ci95[0]:+.1%}, {c.ci95[1]:+.1%}]" if c.ci95 else ""
    table.add_row("Mean score Δ (B−A)", f"{c.mean_delta:+.1%}{_pm(c.delta_stderr)}{ci}")
    table.add_row(
        "Verdicts flipped", f"{c.a} only: {c.a_only_passed} · {c.b} only: {c.b_only_passed}"
    )
    floor = f"{c.noise_floor_sd:.1%}" if c.noise_floor_sd is not None else "not measured"
    table.add_row(
        "Noise floor, mean score (sd)",
        floor + ("" if c.noise_floor_sd is not None else " (repeats: 1)"),
    )
    if c.unmatched:
        table.add_row("Unmatched probes", ", ".join(c.unmatched))
    if c.sensor_faults:
        table.add_row("Sensor faults", f"[red]{c.sensor_faults}[/red]  (no winner is called)")
    out.print(table)
    if c.warning:
        out.print(f"[yellow]Warning:[/yellow] {c.warning}")
    label = {
        "b_better": f"[green]{c.b} better[/green]",
        "a_better": f"[green]{c.a} better[/green]",
        "inconclusive": "[yellow]inconclusive[/yellow] — the difference is within the uncertainty",
    }[c.verdict]
    out.print()
    out.print(f"Verdict: {label}")
    out.print()


@contextlib.contextmanager
def _trial_progress(
    experiment: ExperimentConfig,
) -> Iterator[Callable[[int, Trial, list[Reading]], None]]:
    """A live bar on stderr while trials run — only on a terminal; piped stderr stays quiet.

    It advances as each trial is measured and counts passes and failures, so a long live
    run shows movement between the per-repeat lines instead of nothing.
    """
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        TextColumn,
        TimeElapsedColumn,
    )

    total = len(experiment.probes) * experiment.trials * experiment.repeats
    progress = Progress(
        TextColumn("  {task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn(
            "[green]{task.fields[passed]} pass[/green] · "
            "[yellow]{task.fields[failed]} fail[/yellow]"
        ),
        TimeElapsedColumn(),
        console=err,
        transient=True,
        disable=not err.is_terminal,
    )
    bar = progress.add_task("trials", total=total, passed=0, failed=0)
    passed = failed = 0

    def on_trial(repeat: int, trial: Trial, readings: list[Reading]) -> None:
        nonlocal passed, failed
        if readings and all(r.passed for r in readings):
            passed += 1
        else:
            failed += 1
        label = f"repeat {repeat + 1}/{experiment.repeats}" if experiment.repeats > 1 else "trials"
        progress.update(bar, advance=1, description=label, passed=passed, failed=failed)

    with progress:
        yield on_trial


# --- Errors and JSON -------------------------------------------------------------------


def _json_mode() -> bool:
    """Whether the running command was asked for ``--format json``."""
    ctx = click.get_current_context(silent=True)
    return ctx is not None and ctx.params.get("fmt") == "json"


def _emit(doc: dict[str, Any] | list[dict[str, Any]]) -> None:
    click.echo(json.dumps(doc, indent=2, default=str))


def _classify(e: BaseException) -> tuple[str, int]:
    """(kind, exit code) for an error — what the caller should do next, not where it arose."""
    if isinstance(e, (NotFoundError, MatrixNotFoundError)):
        return "not_found", EXIT_NOT_FOUND
    if isinstance(e, (ConfigError, MissingExtraError, MatrixConfigError)):
        return "config", EXIT_CONFIG
    if isinstance(e, AgentRuntimeError):
        # hardline's BackendError carries `reason` and `retryable`; read them off the cause
        # chain without importing hardline. A runtime whose cause carries neither (the
        # claude-sdk runtime reports HTTP status only in prose) stays unclassified.
        cause = e.__cause__
        while cause is not None:
            if getattr(cause, "reason", None) == "auth":
                return "auth", EXIT_AUTH
            if getattr(cause, "retryable", False) is True:
                return "transient", EXIT_TRANSIENT
            cause = cause.__cause__
        return "unknown", EXIT_FAILURE
    if isinstance(e, EngineError):
        return "engine", EXIT_FAILURE
    return "unknown", EXIT_FAILURE


def _cli_error(
    message: str,
    fix: str | None = None,
    *,
    kind: str = "unknown",
    code: int = EXIT_FAILURE,
    problems: list[str] | None = None,
) -> NoReturn:
    """Report a failure and exit. Under ``--format json``: one JSON line on stderr."""
    if _json_mode():
        body: dict[str, Any] = {"kind": kind, "message": message, "fix": fix}
        if problems is not None:
            body["problems"] = problems
        click.echo(json.dumps({"error": body}), err=True)
    else:
        err.print(f"[red]Error:[/red] {escape(message)}")
        for problem in problems or ():
            err.print(f"  [red]-[/red] {escape(problem)}")
        if fix:
            err.print(f"  [dim]Fix: {escape(fix)}[/dim]")
    raise SystemExit(code)


def _fail(e: BaseException, fix: str | None = None) -> NoReturn:
    kind, code = _classify(e)
    _cli_error(str(e), fix, kind=kind, code=code)


def _resolve_lab(lab_name: str | None) -> Path:
    from pathlib import Path

    from ix.config.settings import find_lab, find_project_root, is_lab

    try:
        return find_lab(lab_name)
    except LabNotFoundError:
        root = find_project_root()
        labs = [d.name for d in sorted(root.iterdir()) if d.is_dir() and is_lab(d)]
        message = (
            f"lab {lab_name!r} not found under {root}"
            if lab_name
            else f"no lab found at or above {Path.cwd()}"
        )
        create = f"ix lab init {lab_name or '<name>'}"
        fix = f"--lab {' | '.join(labs)}, or create one: {create}" if labs else create
        _cli_error(message, fix, kind="not_found", code=EXIT_NOT_FOUND)


def _require_experiment(lab_path: Path, name: str) -> Path:
    exp_path = lab_path / name
    if not (exp_path / "experiment.yaml").exists():
        _cli_error(
            f"experiment {name!r} not found in {lab_path}",
            f"ix experiment list --lab {lab_path.name}",
            kind="not_found",
            code=EXIT_NOT_FOUND,
        )
    return exp_path


def _load(name: str, lab_name: str | None) -> tuple[Path, Path, ExperimentConfig]:
    """(lab, experiment dir, config) — or a clean error naming what is missing."""
    from ix.composition import create_store

    lab_path = _resolve_lab(lab_name)
    exp_path = _require_experiment(lab_path, name)
    try:
        return lab_path, exp_path, create_store(lab=lab_path).load_experiment(exp_path)
    except (IxError, MatrixError) as e:
        _fail(e)  # the message names the file and key; validate would only repeat it


def _results_doc(results: ExperimentResults) -> dict[str, Any]:
    return {"schema": "ix.v1/results", **results.model_dump(mode="json")}


def _format_option(f: Callable[..., Any]) -> Callable[..., Any]:
    return click.option(
        "--format",
        "fmt",
        type=click.Choice(["table", "json"]),
        default="table",
        help="table to read; json for programs (errors become one JSON line on stderr)",
    )(f)


# --- CLI -------------------------------------------------------------------------------


@click.group(invoke_without_command=True, epilog=_EXIT_CODES)
@click.version_option(__version__, prog_name="ix")
@click.pass_context
def main(ctx: click.Context) -> None:
    """ix — Intelligent Experimentation

    Evals, benchmarks, and QoS experiments for AI agents and skills.

    \b
    ix experiment init routing --lab lab     scaffold an experiment
    ix experiment validate routing           compose everything, run nothing
    ix run routing --plan                    what a run would do, and which of it is live
    ix run routing --simulate --seed 42      prove the harness, no API calls
    ix run routing --subject live            one subject, for real
    ix compare routing baseline candidate    is the difference real?
    """
    if ctx.invoked_subcommand is None:
        out.print(ctx.get_help())


# --- Lab -------------------------------------------------------------------------------


@main.group()
def lab() -> None:
    """Manage labs (experiment workspaces)."""


@lab.command("init")
@click.argument("name")
@_format_option
def lab_init(name: str, fmt: str) -> None:
    """Create a new lab — a directory that holds experiments. Never overwrites.

    Examples:
        ix lab init ci-lab
    """
    from ix.config.settings import find_project_root

    lab_path = find_project_root() / name
    created = not lab_path.exists()
    if created:
        lab_path.mkdir(parents=True)
    if fmt == "json":
        _emit(
            {
                "schema": "ix.v1/init",
                "kind": "lab",
                "name": name,
                "path": str(lab_path),
                "created": created,
            }
        )
        return
    if not created:
        err.print(f"[yellow]Lab already exists:[/yellow] {lab_path}")
        return
    err.print(f"[green]Created lab:[/green] [cyan]{name}[/cyan] at {lab_path}")
    err.print(f"  [dim]Next: ix experiment init <name> --lab {name}[/dim]")


@lab.command("list")
@_format_option
def lab_list(fmt: str) -> None:
    """List labs under the project root."""
    from ix.config.settings import find_project_root, is_lab

    root = find_project_root()
    labs = [d for d in sorted(root.iterdir()) if d.is_dir() and is_lab(d)]
    rows = [
        {
            "name": lab_path.name,
            "experiments": sum(
                1 for d in lab_path.iterdir() if d.is_dir() and (d / "experiment.yaml").exists()
            ),
            "path": str(lab_path.relative_to(root)),
        }
        for lab_path in labs
    ]
    if fmt == "json":
        _emit({"schema": "ix.v1/labs", "labs": rows})
        return
    if not rows:
        err.print("[dim]No labs found. Create one with: ix lab init <name>[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Lab")
    table.add_column("Experiments")
    table.add_column("Path")
    for row in rows:
        table.add_row(f"[cyan]{row['name']}[/cyan]", str(row["experiments"]), str(row["path"]))
    out.print(table)


# --- Run -------------------------------------------------------------------------------


def _plan(
    experiment: ExperimentConfig, subjects: list[Subject | None], simulate: bool
) -> list[dict[str, Any]]:
    """Per subject: who plays it, how many agent sessions, and whether they are live."""
    from ix.composition import is_live, results_name, runtime_type

    n = len(experiment.probes)
    rows = []
    for subject in subjects:
        runtime = runtime_type(subject, simulate=simulate)
        options = (subject.config.get("runtime") or {}) if subject and not simulate else {}
        rows.append(
            {
                "subject": subject.name if subject else "default",
                "saved_as": results_name(subject, simulate=simulate),
                "runtime": runtime,
                "permission_mode": options.get("permission_mode")
                if isinstance(options, dict)
                else None,
                "live": is_live(runtime),
                "probes": n,
                "trials": experiment.trials,
                "repeats": experiment.repeats,
                "sessions": n * experiment.trials * experiment.repeats,
            }
        )
    return rows


def _print_plan(experiment: ExperimentConfig, rows: list[dict[str, Any]], refused: bool) -> None:
    out.print(f"[bold]{experiment.name}[/bold] · plan (nothing was run)")
    table = Table(show_header=True, header_style="bold")
    table.add_column("Subject")
    table.add_column("Runtime")
    table.add_column("Sessions (probes × trials × repeats)")
    table.add_column("Live")
    for row in rows:
        live = "[red]LIVE[/red]" if row["live"] else "offline"
        if row["live"] and row["permission_mode"]:
            live += f" ({row['permission_mode']})"
        table.add_row(
            f"[cyan]{row['subject']}[/cyan]",
            str(row["runtime"]),
            f"{row['probes']} × {row['trials']} × {row['repeats']} = {row['sessions']}",
            live,
        )
    out.print(table)
    total = sum(r["sessions"] for r in rows)
    live_total = sum(r["sessions"] for r in rows if r["live"])
    out.print(f"Total: {total} sessions, {live_total} live")
    if refused:
        out.print(
            "[yellow]Run as given, this would be refused:[/yellow] several subjects with a live "
            "runtime. Name one with --subject, or pass --all."
        )


@main.command(epilog=_EXIT_CODES)
@click.argument("name")
@click.option("--lab", "lab_name", help="Lab name (auto-detected if omitted)")
@click.option(
    "--subject",
    "subject_names",
    multiple=True,
    help="Subject to run (repeatable; default: all, see --all)",
)
@click.option(
    "--all",
    "all_subjects",
    is_flag=True,
    default=False,
    help="Run every subject even when several are live (otherwise refused, exit 3)",
)
@click.option(
    "--plan",
    "show_plan",
    is_flag=True,
    default=False,
    help="Print per subject the runtime, sessions and whether it is live; run nothing",
)
@click.option("--trials", type=click.IntRange(min=1), help="Override trials per probe")
@click.option("--repeats", type=click.IntRange(min=1), help="Override whole-run repeats")
@click.option(
    "--simulate",
    is_flag=True,
    default=False,
    help="Run every subject on the simulator — proves the harness, measures nothing",
)
@click.option("--mock", "mock_alias", is_flag=True, default=False, hidden=True)
@click.option(
    "--engine",
    type=click.Choice(["native", "inspect"]),
    default=None,
    help="Override the experiment's engine",
)
@click.option("--seed", type=int, help="Seed for the simulator")
@_format_option
def run(
    name: str,
    lab_name: str | None,
    subject_names: tuple[str, ...],
    all_subjects: bool,
    show_plan: bool,
    trials: int | None,
    repeats: int | None,
    simulate: bool,
    mock_alias: bool,
    engine: str | None,
    seed: int | None,
    fmt: str,
) -> None:
    """Run an experiment for each subject; results are saved per subject.

    With no --subject, every subject runs — unless more than one would run and any of them
    is live (a runtime other than simulated or mock): that is refused until you name a
    --subject or pass --all. A --simulate run of a non-simulated subject is saved as
    <subject>@simulated, never over that subject's own results. --format json prints a
    list of results, one per subject.

    \b
    Examples:
        ix run routing --lab lab --plan
        ix run routing --lab lab --simulate --seed 42
        ix run routing --subject baseline --subject candidate
        ix run codegen --engine inspect --repeats 3
    """
    from ix.composition import create_service, results_name

    if mock_alias:
        err.print("[yellow]--mock is deprecated; use --simulate[/yellow]")
        simulate = True
    if all_subjects and subject_names:
        raise click.UsageError("give --subject or --all, not both")

    lab_path, exp_path, experiment = _load(name, lab_name)
    overrides = {k: v for k, v in (("trials", trials), ("repeats", repeats)) if v}
    if overrides:
        experiment = experiment.model_copy(update=overrides)

    try:
        subjects: list[Subject | None] = (
            [experiment.subject(n) for n in subject_names]
            if subject_names
            else list(experiment.subjects) or [None]
        )
    except IxError as e:
        _fail(e, f"ix experiment show {name} --lab {lab_path.name}")

    plan = _plan(experiment, subjects, simulate)
    refused = (
        not subject_names
        and not all_subjects
        and len(plan) > 1
        and any(row["live"] for row in plan)
    )
    if show_plan:
        if fmt == "json":
            _emit(
                {
                    "schema": "ix.v1/plan",
                    "experiment": experiment.name,
                    "simulate": simulate,
                    "subjects": plan,
                    "total_sessions": sum(r["sessions"] for r in plan),
                    "live_sessions": sum(r["sessions"] for r in plan if r["live"]),
                    "refused": refused,
                }
            )
        else:
            _print_plan(experiment, plan, refused)
        return
    if refused:
        listing = "; ".join(
            f"{r['subject']} ({r['runtime']}, {r['sessions']} sessions"
            + (", live)" if r["live"] else ")")
            for r in plan
        )
        _cli_error(
            f"refusing to run {len(plan)} subjects implicitly when some are live: {listing}",
            f"name one: ix run {name} --lab {lab_path.name} --subject <name>; or run them all: "
            f"--all (preview: --plan)",
            kind="config",
            code=EXIT_CONFIG,
        )

    try:
        service = create_service(
            experiment,
            simulate=simulate,
            lab=lab_path,
            seed=seed,
            experiment_cwd=str(exp_path.resolve()),
            engine=engine,
        )
    except (IxError, MatrixError) as e:
        _fail(e, f"ix experiment validate {name} --lab {lab_path.name}")

    engine_label = engine or str(experiment.engine.get("type", "native"))
    collected: list[ExperimentResults] = []
    for subject, row in zip(subjects, plan, strict=True):
        saved_as = results_name(subject, simulate=simulate)
        err.print(
            f"Running [bold cyan]{experiment.name}[/bold cyan] · subject "
            f"[cyan]{row['subject']}[/cyan] ({row['runtime']}) · "
            f"{len(experiment.probes)} probes × {experiment.trials} trials"
            + (f" × {experiment.repeats} repeats" if experiment.repeats > 1 else "")
            + f" · engine={engine_label}"
            + (f" · saved as {saved_as}" if saved_as != row["subject"] else "")
        )

        def on_probe(probe_result: ProbeResult) -> None:
            verdict = "[green]PASS[/green]" if probe_result.passed else "[yellow]FAIL[/yellow]"
            err.print(f"  {probe_result.probe_id}: {verdict} (score={probe_result.score:.0%})")

        def on_run(run_idx: int, pass_rate: float) -> None:
            err.print(f"  [dim]repeat {run_idx + 1}/{experiment.repeats}: {pass_rate:.1%}[/dim]")

        with _trial_progress(experiment) as on_trial:
            try:
                result = asyncio.run(
                    service.run(
                        experiment,
                        subject=subject,
                        on_probe_complete=on_probe,
                        on_run_complete=on_run if experiment.repeats > 1 else None,
                        on_trial=on_trial,
                        save_as=saved_as,
                    )
                )
            except (IxError, MatrixError) as e:
                _fail(e)
        collected.append(result)
        if fmt == "table":
            _print_metrics(result)

    if fmt == "json":
        _emit([_results_doc(r) for r in collected])


# --- Experiment ------------------------------------------------------------------------


@main.group()
def experiment() -> None:
    """Manage experiments within a lab."""


_EXAMPLE_PROBE = """\
---
id: example
# activation sensor: must_trigger | should_not_trigger | acceptable
expectation: must_trigger
# the skill that should fire for this prompt (with should_not_trigger: the one that must not)
expected_skill: my-skill
---
Replace this with a prompt a real user would send. One probe per file; the body is sent verbatim.
"""


@experiment.command("init")
@click.argument("name")
@click.option("--lab", "lab_name", help="Lab name (auto-detected if omitted)")
@_format_option
def experiment_init(name: str, lab_name: str | None, fmt: str) -> None:
    """Scaffold an experiment: experiment.yaml, an example probe, a starter subject.

    Never overwrites: an existing experiment is reported, not touched.

    Examples:
        ix experiment init skill-activation --lab lab
    """
    lab_path = _resolve_lab(lab_name)
    exp_path = lab_path / name
    created = not exp_path.exists()
    files: list[str] = []
    if created:
        exp_path.mkdir(parents=True)
        (exp_path / "tasks").mkdir()
        (exp_path / "subjects").mkdir()
        scaffold = {
            "experiment.yaml": (
                f'name: {name}\ndescription: ""\nengine: native\nsensor: activation\ntrials: 5\n'
            ),
            "tasks/example.md": _EXAMPLE_PROBE,
            # A starter subject on the simulator, so a new experiment runs before any model is
            # configured. Change runtime.type to claude-sdk or model for a real measurement.
            "subjects/agent.md": (
                "---\nname: agent\ndescription: Starter subject on the simulator.\n"
                "runtime:\n  type: simulated\n---\nYou are a helpful assistant.\n"
            ),
        }
        for rel, text in scaffold.items():
            (exp_path / rel).write_text(text)
            files.append(rel)
    if fmt == "json":
        _emit(
            {
                "schema": "ix.v1/init",
                "kind": "experiment",
                "name": name,
                "path": str(exp_path),
                "created": created,
                "files": files,
            }
        )
        return
    if not created:
        err.print(f"[yellow]Already exists:[/yellow] {exp_path}")
        return
    err.print(f"[green]Created experiment:[/green] [cyan]{name}[/cyan]")
    err.print(f"  [dim]Probe: {exp_path}/tasks/example.md (edit it; one probe per file)[/dim]")
    err.print(f"  [dim]Subject: {exp_path}/subjects/agent.md (runtime: simulated)[/dim]")


@experiment.command("list")
@click.option("--lab", "lab_name", help="Lab name (auto-detected if omitted)")
@_format_option
def experiment_list(lab_name: str | None, fmt: str) -> None:
    """List experiments in a lab. Exits 3 if any of them is invalid (the list still prints)."""
    from ix.composition import create_store

    lab_path = _resolve_lab(lab_name)
    store = create_store(lab=lab_path)
    rows: list[dict[str, Any]] = []
    for exp_path in store.list_experiments(lab_path):
        try:
            exp = store.load_experiment(exp_path)
        except (IxError, MatrixError) as e:
            rows.append({"name": exp_path.name, "valid": False, "error": str(e)})
            continue
        rows.append(
            {
                "name": exp.name,
                "valid": True,
                "error": None,
                "probes": len(exp.probes),
                "trials": exp.trials,
                "repeats": exp.repeats,
                "subjects": [s.name for s in exp.subjects],
                "engine": str(exp.engine.get("type", "native")),
            }
        )

    if fmt == "json":
        _emit({"schema": "ix.v1/experiments", "lab": lab_path.name, "experiments": rows})
    elif not rows:
        err.print(
            f"[dim]No experiments in {lab_path.name}. "
            f"Create one: ix experiment init <name> --lab {lab_path.name}[/dim]"
        )
    else:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Name")
        table.add_column("Probes")
        table.add_column("Trials")
        table.add_column("Subjects")
        table.add_column("Engine")
        for row in rows:
            if not row["valid"]:
                table.add_row(
                    f"[red]{row['name']}[/red]", "-", "-", "[red]invalid (see below)[/red]", "-"
                )
                continue
            table.add_row(
                f"[cyan]{row['name']}[/cyan]",
                str(row["probes"]),
                str(row["trials"]),
                ", ".join(row["subjects"]) or "-",
                row["engine"],
            )
        out.print(table)

    invalid = [row["name"] for row in rows if not row["valid"]]
    if invalid:
        _cli_error(
            f"{len(invalid)} invalid experiment(s): "
            + "; ".join(f"{row['name']}: {row['error']}" for row in rows if not row["valid"]),
            f"ix experiment validate {invalid[0]} --lab {lab_path.name}",
            kind="config",
            code=EXIT_CONFIG,
        )


@experiment.command("show")
@click.argument("name")
@click.option("--lab", "lab_name", help="Lab name (auto-detected if omitted)")
@_format_option
def experiment_show(name: str, lab_name: str | None, fmt: str) -> None:
    """Show an experiment's subjects, sensors, engine and probes."""
    from ix.composition import is_live, runtime_type

    _, _, exp = _load(name, lab_name)
    if fmt == "json":
        _emit(
            {
                "schema": "ix.v1/experiment",
                "name": exp.name,
                "description": exp.description,
                "engine": exp.engine,
                "sensors": list(exp.sensors),
                "trials": exp.trials,
                "repeats": exp.repeats,
                "subjects": [
                    {
                        "name": s.name,
                        "description": s.description,
                        "runtime": runtime_type(s),
                        "live": is_live(runtime_type(s)),
                    }
                    for s in exp.subjects
                ],
                "probes": [
                    {"id": p.id, "prompt": p.prompt, "metadata": p.metadata} for p in exp.probes
                ],
            }
        )
        return
    out.print(f"[bold]{exp.name}[/bold]")
    if exp.description:
        out.print(f"[dim]{exp.description}[/dim]")
    for subject in exp.subjects or ():
        runtime = (subject.config.get("runtime") or {}).get("type", "unset")
        out.print(f"Subject: [cyan]{subject.name}[/cyan] (runtime: {runtime})")
    out.print(f"Sensors: [cyan]{', '.join(s.get('type', '?') for s in exp.sensors)}[/cyan]")
    out.print(f"Engine: {exp.engine.get('type', 'native')}")
    out.print(f"Trials × repeats: {exp.trials} × {exp.repeats}")
    out.print(f"Probes: {len(exp.probes)}")
    if exp.probes:
        out.print()
        table = Table(show_header=True, header_style="bold")
        table.add_column("ID")
        table.add_column("Expectation")
        table.add_column("Prompt", max_width=50)
        for probe in exp.probes:
            table.add_row(probe.id, str(probe.metadata.get("expectation", "-")), probe.prompt[:50])
        out.print(table)


@experiment.command("validate", epilog=_EXIT_CODES)
@click.argument("name")
@click.option("--lab", "lab_name", help="Lab name (auto-detected if omitted)")
@click.option("--engine", type=click.Choice(["native", "inspect"]), default=None)
@_format_option
def experiment_validate(name: str, lab_name: str | None, engine: str | None, fmt: str) -> None:
    """Compose everything a run would — sensors, engine, every subject's runtime — and run
    nothing. Reports every problem, not just the first, and exits 3 if there are any.

    Also checks that local plugin paths (claude-sdk ``runtime.plugins``) exist.
    """
    from ix.composition import create_store, validate_experiment

    lab_path = _resolve_lab(lab_name)
    exp_path = _require_experiment(lab_path, name)
    try:
        exp = create_store(lab=lab_path).load_experiment(exp_path)
    except (IxError, MatrixError) as e:
        problems = [str(e)]  # a file that does not load stops composition at its first error
    else:
        problems = validate_experiment(exp, experiment_cwd=str(exp_path.resolve()), engine=engine)
    if problems:
        _cli_error(
            f"{name}: {len(problems)} problem(s)",
            f"fix each problem, then: ix experiment validate {name} --lab {lab_path.name}",
            kind="config",
            code=EXIT_CONFIG,
            problems=problems,
        )
    subjects = [s.name for s in exp.subjects]
    if fmt == "json":
        _emit(
            {
                "schema": "ix.v1/validation",
                "experiment": exp.name,
                "valid": True,
                "problems": [],
                "probes": len(exp.probes),
                "subjects": subjects,
            }
        )
        return
    out.print(
        f"[green]Valid:[/green] {exp.name} — {len(exp.probes)} probes, "
        f"subjects: {', '.join(subjects) or 'default'}"
    )


# --- Results ---------------------------------------------------------------------------


def _missing_results(lab_path: Path, name: str, e: ResultsNotFoundError) -> NoReturn:
    """A results error whose fix is a command that will work: another subject's results if
    there are any, else a plan of the run that would make some."""
    from ix.composition import create_store

    store = create_store(lab=lab_path)
    if store.subjects_with_results(name):
        fix = f"ix results {name} --lab {lab_path.name}"
    else:
        try:
            store.load_experiment(lab_path / name)
            fix = f"ix run {name} --lab {lab_path.name} --plan"
        except (IxError, MatrixError):
            fix = f"ix experiment validate {name} --lab {lab_path.name}"
    _fail(e, fix)


@main.command(epilog=_EXIT_CODES)
@click.argument("name")
@click.option("--lab", "lab_name", help="Lab name (auto-detected if omitted)")
@click.option(
    "--subject",
    "subject_name",
    help="One subject, or <subject>@simulated (default: every subject with results)",
)
@_format_option
def results(name: str, lab_name: str | None, subject_name: str | None, fmt: str) -> None:
    """Show the latest results of an experiment, per subject.

    --format json prints a list of results, one per subject.
    """
    from ix.composition import create_store

    lab_path = _resolve_lab(lab_name)
    _require_experiment(lab_path, name)
    store = create_store(lab=lab_path)
    names = [subject_name] if subject_name else store.subjects_with_results(name)
    if not names:
        _missing_results(lab_path, name, ResultsNotFoundError(f"no results for {name} yet"))
    try:
        loaded = [store.load_summary(name, n) for n in names]
    except ResultsNotFoundError as e:
        _missing_results(lab_path, name, e)
    except IxError as e:
        _fail(e)
    if fmt == "json":
        _emit([_results_doc(r) for r in loaded])
        return
    for r in loaded:
        _print_metrics(r)


@main.command(epilog=_EXIT_CODES)
@click.argument("name")
@click.argument("subject_a")
@click.argument("subject_b")
@click.option("--lab", "lab_name", help="Lab name (auto-detected if omitted)")
@_format_option
def compare(name: str, subject_a: str, subject_b: str, lab_name: str | None, fmt: str) -> None:
    """Compare two subjects' latest results, paired probe by probe.

    Reports B − A with a standard error and 95% CI, the probes whose verdict flipped, and
    the noise floor. The verdict is "inconclusive" unless the CI excludes zero and the gap
    clears the noise floor. A subject may be <subject>@simulated.

    \b
    Example:
        ix compare routing baseline candidate
    """
    from ix.composition import create_store
    from ix.eval.analysis import compare_results

    lab_path = _resolve_lab(lab_name)
    _require_experiment(lab_path, name)
    store = create_store(lab=lab_path)
    try:
        comparison = compare_results(
            store.load_summary(name, subject_a), store.load_summary(name, subject_b)
        )
    except ResultsNotFoundError as e:
        _missing_results(lab_path, name, e)
    except IxError as e:
        _fail(e)
    if fmt == "json":
        _emit({"schema": "ix.v1/comparison", **comparison.model_dump(mode="json")})
    else:
        _print_comparison(comparison)


if __name__ == "__main__":
    sys.exit(main())

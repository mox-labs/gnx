"""CLI adapter for ix — Intelligent Experimentation.

Thin driving adapter over the composition root. Output discipline (clig.dev): the primary
output — a results table, or JSON with ``--format json`` — goes to stdout; banners,
progress and errors go to stderr, so ``ix results x --format json | jq`` always parses.

Exit codes: 0 success · 1 an ix/matrix/config error or failed validation · 2 usage.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from typing import TYPE_CHECKING, NoReturn

import rich_click as click
from matrix import MatrixError
from rich.console import Console
from rich.table import Table

from ix import __version__
from ix.domain.errors import IxError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from ix.domain.types import Reading, Subject, Trial
    from ix.eval.models import Comparison, ExperimentConfig, ExperimentResults, ProbeResult

out = Console()
err = Console(stderr=True)


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
    out.print(f"Status: {_status_style(results.status)}")
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


def _cli_error(message: str, fix: str | None = None) -> NoReturn:
    err.print(f"[red]Error:[/red] {message}")
    if fix:
        err.print(f"  [dim]Fix: {fix}[/dim]")
    raise SystemExit(1)


def _resolve_lab(lab_name: str | None) -> Path:
    from ix.config.settings import find_lab

    try:
        return find_lab(lab_name)
    except FileNotFoundError as e:
        _cli_error(str(e))


def _load(name: str, lab_name: str | None) -> tuple[Path, Path, ExperimentConfig]:
    """(lab, experiment dir, config) — or a clean error naming what is missing."""
    from ix.composition import create_store

    lab_path = _resolve_lab(lab_name)
    exp_path = lab_path / name
    if not (exp_path / "experiment.yaml").exists():
        _cli_error(f"Experiment not found: {name}", f"ix experiment list --lab {lab_path.name}")
    try:
        return lab_path, exp_path, create_store(lab=lab_path).load_experiment(exp_path)
    except (IxError, MatrixError) as e:
        _cli_error(str(e))


# --- CLI -------------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="ix")
@click.pass_context
def main(ctx: click.Context) -> None:
    """ix — Intelligent Experimentation

    Evals, benchmarks, and QoS experiments for AI agents and skills.

    \b
    ix experiment init routing --lab lab     scaffold an experiment
    ix experiment validate routing           compose everything, run nothing
    ix run routing --simulate --seed 42      prove the harness, no API calls
    ix run routing                           every subject, for real
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
def lab_init(name: str) -> None:
    """Create a new lab — a directory that holds experiments.

    Examples:
        ix lab init ci-lab
    """
    from ix.config.settings import find_project_root

    lab_path = find_project_root() / name
    if lab_path.exists():
        err.print(f"[yellow]Lab already exists:[/yellow] {lab_path}")
        return
    lab_path.mkdir(parents=True)
    err.print(f"[green]Created lab:[/green] [cyan]{name}[/cyan] at {lab_path}")
    err.print(f"  [dim]Next: ix experiment init <name> --lab {name}[/dim]")


@lab.command("list")
def lab_list() -> None:
    """List labs under the project root."""
    from ix.config.settings import find_project_root, is_lab

    root = find_project_root()
    labs = [d for d in sorted(root.iterdir()) if d.is_dir() and is_lab(d)]
    if not labs:
        err.print("[dim]No labs found. Create one with: ix lab init <name>[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Lab")
    table.add_column("Experiments")
    table.add_column("Path")
    for lab_path in labs:
        count = sum(
            1 for d in lab_path.iterdir() if d.is_dir() and (d / "experiment.yaml").exists()
        )
        table.add_row(f"[cyan]{lab_path.name}[/cyan]", str(count), str(lab_path.relative_to(root)))
    out.print(table)


# --- Run -------------------------------------------------------------------------------


@main.command()
@click.argument("name")
@click.option("--lab", "lab_name", help="Lab name (auto-detected if omitted)")
@click.option(
    "--subject", "subject_names", multiple=True, help="Subject to run (repeatable; default: all)"
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
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
def run(
    name: str,
    lab_name: str | None,
    subject_names: tuple[str, ...],
    trials: int | None,
    repeats: int | None,
    simulate: bool,
    mock_alias: bool,
    engine: str | None,
    seed: int | None,
    fmt: str,
) -> None:
    """Run an experiment for each subject; results are saved per subject.

    \b
    Examples:
        ix run routing --lab lab --simulate --seed 42
        ix run routing --subject baseline --subject candidate
        ix run codegen --engine inspect --repeats 3
    """
    from ix.composition import create_service

    if mock_alias:
        err.print("[yellow]--mock is deprecated; use --simulate[/yellow]")
        simulate = True

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
        service = create_service(
            experiment,
            simulate=simulate,
            lab=lab_path,
            seed=seed,
            experiment_cwd=str(exp_path.resolve()),
            engine=engine,
        )
    except (IxError, MatrixError) as e:
        _cli_error(str(e), f"ix experiment validate {name} --lab {lab_path.name}")

    engine_label = engine or str(experiment.engine.get("type", "native"))
    collected: list[ExperimentResults] = []
    for subject in subjects:
        runtime = (
            "simulated"
            if simulate
            else str((subject.config.get("runtime") or {}).get("type", "unset"))
            if subject
            else "unset"
        )
        err.print(
            f"Running [bold cyan]{experiment.name}[/bold cyan] · subject "
            f"[cyan]{subject.name if subject else 'default'}[/cyan] ({runtime}) · "
            f"{len(experiment.probes)} probes × {experiment.trials} trials"
            + (f" × {experiment.repeats} repeats" if experiment.repeats > 1 else "")
            + f" · engine={engine_label}"
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
                    )
                )
            except (IxError, MatrixError) as e:
                _cli_error(str(e))
        collected.append(result)
        if fmt == "table":
            _print_metrics(result)

    if fmt == "json":
        payload = [r.model_dump(mode="json") for r in collected]
        import json

        click.echo(json.dumps(payload[0] if len(payload) == 1 else payload, indent=2))


# --- Experiment ------------------------------------------------------------------------


@main.group()
def experiment() -> None:
    """Manage experiments within a lab."""


@experiment.command("init")
@click.argument("name")
@click.option("--lab", "lab_name", help="Lab name (auto-detected if omitted)")
def experiment_init(name: str, lab_name: str | None) -> None:
    """Scaffold an experiment: experiment.yaml, tasks/, and a starter subject.

    Examples:
        ix experiment init skill-activation --lab lab
    """
    lab_path = _resolve_lab(lab_name)
    exp_path = lab_path / name
    if exp_path.exists():
        err.print(f"[yellow]Already exists:[/yellow] {exp_path}")
        return

    exp_path.mkdir(parents=True)
    (exp_path / "tasks").mkdir()
    (exp_path / "subjects").mkdir()
    (exp_path / "experiment.yaml").write_text(
        f'name: {name}\ndescription: ""\nengine: native\nsensor: activation\ntrials: 5\n'
    )
    # A starter subject on the simulator, so a new experiment runs before any model is
    # configured. Change runtime.type to claude-sdk or model for a real measurement.
    (exp_path / "subjects" / "agent.md").write_text(
        "---\nname: agent\ndescription: Starter subject on the simulator.\n"
        "runtime:\n  type: simulated\n---\nYou are a helpful assistant.\n"
    )
    err.print(f"[green]Created experiment:[/green] [cyan]{name}[/cyan]")
    err.print(f"  [dim]Add probes: {exp_path}/tasks/<id>.md[/dim]")
    err.print(f"  [dim]Subject: {exp_path}/subjects/agent.md (runtime: simulated)[/dim]")


@experiment.command("list")
@click.option("--lab", "lab_name", help="Lab name (auto-detected if omitted)")
def experiment_list(lab_name: str | None) -> None:
    """List experiments in a lab."""
    from ix.composition import create_store

    lab_path = _resolve_lab(lab_name)
    store = create_store(lab=lab_path)
    experiments = store.list_experiments(lab_path)
    if not experiments:
        err.print(
            f"[dim]No experiments in {lab_path.name}. "
            f"Create one: ix experiment init <name> --lab {lab_path.name}[/dim]"
        )
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name")
    table.add_column("Probes")
    table.add_column("Trials")
    table.add_column("Subjects")
    table.add_column("Engine")
    for exp_path in experiments:
        try:
            exp = store.load_experiment(exp_path)
        except (IxError, MatrixError) as e:
            table.add_row(f"[red]{exp_path.name}[/red]", "-", "-", f"[red]invalid: {e}[/red]", "-")
            continue
        table.add_row(
            f"[cyan]{exp.name}[/cyan]",
            str(len(exp.probes)),
            str(exp.trials),
            ", ".join(s.name for s in exp.subjects) or "-",
            str(exp.engine.get("type", "native")),
        )
    out.print(table)


@experiment.command("show")
@click.argument("name")
@click.option("--lab", "lab_name", help="Lab name (auto-detected if omitted)")
def experiment_show(name: str, lab_name: str | None) -> None:
    """Show an experiment's subjects, sensors, engine and probes."""
    _, _, exp = _load(name, lab_name)
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


@experiment.command("validate")
@click.argument("name")
@click.option("--lab", "lab_name", help="Lab name (auto-detected if omitted)")
@click.option("--engine", type=click.Choice(["native", "inspect"]), default=None)
def experiment_validate(name: str, lab_name: str | None, engine: str | None) -> None:
    """Compose everything a run would — sensors, engine, every subject's runtime — and run
    nothing. Reports every problem, not just the first.
    """
    from ix.composition import validate_experiment

    _, exp_path, exp = _load(name, lab_name)
    problems = validate_experiment(exp, experiment_cwd=str(exp_path.resolve()), engine=engine)
    if problems:
        err.print(f"[red]{name}: {len(problems)} problem(s)[/red]")
        for problem in problems:
            err.print(f"  [red]-[/red] {problem}")
        raise SystemExit(1)
    subjects = ", ".join(s.name for s in exp.subjects) or "default"
    out.print(f"[green]Valid:[/green] {exp.name} — {len(exp.probes)} probes, subjects: {subjects}")


# --- Results ---------------------------------------------------------------------------


@main.command()
@click.argument("name")
@click.option("--lab", "lab_name", help="Lab name (auto-detected if omitted)")
@click.option("--subject", "subject_name", help="One subject (default: every subject with results)")
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
def results(name: str, lab_name: str | None, subject_name: str | None, fmt: str) -> None:
    """Show the latest results of an experiment, per subject."""
    from ix.composition import create_store

    lab_path = _resolve_lab(lab_name)
    store = create_store(lab=lab_path)
    names = [subject_name] if subject_name else store.subjects_with_results(name)
    if not names:
        _cli_error(f"No results for {name}", f"ix run {name} --lab {lab_path.name} --simulate")
    try:
        loaded = [store.load_summary(name, n) for n in names]
    except IxError as e:
        _cli_error(str(e))
    if fmt == "json":
        import json

        payload = [r.model_dump(mode="json") for r in loaded]
        click.echo(json.dumps(payload[0] if len(payload) == 1 else payload, indent=2))
        return
    for r in loaded:
        _print_metrics(r)


@main.command()
@click.argument("name")
@click.argument("subject_a")
@click.argument("subject_b")
@click.option("--lab", "lab_name", help="Lab name (auto-detected if omitted)")
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
def compare(name: str, subject_a: str, subject_b: str, lab_name: str | None, fmt: str) -> None:
    """Compare two subjects' latest results, paired probe by probe.

    Reports B − A with a standard error and 95% CI, the probes whose verdict flipped, and
    the noise floor. The verdict is "inconclusive" unless the CI excludes zero and the gap
    clears the noise floor.

    \b
    Example:
        ix compare routing baseline candidate
    """
    from ix.composition import create_store
    from ix.eval.analysis import compare_results

    store = create_store(lab=_resolve_lab(lab_name))
    try:
        comparison = compare_results(
            store.load_summary(name, subject_a), store.load_summary(name, subject_b)
        )
    except IxError as e:
        _cli_error(str(e))
    if fmt == "json":
        click.echo(comparison.model_dump_json(indent=2))
    else:
        _print_comparison(comparison)


if __name__ == "__main__":
    sys.exit(main())

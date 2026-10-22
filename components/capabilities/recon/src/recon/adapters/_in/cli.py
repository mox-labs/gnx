"""The ``recon`` command line: init, survey, status, query, templates, capture.

An inbound adapter over the library API and nothing else: every call goes through
``recon``'s declared names, so a program importing ``recon`` gets exactly what these
commands print. ``main(obj=...)`` runs the commands over the operations given instead, a
root built with other ports: that is how a test drives the command line.

Data goes to stdout and commentary to stderr. ``--json`` prints one versioned document. A
command that fails, a usage error, Ctrl-C and a defect in recon included, prints the
``recon.v1.error`` envelope on stderr (one JSON line under ``--json``) and exits with the
code its kind maps to. A survey whose tables failed still prints its document on stdout,
then the envelope for what the failures add up to. SIGTERM is handled as Ctrl-C is, so a
stopped survey kills the command it was running. Nothing prompts. Off a terminal, nothing
is wrapped: a path or a command stays on one line, whole.
"""

from __future__ import annotations

import json
import signal
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import rich_click as click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

import recon

if TYPE_CHECKING:
    from types import FrameType

out = Console(soft_wrap=True)
err = Console(stderr=True, soft_wrap=True)

#: The width a table is laid out in off a terminal: wide enough that no cell wraps.
_UNWRAPPED = 10_000

# rich-click rewraps an epilog's lines; a blank line between them keeps each on its own.
EXIT_CODES = "Exit codes:\n\n" + "\n\n".join(
    f"{code}  {kind}: {recon.EXIT_MEANINGS[kind]}. Next: {recon.EXIT_NEXT[kind]}"
    for kind, code in recon.EXIT_CODES.items()
)

_json_option = click.option(
    "--json", "as_json", is_flag=True, help="One JSON document on stdout; errors as JSON on stderr"
)


def _emit(document: dict[str, Any]) -> None:
    click.echo(json.dumps(document, indent=2, default=str))


def _print_table(table: Table) -> None:
    """On a terminal, fitted to its width; elsewhere as wide as the cells are."""
    if out.is_terminal:
        out.print(table)
    else:
        Console(file=out.file, width=_UNWRAPPED).print(table)


def _print_warnings(warnings: list[dict[str, Any]]) -> None:
    for warning in warnings:
        where = f"{warning['path']}: " if warning["path"] else ""
        err.print(f"[yellow]warning: {escape(where + warning['message'])}[/]")
        if warning["fix"]:
            err.print(f"[dim]  fix: {escape(warning['fix'])}[/]")


def _telemetry_warnings() -> list[dict[str, Any]]:
    """The command line is an application, so it installs OpenTelemetry's SDK as the
    ``OTEL_*`` variables say; what they ask and cannot get is a warning in its document."""
    return [warning.document() for warning in recon.configure_telemetry()]


def _operations() -> Any:
    """What the commands run: ``recon``'s library API, or what the caller of ``main`` passed
    as its ``obj``, the same operations over other ports (a test's root, built by
    ``recon.composition.root`` with fakes)."""
    context = click.get_current_context(silent=True)
    given = context.find_root().obj if context is not None else None
    return recon if given is None else given


def _interrupt(signum: int, frame: FrameType | None) -> NoReturn:
    raise KeyboardInterrupt


class _InterruptedError(Exception):
    """Ctrl-C or SIGTERM during a command. Raised in place of KeyboardInterrupt, which click
    answers with a blank line on stderr ahead of the envelope."""


def _fail(error: recon.ReconError, *, as_json: bool) -> NoReturn:
    """Print the error envelope and exit with the code the error's kind maps to."""
    if as_json:
        click.echo(json.dumps(recon.error_document(error).document(), default=str), err=True)
    else:
        err.print(f"[red]error ({error.kind}):[/red] {escape(str(error))}")
        for problem in error.problems:
            where = escape(problem.path or "(file)")
            err.print(
                f"  [red]-[/red] [bold]{where}[/bold] [dim]{problem.kind}[/dim] "
                f"{escape(problem.message)}"
            )
            if problem.fix:
                err.print(f"    [dim]fix: {escape(problem.fix)}[/dim]")
        if not error.complete:
            err.print("[yellow]checking stopped early: more problems may follow these fixes[/]")
        if error.fix:
            err.print(f"[dim]fix: {escape(error.fix)}[/dim]")
    sys.exit(error.exit_code)


class _Group(click.RichGroup):
    """The root group. A usage error, Ctrl-C, SIGTERM and a defect are reported in the same
    envelope as every other failure, so a caller parses one shape."""

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except KeyboardInterrupt as interrupt:
            raise _InterruptedError from interrupt

    def main(self, *args: Any, **kwargs: Any) -> Any:
        argv = list(
            kwargs.get("args") or (args[0] if args and args[0] is not None else sys.argv[1:])
        )
        as_json = "--json" in argv
        kwargs["standalone_mode"] = False
        previous = signal.signal(signal.SIGTERM, _interrupt)
        try:
            code = super().main(*args, **kwargs)
        except click.UsageError as error:
            usage = recon.UsageError(
                error.format_message(),
                fix="`recon --help` lists the commands; `recon <command> --help` its arguments",
            )
            _fail(usage, as_json=as_json)
        except (_InterruptedError, click.Abort, KeyboardInterrupt):  # Ctrl-C or SIGTERM, any form
            interrupted = recon.ReconError(
                "interrupted before the command finished",
                kind="interrupted",
                fix="run the command again; an interrupted survey's archive is marked incomplete",
            )
            _fail(interrupted, as_json=as_json)
        except recon.ReconError as error:
            _fail(error, as_json=as_json)
        except Exception as error:  # a defect: still one envelope, so a caller parses one shape
            if not as_json:
                traceback.print_exc()
            defect = recon.ReconError(
                f"{type(error).__name__}: {error}",
                fix="report this as a bug in recon, with the command that raised it",
            )
            _fail(defect, as_json=as_json)
        finally:
            signal.signal(signal.SIGTERM, previous)
        sys.exit(code if isinstance(code, int) else 0)


@click.group(
    cls=_Group,
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog=EXIT_CODES,
)
@click.version_option(recon.__version__, prog_name="recon")
@click.option("--skill", is_flag=True, help="Print how to use recon (for an agent), then exit")
@click.option(
    "-r",
    "--reference",
    metavar="NAME",
    help="With --skill: print one reference instead (" + ", ".join(recon.skill_references()) + ")",
)
@click.pass_context
def main(ctx: click.Context, skill: bool, reference: str | None) -> None:
    """recon: one YAML file names the sources and what to ask them; recon plans it, collects
    the answers into JSONL tables, and answers SQL over them.

    \b
    recon --skill                         how to write a mission, for an agent
    recon init my-scan -t code-forensics  scaffold .recon/my-scan/config.yaml
    recon init my-scan -c mission.yaml    or from a file (- reads stdin)
    recon survey my-scan --dry-run        every problem, every run and its effects
    recon survey my-scan                  collect into a new archive
    recon query my-scan "SELECT ..."      SQL over the latest archive
    recon status                          missions, archives, installed plugins
    """
    if reference is not None and not skill:
        raise recon.UsageError("-r/--reference is used with --skill", fix="recon --skill -r <name>")
    if skill:
        click.echo(recon.skill(reference), nl=False)
        ctx.exit(0)
    if ctx.invoked_subcommand is None:
        out.print(ctx.get_help())


# --- init --------------------------------------------------------------------------------


_config_option = click.option(
    "-c",
    "--config",
    type=click.Path(exists=True, dir_okay=False, allow_dash=True, path_type=Path),
    default=None,
    help="A config file, copied to .recon/<name>/config.yaml (- reads stdin)",
)
_replace_option = click.option(
    "--replace", is_flag=True, help="With -c: overwrite the mission's config when it differs"
)


@main.command(epilog=EXIT_CODES)
@click.argument("name")
@click.option("-t", "--template", default=None, help="Built-in template (default: code-forensics)")
@_config_option
@click.option("--replace", is_flag=True, help="Overwrite the mission's config when it differs")
@_json_option
def init(
    name: str, template: str | None, config: Path | None, replace: bool, as_json: bool
) -> None:
    """Create a mission from a built-in template or a config file.

    Running it again with the same config changes nothing and succeeds; a different config
    is a conflict unless --replace is given. The document's outcome says which happened:
    created, unchanged or replaced.

    \b
    recon init my-scan
    recon init gh-audit --template github-audit
    recon init review -c path/to/catalog.yaml
    """
    document = _operations().init(name, template=template, config=config, replace=replace)
    if as_json:
        _emit(document.document())
        return
    if template is None and config is None:
        err.print(f"[dim]No --template given; used {document.from_.removeprefix('template:')}[/]")
    said = {"created": "Created", "unchanged": "Unchanged", "replaced": "Replaced"}
    err.print(f"[green]{said[document.outcome]}:[/] {escape(document.config)}")
    err.print(f"[dim]Edit it, then: recon survey {name} --dry-run && recon survey {name}[/]")


# --- survey ------------------------------------------------------------------------------


@main.command(epilog=EXIT_CODES)
@click.argument("name")
@_config_option
@_replace_option
@click.option(
    "-n",
    "--dry-run",
    is_flag=True,
    help="Validate and print the plan: no network, no subprocess, nothing written",
)
@_json_option
def survey(name: str, config: Path | None, replace: bool, dry_run: bool, as_json: bool) -> None:
    """Plan and run a collection mission.

    Every survey validates first: an invalid config exits 3 listing every problem, before
    anything is fetched or written. A survey with failed tables still writes its archive
    (marked incomplete); the exit code says what to do about the failures.

    \b
    recon survey my-scan --dry-run
    recon survey my-scan --json
    recon survey my-scan -c other.yaml --replace
    """
    setup = _telemetry_warnings()
    if dry_run:
        plan = _operations().plan(name, config=config, replace=replace)
        plan = plan.model_copy(update={"warnings": setup + plan.warnings})
        if as_json:
            _emit(plan.document())
        else:
            _print_plan(name, plan)
        return
    if not as_json:
        _print_warnings(setup)
    document = _operations().survey(
        name, config=config, replace=replace, on_event=None if as_json else _print_event
    )
    document = document.model_copy(update={"warnings": setup + document.warnings})
    if as_json:
        _emit(document.document())
    else:
        click.echo(document.archive)
    failure = recon.survey_failure(document)
    if failure is not None:
        _fail(failure, as_json=as_json)


def _print_plan(name: str, plan: recon.PlanDocument) -> None:
    _print_warnings(plan.warnings)
    table = Table(show_header=True, header_style="dim", title=f"recon survey {name}: plan")
    for column in ("Output", "Type", "Source", "Effects", "Does"):
        table.add_column(column)
    for run in plan.runs:
        effects = run["effects"] if isinstance(run["effects"], str) else ", ".join(run["effects"])
        table.add_row(run["output"], run["type"], run["source"] or "-", effects, escape(_does(run)))
    _print_table(table)
    if plan.writes.config:
        out.print(f"[dim]Would copy the config to {plan.writes.config}[/]")
    out.print(f"[dim]{len(plan.runs)} run(s); nothing was fetched, executed or written.[/]")


def _does(run: dict[str, Any]) -> str:
    """A run's request (its method, URL, headers and body, a line each), or its commands and
    the directory they run in."""
    if run.get("url"):
        lines = [f"{run.get('method', 'GET')} {run['url']}"]
        lines += [f"{name}: {value}" for name, value in (run.get("headers") or {}).items()]
        if run.get("body") is not None:
            lines.append(json.dumps(run["body"]))
        return "\n".join(lines)
    commands = "; ".join(run.get("commands") or [])
    if commands and "cwd" in run:
        return f"{commands} (in {run['cwd'] or 'the working directory'})"
    return commands or run.get("path") or ""


def _print_event(event: dict[str, Any]) -> None:
    kind, name = event["kind"], event.get("name", "?")
    if kind == "warning":
        err.print(f"[yellow]warning: {escape(event['reason'])}[/]")
        if event.get("fix"):
            err.print(f"[dim]  fix: {escape(event['fix'])}[/]")
    elif kind == "start":
        err.print(
            f"[dim]{event['runs']} run(s) from {event['collectors']} collector(s), "
            f"{event['sources']} source(s)[/]"
        )
    elif kind == "ok":
        err.print(f"[dim]  {name}: {event['records']} records[/]")
    elif kind == "error":
        err.print(f"[red]  {name}: ERROR ({event['error_kind']}) {escape(event['reason'])}[/]")
        if event.get("fix"):
            err.print(f"[dim]    fix: {escape(event['fix'])}[/]")
    elif kind == "table_warning":
        err.print(f"[yellow]  warning: {escape(event['reason'])}[/]")
        if event.get("fix"):
            err.print(f"[dim]    fix: {escape(event['fix'])}[/]")
    elif kind == "archive_warning":
        err.print(f"[yellow]  warning: archive: {escape(event['reason'])}[/]")
        if event.get("fix"):
            err.print(f"[dim]    fix: {escape(event['fix'])}[/]")


# --- status ------------------------------------------------------------------------------


@main.command(epilog=EXIT_CODES)
@click.argument("mission", required=False)
@_json_option
def status(mission: str | None, as_json: bool) -> None:
    """List missions, their archives, and the installed collector types and transforms.

    Given a mission, list every archive it has, oldest first (under --json, as
    missions[0].history): the id `query --archive` takes, its path, when it started and
    finished, its state, and its tables. A plugin that failed to load is listed, and warned
    about, rather than hidden.

    \b
    recon status
    recon status my-scan --json
    """
    document = _operations().status(mission)
    if as_json:
        _emit(document.document())
        return
    plugins = document.plugins
    for broken in plugins.broken:
        err.print(
            f"[yellow]warning: plugin {escape(broken.group)}:{escape(broken.name)} "
            f"failed to load: {escape(broken.error)}[/]"
        )
    if not document.missions:
        err.print(f"[dim]No missions under {document.root}. Start one: recon init <name>[/]")
    else:
        _print_table(_missions_table(document))
    for table in _archive_tables(document):
        _print_table(table)
    types = ", ".join(c.type for c in plugins.collectors) or "(none)"
    err.print(f"[dim]Collector types: {types}[/]")
    err.print(f"[dim]Transforms: {', '.join(plugins.transforms) or '(none)'}[/]")


def _missions_table(document: recon.StatusDocument) -> Table:
    table = Table(show_header=True, header_style="dim")
    for column in ("Mission", "Archives", "Latest", "State", "Captures"):
        table.add_column(column, justify="right" if column in ("Archives", "Captures") else "left")
    for mission in document.missions:
        latest = mission.latest
        state = "-" if latest is None else latest.state
        shown = "[yellow]incomplete[/]" if state == "incomplete" else state
        table.add_row(
            mission.name,
            str(mission.archives),
            latest.id if latest else "-",
            shown,
            str(mission.captures),
        )
    return table


def _archive_tables(document: recon.StatusDocument) -> list[Table]:
    """For a mission listed with its history, every archive it has, oldest first."""
    tables = []
    for mission in document.missions:
        if mission.history is None:
            continue
        table = Table(show_header=True, header_style="dim", title=f"{mission.name}: archives")
        for column in ("Archive", "Finished", "State", "Tables", "Failed", "Records"):
            numeric = column in ("Tables", "Failed", "Records")
            table.add_column(column, justify="right" if numeric else "left")
        for archive in mission.history:
            failed = [t for t in archive.tables if t.status == "error"]
            table.add_row(
                archive.id,
                archive.finished_at or "-",
                "[yellow]incomplete[/]" if archive.state == "incomplete" else archive.state,
                str(len(archive.tables)),
                str(len(failed)),
                str(sum(t.records or 0 for t in archive.tables)),
            )
        tables.append(table)
    return tables


# --- query -------------------------------------------------------------------------------


@main.command(epilog=EXIT_CODES)
@click.argument("name")
@click.argument("sql")
@click.option(
    "--archive",
    default=None,
    help="An archive id, as `recon status <mission>` lists them (default: the latest)",
)
@_json_option
def query(name: str, sql: str, archive: str | None, as_json: bool) -> None:
    """Query a mission's latest archive, or the one --archive names, with SQL (DuckDB).

    Each table is a view named after its file, every character but a letter, digit or _
    becoming _ (oa-works is oa_works); SQL ignores its case. The output names the archive
    the rows came from. An older archive's table, or a table's raw lines, is read by its
    file: read_json_auto('<archive dir>/<table>.jsonl') for an earlier archive,
    read_ndjson_objects(...) for each line as JSON text, and WITH ORDINALITY after either
    for each row's line number in the file. Read one archive's file: a glob across archives
    numbers all its files' lines as one, and their columns can differ.

    \b
    recon query my-scan "SELECT title, year FROM s2_search LIMIT 10"
    recon status my-scan
    recon query my-scan "SELECT count(*) FROM s2_search" --archive 2026-04-01-143022-000000
    """
    setup = _telemetry_warnings()
    document = _operations().query(name, sql, archive=archive)
    document = document.model_copy(update={"warnings": setup + document.warnings})
    if as_json:
        _emit(document.document())
        return
    err.print(f"[dim]archive {document.archive_id} ({escape(document.archive)})[/]")
    _print_warnings(document.warnings)
    if document.state == "incomplete":
        err.print(
            "[yellow]warning: this archive is incomplete (a table failed or the survey was "
            "stopped), so rows may be missing; `recon status` shows which tables failed[/]"
        )
    if not document.rows:
        err.print("[yellow]No rows.[/]")
        return
    table = Table()
    for column in document.columns:
        table.add_column(column)
    for row in document.rows:
        table.add_row(*(escape(str(value)) for value in row.values()))
    _print_table(table)


# --- templates and capture ---------------------------------------------------------------


@main.command(epilog=EXIT_CODES)
@_json_option
def templates(as_json: bool) -> None:
    """List the built-in templates `recon init -t` takes."""
    document = _operations().templates()
    if as_json:
        _emit(document.document())
        return
    for item in document.templates:
        out.print(f"  [bold]{item.name}[/]  {escape(item.description)}")


@main.command(epilog=EXIT_CODES)
@click.argument("name")
@_json_option
def capture(name: str, as_json: bool) -> None:
    """Append a Claude Code PostToolUse hook payload (JSON on stdin) to a mission's capture
    log; a `capture` collector in the mission normalizes it on the next survey.

    \b
    Hook (settings.json), capturing every MCP tool result:
      {"hooks": {"PostToolUse": [{"matcher": "mcp__.*",
        "hooks": [{"type": "command", "command": "recon capture my-mcp --json"}]}]}}
    """
    if sys.stdin.isatty():
        raise recon.UsageError(
            "recon capture reads one PostToolUse hook payload from stdin, and stdin is a terminal",
            fix=f"pipe the payload in: recon capture {name} < payload.json, or run it from a "
            "PostToolUse hook",
        )
    data = sys.stdin.read()
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise recon.ConfigError(
            f"stdin is not JSON ({exc})",
            fix="pipe one PostToolUse hook payload (a JSON object) to recon capture",
        ) from exc
    line = _operations().capture(name, payload)
    if as_json:
        _emit(line.document())
        return
    err.print(f"[dim]captured {line.id} ({line.collector})[/]")

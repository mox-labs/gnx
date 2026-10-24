"""The ``recon`` command line: init, survey, stream, status, query, watch, schedule, templates,
capture.

An inbound adapter over the library API and nothing else: every call goes through
``recon``'s declared names, so a program importing ``recon`` gets exactly what these
commands print. ``main(obj=...)`` runs the commands over the operations given instead, a
root built with other ports: that is how a test drives the command line.

Data goes to stdout and commentary to stderr. ``--json`` prints one versioned document
(``watch --json``: one a line, a read each). A
command that fails, a usage error, Ctrl-C and a defect in recon included, prints the
``recon.v1.error`` envelope on stderr (one JSON line under ``--json``) and exits with the
code its kind maps to. A survey whose tables failed still prints its document on stdout,
then the envelope for what the failures add up to. SIGTERM is handled as Ctrl-C is, so a
stopped survey kills the command it was running. Nothing prompts. Off a terminal, nothing
is wrapped: a path or a command stays on one line, whole.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import rich_click as click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

import recon

if TYPE_CHECKING:
    from collections.abc import Iterator
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
                fix="run the command again; a survey stopped so leaves its archive incomplete, "
                "ended: stopped",
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
    recon watch my-scan                   follow an archive as a survey writes it
    recon stream my-feed --stream ev      collect a source that does not end, in windows
    recon schedule install my-scan        run it by the OS scheduler, as schedule: says
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
    help="Validate and print the plan. Nothing is written; nothing is fetched or run, except "
    "what a collector type's preflight says (mcp starts each stdio server to list its tools)",
)
@_json_option
def survey(name: str, config: Path | None, replace: bool, dry_run: bool, as_json: bool) -> None:
    """Plan and run a collection mission.

    Every survey validates first: an invalid config exits 3 listing every problem, before
    anything is fetched or written. A survey with failed tables still writes its archive
    (marked incomplete); the exit code says what to do about the failures. A mission with
    streams runs one window of each stream, in turn, after its snapshot tables, each up to
    its window: (300 s by default): --dry-run says when the survey returns at the latest,
    as survey.returns. One survey of a mission runs at a time: a second, started while one
    runs, exits 11 (busy) before it writes anything, naming the first one's pid.

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
        click.echo(document.path)
    failure = recon.survey_failure(document)
    if failure is not None:
        _fail(failure, as_json=as_json)


def _instant(ctx: click.Context, param: click.Parameter, value: str | None) -> datetime | None:
    """A time given on the command line: ISO 8601, in UTC unless it names its offset."""
    if value is None:
        return None
    try:
        at = datetime.fromisoformat(value)
    except ValueError as exc:
        raise click.BadParameter(
            f"{value!r} is not an ISO 8601 time (2026-10-24T18:00:00Z)", ctx, param
        ) from exc
    return at if at.tzinfo is not None else at.replace(tzinfo=UTC)


# --- stream ------------------------------------------------------------------------------


@main.command(epilog=EXIT_CODES)
@click.argument("name")
@click.option(
    "--stream",
    "collector",
    required=True,
    metavar="NAME",
    help="The stream to run: a collector of the mission with window:, or of a type whose "
    "records do not end (--stream, as watch and query name it)",
)
@click.option(
    "--windows",
    type=int,
    default=None,
    metavar="N",
    help="Stop after sealing N windows (default: 1, unless --until or --forever is given)",
)
@click.option(
    "--until",
    callback=_instant,
    default=None,
    metavar="TIME",
    help="Stop at TIME, ISO 8601 (UTC unless it names its offset); the window it cuts is "
    "sealed until",
)
@click.option(
    "--forever",
    is_flag=True,
    help="Run until stopped (Ctrl-C or SIGTERM seal the window being written, then return)",
)
@_config_option
@_replace_option
@click.option(
    "-n", "--dry-run", is_flag=True, help="Validate and print the plan, every default with it"
)
@_json_option
def stream(
    name: str,
    collector: str,
    windows: int | None,
    until: datetime | None,
    forever: bool,
    config: Path | None,
    replace: bool,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Collect a stream: a source that does not end, into windows, each sealed as an archive.

    Each window ends when the first of its entry's window: bounds is reached (seconds,
    records, bytes). It runs one window unless told otherwise, so it returns, but it holds
    the command until then: a window of the default 300 s is five minutes, longer than a
    tool call may wait. Run --dry-run first and read stream.returns, the seconds the run
    lasts at most; when that is too long, set a shorter window: or run it in the background.
    Run again, it goes on from the last window, after the last record it kept. Follow it as
    it is written with `recon watch NAME --stream COLLECTOR`; query it with --stream.

    \b
    recon stream my-feed --stream events --dry-run
    recon stream my-feed --stream events --windows 12
    recon stream my-feed --stream events --until 2026-10-24T18:00:00Z --json
    recon stream my-feed --stream events --forever
    """
    setup = _telemetry_warnings()
    if dry_run:
        plan = _operations().plan(
            name, config=config, replace=replace, stream=collector, windows=windows,
            until=until, forever=forever,
        )  # fmt: skip
        plan = plan.model_copy(update={"warnings": setup + plan.warnings})
        if as_json:
            _emit(plan.document())
        else:
            _print_plan(name, plan)
        return
    if not as_json:
        _print_warnings(setup)
    stop = recon.StreamStop()
    with _stopping(stop, as_json=as_json):
        document = _operations().stream(
            name, collector, windows=windows, until=until, forever=forever, config=config,
            replace=replace, on_event=None if as_json else _print_event, stop=stop,
        )  # fmt: skip
    document = document.model_copy(update={"warnings": setup + document.warnings})
    if as_json:
        _emit(document.document())
    else:
        _print_warnings(document.warnings)
        err.print(f"[dim]{document.windows} window(s), {document.records} record(s), "
                  f"{document.dropped} dropped; stream {escape(document.stream)}: "
                  f"{document.state}[/]")  # fmt: skip
        click.echo(document.path)
    failure = recon.stream_failure(document)
    if failure is not None:
        _fail(failure, as_json=as_json)


@contextlib.contextmanager
def _stopping(stop: recon.StreamStop, *, as_json: bool) -> Iterator[None]:
    """Ctrl-C and SIGTERM ask ``stop``, never raise: the first seals the window being
    written once what waits is written, the second seals it at once, and a stop of a stream
    is no failure. After the second, the handlers before these are back, so a third is the
    interrupt every other command has."""
    signals = (signal.SIGINT, signal.SIGTERM)
    previous = {signum: signal.getsignal(signum) for signum in signals}
    asked = 0

    def restore() -> None:
        for signum, handler in previous.items():
            signal.signal(signum, handler)

    def ask(signum: int, frame: FrameType | None) -> None:
        nonlocal asked
        asked += 1
        stop.ask()
        if asked > 1:
            restore()
        if not as_json:  # raw, on the descriptor: a handler may land inside another write
            said = (b"stopping now: what waits is counted dropped\n" if asked > 1 else
                    b"stopping: the window being written is sealed once what waits is "
                    b"written (again to stop now)\n")  # fmt: skip
            with contextlib.suppress(OSError):
                os.write(2, said)

    for signum in signals:
        signal.signal(signum, ask)
    try:
        yield
    finally:
        restore()


def _print_plan(name: str, plan: recon.PlanDocument) -> None:
    _print_warnings(plan.warnings)
    table = Table(show_header=True, header_style="dim", title=f"recon survey {name}: plan")
    for column in ("Output", "Type", "Source", "Effects", "Does"):
        table.add_column(column)
    for run in plan.runs:
        effects = run["effects"] if isinstance(run["effects"], str) else ", ".join(run["effects"])
        table.add_row(run["output"], run["type"], run["source"] or "-", effects, escape(_does(run)))
    _print_table(table)
    skips: dict[str, list[str]] = {}
    for s in plan.skipped:
        skips.setdefault(s["collector"], []).append(f"{s['source']} ({s['kind']})")
    for collector, passed in skips.items():
        out.print(f"[dim]{escape(collector)} skips {escape(', '.join(passed))}: its type does "
                  "not use sources of those kinds[/]")  # fmt: skip
    for required in plan.requirements:
        holds = "yes" if required.holds else "[red]NO[/] "
        said = f"{required.need} ({', '.join(required.needed_by)})"
        if not required.holds:
            said += "".join(f"; {part}" for part in (required.why, required.fix) if part)
        out.print(f"{holds}  {escape(said)}")
    if not plan.runnable:
        out.print("[red]Runnable: no[/]: a survey fails each table whose requirement is NO")
    for value in plan.provenance:
        said = f"{value.path} = {json.dumps(value.value)} ({value.origin}"
        said += f": {value.note})" if value.note else ")"
        out.print(f"[dim]{escape(said)}[/]")
    if plan.writes.config:
        out.print(f"[dim]Would copy the config to {plan.writes.config}[/]")
    if plan.preflight:
        for said in plan.preflight:
            out.print(f"[dim]The preflight of {escape(said)}.[/]")
        out.print(f"[dim]{len(plan.runs)} run(s); nothing else was fetched or run, and nothing "
                  "was written.[/]")  # fmt: skip
    else:
        out.print(f"[dim]{len(plan.runs)} run(s); nothing was fetched, executed or written.[/]")


def _does(run: dict[str, Any]) -> str:
    """A run's request (its method, URL, headers and body, a line each), its MCP call and the
    server it goes to, or its commands and the directory they run in."""
    if run.get("server") and run.get("method"):
        target = run.get("tool") or run.get("resource")
        lines = [f"{run['method']} {target}"]
        if run.get("arguments"):
            lines.append(json.dumps(run["arguments"]))
        lines.append(f"on {run['server']}")
        return "\n".join(lines)
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
    elif kind == "stream":
        err.print(f"[dim]stream {escape(name)}: {escape(event['returns'])}[/]")
    elif kind == "window":
        said = (f"  {name} window {event['index']} ({event['id']}): {event['records']} "
                f"records, {event['dropped']} dropped, closed by {event['closed_by']}")  # fmt: skip
        if event["status"] == "error":
            err.print(f"[red]{escape(said)}: ERROR {escape(event.get('reason') or '')}[/]")
            if event.get("fix"):
                err.print(f"[dim]    fix: {escape(event['fix'])}[/]")
        else:
            err.print(f"[dim]{escape(said)}[/]")


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
    _print_warnings(document.warnings)
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
    for listed in document.missions:
        for stream in listed.streams:
            latest = stream.latest.id if stream.latest else "-"
            said = (f"{listed.name} stream {stream.name}: {stream.state or '-'}, "
                    f"{stream.windows} window(s), latest {latest}")  # fmt: skip
            err.print(f"[dim]{escape(said)}[/]")
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
    help="An archive id, as `recon status <mission>` lists them (default: the latest); with "
    "--stream, one window's id",
)
@click.option(
    "--stream",
    default=None,
    metavar="NAME",
    help="Query the stream's windows instead, each table the union of its windows' (default: "
    "every window)",
)
@click.option(
    "--since",
    callback=_instant,
    default=None,
    metavar="TIME",
    help="With --stream: only the windows that end after TIME (ISO 8601, UTC unless it says)",
)
@click.option(
    "--until",
    callback=_instant,
    default=None,
    metavar="TIME",
    help="With --stream: only the windows that open before TIME",
)
@_json_option
def query(
    name: str,
    sql: str,
    archive: str | None,
    stream: str | None,
    since: datetime | None,
    until: datetime | None,
    as_json: bool,
) -> None:
    """Query a mission's latest archive, or the one --archive names, with SQL (DuckDB).

    Each table is a view named after its file, every character but a letter, digit or _
    becoming _ (oa-works is oa_works); SQL ignores its case. The output names the archive
    the rows came from. An older archive's table, or a table's raw lines, is read by its
    file: read_json_auto('<archive dir>/<table>.jsonl') for an earlier archive,
    read_ndjson_objects(...) for each line as JSON text, and WITH ORDINALITY after either
    for each row's line number in the file. Read one archive's file: a glob across archives
    numbers all its files' lines as one, and their columns can differ. With --stream, the
    stream's windows are read as one, chosen by --since and --until from their ids alone;
    windows collected under different configs are refused, with the ranges to narrow to.

    \b
    recon query my-scan "SELECT title, year FROM s2_search LIMIT 10"
    recon status my-scan
    recon query my-scan "SELECT count(*) FROM s2_search" --archive 2026-04-01-143022-000000
    recon query my-feed "SELECT count(*) FROM events" --stream events --since 2026-10-24T00:00Z
    """
    setup = _telemetry_warnings()
    document = _operations().query(
        name, sql, archive=archive, stream=stream, since=since, until=until
    )
    document = document.model_copy(update={"warnings": setup + document.warnings})
    if as_json:
        _emit(document.document())
        return
    err.print(f"[dim]archive {document.archive} ({escape(document.path)})[/]")
    _print_warnings(document.warnings)
    if document.state == "incomplete":
        err.print(
            "[yellow]warning: this archive is incomplete (a table failed or the survey was "
            "stopped), so rows may be missing; `recon status` shows which tables failed[/]"
        )
    elif document.state == "writing":
        err.print(
            "[yellow]warning: a survey is writing this archive now, so only its tables "
            "already sealed were queried; query it again once `recon status` no longer says "
            "writing[/]"
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


# --- watch -------------------------------------------------------------------------------


@main.command(epilog=EXIT_CODES)
@click.argument("name")
@click.option(
    "--archive",
    default=None,
    help="An archive id, as `recon status <mission>` lists them (default: the latest); with "
    "--stream, the window to start from",
)
@click.option(
    "--stream",
    default=None,
    metavar="NAME",
    help="Follow the stream instead, window after window, from its latest window",
)
@click.option(
    "--cursor",
    default=None,
    help="Read on from where a read left off: the `cursor` a --json line gave (default: from "
    "the archive's first record)",
)
@click.option(
    "--interval",
    "interval_s",
    type=float,
    default=None,
    metavar="SECONDS",
    help="Seconds between two reads once caught up with a survey still writing (default: 1)",
)
@click.option(
    "--max-bytes",
    "max_bytes",
    type=int,
    default=None,
    metavar="BYTES",
    help="The most bytes of records one read prints, cut at a whole record and at least one "
    "(default: 1 MiB); a read it cuts is followed at once",
)
@click.option("--once", is_flag=True, help="Read once, print it, and return, finished or not")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Each read as one JSON document a line on stdout; errors as JSON on stderr",
)
def watch(
    name: str,
    archive: str | None,
    stream: str | None,
    cursor: str | None,
    interval_s: float | None,
    max_bytes: int | None,
    once: bool,
    as_json: bool,
) -> None:
    """Follow a mission's latest archive, or the one --archive names, as a survey writes it.

    Prints each record as it is written, from the tables already sealed and the one being
    written, and returns when the archive is no longer being written and all of it has
    been printed: at once for a finished archive. A record is printed as
    {"table", "record"}, a JSON line on stdout; under --json each read is one
    recon.v1.records-since document a line instead, with the `cursor` to read on from.
    A table that fails while it is watched is retracted: the records printed from it are
    void, and stderr (or `retracted`) says so. Reading writes nothing. With --stream, the
    stream is followed from its latest window (or --archive's) into each window after it,
    and the watch returns once nothing extends the stream and its last window is printed.

    \b
    recon watch my-scan
    recon watch my-scan --json --once
    recon watch my-scan --json --max-bytes 65536
    recon watch my-scan --archive 2026-04-01-143022-000000 --cursor <cursor> --json
    recon watch my-feed --stream events --json
    """
    given: dict[str, Any] = {} if interval_s is None else {"interval_s": interval_s}
    if max_bytes is not None:
        given["max_bytes"] = max_bytes
    if not as_json:
        every = f"{interval_s:g}s (--interval)" if interval_s is not None else "1s (default)"
        err.print(f"[dim]watching {escape(name)}; reading again every {every} while it is "
                  "written[/]")  # fmt: skip
    if stream is not None:
        given["stream"] = stream
    for read in _operations().watch(name, cursor, archive=archive, **given):
        if as_json:
            click.echo(json.dumps(read.document(), default=str))
        else:
            _print_read(read)
        if once:
            return


def _print_read(read: recon.RecordsSince) -> None:
    if read.retired_range is not None:
        jumped = read.retired_range
        err.print(f"[yellow]  {jumped.count} window(s) from {escape(str(jumped.first))} were "
                  f"retired before they were read: going on from {read.archive}[/]")  # fmt: skip
    for record in read.table_records:
        click.echo(json.dumps(record, default=str))
    for table in read.exhausted:
        err.print(f"[dim]  {escape(table)}: every record read[/]")
    for table in read.retracted:
        err.print(f"[yellow]  {escape(table)} failed and was retracted: the records printed "
                  "from it are void[/]")  # fmt: skip
    if read.state != "writing":
        shown = "[yellow]incomplete[/]" if read.state == "incomplete" else read.state
        err.print(f"[dim]archive {read.archive}:[/] {shown}")


# --- schedule ----------------------------------------------------------------------------


_platform_option = click.option(
    "--platform",
    type=click.Choice(["launchd", "systemd", "cron"]),
    default=None,
    help="The scheduler (default: the host's: launchd on macOS, systemd's user timers on "
    "Linux where they run, else cron)",
)


@main.group(epilog=EXIT_CODES)
def schedule() -> None:
    """Run missions by the operating system's own scheduler, as each mission's schedule: says.

    install projects the mission's schedule: {every, missed} onto launchd's user agents,
    systemd's user timers or the user's crontab, and has it load them; list, remove and
    status say and undo what is installed. There is no recon daemon: the scheduler starts
    each tick (`recon schedule tick`), one survey. A tick refuses a config changed since
    install, so read the change, then install again.

    \b
    recon schedule install my-scan --json
    recon schedule status --json
    recon schedule remove my-scan
    """


def _print_schedule(document: recon.ScheduleDocument) -> None:
    _print_warnings(document.warnings)
    said = {"installed": "Installed", "unchanged": "Unchanged", "replaced": "Replaced",
            "removed": "Removed", "absent": "No schedule to remove"}  # fmt: skip
    err.print(f"[green]{said[document.outcome]}:[/] {escape(document.mission)} on "
              f"{document.platform} ({escape(document.label)})")  # fmt: skip
    for file in document.files:
        click.echo(file)


def _print_schedules(document: recon.SchedulesDocument) -> None:
    _print_warnings(document.warnings)
    if not document.schedules:
        err.print(f"[dim]No schedules on {document.platform} for {escape(document.root)}[/]")
        return
    table = Table(show_header=True, header_style="dim")
    for column in ("Mission", "Every", "Installed", "Config changed", "Next"):
        table.add_column(column)
    for listed in document.schedules:
        table.add_row(
            listed.mission,
            f"{listed.every} s" if listed.every is not None else "-",
            "yes" if listed.installed else "[yellow]no[/]",
            {True: "[yellow]yes[/]", False: "no", None: "-"}[listed.config_changed],
            listed.next_at or "-",
        )
    _print_table(table)


@schedule.command(epilog=EXIT_CODES)
@click.argument("name")
@_platform_option
@_json_option
def install(name: str, platform: str | None, as_json: bool) -> None:
    """Install the mission's schedule: write the scheduler's files (owner-only) and load them.

    Again with the same config, it changes nothing (outcome: unchanged); with another, it
    replaces the schedule. Each tick runs `recon schedule tick` with the digest of the
    config installed, and refuses another. Exit 3 for a mission with no schedule:.

    \b
    recon schedule install my-scan --json
    recon schedule install my-scan --platform cron
    """
    document = _operations().schedule_install(name, platform=platform)
    if as_json:
        _emit(document.document())
    else:
        _print_schedule(document)


@schedule.command(name="list", epilog=EXIT_CODES)
@_platform_option
@_json_option
def list_(platform: str | None, as_json: bool) -> None:
    """List the schedules installed for this project's missions."""
    document = _operations().schedule_list(platform=platform)
    if as_json:
        _emit(document.document())
    else:
        _print_schedules(document)


@schedule.command(epilog=EXIT_CODES)
@click.argument("name")
@_platform_option
@_json_option
def remove(name: str, platform: str | None, as_json: bool) -> None:
    """Unload and remove the mission's schedule (outcome: removed, or absent when there was
    none). The mission and its archives stay."""
    document = _operations().schedule_remove(name, platform=platform)
    if as_json:
        _emit(document.document())
    else:
        _print_schedule(document)


@schedule.command(name="status", epilog=EXIT_CODES)
@click.argument("mission", required=False)
@_platform_option
@_json_option
def schedule_status(mission: str | None, platform: str | None, as_json: bool) -> None:
    """Each schedule of the project, or MISSION's: whether it is installed, whether its
    config changed since (then its ticks are refused: install it again), and when its next
    tick is due."""
    document = _operations().schedule_status(mission, platform=platform)
    if as_json:
        _emit(document.document())
    else:
        _print_schedules(document)


@schedule.command(epilog=EXIT_CODES)
@click.argument("name")
@click.option(
    "--project",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="The project the mission is in; the tick runs there",
)
@click.option(
    "--config-sha256",
    "config_sha256",
    required=True,
    metavar="HEX",
    help="The digest of the config the schedule was installed for; another is refused (7)",
)
@_json_option
def tick(name: str, project: Path, config_sha256: str, as_json: bool) -> None:
    """One tick: what an installed schedule runs. A survey of the mission, logged to its
    schedule.log; run by hand, it is one tick.

    It runs from --project, refuses a config whose sha256 is not --config-sha256 (exit 7:
    install the schedule again once the change is read), is skipped and logged while another
    survey holds the mission (exit 11; the second in a row says it overruns), and under
    missed: skip runs no survey when it starts more than 60 s late. Its exit code is its
    survey's otherwise.
    """
    os.chdir(project)
    document = _operations().schedule_tick(name, config_sha256=config_sha256)
    if as_json:
        _emit(document.document())
    elif document.survey is not None:
        click.echo(document.survey.path)
    else:
        err.print(f"[dim]tick skipped ({document.skipped_by}): {escape(name)}[/]")
    if document.survey is not None:
        failure = recon.survey_failure(document.survey)
        if failure is not None:
            _fail(failure, as_json=as_json)


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
        "hooks": [{"type": "command",
                   "command": "cd \\"$CLAUDE_PROJECT_DIR\\" && recon capture my-mcp --json"}]}]}}
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

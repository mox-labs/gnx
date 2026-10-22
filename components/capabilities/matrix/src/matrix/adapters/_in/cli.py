"""``matrix``: see what is installed, check and plan a config, and run it.

    matrix --skill [-r NAME]                the skill an agent reads, or one of its references
    matrix catalog [--point P] [--json]     every registered extension, and any that failed
    matrix describe TYPE_URL [--json]       one extension: config schema, needs
    matrix config [CONFIG]... [--sources]   the merged config a tool would see, and its files
    matrix check [CONFIG]... [--json]       compose runtimes and agents, wire the components
    matrix agents [CONFIG]... [--json]      the agents a config defines, with their runtimes
    matrix plan [CONFIG]... [--json]        what a run would do; runs nothing
    matrix run [CONFIG]... [--input KIND=PATH]... [--run-id ID] [--from RUN_ID] [--json]
    matrix runs [CONFIG]... [--json]        the runs the configured store keeps, oldest first

Every command that reads config reads the tool's tiers (``--tool T``, default ``matrix``),
with each ``CONFIG`` above them, in order, when any is named; ``-`` reads one from standard
input. ``--only`` reads the ``CONFIG`` files alone, none of the tiers. ``plan`` before
``run``: the plan calls nothing, the run may call models.

Data goes to stdout, diagnostics to stderr. With ``--json``, every document carries a
``schema`` and its ``warnings``, and an error of any kind, a usage error and a defect
included, is the ``matrix.v1.error`` envelope on stderr. A run that fails prints its
``matrix.v1.run`` document on stdout and the envelope on stderr. The exit codes are
:data:`matrix.EXIT_CODES`. Nothing prompts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

import rich_click as click

import matrix

if TYPE_CHECKING:
    from collections.abc import Iterator

# rich-click rewraps an epilog's lines; a blank line between them keeps each on its own.
EXIT_CODES = "Exit codes:\n\n" + "\n\n".join(
    f"{code}  {kind}: {matrix.EXIT_MEANINGS[kind]}. Next: {matrix.EXIT_NEXT[kind]}"
    for kind, code in matrix.EXIT_CODES.items()
)

_json_option = click.option("--json", "as_json", is_flag=True, help="machine-readable output")
_tool_option = click.option("--tool", default="matrix", help="whose config tiers to read")
_config_argument = click.argument("config", nargs=-1, metavar="[CONFIG]...")
_only_option = click.option(
    "--only",
    is_flag=True,
    help="read only the CONFIG files named, none of the tiers (their definitions, runtimes "
    "and agents included)",
)


def _locations(config: tuple[str, ...]) -> list[Path | IO[str]]:
    """The CONFIG arguments as locations the loader reads, in order: ``-`` is standard input."""
    if config.count("-") > 1:
        raise matrix.UsageError(
            "standard input can be read once: only one CONFIG may be -",
            fix="write the others to files",
        )
    return [sys.stdin if name == "-" else Path(name) for name in config]


def _emit(document: dict[str, Any], as_json: bool, text: str) -> None:
    click.echo(json.dumps(document, indent=2, default=str) if as_json else text)


def _warn(warnings: list[matrix.Problem] | tuple[matrix.Problem, ...], as_json: bool) -> None:
    """Warnings travel inside the --json document; without it they are lines on stderr."""
    if as_json:
        return
    for found in warnings:
        click.echo(f"warning: {found.path}: {found.message}", err=True)


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog=EXIT_CODES,
)
@click.version_option(matrix.__version__, prog_name="matrix")
@click.option(
    "--skill",
    is_flag=True,
    help="Print the skill an agent reads: the component protocol, config, the commands and "
    "the exit codes; then exit",
)
@click.option(
    "-r",
    "--reference",
    metavar="NAME",
    help="with --skill: print one of the skill's references instead (`matrix --skill` lists them)",
)
@click.pass_context
def cli(ctx: click.Context, skill: bool, reference: str | None) -> None:
    """Inspect, check, plan and run matrix configs."""
    if reference is not None and not skill:
        raise click.UsageError("-r/--reference names a reference of the skill: use it with --skill")
    if skill:
        click.echo(matrix.skill(reference), nl=False)
        ctx.exit()
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command()
@click.option("--point", help="only this extension point (runtime, component, store, ...)")
@_json_option
def catalog(point: str | None, as_json: bool) -> None:
    """List registered extensions and failed ones."""
    found = matrix.catalog(point)
    lines = [f"{e.point:<13} {e.type_url:<36} {e.stability:<12} {e.summary}" for e in found.entries]
    lines += [f"FAILED        {f.extension} ({f.origin}): {f.error}" for f in found.failures]
    _emit(found.document(), as_json, "\n".join(lines) or "(nothing registered)")


@cli.command()
@click.argument("type_url")
@_json_option
def describe(type_url: str, as_json: bool) -> None:
    """Show one extension's config schema and needs."""
    found = matrix.describe_extension(type_url)
    text = "\n\n".join(json.dumps(e.document(), indent=2) for e in found.entries)
    _emit(found.document(), as_json, text)


@cli.command()
@_config_argument
@_tool_option
@_only_option
@click.option("--sources", is_flag=True, help="only list the files")
@_json_option
def config(config: tuple[str, ...], tool: str, only: bool, sources: bool, as_json: bool) -> None:
    """Show the merged config and the files it came from."""
    if sources:
        listed = matrix.config_sources(tool, _locations(config), only=only)
        _emit(listed.document(), as_json, "\n".join(listed.sources))
        return
    document = matrix.config_view(tool, _locations(config), only=only).document()
    _emit(document, as_json, json.dumps(document, indent=2))


@cli.command()
@_config_argument
@_tool_option
@_only_option
@_json_option
def check(config: tuple[str, ...], tool: str, only: bool, as_json: bool) -> None:
    """Compose runtimes and agents, wire components; call nothing."""
    report = matrix.check_config(tool, _locations(config), only=only)
    text = (
        f"ok: {len(report.agent_runtimes)} agent runtimes, {len(report.model_runtimes)} model "
        f"runtimes, {len(report.agents)} agents compose, {len(report.components)} components "
        "wire (nothing was called)"
    )
    _warn(report.warnings, as_json)
    _emit(report.document(), as_json, text)


@cli.command()
@_config_argument
@_tool_option
@_only_option
@_json_option
def agents(config: tuple[str, ...], tool: str, only: bool, as_json: bool) -> None:
    """List the agents a config defines."""
    found = matrix.list_agents(tool, _locations(config), only=only)
    text = "\n".join(f"{a.name:<24} {a.runtime:<14} {a.description}" for a in found.agents)
    _warn(found.warnings, as_json)
    _emit(found.document(), as_json, text or "(no agents)")


@cli.command()
@_config_argument
@_tool_option
@_only_option
@_json_option
def plan(config: tuple[str, ...], tool: str, only: bool, as_json: bool) -> None:
    """Say what a run would do: the order of work, the agents and runtimes in use, how many
    agent and model calls it makes, every problem at once. Runs nothing."""
    found = matrix.plan_config(tool, _locations(config), only=only)
    lines = [f"inputs: {', '.join(found.inputs) or '(none)'}"]
    for number, stage in enumerate(found.stages, start=1):
        lines.append(f"stage {number}: {', '.join(stage)}")
    for c in found.components:
        if c.agent:
            on = f" (agent {c.agent} on {c.runtime})"
        elif c.runtime:
            on = f" (model {c.model or 'default'} on {c.runtime})"
        else:
            on = ""
        lines.append(f"  {c.name:<20} {c.type}{on}")
    for what, low, high in (
        ("agent", found.agent_calls_min, found.agent_calls_max),
        ("model", found.model_calls_min, found.model_calls_max),
    ):
        lines.append(f"{what} calls: {low} to {'unknown' if high is None else high}")
    lines.append(f"record: {found.store.location}")
    _warn(found.warnings, as_json)
    _emit(found.document(), as_json, "\n".join(lines))


@cli.command(name="run")
@_config_argument
@_tool_option
@_only_option
@click.option(
    "--input",
    "inputs",
    multiple=True,
    metavar="KIND=PATH",
    help="rows of a run-input kind, one JSON value a line; PATH - is standard input",
)
@click.option("--run-id", help="the run's id (default: <UTC time>-<4 hex digits>)")
@click.option(
    "--from",
    "reuse_from",
    metavar="RUN_ID",
    help="start from a recorded run: its inputs, and the rows of every component that "
    "completed there and is unchanged",
)
@_json_option
def run_command(
    config: tuple[str, ...],
    tool: str,
    only: bool,
    inputs: tuple[str, ...],
    run_id: str | None,
    reuse_from: str | None,
    as_json: bool,
) -> None:
    """Run the configured components, recorded in the configured store. `matrix plan` first:
    a run may call models."""
    if reuse_from is not None and inputs:
        raise matrix.UsageError(
            "--from takes the recorded run's inputs; --input cannot be given with it",
            fix="drop --input, or drop --from to start a new run",
        )
    if "-" in config and any(item.partition("=")[2] == "-" for item in inputs):
        raise matrix.UsageError(
            "standard input can be read once: CONFIG and an --input cannot both be -",
            fix="write one of them to a file",
        )
    named = _locations(config)
    if only and not named:
        raise matrix.UsageError(
            "--only reads the CONFIG files named and no tier, but none was named",
            fix="name the config to read: matrix run CONFIG --only",
        )
    supplied = _read_inputs(inputs)
    loaded = matrix.load_config(
        client_key=tool,
        sources=named if only else [*matrix.discover_sources(tool), *named] if named else None,
    )
    container = matrix.compose(loaded)
    telemetry = matrix.configure_telemetry()
    warnings = [
        *container.warnings,
        *(w for w in telemetry.warnings if w not in container.warnings),
    ]
    _warn(warnings, as_json)
    extra = []
    if not as_json and sys.stderr.isatty():
        extra.append(container.registry.create("observer", matrix.observer_type_url("console")))
    failure: matrix.MatrixError | None = None

    async def carried() -> matrix.Run | matrix.RunCancelled:
        # A cancellation that leaves a task becomes a bare CancelledError: hand it back as a
        # value so the partial run it carries is printed.
        try:
            return await container.run(
                supplied or None, run_id=run_id, reuse_from=reuse_from, observers=extra
            )
        except matrix.RunCancelled as cancelled:
            return cancelled

    # A loop of its own, without asyncio.run's SIGINT handler: Ctrl-C and SIGTERM reach the
    # run as its cancellation (matrix.cancel_on_signals), and it stops cleanly.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        outcome = loop.run_until_complete(carried())
        if isinstance(outcome, matrix.RunCancelled):
            record = outcome.run
            failure = _Interrupted("the run was interrupted", fix=matrix.EXIT_NEXT["interrupted"])
        else:
            record = outcome
    except matrix.RunError as error:
        record, failure = error.run, error
    finally:
        asyncio.set_event_loop(None)
        loop.close()
        telemetry.shutdown()
    location = container.location(record.run_id)
    tables = _tables(container, record.run_id)
    document = record.document() | {
        "location": location,
        "warnings": [w.document() for w in warnings],
    }
    if tables:
        document["tables"] = tables
    text = "\n".join([f"run {record.run_id} {record.status}: {location}", *_files(tables)])
    _emit(document, as_json, text)
    if failure is not None:
        failure.details["location"] = location
        raise failure


@cli.command()
@_config_argument
@_tool_option
@_only_option
@_json_option
def runs(config: tuple[str, ...], tool: str, only: bool, as_json: bool) -> None:
    """List the runs the configured store keeps, oldest first: id, status, start, where.
    Builds only the store, so a config that does not compose still lists its runs."""
    found = matrix.list_runs(tool, _locations(config), only=only)
    lines = [
        f"{r.run_id:<26} {r.status:<11} {r.started or '-':<34} {r.location}" for r in found.runs
    ]
    _emit(found.document(), as_json, "\n".join(lines) or "(no runs)")


def _tables(container: matrix.Container, run_id: str) -> dict[str, Any]:
    """The record's tables, ``{kind: {file, rows}}``, as a store that keeps files says; an
    empty mapping from one that does not, or when the record cannot be read back."""
    try:
        tables = container.store.describe(run_id).get("tables") or {}
    except matrix.MatrixError:
        return {}
    return {kind: info for kind, info in tables.items() if isinstance(info, dict)}


def _files(tables: dict[str, Any]) -> list[str]:
    """Where to read a recorded run, a line a file, for a store that keeps files."""
    if not tables or not all("file" in info for info in tables.values()):
        return []
    width = max(len(str(info.get("file", ""))) for info in tables.values())
    lines = [f"  {'run.json':<{width}}  the run: status, how each component ended, failures"]
    lines += [
        f"  {str(info.get('file', '')):<{width}}  {_rows_of(info.get('rows', 0))} of {kind}"
        for kind, info in tables.items()
    ]
    lines.append(f"  {'events.jsonl':<{width}}  the events, one a line: telemetry, lossy, no facts")
    lines.append("reading a run: matrix --skill -r running")
    return lines


def _rows_of(count: Any) -> str:
    return f"{count} row" if count == 1 else f"{count} rows"


def _read_inputs(pairs: tuple[str, ...]) -> dict[str, list[Any]]:
    """``KIND=PATH`` pairs read as rows: one JSON value a line, blank lines skipped."""
    supplied: dict[str, list[Any]] = {}
    for pair in pairs:
        kind, sep, path = pair.partition("=")
        if not sep or not kind or not path:
            raise matrix.UsageError(
                f"--input {pair!r} is not KIND=PATH",
                fix="name the kind and a JSONL file: --input acme.v1.diff=diffs.jsonl",
            )
        if path == "-":
            if any(p.partition("=")[2] == "-" for p in pairs if p != pair):
                raise matrix.UsageError(
                    "standard input can be read once: only one --input may be -",
                    fix="write the others to files",
                )
            supplied.setdefault(kind, []).extend(_rows(sys.stdin, kind, "<stdin>"))
            continue
        file = Path(path)
        if not file.is_file():
            raise matrix.NotFoundError(
                f"--input {kind}: there is no file at {file}",
                fix="name an existing JSONL file, or - for standard input",
                path=str(file),
            )
        with file.open(encoding="utf-8") as stream:
            supplied.setdefault(kind, []).extend(_rows(stream, kind, str(file)))
    return supplied


def _rows(stream: IO[str], kind: str, source: str) -> list[Any]:
    rows: list[Any] = []
    problems: list[matrix.Problem] = []
    for number, line in enumerate(stream, start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            problems.append(
                matrix.Problem(
                    path=f"inputs.{kind}",
                    kind="invalid_value",
                    message=f"line {number} is not JSON: {e.msg}",
                    fix="write one JSON value on each line",
                    source=source,
                )
            )
    if problems:
        raise matrix.ConfigError(
            f"the rows of {kind} in {source} are not JSON lines",
            fix="write one JSON value on each line",
            problems=problems,
        )
    return rows


def _report(error: matrix.MatrixError, as_json: bool) -> None:
    """The envelope under --json; otherwise the message, each problem with its own fix, and
    the error's fix when no problem already said it."""
    if as_json:
        click.echo(json.dumps(error.payload(), default=str), err=True)
        return
    head = error.message.split("\n", 1)[0] if error.problems else error.message
    click.echo(f"error ({error.kind}): {head}", err=True)
    for found in error.problems:
        source = f" ({found.source})" if found.source else ""
        click.echo(f"  {found.path or '(document)'}: {found.message}{source}", err=True)
        if found.fix:
            click.echo(f"    fix: {found.fix}", err=True)
    if error.fix and error.fix not in {found.fix for found in error.problems}:
        click.echo(f"fix: {error.fix}", err=True)
    if not error.complete:
        click.echo("checking stopped early: fix these and check again for more", err=True)


class _Interrupted(matrix.MatrixError):
    kind: matrix.ErrorKind = "interrupted"


def main(argv: list[str] | None = None) -> int:
    """Run one command and return the exit code of its outcome. Ctrl-C is the error kind
    ``interrupted``, reported like any other."""
    args = sys.argv[1:] if argv is None else argv
    with _quiet_runtime_log():
        return _main(args)


def _main(args: list[str]) -> int:
    try:
        cli.main(args=args, prog_name="matrix", standalone_mode=False)
    except click.UsageError as error:
        usage = matrix.UsageError(
            error.format_message(),
            fix="`matrix --help` lists the commands; `matrix <command> --help` its options",
        )
        _report(usage, as_json="--json" in args)
        return usage.exit_code
    except click.Abort:  # click's form of Ctrl-C (or of end of input, which nothing reads)
        interrupted = _Interrupted(
            "interrupted before the command finished", fix="run the command again"
        )
        _report(interrupted, as_json="--json" in args)
        return interrupted.exit_code
    except matrix.MatrixError as error:
        _report(error, as_json="--json" in args)
        return error.exit_code
    except Exception as error:  # a defect: still one envelope, so a caller parses one shape
        if "--json" not in args:
            traceback.print_exc()
        defect = matrix.MatrixError(
            f"{type(error).__name__}: {error}",
            fix="report this as a bug in matrix, with the command that raised it",
        )
        _report(defect, as_json="--json" in args)
        return defect.exit_code
    return matrix.EXIT_CODES["ok"]


@contextmanager
def _quiet_runtime_log() -> Iterator[None]:
    """While a command runs, what a runtime logs on ``matrix.RUNTIME_LOGGER`` is not printed:
    its config warnings are in every document's ``warnings`` and, without ``--json``, on
    stderr as ``warning:`` lines already; under ``--json`` stderr keeps to the envelope."""
    quiet = logging.getLogger(matrix.RUNTIME_LOGGER)
    handler, propagate = logging.NullHandler(), quiet.propagate
    quiet.addHandler(handler)
    quiet.propagate = False
    try:
        yield
    finally:
        quiet.removeHandler(handler)
        quiet.propagate = propagate


def run() -> None:
    sys.exit(main())

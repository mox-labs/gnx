"""Recon CLI — the driving adapter.

Commands:
  recon init <name> [--template | --from]      scaffold a mission
  recon survey <name> [-c FILE] [--dry-run]     plan, then run a collection mission
  recon status                                  list missions, archives and installed plugins
  recon query <name> "SQL"                      DuckDB query on an archive's tables
  recon templates                               list built-in templates
  recon capture <name>                          append a Claude Code hook payload (stdin)

Output discipline: the primary output (an archive path, a plan, rows, or JSON with
``--json``) goes to stdout; progress, warnings and errors go to stderr. Every JSON document
names its shape in ``schema`` (``recon.v1.survey``, ``recon.v1.plan``, ...), and
collections are always wrapped in an object.

Exit codes (one scheme, shared with hardline and ix):

    0  success
    1  failure not otherwise classified, including a survey whose failed tables were
       neither all transient nor any auth
    2  usage: a bad flag or argument (click)
    3  config: the input must change — an invalid mission config (every problem is
       listed), a mission config that ``-c`` would overwrite (``conflict``), SQL that fails
       (``query``), a malformed hook payload
    4  not found: a mission, archive or template
    5  transient: every failed table failed transiently (HTTP 429/5xx, network) — retry
    6  auth: at least one table failed on credentials (HTTP 401/403)

A survey with failed tables still writes its archive (marked ``.incomplete``) and still
prints every table's status; the exit code says what to do about the failures.

With ``--json`` an error is one line of JSON on stderr::

    {"error": {"kind", "message", "fix", ...}}

``kind`` is one of config, conflict, query, not_found, transient, auth, collection,
unknown. ``problems`` (``[{"path", "message"}]``) appears for config errors; ``archive``
and ``failed`` appear for a survey with failed tables. Usage errors (2) are click's prose.

Mission layout (under the git root, or the working directory outside a repository):

  .recon/<name>/
    config.yaml
    captures/                  external capture log (`recon capture`)
      captures.jsonl
      raw/<sha256>
    archive/
      2026-04-01-143022-123456/
        *.jsonl
        meta.yaml
        captures.jsonl, raw/   when preserve_raw is on
"""

from __future__ import annotations

import functools
import hashlib
import importlib.resources
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import click
import yaml
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from recon import __version__
from recon import skill as skill_module
from recon.application import query as query_module
from recon.application.archives import (
    archive_state,
    list_archives,
    read_meta,
    resolve_archive,
)
from recon.application.captures import FilesystemCaptureLog, record_hook_payload
from recon.application.plan import Plan, build_plan
from recon.application.recon import failure_kind
from recon.application.recon import run as recon_run
from recon.composition import Registry, build_collectors, load_registry
from recon.domain.capture import CAPTURE_LOG_NAME
from recon.domain.exceptions import (
    ConfigError,
    ErrorKind,
    NotFoundError,
    Problem,
    ReconError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

console = Console(stderr=True)
out = Console()

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3
EXIT_NOT_FOUND = 4
EXIT_TRANSIENT = 5
EXIT_AUTH = 6

EXIT_CODES: dict[ErrorKind, int] = {
    "config": EXIT_CONFIG,
    "conflict": EXIT_CONFIG,
    "query": EXIT_CONFIG,
    "not_found": EXIT_NOT_FOUND,
    "transient": EXIT_TRANSIENT,
    "auth": EXIT_AUTH,
    "collection": EXIT_FAILURE,
    "unknown": EXIT_FAILURE,
}

_EXIT_HELP = """\b
Exit codes: 0 ok · 1 failure · 2 usage · 3 config (incl. conflict, bad SQL)
· 4 not found · 5 transient, retry · 6 auth.
A survey with failed tables exits 6 if any failed on auth, 5 if all were transient, else 1.
With --json, an error is one JSON line on stderr: {"error": {"kind", "message", "fix"}}.
"""

_MISSION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


# --- Errors ------------------------------------------------------------------------------


def _json_mode() -> bool:
    ctx = click.get_current_context(silent=True)
    return ctx is not None and bool(ctx.params.get("as_json"))


def _fail(
    message: str,
    *,
    kind: ErrorKind,
    fix: str | None = None,
    problems: list[Problem] | None = None,
    **extra: Any,
) -> NoReturn:
    """Report a failure and exit with the kind's code. Under ``--json``: one JSON line."""
    code = EXIT_CODES[kind]
    if _json_mode():
        body: dict[str, Any] = {"kind": kind, "message": message, "fix": fix}
        if problems is not None:
            body["problems"] = [{"path": p.path, "message": p.message} for p in problems]
        body.update(extra)
        click.echo(json.dumps({"error": body}, default=str), err=True)
    else:
        console.print(f"[red]Error:[/] {escape(message)}")
        for problem in problems or ():
            console.print(f"  [red]-[/] {escape(str(problem))}")
        if fix:
            console.print(f"  [dim]Fix: {escape(fix)}[/]")
    raise SystemExit(code)


def _guarded(fn: Callable[..., None]) -> Callable[..., None]:
    """Turn any escaping error into a classified failure (never a bare traceback)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            fn(*args, **kwargs)
        except (click.exceptions.ClickException, click.exceptions.Exit, click.exceptions.Abort):
            raise
        except ConfigError as exc:
            _fail(str(exc), kind=exc.kind, problems=exc.problems or None)
        except ReconError as exc:
            _fail(str(exc), kind=exc.kind)
        except Exception as exc:
            _fail(f"{type(exc).__name__}: {exc}", kind="unknown")

    return wrapper


def _emit(doc: dict[str, Any]) -> None:
    click.echo(json.dumps(doc, indent=2, default=str))


def _json_option(fn: Callable[..., Any]) -> Callable[..., Any]:
    return click.option(
        "--json",
        "as_json",
        is_flag=True,
        help="Machine-readable output on stdout; errors become one JSON line on stderr.",
    )(fn)


# --- Project root + mission directory ----------------------------------------------------


def _find_project_root() -> Path:
    """The nearest ancestor of cwd holding `.git` (a directory, or a worktree's file)."""
    current = Path.cwd()
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return Path.cwd()
        current = parent


def _recon_dir() -> Path:
    return _find_project_root() / ".recon"


def _mission_dir(name: str) -> Path:
    if not _MISSION_NAME.match(name):
        raise click.BadParameter(
            f"{name!r}: a mission name is letters, digits, '.', '_' and '-', "
            "starting with a letter or digit",
            param_hint="NAME",
        )
    return _recon_dir() / name


def _require_mission(name: str) -> Path:
    mission = _mission_dir(name)
    if not (mission / "config.yaml").exists():
        raise NotFoundError(f"no mission {name!r} at {mission}")
    return mission


def _list_templates() -> list[str]:
    """List built-in config templates from the package."""
    try:
        configs = importlib.resources.files("recon.configs")
        return sorted(
            p.name.removesuffix(".yaml")
            for p in configs.iterdir()
            if hasattr(p, "name") and p.name.endswith(".yaml")
        )
    except (FileNotFoundError, ModuleNotFoundError):
        return []


def _load_template(name: str) -> str | None:
    """Load a built-in template by name."""
    try:
        ref = importlib.resources.files("recon.configs").joinpath(f"{name}.yaml")
        return ref.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, TypeError):
        return None


def _template_description(content: str | None) -> str:
    for line in (content or "").splitlines():
        if line.startswith("#") and not line.startswith("##"):
            return line.lstrip("# ").strip()
    return ""


def _warn_broken(registry: Registry) -> None:
    for b in registry.broken:
        console.print(f"[yellow]Warning: plugin {escape(b.describe())}[/]")


# --- Commands ----------------------------------------------------------------------------


@click.group(invoke_without_command=True, epilog=_EXIT_HELP)
@click.option("--skill", is_flag=True, help="Output skill documentation for Claude")
@click.option("--reference", "-r", help="Specific skill reference (use with --skill)")
@click.version_option(__version__, prog_name="recon")
@click.pass_context
def main(ctx: click.Context, skill: bool, reference: str | None) -> None:
    """Recon — mechanical collection system.

    Config-driven data collection from HTTP APIs, CLI tools, web pages and capture logs
    into queryable JSONL. Intelligence lives outside — write the config, plan it, run
    the survey, reason over the results.

    \b
    Quick start:
      recon init my-scan --template code-forensics
      # edit .recon/my-scan/config.yaml
      recon survey my-scan --dry-run
      recon survey my-scan
      recon query my-scan "SELECT line FROM todo_files LIMIT 20"
    """
    if skill:
        click.echo(skill_module.get_skill(reference))
        ctx.exit(0)
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.command(epilog=_EXIT_HELP)
@click.argument("name")
@click.option(
    "-t",
    "--template",
    default="code-forensics",
    help="Built-in template to use (default: code-forensics). See `recon templates`.",
)
@click.option(
    "--from",
    "from_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Scaffold from a config file (overrides --template).",
)
@_json_option
@click.pass_context
@_guarded
def init(
    ctx: click.Context,
    name: str,
    template: str,
    from_file: Path | None,
    as_json: bool,
) -> None:
    """Scaffold a new mission from a built-in template or an external config.

    \b
    Examples:
      recon init my-scan
      recon init gh-audit --template github-audit
      recon init review --from path/to/literature-catalog.yaml
    """
    mission = _mission_dir(name)
    config_path = mission / "config.yaml"

    if config_path.exists():
        _fail(
            f"mission {name!r} already exists: {config_path}",
            kind="conflict",
            fix=f"edit {config_path} and run `recon survey {name}`, or choose another name",
        )

    content: str | None
    if from_file is not None:
        content = from_file.read_text()
        origin = str(from_file)
    else:
        if template == "research":
            _fail(
                "the 'research' template moved to craft-research in recon 0.8.0",
                kind="not_found",
                fix=f"recon init {name} --from <craft-research>/skills/collecting/references/"
                "literature-catalog.yaml",
            )
        source = ctx.get_parameter_source("template")
        if source == click.core.ParameterSource.DEFAULT and not as_json:
            console.print(f"[dim]No --template specified, using default: {template}[/]")
        content = _load_template(template)
        if content is None:
            available = ", ".join(_list_templates()) or "(none)"
            _fail(
                f"template {template!r} not found; built-in templates: {available}",
                kind="not_found",
                fix="recon templates",
            )
        origin = f"template:{template}"

    mission.mkdir(parents=True, exist_ok=True)
    config_path.write_text(content)
    if as_json:
        _emit(
            {
                "schema": "recon.v1.mission",
                "mission": name,
                "config": str(config_path),
                "from": origin,
            }
        )
        return
    console.print(f"[green]Created:[/] {config_path}")
    console.print(
        f"[dim]Set the placeholders, then: recon survey {name} --dry-run && recon survey {name}[/]"
    )
    console.print("[dim]For config syntax help: recon --skill[/]")


def _read_config(path: Path) -> tuple[bytes, Any]:
    data = path.read_bytes()
    try:
        return data, yaml.safe_load(data)
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"{path}: not valid YAML",
            [Problem("", " ".join(str(exc).split()))],
        ) from exc


def _plan_doc(name: str, plan: Plan, mission: Path, copy_to: Path | None) -> dict[str, Any]:
    return {
        "schema": "recon.v1.plan",
        "mission": name,
        "valid": plan.valid,
        "runs": [r.as_dict() for r in plan.runs],
        "effects": plan.effects(),
        "writes": {
            "config": str(copy_to) if copy_to else None,
            "archive": str(mission / "archive" / "<timestamp>"),
            "captures": bool(plan.config and plan.config.preserve_raw),
        },
        "problems": [{"path": p.path, "message": p.message} for p in plan.problems],
        "warnings": plan.warnings,
    }


def _print_plan(name: str, plan: Plan, copy_to: Path | None) -> None:
    table = Table(show_header=True, header_style="dim", title=f"recon survey {name} — plan")
    table.add_column("Output")
    table.add_column("Type")
    table.add_column("Source")
    table.add_column("Effects")
    table.add_column("Does")
    for r in plan.runs:
        doc = r.as_dict()
        does = doc.get("url") or "; ".join(doc.get("commands") or []) or doc.get("path") or ""
        effects = ", ".join(r.effects) if r.effects is not None else "unknown"
        table.add_row(r.output, r.type, r.source or "—", effects, escape(str(does)))
    out.print(table)
    if copy_to:
        out.print(f"[dim]Would copy the config to {copy_to}[/]")
    out.print(f"[dim]{len(plan.runs)} run(s); nothing was fetched, executed or written.[/]")


_PARTIAL_FIX: dict[str, str] = {
    "auth": "check the credentials named by the failing sources' auth.env, then: recon survey {n}",
    "transient": "retry later: recon survey {n}",
    "collection": (
        "read each failed table's error (meta.yaml, or --json), fix the config, "
        "then: recon survey {n} --dry-run"
    ),
}


@main.command(epilog=_EXIT_HELP)
@click.argument("name")
@click.option(
    "-c",
    "--config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Config file to run (copied to .recon/<name>/config.yaml).",
)
@click.option(
    "--replace",
    is_flag=True,
    help="With -c: overwrite the mission's existing config when it differs.",
)
@click.option(
    "-n",
    "--dry-run",
    is_flag=True,
    help="Validate and print the plan: no network, no subprocess, nothing written.",
)
@_json_option
@_guarded
def survey(name: str, config: Path | None, replace: bool, dry_run: bool, as_json: bool) -> None:
    """Plan and run a collection mission.

    Every survey validates first: an invalid config exits 3 listing every problem, before
    anything is fetched or written.

    \b
    Examples:
      recon survey attention-papers --dry-run
      recon survey attention-papers --json
      recon survey my-scan -c custom-config.yaml
    """
    mission = _mission_dir(name)
    mission_config = mission / "config.yaml"
    config_path = config or mission_config

    if not config_path.exists():
        _fail(
            f"no config for mission {name!r} at {config_path}",
            kind="not_found",
            fix=f"recon init {name} --template <name> (see `recon templates`), "
            f"or recon survey {name} -c <config.yaml>",
        )

    text, raw = _read_config(config_path)
    registry = load_registry()
    if not as_json:
        _warn_broken(registry)
    plan = build_plan(
        raw,
        collector_types=registry.collector_types,
        transforms=registry.transforms,
        broken=registry.broken,
        env=os.environ,
    )
    if not plan.valid:
        _fail(
            f"{config_path}: {len(plan.problems)} problem(s)",
            kind="config",
            fix=f"fix each problem, then: recon survey {name} --dry-run",
            problems=plan.problems,
        )

    copy_to: Path | None = None
    if config is not None and config.resolve() != mission_config.resolve():
        if mission_config.exists() and mission_config.read_bytes() != text and not replace:
            _fail(
                f"{mission_config} exists and differs from {config}; -c would overwrite it",
                kind="conflict",
                fix=f"recon survey {name} -c {config} --replace",
            )
        if not mission_config.exists() or mission_config.read_bytes() != text:
            copy_to = mission_config

    if dry_run:
        if as_json:
            _emit(_plan_doc(name, plan, mission, copy_to))
        else:
            for warning in plan.warnings:
                console.print(f"[yellow]Warning: {escape(warning)}[/]")
            _print_plan(name, plan, copy_to)
        return

    if not as_json:
        for warning in plan.warnings:
            console.print(f"[yellow]Warning: {escape(warning)}[/]")

    if copy_to is not None:
        mission.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config_path, copy_to)

    assert plan.config is not None  # plan.valid guarantees it
    loaded = len(registry.broken)
    collectors = build_collectors(registry, mission)
    # A factory that raised is now in registry.broken; its tables fail with a config error.
    warnings = plan.warnings + [f"plugin {b.describe()}" for b in registry.broken[loaded:]]
    if not as_json:
        for warning in warnings[len(plan.warnings) :]:
            console.print(f"[yellow]Warning: {escape(warning)}[/]")
        console.print(
            f"[dim]Mission: {name} — {len(plan.runs)} run(s) from "
            f"{len(plan.config.collectors)} collector(s), {len(plan.config.catalog)} source(s)[/]"
        )
    archive_dir, results = recon_run(
        plan.config,
        collectors,
        mission,
        on_event=None if as_json else _cli_event,
        config_sha256=hashlib.sha256(text).hexdigest(),
    )

    kind = failure_kind(results)
    failed = [r["name"] for r in results if r["status"] == "error"]
    if as_json:
        _emit(
            {
                "schema": "recon.v1.survey",
                "mission": name,
                "archive": str(archive_dir),
                "status": "incomplete" if failed else "ok",
                "tables": results,
                "warnings": warnings,
            }
        )
    else:
        click.echo(str(archive_dir))

    if kind is not None:
        _fail(
            f"{len(failed)} of {len(results)} table(s) failed; the archive is incomplete",
            kind=kind,
            fix=_PARTIAL_FIX[kind].format(n=name),
            archive=str(archive_dir),
            failed=failed,
        )


def _cli_event(event: dict[str, object]) -> None:
    """Render a dispatch-loop outcome event to stderr."""
    kind = event.get("kind")
    name = event.get("name", "?")
    if kind == "ok":
        console.print(f"[dim]  {name}: {event.get('records', 0)} records[/]")
    elif kind == "error":
        reason = escape(str(event.get("reason", "")))
        console.print(f"[red]  {name}: ERROR ({event.get('error_kind')}) — {reason}[/]")
    elif kind == "archive_warning":
        console.print(
            f"[yellow]  WARNING: archive finalization issue — {event.get('reason', '')}[/]"
        )


def _count_lines(path: Path) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def _mission_status(mission: Path) -> dict[str, Any]:
    runs = list_archives(mission)
    latest: dict[str, Any] | None = None
    if runs:
        meta = read_meta(runs[-1])
        tables = meta["tables"]
        latest = {
            "id": runs[-1].name,
            "path": str(runs[-1]),
            "state": archive_state(runs[-1]),
            "format_version": meta.get("format_version"),
            "recon_version": meta.get("recon_version"),
            "config_sha256": meta.get("config_sha256"),
            "tables": [
                {
                    "name": t.get("name"),
                    "type_url": t.get("type_url"),
                    "status": t.get("status"),
                    "records": t.get("records"),
                    **({"error": t["error"]} if t.get("error") else {}),
                }
                for t in tables
            ],
        }
    return {
        "name": mission.name,
        "config": str(mission / "config.yaml"),
        "archives": len(runs),
        "latest": latest,
        "captures": _count_lines(mission / "captures" / CAPTURE_LOG_NAME),
    }


@main.command(epilog=_EXIT_HELP)
@_json_option
@_guarded
def status(as_json: bool) -> None:
    """List missions, their archives, and the installed collector types and transforms.

    A plugin that failed to load is listed (and warned about) rather than hidden.

    \b
    Example:
      recon status --json
    """
    recon_dir = _recon_dir()
    missions = (
        sorted(d for d in recon_dir.iterdir() if d.is_dir() and (d / "config.yaml").exists())
        if recon_dir.is_dir()
        else []
    )
    registry = load_registry()
    statuses = [_mission_status(m) for m in missions]

    if as_json:
        _emit(
            {
                "schema": "recon.v1.status",
                "root": str(recon_dir),
                "missions": statuses,
                "plugins": registry.as_dict(),
            }
        )
        return

    _warn_broken(registry)
    if not statuses:
        console.print(f"[dim]No missions under {recon_dir}. Run `recon init <name>` to start.[/]")
    else:
        table = Table(show_header=True, header_style="dim")
        table.add_column("Mission")
        table.add_column("Archives", justify="right")
        table.add_column("Latest")
        table.add_column("State")
        table.add_column("Captures", justify="right")
        for s in statuses:
            latest = s["latest"]
            state = "—"
            if latest:
                state = "[yellow]incomplete[/]" if latest["state"] == "incomplete" else "ok"
            table.add_row(
                s["name"],
                str(s["archives"]),
                latest["id"] if latest else "—",
                state,
                str(s["captures"]),
            )
        out.print(table)
    types = ", ".join(sorted(registry.collector_types)) or "(none)"
    transforms = ", ".join(f"${t}" for t in sorted(registry.transforms)) or "(none)"
    console.print(f"[dim]Collector types: {types}[/]")
    console.print(f"[dim]Transforms: {transforms}[/]")


@main.command(epilog=_EXIT_HELP)
@click.argument("name")
@click.argument("sql")
@click.option("--run", "run_id", default=None, help="Specific archive timestamp.")
@_json_option
@_guarded
def query(name: str, sql: str, run_id: str | None, as_json: bool) -> None:
    """Query mission data with SQL via DuckDB.

    Each output table is a view named after its file (hyphens become underscores).

    \b
    Examples:
      recon query my-research "SELECT title, year FROM s2_search LIMIT 10"
      recon query my-research "SELECT * FROM arxiv_search" --json
      recon query my-research "SELECT count(*) FROM s2_search" --run 2026-04-01-143022
    """
    mission = _mission_dir(name)
    try:
        run_dir = resolve_archive(mission, name, run_id)
    except NotFoundError as exc:
        fix = "recon status --json" if run_id else f"recon survey {name}"
        _fail(str(exc), kind="not_found", fix=fix)

    if (run_dir / ".incomplete").exists():
        console.print(
            f"[yellow]Archive '{run_dir.name}' is marked incomplete — "
            "one or more collectors failed. Query proceeds, but results may be partial. "
            "See meta.yaml for per-table status.[/]"
        )

    try:
        columns, rows = query_module.execute(run_dir, sql)
    except ReconError as exc:
        tables = ", ".join(query_module.available_tables(run_dir)) or "(none)"
        _fail(str(exc), kind=exc.kind, fix=f"query one of these tables: {tables}")

    if as_json:
        # strict=True: `columns` and `rows` come from the same execution, so a width
        # mismatch is a driver error. strict=False would silently drop fields.
        records = [dict(zip(columns, row, strict=True)) for row in rows]
        _emit(
            {
                "schema": "recon.v1.query-rows",
                "mission": name,
                "archive": str(run_dir),
                "columns": columns,
                "rows": records,
                "count": len(records),
            }
        )
        return

    if not rows:
        tables = ", ".join(query_module.available_tables(run_dir))
        console.print(f"[yellow]No rows. Tables in this archive: {tables}[/]")
        return
    out_table = Table()
    for col in columns:
        out_table.add_column(col)
    for row in rows:
        out_table.add_row(*(escape(str(v)) for v in row))
    out.print(out_table)


@main.command(epilog=_EXIT_HELP)
@_json_option
@_guarded
def templates(as_json: bool) -> None:
    """List available built-in config templates.

    \b
    Example:
      recon templates
      recon init my-mission --template code-forensics
    """
    items = [
        {"name": n, "description": _template_description(_load_template(n))}
        for n in _list_templates()
    ]
    if as_json:
        _emit({"schema": "recon.v1.templates", "templates": items})
        return
    if not items:
        console.print("[dim]No built-in templates found[/]")
        return
    for item in items:
        out.print(f"  [bold]{item['name']}[/]  {escape(item['description'])}")


@main.command(epilog=_EXIT_HELP)
@click.argument("name")
@_json_option
@_guarded
def capture(name: str, as_json: bool) -> None:
    """Append a Claude Code PostToolUse hook payload (JSON on stdin) to a mission's
    capture log, as an `external` capture.

    The log is .recon/<name>/captures/captures.jsonl; a `capture` collector in the
    mission's config normalizes it on the next survey. The mission must exist.

    \b
    Hook (settings.json), capturing every MCP tool result:
      {"hooks": {"PostToolUse": [{"matcher": "mcp__.*",
        "hooks": [{"type": "command", "command": "recon capture my-mcp --json"}]}]}}
    """
    try:
        mission = _require_mission(name)
    except NotFoundError as exc:
        _fail(str(exc), kind="not_found", fix=f"recon init {name} --template mcp-captures")
    data = sys.stdin.read()
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        _fail(
            f"stdin is not JSON ({exc})",
            kind="config",
            fix="pipe one PostToolUse hook payload (a JSON object) to recon capture",
        )
    log = FilesystemCaptureLog(mission / "captures")
    try:
        line = record_hook_payload(log, payload)
    except ConfigError as exc:
        _fail(str(exc), kind="config", fix="run recon capture from a PostToolUse hook")
    if as_json:
        _emit(line)
        return
    console.print(f"[dim]captured {line['id']} ({line['collector']}) → {log.log_path}[/]")

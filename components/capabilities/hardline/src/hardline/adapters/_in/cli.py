"""``hardline``: list, check, and call configured models.

    hardline models [--json]                          the registry (* = default)
    hardline check [--json]                           validate tiers, backends and secrets,
                                                      and show each row's worst case
    hardline complete MODEL PROMPT [--json] [...]     call a model, print its text
    hardline --skill                                  the text an agent reads

A thin driving adapter: it reaches hardline through ``hardline.__all__`` alone, and names no
adapter. Its callers are mostly agents, so a failure is classified, not just described: the
exit code says what the caller's next move is, with the codes the capability CLIs agree in
``components/capabilities/exit-codes.json`` (:data:`EXIT_CODES`). With ``--json``, every
failure, a usage error and a defect included, is one line on stderr: the
``hardline.v1.error`` envelope those CLIs agree in ``error-envelope.json``,
``{"schema", "error": {"kind", "message", "fix", "complete", "problems", "details"}}``.
Without it, stderr carries the same as prose, and what the library logs while a command runs as
``hardline: warning:`` lines; under ``--json`` stderr holds the envelope alone. Data goes to
stdout. Nothing prompts.

Telemetry is installed once per command from the standard ``OTEL_*`` variables
(:func:`hardline.configure_telemetry`), as the ``matrix`` and ``ix`` commands install theirs:
with none set, nothing is. What it could not install is a ``hardline: warning:`` line, and, under
``--json``, among ``check``'s ``warnings``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import sys
import traceback
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Literal

import rich_click as click

from hardline import (
    BackendError,
    ConfigError,
    HardlineError,
    Problem,
    SecretError,
    UnknownModelError,
    __version__,
    build_runtime,
    configure_telemetry,
    discover_backends,
    discover_sources,
    get_skill,
    load_registry,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from hardline import CallBound, Completion

ErrorKind = Literal["unknown", "usage", "config", "transient", "auth", "interrupted"]

#: The exit code of success and of each kind a command exits with: the codes the capability
#: CLIs agree. hardline has no kind of its own, so every kind is an agreed one.
EXIT_CODES: dict[str, int] = {
    "ok": 0,
    "unknown": 1,
    "usage": 2,
    "config": 3,
    "transient": 5,
    "auth": 6,
    "interrupted": 130,
}

#: What each kind in :data:`EXIT_CODES` means.
EXIT_MEANINGS: dict[str, str] = {
    "ok": "done",
    "unknown": "a failure hardline did not classify: a bug in hardline or in a backend, or a "
    "reply it cannot read",
    "usage": "a bad command, flag or argument",
    "config": "the input must change: a registry row, a model name, a secret reference, a call "
    "argument, or a request the provider rejected as malformed (details.reason: bad_request)",
    "transient": "rate-limited, timed out or unavailable (details.reason), after the row's "
    "retries and fallbacks, or its limits",
    "auth": "the provider rejected the key",
    "interrupted": "the command was interrupted (Ctrl-C) before it finished",
}

#: What to do next on each kind in :data:`EXIT_CODES`: a step that can succeed from there.
EXIT_NEXT: dict[str, str] = {
    "ok": "use the output",
    "unknown": "report it as a bug in hardline, or in the backend the message names, with the "
    "command that raised it",
    "usage": "correct the command; `hardline --help` lists the commands, `hardline <command> "
    "--help` their options",
    "config": "correct the file and key the message names (each in `error.problems`), then run "
    "`hardline check`",
    "transient": "retry later, after details.retry_after seconds when it is set",
    "auth": "replace the key the model's api_key reference names",
    "interrupted": "run the command again",
}

#: The problem kinds hardline writes: those the CLIs share, and its own.
PROBLEM_KINDS = frozenset(
    {
        "yaml_syntax",
        "wrong_shape",
        "unknown_key",
        "missing_key",
        "invalid_value",
        "name_unknown",
        # hardline's own
        "shared_fate",  # a fallback that sends where its primary does (a warning)
        "secret_unresolved",  # an api_key reference whose variable or file is not there
    }
)

# Each fix is a step that can succeed from the state the error describes.
_FIX_UNKNOWN_MODEL = "run `hardline models` for the registry names, then call one of them"
_FIX_SECRET = (
    "set the environment variable or create the file the reference names, then run `hardline check`"
)
_FIX_BAD_REQUEST = (
    "the provider rejected the request: check the row's model id and options, and any "
    "--max-tokens/--temperature passed"
)

#: BackendError.reason -> kind. The reason itself travels in details.
_BACKEND_KIND: dict[str, ErrorKind] = {
    "rate_limit": "transient",
    "timeout": "transient",
    "unavailable": "transient",
    "auth": "auth",
    "bad_request": "config",
    "unknown": "unknown",
}

# rich-click rewraps an epilog's lines; a blank line between them keeps each on its own.
_EPILOG = "Exit codes:\n\n" + "\n\n".join(
    f"{code}  {kind}: {EXIT_MEANINGS[kind]}. Next: {EXIT_NEXT[kind]}"
    for kind, code in EXIT_CODES.items()
)


class _UsageError(HardlineError):
    """A command was given a flag or an argument it does not take."""


class _InterruptError(HardlineError):
    """The command was interrupted before it finished."""


def _kind(e: HardlineError) -> ErrorKind:
    if isinstance(e, BackendError):
        return _BACKEND_KIND[e.reason]
    if isinstance(e, ConfigError | UnknownModelError | SecretError):
        return "config"
    if isinstance(e, _UsageError):
        return "usage"
    if isinstance(e, _InterruptError):
        return "interrupted"
    # ContractError (a backend broke the port) and SchemaError: nothing the caller can fix.
    return "unknown"


def _fix(e: HardlineError, kind: ErrorKind) -> str:
    if e.fix:
        return e.fix
    if isinstance(e, UnknownModelError):
        return _FIX_UNKNOWN_MODEL
    if isinstance(e, SecretError):
        return _FIX_SECRET
    if isinstance(e, BackendError) and e.reason == "bad_request":
        return _FIX_BAD_REQUEST
    return EXIT_NEXT[kind]


def error_payload(e: HardlineError) -> tuple[dict[str, Any], int]:
    """The ``hardline.v1.error`` envelope for ``e``, and the exit code that goes with it."""
    kind = _kind(e)
    details: dict[str, Any] = {}
    if isinstance(e, BackendError):
        details["reason"] = e.reason
        if e.retry_after is not None:
            details["retry_after"] = e.retry_after
        if e.limit is not None:
            details["limit"] = e.limit
    body = {
        "kind": kind,
        "message": str(e),
        "fix": _fix(e, kind),
        "complete": True,
        "problems": [problem.model_dump(mode="json") for problem in e.problems],
        "details": details,
    }
    return {"schema": "hardline.v1.error", "error": body}, EXIT_CODES[kind]


def _report(e: HardlineError, as_json: bool) -> int:
    payload, code = error_payload(e)
    if as_json:
        click.echo(json.dumps(payload), err=True)
        return code
    error = payload["error"]
    click.echo(f"hardline: error ({error['kind']}): {error['message']}", err=True)
    click.echo(f"hardline: fix: {error['fix']}", err=True)
    return code


def _positive_int(ctx: click.Context, param: click.Parameter, value: int | None) -> int | None:
    # The same bound as ModelSpec.max_tokens (gt=0), enforced before a call is spent.
    if value is not None and value <= 0:
        raise click.BadParameter(f"{value} must be > 0")
    return value


def _non_negative(ctx: click.Context, param: click.Parameter, value: float | None) -> float | None:
    # The same bound as ModelSpec.temperature (ge=0); nan is refused too.
    if value is not None and (math.isnan(value) or value < 0):
        raise click.BadParameter(f"{value} must be >= 0")
    return value


_json_option = click.option("--json", "as_json", is_flag=True, help="machine-readable output")


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog=_EPILOG,
)
@click.version_option(__version__, prog_name="hardline")
@click.option("--skill", is_flag=True, help="Print the skill text for agents, then exit")
@click.pass_context
def cli(ctx: click.Context, skill: bool) -> None:
    """One port for every model family. Models are registry rows, not code."""
    if skill:
        click.echo(get_skill())
        ctx.exit()
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command()
@_json_option
def models(as_json: bool) -> None:
    """List the configured models (* = the default)."""
    registry = load_registry(discover_sources(), discover_backends())
    if as_json:
        hidden = {"api_key", "options"}
        listing = [spec.model_dump(mode="json", exclude=hidden) for spec in registry]
        click.echo(json.dumps({"default": registry.default, "models": listing}, indent=2))
        return
    if not len(registry):
        click.echo(
            "no models configured — looked in ~/.hardline/config.yaml, ./hardline.yaml, "
            "$HARDLINE_CONFIG",
            err=True,
        )
        return
    rows = [("NAME", "FAMILY", "BACKEND", "LOCAL", "MODEL")]
    for spec in registry:
        marker = " *" if spec.name == registry.default else ""
        rows.append(
            (spec.name + marker, spec.family, spec.backend, "yes" if spec.local else "", spec.model)
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for row in rows:
        click.echo("  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip())
    if registry.default:
        click.echo("\n* default")


@cli.command()
@_json_option
def check(as_json: bool) -> None:
    """Validate the config tiers against the installed backends, and resolve every secret."""
    sources = discover_sources()
    tiers = [{"source": source.describe(), "present": bool(source.read())} for source in sources]
    if not as_json:
        for tier in tiers:
            click.echo(f"tier: {tier['source']}{'' if tier['present'] else '  (absent/empty)'}")
    runtime = build_runtime(sources=sources)
    # A row whose key reference does not resolve would fail on its first call; reporting
    # it here is what lets an agent take `check`'s exit 0 at its word.
    problems = runtime.secret_problems()
    warnings = [w.model_dump(mode="json") for w in (*runtime.warnings, *_TELEMETRY)]
    if not as_json:
        for warning in runtime.warnings:
            click.echo(f"warning: {warning.path}: {warning.message}", err=True)
    if as_json:
        rows = [
            {
                "name": spec.name,
                "backend": spec.backend,
                "family": spec.family,
                "ok": spec.name not in problems,
                "problem": problems.get(spec.name),
                "worst_case": runtime.worst_case(spec.name).model_dump(mode="json"),
            }
            for spec in runtime.registry
        ]
        report = {"ok": not problems, "tiers": tiers, "models": rows, "warnings": warnings}
        click.echo(json.dumps(report, indent=2))
    if problems:
        raise SecretError(
            f"{len(problems)} model(s) have an api_key reference that does not resolve:\n"
            + "\n".join(f"  models.{name}.api_key: {msg}" for name, msg in problems.items()),
            problems=[
                Problem(path=f"models.{name}.api_key", kind="secret_unresolved", message=msg)
                for name, msg in problems.items()
            ],
        )
    if not as_json:
        # Limits are unset by default (owner ruling 2026-10-22): what a call can cost is shown,
        # so the default's cost is seen rather than assumed.
        for spec in runtime.registry:
            click.echo(f"{spec.name}: {_worst(runtime.worst_case(spec.name))}")
        click.echo(
            f"ok: {len(runtime.registry)} model(s) valid against installed backends; "
            "every secret reference resolves"
        )


def _worst(bound: CallBound) -> str:
    """One row's worst case, as ``check`` says it."""
    calls = "call" if bound.complete_calls == 1 else "calls"
    seconds = f"{bound.seconds:g} s" if bound.seconds is not None else "no limit in seconds"
    tokens = f"{bound.tokens} tokens" if bound.tokens is not None else "no limit in tokens"
    return (
        f"at most {bound.complete_calls} backend {calls} a complete, {bound.extract_calls} an "
        f"extract; {seconds}; {tokens}"
    )


@cli.command()
@click.argument("model")
@click.argument("prompt")
@click.option("--system", default=None, help="a system prompt")
@click.option("--max-tokens", type=int, default=None, callback=_positive_int, help="> 0")
@click.option("--temperature", type=float, default=None, callback=_non_negative, help=">= 0")
@_json_option
def complete(
    model: str,
    prompt: str,
    system: str | None,
    max_tokens: int | None,
    temperature: float | None,
    as_json: bool,
) -> None:
    """Call MODEL (a registry name; '' for the default) with PROMPT ('-' reads stdin).

    Prints the text; with --json, the Completion and its record. A reply that is not a whole
    answer (cut at max_tokens, refused, withheld, empty) is printed with a note on stderr, and
    exits 0.
    """
    text = sys.stdin.read() if prompt == "-" else prompt
    completion = asyncio.run(_complete(model or None, text, system, max_tokens, temperature))
    if as_json:
        click.echo(completion.model_dump_json(indent=2, exclude={"raw"}))
        return
    click.echo(completion.text)
    if not completion.answered:
        # Printed alone, a cut-off or refused reply would read as the model's answer.
        click.echo(f"hardline: {_not_answered(completion)}", err=True)


async def _complete(
    model: str | None,
    prompt: str,
    system: str | None,
    max_tokens: int | None,
    temperature: float | None,
) -> Completion:
    async with build_runtime() as runtime:
        return await runtime.complete(
            model, prompt, system=system, max_tokens=max_tokens, temperature=temperature
        )


def _not_answered(c: Completion) -> str:
    """Why ``c`` is not a whole answer, for the stderr note beside its text."""
    reason = f"(finish_reason={c.finish_reason}, provider: {c.native_finish_reason})"
    if c.refusal is not None:
        return f"the model refused {reason}" + (f": {c.refusal}" if c.refusal else "")
    if c.finish_reason == "length":
        return f"the reply was cut off at max_tokens {reason}"
    if c.finish_reason == "content_filter":
        return f"the provider withheld or cut the reply {reason}"
    if c.finish_reason is None:
        return "the provider did not say why the reply ended"
    if not c.text:
        return f"the reply is empty {reason}"
    return f"the reply ended without a whole answer {reason}"


#: What telemetry could not install when the command started: ``check --json`` reports it.
_TELEMETRY: list[Problem] = []


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command and return the exit code of its outcome."""
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    telemetry = configure_telemetry()
    _TELEMETRY[:] = telemetry.warnings
    if not as_json:
        for warning in telemetry.warnings:
            click.echo(f"hardline: warning: {warning.path}: {warning.message}", err=True)
    try:
        return _run(args, as_json)
    finally:
        telemetry.shutdown()


def _run(args: list[str], as_json: bool) -> int:
    try:
        with _hardline_log(as_json):
            code = cli.main(args=args, prog_name="hardline", standalone_mode=False)
    except click.UsageError as e:
        return _report(_UsageError(e.format_message()), as_json)
    except click.Abort:  # click's form of Ctrl-C
        return _report(_InterruptError("interrupted before the command finished"), as_json)
    except HardlineError as e:
        return _report(e, as_json)
    except Exception as e:  # a defect: still one envelope, so a caller parses one shape
        if not as_json:
            traceback.print_exc()
        return _report(HardlineError(f"{type(e).__name__}: {e}"), as_json)
    return code if isinstance(code, int) else EXIT_CODES["ok"]


class _Warnings(logging.Handler):
    """What the library logs while a command runs, said as ``hardline: warning:`` lines on
    stderr. Under ``--json`` nothing is: stderr keeps to the one envelope a caller parses. A
    registry warning the command reports itself (``check``'s ``warnings``) is not said twice."""

    def __init__(self, as_json: bool) -> None:
        super().__init__(logging.WARNING)
        self._as_json = as_json

    def emit(self, record: logging.LogRecord) -> None:
        if not self._as_json and not hasattr(record, "problem"):
            click.echo(f"hardline: warning: {record.getMessage()}", err=True)


@contextmanager
def _hardline_log(as_json: bool) -> Iterator[None]:
    log = logging.getLogger("hardline")
    handler, propagate = _Warnings(as_json), log.propagate
    log.addHandler(handler)
    log.propagate = False
    try:
        yield
    finally:
        log.removeHandler(handler)
        log.propagate = propagate


if __name__ == "__main__":
    raise SystemExit(main())

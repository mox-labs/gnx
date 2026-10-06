"""hardline CLI — list, check, and call configured models.

Standard library only (argparse): the CLI is a thin driving adapter over the composition
root and should not add dependencies of its own.

Its callers are mostly agents, so a failure is classified, not just described. The exit
code says what the caller's next move is; the scheme is shared with ix:

    0  success
    1  failure not otherwise classified
    2  usage error (bad flags or arguments; argparse's own)
    3  config error: invalid config, unknown model name, unset secret reference, a
       request the provider rejected as malformed — the caller must change its input
    4  not found: a named thing does not exist (reserved; hardline raises none today)
    5  transient: rate limit, timeout, unavailable — retrying later may succeed
    6  auth: the provider rejected the key

With ``--json`` a failure is also written to stderr as one JSON line,
``{"error": {"kind", "message", "retryable", "retry_after", "fix"}}``; without it, stderr
carries the same message as prose. Every non-zero exit under ``--json`` has that line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import TYPE_CHECKING, Any, Literal

from hardline.composition import build_runtime, discover_sources, load_registry
from hardline.domain.errors import (
    BackendError,
    ConfigError,
    HardlineError,
    SecretError,
    UnknownModelError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3
EXIT_NOT_FOUND = 4
EXIT_TRANSIENT = 5
EXIT_AUTH = 6

ErrorKind = Literal[
    "config", "not_found", "rate_limit", "timeout", "unavailable", "auth", "bad_request", "unknown"
]

# Each fix is a step that can succeed from the state the error describes. A hint that
# would itself fail is worse than none, so a kind with no reliable next step gets null.
_FIX_CONFIG = "correct the file and key the message names, then run `hardline check`"
_FIX_UNKNOWN_MODEL = "run `hardline models` for the registry names, then call one of them"
_FIX_SECRET = (
    "set the environment variable or create the file the reference names, then run `hardline check`"
)
_FIX_TRANSIENT = "retry later; this model's retries and fallbacks are already spent"

#: BackendError.reason -> (exit code, fix). A malformed request is input the caller changes.
_BACKEND: dict[str, tuple[int, str | None]] = {
    "rate_limit": (EXIT_TRANSIENT, _FIX_TRANSIENT),
    "timeout": (EXIT_TRANSIENT, _FIX_TRANSIENT),
    "unavailable": (EXIT_TRANSIENT, _FIX_TRANSIENT),
    "auth": (
        EXIT_AUTH,
        "the provider rejected the key: replace the key the model's api_key reference names",
    ),
    "bad_request": (
        EXIT_CONFIG,
        "the provider rejected the request: check the row's model id and options, "
        "and any --max-tokens/--temperature passed",
    ),
    "unknown": (EXIT_FAILURE, None),
}

_EXAMPLES = """examples:
  hardline models                                  list the registry (* = default)
  hardline complete qwen3-8b "Summarise: ..."      call a model, print its text
  git diff | hardline complete haiku - --system "Review this diff" --json
  hardline check                                   validate tiers, backends and secrets

exit codes:
  0 success · 1 unclassified failure · 2 usage · 3 config (fix the input)
  4 not found · 5 transient (retry later) · 6 auth
  with --json, a failure is one JSON line on stderr:
  {"error": {"kind", "message", "retryable", "retry_after", "fix"}}
"""


def _classify(e: HardlineError) -> tuple[ErrorKind, int, str | None]:
    """An error's kind, exit code and next step."""
    if isinstance(e, BackendError):
        code, fix = _BACKEND[e.reason]
        return e.reason, code, fix
    if isinstance(e, UnknownModelError):
        return "config", EXIT_CONFIG, _FIX_UNKNOWN_MODEL
    if isinstance(e, SecretError):
        return "config", EXIT_CONFIG, _FIX_SECRET
    if isinstance(e, ConfigError):
        return "config", EXIT_CONFIG, _FIX_CONFIG
    # ContractError (a backend broke the port) and SchemaError: nothing the caller can fix.
    return "unknown", EXIT_FAILURE, None


def error_payload(e: HardlineError) -> tuple[dict[str, Any], int]:
    """The ``{"error": {...}}`` object for ``e`` and the exit code that goes with it."""
    kind, code, fix = _classify(e)
    backend = e if isinstance(e, BackendError) else None
    error = {
        "kind": kind,
        "message": str(e),
        "retryable": backend.retryable if backend else False,
        "retry_after": backend.retry_after if backend else None,
        "fix": fix,
    }
    return {"error": error}, code


def _non_negative_float(text: str) -> float:
    # The same bound as ModelSpec.temperature (ge=0), enforced before a call is spent.
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if not value >= 0:  # also rejects nan
        raise argparse.ArgumentTypeError(f"{text!r} must be >= 0")
    return value


def _positive_int(text: str) -> int:
    # The same bound as ModelSpec.max_tokens (gt=0).
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not an integer") from None
    if value <= 0:
        raise argparse.ArgumentTypeError(f"{text!r} must be > 0")
    return value


def _cmd_models(args: argparse.Namespace) -> int:
    registry = load_registry(discover_sources())
    if args.json:
        hidden = {"api_key", "options"}
        listing = [spec.model_dump(mode="json", exclude=hidden) for spec in registry]
        print(json.dumps({"default": registry.default, "models": listing}, indent=2))
        return 0
    if not len(registry):
        print(
            "no models configured — looked in ~/.hardline/config.yaml, ./hardline.yaml, "
            "$HARDLINE_CONFIG",
            file=sys.stderr,
        )
        return 0
    rows = [("NAME", "FAMILY", "BACKEND", "LOCAL", "MODEL")]
    for spec in registry:
        marker = " *" if spec.name == registry.default else ""
        rows.append(
            (spec.name + marker, spec.family, spec.backend, "yes" if spec.local else "", spec.model)
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for row in rows:
        print("  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip())
    if registry.default:
        print("\n* default")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    sources = discover_sources()
    tiers = [{"source": source.describe(), "present": bool(source.read())} for source in sources]
    if not args.json:
        for tier in tiers:
            print(f"tier: {tier['source']}{'' if tier['present'] else '  (absent/empty)'}")
    runtime = build_runtime(sources=sources)
    # A row whose key reference does not resolve would fail on its first call; reporting
    # it here is what lets an agent take `check`'s exit 0 at its word.
    problems = runtime.secret_problems()
    if args.json:
        rows = [
            {
                "name": spec.name,
                "backend": spec.backend,
                "family": spec.family,
                "ok": spec.name not in problems,
                "problem": problems.get(spec.name),
            }
            for spec in runtime.registry
        ]
        print(json.dumps({"ok": not problems, "tiers": tiers, "models": rows}, indent=2))
    if problems:
        raise SecretError(
            f"{len(problems)} model(s) have an api_key reference that does not resolve:\n"
            + "\n".join(f"  models.{name}.api_key: {msg}" for name, msg in problems.items())
        )
    if not args.json:
        print(
            f"ok: {len(runtime.registry)} model(s) valid against installed backends; "
            "every secret reference resolves"
        )
    return 0


async def _complete(args: argparse.Namespace) -> int:
    prompt = sys.stdin.read() if args.prompt == "-" else args.prompt
    runtime = build_runtime()
    completion = await runtime.complete(
        args.model,
        prompt,
        system=args.system,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
    )
    if args.json:
        print(completion.model_dump_json(indent=2, exclude={"raw"}))
    else:
        print(completion.text)
    return 0


def _parser() -> argparse.ArgumentParser:
    from hardline import __version__

    parser = argparse.ArgumentParser(
        prog="hardline",
        description="One port for every model family. Models are registry rows, not code.",
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"hardline {__version__}")
    parser.add_argument("--skill", action="store_true", help="print the skill text for agents")
    sub = parser.add_subparsers(dest="command")

    models = sub.add_parser("models", help="list configured models")
    models.add_argument("--json", action="store_true", help="print the registry as JSON")
    models.set_defaults(func=_cmd_models)
    check = sub.add_parser(
        "check", help="validate config tiers against installed backends, and resolve secrets"
    )
    check.add_argument("--json", action="store_true", help="print the per-model report as JSON")
    check.set_defaults(func=_cmd_check)
    complete = sub.add_parser("complete", help="call a model and print its text")
    complete.add_argument("model", help="registry name; '' uses the default")
    complete.add_argument("prompt", help="the prompt, or '-' to read stdin")
    complete.add_argument("--system", default=None)
    complete.add_argument("--max-tokens", type=_positive_int, default=None, help="> 0")
    complete.add_argument("--temperature", type=_non_negative_float, default=None, help=">= 0")
    complete.add_argument("--json", action="store_true", help="print the Completion as JSON")
    complete.set_defaults(func=lambda a: asyncio.run(_complete(a)))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.skill:
        from hardline.skill import get_skill

        print(get_skill())
        return EXIT_OK
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_USAGE
    if getattr(args, "model", None) == "":
        args.model = None
    try:
        code: int = args.func(args)
    except HardlineError as e:
        payload, code = error_payload(e)
        if getattr(args, "json", False):
            print(json.dumps(payload), file=sys.stderr)
        else:
            print(f"hardline: {e}", file=sys.stderr)
        return code
    return code


if __name__ == "__main__":
    raise SystemExit(main())

"""hardline CLI — list, check, and call configured models.

Standard library only (argparse): the CLI is a thin driving adapter over the composition
root and should not add dependencies of its own.

Exit codes: 0 success · 1 a hardline error (config, secret, backend, schema) · 2 usage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import TYPE_CHECKING

from hardline.composition import build_runtime, discover_sources, load_registry
from hardline.domain.errors import HardlineError

if TYPE_CHECKING:
    from collections.abc import Sequence


_EXAMPLES = """examples:
  hardline models                                  list the registry (* = default)
  hardline complete qwen3-8b "Summarise: ..."      call a model, print its text
  git diff | hardline complete haiku - --system "Review this diff" --json
  hardline check                                   validate every tier against backends
"""


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


def _cmd_check(_: argparse.Namespace) -> int:
    sources = discover_sources()
    for source in sources:
        print(f"tier: {source.describe()}{'' if source.read() else '  (absent/empty)'}")
    runtime = build_runtime(sources=sources)
    print(f"ok: {len(runtime.registry)} model(s) valid against installed backends")
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
    sub.add_parser("check", help="validate config tiers against installed backends").set_defaults(
        func=_cmd_check
    )
    complete = sub.add_parser("complete", help="call a model and print its text")
    complete.add_argument("model", help="registry name; '' uses the default")
    complete.add_argument("prompt", help="the prompt, or '-' to read stdin")
    complete.add_argument("--system", default=None)
    complete.add_argument("--max-tokens", type=int, default=None)
    complete.add_argument("--temperature", type=float, default=None)
    complete.add_argument("--json", action="store_true", help="print the Completion as JSON")
    complete.set_defaults(func=lambda a: asyncio.run(_complete(a)))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.skill:
        from hardline.skill import get_skill

        print(get_skill())
        return 0
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    if getattr(args, "model", None) == "":
        args.model = None
    try:
        code: int = args.func(args)
    except HardlineError as e:
        print(f"hardline: {e}", file=sys.stderr)
        return 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())

"""``matrix``: see what is installed and whether a config composes. Read-only; runs nothing.

    matrix catalog [--point P] [--json]     every registered extension, and any that failed
    matrix describe TYPE_URL [--json]       one extension: config schema, needs, effects
    matrix config [--tool T] [--sources] [--json]
                                            the merged config a tool would see, and its files
    matrix check [--tool T] [--json]        compose runtimes and agents; call nothing
    matrix agents [--tool T] [--json]       the agents a config defines, with their runtimes

Data goes to stdout, diagnostics to stderr. With ``--json``, every document carries a
``schema`` and an error is ``{"error": {"kind", "message", "fix", ...}}`` on stderr. Exit
codes: 0 ok, 1 failure, 2 usage, 3 config, 4 not found, 5 transient, 6 auth.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from matrix.composition.config import load_config
from matrix.composition.container import compose, default_registry
from matrix.domain.errors import MatrixError, NotFoundError

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2


def _emit(document: dict[str, Any], as_json: bool, text: str) -> None:
    if as_json:
        print(json.dumps(document, indent=2, default=str))
    else:
        print(text)


def _catalog(args: argparse.Namespace) -> int:
    registry = default_registry()
    entries = registry.entries(args.point)
    document = {
        "schema": "matrix.v1.catalog",
        "entries": [
            {k: v for k, v in e.describe().items() if k != "config"}
            | {"configurable": e.config is not None}
            for e in entries
        ],
        "failures": [vars(f) for f in registry.failures],
    }
    lines = [f"{e.point:<13} {e.type_url:<36} {e.stability:<12} {e.summary}" for e in entries]
    for f in registry.failures:
        lines.append(f"FAILED        {f.extension} ({f.origin}): {f.error}")
    _emit(document, args.json, "\n".join(lines) or "(nothing registered)")
    return EXIT_OK


def _describe(args: argparse.Namespace) -> int:
    registry = default_registry()
    matches = [e for e in registry.entries() if e.type_url == args.type_url]
    if not matches:
        known = ", ".join(e.type_url for e in registry.entries())
        raise NotFoundError(
            f"nothing is registered as {args.type_url!r}. Registered: {known}",
            fix="run `matrix catalog` for every registered type URL",
        )
    document = {"schema": "matrix.v1.extension", "entries": [e.describe() for e in matches]}
    text = "\n\n".join(json.dumps(e.describe(), indent=2) for e in matches)
    _emit(document, args.json, text)
    return EXIT_OK


def _config(args: argparse.Namespace) -> int:
    config = load_config(None, args.tool)
    document = {
        "schema": "matrix.v1.config",
        "tool": args.tool,
        "sources": list(config.sources),
        "matrix": config.matrix.model_dump(mode="json"),
    }
    if args.sources:
        _emit(
            {"schema": "matrix.v1.config-sources", "sources": list(config.sources)},
            args.json,
            "\n".join(config.sources),
        )
    else:
        _emit(document, args.json, json.dumps(document, indent=2))
    return EXIT_OK


def _check(args: argparse.Namespace) -> int:
    config = load_config(None, args.tool)
    container = compose(config)
    document = {
        "schema": "matrix.v1.check",
        "ok": True,
        "runtimes": sorted(container.runtimes),
        "agents": sorted(container.agents),
        "observers": len(container.observers),
        "sources": list(config.sources),
    }
    text = (
        f"ok: {len(container.runtimes)} runtimes, {len(container.agents)} agents compose "
        "(nothing was called)"
    )
    _emit(document, args.json, text)
    return EXIT_OK


def _agents(args: argparse.Namespace) -> int:
    config = load_config(None, args.tool)
    container = compose(config)
    rows = []
    for name, agent in sorted(container.agents.items()):
        spec = config.matrix.agents[name]
        rows.append(
            {
                "name": name,
                "runtime": spec.runtime,
                "runtime_type": config.matrix.runtimes[spec.runtime].type,
                "model": agent.definition.model,
                "tools": None if agent.definition.tools is None else list(agent.definition.tools),
                "description": agent.definition.description,
            }
        )
    text = "\n".join(f"{r['name']:<24} {r['runtime']:<14} {r['description']}" for r in rows)
    _emit({"schema": "matrix.v1.agents", "agents": rows}, args.json, text or "(no agents)")
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="matrix", description="Inspect matrix extensions and configs. Runs nothing."
    )
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    sub = parser.add_subparsers(dest="command")

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--json", action="store_true", help="machine-readable output")
        return p

    p = add("catalog", "list registered extensions and failed ones")
    p.add_argument("--point", help="only this extension point (runtime, component, ...)")
    p.set_defaults(handler=_catalog)
    p = add("describe", "show one extension's config schema, needs and effects")
    p.add_argument("type_url")
    p.set_defaults(handler=_describe)
    for name, handler, help_text in (
        ("config", _config, "show the merged config and the files it came from"),
        ("check", _check, "compose runtimes and agents without calling anything"),
        ("agents", _agents, "list the agents a config defines"),
    ):
        p = add(name, help_text)
        p.add_argument("--tool", default="matrix", help="whose config tiers to read")
        if name == "config":
            p.add_argument("--sources", action="store_true", help="only list the files")
        p.set_defaults(handler=handler)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0) and EXIT_USAGE
    if args.version:
        from matrix import __version__

        print(__version__)
        return EXIT_OK
    if not getattr(args, "handler", None):
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    try:
        code: int = args.handler(args)
        return code
    except MatrixError as e:
        if args.json:
            print(json.dumps(e.payload(), default=str), file=sys.stderr)
        else:
            print(f"error ({e.kind}): {e.message}", file=sys.stderr)
            if e.fix:
                print(f"fix: {e.fix}", file=sys.stderr)
        return e.exit_code


def run() -> None:
    sys.exit(main())

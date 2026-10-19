"""Load-bearing constraints, checked mechanically so they cannot erode in review.

1. The domain (matrix's core) imports only pydantic, itself, and the standard-library
   modules on :data:`DOMAIN_STDLIB`: no adapter, no YAML, no telemetry, no agent SDK, and
   no filesystem, network, process, signal or import machinery. So the core runs with
   pydantic alone, and a caller who wants no tracing pays nothing for it.
2. Nothing outside ``matrix.domain.ids`` builds or splits a type URL by string surgery on
   ``/``, so the grammar has one owner.
3. The command line reaches matrix only through ``import matrix``: what it can do, a
   program can do with the declared names.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "matrix"
ALLOWED_THIRD_PARTY = {"pydantic"}

#: What the domain may take from the standard library: computation, plus the asyncio
#: scheduling, clock, run ids and log records the executor needs to run components.
DOMAIN_STDLIB = frozenset(
    {
        "__future__",
        "asyncio",
        "collections",
        "contextlib",
        "contextvars",
        "copy",
        "dataclasses",
        "datetime",
        "inspect",
        "json",
        "logging",
        "re",
        "time",
        "typing",
        "uuid",
    }
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_the_domain_imports_only_stdlib_pydantic_and_itself() -> None:
    offenders: list[str] = []
    for path in sorted((SRC / "domain").rglob("*.py")):
        for name in _imports(path):
            root = name.split(".")[0]
            if root in DOMAIN_STDLIB or root in ALLOWED_THIRD_PARTY:
                continue
            if name.startswith("matrix.domain") or name == "matrix":
                continue
            offenders.append(f"{path.relative_to(SRC)} imports {name}")
    assert offenders == []


def test_no_slash_type_urls_remain_in_source() -> None:
    hits = [str(p.relative_to(SRC)) for p in SRC.rglob("*.py") if ".v1/" in p.read_text()]
    assert hits == []


def test_the_command_line_calls_only_the_declared_api() -> None:
    import matrix

    cli = SRC / "adapters" / "_in" / "cli.py"
    assert {name for name in _imports(cli) if name.split(".")[0] == "matrix"} == {"matrix"}
    used = {
        node.attr
        for node in ast.walk(ast.parse(cli.read_text()))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "matrix"
    }
    assert used and used <= set(matrix.__all__)


def test_the_api_reference_lists_every_exported_name_and_no_other() -> None:
    import re

    import matrix

    text = (SRC.parents[1] / "docs" / "reference" / "api.md").read_text()
    block = text[text.index("from matrix import (") : text.index(")\n```")]
    listed = set(re.findall(r"^\s+([A-Za-z_]+),", block, re.M))
    assert listed == set(matrix.__all__), listed ^ set(matrix.__all__)


def test_the_names_the_readme_calls_stable_are_exported() -> None:
    import re

    import matrix

    text = (SRC.parents[1] / "README.md").read_text()
    section = text[text.index("## The stable public API") : text.index("## Documentation")]
    stable = section[: section.index("Every other exported name")]
    named = set(re.findall(r"`([A-Za-z_]+)`", stable)) - {"matrix", "v1"}
    assert named and named <= set(matrix.__all__), named - set(matrix.__all__)

"""Load-bearing constraints, checked mechanically so they cannot erode in review.

1. The domain (matrix's core) imports only the standard library, pydantic and itself: no
   adapter, no YAML, no telemetry, no agent SDK. So the core runs with pydantic alone, and a
   caller who wants no tracing pays nothing for it.
2. Nothing outside ``matrix.domain.ids`` builds or splits a type URL by string surgery on
   ``/`` (the old form), so the grammar has one owner.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "matrix"
ALLOWED_THIRD_PARTY = {"pydantic"}


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
            if root in sys.stdlib_module_names or root in ALLOWED_THIRD_PARTY:
                continue
            if name.startswith("matrix.domain") or name == "matrix":
                continue
            offenders.append(f"{path.relative_to(SRC)} imports {name}")
    assert offenders == []


def test_no_slash_type_urls_remain_in_source() -> None:
    hits = [str(p.relative_to(SRC)) for p in SRC.rglob("*.py") if ".v1/" in p.read_text()]
    assert hits == []

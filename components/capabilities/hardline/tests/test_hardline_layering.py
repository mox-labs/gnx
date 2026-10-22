"""hardline's layers, checked on every test run: ``layers.json`` against ``src/hardline``.

The map is read with the semantics of slick crafting-aces' ``layer-check.py`` for Python, so the
same file runs under that script too: every import counts, one under ``TYPE_CHECKING`` or inside
a function included, a relative import is resolved against its module, and ``*`` admits any
module that is not hardline's own. Three rules, and full coverage:

* **reach**: each file imports only what its layer's ``allow`` lists;
* **forbid**: a layer's file never names an identifier its layer forbids (an adapter's class in
  the CLI, ``logging`` in a port);
* **single_importer**: one file alone names a technology (``httpx2``, ``opentelemetry``,
  ``yaml``);
* **coverage**: every scanned file belongs to a layer, and no layer's glob matches nothing, since
  a rule over no file passes silently.

Beyond the map, the CLI reaches hardline through ``hardline.__all__`` alone (F14). Each rule has a
planted violation below, so a check that stopped biting would fail here.

What this cannot see, so review still reads for it: an adapter reached through a module alias
(``m.MockBackend`` names ``m``), logic in a file whose imports are all allowed, and what a
dependency does at run time.
"""

from __future__ import annotations

import ast
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import hardline

PACKAGE = Path(__file__).resolve().parents[1]
MAP = json.loads((PACKAGE / "layers.json").read_text())


@dataclass
class Report:
    findings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    files_read: int = 0
    layers: dict[str, int] = field(default_factory=dict)


def _module(rel: Path) -> list[str]:
    parts = list(rel.with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return parts


def _reaches(tree: ast.Module, rel: Path) -> list[tuple[int, list[str]]]:
    module = _module(rel)
    package = module[:-1] if rel.name != "__init__.py" else module
    found: list[tuple[int, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name.split(".")) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - (node.level - 1)] if node.level else []
            found.append((node.lineno, base + (node.module.split(".") if node.module else [])))
    return found


def _identifiers(tree: ast.Module) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.append((node.lineno, node.id))
        elif isinstance(node, ast.Import):
            found.extend((node.lineno, a.name.split(".")[0]) for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.lineno, node.module.split(".")[0]))
    return found


def _allowed(path: list[str], allow: list[str], own: set[str]) -> bool:
    joined = ".".join(path)
    for entry in allow:
        if entry == "*":
            if path and path[0] not in own:
                return True
            continue
        if joined == entry or joined.startswith(entry + "."):
            return True
    return False


def check(spec: dict[str, Any], root: Path) -> Report:
    report = Report()
    scan: set[Path] = set()
    for pattern in spec["scan"]:
        hits = {p for p in root.glob(pattern) if p.is_file()}
        if not hits:
            report.errors.append(f"scan glob {pattern!r} matches no file")
        scan |= hits
    trees = {p: ast.parse(p.read_text(encoding="utf-8")) for p in sorted(scan)}
    report.files_read = len(trees)
    own = {_module(p.relative_to(root))[0] for p in scan if _module(p.relative_to(root))}
    claimed: set[Path] = set()
    for layer in spec["layers"]:
        files: set[Path] = set()
        for pattern in layer["files"]:
            hits = {p for p in root.glob(pattern) if p.is_file()}
            if not hits:
                report.errors.append(f"layer {layer['name']}: glob {pattern!r} matches no file")
            files |= hits
        report.layers[layer["name"]] = len(files)
        claimed |= files & scan
        for p in sorted(files & scan):
            rel = p.relative_to(root)
            for line, path in _reaches(trees[p], rel):
                if not _allowed(path, layer.get("allow", []), own):
                    report.findings.append(
                        f"{rel}:{line}: layer {layer['name']} may not reach {'.'.join(path)}"
                    )
            for line, name in _identifiers(trees[p]):
                if name in layer.get("forbid", []):
                    report.findings.append(
                        f"{rel}:{line}: layer {layer['name']} names forbidden {name}"
                    )
    for p in sorted(scan - claimed):
        report.errors.append(f"{p.relative_to(root)} is scanned but no layer claims it")
    for rule in spec.get("single_importer", []):
        only = root / rule["only"]
        if only not in scan:
            report.errors.append(f"single_importer {rule['name']}: {rule['only']} is not scanned")
        for p, tree in trees.items():
            if p == only:
                continue
            for line, name in _identifiers(tree):
                if name == rule["name"]:
                    report.findings.append(
                        f"{p.relative_to(root)}:{line}: only {rule['only']} may name {name}"
                    )
    return report


def _cli_reaches_outside_all(root: Path) -> list[str]:
    """Each place a driving file reaches hardline other than by a name in ``__all__``."""
    driving = next(layer for layer in MAP["layers"] if layer["name"] == "driving")
    found: list[str] = []
    for pattern in driving["files"]:
        for p in sorted(root.glob(pattern)):
            for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.level:
                    found.append(f"{p.name}:{node.lineno}: a relative import")
                elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "hardline"
                ):
                    if node.module != "hardline":
                        found.append(f"{p.name}:{node.lineno}: reaches {node.module}")
                    found.extend(
                        f"{p.name}:{node.lineno}: {a.name} is not in hardline.__all__"
                        for a in node.names
                        if a.name not in hardline.__all__
                    )
                elif isinstance(node, ast.Import):
                    found.extend(
                        f"{p.name}:{node.lineno}: imports {a.name}"
                        for a in node.names
                        if a.name.startswith("hardline.")
                    )
    return found


# --- the tree as it is ------------------------------------------------------------------------


def test_every_file_is_in_a_layer_and_every_dependency_points_inward() -> None:
    report = check(MAP, PACKAGE)
    assert report.errors == []
    assert report.findings == []
    assert report.files_read == len(list((PACKAGE / "src" / "hardline").rglob("*.py")))
    assert all(count > 0 for count in report.layers.values()), report.layers


def test_the_cli_reaches_hardline_through_its_public_names_alone() -> None:
    # F14: the CLI imported load_registry from hardline.composition, outside hardline.__all__.
    assert _cli_reaches_outside_all(PACKAGE) == []


# --- each rule bites: a violation planted in a copy of the tree -------------------------------


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    shutil.copytree(PACKAGE / "src", tmp_path / "src")
    return tmp_path


def _plant(tree: Path, rel: str, line: str) -> None:
    path = tree / "src" / "hardline" / rel
    path.write_text(path.read_text() + f"\n{line}\n")


@pytest.mark.parametrize(
    ("rel", "line", "finding"),
    [
        ("domain/types.py", "import os", "layer domain may not reach os"),
        ("domain/registry.py", "from hardline.domain.runtime import ModelRuntime",
         "layer domain may not reach hardline.domain.runtime"),
        ("domain/ports/_out/backend.py", "from hardline.domain.ports._out import secrets",
         "layer ports may not reach hardline.domain.ports._out"),
        ("domain/runtime.py", "from hardline.adapters._out import mock",
         "layer application may not reach hardline.adapters._out"),
        ("adapters/_out/mock.py", "from hardline.composition import build_runtime",
         "layer adapters_out may not reach hardline.composition"),
        ("adapters/_out/mock.py", "from . import openai_compat",
         "layer adapters_out may not reach hardline.adapters._out"),
    ],
)  # fmt: skip
def test_a_reach_outside_the_layer_is_found(tree: Path, rel: str, line: str, finding: str) -> None:
    _plant(tree, rel, line)
    assert any(finding in f for f in check(MAP, tree).findings), finding


@pytest.mark.parametrize(
    ("rel", "line", "finding"),
    [
        ("adapters/_in/cli.py", "MockBackend = None", "names forbidden MockBackend"),
        ("domain/ports/_out/observer.py", "logging = None", "names forbidden logging"),
    ],
)
def test_a_forbidden_name_is_found(tree: Path, rel: str, line: str, finding: str) -> None:
    _plant(tree, rel, line)
    assert any(finding in f for f in check(MAP, tree).findings), finding


@pytest.mark.parametrize(
    ("rel", "line", "finding"),
    [
        ("adapters/_out/mock.py", "import httpx2", "only src/hardline/adapters/_out/_http.py"),
        ("composition/__init__.py", "import opentelemetry", "may name opentelemetry"),
        ("adapters/_out/secrets.py", "import yaml", "may name yaml"),
    ],
)
def test_a_second_importer_of_a_technology_is_found(
    tree: Path, rel: str, line: str, finding: str
) -> None:
    _plant(tree, rel, line)
    assert any(finding in f for f in check(MAP, tree).findings), finding


def test_a_file_no_layer_claims_is_an_error(tree: Path) -> None:
    (tree / "src" / "hardline" / "telemetry.py").write_text("x = 1\n")
    assert check(MAP, tree).errors == [
        "src/hardline/telemetry.py is scanned but no layer claims it"
    ]


def test_a_layer_whose_glob_matches_nothing_is_an_error(tree: Path) -> None:
    (tree / "src" / "hardline" / "domain" / "digests.py").unlink()
    assert any("matches no file" in e for e in check(MAP, tree).errors)


@pytest.mark.parametrize(
    "line",
    [
        "from hardline.composition import load_registry",
        "from hardline import ModelRuntime as _R, _private",
        "import hardline.composition",
        "from . import _http",
    ],
)
def test_the_cli_reaching_past_hardline_all_is_found(tree: Path, line: str) -> None:
    _plant(tree, "adapters/_in/cli.py", line)
    assert _cli_reaches_outside_all(tree) != []

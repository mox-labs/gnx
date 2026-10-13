"""The layers, checked by scanning imports.

``domain`` holds the model and its pure logic; ``application`` orchestrates it over data.
Each may import only what is on its allowlist: a named set of pure standard-library
modules, pydantic, and the ix layers at or inside it. Everything else fails, including a
module nobody thought to forbid, and a call to the builtin ``open``.

Outside the inner layers: only ``ix.composition`` wires adapters; outbound adapters know
neither the use cases nor the command line; the command line reaches ix only through its
declared API; and importing ``ix`` loads no host.

Every import counts, including one under ``TYPE_CHECKING``: a type-only dependency on a
host is still a dependency on its shape.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import ix

SRC = Path(ix.__file__).resolve().parent

#: Standard-library modules an inner layer may import: pure computation only. A clock, a
#: random source, the filesystem and the network are all absent on purpose.
PURE_STDLIB = frozenset(
    {
        "__future__",
        "collections",
        "dataclasses",
        "hashlib",
        "itertools",
        "json",
        "re",
        "typing",
    }
)

#: Third-party packages an inner layer may import.
THIRD_PARTY = frozenset({"pydantic"})

#: ix packages an inner layer may import, by layer.
INNER = {"domain": ("ix.domain",), "application": ("ix.domain", "ix.application")}


def _imports(file: Path) -> set[str]:
    """Every module a file imports, as dotted names."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{file}: relative import"
            found.add(node.module or "")
    return found


def _within(module: str, packages: tuple[str, ...]) -> bool:
    return any(module == package or module.startswith(package + ".") for package in packages)


def _violations(file: Path, layer: str) -> set[str]:
    """What ``file`` does that a member of ``layer`` may not: an import off the allowlist,
    or a call to the builtin ``open``."""
    bad: set[str] = set()
    for module in _imports(file):
        root = module.split(".")[0]
        if root == "ix":
            if not _within(module, INNER[layer]):
                bad.add(module)
        elif root not in PURE_STDLIB | THIRD_PARTY:
            bad.add(root)
    for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "open"
        ):
            bad.add("open()")
    return bad


def _files(*parts: str) -> list[Path]:
    return sorted(SRC.joinpath(*parts).rglob("*.py"))


@pytest.mark.parametrize("layer", sorted(INNER))
def test_inner_layers_import_only_what_is_allowed(layer: str) -> None:
    assert _files(layer), f"no files in {layer}"
    offenders = {
        file.relative_to(SRC).as_posix(): sorted(bad)
        for file in _files(layer)
        if (bad := _violations(file, layer))
    }
    assert offenders == {}


def test_the_scan_catches_what_it_claims(tmp_path: Path) -> None:
    """The instrument itself: a domain file doing each forbidden thing is reported, and the
    list is an allowlist, so a module nobody named is caught too."""
    file = tmp_path / "bad.py"
    file.write_text(
        "from typing import TYPE_CHECKING\n"
        "import json, hashlib, re\n"
        "import yaml\n"
        "import click\n"
        "from rich.console import Console\n"
        "from pathlib import Path\n"
        "import subprocess, datetime, time, random, uuid, zipfile, os\n"
        "from ix.adapters._out.declaration_files import GenreFiles\n"
        "from ix.composition import root\n"
        "from ix.application.experiments import examine\n"
        "from ix.domain.schema import Record\n"
        "if TYPE_CHECKING:\n"
        "    from matrix import Agent\n"
        "text = open('x').read()\n"
    )
    assert _violations(file, "domain") == {
        "yaml", "click", "rich", "pathlib", "subprocess", "datetime", "time", "random",
        "uuid", "zipfile", "os", "ix.adapters._out.declaration_files", "ix.composition",
        "ix.application.experiments", "matrix", "open()",
    }  # fmt: skip
    assert "ix.application.experiments" not in _violations(file, "application")


def test_only_composition_wires_outbound_adapters() -> None:
    """``ix.adapters._out`` is imported by ``ix.composition`` and by nothing else in ix."""
    importers = {
        file.relative_to(SRC).as_posix()
        for file in _files()
        if any(_within(module, ("ix.adapters._out",)) for module in _imports(file))
    }
    assert importers == {"composition/__init__.py"}


def test_outbound_adapters_know_neither_use_cases_nor_the_command_line() -> None:
    for file in _files("adapters", "_out"):
        reached = {m for m in _imports(file) if m.split(".")[0] == "ix"}
        assert all(_within(m, ("ix.domain",)) for m in reached), (file.name, reached)


def test_the_facade_imports_no_adapter() -> None:
    """``ix/__init__.py`` and ``ix/api.py`` name the composition root and the domain, never
    an adapter: which adapters are bound is not the facade's decision."""
    for name in ("__init__.py", "api.py"):
        reached = {m for m in _imports(SRC / name) if m.split(".")[0] == "ix"}
        assert not any(_within(m, ("ix.adapters", "ix.application")) for m in reached), name


def test_nothing_in_ix_imports_a_host() -> None:
    """No module names matrix. When a run path arrives, only ``ix.composition`` and the
    matrix adapters may, and this test is where that is written down."""
    for file in _files():
        assert "matrix" not in {m.split(".")[0] for m in _imports(file)}, file


def test_the_command_line_calls_only_the_declared_api() -> None:
    """``ix.adapters._in.cli`` imports ``ix`` itself and no module inside it."""
    imported = {
        module
        for module in _imports(SRC / "adapters" / "_in" / "cli.py")
        if module.split(".")[0] == "ix"
    }
    assert imported == {"ix"}


def test_the_command_line_uses_only_declared_names() -> None:
    """Every ``ix.<name>`` the commands touch is in ``ix.__all__``."""
    tree = ast.parse((SRC / "adapters" / "_in" / "cli.py").read_text(encoding="utf-8"))
    used = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ix"
    }
    assert used, "the commands call nothing on ix"
    assert used <= set(ix.__all__)


class TestLibrarySurface:
    def test_all_names_exist_once(self) -> None:
        assert sorted(ix.__all__) == sorted(set(ix.__all__))
        for name in ix.__all__:
            assert hasattr(ix, name), name

    def test_the_surface_is_functions_documents_authored_records_and_errors(self) -> None:
        """The rule for ``__all__``: a function, a document a function returns, a record an
        author or an extension writes, an error, or one of two published tables. A record's
        parts are reachable as field types and are not exported."""
        functions = {"load", "validate", "resolve", "plan", "register", "deviations",
                     "genres", "kinds", "skill", "error_document"}  # fmt: skip
        documents = {"Validation", "Problem", "Design", "Plan", "Registered", "Deviations",
                     "Genres", "Kinds"}  # fmt: skip
        authored = {"Experiment", "Genre", "Kind"}
        errors = {"IxError", "ConfigError", "NotFoundError", "InvalidExperimentError",
                  "RegistrationConflictError"}  # fmt: skip
        tables = {"PROBLEM_KINDS", "EXIT_CODES", "__version__"}
        assert set(ix.__all__) == functions | documents | authored | errors | tables
        assert len(ix.__all__) < 30

    def test_no_declared_name_is_shadowed_by_a_subpackage(self) -> None:
        """Importing ``ix.<sub>`` binds ``<sub>`` on ``ix``; a function of the same name
        would be replaced by the module the first time anything imported it."""
        modules = {path.stem for path in SRC.iterdir() if path.is_dir() or path.suffix == ".py"}
        assert not (set(ix.__all__) & modules)
        ix.skill()  # reads the genre and kind packages
        for name in ("genres", "kinds", "plan", "validate", "register", "deviations"):
            assert callable(getattr(ix, name)), name


def test_importing_ix_loads_no_host_and_wires_nothing() -> None:
    """``import ix`` imports no matrix and builds no composition root."""
    code = (
        "import sys, ix\n"
        "ix.validate\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in "
        "{'matrix', 'hardline', 'claude_agent_sdk', 'inspect_ai'} or m == 'ix.composition'))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_the_domain_names_no_genre_and_no_kind() -> None:
    """A new genre or kind is a data file and no edit to the core: no domain module holds a
    built-in's name in a string it acts on (docstrings aside)."""
    builtin = SRC / "builtin"
    genres = {file.stem for file in (builtin / "genres").glob("*.yaml")}
    kinds = {file.stem.split(".", 1)[1] for file in (builtin / "kinds").glob("*.yaml")}
    assert genres >= {"evaluate", "compare", "characterize", "regress"}
    assert kinds >= {"http-load", "function-test", "inspect-task", "compose", "judge"}
    # Words that are also plain English or a family name stay out of the scan.
    names = (genres | kinds) - {"none", "process", "agent", "prompt", "usage", "memory", "timing"}
    for file in _files("domain"):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        docstrings = {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                words = set(node.value.replace("_", " ").replace(",", " ").split())
                assert not (words & names), f"{file.name}: {node.value!r}"

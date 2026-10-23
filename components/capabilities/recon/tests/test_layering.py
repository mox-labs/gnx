"""The layers, checked by scanning imports.

``domain`` holds the model, its pure logic and the ports; ``application`` orchestrates it
over those ports. Each may import only what is on its allowlist: a named set of pure
standard-library modules, pydantic, and the recon layers at or inside it. Everything else
fails, including a module nobody thought to forbid, a call to the builtin ``open``, a file
operation on a path, and a read of the clock.

Outside the inner layers: only ``recon.composition`` wires outbound adapters; outbound
adapters know neither the use cases nor the command line; every driving adapter, each file
under ``adapters/_in``, reaches recon only through its declared API; importing ``recon``
loads no adapter library; the clock is read only by the clock adapter; and recon names no
matrix.

Every import counts, including one under ``TYPE_CHECKING``: a type-only dependency on an
adapter is still a dependency on its shape.

``layers.json`` holds every module to a layer, read by the workspace's shared checker as
slick crafting-aces' ``layer-check.py`` reads it: a module no layer claims fails the suite,
an adapter reaches only its own family, each technology is named by one file (YAML by the
file store alone), and each rule has a planted violation that must be found.

What these cannot see, so review still reads for it (the design record keeps the list): a
module reached by ``importlib`` or through an alias, a use that is not an import, a
re-export under another name (``composition`` re-exports ``Recon``), a file effect through
a call this scan does not list, logic in a file whose imports are all allowed, and what a
collector plugin does at run time.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

try:
    from _testing.layers import Plant, check, copy_tree, load, plant, plants, reaches_outside
except ImportError:  # a package run apart from its workspace has no shared checker
    pytest.skip(
        "not run from the workspace: no _testing beside the packages", allow_module_level=True
    )

import recon

SRC = Path(__file__).resolve().parents[1] / "src" / "recon"

#: Standard-library modules an inner layer may import: pure computation only. ``pathlib``
#: names a location (a mission directory, an archive); touching the filesystem through it
#: is caught separately. ``datetime`` is the type the clock port returns; reading the time
#: through it is caught separately. ``inspect`` reads a transform's signature, and
#: ``functools`` binds its keywords.
PURE_STDLIB = frozenset(
    {
        "__future__",
        "collections",
        "contextlib",
        "dataclasses",
        "datetime",
        "functools",
        "html",
        "inspect",
        "pathlib",
        "re",
        "typing",
        "urllib",
    }
)

#: What a layer may import beyond that. The application hashes the config it ran and encodes
#: a hook payload as JSON; the domain does neither. The domain reads ``threading.TIMEOUT_MAX``,
#: the longest wait the platform's timers take, to hold a source's ``timeout`` and rate to it
#: (``domain/clock.py``); it starts no thread. Accepted (owner ruling 2026-10-22).
LAYER_STDLIB = {
    "domain": frozenset({"threading"}),
    "application": frozenset({"hashlib", "json"}),
}

#: Third-party packages an inner layer may import.
THIRD_PARTY = frozenset({"pydantic"})

#: recon packages an inner layer may import, by layer.
INNER = {"domain": ("recon.domain",), "application": ("recon.domain", "recon.application")}

#: Methods that touch the filesystem through a path.
PATH_IO = frozenset(
    {
        "read_text", "read_bytes", "write_text", "write_bytes", "open", "exists", "is_dir",
        "is_file", "iterdir", "glob", "rglob", "mkdir", "unlink", "touch", "stat", "resolve",
    }
)  # fmt: skip

#: Reads of the clock other than through the port.
CLOCK_READS = frozenset({"now", "utcnow", "today", "fromtimestamp"})


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


def _effects(file: Path) -> set[str]:
    """The effects ``file`` performs directly: ``open()``, a path operation, a clock read."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            found.add("open()")
        elif isinstance(func, ast.Attribute) and func.attr in PATH_IO:
            found.add(f".{func.attr}()")
        elif (
            isinstance(func, ast.Attribute)
            and func.attr in CLOCK_READS
            and isinstance(func.value, ast.Name)
            and func.value.id in {"datetime", "date"}
        ):
            found.add(f"{func.value.id}.{func.attr}()")
    return found


def _violations(file: Path, layer: str) -> set[str]:
    """What ``file`` does that a member of ``layer`` may not."""
    bad: set[str] = set()
    for module in _imports(file):
        root = module.split(".")[0]
        if root == "recon":
            if not _within(module, INNER[layer]):
                bad.add(module)
        elif root not in PURE_STDLIB | THIRD_PARTY | LAYER_STDLIB[layer]:
            bad.add(root)
    return bad | _effects(file)


def _files(*parts: str) -> list[Path]:
    return sorted(SRC.joinpath(*parts).rglob("*.py"))


def _relative(file: Path) -> str:
    return file.relative_to(SRC).as_posix()


@pytest.mark.parametrize("layer", sorted(INNER))
def test_inner_layers_import_only_what_is_allowed(layer: str) -> None:
    assert _files(layer), f"no files in {layer}"
    offenders = {_relative(f): sorted(bad) for f in _files(layer) if (bad := _violations(f, layer))}
    assert offenders == {}


def test_the_scan_catches_what_it_claims(tmp_path: Path) -> None:
    """The instrument itself: a domain file doing each forbidden thing is reported, and the
    list is an allowlist, so a module nobody named is caught too."""
    file = tmp_path / "bad.py"
    file.write_text(
        "from typing import TYPE_CHECKING\n"
        "import re\n"
        "import yaml, duckdb, httpx2, glom\n"
        "import os, time, uuid, subprocess, json, hashlib\n"
        "from datetime import datetime\n"
        "from recon.adapters._out.archive_files import ArchiveFiles\n"
        "from recon.composition import root\n"
        "from recon.application.plan import build_plan\n"
        "from recon.domain.models import ReconConfig\n"
        "if TYPE_CHECKING:\n"
        "    from matrix import Registry\n"
        "text = open('x').read()\n"
        "stamp = datetime.now()\n"
        "def f(path):\n"
        "    return path.read_text(), path.exists()\n"
    )
    assert _violations(file, "domain") == {
        "yaml", "duckdb", "httpx2", "glom", "os", "time", "uuid", "subprocess", "json",
        "hashlib", "recon.adapters._out.archive_files", "recon.composition",
        "recon.application.plan", "matrix", "open()", "datetime.now()", ".read_text()",
        ".exists()",
    }  # fmt: skip
    in_application = _violations(file, "application")
    assert "recon.application.plan" not in in_application
    assert {"json", "hashlib"}.isdisjoint(in_application) and "yaml" in in_application


def test_only_composition_wires_outbound_adapters() -> None:
    """Outside ``recon.adapters._out`` itself, ``recon.composition`` is the only importer of
    an outbound adapter. (One adapter may build on another: archives on the capture log.)"""
    importers = {
        _relative(file)
        for file in _files()
        if any(_within(module, ("recon.adapters._out",)) for module in _imports(file))
    }
    assert {name for name in importers if not name.startswith("adapters/_out/")} == {
        "composition.py"
    }


def test_outbound_adapters_know_neither_use_cases_nor_the_command_line() -> None:
    for file in _files("adapters", "_out"):
        reached = {m for m in _imports(file) if m.split(".")[0] == "recon"}
        assert all(_within(m, ("recon.domain", "recon.adapters._out")) for m in reached), (
            file.name,
            reached,
        )


def test_the_facade_imports_no_adapter_and_no_use_case() -> None:
    """``recon/__init__.py``, ``api.py`` and ``skill_files.py`` name the composition root and the
    domain, never an adapter or the application: which adapters are bound is not theirs."""
    for name in ("__init__.py", "api.py", "skill_files.py"):
        reached = {m for m in _imports(SRC / name) if m.split(".")[0] == "recon"}
        assert not any(_within(m, ("recon.adapters", "recon.application")) for m in reached), name


def _driving_reaches(src: Path) -> list[str]:
    """Each place a driving adapter, any file under ``adapters/_in``, reaches recon other than
    through ``recon.__all__``."""
    driving = sorted((src / "adapters" / "_in").rglob("*.py"))
    return reaches_outside(driving, "recon", recon.__all__)


def test_the_command_line_calls_only_the_declared_api() -> None:
    """Every driving adapter, the command line first, imports ``recon`` itself and no module
    inside it, and every ``recon.<name>`` it touches is in ``recon.__all__``."""
    cli = SRC / "adapters" / "_in" / "cli.py"
    assert _driving_reaches(SRC) == []
    tree = ast.parse(cli.read_text(encoding="utf-8"))
    used = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "recon"
    }
    assert used, "the commands call nothing on recon"


@pytest.mark.parametrize(
    ("rel", "line"),
    [
        # recon-tests / first pass: the rule bound adapters/_in/cli.py by path, so a second
        # driving adapter could reach the use cases or the root and pass.
        ("adapters/_in/server.py", "from recon.composition import root"),
        ("adapters/_in/server.py", "from recon.application.use_cases import survey"),
        ("adapters/_in/server.py", "import recon\nrecon.composition"),
        ("adapters/_in/cli.py", "from . import server"),
        ("adapters/_in/cli.py", "from recon import _private"),
    ],
)
def test_a_driving_adapter_reaching_past_recon_all_is_found(
    tmp_path: Path, rel: str, line: str
) -> None:
    tree = copy_tree(SRC.parents[1], tmp_path)
    (tree / "src" / "recon" / rel).touch()
    plant(tree, Plant("driving", f"src/recon/{rel}", line, ""))
    assert _driving_reaches(tree / "src" / "recon") != []


def test_the_clock_is_read_only_through_its_port() -> None:
    """Determinism rests on this: the time of day and the passing of time reach recon only
    through ``Clock``, whose one real implementation is the system clock adapter. A unique
    id is drawn only for a capture, the contract's one stated exception."""
    reads_clock = {
        _relative(f)
        for f in _files()
        if "time" in {m.split(".")[0] for m in _imports(f)}
        or any(e.startswith(("datetime.", "date.")) for e in _effects(f))
    }
    assert reads_clock == {"adapters/_out/system_clock.py"}
    draws_ids = {
        _relative(f)
        for f in _files()
        if {"uuid", "random"} & {m.split(".")[0] for m in _imports(f)}
    }
    assert draws_ids == {"adapters/_out/capture_files.py"}


def test_recon_names_no_matrix() -> None:
    """recon's core stays off matrix: a reasoning layer on matrix may sit on top, outside."""
    for file in _files():
        roots = {m.split(".")[0] for m in _imports(file)}
        assert not roots & {"matrix", "ix", "hardline"}, _relative(file)


class TestLibrarySurface:
    def test_all_names_exist_once(self) -> None:
        assert sorted(recon.__all__) == sorted(set(recon.__all__))
        for name in recon.__all__:
            assert hasattr(recon, name), name

    def test_the_surface_is_operations_errors_and_tables(self) -> None:
        """The rule for ``__all__``: an operation, the versioned document an operation
        returns, the skill text, an error, or a published table. The extension contract for
        collector types lives in ``recon.domain.collector`` and is not re-exported.

        Changed requirement: the operations return typed documents, so their types are
        declared names too."""
        operations = {"init", "plan", "survey", "status", "query", "templates", "capture",
                      "survey_failure", "error_document", "skill", "skill_references",
                      "configure_telemetry"}  # fmt: skip
        documents = {"MissionDocument", "PlanDocument", "SurveyDocument", "TableOutcome",
                     "StatusDocument", "QueryRows", "TemplatesDocument", "Capture",
                     "ErrorDocument"}  # fmt: skip
        errors = {"ReconError", "UsageError", "ConfigError", "ConflictError", "NotFoundError",
                  "QueryError", "CollectionError", "Problem"}  # fmt: skip
        tables = {"ERROR_KINDS", "EXIT_CODES", "EXIT_MEANINGS", "EXIT_NEXT", "PROBLEM_KINDS",
                  "__version__"}  # fmt: skip
        assert set(recon.__all__) == operations | documents | errors | tables

    def test_no_declared_name_is_shadowed_by_a_subpackage(self) -> None:
        """Importing ``recon.<sub>`` binds ``<sub>`` on ``recon``; a function of the same
        name would be replaced by the module the first time anything imported it."""
        modules = {path.stem for path in SRC.iterdir() if path.is_dir() or path.suffix == ".py"}
        assert not (set(recon.__all__) & modules)


def test_importing_recon_loads_no_adapter_and_wires_nothing() -> None:
    """``import recon`` builds no root; building one imports neither httpx2 nor DuckDB nor
    markitdown nor the MCP client, so planning and status never load them."""
    code = (
        "import sys, recon\n"
        "heavy = {'httpx2', 'duckdb', 'markitdown', 'yaml', 'fastmcp', 'mcp', 'mcp_types',\n"
        "         'jsonschema', 'referencing'}\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in heavy "
        "or m == 'recon.composition'))\n"
        "from recon.composition import root\n"
        "root()\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in heavy - {'yaml'}))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["[]", "[]"]


#: The MCP client and what only it imports.
MCP_CLIENT = frozenset({"fastmcp", "mcp", "mcp_types", "jsonschema", "referencing"})

_PROBE = (
    "import atexit, sys\n"
    "atexit.register(lambda: print(sorted({m.split('.')[0] for m in sys.modules} & HEAVY),\n"
    "                              file=open('loaded.txt', 'w')))\n"
    "sys.argv = ['recon', *sys.argv[1:]]\n"
    "from recon.adapters._in.cli import main\n"
    "main()\n"
)


@pytest.mark.parametrize(
    "argv", [["--help"], ["init", "m", "-t", "mcp-captures"], ["capture", "m"]], ids=" ".join
)
def test_the_command_line_and_the_hook_route_never_load_the_mcp_client(
    argv: list[str], tmp_path: Path
) -> None:
    """The MCP client is a dependency of every install (no extras), so it must cost nothing
    where it is not used: ``recon --help`` and ``recon capture``, which a hook runs on every
    tool call, load none of it."""
    if argv[0] == "capture":
        init = [
            sys.executable,
            "-c",
            _PROBE.replace("HEAVY", "set()"),
            "init",
            "m",
            "-t",
            "mcp-captures",
        ]
        subprocess.run(init, cwd=tmp_path, check=True, capture_output=True)
    payload = {"tool_name": "mcp__x__y", "tool_input": {}, "hook_event_name": "PostToolUse",
               "tool_response": [{"type": "text", "text": "{}"}]}  # fmt: skip
    result = subprocess.run(
        [sys.executable, "-c", _PROBE.replace("HEAVY", repr(set(MCP_CLIENT))), *argv],
        cwd=tmp_path,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "loaded.txt").read_text().strip() == "[]"


# --- the map: every module in a layer, and each rule biting ---------------------------------

PACKAGE = SRC.parents[1]
MAP = load(PACKAGE)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return copy_tree(PACKAGE, tmp_path)


def test_every_module_is_in_a_layer_and_every_dependency_points_inward() -> None:
    """``layers.json``: no module goes unjudged, adapters reach only their own family, each
    technology is named by one file, and YAML stays in the file store."""
    report = check(MAP, PACKAGE)
    assert report.errors == []
    assert report.findings == []
    assert report.exit_code == 0
    assert report.files_in_scan == len(list(SRC.rglob("*.py")))
    assert all(count > 0 for count in report.layers.values()), report.layers


@pytest.mark.parametrize("violation", [pytest.param(p, id=p.rule) for p in plants(MAP, PACKAGE)])
def test_each_rule_of_the_map_bites(tree: Path, violation: Plant) -> None:
    plant(tree, violation)
    assert any(violation.finding in f for f in check(MAP, tree).findings), violation.finding


@pytest.mark.parametrize(
    ("rel", "line", "finding"),
    [
        # The first pass's finding F3, since fixed: the query read the store's file layout.
        ("adapters/_out/duckdb_query.py", "from .archive_files import ArchiveFiles",
         "layer query may not reach recon.adapters._out.archive_files"),
        ("adapters/_out/web_collector.py", "from .http_requester import HttpRequester",
         "layer collectors may not reach recon.adapters._out.http_requester"),
        ("adapters/_out/capture_collector.py", "import yaml",
         "layer collectors names forbidden yaml"),
    ],
)  # fmt: skip
def test_a_reach_across_adapter_families_is_found(
    tree: Path, rel: str, line: str, finding: str
) -> None:
    plant(tree, Plant("family", f"src/recon/{rel}", line, finding))
    assert any(finding in f for f in check(MAP, tree).findings), finding


def test_a_file_no_layer_claims_leaves_the_tree_unjudged(tree: Path) -> None:
    (tree / "src" / "recon" / "adapters" / "_out" / "sql_collector.py").write_text("x = 1\n")
    report = check(MAP, tree)
    assert report.errors == [
        "src/recon/adapters/_out/sql_collector.py is scanned but no layer claims it"
    ]
    assert report.exit_code == 2


def test_a_layer_whose_file_moved_leaves_the_tree_unjudged(tree: Path) -> None:
    out = tree / "src" / "recon" / "adapters" / "_out"
    (out / "duckdb_query.py").rename(out / "query.py")
    report = check(MAP, tree)
    assert "layer query: glob 'src/recon/adapters/_out/duckdb_query.py' matches no file" in (
        report.errors
    )
    assert report.exit_code == 2


def test_the_public_names_are_the_ones_callers_use() -> None:
    """``import recon``'s names are a contract: removing one breaks a caller. Adding one is
    compatible, and is added here with the change that adds it."""
    assert set(recon.__all__) == {
        "Capture", "CollectionError", "ConfigError", "ConflictError", "ERROR_KINDS",
        "EXIT_CODES", "EXIT_MEANINGS", "EXIT_NEXT", "ErrorDocument", "MissionDocument",
        "NotFoundError", "PROBLEM_KINDS", "PlanDocument", "Problem", "QueryError", "QueryRows",
        "ReconError", "StatusDocument", "SurveyDocument", "TableOutcome", "TemplatesDocument",
        "UsageError", "__version__", "capture", "configure_telemetry", "error_document", "init",
        "plan", "query", "skill", "skill_references", "status", "survey", "survey_failure",
        "templates",
    }  # fmt: skip

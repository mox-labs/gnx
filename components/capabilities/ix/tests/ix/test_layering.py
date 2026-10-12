"""The layers, checked by scanning imports.

``domain`` holds the model and its pure logic: it may import the standard library's pure
modules, pydantic, and itself. It may not import an adapter, the composition root, a host
(matrix), or anything that does I/O or wraps a tool (yaml, click, rich, the filesystem, the
network, subprocesses). ``application`` orchestrates ports and obeys the same rule. The
command-line adapter for the experiment model reaches ix only through its declared API.

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

#: Third-party packages the inner layers may import.
ALLOWED_THIRD_PARTY = frozenset({"pydantic"})

#: Standard-library modules that reach the filesystem, the network, a process or the
#: interpreter's environment.
IO_STDLIB = frozenset(
    {
        "asyncio",
        "glob",
        "http",
        "importlib",
        "io",
        "logging",
        "multiprocessing",
        "os",
        "pathlib",
        "shutil",
        "signal",
        "socket",
        "sqlite3",
        "ssl",
        "subprocess",
        "sys",
        "tempfile",
        "threading",
        "urllib",
    }
)

#: ix packages an inner layer may import, by layer.
INNER = {"domain": ("ix.domain",), "application": ("ix.domain", "ix.application")}

#: Imports of the agent-eval harness's domain modules that break the rule. They predate it
#: and leave with the harness; nothing may be added, and an entry that is no longer needed
#: fails the test below so the list only shrinks.
HARNESS_EXEMPTIONS: dict[str, frozenset[str]] = {
    "domain/type_urls.py": frozenset({"matrix"}),
    "domain/errors.py": frozenset({"matrix"}),
    "domain/ports.py": frozenset({"matrix", "pathlib"}),
}


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


def _violations(file: Path, layer: str) -> set[str]:
    """The roots of the imports ``file`` may not make as a member of ``layer``."""
    bad: set[str] = set()
    for module in _imports(file):
        root = module.split(".")[0]
        if root == "__future__":
            continue
        if root == "ix":
            if not any(module == ok or module.startswith(ok + ".") for ok in INNER[layer]):
                bad.add(module)
        elif root in sys.stdlib_module_names:
            if root in IO_STDLIB:
                bad.add(root)
        elif root not in ALLOWED_THIRD_PARTY:
            bad.add(root)
    return bad


def _files(layer: str) -> list[Path]:
    return sorted((SRC / layer).rglob("*.py"))


@pytest.mark.parametrize("layer", sorted(INNER))
def test_inner_layers_import_no_adapter_host_or_io(layer: str) -> None:
    assert _files(layer), f"no files in {layer}"
    offenders = {}
    for file in _files(layer):
        key = file.relative_to(SRC).as_posix()
        bad = _violations(file, layer) - HARNESS_EXEMPTIONS.get(key, frozenset())
        if bad:
            offenders[key] = sorted(bad)
    assert offenders == {}


def test_harness_exemptions_only_shrink() -> None:
    """Each exemption is still used; one that is not must be deleted from the list."""
    for key, exempt in HARNESS_EXEMPTIONS.items():
        layer = key.split("/")[0]
        assert exempt <= _violations(SRC / key, layer), key


def test_the_scan_catches_what_it_claims(tmp_path: Path) -> None:
    """The instrument itself: a domain file importing each forbidden thing is reported."""
    file = tmp_path / "bad.py"
    file.write_text(
        "from typing import TYPE_CHECKING\n"
        "import json, hashlib\n"
        "import yaml\n"
        "import click\n"
        "from rich.console import Console\n"
        "from pathlib import Path\n"
        "import subprocess\n"
        "from ix.adapters._out.genre_files import GenreFiles\n"
        "from ix.composition import compose_experiment\n"
        "from ix.domain.schema import Record\n"
        "if TYPE_CHECKING:\n"
        "    from matrix import Agent\n"
    )
    assert _violations(file, "domain") == {
        "yaml",
        "click",
        "rich",
        "pathlib",
        "subprocess",
        "ix.adapters._out.genre_files",
        "ix.composition",
        "matrix",
    }


def test_the_command_line_calls_only_the_declared_api() -> None:
    """``ix.adapters._in.commands`` imports ``ix`` itself and no module inside it."""
    imported = {
        module
        for module in _imports(SRC / "adapters" / "_in" / "commands.py")
        if module.split(".")[0] == "ix"
    }
    assert imported == {"ix"}


def test_the_command_line_uses_only_declared_names() -> None:
    """Every ``ix.<name>`` the commands touch is in ``ix.__all__``."""
    tree = ast.parse((SRC / "adapters" / "_in" / "commands.py").read_text(encoding="utf-8"))
    used = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ix"
    }
    assert used, "the commands call nothing on ix"
    assert used <= set(ix.__all__)


def test_all_names_exist() -> None:
    assert sorted(ix.__all__) == sorted(set(ix.__all__))
    for name in ix.__all__:
        assert hasattr(ix, name), name


def test_no_declared_name_is_shadowed_by_a_subpackage() -> None:
    """Importing ``ix.<sub>`` binds ``<sub>`` on ``ix``; a function of the same name would
    be replaced by the module the first time anything imported it."""
    subpackages = {path.stem for path in SRC.iterdir() if path.is_dir() or path.suffix == ".py"}
    assert not (set(ix.__all__) & subpackages)
    ix.catalogue()  # reads the ix.genres package
    ix.skill()
    for name in ix.__all__:
        if name.islower() and not name.startswith("__"):
            assert callable(getattr(ix, name)), name


def test_importing_ix_loads_no_host() -> None:
    """The experiment model needs no host: ``import ix`` does not import matrix."""
    code = (
        "import sys, ix\n"
        "ix.validate\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] in "
        "{'matrix', 'hardline', 'claude_agent_sdk', 'inspect_ai'})\n"
        "print(loaded)\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_the_domain_names_no_genre() -> None:
    """A new genre is a data file and no edit to the core: no domain module holds a
    built-in genre's name in a string it acts on (docstrings aside)."""
    names = {file.stem for file in (SRC / "genres").glob("*.yaml")}
    assert names >= {"evaluate", "compare", "characterise", "regress"}
    harness = {"models.py", "types.py", "type_urls.py"}  # the earlier harness's own modules
    for file in _files("domain"):
        if file.name in harness:
            continue
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
                words = set(node.value.replace("-", " ").replace("_", " ").split())
                assert not (words & names), f"{file.name}: {node.value!r}"

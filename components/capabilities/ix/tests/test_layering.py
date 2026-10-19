"""The layers, checked by scanning imports.

``domain`` holds the model and its pure logic; ``application`` orchestrates it over data.
Each may import only what is on its allowlist: a named set of pure standard-library
modules, pydantic, and the ix layers at or inside it. Everything else fails, including a
module nobody thought to forbid, and a call to the builtin ``open``.

Outside the inner layers: only ``ix.composition`` wires adapters; outbound adapters know
neither the use cases nor the command line; the command line reaches ix only through its
declared API; importing ``ix`` loads no host; and matrix, which runs an experiment, is named
only by ``ix.composition`` and by the adapters ``ix/adapters/*/matrix_*.py``.

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
        "contextlib",
        "dataclasses",
        "hashlib",
        "itertools",
        "json",
        "math",
        "re",
        "statistics",
        "typing",
    }
)

#: What a layer may import beyond that. The application carries a trial out over ports, so
#: it waits, times out and races a stop flag: that is ``asyncio``, and the domain has none.
LAYER_STDLIB = {"domain": frozenset(), "application": frozenset({"asyncio"})}

#: Third-party packages an inner layer may import: pydantic, and its core, whose
#: ``PydanticCustomError`` is how a validator names the shape of what it refused.
THIRD_PARTY = frozenset({"pydantic", "pydantic_core"})

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
        elif root not in PURE_STDLIB | THIRD_PARTY | LAYER_STDLIB[layer]:
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
        "import subprocess, datetime, time, random, uuid, zipfile, os, asyncio\n"
        "from ix.adapters._out.matrix_documents import GenreFiles\n"
        "from ix.composition import root\n"
        "from ix.application.experiments import examine\n"
        "from ix.domain.schema import Record\n"
        "if TYPE_CHECKING:\n"
        "    from matrix import Agent\n"
        "text = open('x').read()\n"
    )
    assert _violations(file, "domain") == {
        "yaml", "click", "rich", "pathlib", "subprocess", "datetime", "time", "random",
        "uuid", "zipfile", "os", "asyncio", "ix.adapters._out.matrix_documents",
        "ix.composition", "ix.application.experiments", "matrix", "open()",
    }  # fmt: skip
    in_application = _violations(file, "application")
    assert "ix.application.experiments" not in in_application
    assert "asyncio" not in in_application and "matrix" in in_application


def test_only_composition_wires_outbound_adapters() -> None:
    """Outside ``ix.adapters._out`` itself, ``ix.composition`` is the only importer of an
    outbound adapter. (One adapter may build on another: a subject on the launcher.)"""
    importers = {
        file.relative_to(SRC).as_posix()
        for file in _files()
        if any(_within(module, ("ix.adapters._out",)) for module in _imports(file))
    }
    outside = {name for name in importers if not name.startswith("adapters/_out/")}
    assert outside == {"composition/__init__.py", "composition/extension.py"}


def test_outbound_adapters_know_neither_use_cases_nor_the_command_line() -> None:
    """An outbound adapter reaches the domain and its fellow adapters, nothing else in ix:
    not the use cases, not the composition root, not the command line."""
    for file in _files("adapters", "_out"):
        reached = {m for m in _imports(file) if m.split(".")[0] == "ix"}
        assert all(_within(m, ("ix.domain", "ix.adapters._out")) for m in reached), (
            file.name,
            reached,
        )


def test_the_facade_imports_no_adapter() -> None:
    """``ix/__init__.py`` and ``ix/api.py`` name the composition root and the domain, never
    an adapter: which adapters are bound is not the facade's decision."""
    for name in ("__init__.py", "api.py"):
        reached = {m for m in _imports(SRC / name) if m.split(".")[0] == "ix"}
        assert not any(_within(m, ("ix.adapters", "ix.application")) for m in reached), name


def _names_matrix(file: Path) -> bool:
    return "matrix" in {module.split(".")[0] for module in _imports(file)}


def test_only_composition_and_the_matrix_adapters_name_matrix() -> None:
    """matrix runs an experiment, and ix's whole assumption about it elsewhere is the
    three-member ``Scope``. Only ``ix.composition`` and ``ix/adapters/*/matrix_*.py`` may
    import it: the components matrix drives ix through, and the adapters ix drives matrix
    through. Every other module, the domain and the use cases first, is held to none."""
    importers = {file.relative_to(SRC).as_posix() for file in _files() if _names_matrix(file)}
    allowed = {
        file.relative_to(SRC).as_posix()
        for file in _files()
        if file.parent == SRC / "composition"
        or (file.parent.parent == SRC / "adapters" and file.name.startswith("matrix_"))
    }
    assert importers <= allowed, importers - allowed
    # The rule is about something: the adapters that speak to matrix exist and do.
    assert {
        "adapters/_in/matrix_components.py",
        "adapters/_out/matrix_process.py",
        "adapters/_out/matrix_runs.py",
    } <= importers
    for layer in ("domain", "application"):
        assert not any(_names_matrix(file) for file in _files(layer)), layer


def _registers(file: Path) -> bool:
    """Whether ``file`` calls a registry's API to declare or register something."""
    for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            method, receiver = node.func.attr, node.func.value
            if method in {"register_payload", "add_point"} or (
                method in {"register", "create", "entries"}
                and isinstance(receiver, ast.Name | ast.Attribute)
                and (receiver.id if isinstance(receiver, ast.Name) else receiver.attr).lstrip("_")
                == "registry"
            ):
                return True
    return False


def test_only_composition_calls_the_registry() -> None:
    """The registry will move; ix calls it from ``ix.composition`` alone, so the move is one
    change in one place. An adapter hands over what it builds and never registers it."""
    calling = {file.relative_to(SRC).as_posix() for file in _files() if _registers(file)}
    assert calling and all(path.startswith("composition/") for path in calling), calling


def test_the_scan_for_matrix_catches_a_type_only_import(tmp_path: Path) -> None:
    file = tmp_path / "leak.py"
    file.write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from matrix.domain import x\n"
    )
    assert _names_matrix(file)
    file.write_text("from ix.domain.ports import Scope\n")
    assert not _names_matrix(file)


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
        author or an extension writes, an error, or a published table. A record's
        parts are reachable as field types and are not exported."""
        # Changed requirement: the command configures telemetry through the API, since it
        # names nothing but ``ix``.
        functions = {"load", "validate", "resolve", "plan", "register", "deviations", "run",
                     "report", "runs", "genres", "kinds", "skill", "error_document",
                     "configure_telemetry"}  # fmt: skip
        documents = {"Validation", "Problem", "Design", "Plan", "Registered", "Deviations",
                     "Report", "Runs", "Genres", "Kinds"}  # fmt: skip
        authored = {"Experiment", "Genre", "Kind"}
        errors = {"IxError", "ConfigError", "NotFoundError", "InvalidExperimentError",
                  "RegistrationConflictError", "UnregisteredError", "UsageError"}  # fmt: skip
        # Changed requirement: the exit table's "what to do next" column (EXIT_NEXT) is
        # published beside its codes and meanings, so help and the skill read one table.
        tables = {"PROBLEM_KINDS", "ERROR_KINDS", "EXIT_CODES", "EXIT_MEANINGS", "EXIT_NEXT",
                  "__version__"}  # fmt: skip
        assert set(ix.__all__) == functions | documents | authored | errors | tables
        assert len(ix.__all__) <= 40

    def test_no_declared_name_is_shadowed_by_a_subpackage(self) -> None:
        """Importing ``ix.<sub>`` binds ``<sub>`` on ``ix``; a function of the same name
        would be replaced by the module the first time anything imported it."""
        modules = {path.stem for path in SRC.iterdir() if path.is_dir() or path.suffix == ".py"}
        assert not (set(ix.__all__) & modules)
        ix.skill()  # reads the genre and kind packages
        for name in ("genres", "kinds", "plan", "validate", "register", "deviations", "run",
                     "report", "runs"):  # fmt: skip
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
    names = (genres | kinds) - {"none", "process", "prompt", "usage", "memory", "timing",
                                "outcome"}  # fmt: skip
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


def test_every_type_reachable_from_a_public_result_is_exported_from_ix_domain() -> None:
    """Audit N6: a caller holding a report reaches ``analysis.findings[].decisions[]
    .interval``; each such type has one public name, in ``ix.domain``. The check walks the
    fields of every document ``ix`` exports and of every port."""
    import typing

    import pydantic

    import ix.domain

    public = set(ix.domain.__all__)
    seen: set[type] = set()

    def walk(annotation: object) -> None:
        for argument in typing.get_args(annotation):
            walk(argument)
        if isinstance(annotation, type) and issubclass(annotation, pydantic.BaseModel):
            if annotation in seen or annotation.__module__.split(".")[0] != "ix":
                return
            seen.add(annotation)
            for field in annotation.model_fields.values():
                walk(field.annotation)

    for name in ("Report", "Plan", "Registered", "Deviations", "Validation", "Experiment",
                 "Genre", "Kind", "Design", "Runs"):  # fmt: skip
        walk(getattr(ix, name))
    reachable = {model.__name__ for model in seen} - set(ix.__all__)
    assert {"Interval", "Decision", "SubjectConfig", "Sensitivity"} <= reachable  # the walk works
    assert reachable <= public, sorted(reachable - public)
    for name in ix.domain.__all__:
        assert getattr(ix.domain, name) is not None

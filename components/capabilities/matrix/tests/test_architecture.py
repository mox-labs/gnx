"""Load-bearing constraints, checked mechanically so they cannot erode in review.

1. **The layers** (``layers.json``, read by the workspace's shared checker as slick
   crafting-aces' ``layer-check.py`` reads it): every file belongs to a layer, and each file
   imports only what its layer allows, relative imports resolved. The core reaches pydantic,
   a named set of the standard library and itself, and never the filesystem, a process, a
   socket or a builtin ``open``; a port reaches domain types and no other port; an adapter
   reaches the domain and no other adapter; the Claude Agent SDK, hardline, YAML and
   rich-click are each named by one file, and OpenTelemetry by the otel observer and the
   root's telemetry module alone. Each rule has a planted violation below, so a rule that
   stopped biting fails here.
2. **Type URLs** are dot-separated, and their grammar has one owner, ``matrix.domain.ids``:
   no source text holds a type URL in the retired slash form (``<namespace>.v<N>/``, any
   version), and nothing outside ``ids`` splits a string on ``/``.
3. **The command line** reaches matrix through ``import matrix`` and the names in
   ``matrix.__all__`` alone, so what it can do, a program can do with the declared names.
4. The API reference and the README's stable names agree with ``matrix.__all__``.

What these cannot see, so review still reads for it (the design record keeps the list): a
module reached by ``importlib`` or through an alias, a use that is not an import, an adapter's
type handed out under another name, logic in a file whose imports are all allowed, a type URL
built by joining strings, and what a dependency does at run time.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

try:
    from _testing.layers import Plant, check, copy_tree, load, plant, plants, reaches_outside
except ImportError:  # a package run apart from its workspace has no shared checker
    pytest.skip(
        "not run from the workspace: no _testing beside the packages", allow_module_level=True
    )

import matrix

PACKAGE = Path(__file__).resolve().parents[1]
SRC = PACKAGE / "src" / "matrix"
MAP = load(PACKAGE)

#: A type URL in the retired slash form: ``matrix.v1/agent``, ``acme.v2beta1/review``.
SLASH_TYPE_URL = re.compile(
    r"[a-z0-9]+(?:-[a-z0-9]+)*\.v[1-9][0-9]*(?:(?:alpha|beta)[1-9][0-9]*)?/"
)
SPLITS = frozenset({"split", "rsplit", "partition", "rpartition"})


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return copy_tree(PACKAGE, tmp_path)


# --- 1. the layers ------------------------------------------------------------------------


def test_every_file_is_in_a_layer_and_every_dependency_points_inward() -> None:
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
        # The review's planted violation 6, which the old import scan read past: it kept only
        # absolute imports.
        ("domain/ref.py", "from ..adapters._out.stores import jsonl",
         "layer domain may not reach matrix.adapters._out.stores"),
        ("domain/ports/_out/agent_runtime.py", "from .model_runtime import ModelRuntime",
         "layer ports may not reach matrix.domain.ports._out.model_runtime"),
        ("adapters/_out/observers/jsonl.py", "from ..stores import jsonl",
         "layer adapters_out may not reach matrix.adapters._out.stores"),
        ("adapters/_out/runtime/mock.py", "from ....composition import builtins",
         "layer adapters_out may not reach matrix.composition"),
    ],
)  # fmt: skip
def test_a_relative_import_is_judged_like_any_other(
    tree: Path, rel: str, line: str, finding: str
) -> None:
    plant(tree, Plant("relative", f"src/matrix/{rel}", line, finding))
    assert any(finding in f for f in check(MAP, tree).findings), finding


def test_a_file_no_layer_claims_leaves_the_tree_unjudged(tree: Path) -> None:
    (tree / "src" / "matrix" / "adapters" / "_out" / "spend.py").write_text("x = 1\n")
    report = check(MAP, tree)
    assert report.errors == ["src/matrix/adapters/_out/spend.py is scanned but no layer claims it"]
    assert report.exit_code == 2


def test_a_layer_whose_file_moved_leaves_the_tree_unjudged(tree: Path) -> None:
    (tree / "src" / "matrix" / "process.py").rename(tree / "src" / "matrix" / "processes.py")
    report = check(MAP, tree)
    assert "layer process: glob 'src/matrix/process.py' matches no file" in report.errors
    assert report.exit_code == 2


# --- 2. type URLs -------------------------------------------------------------------------


def _type_url_surgery(src: Path) -> list[str]:
    """Each slash-form type URL in the source text, and each split on ``/`` outside ids."""
    found: list[str] = []
    for path in sorted(src.rglob("*.py")):
        rel = path.relative_to(src).as_posix()
        text = path.read_text(encoding="utf-8")
        found.extend(f"{rel}: {m.group(0)!r}" for m in SLASH_TYPE_URL.finditer(text))
        if rel == "domain/ids.py":
            continue
        for node in ast.walk(ast.parse(text)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in SPLITS
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "/"
            ):
                found.append(f"{rel}:{node.lineno}: {node.func.attr}('/')")
    return found


def test_no_type_url_is_written_or_split_by_slash() -> None:
    assert _type_url_surgery(SRC) == []


@pytest.mark.parametrize(
    ("rel", "line"),
    [
        # The old check searched for the literal ".v1/" only (mx-tests 10).
        ("domain/ids.py", 'LEGACY = "matrix.v2/agent-step"'),
        ("adapters/_in/skill.py", '# see acme.v1beta2/review'),
        ("composition/builtins.py", 'namespace, _, rest = "a/b".partition("/")'),
        ("adapters/_out/runtime/mock.py", 'PARTS = "a/b".rsplit("/", 1)'),
    ],
)  # fmt: skip
def test_type_url_surgery_is_found(tree: Path, rel: str, line: str) -> None:
    plant(tree, Plant("type-url", f"src/matrix/{rel}", line, ""))
    assert _type_url_surgery(tree / "src" / "matrix") != []


# --- 3. the command line ------------------------------------------------------------------


def test_the_command_line_calls_only_the_declared_api() -> None:
    cli = SRC / "adapters" / "_in" / "cli.py"
    assert reaches_outside([cli], "matrix", matrix.__all__) == []
    used = {
        node.attr
        for node in ast.walk(ast.parse(cli.read_text(encoding="utf-8")))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "matrix"
    }
    assert used, "the commands call nothing on matrix"


@pytest.mark.parametrize(
    "line",
    [
        "from . import skill",  # the old scan kept absolute imports only
        "from matrix.composition import container",
        "import matrix.composition.builtins",
        "from matrix import _private",
        "matrix.composition",
        "import matrix as m",
    ],
)
def test_the_command_line_reaching_past_matrix_all_is_found(tree: Path, line: str) -> None:
    cli = tree / "src" / "matrix" / "adapters" / "_in" / "cli.py"
    plant(tree, Plant("cli", "src/matrix/adapters/_in/cli.py", line, ""))
    assert reaches_outside([cli], "matrix", matrix.__all__) != []


# --- 4. the documented surface ------------------------------------------------------------


def test_the_api_reference_lists_every_exported_name_and_no_other() -> None:
    text = (PACKAGE / "docs" / "reference" / "api.md").read_text()
    block = text[text.index("from matrix import (") : text.index(")\n```")]
    listed = set(re.findall(r"^\s+([A-Za-z_]+),", block, re.M))
    assert listed == set(matrix.__all__), listed ^ set(matrix.__all__)


def test_the_names_the_readme_calls_stable_are_exported() -> None:
    text = (PACKAGE / "README.md").read_text()
    section = text[text.index("## The stable public API") : text.index("## Documentation")]
    stable = section[: section.index("Every other exported name")]
    named = set(re.findall(r"`([A-Za-z_]+)`", stable)) - {"matrix", "v1"}
    assert named and named <= set(matrix.__all__), named - set(matrix.__all__)

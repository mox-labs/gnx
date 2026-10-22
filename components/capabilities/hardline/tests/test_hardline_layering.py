"""hardline's layers, checked on every test run: ``layers.json`` against ``src/hardline``.

The map is read by the workspace's shared checker (``components/capabilities/_testing``),
which reads it as slick crafting-aces' ``layer-check.py`` does for Python, so the same file
runs under that script too: every import counts, one under ``TYPE_CHECKING`` or inside a
function included, a relative import is resolved against its module, and ``*`` admits any
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

from pathlib import Path

import pytest

try:
    from _testing.layers import Plant, check, copy_tree, load, plant, plants, reaches_outside
except ImportError:  # a package run apart from its workspace has no shared checker
    pytest.skip(
        "not run from the workspace: no _testing beside the packages", allow_module_level=True
    )

import hardline

PACKAGE = Path(__file__).resolve().parents[1]
MAP = load(PACKAGE)


def _cli_reaches_outside_all(root: Path) -> list[str]:
    """Each place a driving file reaches hardline other than by a name in ``__all__``."""
    driving = next(layer for layer in MAP["layers"] if layer["name"] == "driving")
    files = sorted(p for pattern in driving["files"] for p in root.glob(pattern))
    return reaches_outside(files, "hardline", hardline.__all__)


# --- the tree as it is ------------------------------------------------------------------------


def test_every_file_is_in_a_layer_and_every_dependency_points_inward() -> None:
    report = check(MAP, PACKAGE)
    assert report.errors == []
    assert report.findings == []
    assert report.exit_code == 0
    assert report.files_in_scan == len(list((PACKAGE / "src" / "hardline").rglob("*.py")))
    assert all(count > 0 for count in report.layers.values()), report.layers


def test_the_cli_reaches_hardline_through_its_public_names_alone() -> None:
    # F14: the CLI imported load_registry from hardline.composition, outside hardline.__all__.
    assert _cli_reaches_outside_all(PACKAGE) == []


# --- each rule bites: a violation planted in a copy of the tree -------------------------------


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return copy_tree(PACKAGE, tmp_path)


def _plant(tree: Path, rel: str, line: str) -> None:
    plant(tree, Plant(rel, f"src/hardline/{rel}", line, ""))


@pytest.mark.parametrize("violation", [pytest.param(p, id=p.rule) for p in plants(MAP, PACKAGE)])
def test_each_rule_of_the_map_bites(tree: Path, violation: Plant) -> None:
    plant(tree, violation)
    assert any(violation.finding in f for f in check(MAP, tree).findings), violation.finding


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
    report = check(MAP, tree)
    assert report.errors == ["src/hardline/telemetry.py is scanned but no layer claims it"]
    assert report.exit_code == 2


def test_a_layer_whose_glob_matches_nothing_is_an_error(tree: Path) -> None:
    (tree / "src" / "hardline" / "domain" / "digests.py").unlink()
    report = check(MAP, tree)
    assert any("matches no file" in e for e in report.errors)
    assert report.exit_code == 2


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

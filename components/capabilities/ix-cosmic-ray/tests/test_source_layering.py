"""The package's layers, checked on every test run: ``layers.json`` against ``src/ix_cosmic_ray``.

The map is read by the workspace's shared checker (``components/capabilities/_testing``), as
the other packages' are. The generator is the one module that imports cosmic-ray, the
registration imports none of it, and the package reaches ix only through ``ix.extension``.
Each rule has a planted violation, so a check that stopped biting fails here.
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

import ix.extension

PACKAGE = Path(__file__).resolve().parents[1]
SRC = PACKAGE / "src" / "ix_cosmic_ray"
MAP = load(PACKAGE)


def test_every_file_is_in_a_layer_and_every_dependency_points_inward() -> None:
    report = check(MAP, PACKAGE)
    assert (report.errors, report.findings, report.exit_code) == ([], [], 0)
    assert report.files_in_scan == len(list(SRC.rglob("*.py")))
    assert all(count > 0 for count in report.layers.values()), report.layers


def test_it_reaches_ix_through_the_extension_contract_alone() -> None:
    files = sorted(SRC.rglob("*.py"))
    assert reaches_outside(files, "ix.extension", ix.extension.__all__) == []


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return copy_tree(PACKAGE, tmp_path)


@pytest.mark.parametrize("violation", [pytest.param(p, id=p.rule) for p in plants(MAP, PACKAGE)])
def test_each_rule_of_the_map_bites(tree: Path, violation: Plant) -> None:
    plant(tree, violation)
    assert any(violation.finding in f for f in check(MAP, tree).findings), violation.finding

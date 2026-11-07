"""The bridge's layers, checked on every test run: ``layers.json`` against ``src/matrix_hardline``.

The map is read by the workspace's shared checker (``components/capabilities/_testing``), as
matrix's and hardline's are. The adapter is the one module that imports hardline, and it
reaches each package it bridges only through that package's ``__all__``, so a change inside
either reaches this package only through what each publishes. Each rule has a planted
violation, so a check that stopped biting fails here.
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
import matrix

PACKAGE = Path(__file__).resolve().parents[1]
SRC = PACKAGE / "src" / "matrix_hardline"
MAP = load(PACKAGE)


def test_every_file_is_in_a_layer_and_every_dependency_points_inward() -> None:
    report = check(MAP, PACKAGE)
    assert (report.errors, report.findings, report.exit_code) == ([], [], 0)
    assert report.files_in_scan == len(list(SRC.rglob("*.py")))
    assert all(count > 0 for count in report.layers.values()), report.layers


def test_the_adapter_reaches_matrix_and_hardline_through_their_public_names_alone() -> None:
    adapter = [SRC / "runtime.py"]
    assert reaches_outside(adapter, "matrix", matrix.__all__) == []
    assert reaches_outside(adapter, "hardline", hardline.__all__) == []


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return copy_tree(PACKAGE, tmp_path)


@pytest.mark.parametrize("violation", [pytest.param(p, id=p.rule) for p in plants(MAP, PACKAGE)])
def test_each_rule_of_the_map_bites(tree: Path, violation: Plant) -> None:
    plant(tree, violation)
    assert any(violation.finding in f for f in check(MAP, tree).findings), violation.finding

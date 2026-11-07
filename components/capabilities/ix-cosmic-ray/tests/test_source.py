"""ix-cosmic-ray: cosmic-ray's operators as an ix mutant source, held to ix's port and to what
cosmic-ray itself makes.

Each test guards one promise of CONTRACT.md. The mutants are cosmic-ray's own, made from real
text; the end-to-end test samples a small suite through ix, whose trials run it under pytest.
No model is called, and every process a trial starts is ended by ix's launcher.
"""

from __future__ import annotations

import json
import subprocess
import sys
from importlib.metadata import entry_points, version
from typing import TYPE_CHECKING, Any

import ix
import matrix
import yaml
from cosmic_ray.mutating import mutate_code
from cosmic_ray.plugins import get_operator
from ix.composition import Root
from ix.composition.settings import IxSettings
from ix.extension import MUTANT_SOURCE, MutantSource

import ix_cosmic_ray
from ix_cosmic_ray import TYPE_URL, build, register

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

#: Text with what the operators meet: comparisons, booleans, numbers, a binary operator, a
#: decorator, a loop, a handler, and characters outside ASCII before a mutated column.
TEXT = """\
import functools


@functools.cache
def grade(score: int, names: list[str]) -> str:
    label = "é" if score >= 90 and names else "ü"
    for name in names:
        if name == "x" or not name:
            return label + name
    try:
        return str(score * 2 - 1)
    except ValueError:
        return label
"""


def _apply(text: str, start: tuple[int, int], end: tuple[int, int], replacement: str) -> str:
    lines = text.splitlines(keepends=True)

    def offset(position: tuple[int, int]) -> int:
        return sum(len(line) for line in lines[: position[0] - 1]) + position[1]

    return text[: offset(start)] + replacement + text[offset(end) :]


def test_it_keeps_ixs_port() -> None:
    assert isinstance(build(), MutantSource)


def test_each_mutant_applied_to_its_span_is_the_text_cosmic_ray_made() -> None:
    """The span contract: ``replacement`` put from ``start`` to ``end`` (columns in
    characters) gives exactly what ``mutate_code`` makes for that operator and occurrence,
    where an operator rewrites more than its node (a decorator removed) as well."""
    made = build().mutations(TEXT).made
    assert {"core/RemoveDecorator", "core/NumberReplacer", "core/ReplaceAndWithOr"} <= {
        mutation.operator for mutation in made
    }
    for mutation in made:
        operator, occurrence = mutation.key.rsplit(":", 1)
        expected = mutate_code(TEXT, get_operator(operator)(), int(occurrence))
        applied = _apply(TEXT, mutation.start, mutation.end, mutation.replacement)
        assert applied == expected, mutation


def test_a_key_is_the_operator_and_its_occurrence_over_the_whole_file_and_is_stable() -> None:
    first, again = build().mutations(TEXT), build().mutations(TEXT)
    assert first == again
    keys = [mutation.key for mutation in first.made]
    assert len(keys) == len(set(keys))
    assert all(
        key == f"{m.operator}:{key.rsplit(':', 1)[1]}"
        for key, m in zip(keys, first.made, strict=True)
    )
    numbers = [m for m in first.made if m.operator == "core/NumberReplacer"]
    assert [m.key for m in numbers] == [f"core/NumberReplacer:{n}" for n in range(len(numbers))]


def test_an_operator_that_takes_arguments_is_not_asked() -> None:
    operators = {mutation.operator for mutation in build().mutations(TEXT).made}
    assert not operators & {"core/VariableReplacer", "core/VariableInserter"}


def test_an_operator_that_raises_is_counted_with_its_reason_and_the_rest_are_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ix_cosmic_ray.generator as generator

    numbers = [m for m in build().mutations(TEXT).made if m.operator == "core/NumberReplacer"]

    def raising(text: str, operator: Any, occurrence: int) -> str | None:
        if type(operator).__name__ == "NumberReplacer":
            raise ValueError("parso said no")
        made: str | None = mutate_code(text, operator, occurrence)
        return made

    monkeypatch.setattr(generator, "mutate_code", raising)
    found = build().mutations(TEXT)
    (unmade,) = [item for item in found.unmade if item.operator == "core/NumberReplacer"]
    assert unmade.count == len(numbers) > 0 and unmade.reason == "ValueError: parso said no"
    assert found.made and not any(m.operator == "core/NumberReplacer" for m in found.made)


def test_loading_the_registration_imports_no_cosmic_ray() -> None:
    """A session that only asks which sources are installed (``ix plan``) pays nothing for
    cosmic-ray and its dependencies; building the source imports it."""
    code = (
        "import sys, matrix, ix_cosmic_ray\n"
        "from ix.composition.entry_point import register_run_path\n"
        "registry = matrix.default_registry(discover=False)\n"
        "register_run_path(registry)\n"
        "ix_cosmic_ray.register(registry)\n"
        "print('cosmic_ray' in sys.modules)\n"
        "ix_cosmic_ray.build()\n"
        "print('cosmic_ray' in sys.modules)\n"
    )
    found = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True
    )
    assert found.stdout.split() == ["False", "True"]


def _suite(tmp_path: Path) -> Path:
    tree = tmp_path / "tree"
    (tree / "src" / "calc").mkdir(parents=True)
    (tree / "tests").mkdir()
    (tree / "src" / "calc" / "__init__.py").write_text("")
    (tree / "src" / "calc" / "core.py").write_text(
        "def small(x: int) -> bool:\n    return x < 3\n\n\ndef twice(x: int) -> int:\n"
        "    return x * 2\n"
    )
    (tree / "tests" / "test_core.py").write_text(
        "from calc.core import small, twice\n\n\ndef test_small():\n    assert small(2)\n"
        "    assert not small(3)\n\n\ndef test_twice():\n    assert twice(3) == 6\n"
    )
    experiment = tmp_path / "experiment"
    experiment.mkdir()
    data = {
        "name": "calc-mutation",
        "question": "Do calc's tests fail when core.py is wrong?",
        "genre": "mutation-score",
        "subjects": {
            "suite": {
                "kind": "test-suite",
                "root": "../tree",
                "command": [
                    sys.executable,
                    "-m",
                    "pytest",
                    "tests",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                ],
                "tests": ["../tree/tests"],
                "scope": ["../tree/src/calc/core.py"],
                "map": "mutation/suite.map.json",
            }
        },
        "primary": {"metric": "killed"},
    }
    (experiment / "experiment.yaml").write_text(yaml.safe_dump(data))
    return experiment


def test_ix_samples_with_it_and_runs_what_it_made(tmp_path: Path) -> None:
    """Installed, it is the source ``ix mutants sample`` finds at its point: the population is
    cosmic-ray's, recorded with cosmic-ray's version, and a run of it kills what the tests
    assert."""
    registry = matrix.default_registry(
        points={*matrix.MATRIX_POINTS, *ix.extension.POINTS.values(), MUTANT_SOURCE}
    )
    assert (MUTANT_SOURCE, TYPE_URL) in registry
    settings = matrix.Config[IxSettings](client=IxSettings(), matrix=matrix.MatrixConfig())
    root = Root(registry=registry, settings=settings)
    experiment = _suite(tmp_path)
    found = root.mutants_sample(experiment, mutant_source=TYPE_URL)
    assert found["source"] == {
        "type_url": TYPE_URL,
        "drives": {"cosmic-ray": version("cosmic-ray")},
    }
    assert found["census"] and found["population"] == found["sampled"]
    assert "core/ReplaceComparisonOperator_Lt_LtE" in found["by_operator"]
    root.run(experiment, exploratory=True)
    read = root.mutants_read(experiment)["subjects"][0]
    assert read["read"] == found["sampled"] and read["killed"] >= read["read"] // 2, read
    record = json.loads((experiment / "mutation" / "population.json").read_text())
    assert all(":core/" in mutant for mutant in record["sampled"])


def test_the_published_surface_is_pinned() -> None:
    """G6: its names, its type URL and the one point its entry points declare."""
    assert set(ix_cosmic_ray.__all__) == {"TYPE_URL", "__version__", "build", "register"}
    assert TYPE_URL == "ix-cosmic-ray.v1.mutant-source"
    declared = {
        point.name: point.value
        for point in entry_points(group="matrix.extensions")
        if point.dist is not None and point.dist.name == "ix-cosmic-ray"
    }
    assert declared == {"ix.mutant-source": "ix_cosmic_ray.source:register"}
    registry = matrix.default_registry(discover=False)
    from ix.composition.entry_point import register_run_path

    register_run_path(registry)
    register(registry)
    entry = registry.entry(MUTANT_SOURCE, TYPE_URL)
    assert entry.drives == ("cosmic-ray",)

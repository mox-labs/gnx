"""Shared test fixtures for ix."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from ix.adapters._out.genre_files import GenreFiles
from ix.domain.genre import Catalogue, catalogue

if TYPE_CHECKING:
    from pathlib import Path

_COMPARE: dict[str, Any] = {
    "name": "proxy",
    "question": "Is streaming no worse than buffered?",
    "genre": "compare",
    "subjects": {
        "buffered": {"kind": "process", "command": ["./proxy", "--buffered"]},
        "streaming": {"kind": "process", "command": ["./proxy", "--streaming"]},
    },
    "probes": [{"id": "download", "kind": "http-load", "target": "http://127.0.0.1:8080/x"}],
    "metrics": [
        {"name": "ttfb", "unit": "ms", "direction": "lower", "shape": "histogram"},
        {"name": "throughput", "unit": "req/s", "direction": "higher", "shape": "scalar"},
        {"name": "quality", "direction": "higher", "shape": "scalar", "judged": True},
        {"name": "passed", "direction": "higher", "shape": "boolean"},
        {"name": "drift", "direction": "neither", "shape": "scalar"},
        {"name": "trace", "direction": "lower", "shape": "series"},
    ],
    "primary": {"metric": "ttfb", "statistic": "p99"},
    "rule": "non-inferiority",
    "margin": "5%",
}

_EVALUATE: dict[str, Any] = {
    "name": "routing",
    "question": "Does the right skill fire?",
    "genre": "evaluate",
    "subjects": {"catalog": {"kind": "agent", "max_turns": 1}},
    "probes": [{"id": "ask", "kind": "prompt", "prompt": "Review this change."}],
    "metrics": _COMPARE["metrics"],
    "primary": {"metric": "passed"},
    "bar": {"op": ">=", "value": 0.9},
}


@pytest.fixture(scope="session")
def builtin_genres() -> Catalogue:
    """ix's four genres, read from the package (no entry-point discovery)."""
    return catalogue(GenreFiles(packages=["ix.genres"]).records())


@pytest.fixture
def compare_data() -> dict[str, Any]:
    """A valid ``compare`` experiment as file data; a test changes what it is about."""
    return copy.deepcopy(_COMPARE)


@pytest.fixture
def evaluate_data() -> dict[str, Any]:
    """A valid ``evaluate`` experiment as file data."""
    return copy.deepcopy(_EVALUATE)


@pytest.fixture
def write_experiment(tmp_path: Path) -> Any:
    """Write experiment data to ``<tmp>/<name>/experiment.yaml`` and return the file."""

    def write(data: dict[str, Any], directory: str = "lab") -> Path:
        folder = tmp_path / directory
        folder.mkdir(parents=True, exist_ok=True)
        file = folder / "experiment.yaml"
        file.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return file

    return write

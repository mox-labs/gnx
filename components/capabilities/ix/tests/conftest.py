"""Shared test fixtures for ix."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from ix.adapters._out.matrix_documents import GenreFiles, KindFiles
from ix.application.experiments import Vocabulary
from ix.composition import type_url_problem
from ix.domain.genre import Genres, genres
from ix.domain.kind import Kinds, kinds

if TYPE_CHECKING:
    from pathlib import Path

_URL = "http://127.0.0.1:8080"

_COMPARE: dict[str, Any] = {
    "name": "proxy",
    "question": "Is streaming no worse than buffered?",
    "genre": "compare",
    "subjects": {
        "buffered": {"kind": "process", "command": ["./proxy", "--buffered"], "url": _URL},
        "streaming": {"kind": "process", "command": ["./proxy", "--streaming"], "url": _URL},
    },
    "probes": [
        {
            "id": "download",
            "kind": "http-load",
            "target": "{url}/x",
            "load_model": "open",
            "rate": "80/s",
            "duration": "60s",
        }
    ],
    "primary": {"metric": "ttfb", "statistic": "p99"},
    "comparison": "non-inferiority",
    "margin": "5%",
}

_EVALUATE: dict[str, Any] = {
    "name": "routing",
    "question": "Does the right skill fire?",
    "genre": "evaluate",
    # The mock runtime: a test that runs this by mistake calls no model.
    "subjects": {"catalog": {"kind": "agent-session", "runtime": "mock", "max_turns": 1}},
    "probes": [
        {
            "id": "ask",
            "kind": "prompt",
            "prompt": "Review this change.",
            "truth": {"expectation": "must_trigger", "expected_skill": "review"},
        }
    ],
    "primary": {"metric": "activated_correctly"},
    "threshold": {"op": ">=", "value": 0.9},
}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "slow: the full simulation of the statistics; IX_SLOW=1 pytest -m slow"
    )


@pytest.fixture(autouse=True)
def _no_tracer_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with no OpenTelemetry tracer provider installed, and leaves none
    behind: a run whose sensors read spans installs one in the process (W11.2b), and
    OpenTelemetry lets a process install one only once."""
    from opentelemetry import trace
    from opentelemetry.util._once import Once

    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER_SET_ONCE", Once())


@pytest.fixture(scope="session")
def builtin_genres() -> Genres:
    """ix's four genres, read from the package (no entry-point discovery)."""
    return genres(GenreFiles(packages=["ix.builtin.genres"]).files(), grammar=type_url_problem)


@pytest.fixture(scope="session")
def builtin_kinds() -> Kinds:
    """ix's kinds, read from the package (no entry-point discovery)."""
    return kinds(KindFiles(packages=["ix.builtin.kinds"]).files(), grammar=type_url_problem)


@pytest.fixture(scope="session")
def vocabulary(builtin_genres: Genres, builtin_kinds: Kinds) -> Vocabulary:
    return Vocabulary(genres=builtin_genres, kinds=builtin_kinds)


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
    """Write experiment data to ``<tmp>/<directory>/experiment.yaml`` and return the file."""

    def write(data: dict[str, Any], directory: str = "lab") -> Path:
        folder = tmp_path / directory
        folder.mkdir(parents=True, exist_ok=True)
        file = folder / "experiment.yaml"
        file.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return file

    return write

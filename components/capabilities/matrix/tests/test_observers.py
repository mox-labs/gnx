"""The observers that ship: a JSONL stream for an agent, progress on stderr for a person."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING, Any

import pytest
from rich.console import Console

from matrix import (
    Event,
    Executor,
    Inputs,
    MatrixConfig,
    RunContext,
    RunError,
    compose,
    default_registry,
    wire,
)
from matrix.adapters._out.observers.console import ConsoleObserver
from matrix.adapters._out.observers.jsonl import JsonlObserver

if TYPE_CHECKING:
    from pathlib import Path

OUT = "acme.v1.out"


class Step:
    """A component whose body is a coroutine function of its context; what the body returns
    is one row of the kind the step provides."""

    def __init__(self, body: Any, *, provides: str | None = None) -> None:
        self.requires: frozenset[str] = frozenset()
        self.provides = frozenset([provides] if provides else [])
        self._body = body

    async def run(self, inputs: Inputs, ctx: RunContext) -> Any:
        row = await self._body(ctx)
        return {kind: row for kind in self.provides}


async def _ok(ctx: Any) -> str:
    return "v"


async def test_the_jsonl_observer_writes_one_document_per_event(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "events.jsonl"
    observer = JsonlObserver(path)
    run = await Executor([observer]).run(wire({"a": Step(_ok, provides=OUT)}))
    observer.close()
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [d["name"] for d in lines] == [
        "matrix.run.start",
        "matrix.component.start",
        "matrix.row.append",
        "matrix.component.end",
        "matrix.run.end",
    ]
    assert {d["schema"] for d in lines} == {"matrix.v1.event"}
    assert {frozenset(d) for d in lines} == {
        frozenset({"schema", "name", "time", "run_id", "span", "parent", "component", "fields"})
    }
    assert {d["run_id"] for d in lines} == {run.run_id}
    assert lines[-1]["fields"]["status"] == "completed"


async def test_the_jsonl_observer_defaults_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    await Executor([JsonlObserver()]).run(wire({"a": Step(_ok)}))
    out, err = capsys.readouterr()
    assert out == ""
    assert json.loads(err.splitlines()[0])["name"] == "matrix.run.start"


@pytest.mark.parametrize("terminal", [False, True])
async def test_the_console_observer_says_how_each_component_ended(terminal: bool) -> None:
    async def boom(ctx: Any) -> str:
        raise RuntimeError("kaput")

    screen = io.StringIO()
    console = Console(file=screen, force_terminal=terminal, no_color=True, width=100)
    wiring = wire({"good": Step(_ok, provides=OUT), "bad": Step(boom)})
    with pytest.raises(RunError):
        await Executor([ConsoleObserver(console)]).run(wiring)
    text = screen.getvalue()
    assert "good completed" in text and "bad failed" in text
    assert "started: good, bad" in text and "failed in" in text
    if not terminal:
        assert "good started" in text  # no live display to show it; a line instead


def test_observers_are_composed_from_config_with_their_options(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    config = MatrixConfig.model_validate(
        {"observers": ["console", {"type": "jsonl", "path": str(path)}]}
    )
    box = compose(config, registry=default_registry(discover=False))
    console, jsonl = box.observers
    assert isinstance(console, ConsoleObserver) and isinstance(jsonl, JsonlObserver)
    jsonl.on_event(Event("acme.tick", span="s"))
    jsonl.close()
    assert json.loads(path.read_text())["name"] == "acme.tick"


def test_the_jsonl_observer_touches_no_file_until_an_event_arrives(tmp_path: Path) -> None:
    """``matrix check`` and ``matrix plan`` call nothing, yet composing the ``jsonl`` observer
    created its file and folders, and nothing ever closed it."""
    path = tmp_path / "nested" / "events.jsonl"
    config = MatrixConfig.model_validate({"observers": [{"type": "jsonl", "path": str(path)}]})
    compose(config, registry=default_registry(discover=False))
    assert not (tmp_path / "nested").exists()


async def test_the_jsonl_observer_closes_its_file_when_a_run_ends_and_opens_it_again(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    observer = JsonlObserver(path)
    executor = Executor([observer])
    await executor.run(wire({"a": Step(_ok, provides=OUT)}))
    assert observer.closed
    await executor.run(wire({"a": Step(_ok, provides=OUT)}))
    assert observer.closed
    names = [json.loads(line)["name"] for line in path.read_text().splitlines()]
    assert names.count("matrix.run.start") == 2 and names[-1] == "matrix.run.end"

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
    Flow,
    Inputs,
    MatrixConfig,
    Member,
    RunError,
    compile_flow,
    compose,
    default_registry,
)
from matrix.adapters._out.observers.console import ConsoleObserver
from matrix.adapters._out.observers.jsonl import JsonlObserver

if TYPE_CHECKING:
    from pathlib import Path

TEXT = "acme.v1.text"


class Step:
    """A component whose body is the coroutine function it is given."""

    requires: dict[str, Any] = {}
    provides = {"out": TEXT}

    def __init__(self, body: Any) -> None:
        self._body = body

    async def run(self, inputs: Inputs) -> dict[str, Any]:
        return {"out": await self._body(inputs.context)}


def flow_of(*members: Member) -> Any:
    return compile_flow(Flow("f", members))


async def _ok(ctx: Any) -> str:
    return "v"


async def test_the_jsonl_observer_writes_one_document_per_event(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "events.jsonl"
    observer = JsonlObserver(path)
    run = await Executor([observer]).run(flow_of(Member("a", Step(_ok), {"out": "t"})))
    observer.close()
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [d["name"] for d in lines] == [
        "matrix.run.start",
        "matrix.member.start",
        "matrix.member.end",
        "matrix.artifact.append",
        "matrix.run.end",
    ]
    assert {d["schema"] for d in lines} == {"matrix.v1.event"}
    assert {d["run_id"] for d in lines} == {run.run_id}
    assert lines[-1]["fields"]["status"] == "completed"


async def test_the_jsonl_observer_defaults_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    await Executor([JsonlObserver()]).run(flow_of(Member("a", Step(_ok))))
    out, err = capsys.readouterr()
    assert out == ""
    assert json.loads(err.splitlines()[0])["name"] == "matrix.run.start"


@pytest.mark.parametrize("terminal", [False, True])
async def test_the_console_observer_says_how_each_member_ended(terminal: bool) -> None:
    async def boom(ctx: Any) -> str:
        raise RuntimeError("kaput")

    screen = io.StringIO()
    console = Console(file=screen, force_terminal=terminal, no_color=True, width=100)
    compiled = flow_of(Member("good", Step(_ok), {"out": "t"}), Member("bad", Step(boom)))
    with pytest.raises(RunError):
        await Executor([ConsoleObserver(console)]).run(compiled)
    text = screen.getvalue()
    assert "good completed" in text and "bad failed" in text
    assert text.count("run ") == 2 and "failed in" in text
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
    jsonl.on_event(Event("acme.tick", "s"))
    jsonl.close()
    assert json.loads(path.read_text())["name"] == "acme.tick"

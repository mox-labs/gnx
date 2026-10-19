"""Progress on stderr, for a person watching a run.

One line per thing worth knowing: a run starting and ending, each component starting, being asked
to stop, and ending (with its state and how long it took), a release that did not go cleanly,
events that were dropped. On a terminal, the components still running are shown live underneath
with their elapsed time; anywhere else (a pipe, a log, ``TERM=dumb``) only the lines are
written. ``NO_COLOR`` is honoured.

Everything goes to stderr: stdout belongs to whatever the caller prints as its result.

Type URL: ``matrix.v1.observer.console``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markup import escape
from rich.progress import Progress, SpinnerColumn, TaskID, TextColumn, TimeElapsedColumn

from matrix.domain.observer import (
    COMPONENT_END,
    COMPONENT_START,
    COMPONENT_STOPPING,
    EVENTS_DROPPED,
    RELEASE_END,
    RUN_END,
    RUN_START,
)

if TYPE_CHECKING:
    from matrix.domain.observer import Event

_STYLES = {
    "completed": "green",
    "failed": "red",
    "cancelled": "yellow",
    "skipped": "dim",
}


class ConsoleObserver:
    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console(stderr=True, highlight=False)
        self._live = self._console.is_terminal and not self._console.is_dumb_terminal
        self._progress: Progress | None = None
        self._runs = 0
        self._rows: dict[str, TaskID] = {}

    def on_event(self, event: Event) -> None:
        fields = event.fields
        run = (event.run_id or "")[:8]
        component = escape(event.component or "")
        if event.name == RUN_START:
            self._runs += 1
            if self._live and self._progress is None:
                self._progress = Progress(
                    SpinnerColumn(),
                    TextColumn("{task.description}"),
                    TimeElapsedColumn(),
                    console=self._console,
                    transient=True,
                )
                self._progress.start()
            names = ", ".join(str(n) for n in fields.get("components") or ())
            self._say(f"[bold]run {run}[/bold] started: {escape(names)}")
        elif event.name == COMPONENT_START:
            if self._progress is not None:
                self._rows[event.span] = self._progress.add_task(component, total=None)
            else:
                self._say(f"  {component} started")
        elif event.name == COMPONENT_STOPPING:
            self._say(
                f"  {component} [yellow]stopping[/yellow] ({escape(str(fields.get('reason')))})"
            )
        elif event.name == COMPONENT_END:
            row = self._rows.pop(event.span, None)
            if self._progress is not None and row is not None:
                self._progress.remove_task(row)
            self._say(f"  {component} {_state(fields)}")
        elif event.name == RELEASE_END and fields.get("status") != "released":
            self._say(
                f"  [red]release {escape(str(fields.get('status')))}[/red] ({component}): "
                f"{escape(str(fields.get('error')))}"
            )
        elif event.name == EVENTS_DROPPED:
            self._say(f"  [dim]{fields.get('count')} events dropped[/dim]")
        elif event.name == RUN_END:
            self._say(f"[bold]run {run}[/bold] {_state(fields)}")
            self._runs = max(0, self._runs - 1)
            if self._runs == 0 and self._progress is not None:
                self._progress.stop()
                self._progress = None
                self._rows.clear()

    def _say(self, line: str) -> None:
        self._console.print(line)


def _state(fields: dict[str, Any]) -> str:
    status = str(fields.get("status"))
    parts = [f"[{_STYLES.get(status, 'default')}]{escape(status)}[/]"]
    if fields.get("reason"):
        parts.append(f"({escape(str(fields['reason']))})")
    if fields.get("abandoned"):
        parts.append("[red]abandoned[/red]")
    if "duration_ms" in fields:
        parts.append(f"in {fields['duration_ms'] / 1000:.2f}s")
    return " ".join(parts)

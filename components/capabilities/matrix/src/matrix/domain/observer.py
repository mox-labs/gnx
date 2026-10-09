"""Observers: what happened during a run, for whoever wants to know.

The executor and bound agents emit :class:`Event` records; observers receive them. Tracing
(OpenTelemetry), logging and cost metering are observers, so matrix's core imports none of
them and a caller that wants none pays nothing.

Events are versioned by ``schema`` and carry plain fields. An observer that raises is logged
and skipped; it cannot stop a run. That makes observers telemetry, **not** an audit channel:
an audit hook would have to be able to abort the operation, which this port deliberately
does not offer.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger("matrix.observer")

#: Event names. ``*.start``/``*.end`` pairs share a ``span`` id.
EVENTS = (
    "run.start",
    "run.end",
    "member.start",
    "member.end",
    "artifact.append",
    "agent.start",
    "agent.end",
)


#: ``(run_id, span)`` of the flow member running in this task, so an agent it calls reports
#: as that member's child without the member threading ids through its arguments.
CURRENT_SPAN: ContextVar[tuple[str, str] | None] = ContextVar("matrix_span", default=None)


@dataclass(frozen=True)
class Event:
    name: str
    span: str
    run_id: str | None = None
    parent: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    time: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema: str = "matrix.v1.event"


@runtime_checkable
class Observer(Protocol):
    def on_event(self, event: Event) -> None: ...


def emit(observers: Sequence[Observer], event: Event) -> None:
    """Deliver ``event`` to each observer; one that raises is logged and does not stop others."""
    for observer in observers:
        try:
            observer.on_event(event)
        except Exception:
            logger.exception("observer %r failed on %s", observer, event.name)


class RecordingObserver:
    """Keeps every event in memory. For tests, and for callers that report after a run."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def on_event(self, event: Event) -> None:
        self.events.append(event)

    def names(self) -> list[str]:
        return [e.name for e in self.events]

"""Observers: what happened during a run, for whoever wants to know.

The executor, bound agents and components emit :class:`Event` records; observers receive them.

**Telemetry is not data.** Nothing a run produces depends on an event being delivered: the
Construct is the record. So the channel is lossy on purpose, and **an observer cannot stall
or fail a run**: events go onto a bounded queue (:class:`EventPolicy`), emitting never waits,
an event that does not fit is dropped and counted (``matrix.events.dropped``,
``Run.events_dropped``), an observer that raises is logged and skipped, and at its end a run
waits at most ``drain_s`` for the queue. A synchronous observer that blocks the event loop is
the one thing this cannot bound.

**Event names are namespaced**, root first: matrix owns ``matrix.*``, and a component emits
under its own root. A ``*.start``/``*.end`` pair shares a ``span`` id. Within
``matrix.v1.event`` fields are only ever added.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import uuid
from collections import deque
from contextvars import ContextVar
from dataclasses import KW_ONLY, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from matrix.domain.errors import ConfigError, ContractError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Sequence

logger = logging.getLogger("matrix.observer")

EVENT_SCHEMA = "matrix.v1.event"

#: The names matrix emits, and the fields each carries beyond ``component``.
RUN_START = "matrix.run.start"  # components
RUN_END = "matrix.run.end"  # status, rows, duration_ms
COMPONENT_START = "matrix.component.start"
COMPONENT_STOPPING = "matrix.component.stopping"  # reason
COMPONENT_END = "matrix.component.end"  # status, reason?, duration_ms?, kind?, abandoned?
COMPONENT_LATE = "matrix.component.late"  # call: the context method used after it ended
COMPONENT_RETRY = "matrix.component.retry"  # attempt, kind, error, wait_s
ROW_APPEND = "matrix.row.append"  # kind, row, seq
RELEASE_START = "matrix.release.start"
RELEASE_END = "matrix.release.end"  # status, error?
AGENT_START = "matrix.agent.start"  # agent, model, task (as sent)
AGENT_END = "matrix.agent.end"  # agent, status, tokens_*, cost_usd, model, family, provider,
#   duration_ms, tool_calls; reason and error when it failed
TOOL_START = "matrix.tool.start"  # tool, call_id?, agent; timed as the runtime saw the call
TOOL_END = "matrix.tool.end"  # tool, call_id?, agent, status, duration_ms
MEASURE = "matrix.measure"  # name, value, unit, kind, attrs
EVENTS_DROPPED = "matrix.events.dropped"  # count

MEASURE_KINDS = ("gauge", "counter", "histogram")

_NAME = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)+$")


@dataclass(frozen=True)
class Event:
    name: str
    _: KW_ONLY
    span: str
    run_id: str | None = None
    #: The span this one is under; None for the run's own span, which is under nothing.
    parent: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    #: The component the event is about, or that emitted it. ``None`` for run-level events.
    component: str | None = None
    time: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema: str = EVENT_SCHEMA

    def document(self) -> dict[str, Any]:
        """The event as a JSON-ready document: the versioned shape every sink writes."""
        return {
            "schema": self.schema,
            "name": self.name,
            "time": self.time.isoformat(),
            "run_id": self.run_id,
            "span": self.span,
            "parent": self.parent,
            "component": self.component,
            "fields": self.fields,
        }


@runtime_checkable
class Observer(Protocol):
    """A sink for events. ``on_event`` may be a plain function or a coroutine function."""

    def on_event(self, event: Event) -> Awaitable[None] | None: ...


def check_event_name(name: str, *, what: str = "event") -> None:
    """Refuse a component's ``name`` unless it is namespaced and outside ``matrix.*``."""
    if not isinstance(name, str) or not _NAME.match(name):
        raise ContractError(
            f"{what} name {name!r} is not namespaced: use dotted lower-case segments, root "
            "first (acme.probe.sample)",
            fix="name it under your own root",
        )
    if name.startswith("matrix."):
        raise ContractError(
            f"{what} name {name!r} is under matrix.*, which matrix reserves for its own events",
            fix="name it under your own root",
        )


@dataclass(frozen=True)
class EventPolicy:
    """How much telemetry a run may hold, and how long it waits for observers at its end."""

    #: Events waiting for the observers. One more than this is dropped and counted.
    queue: int = 4096
    #: At run end, how long to wait for the queue to empty before leaving it.
    drain_s: float = 10.0

    def __post_init__(self) -> None:
        if self.queue < 1:
            raise ConfigError(f"queue must be >= 1, got {self.queue}")
        if self.drain_s < 0:
            raise ConfigError(f"drain_s must be >= 0, got {self.drain_s}")


async def deliver(observers: Sequence[Observer], event: Event) -> None:
    """Hand ``event`` to each observer; one that raises is logged and does not stop others."""
    for observer in observers:
        try:
            pending = observer.on_event(event)
            if inspect.isawaitable(pending):
                await pending
        except Exception:
            logger.exception("observer %r failed on %s", observer, event.name)


class Channel:
    """One run's event queue and the task that drains it into the observers."""

    def __init__(
        self, observers: Sequence[Observer], policy: EventPolicy, run_id: str | None = None
    ) -> None:
        self._observers = tuple(observers)
        self._policy = policy
        self._run_id = run_id
        self._queue: asyncio.Queue[tuple[Event | None, tuple[Observer, ...]]] = asyncio.Queue(
            policy.queue
        )
        self._dispatcher: asyncio.Task[None] | None = None
        self._unreported = 0
        self._accepted = 0
        self._delivered = 0
        self._closed = False
        #: Events emitted but never delivered.
        self.dropped = 0

    def start(self) -> None:
        self._dispatcher = asyncio.ensure_future(self._dispatch())

    def emit(self, event: Event, also: Sequence[Observer] = ()) -> None:
        """Queue ``event`` for the run's observers, and for any in ``also`` not among them.

        Never waits and never raises: a full queue drops the event and counts it.
        """
        extra = tuple(o for o in also if not any(o is known for known in self._observers))
        if self._closed or (not self._observers and not extra):
            return
        try:
            self._queue.put_nowait((event, extra))
            self._accepted += 1
        except asyncio.QueueFull:
            self.dropped += 1
            self._unreported += 1

    async def _dispatch(self) -> None:
        while True:
            event, extra = await self._queue.get()
            try:
                if self._unreported:
                    count, self._unreported = self._unreported, 0
                    notice = Event(
                        EVENTS_DROPPED,
                        span=uuid.uuid4().hex,
                        run_id=self._run_id,
                        fields={"count": count},
                    )
                    await deliver(self._observers, notice)
                if event is not None:
                    await deliver((*self._observers, *extra), event)
                    self._delivered += 1
            finally:
                self._queue.task_done()

    async def close(self) -> int:
        """Give the observers ``drain_s`` to catch up, then stop. Returns the events dropped."""
        if self._dispatcher is None or self._closed:
            return self.dropped
        self._closed = True
        if self._unreported and not self._queue.full():
            self._queue.put_nowait((None, ()))  # nothing follows to carry the last notice
        try:
            await asyncio.wait_for(self._queue.join(), self._policy.drain_s)
        except TimeoutError:
            # Whatever the observers did not get to, the one in their hands included.
            self.dropped += self._accepted - self._delivered
        self._dispatcher.cancel()
        return self.dropped


@dataclass(frozen=True)
class SpanScope:
    """Where the current task is inside a run: whose span it is under, and the run's channel."""

    run_id: str
    span: str
    component: str
    channel: Channel


#: The scope of the component running in this task, so an agent it calls reports as that
#: component's child, through the run's channel, without the component threading ids through.
CURRENT_SPAN: ContextVar[SpanScope | None] = ContextVar("matrix_span", default=None)


async def emit(observers: Sequence[Observer], event: Event) -> None:
    """Emit from code that may or may not be inside a run (a bound agent).

    Inside a run the event goes through the run's channel, so it cannot stall the component;
    outside one there is no channel, and the observers are called here and now.
    """
    scope = CURRENT_SPAN.get()
    if scope is not None:
        scope.channel.emit(event, also=observers)
    else:
        await deliver(observers, event)


class RecordingObserver:
    """Keeps the last ``limit`` events in memory. For tests, and for reporting after a run."""

    def __init__(self, limit: int = 10_000) -> None:
        self.events: deque[Event] = deque(maxlen=limit)

    def on_event(self, event: Event) -> None:
        self.events.append(event)

    def names(self) -> list[str]:
        return [e.name for e in self.events]

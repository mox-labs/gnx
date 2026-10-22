"""Observers: what happened during a run, for whoever wants to know.

The executor, bound agents and components emit :class:`Event` records; observers receive them.

**Telemetry is not data.** Nothing a run produces depends on an event being delivered: the
Construct is the record. So the channel is lossy on purpose, and **an observer's
``on_event`` cannot stall or fail a run**: events go onto a bounded queue
(:class:`EventPolicy`), emitting never waits, an event that does not fit is dropped and
counted (``matrix.events.dropped``, ``Run.events_dropped``), an observer that raises is logged
and skipped, and at its end a run waits at most ``drain_s`` for the queue. A synchronous
observer that blocks the event loop is the one thing this cannot bound.

**Event names are namespaced**, root first: matrix owns ``matrix.*``, and a component emits
under its own root. A ``*.start``/``*.end`` pair matrix emits for a span of work shares a
``span`` id; an event a component emits with ``ctx.emit`` is one event, whatever its name
ends with, and opens no span. Within ``matrix.v1.event`` fields are only ever added.

**Around the work.** An observer may have one optional member besides ``on_event``, read with
``getattr`` so an observer written before it works unedited:

    around(event) -> AbstractContextManager[Callable[[Event], None] | None] | None

matrix enters it **synchronously, in the task doing the work**, around each span of work it
starts: a run, a component (all its attempts), a ``ctx.span``, an agent session, a model
call a model step makes, a tool call a runtime timed, and a deferred release. ``event`` is the
span's start. What entering it gives back, when callable, is handed the span's end event
before the block exits, so the observer can close what it opened with the end's facts without
waiting for the queue; the start and the end also reach ``on_event`` through the channel, as
every event does. This is how the ``otel`` observer makes its span current, so a span a
library opens inside the work nests under it, and how its spans end whatever the channel
drops. Because it runs in the work's own task, ``around`` **must not block**: no I/O that can
wait, no lock another task holds. What it raises, entering, ending or leaving, is logged and
skipped, and it can never swallow the work's own exception. An observer's ``around`` is the
one part of an observer that can slow a run.
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
    from collections.abc import Awaitable, Callable, Sequence
    from contextlib import AbstractContextManager

logger = logging.getLogger("matrix.observer")

EVENT_SCHEMA = "matrix.v1.event"

#: The names matrix emits, and the fields each carries beyond ``component``.
RUN_START = "matrix.run.start"  # components
RUN_END = "matrix.run.end"  # status, rows, duration_ms. Sent before the record is finished:
#   a store that then cannot finish it makes the run failed, which only the Run says
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
#   duration_ms, tool_calls; reason and error when it failed; status cancelled when cancelled
MODEL_START = "matrix.model.start"  # runtime, model (as asked)
MODEL_END = "matrix.model.end"  # runtime, status, name, model, family, provider, finish_reason,
#   duration_ms; reason and error when it failed; status cancelled when cancelled. No usage:
#   the session row holds it, and the model runtime's own telemetry counts it
TOOL_START = "matrix.tool.start"  # tool, call_id?, agent, arguments? (JSON, content); timed
#   as the runtime saw the call
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
    """A sink for events. ``on_event`` may be a plain function or a coroutine function.
    ``around`` is optional (see the module docstring)."""

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


def check_measure(name: str, value: Any, kind: str) -> None:
    """Refuse a measurement a run would not record: a name :func:`check_event_name` refuses,
    a ``kind`` that is not one of :data:`MEASURE_KINDS`, or a value that is not a number."""
    check_event_name(name, what="measure")
    if kind not in MEASURE_KINDS:
        raise ContractError(
            f"measure {name!r}: kind {kind!r} is not one of {', '.join(MEASURE_KINDS)}"
        )
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ContractError(f"measure {name!r}: value must be a number, got {type(value).__name__}")


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

    def observers(self, also: Sequence[Observer] = ()) -> tuple[Observer, ...]:
        """The run's observers, and any in ``also`` not among them."""
        return (*self._observers, *self._extra(also))

    def _extra(self, also: Sequence[Observer]) -> tuple[Observer, ...]:
        return tuple(o for o in also if not any(o is known for known in self._observers))

    def emit(self, event: Event, also: Sequence[Observer] = ()) -> None:
        """Queue ``event`` for the run's observers, and for any in ``also`` not among them.

        Never waits and never raises: a full queue drops the event and counts it.
        """
        extra = self._extra(also)
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


def observing(observers: Sequence[Observer] = ()) -> tuple[Observer, ...]:
    """Who sees the work of code that may or may not be inside a run: the run's observers
    and ``observers`` inside one, ``observers`` outside one. What :func:`emit` reaches."""
    scope = CURRENT_SPAN.get()
    return scope.channel.observers(observers) if scope is not None else tuple(observers)


class _Around:
    """The ``around`` of each observer that has one, entered as one context manager. Entering
    gives back ``end``, which hands the span's end event to each observer that asked for it."""

    def __init__(self, observers: Sequence[Observer], event: Event) -> None:
        self._observers = observers
        self._event = event
        self._entered: list[tuple[Observer, AbstractContextManager[Any]]] = []
        self._enders: list[tuple[Observer, Callable[[Event], None]]] = []

    def __enter__(self) -> Callable[[Event], None]:
        for observer in self._observers:
            hook = getattr(observer, "around", None)
            if not callable(hook):
                continue
            try:
                manager = hook(self._event)
                if manager is None:
                    continue
                given = manager.__enter__()
            except Exception:
                logger.exception("observer %r failed entering %s", observer, self._event.name)
                continue
            self._entered.append((observer, manager))
            if callable(given):
                self._enders.append((observer, given))
        return self.end

    def end(self, event: Event) -> None:
        """Hand ``event``, the span's end, to each observer whose ``around`` asked for it."""
        for observer, ender in self._enders:
            try:
                ender(event)
            except Exception:
                logger.exception("observer %r failed ending %s", observer, self._event.name)

    def __exit__(self, *raised: Any) -> None:
        # Last entered, first left; an observer cannot swallow the work's exception, since
        # what its __exit__ returns is not passed on.
        for observer, manager in reversed(self._entered):
            try:
                manager.__exit__(*raised)
            except Exception:
                logger.exception("observer %r failed leaving %s", observer, self._event.name)


def around(
    observers: Sequence[Observer], event: Event
) -> AbstractContextManager[Callable[[Event], None]]:
    """Enter each observer's optional ``around(event)`` (see the module docstring) for the span
    of work ``event`` starts. Entered in the task doing that work, around all of it; what it
    gives back is called with the span's end event before the block exits."""
    return _Around(observers, event)


class RecordingObserver:
    """Keeps the last ``limit`` events in memory. For tests, and for reporting after a run."""

    def __init__(self, limit: int = 10_000) -> None:
        self.events: deque[Event] = deque(maxlen=limit)

    def on_event(self, event: Event) -> None:
        self.events.append(event)

    def names(self) -> list[str]:
        return [e.name for e in self.events]

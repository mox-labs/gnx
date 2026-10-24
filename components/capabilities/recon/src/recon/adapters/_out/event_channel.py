"""A survey's event channel: a bounded queue and the thread that drains it to receivers.

What a survey tells OpenTelemetry and its caller's ``on_event`` goes through here (ruling
A6), so a receiver that is slow, or stalls, never slows the writing of the archive: the
survey hands an event to the queue and goes on. When the queue is full the event is
dropped and counted, never waited for. When the survey ends, :meth:`ThreadChannel.close`
gives the receivers ``drain_s`` seconds to catch up, then counts what they did not get as
dropped too, and nothing reaches them after that. The archive's own event log is not a
receiver here: it is archive data, written inline.

Nothing here waits past ``drain_s``. A receiver that does not return from an event holds
the channel's thread: what is queued behind it is counted as dropped when the survey ends,
the receiver is named in ``stuck``, and its ``close`` is not called, since the thread is
still inside it. The thread is a daemon and stays until the receiver returns.

A receiver that raises gets nothing more. A sink's error is raised to the survey at its next
event, so :class:`~recon.domain.telemetry.Telemetry` drops it and names it, as it does a sink
called inline; a callback's is kept in ``failed``. A sink's ``around`` and ``trace_headers``
are answered at once, not through the queue: what they return is needed now, and they must
not block (``recon.domain.telemetry``).

Each receiver is called on the channel's one thread, in the order the events were sent.
The names follow matrix's event channel (``EventPolicy.queue``, ``drain_s``, ``dropped``).
"""

from __future__ import annotations

import contextlib
import queue
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from recon.domain.telemetry import Event, EventCallback, EventSink

#: Events waiting for the receivers, at most; one more is dropped and counted.
QUEUE = 4096
#: Seconds the survey's end waits for the receivers to catch up.
DRAIN_S = 10.0

_Delivery = tuple[str, "Callable[[Any], None]", Any]


class ThreadChannel:
    """One survey's channel. ``dropped`` counts the events the receivers never got;
    ``failed`` names each callback that raised, with its error; ``stuck`` each receiver
    that had not returned from an event when the survey ended."""

    def __init__(self, *, queue: int = QUEUE, drain_s: float = DRAIN_S) -> None:
        if queue < 1 or drain_s < 0:
            raise ValueError(f"queue must be >= 1 and drain_s >= 0, got {queue}, {drain_s}")
        self._queue: _Queue = _Queue(queue)
        self._drain_s = drain_s
        self._closed = False
        self._stopped = threading.Event()
        self.dropped = 0
        self.failed: list[str] = []
        self.stuck: list[str] = []
        #: The receiver the thread is delivering to now, by name; None between deliveries.
        self._delivering: str | None = None
        self._thread = threading.Thread(target=self._deliver, name="recon-events", daemon=True)
        self._thread.start()

    def sink(self, sink: EventSink) -> EventSink:
        """``sink``, its events delivered through this channel."""
        return _QueuedSink(self, sink)

    def callback(self, callback: EventCallback) -> EventCallback:
        """``callback``, called through this channel."""
        return _QueuedCallback(self, callback)

    def close(self) -> None:
        """Wait up to ``drain_s`` for the receivers to get what is queued, then stop: what
        they did not get is dropped, and nothing more reaches them. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        left = self._queue.wait_empty(self._drain_s)
        self.dropped += left
        self._stopped.set()
        delivering = self._delivering
        if left and delivering is not None:
            self.note_stuck(delivering)
        # Wakes the thread, so it ends; when the queue is full, it ends at its next delivery.
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(None)

    def send(self, receiver: str, deliver: Callable[[Any], None], item: Any) -> None:
        """Queue ``deliver(item)``, to the receiver named, for the channel's thread; never
        waits."""
        if self._closed:
            self.dropped += 1
            return
        try:
            self._queue.put_nowait((receiver, deliver, item))
        except queue.Full:
            self.dropped += 1

    def note_stuck(self, receiver: str) -> None:
        if receiver not in self.stuck:
            self.stuck.append(receiver)

    def close_wait_s(self) -> float:
        """How long closing a receiver may wait for a delivery to it to end: the drain time
        while the channel is open, nothing once it has closed, its drain spent."""
        return 0.0 if self._closed else self._drain_s

    def _deliver(self) -> None:
        while True:
            got = self._queue.get()
            try:
                if got is None or self._stopped.is_set():
                    return
                receiver, deliver, item = got
                self._delivering = receiver
                deliver(item)
            finally:
                self._delivering = None
                self._queue.task_done()


class _Queue(queue.Queue[_Delivery | None]):
    def wait_empty(self, seconds: float) -> int:
        """Wait up to ``seconds`` for every queued item to be done; the items still not done."""
        with self.all_tasks_done:
            self.all_tasks_done.wait_for(lambda: not self.unfinished_tasks, timeout=seconds)
            return int(self.unfinished_tasks)


class _QueuedSink:
    """A sink whose events go through a channel; ``around`` and ``trace_headers`` do not."""

    def __init__(self, channel: ThreadChannel, sink: EventSink) -> None:
        self._channel = channel
        self._sink = sink
        self._name = type(sink).__name__
        self._lock = threading.Lock()
        self._error: Exception | None = None
        self._closed = False

    def on_event(self, event: Event) -> None:
        self._raise_its_error()
        self._channel.send(self._name, self._deliver, event)

    def around(self, event: Event) -> AbstractContextManager[Any] | None:
        around = getattr(self._sink, "around", None)
        return None if around is None else around(event)

    def trace_headers(self, span_id: str) -> dict[str, str]:
        given = getattr(self._sink, "trace_headers", None)
        return {} if given is None else dict(given(span_id))

    def close(self) -> None:
        """Close the sink, unless a delivery to it has not ended within the channel's wait:
        then it is named stuck, and left alone."""
        if not self._lock.acquire(timeout=self._channel.close_wait_s()):
            self._channel.note_stuck(self._name)
            return
        try:
            self._closed = True
            close = getattr(self._sink, "close", None)
            if close is not None:
                close()
        finally:
            self._lock.release()
        self._raise_its_error()

    def _deliver(self, event: Event) -> None:
        with self._lock:
            if self._closed or self._error is not None:
                return
            try:
                self._sink.on_event(event)
            except Exception as exc:  # raised to the survey at its next event
                self._error = exc

    def _raise_its_error(self) -> None:
        if self._error is not None:
            error = self._error
            raise RuntimeError(
                f"{type(self._sink).__name__} raised {type(error).__name__}: {error}"
            ) from error


class _QueuedCallback:
    def __init__(self, channel: ThreadChannel, callback: EventCallback) -> None:
        self._channel = channel
        self._callback = callback
        self._failed = False

    def __call__(self, event: dict[str, Any]) -> None:
        self._channel.send("on_event", self._deliver, event)

    def _deliver(self, event: dict[str, Any]) -> None:
        if self._failed:
            return
        try:
            self._callback(event)
        except Exception as exc:  # a receiver must not fail the work it watches
            self._failed = True
            self._channel.failed.append(f"on_event: {type(exc).__name__}: {exc}")

"""SseCollector: server-sent events, each a record, for as long as a stream runs.

An ``sse`` collector sends one GET to its ``api`` source's url and ``endpoint`` (with
``params``, and the source's credential as its ``auth`` says, as an ``api`` collector does),
asking for ``text/event-stream``, and reads the events as the server sends them, through the
requester's ``follow``. Each event with data is a record::

    {"id": <the last event id>, "event": <its type, "message" by default>,
     "data": <its data: JSON when it is JSON, else the text>, "_position": <the id>}

as the event-stream format says to read them (comments ignored, ``data:`` lines joined, an id
kept until the next one, ``retry:`` taken as the server's wait). The id is the record's
position: a stream resumed after it sends ``Last-Event-ID``, so a server that keeps its
events sends each once. An event before any id has no position.

An event's data is held to the stream's ``max_record_bytes`` (its ``buffer:``, or recon's
default) as its lines arrive, and so is each line, which the requester lets go past it
(``follow(max_line=)``) rather than hold: an event past it is dropped, its later lines read
and let go, and counted dropped in the window being filled (``CollectorContext.gaps``). So a
stream holds no more than ``buffer.bytes`` + ``max_record_bytes`` of its source.

**Reconnecting.** When the connection ends or breaks off (the server closed it, the network
failed, a quiet source outlasted its ``timeout``, a ``transient`` status), the collector
connects again with ``Last-Event-ID``, after a full-jitter wait: a random time up to
``min(30, wait * 2 ** n)`` seconds, where ``wait`` is the server's ``retry`` (1 second until
it says) and ``n`` the connections in a row that brought no event. The random times are not
seeded, so processes streaming from one server do not reconnect in step; each stretch is
reported as a gap from the clock port (``CollectorContext.gaps``), ``resumed`` when a
``Last-Event-ID`` was sent, so the record of what happened needs no seed. The wait is on the
real clock and ends at once when the collector is closed (a stream stopped while its server
is down stops promptly): recon's one wait not on the clock port. ``reconnect`` (5 by default)
connects in a row that fail are allowed; one more fails the stream (``transient``). A
connection that is made and later ends spends none of it, so a healthy quiet stream runs for
as long as it is asked. A status a retry cannot fix (401, 403: ``auth``; 404 and the rest:
``run_failed``) fails it at once.

Registered as the built-in collector type ``sse`` (entry-point group ``recon.collectors``):
``unbounded`` and ``paced_by: source``, since a server sends whether recon is ready or not.
"""

from __future__ import annotations

import json
import random
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from recon.adapters._out.http_source import auth_headers, auth_params, build_url
from recon.adapters._out.user_agent import DEFAULT_USER_AGENT
from recon.domain.collector import CollectorType
from recon.domain.errors import CollectionError, Problem
from recon.domain.http import LONG_LINE, REDACTED, reported_url
from recon.domain.stream import POSITION_FIELD, BufferSpec, Gap

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping
    from datetime import datetime

    from recon.domain.capture import CaptureLog
    from recon.domain.clock import Clock
    from recon.domain.http import LiveBody, Requester
    from recon.domain.models import CollectorEntry, SourceEntry
    from recon.domain.stream import GapLog

#: The longest wait before a reconnect, in seconds, however many attempts have failed.
_MOST_WAIT_S = 30.0


class SseSettings(BaseModel):
    """An ``sse`` collector's own keys: ``endpoint``, appended to the source's url; ``params``,
    the query parameters, sent as written; ``reconnect``, the connects in a row allowed to fail
    before the stream fails (5; 0 fails it at the first)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: str | None = None
    params: dict[str, str] = {}
    reconnect: int = Field(default=5, ge=0, strict=True)


@dataclass(frozen=True)
class _Event:
    id: str | None
    event: str
    data: str


class _TooLarge:
    """An event whose data passed the stream's ``max_record_bytes``: dropped."""


_TOO_LARGE = _TooLarge()


class SseCollector:
    """Reads a server's events. ``close()``, from any thread, ends the read in progress and the
    wait before a reconnect. ``jitter`` draws the waits (unseeded by default); ``pause(s)``
    waits ``s`` seconds before a reconnect (by default on the real clock, ended by ``close()``):
    a test hands its own of each."""

    def __init__(
        self,
        requester: Requester,
        clock: Clock | None,
        gaps: GapLog,
        env: Mapping[str, str],
        user_agent: str,
        jitter: random.Random | None = None,
        pause: Callable[[float], object] | None = None,
    ) -> None:
        if clock is None:
            raise CollectionError(
                "an sse collector waits on the survey's clock before reconnecting, and none was "
                "given",
                kind="config",
                fix="build it through recon's root, which hands every type the clock",
            )
        self._requester = requester
        self._clock = clock
        self._gaps = gaps
        self._env = env
        self._user_agent = user_agent
        self._jitter = jitter or random.Random()
        self._closed = threading.Event()
        self._pause = pause or self._closed.wait
        self._body: LiveBody | None = None

    def close(self) -> None:
        self._closed.set()
        body = self._body
        if body is not None:
            body.close()

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
        after: Any = None,
    ) -> Iterator[dict[str, Any]]:
        settings = SseSettings.model_validate(entry.settings)
        assert source is not None and settings.endpoint is not None  # the plan holds both
        follow = getattr(self._requester, "follow", None)
        if follow is None:
            raise CollectionError(
                "this survey's requester reads no body as it arrives, which an sse collector needs",
                kind="config",
                fix="survey through recon's own requester (root() without requester=)",
            )
        url = build_url(source.url, settings.endpoint, {})
        params = {**settings.params, **auth_params(source, self._env)}
        last: str | None = None if after is None else str(after)
        cap = (entry.buffer or BufferSpec()).max_record_bytes
        wait_s = 1.0
        failed = 0  # connects in a row that failed: what reconnect: allows
        quiet = 0  # connections in a row that brought no event: what the wait grows with
        lost: datetime | None = None
        self._closed.clear()
        while not self._closed.is_set():
            headers = {**auth_headers(source, self._env, self._user_agent),
                       "Accept": "text/event-stream", "Cache-Control": "no-cache"}  # fmt: skip
            if last is not None:
                headers["Last-Event-ID"] = last
            connected = False
            try:
                with follow(source, url, params=params, headers=headers, max_line=cap) as body:
                    self._body = body
                    connected, failed = True, 0
                    if self._closed.is_set():
                        return
                    if lost is not None:
                        self._gaps.gap(Gap(lost, self._clock.now(), resumed=last is not None))
                        lost = None
                    for item in _events(body, cap):
                        if isinstance(item, int):
                            wait_s = item / 1000
                            continue
                        quiet = 0
                        if isinstance(item, _TooLarge):
                            self._gaps.drop()
                            continue
                        last = item.id if item.id is not None else last
                        yield _record(item, last)
            except CollectionError as exc:
                if self._closed.is_set():
                    return
                if exc.kind != "transient":
                    raise
            finally:
                self._body = None
            if self._closed.is_set():
                return
            lost = lost or self._clock.now()
            quiet += 1
            failed += not connected
            if failed > settings.reconnect:
                raise CollectionError(
                    f"{reported_url(url, source.auth.param)} could not be reached: "
                    f"{settings.reconnect} reconnect(s) in a row failed to connect",
                    kind="transient",
                    fix=f"raise reconnect: on collector {entry.name!r} to wait longer for the "
                    "server, or stream again later",
                )
            self._pause(self._jitter.uniform(0, min(_MOST_WAIT_S, wait_s * 2 ** (quiet - 1))))


def _events(lines: Iterable[str], cap: int) -> Iterator[_Event | _TooLarge | int]:
    """The events of an event stream's lines, and each ``retry`` (milliseconds), as the
    format says to read them: a blank line ends an event, sent only when it has data; an id
    holds until the next one; a line starting ``:`` is a comment. An event whose data passes
    ``cap`` bytes is :data:`_TOO_LARGE`, its data let go as it passes."""
    event_id: str | None = None
    kind = ""
    data: list[str] = []
    size = 0
    over = False
    for line in lines:
        if not line:
            if over:
                yield _TOO_LARGE
            elif data:
                yield _Event(event_id, kind or "message", "\n".join(data))
            kind, data, size, over = "", [], 0, False
            continue
        if line == LONG_LINE:  # a line past the cap, let go: its event goes with it
            over, data = True, []
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "data" and not over:
            size += len(value.encode()) + (1 if data else 0)  # the newline that joins it
            if size > cap:
                over, data = True, []
            else:
                data.append(value)
        elif name == "event":
            kind = value
        elif name == "id" and "\0" not in value:
            event_id = value
        elif name == "retry" and value.isdigit():
            yield int(value)


def _record(event: _Event, last: str | None) -> dict[str, Any]:
    try:
        data: Any = json.loads(event.data)
    except json.JSONDecodeError:
        data = event.data
    record: dict[str, Any] = {"id": last, "event": event.event, "data": data}
    if last is not None:
        record[POSITION_FIELD] = last
    return record


# --- Registration ---


def _check(entry: CollectorEntry) -> list[Problem]:
    if SseSettings.model_validate(entry.settings).endpoint:
        return []
    return [
        Problem(
            "endpoint",
            "an sse collector needs an endpoint",
            kind="missing_key",
            fix="endpoint: /events (the path after the source's url)",
        )
    ]


def _describe(entry: CollectorEntry, source: SourceEntry | None) -> dict[str, Any]:
    settings = SseSettings.model_validate(entry.settings)
    if source is None or not settings.endpoint:
        return {}
    params = dict(settings.params)
    if source.auth.param and source.auth.env:
        params[source.auth.param] = REDACTED
    query = "&".join(f"{key}={value}" for key, value in params.items())
    url = build_url(source.url, settings.endpoint, {}) + (f"?{query}" if query else "")
    headers = {"User-Agent": source.user_agent or DEFAULT_USER_AGENT,
               "Accept": "text/event-stream"}  # fmt: skip
    if source.auth.header and source.auth.env:
        headers[source.auth.header] = REDACTED
    return {
        "method": "GET",
        "url": reported_url(url, source.auth.param),
        "headers": headers,
        "reconnect": settings.reconnect,
    }


collector_type = CollectorType(
    create=lambda ctx: SseCollector(ctx.requester, ctx.clock, ctx.gaps, ctx.env, ctx.user_agent),
    effects=frozenset({"network"}),
    summary="server-sent events → each event a record, positioned by its id",
    settings=SseSettings,
    accepts_kinds=frozenset({"api"}),
    requires_source=True,
    check=_check,
    describe=_describe,
    unbounded=True,
    paced_by="source",
)

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

**Reconnecting.** When the connection ends or breaks off (the server closed it, the network
failed, a ``transient`` status), the collector connects again with ``Last-Event-ID``, after a
full-jitter wait on the clock port: a random time up to ``min(30, wait * 2 ** n)`` seconds,
where ``wait`` is the server's ``retry`` (1 second until it says) and ``n`` the attempts so
far. The random times are drawn from a sequence seeded by the collector's name, so two
streams wait apart while the same stream, given the same answers, waits the same each time:
recon's determinism contract holds for a stream too. Each such stretch is reported as a gap
(``CollectorContext.gaps``), ``resumed`` when a ``Last-Event-ID`` was sent. ``reconnect`` (5
by default) attempts in a row with no event between are allowed; one more fails the stream
(``transient``). A status a retry cannot fix (401, 403: ``auth``; 404 and the rest:
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
from recon.domain.http import REDACTED, reported_url
from recon.domain.stream import POSITION_FIELD, Gap

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping
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
    the query parameters, sent as written; ``reconnect``, the attempts in a row allowed after
    the connection ends before the stream fails (5; 0 fails it at the first)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: str | None = None
    params: dict[str, str] = {}
    reconnect: int = Field(default=5, ge=0, strict=True)


@dataclass(frozen=True)
class _Event:
    id: str | None
    event: str
    data: str


class SseCollector:
    """Reads a server's events. ``close()``, from any thread, ends the read in progress."""

    def __init__(
        self,
        requester: Requester,
        clock: Clock | None,
        gaps: GapLog,
        env: Mapping[str, str],
        user_agent: str,
        jitter: random.Random | None = None,
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
        self._jitter = jitter
        self._closed = threading.Event()
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
        jitter = self._jitter or random.Random(f"recon.sse:{entry.name}")
        wait_s = 1.0
        attempts = 0
        lost: datetime | None = None
        self._closed.clear()
        while not self._closed.is_set():
            headers = {**auth_headers(source, self._env, self._user_agent),
                       "Accept": "text/event-stream", "Cache-Control": "no-cache"}  # fmt: skip
            if last is not None:
                headers["Last-Event-ID"] = last
            try:
                with follow(source, url, params=params, headers=headers) as body:
                    self._body = body
                    if self._closed.is_set():
                        return
                    if lost is not None:
                        self._gaps.gap(Gap(lost, self._clock.now(), resumed=last is not None))
                        lost = None
                    for item in _events(body):
                        if isinstance(item, int):
                            wait_s = item / 1000
                            continue
                        attempts = 0
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
            attempts += 1
            if attempts > settings.reconnect:
                raise CollectionError(
                    f"{reported_url(url, source.auth.param)} did not stay connected: "
                    f"{settings.reconnect} reconnect(s) in a row ended with no event",
                    kind="transient",
                    fix=f"raise reconnect: on collector {entry.name!r} to wait longer for the "
                    "server, or stream again later",
                )
            self._clock.sleep(jitter.uniform(0, min(_MOST_WAIT_S, wait_s * 2 ** (attempts - 1))))


def _events(lines: Iterable[str]) -> Iterator[_Event | int]:
    """The events of an event stream's lines, and each ``retry`` (milliseconds), as the
    format says to read them: a blank line ends an event, sent only when it has data; an id
    holds until the next one; a line starting ``:`` is a comment."""
    event_id: str | None = None
    kind = ""
    data: list[str] = []
    for line in lines:
        if not line:
            if data:
                yield _Event(event_id, kind or "message", "\n".join(data))
            kind, data = "", []
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "data":
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

"""The ``sse`` collector type (ruling B9): server-sent events, each a record positioned by its
event id, through recon's own requester to a loopback server (``tests/sse_server.py``). A
dropped connection is a gap, resumed with ``Last-Event-ID`` after a full-jitter wait, within a
budget; a refusal that a retry cannot fix ends the stream."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

import pytest

from recon.adapters._out.http_requester import HttpRequester
from recon.adapters._out.sse_collector import SseCollector, collector_type
from recon.domain.errors import CollectionError
from recon.domain.models import CollectorEntry, SourceEntry
from recon.domain.ratelimit import RateLimiter
from tests.recon_fakes import HurriedClock
from tests.sse_server import SseServer

if TYPE_CHECKING:
    from recon.domain.stream import Gap


class Gaps:
    def __init__(self) -> None:
        self.said: list[Gap] = []

    def gap(self, gap: Gap) -> None:
        self.said.append(gap)


class Listening:
    """An sse collector run on a thread of its own, as a stream's pump runs it."""

    def __init__(
        self,
        url: str,
        *,
        after: Any = None,
        reconnect: int = 5,
        env: dict[str, str] | None = None,
        auth: dict[str, str] | None = None,
    ) -> None:
        self.clock = HurriedClock()
        self.gaps = Gaps()
        requester = HttpRequester(RateLimiter(self.clock), self.clock)
        self.collector = SseCollector(requester, self.clock, self.gaps, env or {}, "recon-test")
        self.records: list[dict[str, Any]] = []
        self.error: BaseException | None = None
        entry = CollectorEntry.model_validate(
            {"name": "events", "type": "sse", "endpoint": "/events", "reconnect": reconnect}
        )
        source = SourceEntry.model_validate({"name": "feed", "url": url, "auth": auth or {}})
        resume = {} if after is None else {"after": after}
        records = self.collector.collect(entry, source, **resume)

        def run() -> None:
            try:
                self.records.extend(records)
            except BaseException as exc:
                self.error = exc

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def until(self, count: int) -> list[dict[str, Any]]:
        started = time.monotonic()
        while len(self.records) < count:
            assert self.error is None, self.error
            assert time.monotonic() - started < 10, f"timed out with {self.records}"
            time.sleep(0.005)
        return self.records

    def stop(self) -> float:
        started = time.monotonic()
        self.collector.close()
        self.thread.join(5)
        assert not self.thread.is_alive(), "close() ended the read in progress"
        return time.monotonic() - started

    def failed(self) -> BaseException:
        self.thread.join(10)
        assert self.error is not None
        return self.error


class TestEvents:
    def test_each_event_is_a_record_positioned_by_its_id(self) -> None:
        events = [("1", '{"a": 1}'), ("2", "plain\nand a second line"), (None, "no id")]
        with SseServer(events, preamble=": a comment\n\n") as server:
            listening = Listening(server.url)
            records = listening.until(3)
            took = listening.stop()

        assert records == [
            {"id": "1", "event": "message", "data": {"a": 1}, "_position": "1"},
            {"id": "2", "event": "message", "data": "plain\nand a second line",
             "_position": "2"},
            {"id": "2", "event": "message", "data": "no id", "_position": "2"},
        ]  # fmt: skip
        assert took < 2, "a stream stops promptly, even while its server is quiet"
        assert server.headers[0]["Accept"] == "text/event-stream"

    def test_a_resumed_stream_asks_for_the_events_after_its_position(self) -> None:
        events = [("1", "a"), ("2", "b"), ("3", "c")]
        with SseServer(events) as server:
            listening = Listening(server.url, after="2")
            records = listening.until(1)
            listening.stop()

        assert [r["id"] for r in records] == ["3"]
        assert server.asked[0] == "2"

    def test_a_source_s_credential_is_sent_as_its_auth_says(self) -> None:
        with SseServer([("1", "a")]) as server:
            listening = Listening(
                server.url,
                env={"FEED_TOKEN": "t0ken"},
                auth={"header": "Authorization", "env": "FEED_TOKEN", "prefix": "Bearer "},
            )
            listening.until(1)
            listening.stop()

        assert server.headers[0]["Authorization"] == "Bearer t0ken"


class TestDisconnects:
    def test_a_dropped_connection_resumes_after_the_last_event_once_each(self) -> None:
        events = [(str(n), f"event {n}") for n in range(1, 6)]
        with SseServer(events, per_connection=2) as server:
            listening = Listening(server.url)
            records = listening.until(5)
            listening.stop()

        assert [r["id"] for r in records] == ["1", "2", "3", "4", "5"]
        assert server.asked[:3] == [None, "2", "4"]
        assert len(listening.gaps.said) == 2
        assert all(gap.resumed for gap in listening.gaps.said)
        assert all(gap.start is not None and gap.start <= gap.end for gap in listening.gaps.said)

    def test_a_refusal_a_retry_cannot_fix_ends_the_stream(self) -> None:
        with SseServer([], status=401) as server:
            error = Listening(server.url).failed()

        assert isinstance(error, CollectionError)
        assert error.kind == "auth"
        assert len(server.asked) == 1, "no reconnect for a refusal"

    def test_reconnects_are_spent_by_a_budget_with_full_jitter_waits(self) -> None:
        with SseServer([], status=503) as server:
            listening = Listening(server.url, reconnect=3)
            error = listening.failed()

        assert isinstance(error, CollectionError)
        assert error.kind == "transient"
        assert "reconnect" in (error.fix or "")
        assert len(server.asked) == 4, "the first connection and three reconnects"
        caps = [min(30.0, 1.0 * 2**n) for n in range(3)]
        waits = listening.clock.slept
        assert len(waits) == 3
        assert all(0 <= wait <= cap for wait, cap in zip(waits, caps, strict=True))
        with SseServer([], status=503) as server:
            again = Listening(server.url, reconnect=3)
            again.failed()
        assert again.clock.slept == waits, "the same stream waits the same: determinism"


class TestTheType:
    def test_it_declares_a_stream_its_source_paces(self) -> None:
        assert collector_type.unbounded is True
        assert collector_type.paced_by == "source"
        assert collector_type.accepts_kinds == frozenset({"api"})
        assert collector_type.effects == frozenset({"network"})

    @pytest.mark.parametrize("reconnect", [-1, 1.5])
    def test_reconnect_is_a_count_of_zero_or_more(self, reconnect: Any) -> None:
        from pydantic import ValidationError

        assert collector_type.settings is not None
        with pytest.raises(ValidationError):
            collector_type.settings.model_validate({"endpoint": "/e", "reconnect": reconnect})

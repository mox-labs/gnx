"""The ``sse`` collector type (ruling B9): server-sent events, each a record positioned by its
event id, through recon's own requester to a loopback server (``tests/sse_server.py``). A
dropped connection is a gap, resumed with ``Last-Event-ID`` after a full-jitter wait, within a
budget; a refusal that a retry cannot fix ends the stream."""

from __future__ import annotations

import random
import threading
import time
from typing import TYPE_CHECKING, Any, Literal

import pytest

from recon.adapters._out.credentials import Credentials
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
        self.drops = 0

    def gap(self, gap: Gap) -> None:
        self.said.append(gap)

    def drop(self) -> None:
        self.drops += 1


class Longest(random.Random):
    """Jitter that always waits the most it may, and more: a wait long enough to see that
    ``close()`` cuts it short."""

    def uniform(self, a: float, b: float) -> float:
        return 60.0


#: Unset: the collector's own default.
DEFAULT: Any = object()

#: What a prompt stop is held to, on the wall clock: well under the sixty seconds a stop that
#: waited out the read timeout or the jitter would take, and far over what a loaded machine
#: adds to an event-driven close (a bound of 2 s left that to chance).
PROMPT_S = 15.0


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
        jitter: random.Random | None = DEFAULT,
        pause: Any = DEFAULT,
        timeout: float | None = None,
        buffer: dict[str, int] | None = None,
        max_bytes: int | None = None,
    ) -> None:
        """Waits before a reconnect are recorded on a :class:`HurriedClock` and skipped, from
        a jitter seeded the same each time, unless ``jitter`` or ``pause`` says otherwise
        (None: the collector's own)."""
        self.clock = HurriedClock()
        self.gaps = Gaps()
        requester = HttpRequester(RateLimiter(self.clock), self.clock)
        self.collector = SseCollector(
            requester, self.clock, self.gaps, Credentials(env or {}), "recon-test",
            jitter=random.Random("a seed") if jitter is DEFAULT else jitter,
            pause=self.clock.sleep if pause is DEFAULT else pause,
        )  # fmt: skip
        self.records: list[dict[str, Any]] = []
        self.error: BaseException | None = None
        entry = CollectorEntry.model_validate(
            {"name": "events", "type": "sse", "endpoint": "/events", "reconnect": reconnect,
             **({"buffer": buffer} if buffer else {})}
        )  # fmt: skip
        given: dict[str, Any] = {"timeout": timeout} if timeout is not None else {}
        if max_bytes is not None:
            given["max_bytes"] = max_bytes
        source = SourceEntry.model_validate(
            {"name": "feed", "url": url, "auth": auth or {}, **given}
        )
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
        self.thread.join(PROMPT_S + 5)
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
        assert took < PROMPT_S, "a stream stops promptly, even while its server is quiet"
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
                auth={"header": "Authorization", "token": "env:FEED_TOKEN", "prefix": "Bearer "},
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
        assert again.clock.slept == waits, "the same jitter, the same waits"

    def test_the_jitter_is_unseeded_so_streams_do_not_reconnect_in_step(self) -> None:
        """Review item 8 (Erlang #5): a jitter seeded by the collector's name made every
        process streaming the same collector reconnect at the same moments. Each wait is in
        the gap it leaves, so nothing needs the seed."""
        waits = []
        for _ in range(2):
            with SseServer([], status=503) as server:
                listening = Listening(server.url, reconnect=3, jitter=None)
                listening.failed()
            waits.append(listening.clock.slept)

        assert waits[0] != waits[1]

    def test_close_cuts_a_reconnect_wait_short(self) -> None:
        """Review item 9 (Erlang #6): the wait before a reconnect ends when the collector is
        closed, so a stream stops promptly while its server is down."""
        with SseServer([], status=503) as server:
            listening = Listening(server.url, jitter=Longest(), pause=None)
            started = time.monotonic()
            while not server.asked:
                assert time.monotonic() - started < 10
                time.sleep(0.005)
            took = listening.stop()

        assert took < PROMPT_S, "not the sixty seconds the jitter asked"
        assert len(server.asked) == 1

    @pytest.mark.parametrize("after_events", ["ends", "breaks"])
    def test_a_quiet_connection_spends_none_of_the_budget(
        self, after_events: Literal["ends", "breaks"]
    ) -> None:
        """Review item 18 (Erlang): only a connect that fails is spent; a healthy quiet
        stream whose connections end, or break off as a read that times out does, reconnects
        for as long as it runs. Each connection here ends as soon as it is made, so the test
        waits on no real timeout: a read timeout of 0.05 s made the connect itself time out
        under load, a spent reconnect, and the test flaky."""
        with SseServer([], after_events=after_events) as server:
            listening = Listening(server.url, reconnect=1)
            started = time.monotonic()
            # Waited for on the collector's side: the server counts a connection as it is
            # asked, before the collector, given its answer, records the gap it closes.
            while len(listening.gaps.said) < 3:
                assert listening.error is None, listening.error
                assert time.monotonic() - started < PROMPT_S, "it never reconnected"
                time.sleep(0.005)
            listening.stop()

        assert listening.error is None
        assert len(server.asked) >= 4, "three reconnects, each a quiet stretch and a gap"


class TestLargeEvents:
    def test_an_event_over_max_record_bytes_is_dropped_and_counted(self) -> None:
        """Review item 7 (Erlang #4): an event's data lines were joined however many came;
        one past ``max_record_bytes`` is dropped as it arrives, and counted."""
        big = "\n".join("x" * 50 for _ in range(5))
        events = [("1", "small"), ("2", big), ("3", "after")]
        with SseServer(events) as server:
            listening = Listening(server.url, buffer={"max_record_bytes": 100})
            records = listening.until(2)
            listening.stop()

        assert [r["id"] for r in records] == ["1", "3"]
        assert listening.gaps.drops == 1

    def test_a_line_over_max_record_bytes_is_skipped_not_held(self) -> None:
        """Erlang's re-check: a line was held whole up to the source's ``max_bytes`` (50 MB
        by default) and failed the stream past it. A line past ``max_record_bytes`` is let go
        as it arrives, to its newline, and its event dropped and counted: here the line is
        past the source's ``max_bytes`` too, which only a line never held can survive."""
        events = [("1", "small"), ("2", "x" * 300_000), ("3", "after")]  # many chunks
        with SseServer(events) as server:
            listening = Listening(server.url, buffer={"max_record_bytes": 100}, max_bytes=1000)
            records = listening.until(2)
            listening.stop()

        assert listening.error is None
        assert [r["id"] for r in records] == ["1", "3"]
        assert listening.gaps.drops == 1


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

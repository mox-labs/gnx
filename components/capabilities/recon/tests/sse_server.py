"""A loopback server of server-sent events, for the ``sse`` collector's tests: stdlib only, on
``127.0.0.1`` and a port the system picks, as ruling B10 asks.

Each ``GET /events`` sends the events that come after the ``Last-Event-ID`` it was asked from,
at most ``per_connection`` of them before it drops the connection (a forced disconnect), and
then, once every event is sent, holds the connection open with nothing more (a quiet
source) until the server stops. It records each ``Last-Event-ID`` it was asked from.
``status`` answers every request with that status and no events instead.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence


class SseServer:
    def __init__(
        self,
        events: Sequence[tuple[str | None, str]],
        *,
        per_connection: int | None = None,
        status: int = 200,
        preamble: str = "",
    ) -> None:
        self.events = list(events)
        self.per_connection = per_connection
        self.status = status
        self.preamble = preamble
        self.asked: list[str | None] = []
        self.headers: list[dict[str, str]] = []
        self.stopping = threading.Event()
        served = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                served.asked.append(self.headers.get("Last-Event-ID"))
                served.headers.append(dict(self.headers.items()))
                if self.path.split("?")[0] != "/events":
                    self.send_error(404)
                    return
                self.send_response(served.status)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if served.status != 200:
                    self.wfile.write(b"refused")
                    return
                self.wfile.write(served.preamble.encode())
                after = served.after(self.headers.get("Last-Event-ID"))
                for sent, (event_id, data) in enumerate(after):
                    if served.per_connection is not None and sent == served.per_connection:
                        return  # drop the connection mid-stream
                    head = f"id: {event_id}\n" if event_id is not None else ""
                    body = "".join(f"data: {line}\n" for line in data.split("\n"))
                    self.wfile.write(f"{head}{body}\n".encode())
                    self.wfile.flush()
                served.stopping.wait(30)

            def log_message(self, *args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def after(self, last: str | None) -> list[tuple[str | None, str]]:
        ids = [event_id for event_id, _ in self.events]
        if last is None or last not in ids:
            return self.events
        return self.events[ids.index(last) + 1 :]

    def __enter__(self) -> SseServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stopping.set()
        self._server.shutdown()
        self._server.server_close()

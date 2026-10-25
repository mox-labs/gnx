"""A loopback server of a paged list, for the pagination tests that go through recon's own
requester (ixian's round-4 section A): stdlib only, on ``127.0.0.1`` and a port the system
picks.

``GET /items?cursor=<n>`` answers page ``n`` (the first page without one) of ``records``
records, ``size`` to a page, as ``{"items": [...], "meta": {"next": <n + 1 or null>}}``. It
records each page asked for in ``asked`` (None for the first), each page it was sent a request
for in ``tried`` (refused ones too), and counts every request in ``hits``. ``data`` is the list
it serves, which a test may change between two requests (a record inserted ahead of a page
shifts it). ``refuse`` maps a page to the statuses its first requests get (``429`` with a
``Retry-After`` of ``retry_after`` seconds, say) before it is answered. ``hold`` is a page whose
request waits on ``release`` before it is answered; ``holding`` is set once one waits. With
``expiring``, a cursor lasts one ``generation``: one it gave before :meth:`expire` is refused
``400``, as an API whose cursors expire between two runs refuses them.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlsplit


class PagedServer:
    def __init__(
        self,
        records: int,
        size: int,
        *,
        refuse: dict[int, list[int]] | None = None,
        retry_after: int = 2,
        hold: int | None = None,
        expiring: bool = False,
    ) -> None:
        self.data = [{"id": n, "title": f"t{n}"} for n in range(records)]
        self.size = size
        self.refuse = {page: list(statuses) for page, statuses in (refuse or {}).items()}
        self.retry_after = retry_after
        self.hold = hold
        self.release = threading.Event()
        self.holding = threading.Event()
        self.asked: list[int | None] = []
        self.tried: list[int | None] = []
        self.hits = 0
        self.expiring = expiring
        self.generation = 0
        #: The generation each cursor was given in.
        self.given: dict[int, int] = {}
        served = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                served.hits += 1
                parts = urlsplit(self.path)
                if parts.path != "/items":
                    self.send_error(404)
                    return
                cursor = dict(parse_qsl(parts.query)).get("cursor")
                page = int(cursor) if cursor is not None else 0
                served.tried.append(None if cursor is None else page)
                stale = cursor is not None and served.given.get(page, -1) < served.generation
                if served.expiring and stale:
                    served.refuse.setdefault(page, []).insert(0, 400)
                refused = served.refuse.get(page)
                if refused:
                    self.send_response(refused.pop(0))
                    self.send_header("Retry-After", str(served.retry_after))
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                served.asked.append(None if cursor is None else page)
                if page == served.hold:
                    served.holding.set()
                    served.release.wait(30)
                self._send(served.page(page))

            def _send(self, body: dict[str, Any]) -> None:
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def page(self, page: int) -> dict[str, Any]:
        rows = self.data[page * self.size : (page + 1) * self.size]
        more = (page + 1) * self.size < len(self.data)
        if more:
            self.given[page + 1] = self.generation
        return {"items": rows, "meta": {"next": page + 1 if more else None}}

    def expire(self) -> None:
        """Every cursor given so far expires."""
        self.generation += 1

    def __enter__(self) -> PagedServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release.set()
        self._server.shutdown()
        self._server.server_close()

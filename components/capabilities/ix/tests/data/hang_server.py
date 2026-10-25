"""A test subject that never answers some requests: every ``--hang-every``-th request
(counted across all clients) waits far longer than any test's load, and the rest answer at
once. ``--hang-every 1`` never answers at all. ``GET /healthz`` always answers, and
``GET /stats`` answers ``{"received": n, "hung": n}``, the requests counted so far.

    python3 hang_server.py --port 18599 --hang-every 4
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: Long enough that a hung request outlives every test's load and request timeout.
HANG_S = 600.0


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # More connections waiting to be accepted than any test opens at once. With the default
    # of 5, a busy machine's slow accept loop drops a connection, the client tries again a
    # second later, and a 1s request timeout fails a request this server would have answered.
    request_queue_size = 128


def handler(every: int) -> type[BaseHTTPRequestHandler]:
    stats = {"received": 0, "hung": 0}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
            body = b"ok\n"
            if self.path == "/stats":
                with lock:
                    body = json.dumps(stats).encode()
            elif self.path != "/healthz":
                # Counted before it is answered, so a client that saw the answer finds it here.
                with lock:
                    stats["received"] += 1
                    hangs = stats["received"] % every == 0
                    stats["hung"] += hangs
                if hangs:
                    time.sleep(HANG_S)
            try:
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return None

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--hang-every", type=int, default=4)
    arguments = parser.parse_args()
    Server(("127.0.0.1", arguments.port), handler(arguments.hang_every)).serve_forever()


if __name__ == "__main__":
    main()

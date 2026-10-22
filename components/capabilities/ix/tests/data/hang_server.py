"""A test subject that never answers some requests: every ``--hang-every``-th request
(counted across all clients) waits far longer than any test's load, and the rest answer at
once. ``--hang-every 1`` never answers at all. ``GET /healthz`` always answers.

    python3 hang_server.py --port 18599 --hang-every 4
"""

from __future__ import annotations

import argparse
import itertools
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: Long enough that a hung request outlives every test's load and request timeout.
HANG_S = 600.0


def handler(every: int) -> type[BaseHTTPRequestHandler]:
    count = itertools.count(1)
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
            if self.path != "/healthz":
                with lock:
                    seen = next(count)
                if seen % every == 0:
                    time.sleep(HANG_S)
            body = b"ok\n"
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
    server = ThreadingHTTPServer(("127.0.0.1", arguments.port), handler(arguments.hang_every))
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()

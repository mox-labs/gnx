"""A tiny HTTP server that answers every request after a fixed delay.

A subject for the example experiments beside it: start it twice with the same delay and the
two are identical; start one with a longer delay and it is slower by a known amount.

    python3 server.py --port 18473 --delay-ms 40

``GET /healthz`` answers at once, so a harness can tell when the server is up. Every other
path sleeps ``--delay-ms`` and then answers ``ok``.
"""

from __future__ import annotations

import argparse
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def handler(delay_s: float) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keep connections open, as a real service would

        def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
            if self.path != "/healthz":
                time.sleep(delay_s)
            body = b"ok\n"
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client went away, as a load tool does when its time is up

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return None

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=18473)
    parser.add_argument("--delay-ms", type=float, default=40.0)
    arguments = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", arguments.port), handler(arguments.delay_ms / 1000))
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()

"""A runtime's connections belong to the event loop that opened them.

These run hardline's real HTTP transport and connection pool against a keep-alive server on
127.0.0.1 in this process: no network, no model. A runtime used from two ``asyncio.run`` calls
must answer each once, and ``aclose()`` in each loop must leave no connection open.
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import threading
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from hardline_helpers import DictSecrets

from hardline import (
    BackendError,
    Completion,
    ModelRuntime,
    ModelSpec,
    RawCompletion,
    Request,
    build_runtime,
)
from hardline.adapters._out import _http
from hardline.adapters._out.anthropic_native import AnthropicBackend
from hardline.adapters._out.openai_compat import OpenAICompatBackend

if TYPE_CHECKING:
    from collections.abc import Iterator

# The workspace guard (conftest.py) refuses hardline's network transport in every test, so that
# no test reaches a live model. It is taken here, at import, before the guard runs, and given
# back below only for requests to this file's loopback server: what is under test is the real
# transport's connection pool, which a MockTransport does not have.
_REAL_TRANSPORT = _http.network_transport


class _LoopbackOnly(httpx2.AsyncBaseTransport):
    def __init__(self, proxy: str | None = None) -> None:
        self._inner = _REAL_TRANSPORT(proxy)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.host != "127.0.0.1":
            raise AssertionError(f"a lifecycle test reached {request.url.host}, not its server")
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


_OPENAI_REPLY = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 0,
    "model": "m",
    "choices": [
        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}
_ANTHROPIC_REPLY = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "m",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


class _Server:
    """Answers both wire protocols over HTTP/1.1 keep-alive, and counts what it served."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        served = self.requests

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # keep the connection open between requests

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers["content-length"]))
                served.append(self.path)
                reply = _ANTHROPIC_REPLY if self.path.endswith("/messages") else _OPENAI_REPLY
                status = 503 if self.path.startswith("/down/") else 200
                body = json.dumps(reply if status == 200 else {"error": {"message": "down"}})
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *args: Any) -> None:
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join()


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Server]:
    srv = _Server()
    monkeypatch.setattr(_http, "network_transport", _LoopbackOnly)
    yield srv
    srv.close()


def _runtime(backend: str, url: str, retries: int = 0) -> ModelRuntime:
    """One row on ``backend``; with retries 0 a failed attempt is the caller's failure."""
    row: dict[str, Any] = {"backend": backend, "model": "m", "family": "x", "retries": retries}
    if backend == "anthropic":
        row |= {"base_url": url, "api_key": "env:K"}
    else:
        row |= {"base_url": f"{url}/v1", "local": True}
    return build_runtime(
        {"models": {"q": row}},
        backends={"openai-compat": OpenAICompatBackend(), "anthropic": AnthropicBackend()},
        secrets=DictSecrets({"env:K": "loopback-key"}),
        sleep=_no_sleep,
    )


async def _no_sleep(seconds: float) -> None:
    pass


BACKENDS = ["openai-compat", "anthropic"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_runtime_used_from_two_event_loops_answers_each_once(
    backend: str, server: _Server, caplog: pytest.LogCaptureFixture
) -> None:
    rt = _runtime(backend, server.url)

    # Neither loop closes its connections, so each is left open until collected: the leak the
    # warning asserted below names. Collect them inside this test, not in a later one.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        first = asyncio.run(rt.complete("q", "hi"))
        second = asyncio.run(rt.complete("q", "hi"))
        del rt
        gc.collect()

    assert (first.text, first.retries) == ("ok", 0)
    assert (second.text, second.retries) == ("ok", 0)
    assert len(server.requests) == 2  # one request per answer: none duplicated, none billed twice
    # The first loop ended without aclose(), so its connection could not be closed. hardline
    # says so, with the fix, rather than leaving it to a ResourceWarning nobody sees.
    assert any("aclose()" in r.getMessage() for r in caplog.records if r.levelname == "WARNING")


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_runtime_closed_in_each_loop_leaves_no_connection_open(
    backend: str, server: _Server, caplog: pytest.LogCaptureFixture
) -> None:
    rt = _runtime(backend, server.url)

    async def call() -> Completion:
        async with rt:
            return await rt.complete("q", "hi")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        answers = [asyncio.run(call()), asyncio.run(call())]
        gc.collect()

    assert [a.text for a in answers] == ["ok", "ok"]
    assert len(server.requests) == 2
    assert [str(w.message) for w in caught if issubclass(w.category, ResourceWarning)] == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize("backend", BACKENDS)
async def test_no_retry_hides_behind_the_runtimes_own(backend: str, server: _Server) -> None:
    # The provider SDKs retried a 5xx themselves unless told not to. httpx2 retries no status,
    # and the transport is built with no connection retries: a row's `retries` is the only
    # policy, so one retry is two requests at the server.
    rt = _runtime(backend, f"{server.url}/down", retries=1)
    async with rt:
        with pytest.raises(BackendError) as e:
            await rt.complete("q", "hi")
    assert e.value.reason == "unavailable"
    assert len(server.requests) == 2


async def test_aclose_reaches_every_backend_that_holds_connections() -> None:
    class Pooled:
        structured_modes = frozenset({"prompt"})
        closed = 0

        async def complete(
            self, spec: ModelSpec, request: Request, api_key: str | None
        ) -> RawCompletion:
            raise NotImplementedError

        async def aclose(self) -> None:
            self.closed += 1

    pooled = Pooled()
    rt = build_runtime(
        {"models": {"p": {"backend": "pooled", "model": "m", "family": "x"}}},
        backends={"pooled": pooled},
    )
    async with rt:
        pass
    await rt.aclose()
    assert pooled.closed == 2  # aclose() may be called again; the runtime stays usable


async def test_an_https_proxy_is_verified_against_the_system_store_whatever_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    # The TLS hop to an https:// proxy was given no SSL context, so httpcore2 built its own from
    # SSL_CERT_FILE and SSL_CERT_DIR, whatever trust_env said: a variable chose what the proxy
    # was trusted against. Here the variable names a file that is not there, which only a
    # context read from the environment would open.
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "planted.pem"))

    async def hang_up(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server = await asyncio.start_server(hang_up, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        transport = _REAL_TRANSPORT(f"https://127.0.0.1:{port}")
        async with httpx2.AsyncClient(transport=transport, trust_env=False) as client:
            with pytest.raises(httpx2.ConnectError) as e:
                await client.get("https://provider.invalid/v1")
    assert "planted.pem" not in str(e.value)

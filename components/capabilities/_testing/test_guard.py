"""The workspace guard (``conftest.py``) refuses every route from a test to a live provider.

Each test takes one route there and expects the guard to refuse it, and the refusal to be
recorded for the teardown check, which the test then clears. The address is in TEST-NET-3
(203.0.113.0/24, RFC 5737), which routes nowhere, so a guard that let the connection through
fails the test without reaching anyone.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from typing import TYPE_CHECKING

import httpx2
import pytest

if TYPE_CHECKING:
    from pathlib import Path

REMOTE = "203.0.113.7"
URL = f"http://{REMOTE}/v1"


def _refused(refused_live_calls: list[str], what: str) -> None:
    """The guard refused ``what``, once or more (a client may try again), and nothing else;
    the record is cleared, as expected."""
    assert refused_live_calls, "nothing was refused"
    assert all(what in call for call in refused_live_calls), refused_live_calls
    refused_live_calls.clear()


#: What a client raises when its connection is refused: each wraps the guard's AssertionError
#: its own way (httpcore in an exception group, fastmcp in its own error), so the evidence a
#: test reads is the guard's record.
WRAPPED = BaseException


def test_a_socket_may_connect_to_loopback_and_nowhere_else(refused_live_calls: list[str]) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with socket.create_connection(listener.getsockname(), timeout=5):
            pass
    assert refused_live_calls == []
    with socket.socket() as sock, pytest.raises(AssertionError, match="loopback"):
        sock.connect((REMOTE, 80))
    _refused(refused_live_calls, REMOTE)
    with socket.socket() as sock, pytest.raises(AssertionError, match="loopback"):
        sock.connect_ex((REMOTE, 80))
    _refused(refused_live_calls, REMOTE)


async def test_an_httpx2_client_outside_hardlines_transport_is_refused(
    refused_live_calls: list[str],
) -> None:
    # The client pydantic-ai's migrated providers, fastmcp's remote transport and recon's
    # requester each build; here built bare, so hardline's guarded transport is not in its path.
    async with httpx2.AsyncClient(trust_env=False) as client:
        with pytest.raises(WRAPPED):
            await client.get(URL)
    _refused(refused_live_calls, REMOTE)


async def test_pydantic_ais_own_provider_client_is_refused(refused_live_calls: list[str]) -> None:
    # The client a pydantic-ai provider builds when it is given none. No provider SDK is
    # installed today, so a provider cannot be built; the client it would build can.
    from pydantic_ai.models import create_async_httpx2_client

    async with create_async_httpx2_client() as client:
        with pytest.raises(WRAPPED):
            await client.get(URL)
    _refused(refused_live_calls, REMOTE)


async def test_fastmcps_remote_client_is_refused(refused_live_calls: list[str]) -> None:
    from fastmcp import Client

    with pytest.raises(WRAPPED):
        async with Client(f"{URL}/mcp", timeout=5, init_timeout=5):
            pass
    _refused(refused_live_calls, REMOTE)


def _k6(tmp_path: Path) -> str:
    """A stand-in ``k6`` that would exit 0, so only the guard can make starting it fail."""
    k6 = tmp_path / "k6"
    k6.write_text("#!/bin/sh\nexit 0\n")
    k6.chmod(0o755)
    return str(k6)


def test_k6_may_load_loopback_and_nowhere_else(
    tmp_path: Path, refused_live_calls: list[str]
) -> None:
    def start(url: str) -> int:
        target = {"IX_LOAD": json.dumps({"url": url})}
        return subprocess.run([_k6(tmp_path), "run"], env={**os.environ, **target}).returncode

    assert start("http://127.0.0.1:8080/") == 0
    assert refused_live_calls == []
    with pytest.raises(AssertionError, match="loopback"):
        start(URL)
    _refused(refused_live_calls, REMOTE)
    with pytest.raises(AssertionError, match="loopback"):
        subprocess.run([_k6(tmp_path), "run", "--address", f"{REMOTE}:6565"])
    _refused(refused_live_calls, REMOTE)


async def test_ix_loading_a_remote_target_is_refused(
    tmp_path: Path, refused_live_calls: list[str]
) -> None:
    # The route a test of ix's http-load takes: its k6 adapter, through matrix's process groups.
    from ix.adapters._out.k6_load import K6Load
    from ix.adapters._out.matrix_process import MatrixLauncher
    from ix.domain.run import Target

    options = {"target": URL, "load_model": "closed", "concurrency": 1, "duration": "1s"}
    with pytest.raises(AssertionError, match="loopback"):
        await K6Load(MatrixLauncher(), binary=_k6(tmp_path)).apply(
            options, Target(subject="remote"), seed=0
        )
    _refused(refused_live_calls, REMOTE)


@pytest.mark.network
def test_a_test_marked_network_reaches_past_the_guard(refused_live_calls: list[str]) -> None:
    # recon's live tests (`just recon-live`) call third-party APIs on purpose, and say so.
    with socket.socket() as sock:
        sock.setblocking(False)
        sock.connect_ex((REMOTE, 80))  # in progress, to an address that routes nowhere
    assert refused_live_calls == []

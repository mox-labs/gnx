"""Clients bound to the event loop that opened them — the provider adapters' one lifecycle.

An async HTTP client's connection pool belongs to the event loop of its first request: its
sockets are that loop's transports. Used from a second loop (a second ``asyncio.run``), the
pooled connection fails as a connection error *after* the request was written, so the provider
counts a request the caller is told never arrived, and a retry sends it again. So a client is
cached per running loop, never across loops, and pooled only within one.

A connection can be closed only in its own loop, while that loop runs. ``aclose()`` closes the
running loop's clients, and the next call opens new ones. A client whose loop ended first cannot
be closed any more: it is dropped with a warning that names the fix, since its sockets stay open
until the garbage collector finds them.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Hashable

logger = logging.getLogger("hardline")


class LoopClients[C]:
    """``key`` -> client, one set per running event loop.

    ``close`` is how a client is closed (``client.close()`` for the provider SDKs).
    """

    def __init__(self, close: Callable[[C], Awaitable[None]]) -> None:
        self._close = close
        self._loops: dict[asyncio.AbstractEventLoop, dict[Hashable, C]] = {}

    def get(self, key: Hashable, make: Callable[[], C]) -> C:
        """The running loop's client for ``key``, made on first use. Call it inside a loop."""
        loop = asyncio.get_running_loop()
        self._drop_ended()
        clients = self._loops.setdefault(loop, {})
        client = clients.get(key)
        if client is None:
            client = clients[key] = make()
        return client

    async def aclose(self) -> None:
        """Close the running loop's clients; a later ``get`` opens new ones."""
        self._drop_ended()
        for client in self._loops.pop(asyncio.get_running_loop(), {}).values():
            await self._close(client)

    def _drop_ended(self) -> None:
        for loop in [loop for loop in list(self._loops) if loop.is_closed()]:
            dropped = self._loops.pop(loop)
            logger.warning(
                "%d model client(s) were left open by an event loop that has ended; their "
                "connections cannot be closed now. Fix: await runtime.aclose(), or use "
                "`async with runtime`, before the loop that made the calls ends",
                len(dropped),
            )

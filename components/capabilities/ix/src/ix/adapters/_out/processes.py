"""Starting, readying and stopping the programs a run needs.

:class:`Launcher` is what the adapters here ask of whatever starts a child process: start it
so that it and everything it forks can be ended together, and end it. The composition root
supplies the one that does it.

:func:`wait_ready` and :func:`answers` are how a started program is known to be up: a URL
that answers, or failing that a port that accepts a connection.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlsplit

import httpx

if TYPE_CHECKING:
    from collections.abc import Sequence

#: How often a starting program is asked whether it is up.
POLL_S = 0.05


class Launcher(Protocol):
    """Starts a child in a group of its own, and ends the whole group."""

    async def start(self, argv: Sequence[str], **options: Any) -> asyncio.subprocess.Process:
        """Start ``argv``. ``options`` are those of ``asyncio.create_subprocess_exec``."""
        ...

    async def stop(self, process: asyncio.subprocess.Process, *, grace_s: float = 5.0) -> None:
        """End the process and everything it started: politely, then for good."""
        ...


async def answers(url: str) -> bool:
    """Whether an HTTP GET of ``url`` is answered now (any status below 500, a redirect
    included)."""
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(url)
    except (httpx.HTTPError, httpx.InvalidURL):
        return False
    return response.status_code < 500


async def accepts(url: str) -> bool:
    """Whether the host and port of ``url`` accept a connection now."""
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(parts.hostname or "127.0.0.1", port), timeout=2
        )
    except (OSError, TimeoutError):
        return False
    writer.close()
    return True


async def is_up(*, ready: str | None, url: str | None = None) -> bool:
    """Whether a program is up: its ``ready`` URL answers, or (with none) its ``url``
    accepts a connection. With neither there is nothing to ask and the answer is yes."""
    if ready:
        return await answers(ready)
    if url:
        return await accepts(url)
    return True


async def wait_ready(
    process: asyncio.subprocess.Process,
    *,
    ready: str | None,
    url: str | None = None,
    timeout_s: float,
) -> str:
    """Wait until the program is up. Returns ``""`` when it is, ``"exited"`` when the
    process ended first and ``"not_ready"`` when ``timeout_s`` passed."""
    loop = asyncio.get_running_loop()
    until = loop.time() + timeout_s
    while True:
        if process.returncode is not None:
            return "exited"
        if await is_up(ready=ready, url=url):
            # A program that died as it bound its port can leave an older one answering.
            return "exited" if process.returncode is not None else ""
        if loop.time() >= until:
            return "not_ready"
        await asyncio.sleep(POLL_S)

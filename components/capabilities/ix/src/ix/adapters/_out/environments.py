"""The environments ix can bring up: nothing, and programs started beside the subjects."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from ix.adapters._out.processes import answers, is_up, startable, wait_ready
from ix.adapters._out.subjects import STARTUP_TIMEOUT_S
from ix.domain.kind import seconds
from ix.domain.run import HarnessError

if TYPE_CHECKING:
    from ix.adapters._out.processes import Launcher
    from ix.domain.experiment import EnvironmentConfig
    from ix.domain.ports import Requirement, Scope
    from ix.domain.run import Bindings


class NoEnvironment:
    """Nothing to bring up."""

    async def up(self, environment: EnvironmentConfig, *, base: str, scope: Scope) -> Bindings:
        return {}

    async def lost(self, bindings: Bindings) -> str | None:
        return None


class ProcessEnvironment:
    """Programs started before the trials and stopped when the run ends.

    Option ``services``: name to ``{command, url, ready, cwd, env, startup_timeout}``. Each
    is started in order and waited for; its stop is deferred with the run, so it comes down
    however the run ends. A service that does not come up, or that is found down between
    trials, fails the run: nothing measured without it would mean what the experiment says.

    Each service is bound with where it listens, its ``url`` (or, without one, the address
    of its ``ready`` URL), as ``host``, ``port`` and ``url``, which a subject or a probe names
    as ``{<service>.url}``.
    """

    def __init__(self, launcher: Launcher) -> None:
        self._launcher = launcher
        #: Each process started, by pid: whether it is still up is asked of the process
        #: itself, never of a pid the system may have given to another since.
        self._started: dict[int, asyncio.subprocess.Process] = {}

    def requirements(self, options: Mapping[str, Any], *, base: str) -> list[Requirement]:
        """That each service's program can be started here."""
        services = options.get("services")
        found: list[Requirement] = []
        for service in services.values() if isinstance(services, Mapping) else ():
            command = service.get("command") if isinstance(service, Mapping) else None
            if isinstance(command, list | tuple) and command:
                program = startable(str(command[0]), base=base, cwd=str(service.get("cwd") or "."))
                if program is not None:
                    found.append(program)
        return found

    async def up(self, environment: EnvironmentConfig, *, base: str, scope: Scope) -> Bindings:
        bindings: Bindings = {}
        for name, service in environment.options["services"].items():
            command = [str(part) for part in service["command"]]
            ready = service.get("ready")
            timeout_s = seconds(service.get("startup_timeout")) or STARTUP_TIMEOUT_S
            if ready and await is_up(ready=ready):
                raise HarnessError(
                    "address_in_use",
                    f"{ready} already answers before service {name} was started",
                )
            added = {key: str(value) for key, value in (service.get("env") or {}).items()}
            try:
                process = await self._launcher.start(
                    command,
                    cwd=str(Path(base, service.get("cwd") or ".")),
                    env={**os.environ, **added},
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except OSError as error:
                raise HarnessError(
                    "not_started", f"service {name}: {command[0]!r} could not be started: {error}"
                ) from error
            scope.defer(partial(self._launcher.stop, process))
            self._started[process.pid] = process
            state = await wait_ready(process, ready=ready, timeout_s=timeout_s)
            if state:
                raise HarnessError(
                    state,
                    f"service {name} "
                    + (
                        f"exited with code {process.returncode} before it was up"
                        if state == "exited"
                        else f"was not up at {ready} after {timeout_s:g}s"
                    ),
                )
            bindings[name] = {
                "pid": process.pid,
                **address(service.get("url") or ready),
                **({"ready": ready} if ready else {}),
            }
        return bindings

    async def lost(self, bindings: Bindings) -> str | None:
        for name, service in bindings.items():
            process = self._started.get(int(service["pid"]))
            if process is None or process.returncode is not None:
                return f"service {name} exited"
            ready = service.get("ready")
            if ready and not await answers(ready):
                return f"service {name} no longer answers at {ready}"
        return None


def address(url: str | None) -> dict[str, Any]:
    """``host``, ``port`` and ``url`` (scheme, host and port) of where a service listens, from
    a URL it answers at; nothing for none."""
    if not url:
        return {}
    parts = urlsplit(url)
    if not parts.hostname:
        return {}
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return {}
    scheme = parts.scheme or "http"
    host = parts.hostname
    shown = f"[{host}]" if ":" in host else host
    return {"host": host, "port": port, "url": f"{scheme}://{shown}:{port}"}

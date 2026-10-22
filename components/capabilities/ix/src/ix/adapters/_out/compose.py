"""The ``compose`` environment: a docker compose project brought up before the trials and down
after them, for experiments whose subjects need several services around them.

Every docker command goes through the :class:`~ix.adapters._out.processes.Launcher`, so a
command that hangs is ended with its process group, and a test can put a fake ``docker`` on
the path.

**Up.** ``docker compose -f <file> -p <project> up -d --wait --wait-timeout N [services]``:
compose starts the services and waits until each is running, and healthy where it declares a
healthcheck. ``down -v --remove-orphans`` is deferred with the run **before** ``up`` starts,
so a stack that came up only in part, or whose ``up`` was cancelled, still comes down.

**The project.** Without a ``project`` option it is named for the run and this process
(``ix-<run id>-<pid>``), so two runs never share one, and a stack a killed run left behind
(its process gone, the same compose file) is swept before ``up``. A ``project`` the
experiment names is refused when it already has containers: whose they are cannot be told.

**Bindings.** Each service that is running once ``up`` returns is bound with its first
published TCP port (by target port) as ``host``, ``port`` and ``url``, and with the image it
runs and that image's id (``image``, ``image_id``): the id is what a tag resolved to, so the
run's bindings row records exactly what was measured against.

**Liveness.** After each trial, ``compose ps --all`` says whether each bound service is still
running, and not unhealthy.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ix.adapters._out.processes import startable
from ix.domain.kind import placeholders, seconds
from ix.domain.ports import Requirement
from ix.domain.run import HarnessError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ix.adapters._out.processes import Launcher
    from ix.domain.experiment import EnvironmentConfig
    from ix.domain.ports import Scope
    from ix.domain.run import Bindings

#: How long the stack may take to come up, and healthy, when the experiment does not say.
WAIT_TIMEOUT_S = 120.0
#: How long a docker command other than ``up`` and ``down`` may take.
COMMAND_TIMEOUT_S = 60.0
#: How long ``down`` may take; the run's release of the environment is given longer.
DOWN_TIMEOUT_S = 120.0
#: How long the daemon has to answer the preflight.
DAEMON_TIMEOUT_S = 10.0
#: How long each container is given to stop on ``down`` before it is killed.
STOP_TIMEOUT_S = 10

#: A project ix named, and the pid of the process that named it.
_OURS = re.compile(r"^ix-.+-(\d+)$")


def _tail(text: str, limit: int = 400) -> str:
    text = text.strip()
    return text[-limit:] if text else "(it said nothing)"


def _project(run_id: str) -> str:
    """The project a run's stack is named, without a ``project`` option: compose takes
    lowercase letters, digits, ``-`` and ``_``, starting with a letter or digit."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", run_id.lower()).strip("-_") or "run"
    return f"ix-{slug}-{os.getpid()}"


def _alive(pid: int) -> bool:
    """Whether a process with ``pid`` is running on this host; one another user owns is."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _rows(text: str) -> list[dict[str, Any]]:
    """What ``--format json`` printed: one JSON array, or one object a line, as compose
    versions differ."""
    text = text.strip()
    if not text:
        return []
    try:
        found = json.loads(text)
    except json.JSONDecodeError:
        found = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(found, dict):
        found = [found]
    return [row for row in found if isinstance(row, dict)]


def _labels(row: Mapping[str, Any]) -> dict[str, str]:
    labels = row.get("Labels") or ""
    if isinstance(labels, Mapping):
        return {str(k): str(v) for k, v in labels.items()}
    pairs = (item.partition("=") for item in str(labels).split(","))
    return {key: value for key, _, value in pairs if key}


def _binding(row: Mapping[str, Any]) -> dict[str, Any]:
    """One running service as it is bound: its first published TCP port by target port, and
    the image it runs."""
    found: dict[str, Any] = {}
    published = sorted(
        (
            p
            for p in row.get("Publishers") or []
            if isinstance(p, Mapping)
            and int(p.get("PublishedPort") or 0) > 0
            and str(p.get("Protocol") or "tcp") == "tcp"
        ),
        key=lambda p: (int(p.get("TargetPort") or 0), ":" in str(p.get("URL") or "")),
    )
    if published:
        first = published[0]
        host = str(first.get("URL") or "")
        host = "127.0.0.1" if host in ("", "0.0.0.0") else "::1" if host == "::" else host
        port = int(first["PublishedPort"])
        shown = f"[{host}]" if ":" in host else host
        found |= {"host": host, "port": port, "url": f"http://{shown}:{port}"}
    if row.get("Image"):
        found["image"] = str(row["Image"])
    image_id = _labels(row).get("com.docker.compose.image")
    if image_id:
        found["image_id"] = image_id
    return found


class ComposeEnvironment:
    """A docker compose project, up for the run: see the module docstring.

    Options: ``file`` (the compose file, relative to the experiment), ``project``,
    ``services`` (all of the file's when absent), ``wait_timeout`` (120s), ``profiles``,
    ``env_file``, ``build`` (build images first) and ``pull`` (``always``, ``missing`` or
    ``never``). A :class:`~ix.domain.ports.Preflight` (docker, its daemon, compose, the file)
    and a :class:`~ix.domain.ports.Liveness`.
    """

    def __init__(self, launcher: Launcher, binary: str = "docker") -> None:
        self._launcher = launcher
        self._binary = binary
        #: ``docker compose -f <file> -p <project> ...``, once ``up`` has named the project.
        self._compose: list[str] = []

    # --- before anything starts ---

    def requirements(self, options: Mapping[str, Any], *, base: str) -> list[Requirement]:
        """docker on PATH, its daemon answering, compose installed, and the files the
        options name there."""
        found: list[Requirement] = []
        docker = startable(self._binary, base=base)
        if docker is not None:
            found.append(docker)
        if docker is not None and docker.holds:
            found.append(
                self._answers(
                    "the docker daemon answers",
                    [self._binary, "info", "--format", "{{.ServerVersion}}"],
                    fix="start docker (Docker Desktop, OrbStack, colima or the docker "
                    "service), or point DOCKER_HOST at a daemon that is running",
                )
            )
            found.append(
                self._answers(
                    "docker compose is installed",
                    [self._binary, "compose", "version"],
                    fix="install the compose plugin (docker compose version should answer)",
                )
            )
        for key in ("file", "env_file"):
            named = options.get(key)
            if isinstance(named, str) and named and not placeholders(named):
                at = Path(base, named)
                there = at.is_file()
                found.append(
                    Requirement(
                        need=f"the compose {key.replace('_', ' ')} {named!r} is there",
                        holds=there,
                        why="" if there else f"{named!r} is not a file at {at}",
                        fix=""
                        if there
                        else "correct the path; it is relative to the experiment's directory",
                    )
                )
        return found

    def _answers(self, need: str, argv: Sequence[str], *, fix: str) -> Requirement:
        try:
            done = subprocess.run(  # noqa: S603  (an argument list, never a shell)
                list(argv),
                capture_output=True,
                text=True,
                timeout=DAEMON_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return Requirement(need=need, holds=False, why=str(error), fix=fix)
        if done.returncode != 0:
            return Requirement(need=need, holds=False, why=_tail(done.stderr), fix=fix)
        return Requirement(need=need, holds=True)

    # --- the run ---

    async def up(self, environment: EnvironmentConfig, *, base: str, scope: Scope) -> Bindings:
        options = environment.options
        file = str(Path(base, str(options["file"])).resolve())
        named = options.get("project")
        project = str(named) if named else _project(scope.run_id)
        compose = [self._binary, "compose", "-f", file, "-p", project]
        for profile in options.get("profiles") or ():
            compose += ["--profile", str(profile)]
        if options.get("env_file"):
            compose += ["--env-file", str(Path(base, str(options["env_file"])))]
        if named:
            await self._refuse_taken(compose, project)
        else:
            await self._sweep(file, scope)
        self._compose = compose
        # Before up: a stack that comes up in part, or whose up is cancelled, comes down.
        scope.defer(self._down)
        wait_s = seconds(options.get("wait_timeout")) or WAIT_TIMEOUT_S
        argv = [*compose, "up", "-d", "--wait", "--wait-timeout", str(math.ceil(wait_s))]
        if options.get("build"):
            argv.append("--build")
        if options.get("pull"):
            argv += ["--pull", str(options["pull"])]
        argv += [str(service) for service in options.get("services") or ()]
        code, _, said = await self._run(argv, timeout_s=wait_s + COMMAND_TIMEOUT_S)
        if code != 0:
            raise HarnessError(
                "not_ready",
                f"docker compose up of project {project} failed (exit {code}): {_tail(said)}",
            )
        code, out, said = await self._run([*compose, "ps", "--format", "json"])
        if code != 0:
            raise HarnessError("tool_failed", f"docker compose ps failed: {_tail(said)}")
        return {
            str(row["Service"]): _binding(row)
            for row in sorted(_rows(out), key=lambda row: str(row.get("Service")))
            if row.get("Service") and row.get("State") == "running"
        }

    async def lost(self, bindings: Bindings) -> str | None:
        if not self._compose:
            return "the compose project was never brought up"
        code, out, said = await self._run([*self._compose, "ps", "--all", "--format", "json"])
        if code != 0:
            return f"docker compose ps failed: {_tail(said)}"
        states = {str(row.get("Service")): row for row in _rows(out)}
        for name in bindings:
            row = states.get(name)
            if row is None:
                return f"service {name} is gone"
            if row.get("State") != "running":
                return f"service {name} is {row.get('State')} (exit code {row.get('ExitCode')})"
            if row.get("Health") == "unhealthy":
                return f"service {name} is unhealthy"
        return None

    async def _down(self) -> None:
        argv = [*self._compose, "down", "-v", "--remove-orphans", "--timeout", str(STOP_TIMEOUT_S)]
        code, _, said = await self._run(argv, timeout_s=DOWN_TIMEOUT_S)
        if code != 0:
            raise HarnessError(
                "tool_failed",
                f"docker compose down failed (exit {code}); its containers may be left "
                f"running: {_tail(said)}",
            )

    async def _refuse_taken(self, compose: Sequence[str], project: str) -> None:
        code, out, _ = await self._run([*compose, "ps", "--all", "-q"])
        if code == 0 and out.strip():
            raise HarnessError(
                "address_in_use",
                f"compose project {project} already has containers before this run brought it "
                f"up; whose they are cannot be told. If a killed run left them: "
                f"docker compose -p {project} down -v",
            )

    async def _sweep(self, file: str, scope: Scope) -> None:
        """Bring down every project ix named for this compose file whose process is gone: a
        stack a killed run left behind."""
        code, out, _ = await self._run([self._binary, "compose", "ls", "--all", "--format", "json"])
        if code != 0:
            return
        for row in _rows(out):
            name = str(row.get("Name") or "")
            ours = _OURS.match(name)
            files = str(row.get("ConfigFiles") or "").split(",")
            if ours is None or file not in files or _alive(int(ours.group(1))):
                continue
            argv = [self._binary, "compose", "-p", name, "down", "-v", "--remove-orphans"]
            code, _, _ = await self._run(argv, timeout_s=DOWN_TIMEOUT_S)
            scope.emit("ix.environment.swept", project=name, ok=code == 0)

    async def _run(
        self, argv: Sequence[str], *, timeout_s: float = COMMAND_TIMEOUT_S
    ) -> tuple[int, str, str]:
        """Run a docker command to its end: its exit code and what it wrote. Raises
        :class:`HarnessError` when docker cannot be started or the command overruns; a
        cancellation ends the command's process group before it propagates."""
        try:
            process = await self._launcher.start(
                list(argv),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise HarnessError(
                "tool_missing", f"{self._binary!r} could not be started: {error}"
            ) from error
        try:
            out, said = await asyncio.wait_for(process.communicate(), timeout_s)
        except TimeoutError:
            await self._launcher.stop(process)
            raise HarnessError("timeout", f"`{' '.join(argv)}` overran {timeout_s:g}s") from None
        except BaseException:
            await self._launcher.stop(process)
            raise
        return (
            process.returncode or 0,
            out.decode(errors="replace"),
            said.decode(errors="replace"),
        )

"""The subject kinds ix can reach: a program it starts, and an endpoint that is already up.

``process`` starts the subject's command for one trial, waits until it is up, and ends its
whole process group when the trial is over. ``http`` starts nothing: the subject is a URL.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ix.adapters._out.processes import is_up, startable, wait_ready
from ix.domain.kind import placeholders, seconds
from ix.domain.ports import Requirement
from ix.domain.run import HarnessError, SubjectError, Target

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from ix.adapters._out.processes import Launcher
    from ix.domain.experiment import SubjectConfig

#: How long a started program has to come up when the subject does not say.
STARTUP_TIMEOUT_S = 30.0


def _said(path: str, limit: int = 400) -> str:
    """``: <the last of what the program wrote to stderr>``, or nothing when it wrote none."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return f": {text[-limit:]}" if text else ""


class ProcessSubject:
    """A program started for each trial and stopped after it.

    Options: ``command`` (the program and its arguments), ``url`` (where it listens),
    ``ready`` (a URL that answers once it is up; without one, ``url`` accepting a connection
    is taken as up), ``cwd`` (relative to the experiment), ``env`` (added to the
    environment) and ``startup_timeout``.

    Offers ``pid`` (the process, which leads its own group) and ``url``.

    Whose failure: a program that is not there is refused before the run starts. A command
    that cannot be started all the same, a program that exits before it is up, one that is
    not up in time, and one that dies during the probe are the subject's. An address that
    already answers before the subject was started is the harness's: something else holds
    it, and measuring that would measure the wrong thing.
    """

    def __init__(self, launcher: Launcher) -> None:
        self._launcher = launcher

    def requirements(self, options: Mapping[str, Any], *, base: str) -> list[Requirement]:
        """That its program can be started here."""
        command = options.get("command")
        if not isinstance(command, list | tuple) or not command:
            return []  # the kind's declaration reports it
        found = startable(str(command[0]), base=base, cwd=str(options.get("cwd") or "."))
        return [] if found is None else [found]

    @asynccontextmanager
    async def reach(self, name: str, subject: SubjectConfig, *, base: str) -> AsyncIterator[Target]:
        options = subject.options
        command = [str(part) for part in options["command"]]
        url = options.get("url")
        ready = options.get("ready")
        timeout_s = seconds(options.get("startup_timeout")) or STARTUP_TIMEOUT_S
        cwd = str(Path(base, options.get("cwd") or "."))
        environment = {**os.environ, **{k: str(v) for k, v in (options.get("env") or {}).items()}}

        if (ready or url) and await is_up(ready=ready, url=url):
            raise HarnessError(
                "address_in_use",
                f"{ready or url} already answers before {name} was started; something else "
                "is listening there (a subject left by a killed run?)",
            )

        with tempfile.TemporaryDirectory(prefix="ix-subject-") as scratch:
            errors = str(Path(scratch, "stderr"))
            with open(errors, "wb") as sink:
                try:
                    process = await self._launcher.start(
                        command,
                        cwd=cwd,
                        env=environment,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=sink,
                    )
                except OSError as error:
                    raise SubjectError(
                        "not_started", f"{name}: {command[0]!r} could not be started: {error}"
                    ) from error
            died: int | None = None
            try:
                state = await wait_ready(process, ready=ready, url=url, timeout_s=timeout_s)
                if state == "exited":
                    raise SubjectError(
                        "exited",
                        f"{name} exited with code {process.returncode} before it was up"
                        + _said(errors),
                    )
                if state:
                    raise SubjectError(
                        "not_ready", f"{name} was not up at {ready or url} after {timeout_s:g}s"
                    )
                facets: dict[str, Any] = {"pid": process.pid}
                if url:
                    facets["url"] = url
                yield Target(subject=name, facets=facets)
                died = process.returncode
            finally:
                await self._launcher.stop(process)
            if died is not None:
                raise SubjectError(
                    "exited", f"{name} exited with code {died} during the probe" + _said(errors)
                )


def _token(auth: str) -> str:
    """The bearer token that ``auth`` (``env:NAME``) names. Raises :class:`HarnessError`
    (``auth_missing``) when it is not of that form or the variable is not set."""
    scheme, _, variable = auth.partition(":")
    if scheme != "env" or not variable:
        raise HarnessError(
            "auth_missing", f"auth is {auth!r}; write env:<NAME>, the variable that holds a token"
        )
    token = os.environ.get(variable)
    if not token:
        raise HarnessError("auth_missing", f"auth names {variable}, which is not set")
    return token


class HttpSubject:
    """An endpoint that is already running.

    Options: ``url`` (its base URL), ``headers`` (sent with every request) and ``auth``
    (``env:NAME``: the variable that holds a bearer token; the token itself is never
    written into the experiment or the record).

    Offers ``url`` and ``headers``. An ``auth`` that names no token is refused before the
    run starts.
    """

    def requirements(self, options: Mapping[str, Any], *, base: str) -> list[Requirement]:
        """That the variable ``auth`` names is set. Neither the token nor what ``auth``
        holds when it is not of the form ``env:NAME`` is repeated: it may be a token."""
        auth = options.get("auth")
        if not auth or placeholders(auth):
            return []
        scheme, _, variable = str(auth).partition(":")
        if scheme != "env" or not variable:
            return [
                Requirement(
                    need="auth names the variable that holds a bearer token",
                    holds=False,
                    why="auth is not of the form env:<NAME>",
                    fix="write auth: env:<NAME>, the variable that holds a token; never the "
                    "token itself",
                )
            ]
        held = bool(os.environ.get(variable))
        return [
            Requirement(
                need=f"variable {variable} is set (auth)",
                holds=held,
                why="" if held else f"auth names {variable}, which is not set",
                fix="" if held else f"set {variable} to the bearer token",
            )
        ]

    @asynccontextmanager
    async def reach(self, name: str, subject: SubjectConfig, *, base: str) -> AsyncIterator[Target]:
        options = subject.options
        headers = {str(k): str(v) for k, v in (options.get("headers") or {}).items()}
        auth = options.get("auth")
        if auth:
            headers["Authorization"] = f"Bearer {_token(str(auth))}"
        yield Target(
            subject=name, facets={"url": str(options["url"]).rstrip("/"), "headers": headers}
        )

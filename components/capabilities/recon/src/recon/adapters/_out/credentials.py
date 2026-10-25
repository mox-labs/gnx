"""Where a source's credential comes from, resolved the one way every collector type resolves
it (ruling A1; the grammar is :mod:`recon.domain.credentials`).

* ``env:NAME``: the variable, from the environment the mission declares for the type
  (``CollectorContext.env``); unset, there is none, and the request goes without it, which the
  plan warns about.
* ``file:PATH``: the file's text, stripped, a relative path being the mission directory's. A
  file its group or others can read is refused (``config``, with the ``chmod`` that fixes it):
  a credential readable by every account on the host is not kept as one, and a scheduled
  survey reads it unattended.
* ``headers_helper``: a command, run through the shell port within the source's ``timeout``
  and the survey's deadline, whose standard output is one JSON object of header names to
  string values. One that fails is ``auth`` (no credential was sent); one that prints
  anything else is ``config``.

:class:`Credentials` holds what a survey resolved for each source, so a file is read and a
helper run once a survey, however many requests (pages, reconnects) a source gets.
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from recon.domain.capture import credential_values, redact_credentials
from recon.domain.credentials import reference
from recon.domain.errors import CollectionError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from recon.domain.deadline import Deadline
    from recon.domain.models import SourceEntry
    from recon.domain.shell import ShellRunner

#: The permission bits that let a file's group or others read it, or write it (and so choose
#: the credential recon sends: round-4 review M5).
_READABLE_BY_OTHERS = 0o044
_WRITABLE_BY_OTHERS = 0o022


def resolve_reference(
    written: str, env: Mapping[str, str], mission_dir: Path, where: str
) -> str | None:
    """The value an ``env:NAME`` or ``file:PATH`` reference names; None for an unset variable
    (the plan warned about it). ``where`` names the key, for the error."""
    found = reference(written)
    assert found is not None  # every kind's check holds a token to a reference
    scheme, name = found
    if scheme == "env":
        return env.get(name)
    path = Path(name).expanduser()
    path = path if path.is_absolute() else mission_dir / path
    try:
        # The file itself, never what a link names (O_NOFOLLOW): the modes checked are those
        # of the file read, and whoever can change a link cannot choose the file.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise CollectionError(
                f"{where}: {path} is a link, and recon reads a credential from the file itself, "
                "never through a link",
                kind="config",
                fix=f"name the file it links to, or put the credential in {path} itself, chmod 600",
            ) from None
        raise CollectionError(
            f"{where}: the file {path} could not be read: {type(exc).__name__}",
            kind="config",
            fix=f"put the credential in {path}, or correct the path (a relative one is "
            "relative to the mission's directory)",
        ) from exc
    try:
        mode = os.fstat(descriptor).st_mode
        if mode & (_READABLE_BY_OTHERS | _WRITABLE_BY_OTHERS):
            how = "read" if mode & _READABLE_BY_OTHERS else "written"
            raise CollectionError(
                f"{where}: the file {path} can be {how} by its group or by others (mode "
                f"{mode & 0o777:o}), so recon does not take a credential from it",
                kind="config",
                fix=f"chmod 600 {path}",
            )
        with os.fdopen(descriptor, encoding="utf-8", closefd=False) as file:
            return file.read().strip()
    except OSError as exc:
        raise CollectionError(
            f"{where}: the file {path} could not be read: {type(exc).__name__}",
            kind="config",
            fix=f"put the credential in {path}, or correct the path (a relative one is "
            "relative to the mission's directory)",
        ) from exc
    finally:
        os.close(descriptor)


def helper_headers(command: str, shell: ShellRunner, timeout: float, source: str) -> dict[str, str]:
    """The headers a ``headers_helper`` prints, as a JSON object of strings, within
    ``timeout`` seconds."""
    try:
        lines = list(shell.run_lines(command, None, timeout))
    except CollectionError as exc:
        raise CollectionError(
            f"source {source!r}: its headers_helper failed, so no credential was sent: {exc}",
            kind="auth",
            fix="run the headers_helper by hand to see why it failed, then fix it",
        ) from exc
    try:
        given = json.loads("\n".join(lines))
    except json.JSONDecodeError:
        given = None
    if not isinstance(given, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in given.items()
    ):
        raise CollectionError(
            f"source {source!r}: its headers_helper did not print a JSON object of header "
            "names to string values",
            kind="config",
            fix='make it print one object, e.g. {"Authorization": "Bearer ..."}',
        )
    return given


class Credentials:
    """A survey's credentials for the sources it reaches through ``auth``, each resolved on
    first use and kept for the survey. ``mission_dir`` anchors a relative ``file:``; ``shell``
    runs a ``headers_helper``, within the source's ``timeout`` cut to what is left of
    ``deadline``."""

    def __init__(
        self,
        env: Mapping[str, str],
        mission_dir: Path | None = None,
        shell: ShellRunner | None = None,
        deadline: Deadline | None = None,
    ) -> None:
        self._env = env
        self._mission_dir = mission_dir if mission_dir is not None else Path()
        self._shell = shell
        self._deadline = deadline
        self._tokens: dict[str, str | None] = {}
        self._helped: dict[str, dict[str, str]] = {}
        #: Each source's secret as it was read, before its ``auth.prefix``.
        self._read: dict[str, str] = {}

    def token(self, source: SourceEntry) -> str | None:
        """The secret the source's ``auth.token`` names, with its ``auth.prefix``; None when
        it names none or its variable is unset."""
        if source.name not in self._tokens:
            auth = source.auth
            value = (
                resolve_reference(auth.token, self._env, self._mission_dir, "auth.token")
                if auth.token
                else None
            )
            self._tokens[source.name] = f"{auth.prefix}{value}" if value else None
            if value:
                self._read[source.name] = value
        return self._tokens[source.name]

    def secrets(self, source: SourceEntry) -> list[str]:
        """What of the source's credentials has been resolved this survey, to redact where a
        server may write it back (a captured body, an error's snippet): its token, with and
        without its prefix, and each header its helper printed, every one of them a
        credential (Vector, round-4 review S5)."""
        found = [self._read.get(source.name), self._tokens.get(source.name)]
        helped = self._helped.get(source.name, {})
        found += list(helped.values()) + credential_values(helped)
        return [value for value in found if value]

    def redacted(self, source: SourceEntry, error: CollectionError) -> CollectionError:
        """``error``, its message with the source's credentials redacted: a server that
        echoes what it was sent puts them in the snippet of its answer the error quotes."""
        secrets = self.secrets(source)
        message = redact_credentials(str(error), secrets) if secrets else str(error)
        if message == str(error):
            return error
        return CollectionError(message, kind=error.kind, fix=error.fix, details=error.details)

    def helped(self, source: SourceEntry) -> dict[str, str]:
        """The headers the source's ``auth.headers_helper`` prints; none without one."""
        command = source.auth.headers_helper
        if command is None:
            return {}
        if source.name not in self._helped:
            if self._shell is None:
                raise CollectionError(
                    f"source {source.name!r} names a headers_helper, and this collector was "
                    "given no shell to run it",
                    kind="config",
                    fix="build the collector through recon's root, whose context has a shell",
                )
            timeout = source.timeout
            if self._deadline is not None:
                timeout = self._deadline.limit(timeout)
            self._helped[source.name] = helper_headers(command, self._shell, timeout, source.name)
        return self._helped[source.name]

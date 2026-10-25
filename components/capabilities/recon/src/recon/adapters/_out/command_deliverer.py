"""The ``command:`` delivery adapter (ruling R4): a subscription's command, run for each
delivery, with the delivery as one JSON line on its standard input.

The argument list is the config's, run as given, by no shell (``shell=False``), so nothing a row
holds can become a word of a command, a shell's or an AppleScript's: the row is on standard
input alone, ASCII JSON. The child starts in a session of its own, so a timeout stops it with
every process it started, and its stdout goes nowhere. What it writes to stderr is kept, its
last 2 KiB, to say why it failed.

It is not given recon's environment, which can hold a source's credentials: only the short list
a desktop session's notifier needs (``HOME``, ``PATH``, ``USER``, ``LOGNAME``, ``SHELL``,
``TERM``, ``LANG``, ``LC_ALL``, ``TMPDIR``, ``DISPLAY``, ``WAYLAND_DISPLAY``,
``DBUS_SESSION_BUS_ADDRESS``, ``XDG_RUNTIME_DIR``), as recon's environment has them.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import tempfile
from typing import TYPE_CHECKING

from recon.domain.subscriptions import Delivered

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

#: The variables of recon's environment a command is given; no other.
PASSED_ENV = (
    "HOME", "PATH", "USER", "LOGNAME", "SHELL", "TERM", "LANG", "LC_ALL", "TMPDIR", "DISPLAY",
    "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR",
)  # fmt: skip

#: The bytes of a command's stderr kept to say why it failed: its end.
_STDERR_KEPT = 2048


class CommandDeliverer:
    def __init__(self, env: Mapping[str, str]) -> None:
        """``env`` is recon's environment, of which a command is given :data:`PASSED_ENV`."""
        self._env = {name: env[name] for name in PASSED_ENV if name in env}

    def deliver(
        self, argv: Sequence[str], payload: bytes, *, timeout_s: float, cwd: Path
    ) -> Delivered:
        with tempfile.TemporaryFile() as stderr:
            try:
                child = subprocess.Popen(  # noqa: S603 - an argument list, by no shell
                    list(argv),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=stderr,
                    cwd=cwd,
                    env=self._env,
                    shell=False,
                    close_fds=True,
                    start_new_session=True,
                )
            except OSError as exc:
                return Delivered(False, None, f"{argv[0]} could not be started: {exc.strerror}")
            try:
                child.communicate(payload, timeout=timeout_s)
            except subprocess.TimeoutExpired:
                _stop(child)
                return Delivered(False, None, f"{argv[0]} did not exit within {timeout_s:g}s")
            if child.returncode == 0:
                return Delivered(True, 0)
            stderr.seek(max(0, stderr.seek(0, os.SEEK_END) - _STDERR_KEPT))
            said = stderr.read().decode("utf-8", "replace").strip()
            return Delivered(
                False, child.returncode, f"{argv[0]} exited {child.returncode}: {said or '-'}"
            )


def _stop(child: subprocess.Popen[bytes]) -> None:
    """Stop ``child`` and every process of its session, and reap it."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(child.pid, signal.SIGKILL)
    child.kill()
    child.wait()

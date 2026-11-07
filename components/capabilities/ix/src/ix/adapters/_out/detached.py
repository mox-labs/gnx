"""Starting an ``ix run`` as a process of its own, which outlasts the one that started it.

A long run outlasts the tool call an agent makes, so ``ix run --detach`` hands the run to a
child and returns once the child has opened the run's record: from then on ``ix status``,
``ix events`` and ``ix stop`` reach it by its directory. The child is an ordinary ``ix run``
of the same experiment under the run id the parent chose, so it refuses, records, stops and
reports exactly as a run in the foreground does.

The child runs in a session of its own, so the end of the caller's terminal or tool call
does not reach it; it reads nothing on standard input, its standard output (the report,
which ``ix report`` computes again) is discarded, and its standard error is appended to a
log beside the run's directory, where a defect that killed it is left to read.
"""

from __future__ import annotations

import json
import subprocess
import time
from typing import TYPE_CHECKING

from ix.domain import schema
from ix.domain.errors import (
    ConfigError,
    IxError,
    NotFoundError,
    UnregisteredError,
    UsageError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

#: How long a child has to open its run's record: it checks the experiment and what it
#: needs of this host again first, which the parent has just done. One that has not opened
#: it by then is stopped.
STARTUP_S = 120.0
#: How often the parent looks for the record.
POLL_S = 0.05
#: How long a child that is stopped for not starting has to end before it is killed.
GRACE_S = 5.0

#: The error each kind of ``ix.v1.error`` a child printed is raised as again.
_KINDS: dict[str, type[IxError]] = {
    "usage": UsageError,
    "config": ConfigError,
    "not_found": NotFoundError,
    "unregistered": UnregisteredError,
}

#: Every child started here, so each is reaped once it has ended.
_started: list[subprocess.Popen[bytes]] = []


def start(
    argv: Sequence[str],
    *,
    log: Path,
    opened: Callable[[], bool],
    startup_s: float = STARTUP_S,
) -> int:
    """Start ``argv`` detached and return its process id once ``opened()`` says its record
    is open. Raises the error the child printed when it exits before that (a refusal, as
    its ``ix.v1.error`` says, or :class:`IxError` with the end of its log), and
    :class:`ConfigError` when it has not opened it within ``startup_s``, after stopping
    it."""
    _started[:] = [child for child in _started if child.poll() is None]
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as stream:
        child = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stream,
            start_new_session=True,
        )
    _started.append(child)
    until = time.monotonic() + startup_s
    while not opened():
        code = child.poll()
        if code is not None:
            if opened():
                break  # it ended as quickly as it started: the record says how
            raise _relayed(code, log)
        if time.monotonic() > until:
            _end(child)
            raise ConfigError(
                f"the detached run did not open its record within {startup_s:.0f}s, so it was "
                "stopped; nothing was recorded",
                fix="run `ix plan <path>` for what it needs of this host, or run it without "
                f"--detach to see where it waits; its log is {log}",
                details={"path": str(log)},
            )
        time.sleep(POLL_S)
    return child.pid


def _relayed(code: int, log: Path) -> IxError:
    """The error a child that exited with ``code`` before opening its record printed last:
    the ``ix.v1.error`` it wrote to its log, raised as its kind; or, when it wrote none
    (it was killed, or crashed before it could), an :class:`IxError` with the log's end."""
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        try:
            envelope = json.loads(line)
        except ValueError:
            continue
        if isinstance(envelope, dict) and envelope.get("schema") == schema.ERROR:
            error = envelope.get("error") or {}
            found = _KINDS.get(str(error.get("kind")), IxError)(
                str(error.get("message") or ""),
                fix=error.get("fix"),
                details={**(error.get("details") or {}), "log": str(log)},
            )
            return found
    tail = " | ".join(lines[-3:]) or "nothing"
    return IxError(
        f"the detached run exited with code {code} before it opened its record; its log "
        f"ends: {tail}",
        fix=f"read its log, {log}, and run it without --detach to see it fail",
        details={"path": str(log), "value": code},
    )


def _end(child: subprocess.Popen[bytes]) -> None:
    """Stop ``child`` as a stop request would, and kill it if it has not ended in time."""
    child.terminate()
    try:
        child.wait(timeout=GRACE_S)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()

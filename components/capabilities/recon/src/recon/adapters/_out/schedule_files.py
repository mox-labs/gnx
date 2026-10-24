"""What recon keeps of a mission's schedule beside the mission::

    <mission>/schedule.json      what `recon schedule install` put in place: the label, the
                                 scheduler, the config's digest, each file's sha256, when
    <mission>/schedule.log       one JSON line a tick, appended whole; at 1 MiB it becomes
    <mission>/schedule.log.1     the one before, which it replaces: at most 2 MiB in all

Each written readable by its owner alone, as every file of a mission is. Appends take an
exclusive ``flock`` on the log and look again, once they hold it, that the log is still the
file they opened, so two ticks never write into a log one of them has just rotated.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from typing import TYPE_CHECKING, Any

from recon.adapters._out.private_files import make_dirs, open_file, write_file

if TYPE_CHECKING:
    from pathlib import Path

#: A mission's install record.
SCHEDULE_RECORD = "schedule.json"

#: A mission's tick log, and the one before it.
SCHEDULE_LOG = "schedule.log"

#: The bytes the log reaches before it is rotated.
LOG_MAX_BYTES = 1024 * 1024


class ScheduleFiles:
    def __init__(self, *, max_bytes: int = LOG_MAX_BYTES) -> None:
        """``max_bytes`` bounds the log, and the one before it: twice it in all."""
        self._max_bytes = max_bytes

    def record(self, mission_dir: Path, installed: dict[str, Any] | None) -> None:
        path = mission_dir / SCHEDULE_RECORD
        if installed is None:
            path.unlink(missing_ok=True)
            return
        make_dirs(mission_dir)
        tmp = path.with_name(f".{SCHEDULE_RECORD}.{os.getpid()}.tmp")
        write_file(tmp, (json.dumps(installed, indent=2, sort_keys=True) + "\n").encode())
        os.replace(tmp, path)

    def recorded(self, mission_dir: Path) -> dict[str, Any] | None:
        with contextlib.suppress(FileNotFoundError, ValueError):
            found = json.loads((mission_dir / SCHEDULE_RECORD).read_text(encoding="utf-8"))
            return found if isinstance(found, dict) else None
        return None

    def append(self, mission_dir: Path, tick: dict[str, Any]) -> None:
        make_dirs(mission_dir)
        path = mission_dir / SCHEDULE_LOG
        line = (json.dumps(tick, sort_keys=True) + "\n").encode()
        while True:
            descriptor = open_file(path, os.O_WRONLY | os.O_APPEND)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                if not _is(path, descriptor):
                    continue  # another tick rotated it while this one waited: open the new one
                if os.fstat(descriptor).st_size + len(line) > self._max_bytes:
                    os.replace(path, path.with_name(f"{SCHEDULE_LOG}.1"))
                    continue
                os.write(descriptor, line)
                return
            finally:
                os.close(descriptor)

    def lines(self, mission_dir: Path) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for name in (f"{SCHEDULE_LOG}.1", SCHEDULE_LOG):
            with contextlib.suppress(FileNotFoundError):
                for line in (mission_dir / name).read_bytes().splitlines():
                    with contextlib.suppress(ValueError):
                        tick = json.loads(line)
                        if isinstance(tick, dict):
                            found.append(tick)
        return found


def _is(path: Path, descriptor: int) -> bool:
    """Whether ``path`` is still the file open as ``descriptor``."""
    try:
        named = os.stat(path)
    except FileNotFoundError:
        return False
    held = os.fstat(descriptor)
    return (named.st_dev, named.st_ino) == (held.st_dev, held.st_ino)

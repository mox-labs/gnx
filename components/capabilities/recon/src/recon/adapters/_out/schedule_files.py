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
import json
import os
from typing import TYPE_CHECKING, Any

from recon.adapters._out.file_locks import append_bounded
from recon.adapters._out.mission_layout import mission_dir
from recon.adapters._out.private_files import make_dirs, write_file

if TYPE_CHECKING:
    from pathlib import Path

#: A mission's install record.
SCHEDULE_RECORD = "schedule.json"

#: A mission's tick log, and the one before it.
SCHEDULE_LOG = "schedule.log"

#: The bytes the log reaches before it is rotated.
LOG_MAX_BYTES = 1024 * 1024


class ScheduleFiles:
    """What recon keeps of the schedule of every mission under ``root`` (the project's
    ``.recon`` directory)."""

    def __init__(self, root: Path, *, max_bytes: int = LOG_MAX_BYTES) -> None:
        """``max_bytes`` bounds the log, and the one before it: twice it in all."""
        self._root = root
        self._max_bytes = max_bytes

    def record(self, mission: str, installed: dict[str, Any] | None) -> None:
        home = mission_dir(self._root, mission)
        path = home / SCHEDULE_RECORD
        if installed is None:
            path.unlink(missing_ok=True)
            return
        make_dirs(home)
        tmp = path.with_name(f".{SCHEDULE_RECORD}.{os.getpid()}.tmp")
        write_file(tmp, (json.dumps(installed, indent=2, sort_keys=True) + "\n").encode())
        os.replace(tmp, path)

    def recorded(self, mission: str) -> dict[str, Any] | None:
        with contextlib.suppress(FileNotFoundError, ValueError):
            path = mission_dir(self._root, mission) / SCHEDULE_RECORD
            found = json.loads(path.read_text(encoding="utf-8"))
            return found if isinstance(found, dict) else None
        return None

    def append(self, mission: str, tick: dict[str, Any]) -> None:
        line = (json.dumps(tick, sort_keys=True) + "\n").encode()
        append_bounded(mission_dir(self._root, mission) / SCHEDULE_LOG, line, self._max_bytes)

    def lines(self, mission: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for name in (f"{SCHEDULE_LOG}.1", SCHEDULE_LOG):
            with contextlib.suppress(FileNotFoundError):
                for line in (mission_dir(self._root, mission) / name).read_bytes().splitlines():
                    with contextlib.suppress(ValueError):
                        tick = json.loads(line)
                        if isinstance(tick, dict):
                            found.append(tick)
        return found

"""What recon keeps of a mission's schedule beside the mission::

    <mission>/schedule.json    what `recon schedule install` put in place: the label, the
                               scheduler, the config's digest, each file's sha256, when

Written whole and readable by its owner alone, as every file of a mission is.
"""

from __future__ import annotations

import contextlib
import json
import os
from typing import TYPE_CHECKING, Any

from recon.adapters._out.private_files import make_dirs, write_file

if TYPE_CHECKING:
    from pathlib import Path

#: A mission's install record.
SCHEDULE_RECORD = "schedule.json"


class ScheduleFiles:
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

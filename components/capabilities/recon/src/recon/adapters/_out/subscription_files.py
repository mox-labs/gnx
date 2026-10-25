"""What recon keeps of a mission's subscriptions, beside the mission::

    <mission>/subscriptions/<name>.json        its state: the output it reads, the cursor it
                                               acked last and when, the row a command failed
    <mission>/subscriptions/<name>.lock        locked by its one consumer while it reads, acks
                                               or delivers, holding its pid; kept, emptied
    <mission>/subscriptions/<name>.dead.jsonl  its dead letters, a JSON line each; at 1 MiB it
    <mission>/subscriptions/<name>.dead.jsonl.1  becomes the one before, which it replaces

Each written readable by its owner alone, as every file of a mission is. A state is replaced
whole, by a rename, so a reader never finds half of one. A name is a subscription's, which the
plan holds to a mission name's shape, so each is one path component.
"""

from __future__ import annotations

import contextlib
import json
import os
from typing import TYPE_CHECKING, Any

from recon.adapters._out.file_locks import append_bounded, hold_naming_pid, holder, let_go
from recon.adapters._out.private_files import make_dirs, write_file
from recon.domain.errors import ReconError, UsageError
from recon.domain.missions import MISSION_NAME
from recon.domain.subscriptions import SubscriptionState

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

#: The directory under a mission that holds what is kept of its subscriptions.
SUBSCRIPTIONS = "subscriptions"

#: The bytes a subscription's dead letters reach before they are rotated.
DEAD_LETTERS_MAX_BYTES = 1024 * 1024


class SubscriptionFiles:
    def __init__(self, *, max_bytes: int = DEAD_LETTERS_MAX_BYTES) -> None:
        """``max_bytes`` bounds a subscription's dead letters, and the ones before them: twice
        it in all."""
        self._max_bytes = max_bytes
        #: The lock held on each subscription a consumer through this store holds.
        self._held: dict[Path, int] = {}

    def hold(self, mission_dir: Path, name: str) -> None:
        lock = _home(mission_dir, name) / f"{name}.lock"
        try:
            make_dirs(lock.parent)
            descriptor = hold_naming_pid(lock)
        except OSError as exc:
            pid = holder(lock)
            if pid == os.getpid():
                stop = "this process holds it now: run the command again once that one returns"
            elif pid is not None:
                stop = f"let it finish (pid {pid}), then run the command again"
            else:
                stop = "let it finish, then run the command again"
            raise ReconError(
                f"another recon is reading or delivering subscription {name} of "
                f"{mission_dir.name}; a subscription has one consumer at a time",
                kind="busy",
                fix=stop,
                details={"mission": mission_dir.name, "subscription": name}
                | ({} if pid is None else {"pid": pid}),
            ) from exc
        self._held[lock] = descriptor

    def release(self, mission_dir: Path, name: str) -> None:
        let_go(self._held.pop(_home(mission_dir, name) / f"{name}.lock", None))

    def state(self, mission_dir: Path, name: str) -> SubscriptionState | None:
        path = _home(mission_dir, name) / f"{name}.json"
        with contextlib.suppress(FileNotFoundError, ValueError):
            return SubscriptionState.model_validate_json(path.read_bytes())
        return None

    def keep(self, mission_dir: Path, name: str, state: SubscriptionState) -> None:
        home = _home(mission_dir, name)
        make_dirs(home)
        tmp = home / f".{name}.{os.getpid()}.json.tmp"
        try:
            write_file(tmp, (state.model_dump_json(indent=2) + "\n").encode())
            os.replace(tmp, home / f"{name}.json")
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise

    def dead_letter(self, mission_dir: Path, name: str, letter: Mapping[str, Any]) -> None:
        line = (json.dumps(dict(letter), sort_keys=True, default=str) + "\n").encode()
        append_bounded(_home(mission_dir, name) / f"{name}.dead.jsonl", line, self._max_bytes)

    def dead_letters(self, mission_dir: Path, name: str) -> int:
        home = _home(mission_dir, name)
        count = 0
        for path in (home / f"{name}.dead.jsonl.1", home / f"{name}.dead.jsonl"):
            with contextlib.suppress(FileNotFoundError):
                count += path.read_bytes().count(b"\n")
        return count

    def names(self, mission_dir: Path) -> list[str]:
        home = mission_dir / SUBSCRIPTIONS
        if not home.is_dir():
            return []
        return sorted(path.stem for path in home.glob("*.json") if MISSION_NAME.match(path.stem))

    def remove(self, mission_dir: Path, name: str) -> bool:
        home = _home(mission_dir, name)
        removed = False
        for path in (home / f"{name}.json", home / f"{name}.dead.jsonl",
                     home / f"{name}.dead.jsonl.1"):  # fmt: skip
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
                removed = True
        return removed


def _home(mission_dir: Path, name: str) -> Path:
    """The directory of the mission's subscriptions; UsageError for a name that is not one
    path component a subscription can have."""
    if not MISSION_NAME.match(name):
        raise UsageError(
            f"{name!r} is not a subscription's name: letters, digits, ., _ and -, starting with "
            "a letter or a digit",
            fix="name a subscription the mission's config declares (`recon status <mission>`)",
        )
    return mission_dir / SUBSCRIPTIONS

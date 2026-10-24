"""What recon writes for a mission is its owner's alone: each directory it makes is 0700 and
each file 0600 (ruling A2), whatever the umask would let through. A mission's archive holds
what its sources answered, a mailbox or a repository's secrets as readily as a public feed,
and a scheduled survey writes it while nobody watches.

The modes are set as each path is made, never changed after: a directory or file that was
there before recon wrote into it keeps its own.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

#: The mode of every directory recon makes for a mission.
DIR_MODE = 0o700

#: The mode of every file recon makes for a mission.
FILE_MODE = 0o600


def make_dirs(path: Path) -> None:
    """Make ``path`` and each parent it lacks, each with :data:`DIR_MODE`; one already there is
    left as it is."""
    missing = []
    for directory in (path, *path.parents):
        if directory.is_dir():
            break
        missing.append(directory)
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=DIR_MODE)
        except FileExistsError:  # made meanwhile, by another writer: theirs
            continue


def open_file(path: Path, flags: int) -> int:
    """``os.open(path, flags)``, a file made by it given :data:`FILE_MODE`."""
    return os.open(path, flags | os.O_CREAT, FILE_MODE)


def write_file(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` in place of what it held, a new file with :data:`FILE_MODE`.
    Raises OSError when the write falls short."""
    descriptor = open_file(path, os.O_WRONLY | os.O_TRUNC)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(f"wrote nothing more of {len(data)} bytes to {path}")
            view = view[written:]
    finally:
        os.close(descriptor)

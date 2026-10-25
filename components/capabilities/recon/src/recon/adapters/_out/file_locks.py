"""The locks recon's stores hold on a mission's files, and the bounded logs they append to.

A lock is an ``flock`` on a file, held from when it is taken until its descriptor is closed or
its holder dies, so a pid reused by another process never makes a dead holder read as alive
(matrix's JSONL store's pattern). A holder writes its pid in the file once it holds it, so one
refused can be told whose it is, and empties it before letting go; the file stays, so no two
holders ever lock two files of one name. Whether a holder is alive is known on its own host
only.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
from typing import TYPE_CHECKING

from recon.adapters._out.private_files import make_dirs, open_file

if TYPE_CHECKING:
    from pathlib import Path


def hold(path: Path) -> int:
    """Hold an exclusive lock on ``path``, made if need be, until the descriptor is closed.
    Raises OSError (``BlockingIOError``) while another holds it."""
    descriptor = open_file(path, os.O_WRONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(descriptor)
        raise
    return descriptor


def hold_naming_pid(path: Path) -> int:
    """Hold the lock on ``path`` (:func:`hold`) and write this process's pid in it, so one
    refused can name the holder; :func:`let_go` clears it before letting go."""
    descriptor = hold(path)
    os.ftruncate(descriptor, 0)
    os.pwrite(descriptor, f"{os.getpid()}\n".encode(), 0)
    return descriptor


def let_go(descriptor: int | None) -> None:
    """Let go of a lock :func:`hold_naming_pid` took, its pid cleared; the file stays."""
    if descriptor is not None:
        with contextlib.suppress(OSError):
            os.ftruncate(descriptor, 0)  # no pid of a holder that has let go
        os.close(descriptor)  # closing releases the lock


def holder(path: Path) -> int | None:
    """The pid the holder of the lock on ``path`` wrote in it, when it is a process alive on
    this host; None when it has not written it yet, or the file says something else."""
    try:
        said = path.read_text().strip()
    except OSError:
        return None
    if not said.isdigit():
        return None
    pid = int(said)
    return pid if alive(pid) else None


def alive(pid: int) -> bool:
    """Whether a process ``pid`` runs on this host. A pid reused since reads alive, so what
    it made is not reported: the error is on the side of saying nothing."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # another user's
        return True
    return True


def locked(path: Path) -> bool:
    """True while some process, this one included, holds the lock on ``path``."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(descriptor)  # closing releases a lock this call took
    return False


def append_bounded(path: Path, line: bytes, max_bytes: int) -> None:
    """Append ``line`` to the log ``path`` whole, in one write. At ``max_bytes`` the log becomes
    ``<path>.1``, in place of the one before, so the two hold at most twice it. Each append
    takes an exclusive ``flock`` on the log and looks again, once it holds it, that the log is
    still the file it opened, so two writers never write into a log one of them has just
    rotated. A log is rotated only when it holds something: a line past the cap, made room for
    in an empty log, would rotate it for ever, so it is written alone (round-4 review M1)."""
    make_dirs(path.parent)
    while True:
        descriptor = open_file(path, os.O_WRONLY | os.O_APPEND)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            if not _is(path, descriptor):
                continue  # another writer rotated it while this one waited: open the new one
            size = os.fstat(descriptor).st_size
            if size and size + len(line) > max_bytes:
                os.replace(path, path.with_name(f"{path.name}.1"))
                continue
            os.write(descriptor, line)
            return
        finally:
            os.close(descriptor)


def _is(path: Path, descriptor: int) -> bool:
    """Whether ``path`` is still the file open as ``descriptor``."""
    try:
        named = os.stat(path)
    except FileNotFoundError:
        return False
    held = os.fstat(descriptor)
    return (named.st_dev, named.st_ino) == (held.st_dev, held.st_ino)

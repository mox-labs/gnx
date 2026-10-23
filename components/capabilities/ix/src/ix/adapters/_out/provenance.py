"""Reading what a verdict rests on from this machine: the digests of the files an experiment
names, the git commit it sits at, and the installed versions of what runs it.

The digest rule is :mod:`ix.domain.provenance`'s. Git is asked as a program (``git -C <dir>
...``), never through a library, with its file-system monitor turned off; a repository's
config can still name a program ``git status`` runs (a clean filter), as SECURITY.md says.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from glob import glob
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING

from ix.domain.errors import ConfigError
from ix.domain.provenance import UNDIGESTED, Content, Provenance, label

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from ix.domain.provenance import Named

#: How long one git command may take before the commit is recorded as unknown.
GIT_TIMEOUT_S = 10.0

_CHUNK = 1 << 20


def _file(path: Path) -> str:
    found = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            found.update(chunk)
    return found.hexdigest()


def _files(folder: Path) -> Iterator[Path]:
    """Every file under ``folder``, links followed, each folder entered once (a link back
    up the tree is not followed round), without the names left out of a digest."""
    entered: set[str] = set()
    for at, folders, files in os.walk(folder, followlinks=True):
        real = os.path.realpath(at)
        if real in entered:
            folders[:] = []
            continue
        entered.add(real)
        folders[:] = [name for name in folders if name not in UNDIGESTED]
        for name in files:
            path = Path(at, name)
            if name not in UNDIGESTED and path.is_file():
                yield path


def digest(path: Path) -> str:
    """The digest of a file, or of a folder by the rule :mod:`ix.domain.provenance` states;
    empty when nothing is there."""
    if path.is_file():
        return _file(path)
    if not path.is_dir():
        return ""
    lines = sorted(f"{file.relative_to(path).as_posix()}\0{_file(file)}\n" for file in _files(path))
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def _matched(written: str, base: str) -> list[str]:
    """The paths ``written`` stands for: itself, or what its ``*`` matches, each relative to
    ``base`` unless written absolute."""
    if "*" not in written:
        return [written]
    pattern = Path(base, written).expanduser()
    found = sorted(glob(str(pattern)))
    if Path(written).expanduser().is_absolute():
        return found
    return [os.path.relpath(match, base) for match in found]


def contents(named: Iterable[Named], base: str) -> tuple[Content, ...]:
    """What each file or folder ``named`` lists holds, its relative paths resolved against
    the directory ``base``; an item of a command only when it names a file, as written.
    Raises :class:`ConfigError` for one that is there and cannot be read."""
    found: list[Content] = []
    for item in named:
        if item.argument and not Path(base, item.path).expanduser().is_file():
            continue  # a program on PATH, or an argument that names no file here
        for path in [item.path] if item.argument else _matched(item.path, base):
            try:
                held = digest(Path(base, path).expanduser())
            except OSError as error:
                raise ConfigError(
                    f"{item.entry}.{item.option}: {path} cannot be read: {error}",
                    fix="make it readable by the user running ix, or name another path",
                    details={"path": f"{item.entry}.{item.option}", "value": path},
                ) from error
            found.append(
                Content(
                    entry=item.entry,
                    option=item.option,
                    path=path,
                    label=label(item.noun, path),
                    digest=held,
                )
            )
    return tuple(found)


def _git(directory: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", directory, *arguments],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
        check=False,
    )


def checkout(directory: str) -> Provenance:
    """The commit checked out in the git work tree ``directory`` is in, and whether a
    tracked file differs from it; or why there is none."""
    try:
        inside = _git(directory, "rev-parse", "--is-inside-work-tree")
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return Provenance(no_commit="not a repository")
        head = _git(directory, "rev-parse", "--verify", "--quiet", "HEAD")
        if head.returncode != 0:
            return Provenance(no_commit="no commit yet")
        status = _git(directory, "status", "--porcelain", "--untracked-files=no")
    except FileNotFoundError:
        return Provenance(no_commit="git is not installed")
    except (OSError, subprocess.TimeoutExpired) as error:
        return Provenance(no_commit=f"git could not be asked: {error}")
    if status.returncode != 0:
        # Whether the tree matches the commit is unknown, so the commit alone would say more
        # than is known.
        return Provenance(no_commit=f"git status failed: {status.stderr.strip()[-200:]}")
    return Provenance(commit=head.stdout.strip(), dirty=bool(status.stdout.strip()))


def installed(distribution: str) -> str:
    """The installed version of ``distribution``, or ``not installed``."""
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "not installed"

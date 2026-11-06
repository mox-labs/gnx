"""Where a mission's files are: the one place the file adapters map a mission's name, an
archive's reference and a stream's name to directories.

::

    <project root>/.recon/            STATE: every mission's directory
      <mission>/                      mission_dir
        archive/<archive id>/         a survey's archive (archive_files)
        streams/<stream>/<window id>/ a stream's window (archive_files)
        .joins/                       what a large change-detection join spills (duckdb_query)

A name that would leave its directory is refused here, before any adapter makes a path of
it: a reference is input to the adapters, and nothing it names may reach outside the mission.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from recon.domain.archives import ARCHIVE_ID_FORMAT
from recon.domain.errors import UsageError
from recon.domain.missions import MISSION_NAME
from recon.domain.stream import window_index

if TYPE_CHECKING:
    from pathlib import Path

    from recon.domain.archives import ArchiveRef

#: The directory under the project root that holds every mission.
STATE = ".recon"

#: Under a mission, the directory of its survey archives, that of its streams, and the one a
#: large change-detection join spills into.
ARCHIVES = "archive"
STREAMS = "streams"
JOINS = ".joins"


def mission_dir(root: Path, name: str) -> Path:
    """The directory of the mission ``name`` under ``root`` (the ``.recon`` directory). Raises
    UsageError for a name that is not a mission name."""
    if not MISSION_NAME.match(name):
        raise UsageError(
            f"{name!r} is not a mission name",
            fix="name a mission with letters, digits, '.', '_' and '-', starting with a "
            "letter or digit",
            details={"value": name},
        )
    return root / name


def stream_dir(root: Path, mission: str, stream: str) -> Path:
    """The directory of the mission's stream ``stream``. Raises UsageError for a stream name
    that is not one path component of its own."""
    if not stream or stream.startswith(".") or any(char in stream for char in "/\\\0"):
        raise UsageError(
            f"{stream!r} is not a stream name",
            fix="name a stream by its collector's name (recon status <mission> lists them)",
            details={"value": stream},
        )
    return mission_dir(root, mission) / STREAMS / stream


def home(root: Path, mission: str, stream: str | None) -> Path:
    """Where the mission's archives are kept, or the stream's windows."""
    if stream is None:
        return mission_dir(root, mission) / ARCHIVES
    return stream_dir(root, mission, stream)


def archive_dir(root: Path, ref: ArchiveRef) -> Path:
    """The directory of the archive ``ref``. Raises UsageError for an id that is not an
    archive's (a survey's: its start time; a window's: its index first)."""
    if not is_archive_id(ref.id, stream=ref.stream is not None):
        raise UsageError(
            f"{ref.id!r} is not the id of "
            + ("a window" if ref.stream is not None else "an archive"),
            fix=f"recon status {ref.mission} lists its archives and streams",
            details={"value": ref.id},
        )
    return home(root, ref.mission, ref.stream) / ref.id


def is_archive_id(name: str, *, stream: bool) -> bool:
    """Whether ``name`` is an id a survey gives its archive (``stream`` False) or a stream
    its window: a stray ``notes/`` is neither."""
    if stream:
        return window_index(name) is not None
    try:
        datetime.strptime(name, ARCHIVE_ID_FORMAT)
    except ValueError:
        return False
    return True

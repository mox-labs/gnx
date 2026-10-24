"""Missions as YAML files under the project root.

::

    <project root>/.recon/<name>/
      config.yaml
      captures/captures.jsonl, captures/raw/    the external capture log (`recon capture`)
      archive/<archive id>/                     one per survey

The project root is the one the caller names, or else the nearest ancestor of the working
directory holding ``.git`` (a directory, or a worktree's file); outside a repository it is
the working directory. A config path of ``-`` is standard input. The built-in templates
are the YAML files of the ``recon.configs`` package.
"""

from __future__ import annotations

import importlib.resources
import os
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from recon.adapters._out.capture_files import FilesystemCaptureLog
from recon.adapters._out.private_files import make_dirs, write_file
from recon.domain.capture import CAPTURE_LOG_NAME
from recon.domain.errors import ConfigError, NotFoundError, Problem, UsageError
from recon.domain.missions import MISSION_NAME, ConfigFile, Template

if TYPE_CHECKING:
    from recon.domain.clock import Clock


def find_project(start: Path) -> tuple[Path, str]:
    """The project root for ``start``, and how it was found: ``repository`` (the nearest
    ancestor holding ``.git``) or ``working_directory`` (``start`` itself, outside one)."""
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return directory, "repository"
    return start, "working_directory"


#: The config path that means standard input.
STDIN = "-"


class MissionFiles:
    def __init__(self, project: Path, clock: Clock) -> None:
        self._project = project
        self._root = project / ".recon"
        self._clock = clock

    @property
    def project(self) -> Path:
        return self._project

    @property
    def root(self) -> Path:
        return self._root

    def mission_dir(self, name: str) -> Path:
        if not MISSION_NAME.match(name):
            raise UsageError(
                f"{name!r} is not a mission name",
                fix="name a mission with letters, digits, '.', '_' and '-', starting with a "
                "letter or digit",
                details={"value": name},
            )
        return self._root / name

    def config_path(self, name: str) -> Path:
        return self.mission_dir(name) / "config.yaml"

    def config_text(self, name: str) -> bytes | None:
        try:
            return self.config_path(name).read_bytes()
        except FileNotFoundError:
            return None

    def read_config(self, path: Path) -> ConfigFile:
        try:
            text = sys.stdin.buffer.read() if str(path) == STDIN else path.read_bytes()
        except FileNotFoundError as exc:
            raise NotFoundError(f"no config at {path}") from exc
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            problem = Problem(
                "", " ".join(str(exc).split()), kind="yaml_syntax", fix="correct the YAML syntax"
            )
            raise ConfigError(
                f"{path}: not valid YAML",
                [problem],
                fix=f"correct the YAML in {path}",
                complete=False,
            ) from exc
        return ConfigFile(path=path, text=text, data=data)

    def write_config(self, name: str, text: bytes) -> Path:
        path = self.config_path(name)
        make_dirs(path.parent)
        write_file(path, text)
        return path

    def missions(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(
            d.name for d in self._root.iterdir() if d.is_dir() and (d / "config.yaml").exists()
        )

    def capture_log(self, name: str) -> FilesystemCaptureLog:
        return FilesystemCaptureLog(self.mission_dir(name) / "captures", self._clock)

    def capture_count(self, name: str) -> int:
        try:
            with open(self.mission_dir(name) / "captures" / CAPTURE_LOG_NAME, "rb") as f:
                return sum(1 for line in f if line.strip())
        except OSError:
            return 0

    def exposed(self, name: str) -> Path | None:
        top = self.mission_dir(name)
        if _reachable(top):
            return top
        for directory, subdirectories, files in os.walk(top):
            subdirectories.sort()
            here = Path(directory)
            for entry in subdirectories + sorted(files):
                if _reachable(here / entry):
                    return here / entry
        return None

    def is_directory(self, path: Path) -> bool:
        return path.is_dir()

    def templates(self) -> list[Template]:
        files = importlib.resources.files("recon.configs")
        found = []
        for file in files.iterdir():
            if file.name.endswith(".yaml"):
                text = file.read_text(encoding="utf-8")
                found.append(Template(file.name.removesuffix(".yaml"), _description(text), text))
        return sorted(found, key=lambda t: t.name)


def _reachable(path: Path) -> bool:
    """Whether a user other than the owner has any permission on ``path``; a link is not
    followed (its own bits mean nothing)."""
    try:
        mode = path.lstat().st_mode
    except OSError:
        return False
    return not stat.S_ISLNK(mode) and bool(mode & (stat.S_IRWXG | stat.S_IRWXO))


def _description(text: str) -> str:
    """A template's first comment line."""
    for line in text.splitlines():
        if line.startswith("#") and not line.startswith("##"):
            return line.lstrip("# ").strip()
    return ""

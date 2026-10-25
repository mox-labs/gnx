"""The operating system's schedulers, behind the :class:`~recon.domain.schedule.Scheduler`
port: launchd's user agents, systemd's user timers, and the user's crontab.

Each runs its scheduler's own command through a ``Runner`` (``launchctl``, ``systemctl
--user``, ``crontab``), by its absolute path and as an argument list, never a shell; a test
gives a runner that records each argv and runs nothing. Files are written readable by their
owner alone (0600), each replaced whole, under the user's own directories::

    launchd   ~/Library/LaunchAgents/<label>.plist          launchctl bootstrap gui/<uid>
    systemd   $XDG_CONFIG_HOME/systemd/user/<label>.service  systemctl --user enable, restart
              $XDG_CONFIG_HOME/systemd/user/<label>.timer
    cron      recon's block of the user's crontab            crontab -l, crontab -

Never a system domain: no LaunchDaemon, no system unit, no ``/etc/cron.d``, no sudo.

cron's block is recon's alone, between two marker lines; every line outside it is kept as it
was, and the block goes once its last entry does. Each entry starts with the comment line
naming its label.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from recon.adapters._out.private_files import make_dirs, write_file
from recon.domain.errors import ReconError
from recon.domain.schedule import ProjectedFile

if TYPE_CHECKING:
    from collections.abc import Mapping

    from recon.domain.schedule import Platform, Scheduler


@dataclass(frozen=True)
class Ran:
    """What a scheduler's command did: its exit code, and what it printed."""

    code: int
    stdout: str
    stderr: str


#: Runs a scheduler's command: its argv, and what to give it on stdin.
Runner = Callable[[Sequence[str], str | None], Ran]

#: The longest a scheduler's command may take.
COMMAND_TIMEOUT_S = 30.0


def run_command(argv: Sequence[str], stdin: str | None = None) -> Ran:
    """Run ``argv`` (no shell), bounded by :data:`COMMAND_TIMEOUT_S`; a command not there
    ran and failed (127), as a shell would say."""
    try:
        done = subprocess.run(
            list(argv), input=stdin, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S,
            check=False,
        )  # fmt: skip
    except FileNotFoundError:
        return Ran(127, "", f"{argv[0]}: not found\n")
    except subprocess.TimeoutExpired:
        return Ran(124, "", f"{argv[0]}: no answer in {COMMAND_TIMEOUT_S:g}s\n")
    return Ran(done.returncode, done.stdout, done.stderr)


def safe_path(path: str, *, project: Path | None = None, uid: int | None = None) -> tuple[str, ...]:
    """The directories of a ``PATH`` a tick may run with, each once, in order: absolute ones
    that are there, that no other user can write (neither group- nor world-writable, and
    owned by root or by ``uid``, the user's own by default: an owner may change a mode), and
    that are not inside ``project`` (a checkout's ``bin``, ``node_modules/.bin``: what a
    dependency or a pull put there, which the config's digest does not cover), whether by
    where they resolve or by their own path (a link committed in the project). ``.``, an
    empty entry and a relative one would resolve in whatever directory a command is started
    from."""
    owners = {0, os.getuid() if uid is None else uid}
    roots = (
        {os.path.normpath(os.path.abspath(project)), os.path.realpath(project)}
        if project is not None
        else set()
    )
    kept: list[str] = []
    for entry in path.split(os.pathsep):
        if not entry.startswith("/") or entry in kept:
            continue
        try:
            found = os.stat(entry)
        except OSError:
            continue
        if not stat.S_ISDIR(found.st_mode) or found.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            continue
        if found.st_uid not in owners:
            continue
        # Judged by where it resolves and by its own path: a link committed in the project
        # (tools -> /usr/bin) can be pointed anywhere by a later pull.
        named = os.path.normpath(entry)
        parent = os.path.join(os.path.realpath(os.path.dirname(named)), os.path.basename(named))
        seen = {os.path.realpath(entry), named, parent}
        if any(p == root or p.startswith(root + os.sep) for p in seen for root in roots):
            continue
        kept.append(entry)
    return tuple(kept)


def this_recon() -> str | None:
    """The ``recon`` command of this installation, beside the Python it runs on (the
    interpreter's own directory, never resolved through a link out of its environment);
    None when there is none."""
    candidate = Path(sys.executable).parent / "recon"
    return str(candidate) if os.access(candidate, os.X_OK) else None


def host_platform(env: Mapping[str, str]) -> Platform:
    """The scheduler this host has: launchd on macOS, systemd's user manager on Linux where
    one runs for this user (``systemctl`` there, and ``XDG_RUNTIME_DIR`` set), else cron."""
    if sys.platform == "darwin":
        return "launchd"
    if (
        sys.platform.startswith("linux")
        and shutil.which("systemctl")
        and env.get("XDG_RUNTIME_DIR")
    ):
        return "systemd"
    return "cron"


def scheduler_commands() -> dict[str, str]:
    """Each scheduler's command, by its absolute path where one is found."""
    found = {name: shutil.which(name) for name in ("launchctl", "systemctl", "crontab")}
    defaults = {"launchctl": "/bin/launchctl", "systemctl": "/usr/bin/systemctl",
                "crontab": "/usr/bin/crontab"}  # fmt: skip
    return {name: found[name] or defaults[name] for name in defaults}


def make_scheduler(
    platform: Platform,
    *,
    home: Path,
    config_home: Path,
    uid: int,
    run: Runner,
    commands: Mapping[str, str],
) -> Scheduler:
    """The scheduler for ``platform``, over the user's ``home`` (and ``config_home``, where
    systemd reads user units), running its commands through ``run``."""
    if platform == "launchd":
        return LaunchdScheduler(home / "Library" / "LaunchAgents", uid, run, commands["launchctl"])
    if platform == "systemd":
        return SystemdScheduler(config_home / "systemd" / "user", run, commands["systemctl"])
    return CronScheduler(run, commands["crontab"])


def _refused(ran: Ran, what: str, argv: Sequence[str]) -> ReconError:
    said = (ran.stderr or ran.stdout).strip() or f"exit {ran.code}"
    return ReconError(
        f"the scheduler refused to {what}: {' '.join(argv)}: {said}",
        kind="run_failed",
        fix="read what it said; the projected files are in place, so once its cause is fixed "
        "`recon schedule install` again loads them",
        details={"exit": ran.code},
    )


def _put(directory: Path, files: Sequence[ProjectedFile]) -> None:
    """Write each file whole into ``directory``, readable by its owner alone."""
    make_dirs(directory)
    for file in files:
        path = directory / file.name
        tmp = path.with_name(f".{file.name}.{os.getpid()}.tmp")
        write_file(tmp, file.text.encode())
        os.replace(tmp, path)


def _read(directory: Path, names: Sequence[str]) -> tuple[ProjectedFile, ...]:
    found = []
    for name in names:
        with contextlib.suppress(FileNotFoundError):
            found.append(ProjectedFile(name, (directory / name).read_text(encoding="utf-8")))
    return tuple(found)


def _labels(directory: Path, prefix: str, suffix: str) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(
        p.name.removesuffix(suffix)
        for p in directory.iterdir()
        if p.name.startswith(prefix) and p.name.endswith(suffix)
    )


class LaunchdScheduler:
    platform: Platform = "launchd"

    def __init__(self, agents: Path, uid: int, run: Runner, launchctl: str) -> None:
        self._agents = agents
        self._domain = f"gui/{uid}"
        self._run = run
        self._launchctl = launchctl

    def install(self, label: str, files: Sequence[ProjectedFile]) -> None:
        _put(self._agents, files)
        # Unloaded first, so the new file is the one loaded; one not loaded is no failure.
        self._run([self._launchctl, "bootout", f"{self._domain}/{label}"], None)
        argv = [self._launchctl, "bootstrap", self._domain, *self.locations(label)]
        ran = self._run(argv, None)
        if ran.code != 0:
            raise _refused(ran, "load the schedule", argv)

    def locations(self, label: str) -> list[str]:
        return [str(self._agents / f"{label}.plist")]

    def remove(self, label: str) -> bool:
        path = self._agents / f"{label}.plist"
        if not path.exists():
            return False
        self._run([self._launchctl, "bootout", f"{self._domain}/{label}"], None)
        path.unlink(missing_ok=True)
        return True

    def installed(self, prefix: str) -> list[str]:
        return _labels(self._agents, prefix, ".plist")

    def files(self, label: str) -> tuple[ProjectedFile, ...]:
        return _read(self._agents, [f"{label}.plist"])


class SystemdScheduler:
    platform: Platform = "systemd"

    def __init__(self, units: Path, run: Runner, systemctl: str) -> None:
        self._units = units
        self._run = run
        self._systemctl = systemctl

    def _user(self, *args: str) -> tuple[list[str], Ran]:
        argv = [self._systemctl, "--user", *args]
        return argv, self._run(argv, None)

    def install(self, label: str, files: Sequence[ProjectedFile]) -> None:
        _put(self._units, files)
        # Read again, enabled for the next login, and restarted so a timer whose calendar
        # changed runs on the new one now.
        for args in (("daemon-reload",), ("enable", f"{label}.timer"),
                     ("restart", f"{label}.timer")):  # fmt: skip
            argv, ran = self._user(*args)
            if ran.code != 0:
                raise _refused(ran, "load the schedule", argv)

    def locations(self, label: str) -> list[str]:
        return [str(self._units / f"{label}.service"), str(self._units / f"{label}.timer")]

    def remove(self, label: str) -> bool:
        names = [f"{label}.service", f"{label}.timer"]
        if not any((self._units / name).exists() for name in names):
            return False
        self._user("disable", "--now", f"{label}.timer")
        for name in names:
            (self._units / name).unlink(missing_ok=True)
        self._user("daemon-reload")
        return True

    def installed(self, prefix: str) -> list[str]:
        return _labels(self._units, prefix, ".timer")

    def files(self, label: str) -> tuple[ProjectedFile, ...]:
        return _read(self._units, [f"{label}.service", f"{label}.timer"])


#: The lines that open and close recon's block of the user's crontab.
CRON_BEGIN = "# BEGIN recon schedules: kept by `recon schedule`; edit outside this block only"
CRON_END = "# END recon schedules"


class CronScheduler:
    platform: Platform = "cron"

    def __init__(self, run: Runner, crontab: str) -> None:
        self._run = run
        self._crontab = crontab

    def _read(self, *, edit: bool = False) -> tuple[list[str], dict[str, str], list[str]]:
        """The crontab's lines before recon's block, its entries by label, and the lines
        after it. To ``edit`` it, a block that opens and never closes is refused: where it
        ends cannot be known, and the user's own lines after it would go with it (round-4
        review M4)."""
        argv = [self._crontab, "-l"]
        ran = self._run(argv, None)
        if ran.code != 0 and "no crontab" not in ran.stderr.lower():
            raise _refused(ran, "read the crontab", argv)
        lines = ran.stdout.splitlines(keepends=True) if ran.code == 0 else []
        stripped = [line.rstrip("\n") for line in lines]
        if CRON_BEGIN not in stripped:
            return lines, {}, []
        begin = stripped.index(CRON_BEGIN)
        if edit and CRON_END not in stripped[begin:]:
            raise ReconError(
                f"the crontab has recon's line {CRON_BEGIN!r} and no {CRON_END!r} after it, so "
                "recon cannot tell its block from your own lines, and edits none of them",
                kind="config",
                fix=f"run `crontab -e`, put the line {CRON_END} after recon's entries (or delete "
                "recon's lines), then run the command again",
            )
        end = stripped.index(CRON_END, begin) if CRON_END in stripped[begin:] else len(lines)
        entries: dict[str, str] = {}
        label = None
        for line in lines[begin + 1 : end]:
            if line.startswith("# recon.") and ":" in line:
                label = line[2:].split(":", 1)[0]
                entries[label] = ""
            if label is not None:
                entries[label] += line
        return lines[:begin], entries, lines[end + 1 :]

    def _write(self, before: list[str], entries: dict[str, str], after: list[str]) -> None:
        text = "".join(before)
        if entries:
            if text and not text.endswith("\n"):
                text += "\n"
            text += CRON_BEGIN + "\n" + "".join(entries[k] for k in sorted(entries))
            text += CRON_END + "\n"
        text += "".join(after)
        argv = [self._crontab, "-"]
        ran = self._run(argv, text)
        if ran.code != 0:
            raise _refused(ran, "write the crontab", argv)

    def install(self, label: str, files: Sequence[ProjectedFile]) -> None:
        before, entries, after = self._read(edit=True)
        entries[label] = files[0].text
        self._write(before, entries, after)

    def locations(self, label: str) -> list[str]:
        return ["crontab"]

    def remove(self, label: str) -> bool:
        before, entries, after = self._read(edit=True)
        if entries.pop(label, None) is None:
            return False
        self._write(before, entries, after)
        return True

    def installed(self, prefix: str) -> list[str]:
        return sorted(label for label in self._read()[1] if label.startswith(prefix))

    def files(self, label: str) -> tuple[ProjectedFile, ...]:
        text = self._read()[1].get(label)
        return () if text is None else (ProjectedFile(f"{label}.cron", text),)

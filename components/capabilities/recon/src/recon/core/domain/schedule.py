"""Schedules, as a mission's config says them: a mission that runs itself, by the operating
system's own scheduler. How a schedule is projected onto a scheduler, and when a tick is due,
are the observe layer's (``recon.observe.domain.scheduling``); what a ``schedule:`` may say,
and what a plan checks of a scheduled mission, are here.

A mission declares ``schedule: {every, missed}`` and recon projects it onto the scheduler the
host has (launchd, systemd's user timers, or cron): there is no recon daemon. Each tick the
scheduler starts is one survey of the mission (``recon schedule tick``).

``every`` is bare seconds, as every duration in a recon config is. It is a whole number of
minutes that divides a day, so the ticks fall on one daily grid of wall-clock times that every
scheduler keeps alike: launchd and cron count in minutes, and a systemd timer that runs a
missed tick (``Persistent=true``) is a calendar, which repeats daily. ``missed`` says what a
tick missed while the host was asleep or off becomes: ``once``, the missed ticks coalesced
into one run when the host is back (the default), or ``skip``, none. Missed ticks are never
caught up one by one: that would be a burst of surveys against each source's rate limit.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from collections.abc import Sequence

#: What a tick the host missed (asleep, off) becomes: one run once it is back, or none.
Missed = Literal["once", "skip"]

#: The shortest ``every``, in seconds.
MIN_EVERY_S = 60

#: The seconds in a day: the grid every schedule's ticks fall on repeats daily.
DAY_S = 86400

#: Every ``every`` a schedule may have, in seconds: the whole minutes that divide a day.
EVERY_ALLOWED: tuple[int, ...] = tuple(m * 60 for m in range(1, 1441) if 1440 % m == 0)

#: A scheduled mission's ``deadline:``, unless it sets one, as a share of ``every``: a tick
#: ends before the next one is due, so two ticks do not overlap (ruling S2).
DEADLINE_SHARE = 0.8

#: The least share of ``every`` a scheduled mission's default ``deadline:`` leaves its
#: survey, however long its deliveries may take.
DEADLINE_FLOOR = 0.2

#: How late a tick may start and still be the one that was due, in seconds: the grid's own
#: grain. Under ``missed: skip`` a later one is the scheduler catching up after sleep, and is
#: skipped.
MISSED_GRACE_S = 60.0

#: The skipped ticks in a row, each refused ``busy``, that make an overrun: the mission's
#: survey takes longer than ``every``.
OVERRUN_SKIPS = 2

#: Why a tick ran no survey: another held the mission (``busy``), it was a missed tick under
#: ``missed: skip``, or the config is not the one installed.
Skipped = Literal["busy", "missed", "config_changed"]


def nearest_every(every: int) -> tuple[int, ...]:
    """The allowed ``every`` nearest ``every``: the one below it and the one above, or the one
    at the end of the range it is past."""
    below = [e for e in EVERY_ALLOWED if e < every]
    above = [e for e in EVERY_ALLOWED if e > every]
    return tuple(([below[-1]] if below else []) + ([above[0]] if above else []))


def every_fix(every: int) -> str:
    """The fix for an ``every`` no scheduler keeps: the nearest that one does."""
    nearest = " or ".join(f"every: {e}" for e in nearest_every(every))
    return f"set {nearest}: whole minutes that divide a day (86400 s), in seconds"


@dataclass(frozen=True)
class _Fetcher:
    """A command that fetches the package it is given each time it runs, unless it names an
    exact version: the ``words`` that start it, how its packages are written (``npm``'s or
    Python's), its options whose value is a package, and those whose value is something else
    to step over (a Python, an index)."""

    words: tuple[str, ...]
    ecosystem: Literal["npm", "python"]
    packages: tuple[str, ...]
    values: tuple[str, ...] = ()


_NPM_VALUES = ("-c", "--call", "--registry", "--cache", "--prefix", "-w", "--workspace")
_UV_PACKAGES = ("--from", "--with")
_UV_VALUES = ("-p", "--python", "--index", "--index-url", "--extra-index-url", "--default-index",
              "--with-editable", "--with-requirements", "-c", "--constraints", "--overrides",
              "--cache-dir", "--directory", "--project", "--config-file")  # fmt: skip

#: The fetchers judged (the owner's ruling, round-4 review S7). ``npx`` with ``--yes`` or not:
#: it assumes yes without a terminal, as a tick has none.
_FETCHERS = (
    _Fetcher(("npx",), "npm", ("-p", "--package"), _NPM_VALUES),
    _Fetcher(("npm", "exec"), "npm", ("-p", "--package"), _NPM_VALUES),
    _Fetcher(("bunx",), "npm", ("-p", "--package")),
    _Fetcher(("pnpm", "dlx"), "npm", ("--package",), ("--reporter",)),
    _Fetcher(("uvx",), "python", _UV_PACKAGES, _UV_VALUES),
    _Fetcher(("uv", "tool", "run"), "python", _UV_PACKAGES, _UV_VALUES),
    _Fetcher(("pipx", "run"), "python", ("--spec",), ("--python", "--pip-args", "--index-url")),
)

#: Each fetcher judged, as it is written.
FETCHERS: tuple[str, ...] = tuple(" ".join(fetcher.words) for fetcher in _FETCHERS)
_BY_NAME = {" ".join(fetcher.words): fetcher for fetcher in _FETCHERS}

#: An exact version: npm's semver, and a Python version with no wildcard.
_EXACT_NPM = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_EXACT_PYTHON = re.compile(r"^\d[0-9A-Za-z.+!-]*$")

#: A repository named by its commit, the only pin a repository has: a whole SHA-1 or SHA-256
#: after ``@`` (pip's, uv's) or ``#`` (npm's). A branch, a tag or a short hash can move.
_COMMIT = re.compile(r"[@#](?:[0-9a-f]{40}|[0-9a-f]{64})$")
_REPOSITORY = ("git+", "github:", "gitlab:", "bitbucket:", "git:")

#: What separates one simple command from the next in a shell line.
_SHELL_OPERATORS = frozenset({";", "&", "&&", "|", "||", "(", ")", "|&", ";;", "\n"})

#: Words that start another command after them, and their options that take a value.
_WRAPPERS = frozenset({"env", "exec", "command", "nohup", "time", "nice", "sudo"})
_WRAPPER_VALUES = frozenset({"-u", "-g", "-n", "-C"})
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def unpinned(line: str | Sequence[str]) -> list[tuple[str, str]]:
    """Each package a command ``line`` (a shell line, or an argument list) has a fetcher
    (:data:`FETCHERS`) run without an exact version, as ``(fetcher, package)``, in order:
    ``npx pkg``, ``pnpm dlx pkg@latest``, ``uvx pkg``, ``pipx run --spec 'pkg>=1' cmd``, a
    repository at a branch. Only a fetcher in command position counts: the first word of a
    simple command, after its variable assignments and a wrapper (``env``, ``exec``), never
    an argument (``echo npx``). ``pkg@1.2.3`` (npm's), ``pkg==1.2.3`` or ``pkg@1.2.3``
    (Python's), a repository at a whole commit, and a local path are pinned. A shell line
    that does not parse (an open quote) gives nothing: it would not run either."""
    try:
        commands = [list(line)] if not isinstance(line, str) else _simple(_shell_words(line))
    except ValueError:
        return []
    found: list[tuple[str, str]] = []
    for words in commands:
        named = _command(words)
        for fetcher in _FETCHERS:
            size = len(fetcher.words)
            if (
                named[:1]
                and named[0].rsplit("/", 1)[-1] == fetcher.words[0]
                and tuple(named[1:size]) == fetcher.words[1:]
            ):
                found += [(" ".join(fetcher.words), package)
                          for package in _fetched(fetcher, named[size:])
                          if not _pinned(fetcher, package)]  # fmt: skip
                break
    return found


def pinned_form(fetcher: str, package: str) -> str:
    """How ``package`` is written pinned, for ``fetcher`` (one of :data:`FETCHERS`)."""
    if _repository(package):
        at = "#" if _BY_NAME[fetcher].ecosystem == "npm" else "@"
        return (
            f"`{re.split(r'[@#]', package.split('://', 1)[-1], maxsplit=1)[0]}...{at}<commit>` "
            "(a whole commit hash; a branch or a tag can move)"
        )
    if _BY_NAME[fetcher].ecosystem == "npm":
        name, at, _ = package.rpartition("@")
        return f"`{name if at and name else package}@<version>` ({fetcher})"
    project = re.match(r"[A-Za-z0-9._-]+(?:\[[^\]]*\])?", package)
    pinned = f"{project.group(0) if project else package}==<version>"
    named = {"pipx run": "--spec"}.get(fetcher, "--from")
    return (
        f"`{pinned}` ({fetcher}; `{fetcher} {named} '{pinned}' <command>` when its command "
        "is another name)"
    )


def _shell_words(line: str) -> list[str]:
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""  # a "#" inside a word (npm's github:o/r#<commit>) is no comment
    return list(lexer)


def _simple(words: Sequence[str]) -> list[list[str]]:
    """A shell line's words, as its simple commands."""
    commands: list[list[str]] = [[]]
    for word in words:
        if word in _SHELL_OPERATORS:
            commands.append([])
        else:
            commands[-1].append(word)
    return [command for command in commands if command]


def _command(words: Sequence[str]) -> list[str]:
    """A simple command's words from its command word on: past its variable assignments,
    and past a wrapper (``env``, ``exec``, ``sudo``...) with its options and assignments."""
    at = 0
    while at < len(words):
        word = words[at]
        if _ASSIGNMENT.match(word):
            at += 1
        elif word.rsplit("/", 1)[-1] in _WRAPPERS:
            at += 1
            while at < len(words) and (words[at].startswith("-") or _ASSIGNMENT.match(words[at])):
                at += 2 if words[at] in _WRAPPER_VALUES else 1
        else:
            break
    return list(words[at:])


def _fetched(fetcher: _Fetcher, words: Sequence[str]) -> list[str]:
    """The packages ``fetcher`` is given by ``words``, its arguments: each a package option
    names, else the first word that is no option (the command's own arguments follow it)."""
    named: list[str] = []
    expects: str | None = None
    for word in words:
        if expects is not None:
            if expects == "package":
                named.append(word)
            expects = None
            continue
        option, has_value, value = word.partition("=")
        if option in fetcher.packages:
            named += [value] if has_value else []
            expects = None if has_value else "package"
        elif option in fetcher.values:
            expects = None if has_value else "value"
        elif not word.startswith("-"):
            # Given packages by option, the first word is the command they provide.
            return named or [word]
    return named


def _repository(package: str) -> bool:
    return "://" in package or package.startswith(_REPOSITORY) or package.endswith(".git")


def _pinned(fetcher: _Fetcher, package: str) -> bool:
    if package.startswith((".", "/", "~", "file:")):
        return True  # a local path: nothing is fetched
    if _repository(package):
        return _COMMIT.search(package) is not None
    if fetcher.ecosystem == "npm":
        name, at, version = package.rpartition("@")
        return bool(at) and bool(name) and _EXACT_NPM.match(version) is not None
    for pin in ("===", "==", "@"):
        name, found, version = package.partition(pin)
        if found:
            return bool(name) and _EXACT_PYTHON.match(version) is not None
    return False


class ScheduleSpec(BaseModel):
    """``schedule:``: run the mission every ``every`` seconds, by the operating system's
    scheduler, a tick missed while the host was away run ``once`` when it is back or
    ``skip``-ped. ``every`` has no default: how often is the one thing only the mission's
    author knows, and no ``schedule:`` at all is the default."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    every: int = Field(ge=MIN_EVERY_S)
    missed: Missed = "once"

    @field_validator("every")
    @classmethod
    def _on_a_daily_grid(cls, value: int) -> int:
        if value not in EVERY_ALLOWED:
            raise ValueError(
                f"{value} s is not whole minutes that divide a day: launchd and cron count in "
                "minutes, and a schedule's ticks repeat daily"
            )
        return value

    @property
    def deadline(self) -> float:
        """The ``deadline:`` a mission scheduled so has unless it sets one."""
        return DEADLINE_SHARE * self.every

    def deadline_beside(self, delivering: float) -> float:
        """The ``deadline:`` a mission scheduled so has unless it sets one, when its tick then
        delivers for up to ``delivering`` seconds: 0.8 × ``every`` less that, so the tick's
        survey and delivery end before the next tick is due, and never under 0.2 × ``every``
        (Erlang, review F3)."""
        return max(DEADLINE_FLOOR * self.every, self.deadline - delivering)


#: The schedulers a schedule is projected onto: launchd's user agents (macOS), systemd's user
#: timers (Linux), and the user's crontab where neither is.
Platform = Literal["launchd", "systemd", "cron"]

PLATFORMS: tuple[Platform, ...] = ("launchd", "systemd", "cron")

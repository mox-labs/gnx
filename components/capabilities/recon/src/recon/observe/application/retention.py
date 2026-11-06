"""Retention: which sealed archives (and windows) a mission's ``retain:`` lets go, and when.

Retiring one is the core's operation (``Hold.retire``, under the writer's hold), and so is
what its tombstone carries (its tables' committed ranges, ruling R-TOMB: ``recon.core.
kept_in_tombstone``). Which go is this module's policy: each entry's rules, but never the
newest, never one being written, never the last one a table with ``changes:`` was told in (the
told index the next comparison reads), and never one holding change rows a subscription the
config declares has not acked: a notification lost in silence is worse than the disk it
takes.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from recon.core import ARCHIVE_ID_FORMAT, Problem, kept_in_tombstone, window_opened

if TYPE_CHECKING:
    from collections.abc import Callable

    from recon.core import (
        ArchiveRef,
        Archives,
        Clock,
        CollectorEntry,
        Hold,
        Prepared,
        Retain,
        RetainSpec,
        WindowOutcome,
    )
    from recon.observe.application.subscriptions import Subscriptions


class Retention:
    """What ``retain:`` lets go of a mission's archives and its streams' windows, read through
    ``archives`` at the clock's time; ``subscriptions`` (None: none are kept) say what each
    holds back."""

    def __init__(
        self, archives: Archives, clock: Clock, subscriptions: Subscriptions | None = None
    ) -> None:
        self._archives = archives
        self._clock = clock
        self._subscriptions = subscriptions

    def held(self, name: str, prepared: Prepared, stream: str | None) -> set[str]:
        """The archives (with ``stream``, the windows) holding change rows a declared
        subscription has not acked."""
        if self._subscriptions is None:
            return set()
        held = self._subscriptions.held(name, prepared, stream)
        return set().union(*(archives for _, archives in held.values()))

    def snapshots(self, hold: Hold, name: str, prepared: Prepared) -> None:
        """Retire the archives every snapshot entry's ``retain:`` lets go (Knuth, round-4
        review S1), oldest first, once the survey's archive is sealed, the mission still held.
        An archive holds every entry's tables, so it goes only when every snapshot entry lets
        it go; one that keeps ``all`` (the default) keeps every archive. Each leaves a
        tombstone (:meth:`Archives.retired`); a retirement that fails is tried again by the
        next."""
        config = prepared.config
        streams = {stream.index for stream in prepared.plan.streams}
        entries = [entry for i, entry in enumerate(config.collectors) if i not in streams]
        rules = [entry.retain for entry in entries]
        going = self.letting_go(name, prepared)
        if not going:
            return
        kept = self.held(name, prepared, None)
        archives = self._archives.archives(name)
        now = self._clock.now()
        for archive in archives[:-1]:
            if archive.id not in going or archive.id in kept:
                continue
            if self._archives.state(archive) == "writing":
                continue
            meta = self._archives.meta(archive)
            tombstone = {
                "id": archive.id,
                "index": meta.get("index"),
                "started_at": meta.get("started_at"),
                "timestamp": meta.get("timestamp"),
                "ended": meta.get("ended"),
                "retired_at": now.isoformat(),
                "rule": [rule.model_dump(exclude_none=True) for rule in rules
                         if rule is not None and not isinstance(rule, str)],
                # Its tables' committed ranges: a range goes on from where it was, whatever
                # retention removed (ruling R-TOMB).
                "ranges": kept_in_tombstone(meta),
            }  # fmt: skip
            with contextlib.suppress(OSError):  # left listed: the next survey retires it
                hold.retire(archive, tombstone)

    def letting_go(self, name: str, prepared: Prepared) -> set[str]:
        """The archives the snapshot entries' ``retain:`` lets go now: those every entry's
        rules let go, but the newest and the last one each table with ``changes:`` was told
        in; none when an entry keeps ``all``."""
        config = prepared.config
        streams = {stream.index for stream in prepared.plan.streams}
        entries = [entry for i, entry in enumerate(config.collectors) if i not in streams]
        rules = [entry.retain for entry in entries]
        if not entries or any(rule is None or isinstance(rule, str) for rule in rules):
            return set()
        archives = self._archives.archives(name)
        if len(archives) < 2:
            return set()
        now = self._clock.now()
        sizes: dict[str, int] = {}
        going: set[str] | None = None
        for rule in rules:
            assert rule is not None and not isinstance(rule, str)
            lets_go = self._snapshots_going(archives, rule, now, sizes)
            going = lets_go if going is None else going & lets_go
        kept = {archives[-1].id} | self._told_in(archives, entries)
        return (going or set()) - kept

    def window(
        self,
        hold: Hold,
        name: str,
        stream: str,
        rule: Retain,
        sealed: WindowOutcome,
        sizes: dict[str, int],
        *,
        held: Callable[[], set[str]] | None = None,
    ) -> None:
        """Retire the sealed windows of ``stream`` its ``rule`` does not keep, oldest first,
        once ``sealed`` is: each that ``windows`` leaves out, that closed more than ``seconds``
        before it, or that the newest windows' ``bytes`` leave no room for. The newest is
        always kept, a window not sealed is never retired, and nor is one ``held`` gives (those
        holding change rows a subscription has not acked, asked only when a window would go).
        Each costs the windows the rules look at; ``sizes`` are read once a run."""
        if isinstance(rule, str):
            return
        windows = self._archives.archives(name, stream=stream)
        now = datetime.fromisoformat(sealed.closed_at)
        going = windows_going(self._archives, windows, rule, now, sizes)
        kept = held() if held is not None and going else set()
        for window in windows[:-1]:
            if window.id not in going or window.id in kept:
                continue
            meta = self._archives.meta(window)
            found = meta.get("stream")
            if not isinstance(found, dict) or self._archives.state(window) == "writing":
                continue  # never sealed: kept, as what a dead writer left
            [source] = found.get("sources") or [{}]
            tombstone = {
                "id": window.id,
                "index": found.get("index"),
                "opened_at": found.get("opened_at"),
                "closed_at": found.get("closed_at"),
                "closed_by": found.get("closed_by"),
                "records": source.get("records"),
                "dropped": source.get("dropped"),
                "retired_at": now.isoformat(),
                "rule": rule.model_dump(exclude_none=True),
            }
            hold.retire(window, tombstone)
            sizes.pop(window.id, None)

    def behind(self, name: str, prepared: Prepared) -> list[Problem]:
        """A ``subscription_behind`` warning for each declared subscription holding back
        archives (or a stream's windows) ``retain:`` would let go: its change rows there are
        not acked."""
        config = prepared.config
        if self._subscriptions is None or not config.subscriptions:
            return []
        letting: dict[str | None, set[str]] = {None: self.letting_go(name, prepared)}
        for stream in prepared.plan.streams:
            windows = self._archives.archives(name, stream=stream.name)
            if isinstance(stream.retain, str) or len(windows) < 2:
                continue
            going = windows_going(self._archives, windows, stream.retain, self._clock.now(), {})
            letting[stream.name] = going - {windows[-1].id}
        problems = []
        for location, going in letting.items():
            if not going:
                continue
            held = self._subscriptions.held(name, prepared, location)
            for index, (subscription, archives) in held.items():
                count = len(archives & going)
                if not count:
                    continue
                problems.append(
                    Problem(
                        f"subscriptions.{index}",
                        f"subscription {subscription} has not acked change rows in {count} "
                        f"archive(s) retain: would remove, so they are kept until it does",
                        kind="subscription_behind",
                        fix=f"read and ack them (recon subscribe {name} {subscription}, then "
                        f"recon ack), deliver them (recon deliver {name}), or remove the "
                        "subscription from subscriptions: if nobody reads it",
                        got=count,
                    )
                )
        return problems

    def _snapshots_going(
        self, archives: list[ArchiveRef], rule: RetainSpec, now: datetime, sizes: dict[str, int]
    ) -> set[str]:
        """The archives one entry's ``retain:`` lets go: beyond its newest ``archives``, ended
        more than ``seconds`` before now, or past the newest whose sizes add up to ``bytes``.
        Any one of its rules lets an archive go, as a stream's do."""
        going: set[str] = set()
        if rule.archives is not None:
            going.update(archive.id for archive in archives[: -rule.archives])
        if rule.seconds is not None:
            cutoff = now - timedelta(seconds=rule.seconds)
            for archive in archives:
                ended = _when(self._archives.meta(archive).get("timestamp"), archive.id)
                if ended is not None and ended < cutoff:
                    going.add(archive.id)
        if rule.bytes is not None:
            held = 0
            for at, archive in enumerate(reversed(archives)):
                if archive.id not in sizes:
                    sizes[archive.id] = self._archives.size(archive)
                held += sizes[archive.id]
                if held > rule.bytes and at > 0:
                    going.update(a.id for a in archives[: len(archives) - at])
                    break
        return going

    def _told_in(self, archives: list[ArchiveRef], entries: list[CollectorEntry]) -> set[str]:
        """The last archive each table of an entry with ``changes:`` was told in: what its next
        comparison reads, kept whatever a rule says."""
        changing = {entry.name for entry in entries if entry.changes is not None}
        found: dict[tuple[Any, Any], str] = {}
        for archive in reversed(archives):
            for table in self._archives.meta(archive).get("tables") or []:
                told = table.get("changes")
                if table.get("collector") not in changing or not isinstance(told, dict):
                    continue
                if told.get("error"):
                    continue
                found.setdefault((table.get("name"), table.get("source")), archive.id)
        return set(found.values())


def windows_going(
    archives: Archives,
    windows: list[ArchiveRef],
    rule: RetainSpec,
    now: datetime,
    sizes: dict[str, int],
) -> set[str]:
    """The windows of a stream (oldest first) ``rule`` lets go at ``now``: each that
    ``archives`` leaves out, that closed more than ``seconds`` before it, or that the newest
    windows' ``bytes`` leave no room for. Each window's size is read once into ``sizes``."""
    going: set[str] = set()
    if rule.archives is not None:
        going.update(window.id for window in windows[: -rule.archives])
    if rule.seconds is not None:
        cutoff = now - timedelta(seconds=rule.seconds)
        for window, after in zip(windows, windows[1:], strict=False):
            closed = window_opened(after.id)  # a window closes as the next opens
            if closed is not None and closed < cutoff:
                going.add(window.id)
    if rule.bytes is not None:
        held = 0
        for at, window in enumerate(reversed(windows)):
            if window.id not in sizes:
                sizes[window.id] = archives.size(window)
            held += sizes[window.id]
            if held > rule.bytes and at > 0:
                going.update(w.id for w in windows[: len(windows) - at])
                break
    return going


def _when(stamp: Any, archive_id: str) -> datetime | None:
    """When an archive ended, as its meta says, else when it started, as its id says; None
    when neither can be read."""
    if isinstance(stamp, str):
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(stamp)
    with contextlib.suppress(ValueError):
        return datetime.strptime(archive_id, ARCHIVE_ID_FORMAT).replace(tzinfo=UTC)
    return None

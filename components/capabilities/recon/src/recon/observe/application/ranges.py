"""Each ranged entry's run, planned over what was committed (:mod:`recon.core.domain.ranges`).

A table's checkpoint is read back from what was landed, never from a file of its own: the
latest range its output and source committed, in the order the mission's archives were made,
from the archives kept and the tombstones of those retired alike, so retention never loses
it. The survey that runs a mission holds the mission while it reads them and while it lands,
so no other survey's commit can come between.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from recon.core import (
    Interval,
    Problem,
    RangeRun,
    UsageError,
    accepted_kinds,
    archive_rank,
    committed_in,
    committed_kept,
    fan_out,
)
from recon.observe.domain.spans import plan_range

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping
    from datetime import datetime

    from recon.core import (
        Archives,
        CollectorEntry,
        CollectorType,
        Committed,
        ReconConfig,
        SourceEntry,
    )

#: A table of a mission, by its output and its source.
Key = tuple[str, "str | None"]


@dataclass(frozen=True)
class RangeRuns:
    """The run of each ranged table of a survey, by output, and what planning them found: the
    problems that refuse the survey, and the warnings it goes on with."""

    runs: dict[str, RangeRun] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)


def request_digest(entry: CollectorEntry, source: SourceEntry | None) -> str:
    """The digest of the question an entry asks its source: its type's own settings as
    written (placeholders unfilled), its type and ``extract:``, and the source by name.
    ``normalize:``, ``range:`` and every other key recon reads are no part of it, so how the
    records are shaped, or the range's own keys, never strand a checkpoint."""
    asked = {"type": entry.type, "settings": entry.settings, "extract": entry.extract,
             "source": source.name if source is not None else None}  # fmt: skip
    text = json.dumps(asked, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def checkpoints(archives: Archives, mission: str, keys: Collection[Key]) -> dict[Key, Committed]:
    """The latest range each table of ``keys`` committed, of any request, in the order the
    mission's archives were made: the archives kept and the retired ones' tombstones read as
    one order. A table that committed none has no entry."""
    wanted = set(keys)
    if not wanted:
        return {}
    retired: dict[Key, tuple[tuple[int, int, str], Committed]] = {}
    for stone in archives.retired(mission):
        index = stone.get("index")
        rank = archive_rank(str(stone.get("id")), index if type(index) is int else None)
        for output, source, committed in committed_kept(stone):
            key = (output, source)
            if key in wanted and (key not in retired or retired[key][0] < rank):
                retired[key] = (rank, committed)
    found: dict[Key, Committed] = {}
    for ref in reversed(archives.archives(mission)):
        open_keys = wanted - set(found)
        if not open_keys:
            break
        for key in list(open_keys):
            if key in retired and retired[key][0] > ref.rank:
                found[key] = retired[key][1]
        here = {(o, s): c for o, s, c in committed_in(archives.meta(ref))}
        for key in wanted - set(found):
            if key in here:
                found[key] = here[key]
    for key, (_, kept) in retired.items():
        found.setdefault(key, kept)
    return found


def committed_intervals(archives: Archives, mission: str, key: Key) -> list[Interval]:
    """Every interval the table ``key`` committed, in the archives kept and their tombstones."""
    intervals = [c.interval for o, s, c in _kept(archives, mission) if (o, s) == key]
    for ref in archives.archives(mission):
        intervals += [c.interval for o, s, c in committed_in(archives.meta(ref)) if (o, s) == key]
    return intervals


def _kept(archives: Archives, mission: str) -> list[tuple[str, str | None, Committed]]:
    return [found for stone in archives.retired(mission) for found in committed_kept(stone)]


def range_runs(
    archives: Archives,
    mission: str,
    config: ReconConfig,
    types: Mapping[str, CollectorType],
    now: datetime,
    *,
    interval: Interval | None = None,
    rebase: Collection[str] = (),
    restart: Collection[str] = (),
) -> RangeRuns:
    """The run of each table of ``config`` whose entry has ``range:``, at ``now``, after the
    checkpoint each committed: ``interval`` overrides every one (a backfill, which moves no
    checkpoint); ``rebase`` and ``restart`` name outputs whose run goes on from the checkpoint
    under a changed request, or begins again from ``start``. Raises UsageError for a
    ``rebase`` or ``restart`` output no ranged entry makes, and for an ``interval`` when no
    entry is ranged."""
    catalog = {source.name: source for source in config.catalog}
    targets: list[tuple[int, CollectorEntry, SourceEntry | None, str]] = []
    for i, entry in enumerate(config.collectors):
        if entry.range is None:
            continue
        for source, output in fan_out(entry, catalog, accepted_kinds(types, entry)):
            targets.append((i, entry, source, output))
    outputs = {output for _, _, _, output in targets}
    if interval is not None and not targets:
        raise UsageError(
            "--range asks each entry with range: for its interval, and this mission has none",
            fix="give range: to the entries to backfill (recon --skill -r ranges), or survey "
            "without --range",
        )
    for flag, named in (("--rebase", rebase), ("--restart", restart)):
        unknown = sorted(set(named) - outputs)
        if unknown:
            raise UsageError(
                f"{flag} names {', '.join(unknown)}, which no entry with range: makes; its "
                f"outputs with range: {', '.join(sorted(outputs)) or '(none)'}",
                fix=f"name one of them, as {flag} <output>",
                details={"value": unknown, "allowed": sorted(outputs)},
            )
    found = checkpoints(archives, mission, [(o, s.name if s else None) for *_, s, o in targets])
    result = RangeRuns()
    for i, entry, source, output in targets:
        assert entry.range is not None
        key = (output, source.name if source is not None else None)
        run = plan_range(
            entry.range, now, request_digest(entry, source), found.get(key),
            override=interval, rebase=output in rebase, restart=output in restart,
        )  # fmt: skip
        result.runs[output] = run
        at = f"collectors.{i}.range"
        result.problems.extend(
            Problem(at, f"{output}: {message}", kind=kind, fix=fix, got=output)
            for kind, message, fix in run.refused
        )
        result.warnings.extend(_said(at, output, entry, run))
        committed = committed_intervals(archives, mission, key) if interval is not None else []
        if interval is not None and _overlaps(interval, committed):
            result.warnings.append(Problem(
                at,
                f"{output}: the interval asked for overlaps source time its table committed: "
                "those records are collected again, in this archive alone",
                kind="range_overlaps",
                fix="narrow --range to source time not yet collected, or keep the overlap if "
                "it is a re-read on purpose",
                got=output,
            ))  # fmt: skip
    return result


def _said(at: str, output: str, entry: CollectorEntry, run: RangeRun) -> list[Problem]:
    """The warnings a run's range gives: a backlog, nothing new yet, or a fixed range done."""
    said = []
    if run.behind_s > 0:
        said.append(Problem(
            at,
            f"{output}: {run.behind_s:g}s of source time are left behind this run, past its "
            f"{len(run.steps)} step(s): the next runs go on from its end",
            kind="range_behind",
            fix=f"let the next runs drain it, or raise range.max_steps or range.step on "
            f"collector {entry.name!r}",
            got=run.behind_s,
        ))  # fmt: skip
    if run.complete:
        said.append(Problem(
            at,
            f"{output}: its fixed range is collected to its end, so this run asks for nothing",
            kind="range_complete",
            fix="nothing to do; move range.end to collect further, or remove range.end to "
            "go on as time passes",
            got=output,
        ))  # fmt: skip
    elif run.current:
        said.append(Problem(
            at,
            f"{output}: nothing past range.settle is new since its checkpoint, so this run "
            "asks for nothing and its table is empty",
            kind="range_current",
            fix="nothing to do; the next run past a step asks for it",
            got=output,
        ))  # fmt: skip
    return said


def _overlaps(interval: Interval, committed: list[Interval]) -> bool:
    return any(interval.start < kept.end and kept.start < interval.end for kept in committed)

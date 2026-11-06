"""Ranges, property-based (owner rulings R1 and R-TOMB): random histories of a ranged mission,
each step of which is a scheduled tick, a survey by hand, a survey whose source fails partway,
a survey killed just before or just after its commit, or a backfill, with retention removing
archives as it goes. After every step, over what the mission committed (the archives kept and
the tombstones of those retired):

- **contiguous and disjoint**: the committed ranges, in order, each begin where the last
  ended, so no source time is collected twice and none is left between;
- **never asked twice**: no run that moves the checkpoint asks for source time committed
  before it began, so a refetch is only ever of what was never committed;
- **what was committed is there**: each kept archive's table holds one record per step of the
  range it committed, its ``_range`` that step, tiling the range exactly;
- **it catches up**: once the faults stop, runs drain the backlog to the horizon.

``hypothesis`` is not a dev dependency, so the properties run over seeded ``random`` histories,
and a failure names its seed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from recon.composition import root
from recon.domain.errors import CollectionError
from recon.domain.ranges import Interval, committed_in, committed_kept, iso
from tests.recon_fakes import FixedClock, LandedThrough, WrappingArchives, files_of
from tests.test_ranges import StepsSettings, _plugins

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from recon.composition import Recon
    from recon.domain.archives import ArchiveRef, Landed
    from recon.domain.capture import CaptureLog
    from recon.domain.models import CollectorEntry, SourceEntry

HISTORIES = 25
EVENTS = 24
START = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
KEY = ("metrics", None)


class Killed(BaseException):
    """The process dying where the test says: no ``except Exception`` in recon stops it."""


@dataclass
class Faults:
    #: Kill the next survey just ``before`` or just ``after`` its meta commits.
    kill: str | None = None
    #: Fail the next survey's source on its call with this index (0 is the first).
    fail_at: int | None = None
    calls: int = 0


class Killing(LandedThrough):
    def __init__(self, inner: Landed, faults: Faults) -> None:
        super().__init__(inner)
        self._faults = faults

    def record(self, meta: Mapping[str, Any]) -> None:
        when, self._faults.kill = self._faults.kill, None
        if meta.get("ended") is None or when is None:
            if when is not None:
                self._faults.kill = when  # an interim record: kill at the commit itself
            self.inner.record(meta)
            return
        if when == "after":
            self.inner.record(meta)
        raise Killed


@dataclass
class Asked:
    """The source: each interval it was asked for, and by whether its run moved anything."""

    faults: Faults
    asked: list[tuple[str, str]] = field(default_factory=list)

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
    ) -> Iterator[dict[str, Any]]:
        asked = StepsSettings.model_validate(entry.settings)
        call, self.faults.calls = self.faults.calls, self.faults.calls + 1
        if self.faults.fail_at is not None and call >= self.faults.fail_at:
            raise CollectionError("the source is down", kind="transient")
        self.asked.append((asked.since, asked.until))
        yield {"id": asked.since, "since": asked.since, "until": asked.until}


class History:
    def __init__(self, project: Path, rng: random.Random) -> None:
        self.project = project
        self.rng = rng
        self.clock = FixedClock(START)
        self.faults = Faults()
        self.source = Asked(self.faults)
        step = rng.choice([300, 600, 900])
        spec: dict[str, Any] = {"step": step, "max_steps": rng.randint(1, 4),
                                "settle": rng.choice([0, 30, 60])}  # fmt: skip
        if rng.random() < 0.5:
            spec["align"] = rng.choice([60, step])
        if rng.random() < 0.5:
            spec["start"] = iso(START - timedelta(seconds=step * rng.randint(1, 8)))
        self.spec = spec
        entry: dict[str, Any] = {"name": "metrics", "type": "steps", "since": "{range.start}",
                                 "until": "{range.end}", "range": spec}  # fmt: skip
        if rng.random() < 0.6:
            entry["retain"] = {"archives": rng.randint(1, 3)}
        mission = project / ".recon" / "m"
        mission.mkdir(parents=True, exist_ok=True)
        (mission / "config.yaml").write_text(yaml.safe_dump({"collectors": [entry]}))
        self.trail: list[str] = [f"spec {spec}, retain {entry.get('retain')}"]
        #: The archives whose tables were checked to tile what they committed.
        self.tiled: set[str] = set()
        #: Each archive's meta, read once: a survey done never records it again.
        self.metas: dict[str, Mapping[str, Any]] = {}
        self.plugins = _plugins(self.source)

    @property
    def recon(self) -> Recon:
        def archives(state: Path) -> WrappingArchives:
            return WrappingArchives(state, self.clock, lambda landed: Killing(landed, self.faults))

        return root({}, project=self.project, clock=self.clock, plugins=self.plugins,
                    archives=archives)  # fmt: skip

    def committed(self) -> list[Interval]:
        """Every non-empty range committed, in order: the oracle's own reading of the archives
        kept and the tombstones, through the read port."""
        files = files_of(self.project, self.clock)
        found = [kept.interval for stone in files.retired("m")
                 for output, source, kept in committed_kept(stone)
                 if (output, source) == KEY]  # fmt: skip
        for ref in files.archives("m"):
            if ref.id not in self.metas:
                self.metas[ref.id] = files.meta(ref)
            found += [kept.interval for output, source, kept in committed_in(self.metas[ref.id])
                      if (output, source) == KEY]  # fmt: skip
        return sorted((i for i in found if i.seconds > 0), key=lambda i: i.start)

    def survey(self, interval: tuple[datetime, datetime] | None = None) -> None:
        self.faults.calls = 0
        try:
            self.recon.survey("m", interval=interval)
        except Killed:
            self.trail.append("  killed")
        finally:
            self.faults.fail_at = None
            self.faults.kill = None

    def event(self) -> None:
        rng = self.rng
        what = rng.choice(["tick", "tick", "manual", "fail", "kill_before", "kill_after",
                           "backfill"])  # fmt: skip
        if what == "tick":
            self.clock.advance(600 - self.clock.now().timestamp() % 600 + rng.randint(0, 5))
        else:
            self.clock.advance(rng.randint(1, 1500))
        self.trail.append(f"{what} at {iso(self.clock.now())}")
        if what == "fail":
            self.faults.fail_at = rng.randint(0, self.spec["max_steps"] - 1)
        elif what.startswith("kill"):
            self.faults.kill = what.removeprefix("kill_")
        if what != "backfill":
            self.survey()
            return
        step = timedelta(seconds=self.spec["step"])
        end = self.clock.now() - timedelta(hours=rng.randint(0, 6))
        length = step * rng.randint(1, self.spec["max_steps"])
        self.survey((end - length, end))


def _check(history: History, before: list[Interval], asked_from: int) -> None:
    committed = history.committed()
    for earlier, later in zip(committed, committed[1:], strict=False):
        assert earlier.end == later.start, (
            f"committed ranges {earlier} and {later} are not contiguous and disjoint"
        )
    if "start" in history.spec and committed:
        first = _down(datetime.fromisoformat(history.spec["start"]), history.spec)
        assert committed[0].start == first, "the first range begins at range.start"
    files = files_of(history.project, history.clock)
    for ref in files.archives("m"):
        if ref.id in history.tiled:
            continue  # an archive's tables never change once its survey is done
        history.tiled.add(ref.id)
        for output, _, kept in committed_in(history.metas.get(ref.id) or files.meta(ref)):
            _tiles(files, ref, output, kept.interval)
    for since, until in history.source.asked[asked_from:]:
        step = Interval(datetime.fromisoformat(since), datetime.fromisoformat(until))
        for old in before:
            overlaps = step.start < old.end and old.start < step.end
            assert not overlaps or history.trail[-1].startswith("backfill"), (
                f"{step} was asked again, though {old} was committed before the run began"
            )


def _tiles(files: Any, ref: ArchiveRef, output: str, interval: Interval) -> None:
    records = files.records(ref, output)
    assert records is not None, f"{ref.id} committed {output} and holds no table"
    steps = [r["_range"] for r in records if not r.get("_empty")]
    at = iso(interval.start)
    for step in steps:
        assert step["start"] == at, f"{ref.id}: steps {steps} do not tile {interval}"
        at = step["end"]
    assert at == iso(interval.end), f"{ref.id}: steps {steps} do not tile {interval}"


@pytest.mark.parametrize("seed", range(HISTORIES))
def test_committed_ranges_are_contiguous_disjoint_and_never_asked_twice(
    tmp_path: Path, seed: int
) -> None:
    history = History(tmp_path, random.Random(seed))
    try:
        for _ in range(EVENTS):
            before, asked_from = history.committed(), len(history.source.asked)
            history.event()
            _check(history, before, asked_from)
        # The faults stop: it catches up within the runs its backlog needs, max_steps a run
        # (each run a second after the last, so the horizon barely moves).
        settle = timedelta(seconds=history.spec["settle"])
        per_run = history.spec["step"] * history.spec["max_steps"]
        committed = history.committed()
        since = committed[-1].end if committed else history.clock.now() - 2 * settle
        backlog = (history.clock.now() - since).total_seconds()
        for _ in range(int(backlog // per_run) + 3):
            history.clock.advance(1)
            before, asked_from = history.committed(), len(history.source.asked)
            history.trail.append(f"catch up at {iso(history.clock.now())}")
            history.survey()
            _check(history, before, asked_from)
            end = history.committed()[-1].end if history.committed() else None
            if end is not None and end >= _down(history.clock.now() - settle, history.spec):
                break
        else:
            raise AssertionError(f"a backlog of {backlog:g}s was not drained {per_run}s a run")
    except AssertionError as failed:
        trail = "\n".join(history.trail)
        raise AssertionError(f"seed {seed}: {failed}\n{trail}") from failed


def _down(at: datetime, spec: dict[str, Any]) -> datetime:
    seconds = int(at.timestamp())
    align = int(spec.get("align") or 1)
    return datetime.fromtimestamp(seconds - seconds % align, tz=UTC)

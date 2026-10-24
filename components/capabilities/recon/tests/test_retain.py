"""``retain:`` (ruling B4): which sealed windows a stream keeps. ``all`` (the default) removes
nothing; a rule removes the oldest sealed windows it does not keep, and leaves a tombstone for
each, so a hole in the chain reads as retention, not loss. A window not sealed is never
removed, and the newest is always kept."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from recon.adapters._out.archive_files import ArchiveFiles
from recon.domain.stream import RetainSpec, WindowSpec, window_id
from tests.recon_fakes import VirtualClock
from tests.test_streams import Script, _plan, _run

if TYPE_CHECKING:
    from recon.domain.stream import Retain


def _stream(
    tmp_path: Path, retain: Retain, *, count: int = 50, every: float = 1.0
) -> tuple[ArchiveFiles, list[Any]]:
    clock = VirtualClock()
    store = ArchiveFiles(clock)
    plan = _plan(WindowSpec(seconds=10))
    plan = type(plan)(**{**plan.__dict__, "retain": retain})
    timeline = [(i * every, {"seq": i}) for i in range(count)]
    _, outcomes, _ = _run(tmp_path, clock, plan, Script(clock, timeline), store=store)
    return store, outcomes


def _kept(store: ArchiveFiles, project: Path) -> list[int]:
    return [int(w.name[:8]) for w in store.archives(project, stream="s")]


class TestRules:
    def test_all_keeps_every_window(self, tmp_path: Path) -> None:
        store, outcomes = _stream(tmp_path, "all")

        assert _kept(store, tmp_path) == [o.index for o in outcomes]
        assert store.retired(tmp_path, "s") == []

    def test_windows_keeps_the_newest_n_and_tombstones_the_rest(self, tmp_path: Path) -> None:
        store, outcomes = _stream(tmp_path, RetainSpec(windows=2))

        assert len(outcomes) == 5
        assert _kept(store, tmp_path) == [3, 4]
        tombstones = store.retired(tmp_path, "s")
        assert [t["index"] for t in tombstones] == [0, 1, 2]
        first = tombstones[0]
        assert first["id"] == outcomes[0].id
        assert first["records"] == 10
        assert first["rule"] == {"windows": 2}
        assert first["retired_at"]

    def test_seconds_keeps_the_windows_closed_within_that_long(self, tmp_path: Path) -> None:
        store, _ = _stream(tmp_path, RetainSpec(seconds=25))

        # The stream ends at 49 s: windows that closed by 24 s ([0, 10) and [10, 20)) go.
        assert _kept(store, tmp_path) == [2, 3, 4]

    def test_bytes_keeps_the_newest_that_fit_and_always_the_newest(self, tmp_path: Path) -> None:
        store, outcomes = _stream(tmp_path, RetainSpec(bytes=1))

        assert _kept(store, tmp_path) == [outcomes[-1].index]

    def test_a_window_not_sealed_is_never_removed(self, tmp_path: Path) -> None:
        clock = VirtualClock()
        store = ArchiveFiles(clock)
        store.hold_stream(tmp_path, "s")
        dead = store.create(tmp_path, window_id(0, clock.now()), stream="s")
        (dead / "s.jsonl.tmp").write_text('{"seq": 0}\n')
        store.release(dead)
        store.release_stream(tmp_path, "s")

        plan = _plan(WindowSpec(seconds=10))
        plan = type(plan)(**{**plan.__dict__, "retain": RetainSpec(windows=1)})
        timeline = [(i * 1.0, {"seq": i}) for i in range(30)]
        _run(tmp_path, clock, plan, Script(clock, timeline), store=store)

        kept = store.archives(tmp_path, stream="s")
        assert dead in kept, "a window its writer never sealed is kept, as evidence"
        assert len(kept) == 2


class TestTombstones:
    def test_tombstones_are_lines_of_one_file_beside_the_windows(self, tmp_path: Path) -> None:
        """Review item 10 (Knuth): one append-only ``retired.jsonl``, so listing a stream's
        windows costs the windows kept, and counting the retired costs no parsing."""
        store, outcomes = _stream(tmp_path, RetainSpec(windows=1))

        stream = tmp_path / "streams" / "s"
        lines = (stream / "retired.jsonl").read_text().splitlines()
        assert [json.loads(line)["id"] for line in lines] == [o.id for o in outcomes[:-1]]
        assert sorted(p.name for p in stream.iterdir()) == sorted(
            ["retired.jsonl", "writer.lock", outcomes[-1].id]
        ), "no file per retired window"
        assert store.retired_count(tmp_path, "s") == len(outcomes) - 1
        assert not (stream / outcomes[0].id).exists()
        newest = yaml_of(Path(outcomes[-1].archive))
        assert newest["stream"]["previous"] == outcomes[-2].id, "the chain still names it"


@pytest.mark.parametrize("retain", [RetainSpec(windows=3), RetainSpec(seconds=1)])
def test_every_window_is_kept_or_has_a_tombstone(tmp_path: Path, retain: RetainSpec) -> None:
    store, outcomes = _stream(tmp_path, retain)

    retired = [t["index"] for t in store.retired(tmp_path, "s")]
    assert sorted(retired + _kept(store, tmp_path)) == [o.index for o in outcomes]


def yaml_of(archive: Path) -> dict[str, Any]:
    import yaml

    meta = yaml.safe_load((archive / "meta.yaml").read_text())
    assert isinstance(meta, dict)
    return meta

"""The two layers at work (step M3): an act driven through the collect core alone, and what
the observe layer derives after the core seals (ruling R9).

The import rules of the line are ``test_layering``'s; these hold its behaviour: the core lands
and commits an act whole without the observe layer, and a table the observe layer has not yet
told (a writer that died between the seal and its deriving) is never passed by a reader and is
told in its own archive by the next run.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
from typing import TYPE_CHECKING, Any

import yaml

from recon.composition import root
from tests.recon_fakes import WrappingArchives, kept
from tests.test_changes import A, B, C, Surveys, _mission, _ops, _rows

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from recon.core.domain.archives import ArchiveRef, Hold


def test_importing_the_core_loads_nothing_of_the_observe_layer() -> None:
    """The core alone is a working subset: importing it, its driving port included, imports
    no module of ``recon.observe``."""
    code = (
        "import sys, recon.core\n"
        "from recon.core import Collection, Prepared\n"
        "print(sorted(m for m in sys.modules if m.startswith('recon.observe')))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_one_act_driven_through_the_core_alone_lands_and_commits(tmp_path: Path) -> None:
    """An outside driver runs one act with the core's port alone: the config read and planned,
    the mission held, the snapshot act run and sealed. The archive is whole and finished, and
    what changed is not the core's: the table's changes are left to the observe layer."""
    _mission(tmp_path, {"key": ["id"]})
    surveys = Surveys(tmp_path)
    surveys.inbox.messages = [A, B]
    core = surveys.recon.collection
    prepared = core.prepare("m")
    with core.hold("m") as hold:
        act = core.snapshot(hold, "m", prepared, started=surveys.clock.now())
    assert act.finished and [t.records for t in act.tables] == [2]
    assert act.tables[0].changes is None, "the core derives nothing"
    archive = tmp_path / ".recon" / "m" / "archive" / act.archive.id
    meta = yaml.safe_load((archive / "meta.yaml").read_text())
    assert meta["ended"] == "finished" and meta["config_sha256"] == prepared.sha256
    assert not (archive / "inbox.changes.jsonl").exists()


class _DyingArchives(WrappingArchives):
    def __init__(self, home: Path, clock: Any) -> None:
        super().__init__(home, clock, lambda landed: landed)
        self.left = 1

    @contextlib.contextmanager
    def hold(self, mission: str, *, stream: str | None = None) -> Iterator[Hold]:
        with super().hold(mission, stream=stream) as held:
            yield _DyingHold(held, self)


class _DyingHold:
    def __init__(self, inner: Hold, store: _DyingArchives) -> None:
        self._inner = inner
        self._store = store

    def begin(self, archive_id: str) -> Any:
        return self._inner.begin(archive_id)

    def derive(self, ref: ArchiveRef) -> Any:
        if self._store.left:
            self._store.left -= 1
            raise SystemExit("killed after the seal")
        return self._inner.derive(ref)

    def retire(self, ref: ArchiveRef, tombstone: Any) -> None:
        self._inner.retire(ref, tombstone)


def _dies_after_the_seal(tmp_path: Path) -> tuple[Surveys, Path]:
    _mission(tmp_path, {"key": ["id"]})
    surveys = Surveys(tmp_path)
    surveys.recon = root({}, project=tmp_path, clock=surveys.clock,
                         archives=lambda home: _DyingArchives(home, surveys.clock),
                         requester=lambda t, d, r: surveys.inbox)  # fmt: skip
    with contextlib.suppress(SystemExit):
        surveys.survey(A, B)
    [pending] = sorted((tmp_path / ".recon" / "m" / "archive").iterdir())
    return surveys, pending


def test_a_table_left_pending_holds_its_reader_and_is_told_in_its_own_archive(
    tmp_path: Path,
) -> None:
    """A writer killed after the seal and before its changes leaves the table pending: sealed
    under this config, finished, no change summary. A reader of the output does not pass it;
    the next survey derives it into it first, then tells its own changes against it."""
    surveys, pending = _dies_after_the_seal(tmp_path)
    meta = yaml.safe_load((pending / "meta.yaml").read_text())
    assert meta["ended"] == "finished" and meta["tables"][0]["changes"] is None
    held = surveys.recon.observation.changes_since("m", "inbox")
    assert held.rows == [] and not held.more
    again, _ = surveys.survey(A, B, C)
    assert _ops(pending) == [("insert", "a"), ("insert", "b")], "told in its own archive"
    assert _ops(again) == [("insert", "c")]
    read = surveys.recon.observation.changes_since("m", "inbox", held.cursor)
    assert [(row.archive, row.key) for row in read.rows] == [
        (pending.name, {"id": "a"}), (pending.name, {"id": "b"}), (again.name, {"id": "c"}),
    ], "the reader that waited reads each row once, in archive order"  # fmt: skip


def test_a_table_sealed_under_another_config_is_not_pending(tmp_path: Path) -> None:
    """A table sealed without a change summary under another config may have been sealed
    before ``changes:`` was set: never pending, so a reader passes it and the next survey
    starts the told index again (``rebaselined``), as before."""
    surveys, pending = _dies_after_the_seal(tmp_path)
    _mission(tmp_path, {"key": ["id"], "volatile": ["seen"]})  # another config's bytes
    read = surveys.recon.observation.changes_since("m", "inbox")
    assert read.rows == []
    again, _ = surveys.survey(A, B)
    assert not (pending / "inbox.changes.jsonl").exists()
    assert [row["op"] for row in _rows(again)] == ["rebaselined"]


def test_a_survey_tells_its_changes_after_its_archive_is_sealed(tmp_path: Path) -> None:
    """The change table is written into the sealed archive, and the meta records the change
    summary beside the table's outcome; the archive stays complete."""
    _mission(tmp_path, {"key": ["id"]})
    surveys = Surveys(tmp_path)
    archive, document = surveys.survey(A, B)
    assert document.status == "complete"
    assert document.tables[0].changes is not None and document.tables[0].changes.rows == 2
    meta = yaml.safe_load((archive / "meta.yaml").read_text())
    assert meta["ended"] == "finished" and meta["tables"][0]["changes"]["rows"] == 2
    assert not (archive / ".incomplete").exists()
    assert kept(tmp_path, document) == archive

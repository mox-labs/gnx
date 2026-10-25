"""Ruling S2: every survey of a mission holds the mission while it runs, so two never overlap.
Only a stream refused a second writer before; two snapshot surveys of one mission, two
scheduled ticks that overlap, each wrote an archive from the same sources, and every change
row a later diff draws from them would come twice. A second survey is refused ``busy`` (11),
naming the first one's process, before it fetches or writes anything."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from click.testing import CliRunner

from recon.adapters._in.cli import main
from recon.adapters._out.archive_files import ArchiveFiles
from recon.adapters._out.system_clock import SystemClock
from recon.composition import root
from recon.domain.errors import EXIT_CODES, EXIT_MEANINGS, ReconError
from tests.recon_fakes import plugins_with

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from recon.composition import Recon
    from recon.domain.capture import CaptureLog
    from recon.domain.models import CollectorEntry, SourceEntry


def _store() -> ArchiveFiles:
    return ArchiveFiles(SystemClock())


class Slow:
    """A collector that gives one record once the test lets it go: a survey held mid-table."""

    def __init__(self) -> None:
        self.inside = threading.Event()
        self.go = threading.Event()

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
    ) -> Iterator[dict[str, Any]]:
        self.inside.set()
        assert self.go.wait(10), "the test never let the survey go on"
        yield {"seq": 1}


def _mission(project: Path, name: str = "m") -> None:
    mission = project / ".recon" / name
    mission.mkdir(parents=True)
    config = {"collectors": [{"name": "inbox", "type": "slow"}]}
    (mission / "config.yaml").write_text(yaml.safe_dump(config))


def _root(project: Path, slow: Slow) -> Recon:
    return root({}, project=project, plugins=plugins_with(slow=slow))


class TestTheStoreHoldsAMission:
    def test_a_second_holder_is_refused_busy_until_the_first_lets_go(self, tmp_path: Path) -> None:
        first = _store()
        first.hold_mission(tmp_path)

        with pytest.raises(ReconError, match="another recon is surveying mission") as raised:
            _store().hold_mission(tmp_path)
        assert raised.value.kind == "busy"
        assert raised.value.exit_code == EXIT_CODES["busy"] == 11
        assert raised.value.details["mission"] == tmp_path.name
        first.release_mission(tmp_path)
        _store().hold_mission(tmp_path)

    def test_one_store_holds_a_mission_once(self, tmp_path: Path) -> None:
        """A stream's hold is the store's for as long as it writes; a survey's is one survey's:
        two surveys on two threads of one process share one store, and are still two."""
        store = _store()
        store.hold_mission(tmp_path)

        with pytest.raises(ReconError) as raised:
            store.hold_mission(tmp_path)
        assert raised.value.kind == "busy"
        assert raised.value.fix and "kill" not in raised.value.fix
        assert "this process" in raised.value.fix

    def test_busy_names_the_survey_s_process_and_how_to_stop_it(self, tmp_path: Path) -> None:
        holds = (
            "import sys\n"
            "from pathlib import Path\n"
            "from recon.adapters._out.archive_files import ArchiveFiles\n"
            "from recon.adapters._out.system_clock import SystemClock\n"
            "ArchiveFiles(SystemClock()).hold_mission(Path(sys.argv[1]))\n"
            "print('held', flush=True)\n"
            "sys.stdin.read()\n"
        )
        holder = subprocess.Popen(
            [sys.executable, "-c", holds, str(tmp_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )  # fmt: skip
        try:
            assert holder.stdout is not None and holder.stdout.readline() == "held\n"
            with pytest.raises(ReconError) as raised:
                _store().hold_mission(tmp_path)
        finally:
            holder.communicate("", timeout=10)

        assert raised.value.details == {"mission": tmp_path.name, "pid": holder.pid}
        assert raised.value.fix and f"kill -TERM {holder.pid}" in raised.value.fix
        _store().hold_mission(tmp_path)  # its holder died: the lock went with it

    def test_the_lock_is_one_file_of_the_mission_and_a_reader_sees_no_archive_in_it(
        self, tmp_path: Path
    ) -> None:
        store = _store()
        store.hold_mission(tmp_path)
        store.release_mission(tmp_path)

        assert (tmp_path / "writer.lock").read_text() == "", "no pid of a holder that let go"
        assert store.archives(tmp_path) == []


class TestOneSurveyAtATime:
    def test_a_survey_while_another_runs_is_busy_and_writes_nothing(self, tmp_path: Path) -> None:
        _mission(tmp_path)
        slow = Slow()
        first: list[Any] = []
        running = threading.Thread(target=lambda: first.append(_root(tmp_path, slow).survey("m")))
        running.start()
        try:
            assert slow.inside.wait(10)
            with pytest.raises(ReconError) as raised:
                _root(tmp_path, Slow()).survey("m")
        finally:
            slow.go.set()
            running.join(10)

        assert raised.value.kind == "busy"
        assert raised.value.details["pid"] == os.getpid()
        [archive] = (tmp_path / ".recon" / "m" / "archive").iterdir()
        assert first[0].archive == archive.name and first[0].status == "complete"
        assert _root(tmp_path, slow).survey("m").status == "complete", "free once it returned"

    def test_two_overlapping_surveys_with_changes_tell_each_change_once(
        self, tmp_path: Path
    ) -> None:
        """Erlang's round-4 case, across the slices: a mission with ``changes:`` surveyed twice
        at once (two ticks that overlap) would compare both tables with the same baseline and
        tell the same insert twice. The second is refused ``busy`` before it collects; the
        record is told once, and a later survey of the same answer tells nothing."""
        mission = tmp_path / ".recon" / "m"
        mission.mkdir(parents=True)
        config = {"collectors": [{"name": "inbox", "type": "slow", "changes": {"key": ["seq"]}}]}
        (mission / "config.yaml").write_text(yaml.safe_dump(config))
        slow, second = Slow(), Slow()
        second.go.set()  # were it let in, it would run to its end at once
        first: list[Any] = []
        running = threading.Thread(target=lambda: first.append(_root(tmp_path, slow).survey("m")))
        running.start()
        try:
            assert slow.inside.wait(10)
            with pytest.raises(ReconError) as raised:
                _root(tmp_path, second).survey("m")
        finally:
            slow.go.set()
            running.join(10)
        later = Slow()
        later.go.set()
        again = _root(tmp_path, later).survey("m")

        assert raised.value.kind == "busy" and not second.inside.is_set(), "refused before it ran"
        assert first[0].status == "complete" and again.status == "complete"
        told = [
            (archive.name, json.loads(line))
            for archive in sorted((mission / "archive").iterdir())
            for line in (archive / "inbox.changes.jsonl").read_text().splitlines()
        ]
        rows = [(archive, row["op"], row["key"]) for archive, row in told if "op" in row]
        assert rows == [(first[0].archive, "insert", {"seq": 1})], "each change told once"

    def test_the_command_line_exits_11_with_the_envelope(self, tmp_path: Path) -> None:
        _mission(tmp_path)
        slow = Slow()
        running = threading.Thread(target=lambda: _root(tmp_path, slow).survey("m"))
        running.start()
        try:
            assert slow.inside.wait(10)
            result = CliRunner().invoke(
                main, ["survey", "m", "--json"], obj=_root(tmp_path, Slow()),
                catch_exceptions=False,
            )  # fmt: skip
        finally:
            slow.go.set()
            running.join(10)

        assert result.exit_code == 11
        error = json.loads(result.stderr)["error"]
        assert error["kind"] == "busy" and error["details"]["mission"] == "m"
        assert "survey" in EXIT_MEANINGS["busy"]

    def test_a_dry_run_plans_while_a_survey_runs(self, tmp_path: Path) -> None:
        """Planning writes nothing, so it holds nothing."""
        _mission(tmp_path)
        slow = Slow()
        running = threading.Thread(target=lambda: _root(tmp_path, slow).survey("m"))
        running.start()
        try:
            assert slow.inside.wait(10)
            assert _root(tmp_path, Slow()).plan("m").valid
        finally:
            slow.go.set()
            running.join(10)

    def test_a_config_error_is_said_before_busy(self, tmp_path: Path) -> None:
        """What to fix in the mission is worth more than when to run it."""
        _mission(tmp_path)
        store = _store()
        store.hold_mission(tmp_path / ".recon" / "m")
        (tmp_path / ".recon" / "m" / "config.yaml").write_text("collectors: [{name: a}]\n")

        with pytest.raises(ReconError) as raised:
            _root(tmp_path, Slow()).survey("m")

        assert raised.value.kind == "config"

"""A stream's windows in the archive store (ruling B3): each window is an ordinary archive, a
stream's windows are listed at the cost of that stream alone, and one writer at a time extends a
stream, so a window's id cannot be taken twice."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from recon.adapters._out.archive_files import ArchiveFiles
from recon.adapters._out.system_clock import SystemClock
from recon.domain.errors import EXIT_CODES, EXIT_MEANINGS, EXIT_NEXT, ReconError
from recon.domain.stream import window_id

AT = datetime(2026, 10, 24, 1, 2, 3, tzinfo=UTC)


def _store() -> ArchiveFiles:
    return ArchiveFiles(SystemClock())


class TestAWindowIsAnArchive:
    def test_a_window_is_made_held_and_finished_as_a_survey_s_archive_is(
        self, tmp_path: Path
    ) -> None:
        store, reader = _store(), _store()
        store.hold_stream(tmp_path, "events")
        window = store.create(tmp_path, window_id(0, AT), stream="events")

        assert window == tmp_path / "streams" / "events" / window_id(0, AT)
        assert reader.state(window) == "writing"
        store.write_meta(window, {"format_version": 2, "tables": []})
        store.mark_complete(window)
        store.release(window)
        assert reader.state(window) == "complete"
        assert store.archives(tmp_path) == [], "a window is not one of the survey's archives"

    def test_a_stream_s_windows_are_listed_oldest_first_by_index(self, tmp_path: Path) -> None:
        store = _store()
        store.hold_stream(tmp_path, "events")
        for index in (2, 0, 10, 1):
            store.release(store.create(tmp_path, window_id(index, AT), stream="events"))
        (tmp_path / "streams" / "events" / "notes").mkdir()

        listed = [w.name for w in store.archives(tmp_path, stream="events")]

        assert listed == [window_id(i, AT) for i in (0, 1, 2, 10)]
        assert store.streams(tmp_path) == ["events"]

    def test_listing_one_stream_reads_that_stream_s_directory_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B3: the cost of listing a stream is its windows, not the mission's archives or other
        streams'."""
        store = _store()
        for stream in ("a", "b"):
            store.hold_stream(tmp_path, stream)
            for index in range(3):
                store.release(store.create(tmp_path, window_id(index, AT), stream=stream))
        for second in range(5):
            survey = f"2026-10-24-0102{second:02d}-000000"
            store.release(store.create(tmp_path, survey))
        read: list[Path] = []
        listdir = Path.iterdir

        def counted(self: Path):  # type: ignore[no-untyped-def]
            read.append(self)
            return listdir(self)

        monkeypatch.setattr(Path, "iterdir", counted)
        assert len(store.archives(tmp_path, stream="a")) == 3
        assert read == [tmp_path / "streams" / "a"]


class TestOneWriterAStream:
    def test_a_second_writer_of_a_stream_is_refused_as_busy(self, tmp_path: Path) -> None:
        """``busy``, recon's own kind with its own exit code: ``conflict`` means a config
        would be overwritten, and a writer that holds the stream is another thing."""
        held = _store()
        held.hold_stream(tmp_path, "events")

        with pytest.raises(ReconError, match="another recon is writing stream events") as raised:
            _store().hold_stream(tmp_path, "events")
        assert raised.value.kind == "busy"
        assert raised.value.exit_code == EXIT_CODES["busy"] == 11
        assert raised.value.fix, "what to do: its fix names the writer (the tests below)"
        assert "busy" in EXIT_MEANINGS and "busy" in EXIT_NEXT
        assert "stream" not in EXIT_MEANINGS["conflict"], "conflict keeps its one meaning"
        held.release_stream(tmp_path, "events")
        _store().hold_stream(tmp_path, "events")

    def test_busy_names_the_writer_s_process_and_how_to_stop_it(self, tmp_path: Path) -> None:
        """Polish R4 (Ace 3): ``busy``'s fix said "stop it" without saying which process. The
        writer's pid is kept in the stream's lock, so the error names it, in ``details`` and
        in a fix an agent can run: ``kill -TERM`` seals the window the writer is writing."""
        holds = (
            "import sys\n"
            "from pathlib import Path\n"
            "from recon.adapters._out.archive_files import ArchiveFiles\n"
            "from recon.adapters._out.system_clock import SystemClock\n"
            "ArchiveFiles(SystemClock()).hold_stream(Path(sys.argv[1]), 'events')\n"
            "print('held', flush=True)\n"
            "sys.stdin.read()\n"
        )
        writer = subprocess.Popen(
            [sys.executable, "-c", holds, str(tmp_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )  # fmt: skip
        try:
            assert writer.stdout is not None and writer.stdout.readline() == "held\n"
            with pytest.raises(ReconError) as raised:
                _store().hold_stream(tmp_path, "events")
        finally:
            writer.communicate("", timeout=10)

        assert raised.value.kind == "busy"
        assert raised.value.details == {"stream": "events", "pid": writer.pid}
        assert raised.value.fix and f"kill -TERM {writer.pid}" in raised.value.fix

    def test_busy_in_the_writer_s_own_process_never_says_to_kill_it(self, tmp_path: Path) -> None:
        held = _store()
        held.hold_stream(tmp_path, "events")

        with pytest.raises(ReconError) as raised:
            _store().hold_stream(tmp_path, "events")

        assert raised.value.details["pid"] == os.getpid()
        assert raised.value.fix and "kill" not in raised.value.fix
        assert "this process" in raised.value.fix

    def test_a_stream_reads_writing_while_held_then_its_last_window_s_state(
        self, tmp_path: Path
    ) -> None:
        store, reader = _store(), _store()
        assert reader.stream_state(tmp_path, "events") is None, "a stream never written"
        store.hold_stream(tmp_path, "events")
        assert reader.stream_state(tmp_path, "events") == "writing"
        window = store.create(tmp_path, window_id(0, AT), stream="events")
        store.release(window)
        store.release_stream(tmp_path, "events")
        assert reader.stream_state(tmp_path, "events") == "incomplete", "never marked complete"
        store.hold_stream(tmp_path, "events")
        second = store.create(tmp_path, window_id(1, AT), stream="events")
        store.write_meta(second, {"format_version": 2, "tables": []})
        store.mark_complete(second)
        store.release(second)
        store.release_stream(tmp_path, "events")
        assert reader.stream_state(tmp_path, "events") == "complete"

    def test_a_stream_whose_writer_died_is_no_longer_writing(self, tmp_path: Path) -> None:
        dies = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "from recon.adapters._out.archive_files import ArchiveFiles\n"
            "from recon.adapters._out.system_clock import SystemClock\n"
            "store = ArchiveFiles(SystemClock())\n"
            "store.hold_stream(Path(sys.argv[1]), 'events')\n"
            "store.create(Path(sys.argv[1]), sys.argv[2], stream='events')\n"
            "os._exit(9)\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", dies, str(tmp_path), window_id(0, AT)], check=False
        )

        assert done.returncode == 9
        assert _store().stream_state(tmp_path, "events") == "incomplete"
        _store().hold_stream(tmp_path, "events")


class TestTheLastRecordOfAWindowADeadWriterLeft:
    def test_a_torn_last_line_is_moved_aside_and_the_last_whole_record_read(
        self, tmp_path: Path
    ) -> None:
        store = _store()
        store.hold_stream(tmp_path, "events")
        window = store.create(tmp_path, window_id(0, AT), stream="events")
        live = window / "t.jsonl.tmp"
        live.write_bytes(b'{"n": 1}\n{"n": 2}\n{"n": 3, "cut')

        assert store.last_record(window, "t") == {"n": 2}
        assert live.read_bytes() == b'{"n": 1}\n{"n": 2}\n', "the whole lines are as they were"
        assert (window / "t.jsonl.tmp.torn").read_bytes() == b'{"n": 3, "cut'

    def test_a_sealed_table_s_last_record_is_read_and_nothing_moves(self, tmp_path: Path) -> None:
        store = _store()
        store.hold_stream(tmp_path, "events")
        window = store.create(tmp_path, window_id(0, AT), stream="events")
        store.write_table(window, "t", (r for r in [{"n": 1}, {"n": 2}]))

        assert store.last_record(window, "t") == {"n": 2}
        assert store.last_record(window, "other") is None
        assert sorted(os.listdir(window)) == [".incomplete", "t.jsonl", "writer.lock"]

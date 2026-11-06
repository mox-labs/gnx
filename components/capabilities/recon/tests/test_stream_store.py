"""A stream's windows in the archive adapter (ruling B3): each window is an ordinary archive, a
stream's windows are listed at the cost of that stream alone, and one writer at a time holds a
stream, so a window's id cannot be taken twice."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from recon.adapters._out.mission_layout import STATE
from recon.domain.archives import ArchiveRef
from recon.domain.errors import EXIT_CODES, EXIT_MEANINGS, EXIT_NEXT, ReconError
from recon.domain.stream import window_id
from tests.recon_fakes import files_of

if TYPE_CHECKING:
    from recon.adapters._out.archive_files import ArchiveFiles

AT = datetime(2026, 10, 24, 1, 2, 3, tzinfo=UTC)

#: A child that holds stream ``events`` of mission ``m`` under the project ``sys.argv[1]``.
_HOLDER = (
    "import os, sys\n"
    "from pathlib import Path\n"
    "from recon.adapters._out.archive_files import ArchiveFiles\n"
    "from recon.adapters._out.system_clock import SystemClock\n"
    "store = ArchiveFiles(Path(sys.argv[1]) / '.recon', SystemClock())\n"
)


def _window(index: int) -> ArchiveRef:
    return ArchiveRef("m", window_id(index, AT), "events")


class TestAWindowIsAnArchive:
    def test_a_window_is_made_held_and_finished_as_a_survey_s_archive_is(
        self, tmp_path: Path
    ) -> None:
        store, reader = files_of(tmp_path), files_of(tmp_path)
        with store.hold("m", stream="events") as held:
            with held.begin(window_id(0, AT)) as window:
                assert window.ref == _window(0) and window.ref.index == 0
                assert (tmp_path / STATE / "m" / "streams" / "events" / window_id(0, AT)).is_dir()
                assert reader.state(window.ref) == "writing"
                window.record({"format_version": 2, "tables": []})
                window.complete()
            assert reader.state(window.ref) == "complete"
        assert store.archives("m") == [], "a window is not one of the survey's archives"

    def test_a_stream_s_windows_are_listed_oldest_first_by_index(self, tmp_path: Path) -> None:
        store = files_of(tmp_path)
        with store.hold("m", stream="events") as held:
            for index in (2, 0, 10, 1):
                with held.begin(window_id(index, AT)):
                    pass
        (tmp_path / STATE / "m" / "streams" / "events" / "notes").mkdir()

        listed = store.archives("m", stream="events")

        assert [w.id for w in listed] == [window_id(i, AT) for i in (0, 1, 2, 10)]
        assert [w.index for w in listed] == [0, 1, 2, 10]
        assert store.streams("m") == ["events"]

    def test_listing_one_stream_reads_that_stream_s_directory_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B3: the cost of listing a stream is its windows, not the mission's archives or other
        streams'."""
        store = files_of(tmp_path)
        for stream in ("a", "b"):
            with store.hold("m", stream=stream) as held:
                for index in range(3):
                    with held.begin(window_id(index, AT)):
                        pass
        with store.hold("m") as held:
            for second in range(5):
                with held.begin(f"2026-10-24-0102{second:02d}-000000"):
                    pass
        read: list[Path] = []
        listdir = Path.iterdir

        def counted(self: Path):  # type: ignore[no-untyped-def]
            read.append(self)
            return listdir(self)

        monkeypatch.setattr(Path, "iterdir", counted)
        assert len(store.archives("m", stream="a")) == 3
        assert read == [tmp_path / STATE / "m" / "streams" / "a"]


class TestOneWriterAStream:
    def test_a_second_writer_of_a_stream_is_refused_as_busy(self, tmp_path: Path) -> None:
        """``busy``, recon's own kind with its own exit code: ``conflict`` means a config
        would be overwritten, and a writer that holds the stream is another thing."""
        with (
            files_of(tmp_path).hold("m", stream="events"),
            pytest.raises(ReconError, match="another recon is writing stream events") as raised,
            files_of(tmp_path).hold("m", stream="events"),
        ):
            pass
        assert raised.value.kind == "busy"
        assert raised.value.exit_code == EXIT_CODES["busy"] == 11
        assert raised.value.fix, "what to do: its fix names the writer (the tests below)"
        assert "busy" in EXIT_MEANINGS and "busy" in EXIT_NEXT
        assert "stream" not in EXIT_MEANINGS["conflict"], "conflict keeps its one meaning"
        with files_of(tmp_path).hold("m", stream="events"):
            pass  # let go, so another writer holds it

    def test_one_adapter_holds_a_stream_once(self, tmp_path: Path) -> None:
        """A hold is its writer's alone, in its own adapter too: a second hold of a stream
        already held is refused, never shared."""
        store = files_of(tmp_path)
        with (
            store.hold("m", stream="events"),
            pytest.raises(ReconError) as raised,
            store.hold("m", stream="events"),
        ):
            pass
        assert raised.value.kind == "busy"

    def test_busy_names_the_writer_s_process_and_how_to_stop_it(self, tmp_path: Path) -> None:
        """Polish R4 (Ace 3): ``busy``'s fix said "stop it" without saying which process. The
        writer's pid is kept in the stream's lock, so the error names it, in ``details`` and
        in a fix an agent can run: ``kill -TERM`` seals the window the writer is writing."""
        holds = _HOLDER + (
            "with store.hold('m', stream='events'):\n"
            "    print('held', flush=True)\n"
            "    sys.stdin.read()\n"
        )
        writer = subprocess.Popen(
            [sys.executable, "-c", holds, str(tmp_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )  # fmt: skip
        try:
            assert writer.stdout is not None and writer.stdout.readline() == "held\n"
            with pytest.raises(ReconError) as raised, files_of(tmp_path).hold("m", stream="events"):
                pass
        finally:
            writer.communicate("", timeout=10)

        assert raised.value.kind == "busy"
        assert raised.value.details == {"stream": "events", "pid": writer.pid}
        assert raised.value.fix and f"kill -TERM {writer.pid}" in raised.value.fix

    def test_busy_in_the_writer_s_own_process_never_says_to_kill_it(self, tmp_path: Path) -> None:
        with (
            files_of(tmp_path).hold("m", stream="events"),
            pytest.raises(ReconError) as raised,
            files_of(tmp_path).hold("m", stream="events"),
        ):
            pass

        assert raised.value.details["pid"] == os.getpid()
        assert raised.value.fix and "kill" not in raised.value.fix
        assert "this process" in raised.value.fix

    def test_a_stream_reads_writing_while_held_then_its_last_window_s_state(
        self, tmp_path: Path
    ) -> None:
        store, reader = files_of(tmp_path), files_of(tmp_path)
        assert reader.stream_state("m", "events") is None, "a stream never written"
        with store.hold("m", stream="events") as held:
            assert reader.stream_state("m", "events") == "writing"
            with held.begin(window_id(0, AT)):
                pass
        assert reader.stream_state("m", "events") == "incomplete", "never marked complete"
        with store.hold("m", stream="events") as held, held.begin(window_id(1, AT)) as second:
            second.record({"format_version": 2, "tables": []})
            second.complete()
        assert reader.stream_state("m", "events") == "complete"

    def test_a_stream_whose_writer_died_is_no_longer_writing(self, tmp_path: Path) -> None:
        dies = _HOLDER + (
            "with store.hold('m', stream='events') as held, held.begin(sys.argv[2]):\n"
            "    os._exit(9)\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", dies, str(tmp_path), window_id(0, AT)], check=False
        )

        assert done.returncode == 9
        assert files_of(tmp_path).stream_state("m", "events") == "incomplete"
        with files_of(tmp_path).hold("m", stream="events"):
            pass

    def test_a_stream_name_that_would_leave_its_directory_is_refused(self, tmp_path: Path) -> None:
        """A reference is input to the adapter: a name that is not one name of its own never
        becomes a path."""
        store: ArchiveFiles = files_of(tmp_path)
        for name in ("../escape", ".hidden", "a/b", ""):
            with pytest.raises(ReconError) as raised, store.hold("m", stream=name):
                pass
            assert raised.value.kind == "usage"
        with pytest.raises(ReconError) as refused:
            store.state(ArchiveRef("m", "../../etc", "events"))
        assert refused.value.kind == "usage"
        assert not (tmp_path / "escape").exists()


class TestTheLastRecordOfAWindowADeadWriterLeft:
    def test_a_torn_last_line_is_moved_aside_and_the_last_whole_record_read(
        self, tmp_path: Path
    ) -> None:
        store = files_of(tmp_path)
        with store.hold("m", stream="events") as held, held.begin(window_id(0, AT)) as window:
            pass
        where = tmp_path / STATE / "m" / "streams" / "events" / window_id(0, AT)
        live = where / "t.jsonl.tmp"
        live.write_bytes(b'{"n": 1}\n{"n": 2}\n{"n": 3, "cut')

        assert store.last_record(window.ref, "t") == {"n": 2}
        assert live.read_bytes() == b'{"n": 1}\n{"n": 2}\n', "the whole lines are as they were"
        assert (where / "t.jsonl.tmp.torn").read_bytes() == b'{"n": 3, "cut'

    def test_a_sealed_table_s_last_record_is_read_and_nothing_moves(self, tmp_path: Path) -> None:
        store = files_of(tmp_path)
        with store.hold("m", stream="events") as held, held.begin(window_id(0, AT)) as window:
            window.write("t", (r for r in [{"n": 1}, {"n": 2}]))

        assert store.last_record(window.ref, "t") == {"n": 2}
        assert store.last_record(window.ref, "other") is None
        where = tmp_path / STATE / "m" / "streams" / "events" / window_id(0, AT)
        assert sorted(os.listdir(where)) == [".incomplete", "t.jsonl"]

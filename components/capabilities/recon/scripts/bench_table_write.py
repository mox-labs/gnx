"""Measure what writing a table so a reader sees it as it goes costs, against the buffered
writer it replaced.

    python bench_table_write.py [--records N] [--repeats R]

Ruling A2: a table's records are made visible promptly, each line whole; flush per line,
measured, and past a 5% loss on a large ``cli`` output, write every 64 KiB or 250 ms
instead. Three table writers are compared:

* ``buffered``: the writer before A2, buffered and flushed every 1000 records;
* ``per line``: one ``write`` per line (``ArchiveFiles(flush_bytes=1)``);
* ``as built``: the store as it is, whole lines written every 64 KiB or 250 ms.

All three fsync every 1000 records. Each is timed two ways, for two shapes of line (``mixed``,
records of 50-110 bytes like a code search's; ``short``, records of about 12 bytes):

* ``survey``: a ``cli`` survey, through recon's own root, whose command prints N JSON lines;
* ``writer``: the store's ``write_table`` alone, over N records held in memory.

Every timing is run once untimed first, the writers' order rotates from one repeat to the
next, and each writer's median is printed with its interquartile range and its loss against
``buffered``'s median. The command runs locally and reaches no network. It is not a test and
runs in no suite: timings are the machine's, not a requirement.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shlex
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from recon.adapters._out.archive_files import EMPTY_SENTINEL, FLUSH_BYTES, ArchiveFiles
from recon.adapters._out.system_clock import SystemClock
from recon.composition import root

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Sequence


class BufferedArchiveFiles(ArchiveFiles):
    """The store with the table writer recon had before A2: buffered, flushed every 1000."""

    def write_table(
        self,
        archive: Path,
        output: str,
        records: Generator[dict[str, Any], None, None],
        *,
        columns: Sequence[str] = (),
    ) -> int:
        path = archive / f"{output}.jsonl"
        tmp = path.with_suffix(".jsonl.tmp")
        count = 0
        with contextlib.closing(records), open(tmp, "w") as f:
            for record in records:
                f.write(json.dumps(record, default=str) + "\n")
                count += 1
                if count % 1000 == 0:
                    f.flush()
                    os.fsync(f.fileno())
            if count == 0:
                f.write(json.dumps(EMPTY_SENTINEL) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return count


#: Each shape of line: a record for the n-th line, as Python source the command evaluates.
SHAPES = {
    "mixed": "{'n': n, 'path': f'src/module_{n % 97}.py', 'line': n % 400, "
    "'text': 'TODO: ' + 'x' * (n % 60)}",
    "short": "{'n': n}",
}


def _writer(name: str, clock: SystemClock) -> ArchiveFiles:
    if name == "buffered":
        return BufferedArchiveFiles(clock)
    return ArchiveFiles(clock, flush_bytes=1 if name == "per line" else FLUSH_BYTES)


def _survey(project: Path, archives: ArchiveFiles) -> float:
    started = time.perf_counter()
    document = root({}, project=project, archives=archives).survey("bench")
    elapsed = time.perf_counter() - started
    assert document.status == "complete", document.tables
    return elapsed


def _write(directory: Path, archives: ArchiveFiles, records: list[dict[str, Any]]) -> float:
    started = time.perf_counter()
    archives.write_table(directory, "t", (record for record in records))
    return time.perf_counter() - started


def _spread(times: list[float]) -> tuple[float, float, float]:
    """The median, and the first and third quartiles."""
    q1, median, q3 = statistics.quantiles(times, n=4, method="inclusive")
    return median, q1, q3


def _report(title: str, timings: dict[str, list[float]]) -> None:
    base = statistics.median(timings["buffered"])
    print(title)
    for name, times in timings.items():
        median, q1, q3 = _spread(times)
        loss = (median - base) / base * 100
        print(f"  {name:<9} {median:.3f}s  IQR {q1:.3f}-{q3:.3f}s  {loss:+.1f}% (bound: 5%)")


def _timed(names: list[str], repeats: int, run: Callable[[str], float]) -> dict[str, list[float]]:
    """Each writer run once untimed, then ``repeats`` times, the order rotating per repeat."""
    for name in names:
        run(name)
    timings: dict[str, list[float]] = {name: [] for name in names}
    for repeat in range(repeats):
        for name in names[repeat % len(names) :] + names[: repeat % len(names)]:
            timings[name].append(run(name))
    return timings


def _bench(shape: str, record: str, records: int, repeats: int) -> None:
    """Time the writers on lines of ``shape``, each the record ``record`` evaluates to."""
    clock = SystemClock()
    names = ["buffered", "per line", "as built"]
    with tempfile.TemporaryDirectory() as directory:
        project = Path(directory)
        mission = project / ".recon" / "bench"
        mission.mkdir(parents=True)
        script = project / "print_lines.py"
        script.write_text(
            "import json, sys\n"
            f"for n in range({records}):\n"
            f"    sys.stdout.write(json.dumps({record}) + '\\n')\n"
        )
        command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
        config = {"collectors": [{"name": "lines", "type": "cli", "run": command}]}
        (mission / "config.yaml").write_text(yaml.safe_dump(config))
        surveys = _timed(names, repeats, lambda name: _survey(project, _writer(name, clock)))
        held = [eval(record, {}, {"n": n}) for n in range(records)]
        writers = _timed(names, repeats, lambda name: _write(project, _writer(name, clock), held))
    _report(f"{shape} lines, survey:", surveys)
    _report(f"{shape} lines, writer alone:", writers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--records", type=int, default=200_000)
    parser.add_argument("--repeats", type=int, default=9)
    args = parser.parse_args(argv)
    print(f"{args.records} records, {args.repeats} timed runs per writer after a warmup")
    for shape, record in SHAPES.items():
        _bench(shape, record, args.records, args.repeats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

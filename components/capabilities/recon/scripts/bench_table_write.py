"""Measure what writing a table so a reader sees it as it goes costs, against the buffered
writer it replaced.

    python bench_table_write.py [--records N] [--repeats R]

Ruling A2: a table's records are made visible promptly, each line whole; flush per line,
measured, and past a 5% loss on a large ``cli`` output, write every 64 KiB or 250 ms
instead. This runs a ``cli`` survey whose command prints N JSON lines, through recon's own
root, with three table writers in turn, and prints the median of each and its loss against
the first:

* ``buffered``: the writer before A2, buffered and flushed every 1000 records;
* ``per line``: one ``write`` per line (the store with its batch bound set to one byte);
* ``as built``: the store as it is, whole lines written every 64 KiB or 250 ms.

All three fsync every 1000 records. The command runs locally and reaches no network. It is
not a test and runs in no suite: timings are the machine's, not a requirement.
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

from recon.adapters._out import archive_files
from recon.adapters._out.archive_files import EMPTY_SENTINEL, ArchiveFiles
from recon.adapters._out.system_clock import SystemClock
from recon.composition import root

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator, Sequence


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


@contextlib.contextmanager
def _batch_bound(size: int) -> Iterator[None]:
    """The store's batch bound set to ``size`` bytes while the block runs."""
    kept = archive_files._WRITE_BYTES
    archive_files._WRITE_BYTES = size
    try:
        yield
    finally:
        archive_files._WRITE_BYTES = kept


def _survey(project: Path, archives: ArchiveFiles) -> float:
    started = time.perf_counter()
    document = root({}, project=project, archives=archives).survey("bench")
    elapsed = time.perf_counter() - started
    assert document.status == "ok", document.tables
    return elapsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--records", type=int, default=200_000)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args(argv)
    printer = (
        "import json,sys\n"
        f"for n in range({args.records}):\n"
        "    sys.stdout.write(json.dumps({'n': n, 'path': f'src/module_{n % 97}.py', "
        "'line': n % 400, 'text': 'TODO: ' + 'x' * (n % 60)}) + '\\n')\n"
    )
    clock = SystemClock()
    bounds = {"buffered": None, "per line": 1, "as built": archive_files._WRITE_BYTES}
    timings: dict[str, list[float]] = {name: [] for name in bounds}
    with tempfile.TemporaryDirectory() as directory:
        project = Path(directory)
        mission = project / ".recon" / "bench"
        mission.mkdir(parents=True)
        script = project / "print_lines.py"
        script.write_text(printer)
        command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
        config = {"collectors": [{"name": "lines", "type": "cli", "run": command}]}
        (mission / "config.yaml").write_text(yaml.safe_dump(config))
        for _ in range(args.repeats):
            for name, bound in bounds.items():
                if bound is None:
                    timings[name].append(_survey(project, BufferedArchiveFiles(clock)))
                    continue
                with _batch_bound(bound):
                    timings[name].append(_survey(project, ArchiveFiles(clock)))
    medians = {name: statistics.median(times) for name, times in timings.items()}
    base = medians["buffered"]
    print(f"{args.records} records, median of {args.repeats} surveys each:")
    for name, median in medians.items():
        loss = (median - base) / base * 100
        print(f"  {name:<9} {median:.3f}s  {loss:+.1f}% (the ruling's bound: 5%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The record: typed tables and a ledger, durable as rows land, readable after a kill."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import textwrap
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel

from matrix import (
    LEDGER,
    REF,
    Construct,
    Executor,
    Inputs,
    JsonlConstructStore,
    LedgerEntry,
    Limits,
    MemoryConstructStore,
    NotFoundError,
    Ref,
    RunContext,
    RunError,
    StoreError,
    default_registry,
    wire,
)

if TYPE_CHECKING:
    from pathlib import Path

TRIAL, READING, SEED = "acme.v1.trial", "acme.v1.reading", "acme.v1.seed"


class Step:
    def __init__(self, body: Any, requires: Any = (), provides: Any = ()) -> None:
        self.requires = frozenset(requires)
        self.provides = frozenset(provides)
        self._body = body

    async def run(self, inputs: Inputs, ctx: RunContext) -> Any:
        return await self._body(inputs, ctx)


class Trial(BaseModel):
    seq: int
    note: str = ""


def _table(directory: Path, kind: str) -> list[Any]:
    return [json.loads(line) for line in (directory / f"{kind}.jsonl").read_text().splitlines()]


async def test_a_row_handed_over_is_on_disk_before_the_call_returns(tmp_path: Path) -> None:
    store = JsonlConstructStore(tmp_path)
    seen: dict[str, Any] = {}

    async def trials(inputs: Inputs, ctx: RunContext) -> Any:
        ctx.provide(TRIAL, Trial(seq=0))
        ctx.provide(TRIAL, {"seq": 1})
        # Still running, and nothing has been awaited: this is what a kill right now leaves.
        directory = tmp_path / ctx.run_id
        seen["rows"] = _table(directory, TRIAL)
        seen["ledger"] = [(e["seq"], e["kind"], e["row"]) for e in _table(directory, LEDGER)]
        seen["record"] = store.describe(ctx.run_id)
        seen["loaded"] = store.load(ctx.run_id).rows(TRIAL)
        return {TRIAL: Trial(seq=2)}

    wiring = wire({"trials": Step(trials, provides=[TRIAL])}, schemas={TRIAL: Trial})
    run = await Executor().run(wiring, store=store, run_id="r1")
    assert seen["rows"] == [{"seq": 0, "note": ""}, {"seq": 1}]  # the records, nothing around them
    assert seen["ledger"] == [(0, TRIAL, 0), (1, TRIAL, 1)]
    assert seen["record"]["status"] == "running"
    assert seen["record"]["tables"][TRIAL] == {"file": f"{TRIAL}.jsonl", "rows": 2}
    assert seen["loaded"] == ({"seq": 0, "note": ""}, {"seq": 1})
    assert run.rows(TRIAL) == (Trial(seq=0), {"seq": 1}, Trial(seq=2))


async def test_a_finished_run_reads_back_with_rows_ledger_and_status(tmp_path: Path) -> None:
    async def trials(inputs: Inputs, ctx: RunContext) -> Any:
        return {TRIAL: [{"seq": i, "seed": inputs.one(SEED)} for i in range(2)]}

    async def sense(inputs: Inputs, ctx: RunContext) -> Any:
        return {READING: [{"seq": t["seq"], "ok": True} for t in inputs[TRIAL]]}

    wiring = wire(
        {
            "sense": Step(sense, requires=[TRIAL], provides=[READING]),
            "trials": Step(trials, requires=[SEED], provides=[TRIAL]),
        },
        inputs=[SEED],
    )
    store = JsonlConstructStore(tmp_path)
    run = await Executor().run(wiring, {SEED: 7}, store=store)
    record = json.loads((tmp_path / run.run_id / "run.json").read_text())
    assert record == store.describe(run.run_id)
    assert record["schema"] == "matrix.v1.run" and record["status"] == "completed"
    assert record["components"]["trials"]["status"] == "completed"
    assert record["tables"] == {
        SEED: {"file": f"{SEED}.jsonl", "rows": 1},
        READING: {"file": f"{READING}.jsonl", "rows": 2},
        TRIAL: {"file": f"{TRIAL}.jsonl", "rows": 2},
        LEDGER: {"file": f"{LEDGER}.jsonl", "rows": 5},
    }
    loaded = store.load(run.run_id)
    assert loaded.torn == ()
    assert loaded.rows(TRIAL) == ({"seq": 0, "seed": 7}, {"seq": 1, "seed": 7})
    assert loaded.rows(READING) == ({"seq": 0, "ok": True}, {"seq": 1, "ok": True})
    assert loaded.ledger == run.construct.ledger == loaded.rows(LEDGER)
    assert [(e.component, e.row) for e in loaded.provenance(TRIAL)] == [
        ("trials", 0),
        ("trials", 1),
    ]
    assert loaded.provenance(SEED)[0].component == "$input"
    assert loaded.last(READING) == {"seq": 1, "ok": True}
    assert store.runs() == [run.run_id]


def test_the_ledger_entry_is_written_before_its_row() -> None:
    written: list[tuple[str, Any]] = []
    construct = Construct([TRIAL])
    construct.write_through(lambda kind, row: written.append((kind, row)))
    first = construct.append(TRIAL, {"seq": 0}, component="trials")
    construct.append(TRIAL, {"seq": 1}, component="trials")
    assert [kind for kind, _ in written] == [LEDGER, TRIAL, LEDGER, TRIAL]
    assert written[0][1] == first and (first.seq, first.kind, first.row) == (0, TRIAL, 0)
    assert isinstance(written[2][1], LedgerEntry) and written[2][1].row == 1


def test_a_ledger_entry_whose_row_never_landed_is_reported_as_torn(tmp_path: Path) -> None:
    entries = [
        {"seq": 0, "kind": TRIAL, "row": 0, "component": "trials", "time": "2026-01-01T00:00:00Z"},
        {"seq": 1, "kind": TRIAL, "row": 1, "component": "trials", "time": "2026-01-01T00:00:01Z"},
    ]
    restored = Construct.restore({TRIAL: [{"seq": 0}], LEDGER: entries})
    assert restored.rows(TRIAL) == ({"seq": 0},)
    assert [e.seq for e in restored.ledger] == [0]  # only what is accounted for and present
    assert [(e.seq, e.kind, e.row) for e in restored.torn] == [(1, TRIAL, 1)]


async def test_a_run_cut_off_between_the_two_writes_reads_back_with_its_torn_tail(
    tmp_path: Path,
) -> None:
    async def trials(inputs: Inputs, ctx: RunContext) -> Any:
        return {TRIAL: [{"seq": 0}, {"seq": 1}]}

    store = JsonlConstructStore(tmp_path)
    await Executor().run(wire({"trials": Step(trials, provides=[TRIAL])}), store=store, run_id="r")
    directory = tmp_path / "r"
    record = json.loads((directory / "run.json").read_text())
    record["status"] = "running"  # as it stood before the run got to finish
    (directory / "run.json").write_text(json.dumps(record))
    # What a kill leaves at its worst: an entry for a row that was cut short as it was written.
    torn = {
        "seq": 2,
        "kind": TRIAL,
        "row": 2,
        "component": "trials",
        "time": "2026-01-01T00:00:00Z",
    }
    with (directory / f"{LEDGER}.jsonl").open("a") as ledger:
        ledger.write(json.dumps(torn) + "\n")
    with (directory / f"{TRIAL}.jsonl").open("a") as table:
        table.write('{"seq": 2, "no')
    loaded = store.load("r")
    assert loaded.rows(TRIAL) == ({"seq": 0}, {"seq": 1})  # the cut-short line is not a row
    assert [(e.seq, e.row) for e in loaded.torn] == [(2, 2)]
    described = store.describe("r")
    assert described["tables"][TRIAL]["rows"] == 2 and described["torn"] == 1


def test_a_record_that_contradicts_itself_is_a_store_error() -> None:
    entry = {"seq": 0, "kind": TRIAL, "row": 0, "component": "t", "time": "2026-01-01T00:00:00Z"}
    with pytest.raises(StoreError, match="holds 2 rows but the ledger accounts for 1"):
        Construct.restore({TRIAL: [{}, {}], LEDGER: [entry]})
    with pytest.raises(StoreError, match="out of order"):
        Construct.restore({TRIAL: [{}, {}], LEDGER: [entry | {"row": 1}]})


@pytest.mark.parametrize("make", [JsonlConstructStore, lambda _: MemoryConstructStore()])
async def test_opening_an_existing_run_id_is_a_store_error(make: Any, tmp_path: Path) -> None:
    ran: list[str] = []

    async def body(inputs: Inputs, ctx: RunContext) -> Any:
        ran.append("ran")

    store = make(tmp_path)
    wiring = wire({"a": Step(body)})
    await Executor().run(wiring, store=store, run_id="taken")
    with pytest.raises(StoreError, match="never reopened or rewritten") as e:
        await Executor().run(wiring, store=store, run_id="taken")
    assert e.value.kind == "store" and e.value.exit_code == 1
    assert ran == ["ran"]  # refused before anything ran


async def test_the_memory_store_keeps_the_same_record() -> None:
    async def body(inputs: Inputs, ctx: RunContext) -> Any:
        ctx.provide(TRIAL, {"seq": 0})
        return {TRIAL: {"seq": 1}}

    store = MemoryConstructStore()
    run = await Executor().run(wire({"t": Step(body, provides=[TRIAL])}), store=store)
    loaded = store.load(run.run_id)
    assert loaded.rows(TRIAL) == ({"seq": 0}, {"seq": 1}) and loaded.ledger == run.construct.ledger
    described = store.describe(run.run_id)
    assert described["status"] == "completed"
    assert described["tables"] == {TRIAL: {"rows": 2}, LEDGER: {"rows": 2}}
    assert store.runs() == [run.run_id]
    with pytest.raises(NotFoundError, match="Runs: "):
        store.load("nope")


def test_missing_runs_and_unsafe_ids(tmp_path: Path) -> None:
    store = JsonlConstructStore(tmp_path)
    with pytest.raises(NotFoundError, match="Runs: \\(none\\)"):
        store.load("nope")
    with pytest.raises(ValueError, match="safe"):
        store.load("../etc")


class Failing:
    """A store whose writer fails on its nth append, or when the run is finished."""

    def __init__(self, on_append: int | None = None, on_finish: bool = False) -> None:
        self._inner = MemoryConstructStore()
        self._on_append, self._on_finish = on_append, on_finish
        self.appends = 0

    def open(self, run: Any) -> Any:
        self._writer = self._inner.open(run)
        return self

    def append(self, kind: str, row: Any) -> None:
        self.appends += 1
        if self.appends == self._on_append:
            raise OSError("no space left on device")
        self._writer.append(kind, row)

    def finish(self, run: Any) -> str:
        if self._on_finish:
            raise OSError("read-only file system")
        return str(self._writer.finish(run))

    def load(self, run_id: str) -> Construct:
        return self._inner.load(run_id)

    def describe(self, run_id: str) -> dict[str, Any]:
        return self._inner.describe(run_id)

    def runs(self) -> list[str]:
        return self._inner.runs()


async def test_a_store_that_fails_fails_the_run_with_kind_store() -> None:
    caught: list[BaseException] = []
    released: list[str] = []
    stopping = asyncio.Event()

    async def trials(inputs: Inputs, ctx: RunContext) -> Any:
        ctx.defer(lambda: released.append("released"))
        ctx.provide(TRIAL, {"seq": 0})  # appends 1 and 2: its ledger entry, then the row
        try:
            ctx.provide(TRIAL, {"seq": 1})  # append 3 goes through; append 4, the row, fails
        except StoreError as e:
            caught.append(e)
            raise

    async def bystander(inputs: Inputs, ctx: RunContext) -> Any:
        await ctx.stopping.wait()
        stopping.set()

    async def sense(inputs: Inputs, ctx: RunContext) -> Any:
        return None

    store = Failing(on_append=4)
    wiring = wire(
        {
            "trials": Step(trials, provides=[TRIAL]),
            "bystander": Step(bystander),
            "sense": Step(sense, requires=[TRIAL]),
        }
    )
    with pytest.raises(RunError) as e:
        # isolate would let the bystander go on; a run with no record does not go on.
        await Executor().run(wiring, store=store, limits=Limits(on_failure="isolate"))
    run = e.value.run
    assert e.value.kind == "store" and e.value.component == "trials"
    assert isinstance(e.value.__cause__, StoreError) and caught == [e.value.__cause__]
    assert "no space left on device" in str(e.value)
    assert run.status == "failed" and run.failures[0]["kind"] == "store"
    assert run.components["trials"]["status"] == "failed"
    assert stopping.is_set() and run.components["bystander"]["reason"] == "run_failed"
    assert run.components["sense"] == {"status": "skipped", "reason": "store failed"}
    assert released == ["released"]
    assert run.rows(TRIAL) == ({"seq": 0},)  # a row that was not kept is not in the record
    assert store.appends == 4  # and nothing was written after the failure
    loaded = store.load(run.run_id)
    assert loaded.rows(TRIAL) == ({"seq": 0},) and [t.row for t in loaded.torn] == [1]


async def test_a_returned_row_or_an_input_the_store_cannot_keep_fails_the_run() -> None:
    async def trials(inputs: Inputs, ctx: RunContext) -> Any:
        return {TRIAL: {"seq": 0}}

    wiring = wire({"trials": Step(trials, requires=[SEED], provides=[TRIAL])}, inputs=[SEED])
    with pytest.raises(RunError) as returned:
        await Executor().run(wiring, {SEED: 1}, store=Failing(on_append=3))
    assert (returned.value.kind, returned.value.component) == ("store", "trials")
    with pytest.raises(RunError) as supplied:
        await Executor().run(wiring, {SEED: 1}, store=Failing(on_append=1))
    assert (supplied.value.kind, supplied.value.component) == ("store", "$input")
    assert supplied.value.run.components["trials"] == {
        "status": "skipped",
        "reason": "store failed",
    }


async def test_a_run_that_cannot_be_finished_in_the_store_is_a_failed_run() -> None:
    async def body(inputs: Inputs, ctx: RunContext) -> Any:
        return None

    with pytest.raises(RunError) as e:
        await Executor().run(wire({"a": Step(body)}), store=Failing(on_finish=True))
    assert (e.value.kind, e.value.component) == ("store", "$store")
    assert e.value.run.status == "failed" and e.value.run.components["a"]["status"] == "completed"


async def test_a_jsonl_writer_refuses_a_kind_the_run_has_no_table_for(tmp_path: Path) -> None:
    async def body(inputs: Inputs, ctx: RunContext) -> Any:
        return None

    store = JsonlConstructStore(tmp_path)
    run = await Executor().run(wire({"a": Step(body, provides=[TRIAL])}), run_id="r")
    writer = store.open(run)
    writer.append(TRIAL, {"seq": 0})
    with pytest.raises(StoreError, match="no table for kind 'acme.v1.other'"):
        writer.append("acme.v1.other", {})


def test_a_ref_stands_for_a_large_value_and_is_a_registered_kind() -> None:
    schemas = default_registry(discover=False).payload_schemas()
    assert schemas[REF] is Ref and schemas[LEDGER] is LedgerEntry
    ref = Ref(uri="series/rss.parquet", media_type="application/parquet", size=1024)
    assert ref.model_dump(mode="json") == {
        "uri": "series/rss.parquet",
        "media_type": "application/parquet",
        "size": 1024,
        "digest": None,
    }


_KILLED = """
    import asyncio, sys
    from matrix import Executor, JsonlConstructStore, wire

    class Trials:
        requires = ("acme.v1.seed",)
        provides = ("acme.v1.trial",)

        async def run(self, inputs, ctx):
            for seq in range(3):
                ctx.provide("acme.v1.trial", {"seq": seq, "seed": inputs.one("acme.v1.seed")})
            print("ready", flush=True)
            await asyncio.Event().wait()   # a long run, killed before it returns anything

    wiring = wire({"trials": Trials()}, inputs=["acme.v1.seed"])
    store = JsonlConstructStore(sys.argv[1])
    asyncio.run(Executor().run(wiring, {"acme.v1.seed": 7}, store=store, run_id="killed"))
"""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_rows_handed_over_survive_sigkill_and_the_run_reads_as_interrupted(tmp_path: Path) -> None:
    script = tmp_path / "killed.py"
    script.write_text(textwrap.dedent(_KILLED))
    runs = tmp_path / "runs"
    with subprocess.Popen(
        [sys.executable, str(script), str(runs)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    ) as proc:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "ready", proc.stderr and proc.stderr.read()
        store = JsonlConstructStore(runs)
        assert store.describe("killed")["status"] == "running"  # readable while it runs
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=60)
    assert proc.returncode == -signal.SIGKILL
    record = store.describe("killed")
    assert record["status"] == "interrupted" and record["ended"] is None
    assert record["tables"][TRIAL]["rows"] == 3 and record["torn"] == 0
    loaded = store.load("killed")
    assert loaded.rows(TRIAL) == tuple({"seq": seq, "seed": 7} for seq in range(3))
    assert loaded.rows(SEED) == (7,)
    assert [e.component for e in loaded.ledger] == ["$input", "trials", "trials", "trials"]
    assert loaded.torn == ()

"""The record: typed tables and a ledger, durable as rows land, readable after a kill."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel, field_validator

from matrix import (
    LEDGER,
    REF,
    SESSION,
    ConfigError,
    Construct,
    ContractError,
    Executor,
    Inputs,
    JsonlConstructStore,
    LedgerEntry,
    Limits,
    MemoryConstructStore,
    NotFoundError,
    Ref,
    Run,
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
    # The records as their kind's schema reads them, nothing around them.
    assert seen["rows"] == [{"seq": 0, "note": ""}, {"seq": 1, "note": ""}]
    assert seen["ledger"] == [(0, TRIAL, 0), (1, TRIAL, 1)]
    assert seen["record"]["status"] == "running"
    assert seen["record"]["tables"][TRIAL] == {"file": f"{TRIAL}.jsonl", "rows": 2}
    assert seen["loaded"] == ({"seq": 0, "note": ""}, {"seq": 1, "note": ""})
    assert run.rows(TRIAL) == (Trial(seq=0), Trial(seq=1), Trial(seq=2))


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
        SESSION: {"file": f"{SESSION}.jsonl", "rows": 0},  # every run has one, as a ledger
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


async def test_nested_values_are_written_as_json_not_as_their_str(tmp_path: Path) -> None:
    import dataclasses
    from datetime import UTC, datetime

    @dataclasses.dataclass
    class Point:
        x: int

    class Inner(BaseModel):
        a: int
        b: str

    async def give(inputs: Inputs, ctx: RunContext) -> Any:
        return {
            TRIAL: {
                "inner": Inner(a=1, b="x"),
                "tags": {"s"},
                "at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
                "point": Point(x=2),
                "pair": (1, 2),
            }
        }

    store = JsonlConstructStore(tmp_path)
    await Executor().run(wire({"give": Step(give, provides=[TRIAL])}), store=store, run_id="r")
    expected = {
        "inner": {"a": 1, "b": "x"},
        "tags": ["s"],
        "at": "2026-01-02T03:04:05Z",
        "point": {"x": 2},
        "pair": [1, 2],
    }
    assert _table(tmp_path / "r", TRIAL) == [expected]
    assert store.load("r").rows(TRIAL) == (expected,)


async def test_a_row_that_is_not_json_data_is_refused_naming_the_field(tmp_path: Path) -> None:
    """The JSONL store refuses it before writing anything for it, so the record stays whole
    and the component may go on."""
    refused: list[ContractError] = []

    class Handle:
        pass

    async def give(inputs: Inputs, ctx: RunContext) -> Any:
        try:
            ctx.provide(TRIAL, {"meta": {"handle": Handle()}})
        except ContractError as e:
            refused.append(e)
        ctx.provide(TRIAL, {"seq": 1})

    store = JsonlConstructStore(tmp_path)
    run = await Executor().run(
        wire({"give": Step(give, provides=[TRIAL])}), store=store, run_id="r"
    )
    assert len(refused) == 1 and "meta.handle" in str(refused[0])
    assert run.rows(TRIAL) == ({"seq": 1},)
    loaded = store.load("r")
    assert loaded.rows(TRIAL) == ({"seq": 1},) and loaded.torn == ()
    assert [e["row"] for e in _table(tmp_path / "r", LEDGER)] == [0]


def test_the_ledger_entry_is_written_before_its_row() -> None:
    written: list[tuple[str, Any]] = []
    construct = Construct([TRIAL])
    construct.write_through(lambda kind, row: written.append((kind, row)))
    first = construct.append(TRIAL, {"seq": 0}, component="trials")
    construct.append(TRIAL, {"seq": 1}, component="trials")
    assert [kind for kind, _ in written] == [LEDGER, TRIAL, LEDGER, TRIAL]
    assert written[0][1] == first and (first.seq, first.kind, first.row) == (0, TRIAL, 0)
    assert isinstance(written[2][1], LedgerEntry) and written[2][1].row == 1


def test_after_a_failed_write_through_every_append_is_refused() -> None:
    written: list[str] = []

    def sink(kind: str, row: Any) -> None:
        if len(written) == 1:
            raise OSError("no space left on device")
        written.append(kind)

    construct = Construct([TRIAL])
    construct.write_through(sink)
    with pytest.raises(OSError):
        construct.append(TRIAL, {"seq": 0}, component="trials")
    with pytest.raises(StoreError, match="no space left on device"):
        construct.append(TRIAL, {"seq": 1}, component="trials")
    assert written == [LEDGER] and construct.rows(TRIAL) == () and len(construct) == 0


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
    assert e.value.kind == "store" and e.value.exit_code == 9
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
    assert described["tables"] == {TRIAL: {"rows": 2}, SESSION: {"rows": 0}, LEDGER: {"rows": 2}}
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
    # isolate would let the bystander go on; a run with no record does not go on. (The
    # timeout turns "the store never failed, so nothing stopped the bystander" into a failure.)
    with pytest.raises(RunError) as e:
        async with asyncio.timeout(30):
            await Executor().run(wiring, store=store, limits=Limits(on_failure="isolate"))
    run = e.value.run
    assert e.value.kind == "store" and e.value.component == "trials"
    assert isinstance(e.value.__cause__, StoreError) and caught == [e.value.__cause__]
    assert "no space left on device" in str(e.value)
    assert run.status == "failed" and run.failures[0].kind == "store"
    assert run.components["trials"].status == "failed"
    assert stopping.is_set() and run.components["bystander"].reason == "run_failed"
    assert run.components["sense"].document() == {"status": "skipped", "reason": "store failed"}
    assert released == ["released"]
    assert run.rows(TRIAL) == ({"seq": 0},)  # a row that was not kept is not in the record
    assert store.appends == 4  # and nothing was written after the failure
    loaded = store.load(run.run_id)
    assert loaded.rows(TRIAL) == ({"seq": 0},) and [t.row for t in loaded.torn] == [1]


async def test_a_row_handed_over_after_the_store_failed_is_refused_not_kept_in_memory() -> None:
    """Under ``isolate`` a sibling may still be running when the store fails. Its next
    ``provide`` raises: a row that returned normally would be one the record lacks."""
    failed = asyncio.Event()
    refused: list[BaseException] = []

    async def first(inputs: Inputs, ctx: RunContext) -> Any:
        try:
            ctx.provide(TRIAL, {"seq": 0})
        finally:
            failed.set()

    async def second(inputs: Inputs, ctx: RunContext) -> Any:
        await failed.wait()
        try:
            ctx.provide(READING, "kept?")
        except StoreError as e:
            refused.append(e)
            raise

    store = Failing(on_append=2)  # the first row's ledger entry lands, the row does not
    wiring = wire(
        {"first": Step(first, provides=[TRIAL]), "second": Step(second, provides=[READING])}
    )
    with pytest.raises(RunError) as e:
        await Executor().run(wiring, store=store, limits=Limits(on_failure="isolate"))
    run = e.value.run
    assert len(refused) == 1 and store.appends == 2
    assert run.components["second"].status == "failed"
    assert run.rows(READING) == () and store.load(run.run_id).rows(READING) == ()


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
    assert supplied.value.run.components["trials"].document() == {
        "status": "skipped",
        "reason": "store failed",
    }


async def test_a_run_that_cannot_be_finished_in_the_store_is_a_failed_run() -> None:
    async def body(inputs: Inputs, ctx: RunContext) -> Any:
        return None

    with pytest.raises(RunError) as e:
        await Executor().run(wire({"a": Step(body)}), store=Failing(on_finish=True))
    assert (e.value.kind, e.value.component) == ("store", "$store")
    assert e.value.run.status == "failed" and e.value.run.components["a"].status == "completed"


async def test_a_schema_that_raises_any_error_breaks_the_contract_and_the_run_ends_failed(
    tmp_path: Path,
) -> None:
    """A validator may raise what pydantic does not wrap (a TypeError). That is still a row
    its kind refuses: the component fails, its sibling is stopped before the run returns, and
    the record says ``failed``."""

    class Strict(BaseModel):
        seq: int

        @field_validator("seq")
        @classmethod
        def _not_negative(cls, value: int) -> int:
            if value < 0:
                raise TypeError("negative")
            return value

    running = asyncio.Event()
    unwound: list[str] = []

    async def bad(inputs: Inputs, ctx: RunContext) -> Any:
        await running.wait()
        return {TRIAL: {"seq": -1}}

    async def sibling(inputs: Inputs, ctx: RunContext) -> Any:
        running.set()
        try:
            await ctx.stopping.wait()
        finally:
            unwound.append("sibling")

    store = JsonlConstructStore(tmp_path)
    wiring = wire(
        {"bad": Step(bad, provides=[TRIAL]), "sibling": Step(sibling)}, schemas={TRIAL: Strict}
    )
    # (The timeout turns "the sibling was orphaned, never asked to stop" into a failure.)
    with pytest.raises(RunError) as e:
        async with asyncio.timeout(30):
            await Executor().run(wiring, store=store, run_id="r")
    assert (e.value.kind, e.value.cause_kind, e.value.component) == (
        "run_failed",
        "contract",
        "bad",
    )
    assert "negative" in str(e.value)
    assert unwound == ["sibling"] and e.value.run.components["sibling"].reason == "run_failed"
    assert store.describe("r")["status"] == "failed"


async def test_a_run_whose_record_broke_in_an_unforeseen_way_is_recorded_failed() -> None:
    """Whatever escapes the run (here a store's own error, not a StoreError, while the inputs
    are recorded) reaches the caller, and the record never says ``completed``."""

    class Refusing(Failing):
        def append(self, kind: str, row: Any) -> None:
            raise ConfigError("the bucket is read-only")

    async def body(inputs: Inputs, ctx: RunContext) -> Any:
        return None

    store = Refusing()
    wiring = wire({"a": Step(body, requires=[SEED])}, inputs=[SEED])
    with pytest.raises(ConfigError, match="read-only"):
        await Executor().run(wiring, {SEED: 1}, store=store, run_id="r")
    assert store.describe("r")["status"] == "failed"


async def test_an_input_that_cannot_be_copied_is_refused_before_the_record_opens(
    tmp_path: Path,
) -> None:
    import threading

    async def body(inputs: Inputs, ctx: RunContext) -> Any:
        return None

    store = JsonlConstructStore(tmp_path)
    wiring = wire({"a": Step(body, requires=[SEED])}, inputs=[SEED])
    with pytest.raises(ContractError, match="must be data"):
        await Executor().run(wiring, {SEED: {"lock": threading.Lock()}}, store=store, run_id="r")
    assert store.runs() == [] and not (tmp_path / "r").exists()


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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_dead_writer_whose_pid_was_reused_still_reads_as_interrupted(tmp_path: Path) -> None:
    """Regression: liveness was ``kill(pid, 0)``, so a SIGKILLed writer whose pid another
    live process had taken read as ``running`` for ever."""
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
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=60)
    record_path = runs / "killed" / "run.json"
    record = json.loads(record_path.read_text())
    record["writer"]["pid"] = os.getpid()  # the pid now belongs to a live process: this one
    record_path.write_text(json.dumps(record))
    assert JsonlConstructStore(runs).describe("killed")["status"] == "interrupted"


def _killed(tmp_path: Path) -> Path:
    """A run killed after three rows landed. Returns the root."""
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
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=60)
    return runs


def _killed_mid_write(tmp_path: Path) -> Path:
    """A run killed while it was writing: three rows landed, then a fourth row's line and an
    event's line were cut short, as a kill (or a full disk) leaves them. Returns the root."""
    runs = _killed(tmp_path)
    directory = runs / "killed"
    with (directory / f"{TRIAL}.jsonl").open("a") as table:
        table.write('{"seq": 3, "se')
    with (directory / "events.jsonl").open("a") as events:
        events.write('{"schema": "matrix.v1.ev')
    return runs


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_killed_run_once_read_says_interrupted_on_disk_and_reads_without_matrix(
    tmp_path: Path,
) -> None:
    """A torn last line broke jq and DuckDB, and ``interrupted`` lived only in what
    ``describe`` computed (mx-src F15's sibling, area 5). The first matrix reader that finds
    the writer gone finishes the record: ``run.json`` says ``interrupted``, and each torn
    tail is moved aside to ``<file>.torn``, so every file reads without matrix (W5.1)."""
    runs = _killed_mid_write(tmp_path)
    directory = runs / "killed"
    assert json.loads((directory / "run.json").read_text())["status"] == "running"

    described = JsonlConstructStore(runs).describe("killed")
    assert described["status"] == "interrupted"
    on_disk = json.loads((directory / "run.json").read_text())
    assert on_disk["status"] == "interrupted" and on_disk["ended"] is None
    assert on_disk["tables"][TRIAL]["rows"] == 3 and on_disk["torn"] == 0
    for path in directory.glob("*.jsonl"):  # every line a JSON document, as jq reads them
        assert all(json.loads(line) is not None for line in path.read_text().splitlines()), path
    assert (directory / f"{TRIAL}.jsonl.torn").read_text() == '{"seq": 3, "se'
    assert (directory / "events.jsonl.torn").read_text() == '{"schema": "matrix.v1.ev'
    if shutil.which("jq"):
        for path in directory.glob("*.jsonl"):
            read = subprocess.run(["jq", "-c", ".", str(path)], capture_output=True, check=False)
            assert read.returncode == 0, (path, read.stderr)
    duckdb = pytest.importorskip("duckdb")
    rows = duckdb.sql(f"SELECT seq FROM read_json_auto('{directory / TRIAL}.jsonl')").fetchall()
    assert rows == [(0,), (1,), (2,)]
    # Read again, the record is as it was left: finished once, by whoever read it first.
    assert JsonlConstructStore(runs).describe("killed") == described
    assert JsonlConstructStore(runs).load("killed").rows(TRIAL) == tuple(
        {"seq": seq, "seed": 7} for seq in range(3)
    )


def test_a_write_cut_short_leaves_no_torn_line_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write the disk cut short (a full disk) is taken back before the StoreError is raised,
    so the table never holds half a line while the process lives."""
    from matrix.adapters._out.stores import jsonl

    store = JsonlConstructStore(tmp_path)
    run = Run(run_id="r", construct=Construct([TRIAL]))
    writer = store.open(run)
    writer.append(TRIAL, {"seq": 0})
    real = os.write

    def half(descriptor: int, data: bytes) -> int:
        real(descriptor, data[: len(data) // 2])
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(jsonl.os, "write", half)
    with pytest.raises(StoreError, match="No space left"):
        writer.append(TRIAL, {"seq": 1, "note": "long enough to be cut in half"})
    assert (tmp_path / "r" / f"{TRIAL}.jsonl").read_text() == '{"seq": 0}\n'


def test_a_damaged_run_record_is_skipped_when_runs_are_listed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """One unreadable ``run.json`` made ``runs()`` fail, and with it ``matrix runs`` (mx-src
    F15): it is skipped, and said."""
    store = JsonlConstructStore(tmp_path)
    store.open(Run(run_id="good", construct=Construct([TRIAL]))).finish(
        Run(run_id="good", construct=Construct([TRIAL]))
    )
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "run.json").write_text("{trunc")
    with caplog.at_level("WARNING", logger="matrix.store"):
        assert store.runs() == ["good"]
    assert any("bad" in r.getMessage() for r in caplog.records)


def test_with_fsync_the_run_directory_is_flushed_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``fsync=True`` promises a record that survives the machine going down, which a new
    file's or a renamed ``run.json``'s directory entry does not without the directory's own
    flush (mx-src F28)."""
    import stat

    from matrix.adapters._out.stores import jsonl

    flushed: list[bool] = []
    real = os.fsync

    def fsync(descriptor: int) -> None:
        flushed.append(stat.S_ISDIR(os.fstat(descriptor).st_mode))
        real(descriptor)

    monkeypatch.setattr(jsonl.os, "fsync", fsync)
    run = Run(run_id="r", construct=Construct([TRIAL]))
    writer = JsonlConstructStore(tmp_path, fsync=True).open(run)
    assert True in flushed  # the directory, once its files exist
    flushed.clear()
    writer.append(TRIAL, {"seq": 0})
    assert flushed == [False]  # each row, flushed before append returns
    flushed.clear()
    writer.finish(run)
    assert True in flushed  # and after run.json is renamed into place


def test_a_store_root_that_is_a_file_is_not_called_an_existing_run(tmp_path: Path) -> None:
    """Any ``FileExistsError`` read as "run already exists" (mx-src F29)."""
    root = tmp_path / "runs"
    root.write_text("not a directory")
    with pytest.raises(StoreError) as e:
        JsonlConstructStore(root).open(Run(run_id="r", construct=Construct([TRIAL])))
    assert "already exists" not in str(e.value) and "not a directory" in str(e.value)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_the_documented_readers_skip_a_line_cut_short_in_a_run_not_yet_finished(
    tmp_path: Path,
) -> None:
    """Before any matrix reader has finished a killed run, its tables may end in a cut line:
    the recipes ``data-model.md`` gives read past it, run here as written there."""
    import re
    from pathlib import Path as _Path

    runs = _killed_mid_write(tmp_path)
    table = runs / "killed" / f"{TRIAL}.jsonl"
    text = (_Path(__file__).parents[1] / "docs" / "explanation" / "data-model.md").read_text()
    found = re.search(r"<!-- torn-tolerant -->\s*```sql\n(.*?)```", text, re.S)
    assert found, "data-model.md shows how to read a table whose last line may be cut"
    duckdb = pytest.importorskip("duckdb")
    sql = found.group(1).replace("RUN/KIND.jsonl", str(table))
    rows = duckdb.sql(sql).fetchall()
    assert [(row, json.loads(doc)["seq"]) for row, doc in rows] == [(0, 0), (1, 1), (2, 2)]
    assert "jq -cR 'fromjson? // empty' FILE" in text
    if shutil.which("jq"):
        read = subprocess.run(
            ["jq", "-cR", "fromjson? // empty", str(table)], capture_output=True, check=True
        )
        assert [json.loads(line)["seq"] for line in read.stdout.splitlines()] == [0, 1, 2]


# --- the record's observable behaviours, each pinned (W5.3; mx-tests 11) ------------------


def test_every_table_file_exists_from_the_start_even_with_no_rows(tmp_path: Path) -> None:
    store = JsonlConstructStore(tmp_path)
    run = Run(run_id="r", construct=Construct([TRIAL, READING]))
    store.open(run)
    for kind in (TRIAL, READING, LEDGER, SESSION):
        assert (tmp_path / "r" / f"{kind}.jsonl").read_text() == "", kind
    assert (tmp_path / "r" / "events.jsonl").exists() is False  # only once a run keeps events


def test_run_json_is_only_ever_replaced_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reader sees the record as it was while running or the finished one, never a mixture:
    ``run.json`` is written to a sibling and renamed into place, at open and at finish."""
    from matrix.adapters._out.stores import jsonl

    renamed: list[tuple[str, str]] = []
    real = os.replace

    def replace(source: Any, target: Any) -> None:
        renamed.append((os.path.basename(source), os.path.basename(target)))
        real(source, target)

    monkeypatch.setattr(jsonl.os, "replace", replace)
    run = Run(run_id="r", construct=Construct([TRIAL]))
    JsonlConstructStore(tmp_path).open(run).finish(run)
    assert renamed == [("run.json.tmp", "run.json")] * 2
    assert sorted(p.name for p in (tmp_path / "r").glob("run.json*")) == ["run.json"]


async def test_the_run_directory_holds_what_the_docs_say(tmp_path: Path) -> None:
    """The layout a reader without matrix relies on (data-model.md): the file names, and the
    keys of a finished ``run.json``."""

    async def trials(inputs: Inputs, ctx: RunContext) -> Any:
        return {TRIAL: {"seq": 0}}

    store = JsonlConstructStore(tmp_path)
    await Executor().run(wire({"trials": Step(trials, provides=[TRIAL])}), store=store, run_id="r")
    names = sorted(p.name for p in (tmp_path / "r").iterdir())
    assert names == sorted(
        [
            "run.json",
            f"{TRIAL}.jsonl",
            f"{SESSION}.jsonl",
            f"{LEDGER}.jsonl",
            "events.jsonl",
            "writer.lock",
        ]
    )
    recorded = json.loads((tmp_path / "r" / "run.json").read_text())
    assert set(recorded) == {
        "schema",
        "run_id",
        "status",
        "started",
        "ended",
        "components",
        "failures",
        "releases",
        "events_dropped",
        "tables",
    }
    assert (recorded["schema"], recorded["status"]) == ("matrix.v1.run", "completed")
    assert recorded["tables"][TRIAL] == {"file": f"{TRIAL}.jsonl", "rows": 1}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_dead_run_on_another_host_still_reads_running_and_is_left_alone(tmp_path: Path) -> None:
    """matrix does not guess: a writer on another host cannot be told dead, so its record is
    neither called ``interrupted`` nor finished."""
    runs = _killed(tmp_path)
    record_path = runs / "killed" / "run.json"
    record = json.loads(record_path.read_text())
    record["writer"]["host"] = "another-host"
    record_path.write_text(json.dumps(record))
    described = JsonlConstructStore(runs).describe("killed")
    assert described["status"] == "running" and described["tables"][TRIAL]["rows"] == 3
    assert json.loads(record_path.read_text())["status"] == "running"


class _Finishes:
    """A store that records what it was asked, and whose event sink records being closed."""

    def __init__(self, fail_on_append: int | None = None) -> None:
        self.calls: list[str] = []
        self._fail_on = fail_on_append
        self._appends = 0
        self._inner = MemoryConstructStore()

    def open(self, run: Any) -> Any:
        self._writer = self._inner.open(run)
        return self

    def append(self, kind: str, row: Any) -> None:
        self._appends += 1
        if self._appends == self._fail_on:
            raise OSError("no space left on device")
        self._writer.append(kind, row)

    def finish(self, run: Any) -> str:
        self.calls.append(f"finish:{run.status}")
        return str(self._writer.finish(run))

    def events(self) -> Any:
        store = self

        class Sink:
            def on_event(self, event: Any) -> None:
                return None

            def close(self) -> None:
                store.calls.append("close events")

        return Sink()

    def load(self, run_id: str) -> Construct:
        return self._inner.load(run_id)

    def describe(self, run_id: str) -> dict[str, Any]:
        return self._inner.describe(run_id)

    def runs(self) -> list[str]:
        return self._inner.runs()


async def test_a_failed_append_still_finishes_the_record_failed_then_closes_its_events() -> None:
    """After a failed append no row is appended, but the store is still asked to finish the
    record, as ``failed``; the event sink is closed after that, once the events drained."""

    async def trials(inputs: Inputs, ctx: RunContext) -> Any:
        ctx.provide(TRIAL, {"seq": 0})

    store = _Finishes(fail_on_append=2)  # the first row's ledger entry lands, the row does not
    with pytest.raises(RunError):
        await Executor().run(wire({"trials": Step(trials, provides=[TRIAL])}), store=store)
    assert store.calls == ["finish:failed", "close events"]


async def test_an_unsafe_run_id_is_refused_before_anything_runs(tmp_path: Path) -> None:
    ran: list[str] = []

    async def trials(inputs: Inputs, ctx: RunContext) -> Any:
        ran.append("ran")

    with pytest.raises(ConfigError, match="safe"):
        await Executor().run(
            wire({"trials": Step(trials, provides=[TRIAL])}),
            store=JsonlConstructStore(tmp_path),
            run_id="../outside",
        )
    assert ran == [] and list(tmp_path.iterdir()) == []

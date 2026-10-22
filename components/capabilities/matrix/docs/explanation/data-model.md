# The data model

A run's rows live in a **Construct**: one typed table per kind, plus a **ledger** of every row
in the order it landed. The Construct belongs to a **Run**, the observed record of one
execution. A **ConstructStore** keeps the record as it is made.

---

## Construct

A **kind** is the type URL of some data, and it is the schema of its table. The rows of
`acme.v1.review` are reviews: the records themselves, with nothing wrapped around them. Each
kind has one provider (the wiring enforces it), so a table holds one writer's rows in the
order it handed them over. Run inputs are tables too.

```python
construct = run.construct

construct.rows("demo.v1.shout")  # the rows, in the order they landed; () if none
construct.last("demo.v1.shout")  # the most recent row; NotFoundError if there is none
construct.kinds()  # every table: the run's kinds, then matrix.v1.ledger
construct.ledger  # every row's LedgerEntry, in the order rows were appended
construct.provenance("demo.v1.shout")  # the ledger entries for that kind's rows, in row order
construct.torn  # entries whose row never landed; () for a live run
"demo.v1.shout" in construct  # True if the kind has at least one row
len(construct)  # number of rows, the ledger's own aside
```

`last` on an empty kind raises `NotFoundError`, which lists the kinds that do have rows.

Rows are append-only and the executor is their only writer. Components never receive the
Construct. A component gets `Inputs`: for each kind it requires, a tuple of that kind's rows,
each its own deep copy. That is what makes `requires` the true data dependencies, and why a
component that mutates what it read changes neither the record nor what another component
sees. A row of a kind with a registered schema is recorded as an instance of that schema,
however it was handed over (a dict, or the JSON a store gives back for a reused row), so its
consumers see one type on every run. A row of a kind without one is kept as given. The
record keeps its own copy of a row too, so a component that goes on to change an
object after handing it over does not change what was recorded. Rows must therefore be data
that can be copied, not live handles.

### The ledger

Provenance is one more typed table. Its kind is `matrix.v1.ledger`, and every row in every
other table has exactly one entry in it:

| Field | Type | Meaning |
|-------|------|---------|
| `seq` | `int` | the row's place in the run's order, from 0 |
| `kind` | `str` | the table the row is in |
| `row` | `int` | the row's 0-based position in that table |
| `component` | `str` | the component that handed it over, or `$input` for a run input |
| `time` | `datetime` | UTC |

`construct.rows("matrix.v1.ledger")` is the same thing as `construct.ledger`. No component
may provide or require the ledger's kind.

**The pointer** from an entry to its row is `(kind, row)`: the table, and a position in it.
Tables are append-only with one writer, so a position never changes, and nothing has to be
added to a record to make it addressable. The alternative, an id carried in every row, would
put matrix's field inside the caller's record, which is the wrapper the model does without.

**The write order** is the entry first, then the row. Both go through to the store before the
append returns. The two cannot be made one atomic write across two tables, so the order
decides what a kill between them leaves:

- entry first (what matrix does): an entry pointing one past the end of its table. A reader
  can see that, and nothing is unaccounted for;
- row first: a row with no entry. A reader cannot tell it from a row that belongs, and its
  provenance is gone.

So a row never exists without its ledger entry. The reverse, an entry without its row, is a
**torn tail**: `Construct.restore` (and so `store.load`) keeps every entry whose row exists
and reports the others in `construct.torn`. The two writes are consecutive and nothing is
written after one fails, so a torn tail is the ledger's last entry and nothing else. A stored
record that could not have come from that order (a row with no entry, entries out of step
with their table) is damaged, and a `StoreError`.

Order across kinds (`seq`) is the order rows arrived, which depends on scheduling when
components run side by side. Order within one kind is its provider's.

## Run

The observed state of one execution, kept apart from the wiring, which a run never changes.

| Field | Type | Meaning |
|-------|------|---------|
| `run_id` | `str` | given to `Executor.run`, or a fresh uuid4 hex |
| `construct` | `Construct` | every row recorded, run inputs included |
| `status` | `"running" \| "completed" \| "failed" \| "cancelled"` | |
| `started`, `ended` | `datetime` | UTC; `ended` is `None` while running |
| `failures` | `list[ComponentFailure]` | `component`, `kind`, `message` for each component that failed |
| `components` | `dict[str, ComponentEnding]` | name to `status`, `reason`, `duration_ms`, `abandoned`, `attempts`: how each component ended |
| `releases` | `list[ReleaseOutcome]` | `component`, `status`, `error` for each deferred release, in the order called |
| `events_dropped` | `int` | events no observer received |
| `reused_from` | `str \| None` | the run whose rows this one reused |
| `fingerprints` | `dict[str, str]` | name to a digest of what each component was built from |

`Run` and its parts are pydantic models. `run.rows(kind)` is `run.construct.rows(kind)`.
`run.document()` is the run as a JSON-ready document (`matrix.v1.run`), without its tables;
each record above is there as its `document()`.

Run inputs are recorded first, by `$input`, then the rows of any component reused from an
earlier run, by that component, which ends `reused`. A component is reused only when it
completed in that run (`completed` or `reused`; a `stopped` component's rows may be partial),
its fingerprint is the same, and every component it depends on is reused too: so what it
was given then is what it would be given now. The fingerprint digests the component's type
and config and each need it is handed: for an agent step its agent, for another component
that needs `agents` every agent, and `models` and `cwd` (the process's working directory
when none was given). An agent counts by its definition, its runtime's config and needs, and
what the runtime resolved when it was built (the model runtime's registry, whether from
`matrix.models` or the tiers hardline discovered). Not in it: the code that runs, and a need a
host adds through `compose(context=)`. A reused component whose
rows its kind's schema now refuses ends `failed` and is not run again; what depends on it is
skipped. Then each row is recorded when its component
hands it over (`ctx.provide`) or, for what it returns, when it ends.

When a component fails, the executor stops or skips the others as `Limits.on_failure` says,
records each failure, calls the deferred releases, and raises `RunError`. `RunError.run` is
the partial Run, with `status == "failed"`, and `RunError.construct` is its Construct. A
cancelled run raises `RunCancelled` with the partial Run in the same way. A component that
was asked to stop but returned within its grace has its rows recorded like any other, and
ends `stopped`: it returned what it had, which may not be all of it.

## Keeping a run

A `ConstructStore` is given to `Executor.run(..., store=)`, or chosen in config
(`matrix.store`) and given by `Container.run`. The executor opens the run in it
before anything starts, appends each row as it lands, and finishes the run when it ends. So
the record is outside the process the whole time: it can be read while the run is going, and
a run that is killed leaves everything it had recorded.

The stores:

- `MemoryConstructStore()` keeps runs in a dict, rows as given. For tests and single-process
  callers.
- `JsonlConstructStore(root, fsync=False)` keeps one directory per run, each row as JSON:
  models, dataclasses, sets, tuples and datetimes inside a row are written as pydantic writes
  them, and a row holding anything else is refused with a `ContractError` that names where in
  the row it is, before anything is written for it:

```
<root>/<run_id>/run.json                {"schema": "matrix.v1.run", run_id, status, started,
                                         ended, components, failures, releases,
                                         "tables": {kind: {"file", "rows"}}}
<root>/<run_id>/<kind>.jsonl            one row per line: the typed record itself
<root>/<run_id>/matrix.v1.ledger.jsonl  one line per row landed: seq, kind, row, component, time
<root>/<run_id>/events.jsonl            the run's events, one matrix.v1.event a line (telemetry)
<root>/<run_id>/writer.lock             held (flock) by the writing process while it lives
```

One kind per file means one schema per file, so `jq` or DuckDB's `read_json_auto` reads a
table without matrix installed. An agent step's reply rows are `AgentResponse`s that carry
the task as sent (`task`) beside the reply (`content`), and each `matrix.agent.start` event
carries it too, so a step that failed before its reply was recorded still shows what it was
asked. `matrix run` prints these files when the run ends; `matrix runs` lists the runs.

**While a run is going**, `run.json` says `running`, names the process writing it
(`writer`: pid and host) and lists each table's file; every table file exists. Each row is one `write` of one whole line to a file opened for
appending, made before the append returns, so a process killed at any moment leaves every row
it had appended. With `fsync=True` each row is also flushed to the disk, which survives the
machine going down and costs a disk flush per row. A line cut short by a kill is the last line
of its file and has no newline; readers do not count it as a row.

**When a run ends**, `run.json` is replaced in one rename by the final document, with the row
count of each table. A reader sees the record as it was while running or the finished one,
never a mixture.

```python
import asyncio

from matrix import Executor, JsonlConstructStore

store = JsonlConstructStore("runs/")
run = asyncio.run(Executor().run(wiring, inputs, store=store, run_id="nightly-7"))

store.runs()  # run ids, oldest first
store.describe("nightly-7")  # the run.json document
construct = store.load("nightly-7")  # the Construct, ledger included
construct.torn  # () unless the run was cut off between two writes
```

The store contract:

- `open` on a run id that already exists is a `StoreError`: a record is never reopened or
  rewritten.
- `append(kind, row)` is durable when it returns. A store that cannot append, or cannot
  finish, fails the run: `RunError` with kind `store`. After one failed append no row is
  appended to that record, and none is kept in memory either: a later `ctx.provide` raises
  `StoreError`. The store is still asked to finish the record, as `failed`.
- `load` works on a run in any state. From the JSONL store rows come back as JSON data, not
  as the Python objects that produced them (a pydantic model comes back as a dict). A run
  that reuses them validates each into its kind's schema, as a fresh run does.
- A store may refuse a row it cannot keep with a `ContractError`, only before writing anything
  for it, its ledger entry included; the component that handed it over fails, and the record
  goes on. Any other failure to append fails the run, as above.
- `describe` returns `status` as recorded for a finished run. For one that never finished it
  counts the rows on disk, reports `torn`, and says `running`, or `interrupted` when the
  process that was writing it, on this host, no longer holds `writer.lock`. From another
  host a dead run still reads `running`: matrix does not guess.
- A missing run is a `NotFoundError` naming the runs that exist.
- In the JSONL store a run id must be a safe directory name (`[A-Za-z0-9._-]`, not `.` or
  `..`); any other is a `ConfigError` before anything runs.

## Large values

A row is read whole by every component that requires its kind and written whole to the store.
A value too large for that (a sample series, a capture, a workspace) stays where it is, and
the row says where, with a `Ref` (kind `matrix.v1.ref`): `uri`, `media_type`, `size`,
`digest`. A component provides a `Ref` on its own or as a field of its own record. matrix
records it like any row; it does not fetch, copy or verify what it points at.

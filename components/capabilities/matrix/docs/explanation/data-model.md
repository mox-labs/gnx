# The data model

A run's values live in a **Construct**: one typed table per topic, plus a ledger of every row
in the order it landed. Each row is an **Artifact**. The Construct belongs to a **Run**, the
observed record of one execution. A **ConstructStore** keeps finished runs.

---

## Artifact

One value produced on one topic during a run, with its provenance. A frozen pydantic model.

| Field | Type | Meaning |
|-------|------|---------|
| `id` | `str` | uuid4 hex |
| `run_id` | `str` | the run it belongs to |
| `topic` | `str` | the topic it was produced on |
| `type_url` | `str` | the topic's type |
| `producer` | `str` | the member alias that produced it, or `$input` for a flow input |
| `port` | `str` | the producer's port name (the input's name for `$input`) |
| `value` | `Any` | the value itself |
| `episode` | `int` | which repetition of the flow produced it; `0` for a single run |
| `timestamp` | `datetime` | UTC |

`Artifact.create(*, run_id, topic, type_url, producer, port, value, episode=0)` stamps `id`
and `timestamp`. The executor is the only caller in a run.

## Construct

Typed tables, one per topic. A topic carries exactly one type (the compiler enforces it), so
each table has one schema. Several producers on one topic (fan-in) append rows to the same
table.

```python
construct = run.construct

construct.rows("loud")            # tuple[Artifact, ...], in the order they landed; () if none
construct.values("loud")          # tuple of the values on that topic
construct.last("loud")            # the most recent Artifact; NotFoundError if there is none
construct.by_type("demo.v1.text") # every artifact of that type, across topics, in ledger order
construct.topics()                # {topic: type_url} for every topic declared or seen
construct.ledger                  # every artifact, in append order
"loud" in construct               # True if the topic has at least one row
len(construct)                    # number of artifacts
```

`last` on an empty topic raises `NotFoundError`, which lists the topics that do have rows.

Rows are append-only and the executor is their only writer. Within a level, the executor
appends results in the members' declaration order, not completion order, so a Construct reads
the same however its members were scheduled. `append` refuses a row whose type differs from
the type its topic already carries.

Components never receive the Construct. A member gets `Inputs`, holding deep copies of just
the values on the topics it bound: the last value for an ordinary port, a tuple of every value
for a `many` port. That is what makes the declared ports the true data dependencies, and why
a component that mutates its input cannot alter a recorded artifact.

## Run

The observed state of one execution, kept apart from the `Flow`, which a run never changes.

| Field | Type | Meaning |
|-------|------|---------|
| `run_id` | `str` | given to `Executor.run`, or a fresh uuid4 hex |
| `flow` | `str` | the flow's name |
| `construct` | `Construct` | every value produced, flow inputs included |
| `status` | `"running" \| "completed" \| "failed"` | |
| `started`, `ended` | `datetime` | UTC; `ended` is `None` while running |
| `failures` | `list[dict]` | `{member, kind, message}` for each member that failed |
| `episode` | `int` | stamped on every artifact the run produces |

`run.outputs(topic)` is `run.construct.values(topic)`.

Flow inputs are recorded first, with producer `$input`. A provide port the member returned
but the flow left unbound is checked against its declaration and then dropped: only bound
topics become tables.

When a member fails, the executor lets the rest of its level finish, records each failure,
and raises `RunError`. `RunError.run` is the partial Run, with `status == "failed"`, and
`RunError.construct` is its Construct.

## Saving a run

A `ConstructStore` keeps finished runs and gives back their Constructs. Two adapters ship:

- `MemoryConstructStore()` keeps `Run` objects in a dict, values as given. For tests and
  single-process callers.
- `JsonlConstructStore(root)` writes one directory per run:

```
<root>/<run_id>/run.json        {"schema": "matrix.v1.run", run_id, flow, status, episode,
                                 started, ended, failures,
                                 "tables": {topic: {"type_url", "rows", "file"}}}
<root>/<run_id>/<topic>.jsonl   one artifact per line:
                                 seq, id, producer, port, episode, timestamp, value
```

One file per topic means one schema per file, so `jq` or DuckDB's `read_json_auto` reads a
table without matrix installed. `seq` is the artifact's position in the ledger, which `load`
uses to rebuild the ledger order. Topic names with characters outside `[A-Za-z0-9._-]` are
sanitised in the file name; `run.json` maps each topic to its file. Each file is written to a
temporary name and renamed, so a crashed save does not leave a half-written table.

```python
from matrix import JsonlConstructStore

store = JsonlConstructStore("runs/")
store.save(run)                        # -> "runs/<run_id>"
store.runs()                           # saved run ids, oldest first
store.describe(run.run_id)["tables"]   # {"loud": {"type_url": "demo.v1.text", "rows": 1, ...}}
construct = store.load(run.run_id)     # the Construct, ledger order restored
```

The store contract:

- `save` never rewrites a run; saving a run id that already exists is a `ConfigError`.
- `load` returns the Construct as saved. From the JSONL store, values come back as JSON data,
  not as the Python objects that produced them (a pydantic model comes back as a dict).
- A missing run is a `NotFoundError` naming the runs that exist.
- A run id must be a safe directory name (`[A-Za-z0-9._-]`, not `.` or `..`).

# The Data Model

Matrix's core data model is three types: **Artifact**, **Construct**, and **TypedStruct**.
Together they form an append-only execution ledger that grows as components run.

> Regenerated 2026-08-17 at gnx intake, from source. The previous revision described a frozen
> generic `Construct[S]` carrying a `subject`, an `Artifact.kind` field, and copy-on-append
> semantics — none of which the code has. See
> [the migration table](../reference/api.md#migrating-from-the-pre-intake-contract).

---

## Artifact

An atomic, immutable fact produced by a component. A frozen Pydantic model, not a dataclass.

```python
class Artifact(BaseModel, frozen=True):
    type_url: str        # "probe.response", "matrix.v1/agent.response", etc.
    producer: str        # component name that created this
    data: Any            # the actual value
    id: str              # uuid4 string
    timestamp: datetime  # UTC
```

`id` and `timestamp` have no defaults. Build artifacts through the factory, which stamps both:

```python
artifact = Artifact.create(type_url="probe.response", producer="probe", data=payload)
```

`type_url` is how components find each other. A sensor that declares
`requires={"probe.response"}` finds its upstream data via `construct.last("probe.response")`.

The convention is `<namespace>.v<version>/<resource>` — `matrix.v1/agent.response`,
`ix.v1/eval.readings`. Matrix does not parse or enforce the shape; it compares `type_url`
strings for equality. The convention buys legibility, not validation.

`data` is `Any` — Matrix doesn't constrain what components produce. Lists, dicts, Pydantic
models, primitives: the consuming domain decides the shape.

## TypedStruct

What a component *returns*. A `NamedTuple` pairing the declared type with the value:

```python
class TypedStruct(NamedTuple):
    type_url: str
    value: Any
```

This is the double-entry mechanism. A component declares `provides` at class level and returns
a `type_url` at runtime. The Orchestrator compares them and raises `ContractError` if they
disagree — so a component's declaration is binding, not advisory. The Orchestrator then unwraps
the `TypedStruct` into an `Artifact` and appends it.

Components return `TypedStruct`; the ledger holds `Artifact`. Only the Orchestrator converts
between them.

## Construct

The append-only ledger. A plain mutable class — no constructor arguments, no generic parameter,
no `subject`.

```python
class Construct:
    def __init__(self) -> None:
        self._ledger: list[Artifact] = []
        self._by_type: dict[str, list[Artifact]] = {}
```

Three views over one write, maintained together: the ordered `_ledger`, the `_by_type` index for
lookup, and the `ledger` property for an immutable snapshot.

`append()` **mutates in place and returns `None`.** It is not copy-on-append.

### Reading Artifacts

```python
# Most recent artifact of a type — returns the Artifact, not its data
artifact = construct.last("probe.response")
payload  = artifact.data

# Shorthand for last(...).data
payload = construct["probe.response"]

# All artifacts of a type, in append order (empty list if none)
all_grades = construct.query("sensor.grade")

# What types exist
kinds = construct.kinds()          # frozenset({"probe.response", "sensor.grade"})

# Presence check
if "sensor.grade" in construct: ...

# Immutable snapshot of everything
history = construct.ledger         # tuple[Artifact, ...]
```

`last()` raises `LookupError` when no artifact of that type exists, and the message lists what
*is* available. The DagCompiler guarantees that if a component's `requires` are satisfiable at
compile time, those artifacts are present when it runs — so `last()` on a declared requirement
won't raise.

One sharp edge: `len(construct)` returns the number of **distinct type_urls**, not the number of
artifacts. For the artifact count use `len(construct.ledger)`.

### Why Append-Only

Append-only means components can't overwrite upstream artifacts. That eliminates a class of bug
where component B corrupts component A's output, and it enables replay — the ledger is a
complete record of what happened.

The guarantee rests on two things beyond the data structure: only the Orchestrator ever calls
`append()` (single mediated writer), and the compiler statically rejects two components
declaring the same `provides`, so no type can have competing producers.

The trade-off: the full ledger stays in memory. For pipelines with many large artifacts that
matters. For typical 2-3 node DAGs it's negligible.

## The Execution Flow

```
1. Orchestrator.run()                        ← no arguments
2. construct = Construct()                   ← empty ledger
3. For each batch (topological order):
     For each component in batch:
       result = await component.run(construct)          → TypedStruct
       assert result.type_url == component.provides      → else ContractError
       artifact = Artifact.create(
           type_url=result.type_url,
           producer=component.name,
           data=result.value,
       )
       construct.append(artifact)             ← mutates in place
4. Return construct
```

Each component sees the full Construct as of the moment it runs — every upstream artifact is
available. The Construct grows monotonically. Nothing is removed or overwritten.

## Where the Seed Input Enters

There is no `subject` and `run()` takes no arguments, so external input cannot arrive through
the run call. It enters through component constructors — a root component closes over its input
and declares `requires = frozenset()`:

```python
class ProbeNode:
    name = "probe"
    requires: frozenset[str] = frozenset()
    provides = "probe.stimulus"

    def __init__(self, probe: Probe) -> None:   # ← input enters here
        self._probe = probe

    async def run(self, construct: Construct) -> TypedStruct:
        return TypedStruct(type_url="probe.stimulus", value=self._probe)
```

This is why the runtime needs no notion of what is being processed: the DAG is assembled with
its inputs already bound.

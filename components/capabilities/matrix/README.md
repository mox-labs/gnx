# Matrix

Component runtime for DAG execution.

## What Matrix Does

Matrix is the runtime where composed components execute. A consuming domain (like ix) decomposes its intent into a component configuration — which components to run, with what config. Matrix takes that composed DAG, validates the topology, and executes it. Components in, artifacts out.

Matrix doesn't know what probes, sensors, or hypotheses are. It knows what components are. Your domain decomposes its concepts into components and hands them to Matrix for execution.

## Usage

```python
from matrix import Orchestrator, Construct, TypedStruct

# Components declare what they require and what they provide
class Probe:
    name = "probe"
    requires = frozenset()           # root node — no dependencies
    provides = "probe.response"

    async def run(self, construct: Construct) -> TypedStruct:
        return TypedStruct(type_url="probe.response", value="hello")

class Sensor:
    name = "sensor"
    requires = frozenset({"probe.response"})
    provides = "sensor.grade"

    async def run(self, construct: Construct) -> TypedStruct:
        upstream = construct.last("probe.response")     # → Artifact
        return TypedStruct(type_url="sensor.grade", value=upstream.data == "hello")

# Domain decomposes work into components, Matrix just runs them
orch = Orchestrator([Probe(), Sensor()])
construct = await orch.run()                            # no arguments

construct.last("sensor.grade").data   # True
construct["sensor.grade"]             # True — shorthand for the same
```

Seed input enters through component constructors, not through `run()` — a root closes over its
input and declares `requires = frozenset()`.

## The Component Protocol

One contract. Three attributes, one method. No base class.

```python
class Component(Protocol):
    name: str                  # unique identifier within the DAG
    requires: frozenset[str]   # artifact type_urls this component reads
    provides: str              # artifact type_url this component writes

    async def run(self, construct: Construct) -> TypedStruct:
        """Read upstream artifacts, do work, return self-describing output."""
        ...
```

Structural typing via `typing.Protocol` — implement the shape, no inheritance. The one import
you need is `TypedStruct` for the return value.

`run` must return a `TypedStruct` whose `type_url` equals the declared `provides`. The
Orchestrator compares the two and raises `ContractError` on mismatch, which is what makes the
declaration binding rather than advisory.

## Documentation

| Document | Description |
|----------|-------------|
| [What is Matrix?](docs/explanation/what-is-matrix.md) | Why it exists, design decisions, what it's not |
| [The Data Model](docs/explanation/data-model.md) | Construct and Artifact — the append-only execution ledger |
| [API Reference](docs/reference/api.md) | All public types: Component, Orchestrator, DagCompiler, ... |
